import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
import sys

import numpy as np
from scipy.signal import butter, resample_poly, sosfiltfilt
from sklearn.metrics import balanced_accuracy_score, roc_curve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audio import AudioValidationError, decode_audio
from app.config import RESULTS_FILE
from app.inference.lfcc_baseline import LFCCBaseline

BACKEND_DIR = Path(__file__).resolve().parents[1]


def equal_error_rate(labels: np.ndarray, scores: np.ndarray) -> float:
    false_positive_rate, true_positive_rate, _ = roc_curve(labels, scores)
    false_negative_rate = 1.0 - true_positive_rate
    crossing = np.where(np.diff(np.sign(false_positive_rate - false_negative_rate)) != 0)[0]
    if not len(crossing):
        return float(np.min(np.abs(false_positive_rate - false_negative_rate)) / 2
                     + false_positive_rate[np.argmin(np.abs(false_positive_rate - false_negative_rate))] / 2)
    index = crossing[0]
    x0, x1 = false_positive_rate[index], false_positive_rate[index + 1]
    y0 = false_negative_rate[index] - x0
    y1 = false_negative_rate[index + 1] - x1
    fraction = 0.0 if y0 == y1 else y0 / (y0 - y1)
    return float(x0 + fraction * (x1 - x0))


def simulate_phone_band(audio: np.ndarray, sample_rate: int = 16_000) -> np.ndarray:
    """Apply a simple 300–3400 Hz band limit and 8 kHz resampling simulation."""
    filter_sections = butter(
        4,
        (300, min(3400, sample_rate / 2 - 1)),
        btype="bandpass",
        fs=sample_rate,
        output="sos",
    )
    filtered = sosfiltfilt(filter_sections, audio)
    downsampled = resample_poly(filtered, 1, 2)
    return resample_poly(downsampled, 2, 1).astype(np.float32)


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a VoiceShield classifier using labeled CSV rows.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--model", type=Path, default=BACKEND_DIR / "models" / "lfcc_baseline.joblib")
    parser.add_argument("--partition", default="dev")
    parser.add_argument(
        "--phone-stress",
        action="store_true",
        help="Also evaluate a simulated 300–3400 Hz, 8 kHz phone-band transformation.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=RESULTS_FILE,
        help="Path for the JSON metrics report.",
    )
    args = parser.parse_args()

    detector = LFCCBaseline(args.model)
    groups: dict[tuple[str, str, str], list[tuple[int, float]]] = defaultdict(list)
    with args.manifest.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    for row_number, row in enumerate(rows, start=2):
        partition = row.get("partition", "").strip().lower()
        if partition != args.partition.lower():
            continue
        label = row.get("label", "").strip().lower()
        if label not in {"bona_fide", "bonafide", "genuine", "spoof", "fake"}:
            raise ValueError(f"Unknown label at manifest row {row_number}: {label!r}")
        source = row.get("source", "unspecified").strip() or "unspecified"
        path = Path(row["path"])
        if not path.is_absolute():
            path = args.manifest.parent / path
        try:
            processed = decode_audio(path.read_bytes())
            score = detector.score(processed.samples)
            label_value = 1 if label in {"bona_fide", "bonafide", "genuine"} else 0
            groups[(source, partition, "original")].append((label_value, score))
            if args.phone_stress:
                stressed = simulate_phone_band(processed.samples)
                stressed_score = detector.score(stressed)
                groups[(source, partition, "simulated_phone_band_8khz")].append(
                    (label_value, stressed_score)
                )
        except (OSError, AudioValidationError, ValueError, RuntimeError) as exc:
            raise ValueError(f"Could not process row {row_number} ({path}): {exc}") from exc

    if not groups:
        raise ValueError(f"No rows found for partition {args.partition!r}.")
    reports: list[dict[str, int | float | str]] = []
    for (source, partition, condition), records in sorted(groups.items()):
        y_true = np.asarray([record[0] for record in records])
        scores = np.asarray([record[1] for record in records])
        if len(np.unique(y_true)) != 2:
            raise ValueError(f"{source}/{partition} needs both bona_fide and spoof examples.")
        y_pred = (scores >= 0).astype(int)
        false_positives = int(np.sum((y_true == 0) & (y_pred == 1)))
        false_negatives = int(np.sum((y_true == 1) & (y_pred == 0)))
        report = {
            "source": source,
            "partition": partition,
            "condition": condition,
            "samples": len(records),
            "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
            "eer": equal_error_rate(y_true, scores),
            "false_positives": false_positives,
            "false_negatives": false_negatives,
        }
        reports.append(report)
        print(
            f"source={source} partition={partition} condition={condition} samples={len(records)} "
            f"balanced_accuracy={report['balanced_accuracy']:.4f} "
            f"eer={report['eer']:.4f} "
            f"false_positives={false_positives} false_negatives={false_negatives}"
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "model": detector.model_id,
                "threshold": 0,
                "phone_stress": args.phone_stress,
                "phone_stress_description": (
                    "Simulated 300–3400 Hz band limit and 8 kHz resampling; not a real call recording."
                    if args.phone_stress
                    else None
                ),
                "reports": reports,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved local evaluation report to {args.output}")


if __name__ == "__main__":
    main()
