import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
load_dotenv(PROJECT_ROOT / ".env")


def configured_path(name: str, default: Path) -> Path:
    value = Path(os.getenv(name, str(default)))
    return value if value.is_absolute() else BACKEND_DIR / value


MODEL_PATH = configured_path(
    "VOICESHIELD_MODEL_PATH",
    BACKEND_DIR / "models" / "lfcc_baseline.joblib",
)
AASIST_MODEL_PATH = configured_path(
    "VOICESHIELD_AASIST_MODEL_PATH",
    BACKEND_DIR / "models" / "aasist.onnx",
)
SAMPLE_RATE = 16_000
MIN_DURATION_S = 2.0
MAX_DURATION_S = 10.0
MAX_UPLOAD_BYTES = int(os.getenv("VOICESHIELD_MAX_UPLOAD_MB", "20")) * 1024 * 1024
INCONCLUSIVE_MARGIN = float(os.getenv("VOICESHIELD_INCONCLUSIVE_MARGIN", "0.25"))
SCREENING_LABELS_VALIDATED = os.getenv("VOICESHIELD_SCREENING_LABELS_VALIDATED", "false").lower() == "true"
CALL_STREAM_TOKEN = os.getenv("VOICESHIELD_CALL_STREAM_TOKEN", "").strip()
CALL_AUDIO_LABELS_VALIDATED = os.getenv("VOICESHIELD_CALL_AUDIO_LABELS_VALIDATED", "false").lower() == "true"
PRIVATE_REPORTS_ENABLED = os.getenv("VOICESHIELD_ENABLE_PRIVATE_REPORTS", "true").lower() == "true"
UI_DIR = PROJECT_ROOT / "frontend" / "web"
DEMO_DIR = BACKEND_DIR / "data" / "demo"
RESULTS_FILE = BACKEND_DIR / "results" / "metrics.json"
ANDROID_APK_PATH = (
    PROJECT_ROOT
    / "frontend"
    / "mobile"
    / "android"
    / "app"
    / "build"
    / "outputs"
    / "apk"
    / "debug"
    / "app-debug.apk"
)
