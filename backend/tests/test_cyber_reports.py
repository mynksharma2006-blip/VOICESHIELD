from datetime import datetime, timedelta
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from pydantic import ValidationError

from app import api
from app.cyber_reports import (
    REPORTING_SERVICES,
    ReportAccessRequest,
    ReportSubmission,
    inspect_evidence,
    lookup_private_report,
    safety_steps_for_report,
    save_report,
    update_report_status,
)


def valid_submission(**updates: object) -> ReportSubmission:
    fields: dict[str, object] = {
        "country": "IN",
        "incident_type": "voice_phone",
        "incident_datetime": (datetime.now() - timedelta(days=1)).isoformat(timespec="minutes"),
        "contact_method": "phone",
        "involved_contact": "+91 1234567890",
        "amount_lost": "120.50",
        "currency": "INR",
        "description": "Caller claimed to be from my bank and requested a transfer.",
        "reporter_name": "Test User",
        "contact_email": "person@example.com",
        "contact_phone": None,
        "callback_requested": False,
        "callback_phone": None,
        "callback_preferred_time": None,
        "callback_timezone_offset_minutes": None,
        "callback_consent": False,
        "confirm_accuracy": True,
        "consent_to_store": True,
    }
    fields.update(updates)
    return ReportSubmission.model_validate(fields)


class CyberReportTests(unittest.TestCase):
    def test_validates_required_follow_up_and_consent(self) -> None:
        with self.assertRaises(ValidationError):
            valid_submission(contact_email=None)
        with self.assertRaises(ValidationError):
            valid_submission(consent_to_store=False)
        with self.assertRaises(ValidationError):
            valid_submission(confirm_accuracy=False)

    def test_callback_request_requires_phone_and_future_preferred_time(self) -> None:
        with self.assertRaises(ValidationError):
            valid_submission(callback_requested=True)
        with self.assertRaises(ValidationError):
            valid_submission(
                callback_requested=True,
                callback_phone="+91 9876543210",
                callback_preferred_time=(datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
                callback_timezone_offset_minutes=330,
            )
        with self.assertRaises(ValidationError):
            valid_submission(
                callback_requested=True,
                callback_phone="+91 9876543210",
                callback_preferred_time=(datetime.now() - timedelta(hours=1)).isoformat(timespec="minutes"),
                callback_timezone_offset_minutes=330,
                callback_consent=True,
            )

    def test_callback_can_be_the_reporters_only_follow_up_contact(self) -> None:
        submission = valid_submission(
            contact_email=None,
            contact_phone=None,
            callback_requested=True,
            callback_phone="+91 9876543210",
            callback_preferred_time=(datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
            callback_timezone_offset_minutes=330,
            callback_consent=True,
        )
        self.assertEqual(submission.callback_phone, "+91 9876543210")
        self.assertTrue(submission.callback_requested)

    def test_callback_details_are_not_accepted_without_opt_in(self) -> None:
        with self.assertRaises(ValidationError):
            valid_submission(
                callback_phone="+91 9876543210",
                callback_preferred_time=(datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
                callback_timezone_offset_minutes=330,
            )
        with self.assertRaises(ValidationError):
            valid_submission(
                callback_requested=True,
                callback_phone="+91 9876543210",
                callback_preferred_time=(datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
                callback_timezone_offset_minutes=330,
                callback_consent=False,
            )

    def test_rejects_future_incident_dates(self) -> None:
        with self.assertRaises(ValidationError):
            valid_submission(incident_datetime=(datetime.now() + timedelta(days=1)).isoformat())

    def test_rejects_negative_or_invalid_amount(self) -> None:
        with self.assertRaises(ValidationError):
            valid_submission(amount_lost="-1")
        with self.assertRaises(ValidationError):
            valid_submission(amount_lost="one hundred")

    def test_accepts_only_supported_evidence_and_checks_file_signature(self) -> None:
        self.assertEqual(
            inspect_evidence(b"%PDF-1.7\nprivate", "application/pdf"),
            ("application/pdf", ".pdf"),
        )
        with self.assertRaises(ValueError):
            inspect_evidence(b"not a pdf", "application/pdf")
        with self.assertRaises(ValueError):
            inspect_evidence(b"<script>alert(1)</script>", "text/html")

    def test_saves_private_record_and_evidence_without_original_filename(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports_file = root / "reports.jsonl"
            submission = valid_submission()
            with (
                patch("app.cyber_reports.REPORT_STORAGE_DIR", root),
                patch("app.cyber_reports.REPORTS_FILE", reports_file),
            ):
                reference, access_key = save_report(submission, b"%PDF-1.7\nprivate", "application/pdf", ".pdf")
                self.assertEqual(lookup_private_report(reference, "wrong-key"), None)
                private = lookup_private_report(reference, access_key)
            saved = json.loads(reports_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["reference"], reference)
            self.assertEqual(saved["status"], "Submitted")
            self.assertNotIn(access_key, reports_file.read_text(encoding="utf-8"))
            self.assertEqual(len(saved["access_key_hash"]), 64)
            self.assertEqual(saved["reporter_name"], "Test User")
            self.assertEqual(saved["submission_type"], "private in-app report; not sent to an authority")
            self.assertEqual((root / f"{reference}.pdf").read_bytes(), b"%PDF-1.7\nprivate")
            self.assertNotIn("filename", saved)
            self.assertEqual(private["status"], "Submitted")
            self.assertNotIn("reporter_name", private)
            self.assertNotIn("contact_email", private)
            self.assertNotIn("evidence_storage_id", private)

    def test_saves_callback_request_with_private_complaint(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports_file = root / "reports.jsonl"
            submission = valid_submission(
                callback_requested=True,
                callback_phone="+91 9876543210",
                callback_preferred_time=(datetime.now() + timedelta(days=1)).isoformat(timespec="minutes"),
                callback_timezone_offset_minutes=330,
                callback_consent=True,
            )
            with (
                patch("app.cyber_reports.REPORT_STORAGE_DIR", root),
                patch("app.cyber_reports.REPORTS_FILE", reports_file),
            ):
                reference, _ = save_report(submission, None, None, None)
            saved = json.loads(reports_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["reference"], reference)
            self.assertTrue(saved["callback_requested"])
            self.assertEqual(saved["callback_phone"], "+91 9876543210")
            self.assertIsNotNone(saved["callback_preferred_time"])
            self.assertEqual(saved["callback_timezone_offset_minutes"], 330)
            self.assertTrue(saved["callback_consent"])

    def test_report_endpoint_returns_saved_callback_status_without_claiming_call_placed(self) -> None:
        preferred_time = (datetime.now() + timedelta(days=1)).isoformat(timespec="minutes")
        with patch(
            "app.api.asyncio.to_thread",
            new_callable=AsyncMock,
            return_value=("VS-TEST1234567890ABCDEF", "test-private-key"),
        ):
            result = asyncio.run(
                api.submit_cyber_fraud_report(
                    country="IN",
                    incident_type="voice_phone",
                    incident_datetime=(datetime.now() - timedelta(days=1)).isoformat(timespec="minutes"),
                    contact_method="phone",
                    involved_contact=None,
                    amount_lost=None,
                    currency="INR",
                    description="A caller impersonated my bank and requested a transfer.",
                    reporter_name="Test User",
                    contact_email=None,
                    contact_phone=None,
                    callback_requested=True,
                    callback_phone="+91 9876543210",
                    callback_preferred_time=preferred_time,
                    callback_timezone_offset_minutes=330,
                    callback_consent=True,
                    confirm_accuracy=True,
                    consent_to_store=True,
                    evidence=None,
                )
            )

        self.assertEqual(result["reference"], "VS-TEST1234567890ABCDEF")
        self.assertEqual(result["access_key"], "test-private-key")
        self.assertEqual(result["status"], "Submitted")
        self.assertTrue(result["next_steps"])
        self.assertTrue(result["callback_request_saved"])
        self.assertIn("No call was placed", result["callback_message"])
        self.assertIn("not guaranteed", result["callback_message"])

    def test_status_updates_preserve_legacy_records_and_reject_invalid_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reports_file = root / "reports.jsonl"
            legacy = {"reference": "VS-LEGACY000000000000", "reporter_name": "Old report"}
            reports_file.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
            with (
                patch("app.cyber_reports.REPORT_STORAGE_DIR", root),
                patch("app.cyber_reports.REPORTS_FILE", reports_file),
            ):
                self.assertTrue(update_report_status(legacy["reference"], "Under Review"))
                self.assertFalse(update_report_status("VS-MISSING000000000000", "Resolved"))
                with self.assertRaises(ValueError):
                    update_report_status(legacy["reference"], "Automatically Reviewed")
            saved = json.loads(reports_file.read_text(encoding="utf-8"))
            self.assertEqual(saved["reporter_name"], "Old report")
            self.assertEqual(saved["status"], "Under Review")

    def test_private_lookup_endpoint_requires_both_credentials(self) -> None:
        with patch("app.cyber_reports.lookup_private_report", return_value=None):
            with self.assertRaises(api.HTTPException) as raised:
                api.private_report_history(
                    ReportAccessRequest(reference="VS-ABCDEF1234567890ABCD", access_key="x" * 43),
                    api.Response(),
                )
        self.assertEqual(raised.exception.status_code, 404)

    def test_safety_steps_are_relevant_to_payment_and_account_incidents(self) -> None:
        steps = safety_steps_for_report(valid_submission(incident_type="payment_fraud"))
        self.assertTrue(any("bank or payment provider" in step for step in steps))
        self.assertTrue(any("original messages" in step for step in steps))
        self.assertTrue(any("official cybercrime service" in step for step in steps))

    def test_official_services_use_expected_government_destinations(self) -> None:
        self.assertEqual(set(REPORTING_SERVICES), {"AU", "CA", "GB", "IN", "US"})
        self.assertTrue(REPORTING_SERVICES["US"]["url"].startswith("https://www.ic3.gov/"))
        self.assertTrue(REPORTING_SERVICES["CA"]["url"].startswith("https://reportcyberandfraud.canada.ca/"))


if __name__ == "__main__":
    unittest.main()
