import argparse
import csv
from dataclasses import dataclass
import json
from pathlib import Path
import sys

import numpy as np
import soundfile as sf
from sklearn.metrics import f1_score, precision_score, recall_score

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audio import AudioValidationError, decode_audio
from app.inference.aasist import AASISTDetector


SAMPLE_RATE = 16_000
FRAME_DURATION_S = 0.5
FRAME_SAMPLES = int(SAMPLE_RATE * FRAME_DURATION_S)
CROSSFADE_S = 0.02
BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class SourceClip:
    path: Path
    samples: np.ndarray
    source: str


def read_manifest(path: Path, partition: str) -> tuple[list[SourceClip], list[SourceClip]]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("The manifest is empty.")
    for required in ("path", "label", "partition"):
        if required not in rows[0]:
            raise ValueError(f"Manifest is missing required column {required!r}.")

    genuine: list[SourceClip] = []
    spoof: list[SourceClip] = []
    for row_number, row in enumerate(rows, start=2):
        if row.get("partition", "").strip().lower() != partition.lower():
            continue
        label = row.get("label", "").strip().lower()
        if label in {"bona_fide", "bonafide", "genuine", "real"}:
            destination = genuine
        elif label in {"spoof", "fake", "synthetic"}:
            destination = spoof
        else:
            raise ValueError(f"Unknown label at manifest row {row_number}: {label!r}")
        clip_path = Path(row["path"])
        if not clip_path.is_absolute():
            clip_path = path.parent / clip_path
        try:
            processed = decode_audio(clip_path.read_bytes())
        except (OSError, AudioValidationError) as exc:
            raise ValueError(f"Could not process manifest row {row_number} ({clip_path}): {exc}") from exc
        destination.append(
            SourceClip(
                path=clip_path,
                samples=processed.samples,
                source=row.get("source", "unspecified").strip() or "unspecified",
            )
        )
    if not genuine or not spoof:
        raise ValueError(
            f"Partition {partition!r} must contain genuine and spoof source clips."
        )
    return genuine, spoof


def make_partial_fake(
    genuine: np.ndarray,
    spoof: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, float, float]:
    if len(genuine) < FRAME_SAMPLES or len(spoof) < FRAME_SAMPLES:
        raise ValueError("Source clips must be at least 0.5 seconds long.")
    available_frames = len(genuine) // FRAME_SAMPLES
    segment_frames = int(rng.integers(1, min(6, available_frames) + 1))
    start_frame = int(rng.integers(0, available_frames - segment_frames + 1))
    start = start_frame * FRAME_SAMPLES
    end = min(len(genuine), start + segment_frames * FRAME_SAMPLES)
    replacement = spoof[int(rng.integers(0, max(1, len(spoof) - (end - start) + 1))) :]
    replacement = replacement[: end - start]
    if len(replacement) < end - start:
        replacement = np.pad(replacement, (0, end - start - len(replacement)), mode="wrap")

    composite = genuine.copy()
    composite[start:end] = replacement
    fade_samples = min(
        int(SAMPLE_RATE * CROSSFADE_S),
        (end - start) // 2,
    )
    if fade_samples:
        fade = np.linspace(0, 1, fade_samples, dtype=np.float32)
        if start > 0:
            composite[start : start + fade_samples] = (
                genuine[start : start + fade_samples] * (1 - fade)
                + replacement[:fade_samples] * fade
            )
        if end < len(genuine):
            composite[end - fade_samples : end] = (
                replacement[-fade_samples:] * (1 - fade)
                + genuine[end - fade_samples : end] * fade
            )

    peak = float(np.max(np.abs(composite)))
    if peak > 0:
        composite = (composite / peak).astype(np.float32)
    return composite, start / SAMPLE_RATE, end / SAMPLE_RATE


def centered_windows(samples: np.ndarray) -> tuple[list[np.ndarray], np.ndarray]:
    frame_count = int(np.ceil(len(samples) / FRAME_SAMPLES))
    centers = (np.arange(frame_count, dtype=np.float64) + 0.5) * FRAME_DURATION_S
    half_window = AASISTDetector.window_samples // 2
    windows = []
    for center_s in centers:
        start = int(round(center_s * SAMPLE_RATE)) - half_window
        end = start + AASISTDetector.window_samples
        pad_left = max(0, -start)
        pad_right = max(0, end - len(samples))
        padded = np.pad(samples, (pad_left, pad_right), mode="reflect")
        window = padded[max(0, start) : max(0, start) + AASISTDetector.window_samples]
        windows.append(window)
    return windows, centers


def interval_to_frames(start_s: float, end_s: float, centers: np.ndarray) -> np.ndarray:
    return (centers >= start_s) & (centers < end_s)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create and evaluate partial-fake composites with 0.5-second segment labels."
    )
    parser.add_argument("manifest", type=Path, help="CSV with path,label,partition[,source] columns")
    parser.add_argument("--model", type=Path, default=BACKEND_DIR / "models" / "aasist.onnx")
    parser.add_argument("--partition", default="dev")
    parser.add_argument("--count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument("--output-dir", type=Path, default=BACKEND_DIR / "data" / "phase2_partial_fakes")
    parser.add_argument("--report", type=Path, default=BACKEND_DIR / "results" / "partial_fake_metrics.json")
    args = parser.parse_args()
    if args.count < 1:
        raise ValueError("--count must be at least one.")

    genuine, spoof = read_manifest(args.manifest, args.partition)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    detector = AASISTDetector(args.model)
    rng = np.random.default_rng(args.seed)
    records: list[dict[str, str | int | float]] = []
    truth_frames: list[int] = []
    predicted_frames: list[int] = []
    per_clip: list[dict[str, str | int | float]] = []
    fade_s = CROSSFADE_S

    for index in range(args.count):
        real_clip = genuine[index % len(genuine)]
        fake_clip = spoof[(index // len(genuine) + index) % len(spoof)]
        composite, start_s, end_s = make_partial_fake(
            real_clip.samples,
            fake_clip.samples,
            rng,
        )
        output_path = args.output_dir / f"partial_fake_{index + 1:03d}.wav"
        sf.write(output_path, composite, SAMPLE_RATE, subtype="PCM_16")

        windows, centers = centered_windows(composite)
        scores = detector.score_batch(windows)
        predictions = scores < 0
        truth = interval_to_frames(start_s, end_s, centers)
        truth_frames.extend(truth.astype(int).tolist())
        predicted_frames.extend(predictions.astype(int).tolist())
        clip_f1 = float(f1_score(truth, predictions, zero_division=0))
        per_clip.append(
            {
                "clip": output_path.name,
                "genuine_source": real_clip.path.name,
                "spoof_source": fake_clip.path.name,
                "duration_s": len(composite) / SAMPLE_RATE,
                "fake_start_s": start_s,
                "fake_end_s": end_s,
                "f1_at_0.5s": clip_f1,
            }
        )
        for center_s, score, truth_value, prediction in zip(
            centers,
            scores,
            truth,
            predictions,
        ):
            records.append(
                {
                    "clip": output_path.name,
                    "center_s": float(center_s),
                    "label_fake": int(truth_value),
                    "predicted_fake": int(prediction),
                    "score_raw": float(score),
                }
            )
        print(
            f"{output_path.name}: fake={start_s:.2f}-{end_s:.2f}s "
            f"frames={len(centers)} f1={clip_f1:.3f}"
        )

    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "model": detector.model_id,
        "source_manifest": str(args.manifest),
        "source_partition": args.partition,
        "count": args.count,
        "seed": args.seed,
        "frame_duration_s": FRAME_DURATION_S,
        "decision_rule": "predicted fake when centered AASIST window score is below zero",
        "precision_at_0.5s": float(precision_score(truth_frames, predicted_frames, zero_division=0)),
        "recall_at_0.5s": float(recall_score(truth_frames, predicted_frames, zero_division=0)),
        "f1_at_0.5s": float(f1_score(truth_frames, predicted_frames, zero_division=0)),
        "macro_clip_f1_at_0.5s": float(
            np.mean([float(item["f1_at_0.5s"]) for item in per_clip])
        ),
        "localization_method": (
            "One centered 4.04-second AASIST window per 0.5-second frame. "
            "The output hop is 0.5 seconds, but AASIST's receptive field is about 4.04 seconds."
        ),
        "crossfade_s": fade_s,
        "sources": {
            "genuine_count": len(genuine),
            "spoof_count": len(spoof),
            "distinct_source_pairs": len(
                {
                    (str(item["genuine_source"]), str(item["spoof_source"]))
                    for item in per_clip
                }
            ),
        },
        "clips": per_clip,
        "limitations": [
            "Generated clips are synthetic splices, not natural real-time voice-clone calls.",
            "Multiple composites may reuse source recordings; this script is not an independent benchmark.",
            "A 0.5-second output hop does not give 0.5-second model receptive-field resolution.",
            "Evaluate untouched generators and natural partial-fake recordings separately.",
        ],
    }
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    prediction_path = args.report.with_name(f"{args.report.stem}_frames.csv")
    with prediction_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    print(f"Aggregate frame F1 at 0.5s: {report['f1_at_0.5s']:.4f}")
    print(f"Saved report to {args.report}")
    print(f"Saved frame scores to {prediction_path}")


if __name__ == "__main__":
    main()
