from datetime import datetime, timedelta, timezone
from decimal import Decimal
from hashlib import sha256
import json
import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from app.config import BACKEND_DIR


MAX_EVIDENCE_BYTES = 5 * 1024 * 1024
MAX_REPORT_BODY_BYTES = MAX_EVIDENCE_BYTES + 128 * 1024
REPORT_STORAGE_DIR = BACKEND_DIR / "data" / "private_reports"
REPORTS_FILE = REPORT_STORAGE_DIR / "reports.jsonl"
_REPORT_WRITE_LOCK = threading.Lock()
LOGGER = logging.getLogger(__name__)
REPORT_STATUSES = ("Submitted", "Under Review", "Resolved")

REPORTING_SERVICES = {
    "AU": {
        "country": "Australia",
        "name": "Australian Cyber Security Centre — ReportCyber",
        "url": "https://www.cyber.gov.au/report-and-recover/report",
        "guidance": "Use the Australian Government’s ReportCyber service to report a cybercrime. For immediate danger, contact local emergency services.",
    },
    "CA": {
        "country": "Canada",
        "name": "Canadian Anti-Fraud Centre",
        "url": "https://reportcyberandfraud.canada.ca/",
        "guidance": "Submit a fraud or cybercrime report through the Canadian Anti-Fraud Centre. If you are a victim, contact your local police as well.",
    },
    "GB": {
        "country": "United Kingdom",
        "name": "Report Fraud (UK)",
        "url": "https://www.reportfraud.police.uk/",
        "guidance": "Use the UK national Report Fraud service. Contact your local police if you are in immediate danger.",
    },
    "IN": {
        "country": "India",
        "name": "National Cyber Crime Reporting Portal",
        "url": "https://cybercrime.gov.in/",
        "guidance": "Use the Government of India’s National Cyber Crime Reporting Portal. For urgent financial cyber fraud, contact your bank promptly and use the official portal’s current helpline instructions.",
    },
    "US": {
        "country": "United States",
        "name": "FBI Internet Crime Complaint Center (IC3)",
        "url": "https://www.ic3.gov/",
        "guidance": "Use the FBI’s IC3 portal to report cyber-enabled crime. Contact your bank or payment provider promptly if money was sent.",
    },
}


class ReportSubmission(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    country: Literal["AU", "CA", "GB", "IN", "US"]
    incident_type: Literal["voice_phone", "impersonation", "phishing", "payment_fraud", "other"]
    incident_datetime: datetime
    contact_method: Literal["phone", "email", "website", "social_media", "in_person", "other"]
    involved_contact: str | None = Field(default=None, max_length=320)
    amount_lost: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    currency: Literal["AUD", "CAD", "GBP", "INR", "USD"]
    description: str = Field(min_length=20, max_length=5000)
    reporter_name: str = Field(min_length=1, max_length=120)
    contact_email: str | None = Field(default=None, max_length=254)
    contact_phone: str | None = Field(default=None, max_length=40)
    callback_requested: bool = False
    callback_phone: str | None = Field(default=None, max_length=40)
    callback_preferred_time: datetime | None = None
    callback_timezone_offset_minutes: int | None = Field(default=None, ge=-840, le=840)
    callback_consent: bool = False
    confirm_accuracy: bool
    consent_to_store: bool

    @field_validator("incident_datetime")
    @classmethod
    def incident_must_not_be_in_the_future(cls, value: datetime) -> datetime:
        now = datetime.now(value.tzinfo) if value.tzinfo else datetime.now()
        if value > now + timedelta(minutes=5):
            raise ValueError("Incident date and time cannot be in the future.")
        return value

    @field_validator("involved_contact")
    @classmethod
    def empty_involved_contact_becomes_none(cls, value: str | None) -> str | None:
        return value or None

    @field_validator("contact_email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is not None and value and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Enter a valid email address.")
        return value or None

    @field_validator("contact_phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        if value is not None and value:
            if not re.fullmatch(r"[+0-9().\-\s]{7,40}", value):
                raise ValueError("Enter a valid phone number.")
        return value or None

    @field_validator("callback_phone")
    @classmethod
    def validate_callback_phone(cls, value: str | None) -> str | None:
        if value is not None and value:
            if not re.fullmatch(r"[+0-9().\-\s]{7,40}", value):
                raise ValueError("Enter a valid callback phone number.")
        return value or None

    @model_validator(mode="after")
    def validate_follow_up_contact_and_consent(self) -> "ReportSubmission":
        if self.callback_requested:
            if not self.callback_phone:
                raise ValueError("Enter a phone number for the callback request.")
            if not self.callback_preferred_time:
                raise ValueError("Choose a preferred callback time.")
            if self.callback_timezone_offset_minutes is None:
                raise ValueError("Callback time zone information is required.")
            if not self.callback_consent:
                raise ValueError("Consent is required to save a callback request.")
            callback_timezone = timezone(timedelta(minutes=self.callback_timezone_offset_minutes))
            preferred_time = self.callback_preferred_time
            if preferred_time.tzinfo is None:
                preferred_time = preferred_time.replace(tzinfo=callback_timezone)
                self.callback_preferred_time = preferred_time
            if preferred_time <= datetime.now(preferred_time.tzinfo):
                raise ValueError("Preferred callback time must be in the future.")
        elif (
            self.callback_phone
            or self.callback_preferred_time
            or self.callback_timezone_offset_minutes is not None
        ):
            raise ValueError("Callback details require opting in to Request a Callback.")
        elif self.callback_consent:
            raise ValueError("Callback consent requires opting in to Request a Callback.")

        if not self.contact_email and not self.contact_phone and not (
            self.callback_requested and self.callback_phone
        ):
            raise ValueError("Provide at least one safe follow-up method: email or phone.")
        if not self.confirm_accuracy:
            raise ValueError("Confirm that the information is accurate.")
        if not self.consent_to_store:
            raise ValueError("Consent is required to save this in-app report.")
        return self


class ReportAccessRequest(BaseModel):
    reference: str = Field(min_length=15, max_length=40, pattern=r"^VS-[A-F0-9]+$")
    access_key: str = Field(min_length=40, max_length=100)


class ReportStorageError(OSError):
    pass


def validation_messages(error: ValidationError) -> list[str]:
    return list(dict.fromkeys(item["msg"].removeprefix("Value error, ") for item in error.errors()))


def inspect_evidence(content: bytes, content_type: str | None) -> tuple[str, str]:
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    if content.startswith(b"%PDF-") and mime == "application/pdf":
        return "application/pdf", ".pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n") and mime == "image/png":
        return "image/png", ".png"
    if content.startswith(b"\xff\xd8\xff") and mime in {"image/jpeg", "image/jpg"}:
        return "image/jpeg", ".jpg"
    if mime == "text/plain":
        try:
            decoded = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("Text evidence must use UTF-8 encoding.") from exc
        if "\x00" not in decoded:
            return "text/plain", ".txt"
    raise ValueError("Evidence must be a valid PDF, PNG, JPEG, or UTF-8 text file.")


def _read_report_records() -> list[dict[str, object]]:
    try:
        with REPORTS_FILE.open("r", encoding="utf-8") as reports:
            records = [json.loads(line) for line in reports if line.strip()]
        if not all(isinstance(record, dict) for record in records):
            raise ReportStorageError("Private report storage contains an invalid record.")
        return records
    except FileNotFoundError:
        return []
    except (OSError, json.JSONDecodeError) as exc:
        raise ReportStorageError("Private report storage could not be read.") from exc


def save_report(
    submission: ReportSubmission,
    evidence: bytes | None,
    evidence_mime: str | None,
    evidence_suffix: str | None,
) -> tuple[str, str]:
    evidence_path: Path | None = None
    access_key = secrets.token_urlsafe(32)
    access_key_hash = sha256(access_key.encode("ascii")).hexdigest()
    record = submission.model_dump(mode="json")

    try:
        REPORT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        with _REPORT_WRITE_LOCK:
            existing_references = {record.get("reference") for record in _read_report_records()}
            reference = ""
            while not reference or reference in existing_references:
                reference = f"VS-{uuid4().hex.upper()}"
            record.update(
                {
                    "reference": reference,
                    "access_key_hash": access_key_hash,
                    "status": "Submitted",
                    "status_updated_at": datetime.now().astimezone().isoformat(),
                    "submitted_at": datetime.now().astimezone().isoformat(),
                    "evidence_mime": evidence_mime,
                    "evidence_size_bytes": len(evidence) if evidence is not None else None,
                    "evidence_storage_id": f"{reference}{evidence_suffix}" if evidence is not None else None,
                    "submission_type": "private in-app report; not sent to an authority",
                }
            )
            if evidence is not None and evidence_suffix is not None:
                evidence_path = REPORT_STORAGE_DIR / f"{reference}{evidence_suffix}"
                with evidence_path.open("xb") as output:
                    output.write(evidence)
                    output.flush()
                    os.fsync(output.fileno())
                if os.name != "nt":
                    os.chmod(evidence_path, 0o600)

            with REPORTS_FILE.open("a", encoding="utf-8", newline="\n") as output:
                output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                output.flush()
                os.fsync(output.fileno())
            if os.name != "nt":
                os.chmod(REPORT_STORAGE_DIR, 0o700)
                os.chmod(REPORTS_FILE, 0o600)

    except OSError as exc:
        if evidence_path is not None:
            try:
                evidence_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                LOGGER.error("Could not remove orphaned evidence after report storage failed: %s", cleanup_exc)
        raise ReportStorageError("The report could not be saved to local storage.") from exc
    return reference, access_key


def lookup_private_report(reference: str, access_key: str) -> dict[str, object] | None:
    access_key_hash = sha256(access_key.encode("utf-8")).hexdigest()
    with _REPORT_WRITE_LOCK:
        records = _read_report_records()
    for record in records:
        if record.get("reference") != reference:
            continue
        stored_hash = record.get("access_key_hash")
        if not isinstance(stored_hash, str) or not secrets.compare_digest(stored_hash, access_key_hash):
            return None
        return {
            "reference": record["reference"],
            "submitted_at": record["submitted_at"],
            "status": record.get("status", "Submitted"),
            "incident_type": record["incident_type"],
            "incident_datetime": record["incident_datetime"],
            "description": record["description"],
            "amount_lost": record.get("amount_lost"),
            "currency": record["currency"],
            "callback_requested": bool(record.get("callback_requested", False)),
            "callback_preferred_time": record.get("callback_preferred_time"),
            "evidence_attached": bool(record.get("evidence_storage_id")),
            "next_steps": safety_steps_for_report(record),
        }
    return None


def safety_steps_for_report(report: ReportSubmission | dict[str, object]) -> list[str]:
    if isinstance(report, ReportSubmission):
        incident_type = report.incident_type
        amount_lost = report.amount_lost
    else:
        incident_type = report.get("incident_type")
        amount_lost = report.get("amount_lost")

    steps = ["Keep original messages, screenshots, receipts, transaction IDs, and dates; do not edit the originals."]
    if incident_type == "payment_fraud" or (
        amount_lost is not None and Decimal(str(amount_lost)) > 0
    ):
        steps.insert(0, "Contact your bank or payment provider promptly using its official app or a number you already trust.")
    if incident_type == "phishing":
        steps.insert(0, "Do not click the link or reply to the suspicious message; report it through the service's official channel.")
    if incident_type in {"impersonation", "phishing", "voice_phone"}:
        steps.append("Secure affected accounts and change any password you may have shared; never share one-time codes.")
    steps.append("Submit a separate report to the official cybercrime service for your country; saving here does not file it there.")
    return steps


def update_report_status(reference: str, status: str) -> bool:
    if status not in REPORT_STATUSES:
        raise ValueError(f"Status must be one of: {', '.join(REPORT_STATUSES)}.")
    with _REPORT_WRITE_LOCK:
        records = _read_report_records()
        matching = next((record for record in records if record.get("reference") == reference), None)
        if matching is None:
            return False
        matching["status"] = status
        matching["status_updated_at"] = datetime.now().astimezone().isoformat()
        REPORT_STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        temporary_path = REPORTS_FILE.with_suffix(".jsonl.tmp")
        try:
            with temporary_path.open("w", encoding="utf-8", newline="\n") as output:
                for record in records:
                    output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary_path, REPORTS_FILE)
            if os.name != "nt":
                os.chmod(temporary_path.parent, 0o700)
                os.chmod(REPORTS_FILE, 0o600)
        except OSError as exc:
            temporary_path.unlink(missing_ok=True)
            raise ReportStorageError("The report status could not be updated.") from exc
    return True
