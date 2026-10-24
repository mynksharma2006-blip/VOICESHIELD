import argparse
import csv
from pathlib import Path
import sys

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audio import AudioValidationError, decode_audio
from app.inference.lfcc_baseline import extract_lfcc

BACKEND_DIR = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the optional local VoiceShield LFCC baseline.")
    parser.add_argument("manifest", type=Path, help="CSV with path,label,partition columns")
    parser.add_argument("--output", type=Path, default=BACKEND_DIR / "models" / "lfcc_baseline.joblib")
    parser.add_argument("--partition", default="train", help="Only rows with this partition are used")
    args = parser.parse_args()

    features: list[np.ndarray] = []
    labels: list[int] = []
    with args.manifest.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    for row_number, row in enumerate(rows, start=2):
        if row.get("partition", "").strip().lower() != args.partition.lower():
            continue
        label_name = row.get("label", "").strip().lower()
        if label_name not in {"bona_fide", "bonafide", "genuine", "spoof", "fake"}:
            raise ValueError(f"Unknown label at manifest row {row_number}: {label_name!r}")
        path = Path(row["path"])
        if not path.is_absolute():
            path = args.manifest.parent / path
        try:
            audio = decode_audio(path.read_bytes())
            features.append(extract_lfcc(audio.samples))
        except (OSError, AudioValidationError, ValueError) as exc:
            raise ValueError(f"Could not process row {row_number} ({path}): {exc}") from exc
        labels.append(1 if label_name in {"bona_fide", "bonafide", "genuine"} else 0)

    if len(set(labels)) != 2:
        raise ValueError(f"Partition {args.partition!r} must contain both bona_fide and spoof samples.")
    classifier = make_pipeline(
        StandardScaler(),
        LogisticRegression(class_weight="balanced", max_iter=2000, random_state=17),
    )
    classifier.fit(np.vstack(features), np.asarray(labels))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {"classifier": classifier, "model_id": "LFCC + scikit-learn demo baseline"},
        args.output,
    )
    print(f"Saved model trained on {len(labels)} {args.partition!r} samples to {args.output}")


if __name__ == "__main__":
    main()
