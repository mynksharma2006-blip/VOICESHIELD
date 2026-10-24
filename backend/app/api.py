from contextlib import asynccontextmanager
import asyncio
import hmac
import json

from fastapi import FastAPI, File, Form, HTTPException, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import numpy as np
from pydantic import ValidationError
from scipy.signal import resample_poly

from app.audio import AudioValidationError, decode_audio
from app.config import (
    AASIST_MODEL_PATH,
    ANDROID_APK_PATH,
    CALL_AUDIO_LABELS_VALIDATED,
    CALL_STREAM_TOKEN,
    DEMO_DIR,
    INCONCLUSIVE_MARGIN,
    MAX_UPLOAD_BYTES,
    MODEL_PATH,
    RESULTS_FILE,
    SCREENING_LABELS_VALIDATED,
    UI_DIR,
)
from app.cyber_reports import (
    MAX_EVIDENCE_BYTES,
    MAX_REPORT_BODY_BYTES,
    REPORTING_SERVICES,
    ReportAccessRequest,
    ReportStorageError,
    ReportSubmission,
    inspect_evidence,
    lookup_private_report,
    save_report,
    safety_steps_for_report,
    validation_messages,
)
from app.inference.decision import classify_screening_score, explain_screening_score
from app.inference.aasist import AASISTDetector
from app.inference.lfcc_baseline import LFCCBaseline
from app.scam_news import COUNTRIES, get_scam_news
from app.schemas import AnalysisResponse, DemoExample, PerformanceResponse


class _RequestBodyTooLarge(Exception):
    pass


class _DemoStreamWebSocket:
    def __init__(self) -> None:
        self.query_params = {"sample_rate": "16000"}
        self.messages = [
            {"type": "websocket.receive", "bytes": b"\x00\x00" * 8_000},
            {"type": "websocket.disconnect"},
        ]
        self.sent_messages: list[dict[str, object]] = []

    async def accept(self) -> None:
        return None

    async def send_json(self, message: dict[str, object]) -> None:
        self.sent_messages.append(message)

    async def receive(self) -> dict[str, object]:
        return self.messages.pop(0)


class _DemoStreamDetector:
    model_id = "simulated-demo"
    window_samples = 8_000

    def score(self, samples: np.ndarray) -> float:
        return -5.9


class CyberReportBodyLimitMiddleware:
    def __init__(self, application):
        self.application = application

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") != "/api/cyber-fraud/reports":
            await self.application(scope, receive, send)
            return

        content_length = next(
            (
                value.decode("latin-1")
                for name, value in scope.get("headers", [])
                if name.lower() == b"content-length"
            ),
            None,
        )
        if content_length is not None:
            try:
                if int(content_length) > MAX_REPORT_BODY_BYTES:
                    response = JSONResponse(
                        status_code=413,
                        content={"detail": "The report request exceeds the 5 MB evidence upload limit."},
                    )
                    await response(scope, receive, send)
                    return
            except ValueError:
                response = JSONResponse(status_code=400, content={"detail": "Invalid request size header."})
                await response(scope, receive, send)
                return

        received_bytes = 0

        async def limited_receive():
            nonlocal received_bytes
            message = await receive()
            if message["type"] == "http.request":
                received_bytes += len(message.get("body", b""))
                if received_bytes > MAX_REPORT_BODY_BYTES:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.application(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            response = JSONResponse(
                status_code=413,
                content={"detail": "The report request exceeds the 5 MB evidence upload limit."},
            )
            await response(scope, receive, send)


@asynccontextmanager
async def lifespan(application: FastAPI):
    if AASIST_MODEL_PATH.is_file():
        application.state.detector = AASISTDetector(AASIST_MODEL_PATH)
    elif MODEL_PATH.is_file():
        application.state.detector = LFCCBaseline(MODEL_PATH)
    else:
        application.state.detector = None
    yield


app = FastAPI(title="VoiceShield AI", lifespan=lifespan)
app.add_middleware(CyberReportBodyLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["POST"],
    allow_headers=["Accept"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/status")
def app_status() -> dict[str, object]:
    detector = getattr(app.state, "detector", None)
    return {
        "status": "ready" if detector is not None else "demo",
        "model_id": detector.model_id if detector is not None else None,
        "screening_labels_validated": SCREENING_LABELS_VALIDATED,
        "message": (
            (
                "The local model is connected, but its screening labels have not been validated for your recordings."
                if not SCREENING_LABELS_VALIDATED
                else "Local voice recording checks are ready."
            )
            if detector is not None
            else "No local detection model is connected. Results will be marked as a demo response."
        ),
    }


@app.get("/api/news/countries")
def news_countries() -> list[dict[str, str]]:
    return [
        {"code": code, "name": config["name"]}
        for code, config in sorted(COUNTRIES.items(), key=lambda item: item[1]["name"])
    ]


@app.get("/api/news")
async def scam_news(response: Response, country: str = "IN") -> dict[str, object]:
    code = country.strip().upper()
    if code not in COUNTRIES:
        raise HTTPException(status_code=400, detail="Choose a country from the available list.")
    response.headers["Cache-Control"] = "no-store"
    return await asyncio.to_thread(get_scam_news, code)


@app.get("/api/cyber-fraud/reporting-services")
def cyber_fraud_reporting_services() -> list[dict[str, str]]:
    return [dict(code=code, **service) for code, service in REPORTING_SERVICES.items()]


@app.post("/api/cyber-fraud/reports", status_code=201)
async def submit_cyber_fraud_report(
    country: str = Form(...),
    incident_type: str = Form(...),
    incident_datetime: str = Form(...),
    contact_method: str = Form(...),
    involved_contact: str | None = Form(None),
    amount_lost: str | None = Form(None),
    currency: str = Form(...),
    description: str = Form(...),
    reporter_name: str = Form(...),
    contact_email: str | None = Form(None),
    contact_phone: str | None = Form(None),
    callback_requested: bool = Form(False),
    callback_phone: str | None = Form(None),
    callback_preferred_time: str | None = Form(None),
    callback_timezone_offset_minutes: int | None = Form(None),
    callback_consent: bool = Form(False),
    confirm_accuracy: bool = Form(False),
    consent_to_store: bool = Form(False),
    evidence: UploadFile | None = File(None),
) -> dict[str, object]:
    evidence_bytes: bytes | None = None
    evidence_mime: str | None = None
    evidence_suffix: str | None = None
    if evidence is not None and evidence.filename:
        evidence_content_type = evidence.content_type
        try:
            evidence_bytes = await evidence.read(MAX_EVIDENCE_BYTES + 1)
        finally:
            await evidence.close()
        if len(evidence_bytes) > MAX_EVIDENCE_BYTES:
            raise HTTPException(status_code=413, detail="Evidence file exceeds the 5 MB limit.")
        try:
            evidence_mime, evidence_suffix = inspect_evidence(evidence_bytes, evidence_content_type)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    elif evidence is not None:
        await evidence.close()

    try:
        submission = ReportSubmission.model_validate(
            {
                "country": country,
                "incident_type": incident_type,
                "incident_datetime": incident_datetime,
                "contact_method": contact_method,
                "involved_contact": involved_contact,
                "amount_lost": amount_lost or None,
                "currency": currency,
                "description": description,
                "reporter_name": reporter_name,
                "contact_email": contact_email,
                "contact_phone": contact_phone,
                "callback_requested": callback_requested,
                "callback_phone": callback_phone,
                "callback_preferred_time": callback_preferred_time or None,
                "callback_timezone_offset_minutes": callback_timezone_offset_minutes,
                "callback_consent": callback_consent,
                "confirm_accuracy": confirm_accuracy,
                "consent_to_store": consent_to_store,
            }
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=validation_messages(exc)) from exc

    try:
        reference, access_key = await asyncio.to_thread(
            save_report,
            submission,
            evidence_bytes,
            evidence_mime,
            evidence_suffix,
        )
    except ReportStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "reference": reference,
        "access_key": access_key,
        "status": "Submitted",
        "message": (
            "Your report was saved locally in VoiceShield. It was not sent to police, a government agency, "
            "or any other external service."
        ),
        "callback_request_saved": submission.callback_requested,
        "callback_message": (
            "Your callback request and preferred time were saved with the report. No call was placed. Callback follow-up is not connected, so a callback is not guaranteed."
            if submission.callback_requested
            else "No callback was requested."
        ),
        "next_steps": safety_steps_for_report(submission),
    }


@app.post("/api/cyber-fraud/reports/history")
def private_report_history(request: ReportAccessRequest, response: Response) -> dict[str, object]:
    response.headers["Cache-Control"] = "no-store, private"
    try:
        report = lookup_private_report(request.reference, request.access_key)
    except ReportStorageError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    if report is None:
        raise HTTPException(
            status_code=404,
            detail="Report not found or the private access key is incorrect.",
        )
    return report


@app.get("/api/mobile/android.apk")
def download_android_app() -> FileResponse:
    if not ANDROID_APK_PATH.is_file():
        raise HTTPException(status_code=404, detail="The Android app has not been built in this workspace.")
    return FileResponse(
        ANDROID_APK_PATH,
        media_type="application/vnd.android.package-archive",
        filename="VoiceShield-Calls-debug.apk",
    )


@app.post("/api/call-stream/demo-test")
async def call_stream_demo_test() -> dict[str, object]:
    websocket = _DemoStreamWebSocket()
    await _stream_audio(
        websocket,
        "simulated speakerphone microphone; demo only",
        labels_validated=False,
        detector_override=_DemoStreamDetector(),
    )
    scored_message = next(
        (
            message
            for message in websocket.sent_messages
            if message.get("status") == "scored"
        ),
        None,
    )
    if scored_message is None or scored_message.get("label") != "inconclusive":
        detail = next(
            (
                message.get("error")
                for message in websocket.sent_messages
                if message.get("error")
            ),
            "The simulated stream did not produce the expected inconclusive result.",
        )
        raise HTTPException(status_code=503, detail=str(detail))

    return {
        "status": "passed",
        "result": "Can’t tell",
        "steps": [
            "Simulated PCM audio chunk accepted",
            "Streaming window scored",
            "Unvalidated call audio remained inconclusive",
        ],
        "notice": (
            "This demo test uses synthetic silence and a dummy detector. "
            "It does not test the configured model, token, HTTPS/WSS, a phone microphone, or a live call."
        ),
    }


@app.websocket("/api/stream")
async def stream_audio(websocket: WebSocket) -> None:
    await _stream_audio(websocket, "microphone input; not direct phone-call audio")


@app.websocket("/api/call-stream")
async def stream_call_audio(websocket: WebSocket) -> None:
    if not CALL_STREAM_TOKEN:
        await websocket.close(code=1013, reason="Speakerphone streaming is not configured.")
        return
    if websocket.scope.get("scheme") != "wss":
        await websocket.close(code=1008, reason="Speakerphone audio requires a secure WSS connection.")
        return
    authorization = websocket.headers.get("authorization", "")
    scheme, separator, provided_token = authorization.partition(" ")
    if (
        not separator
        or scheme.casefold() != "bearer"
        or not hmac.compare_digest(provided_token.strip(), CALL_STREAM_TOKEN)
    ):
        await websocket.close(code=4401, reason="Invalid call-stream access token.")
        return
    await _stream_audio(
        websocket,
        "speakerphone microphone room audio; not direct cellular-call audio",
        CALL_AUDIO_LABELS_VALIDATED,
    )


async def _stream_audio(
    websocket: WebSocket,
    source_description: str,
    labels_validated: bool = SCREENING_LABELS_VALIDATED,
    detector_override: object | None = None,
) -> None:
    await websocket.accept()
    detector = detector_override if detector_override is not None else getattr(app.state, "detector", None)
    if detector is None:
        await websocket.send_json({"error": "No local authenticity model is configured."})
        await websocket.close(code=1013)
        return
    if not hasattr(detector, "window_samples"):
        await websocket.send_json({"error": "Live streaming currently requires the AASIST model."})
        await websocket.close(code=1013)
        return

    try:
        sample_rate = int(websocket.query_params.get("sample_rate", "0"))
    except ValueError:
        await websocket.send_json({"error": "Invalid microphone sample rate."})
        await websocket.close(code=1008)
        return
    if not 8_000 <= sample_rate <= 96_000:
        await websocket.send_json({"error": "Microphone sample rate must be between 8 kHz and 96 kHz."})
        await websocket.close(code=1008)
        return

    sample_buffer = np.empty(0, dtype=np.float32)
    source_samples_received = 0
    await websocket.send_json(
        {
            "status": "ready",
            "model_id": detector.model_id,
            "window_duration_s": detector.window_samples / 16_000,
            "update_interval_s": 0.5,
            "source": source_description,
            "labels_validated": labels_validated,
        }
    )

    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            chunk = message.get("bytes")
            if chunk is None:
                await websocket.send_json({"error": "Send microphone audio as binary PCM16 chunks."})
                await websocket.close(code=1003)
                return
            if not chunk or len(chunk) % 2 or len(chunk) > sample_rate * 2:
                await websocket.send_json({"error": "Invalid audio chunk; expected at most one second of PCM16."})
                await websocket.close(code=1003)
                return

            pcm = np.frombuffer(chunk, dtype="<i2").astype(np.float32) / 32768.0
            source_samples_received += len(pcm)
            samples = (
                resample_poly(pcm, 16_000, sample_rate).astype(np.float32)
                if sample_rate != 16_000
                else pcm
            )
            sample_buffer = np.concatenate((sample_buffer, samples))[-detector.window_samples :]
            elapsed_s = source_samples_received / sample_rate
            if len(sample_buffer) < detector.window_samples:
                await websocket.send_json(
                    {
                        "status": "warming_up",
                        "elapsed_s": round(elapsed_s, 2),
                        "required_s": round(detector.window_samples / 16_000, 2),
                        "update_interval_s": 0.5,
                    }
                )
                continue

            score = await asyncio.to_thread(detector.score, sample_buffer.copy())
            label = classify_screening_score(
                score,
                INCONCLUSIVE_MARGIN,
                labels_validated,
            )
            await websocket.send_json(
                {
                    "status": "scored",
                    "label": label,
                    "reason": explain_screening_score(
                        score,
                        label,
                        INCONCLUSIVE_MARGIN,
                        labels_validated,
                    ),
                    "score_raw": round(score, 4),
                    "risk_percentage": None,
                    "risk_percentage_reason": (
                        "Not available: the raw model score is not calibrated as a probability."
                    ),
                    "elapsed_s": round(elapsed_s, 2),
                    "window_duration_s": round(detector.window_samples / 16_000, 2),
                    "update_interval_s": 0.5,
                    "model_id": detector.model_id,
                    "source": f"rolling {source_description}",
                }
            )
    except WebSocketDisconnect:
        return
    except (ValueError, RuntimeError) as exc:
        await websocket.send_json({"error": f"Local streaming inference failed: {exc}"})
        await websocket.close(code=1011)


@app.post("/api/analyze", response_model=AnalysisResponse)
async def analyze(file: UploadFile = File(...)) -> AnalysisResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="Choose an audio file to analyze.")
    if file.content_type and not (
        file.content_type.startswith("audio/")
        or file.content_type in {"application/octet-stream", "application/x-flac"}
    ):
        raise HTTPException(status_code=415, detail="Unsupported file type. Use WAV, FLAC, or OGG audio.")

    try:
        content = await file.read(MAX_UPLOAD_BYTES + 1)
    finally:
        await file.close()
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"File exceeds the {MAX_UPLOAD_BYTES // (1024 * 1024)} MB limit.")
    try:
        processed = decode_audio(content)
    except AudioValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    detector = app.state.detector
    warnings = list(processed.warnings)
    if processed.quality_blockers:
        label = "inconclusive"
        model_id = detector.model_id if detector is not None else "No trained model loaded"
        score_raw = None
        warnings.extend(processed.quality_blockers)
        reason = processed.quality_blockers[0]
    elif detector is None:
        label = "inconclusive"
        model_id = "Demo response (no trained model loaded)"
        score_raw = None
        warnings.append(
            "No local classifier is configured. This demo response does not analyze authenticity."
        )
        reason = (
            f"{processed.warnings[0]} The demo response does not analyze authenticity."
            if processed.warnings
            else "No trained local model is configured, so this is a demo response only."
        )
    else:
        try:
            score_raw = detector.score(processed.samples)
        except (ValueError, RuntimeError) as exc:
            raise HTTPException(status_code=500, detail=f"Local inference failed: {exc}") from exc
        label = classify_screening_score(
            score_raw,
            INCONCLUSIVE_MARGIN,
            SCREENING_LABELS_VALIDATED,
        )
        model_id = detector.model_id
        reason = explain_screening_score(
            score_raw,
            label,
            INCONCLUSIVE_MARGIN,
            SCREENING_LABELS_VALIDATED,
        )
        if warnings:
            label = "inconclusive"
            score_raw = None
            reason = "Audio quality may make a screening result unreliable; treat this clip as inconclusive."

    return AnalysisResponse(
        label=label,
        reason=reason,
        clip_duration_s=round(processed.duration_s, 2),
        model_id=model_id,
        score_raw=round(score_raw, 4) if score_raw is not None else None,
        risk_percentage=None,
        risk_percentage_reason="Not available: the raw model score is not calibrated as a probability.",
        warnings=warnings,
        limitation=(
            "A screening signal can be wrong and cannot prove who is speaking. "
            "For urgent requests, verify using a separate trusted contact channel."
        ),
    )


@app.get("/api/examples", response_model=list[DemoExample])
def demo_examples() -> list[DemoExample]:
    examples = []
    for example_id, label in (("genuine", "Genuine"), ("synthetic", "Synthetic")):
        path = DEMO_DIR / f"{example_id}.wav"
        if path.is_file():
            examples.append(
                DemoExample(
                    id=example_id,
                    label=label,
                    description="Team-provided, permitted local demo clip",
                )
            )
    return examples


@app.get("/api/examples/{example_id}")
def get_demo_example(example_id: str) -> FileResponse:
    if example_id not in {"genuine", "synthetic"}:
        raise HTTPException(status_code=404, detail="Demo example not found.")
    path = DEMO_DIR / f"{example_id}.wav"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="This permitted demo clip has not been added locally.")
    return FileResponse(path, media_type="audio/wav", filename=path.name)


@app.get("/api/performance", response_model=PerformanceResponse)
def performance() -> PerformanceResponse:
    if not RESULTS_FILE.is_file():
        return PerformanceResponse(
            available=False,
            model=None,
            threshold=None,
            phone_stress_description=None,
            reports=[],
            message="No evaluation report has been generated. No model performance results are available.",
        )
    try:
        report = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not read the local evaluation report: {exc}") from exc
    if not isinstance(report, dict) or not isinstance(report.get("reports"), list):
        raise HTTPException(status_code=500, detail="The local evaluation report has an invalid format.")
    return PerformanceResponse(
        available=bool(report["reports"]),
        model=report.get("model"),
        threshold=report.get("threshold"),
        phone_stress_description=report.get("phone_stress_description"),
        reports=report["reports"],
        message=(
            "Measured on the listed local test data. Results are not proof of real-world performance."
            if report["reports"]
            else "The evaluation report contains no scored examples."
        ),
    )


@app.get("/api/performance/download")
def download_performance_report() -> FileResponse:
    if not RESULTS_FILE.is_file():
        raise HTTPException(status_code=404, detail="No local evaluation report is available to download.")
    return FileResponse(
        RESULTS_FILE,
        media_type="application/json",
        filename="voiceshield-evaluation-report.json",
    )


@app.get("/")
def home() -> FileResponse:
    return FileResponse(UI_DIR / "index.html")


@app.get("/scam-news")
def scam_news_page() -> FileResponse:
    return FileResponse(UI_DIR / "scam-news.html")


app.mount("/static", StaticFiles(directory=UI_DIR), name="static")
