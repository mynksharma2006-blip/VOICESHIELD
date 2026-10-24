import hashlib
from pathlib import Path
import sys
from urllib.error import URLError
from urllib.request import urlopen


BACKEND_DIR = Path(__file__).resolve().parents[1]
MODEL_PATH = BACKEND_DIR / "models" / "aasist.onnx"
MODEL_URL = (
    "https://huggingface.co/SpeechAntiSpoofingBenchmarks/AASIST/resolve/"
    "16774d458d86d2a021ae31646c1bf66a5331b53e/aasist.onnx"
)
EXPECTED_SHA256 = "130e536266b7c537f9a13029e1612a9f392fd1cc827783683b6d1c062a3db5e1"


def main() -> int:
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = MODEL_PATH.with_suffix(".onnx.download")
    try:
        print(f"Downloading the AASIST model from {MODEL_URL}")
        with urlopen(MODEL_URL, timeout=60) as response, temporary_path.open("wb") as output:
            while chunk := response.read(1024 * 1024):
                output.write(chunk)
        digest = hashlib.sha256(temporary_path.read_bytes()).hexdigest()
        if digest != EXPECTED_SHA256:
            raise ValueError(f"Model checksum mismatch: expected {EXPECTED_SHA256}, got {digest}.")
        temporary_path.replace(MODEL_PATH)
        print(f"Verified AASIST model saved to {MODEL_PATH}")
        return 0
    except (OSError, URLError, ValueError) as exc:
        temporary_path.unlink(missing_ok=True)
        print(f"Could not download the AASIST model: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
