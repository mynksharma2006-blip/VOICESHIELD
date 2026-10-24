import argparse
import csv
from dataclasses import dataclass
from io import BytesIO
import json
from pathlib import Path
import sys

import av
import numpy as np
from scipy.signal import butter, resample_poly, sosfiltfilt
from sklearn.metrics import balanced_accuracy_score, roc_curve

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audio import AudioValidationError, decode_audio
from app.inference.aasist import AASISTDetector
from app.inference.decision import classify


SAMPLE_RATE = 16_000
MAX_EER_RATIO = 2.5
BACKEND_DIR = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class Condition:
    name: str
    phone_band: bool = False
    codec: str | None = None
    snr_db: float | None = None


CONDITIONS = (
    Condition("clean"),
    Condition("telephone_8khz", phone_band=True),
    Condition("opus_16kbps", codec="opus"),
    Condition("aac_16kbps", codec="aac"),
    Condition("noise_20db", snr_db=20),
    Condition("noise_10db", snr_db=10),
    Condition("telephone_8khz_opus_16kbps_noise_10db", True, "opus", 10),
    Condition("telephone_8khz_aac_16kbps_noise_10db", True, "aac", 10),
)


def phone_band_8khz(samples: np.ndarray) -> np.ndarray:
    sos = butter(6, (300, 3400), btype="bandpass", fs=SAMPLE_RATE, output="sos")
    filtered = sosfiltfilt(sos, samples)
    return resample_poly(resample_poly(filtered, 1, 2), 2, 1).astype(np.float32)


def codec_roundtrip(samples: np.ndarray, codec: str) -> np.ndarray:
    if codec == "opus":
        codec_name, container_format = "libopus", "ogg"
    elif codec == "aac":
        codec_name, container_format = "aac", "adts"
    else:
        raise ValueError(f"Unsupported codec: {codec}")

    encoded = BytesIO()
    with av.open(encoded, mode="w", format=container_format) as container:
        stream = container.add_stream(codec_name, rate=SAMPLE_RATE)
        stream.layout = "mono"
        stream.bit_rate = 16_000
        frame = av.AudioFrame.from_ndarray(
            np.asarray(samples, dtype=np.float32)[None, :],
            format="fltp",
            layout="mono",
        )
        frame.sample_rate = SAMPLE_RATE
        for packet in stream.encode(frame):
            container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)

    decoded_parts: list[np.ndarray] = []
    with av.open(BytesIO(encoded.getvalue()), mode="r") as container:
        stream = next(item for item in container.streams if item.type == "audio")
        resampler = av.AudioResampler(format="flt", layout="mono", rate=SAMPLE_RATE)
        for frame in container.decode(stream):
            decoded_parts.extend(
                output.to_ndarray().reshape(-1)
                for output in resampler.resample(frame)
            )
        decoded_parts.extend(
            output.to_ndarray().reshape(-1)
            for output in resampler.resample(None)
        )
    if not decoded_parts:
        raise RuntimeError(f"{codec_name} produced no decoded audio.")
    decoded = np.concatenate(decoded_parts).astype(np.float32)
    if len(decoded) < len(samples):
        decoded = np.pad(decoded, (0, len(samples) - len(decoded)))
    return decoded[: len(samples)]


def add_noise(samples: np.ndarray, snr_db: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    signal_rms = float(np.sqrt(np.mean(np.square(samples, dtype=np.float64))))
    noise = rng.standard_normal(len(samples)).astype(np.float32)
    noise_rms = float(np.sqrt(np.mean(np.square(noise, dtype=np.float64))))
    if signal_rms <= 1e-12 or noise_rms <= 1e-12:
        raise ValueError("Cannot add SNR-controlled noise to silent audio.")
    noise *= np.float32(signal_rms / (noise_rms * 10 ** (snr_db / 20)))
    noisy = samples + noise
    peak = float(np.max(np.abs(noisy)))
    return (noisy / peak).astype(np.float32) if peak > 1 else noisy


def apply_condition(samples: np.ndarray, condition: Condition, seed: int) -> np.ndarray:
    transformed = samples.astype(np.float32, copy=True)
    if condition.phone_band:
        transformed = phone_band_8khz(transformed)
    if condition.codec:
        transformed = codec_roundtrip(transformed, condition.codec)
    if condition.snr_db is not None:
        transformed = add_noise(transformed, condition.snr_db, seed)
    if not np.isfinite(transformed).all():
        raise ValueError(f"Condition {condition.name} produced non-finite samples.")
    return transformed


def equal_error_rate(labels: np.ndarray, scores: np.ndarray) -> float:
    false_positive_rate, true_positive_rate, _ = roc_curve(labels, scores)
    false_negative_rate = 1.0 - true_positive_rate
    differences = false_positive_rate - false_negative_rate
    crossings = np.flatnonzero(np.diff(np.sign(differences)))
    if len(crossings):
        index = int(crossings[0])
        x0, x1 = false_positive_rate[index : index + 2]
        y0, y1 = false_negative_rate[index : index + 2]
        fraction = 0.0 if (y1 - x1) == (y0 - x0) else (y0 - x0) / ((y1 - x1) - (y0 - x0))
        return float(x0 + fraction * (x1 - x0))
    index = int(np.argmin(np.abs(differences)))
    return float((false_positive_rate[index] + false_negative_rate[index]) / 2)


def parse_label(value: str, line_number: int) -> int:
    normalized = value.strip().lower()
    if normalized in {"bona_fide", "bonafide", "genuine", "real"}:
        return 1
    if normalized in {"spoof", "fake", "synthetic"}:
        return 0
    raise ValueError(f"Unknown label at manifest row {line_number}: {value!r}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate AASIST phone-band, codec, and noise robustness on a labeled manifest."
    )
    parser.add_argument("manifest", type=Path, help="CSV with path,label,partition,source columns")
    parser.add_argument("--model", type=Path, default=BACKEND_DIR / "models" / "aasist.onnx")
    parser.add_argument("--partition", default="dev")
    parser.add_argument("--seed", type=int, default=1729)
    parser.add_argument("--output", type=Path, default=BACKEND_DIR / "results" / "phase2_metrics.json")
    parser.add_argument(
        "--max-eer-ratio",
        type=float,
        default=MAX_EER_RATIO,
        help="Maximum permitted stressed/clean EER ratio; this is reported, not used to tune.",
    )
    args = parser.parse_args()
    if args.max_eer_ratio <= 0:
        raise ValueError("--max-eer-ratio must be positive.")

    detector = AASISTDetector(args.model)
    with args.manifest.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError("The evaluation manifest is empty.")
    for required in ("path", "label", "partition"):
        if required not in rows[0]:
            raise ValueError(f"Manifest is missing required column {required!r}.")

    records: list[dict[str, int | float | str]] = []
    for row_number, row in enumerate(rows, start=2):
        if row.get("partition", "").strip().lower() != args.partition.lower():
            continue
        label = parse_label(row.get("label", ""), row_number)
        path = Path(row["path"])
        if not path.is_absolute():
            path = args.manifest.parent / path
        try:
            processed = decode_audio(path.read_bytes())
            for condition_index, condition in enumerate(CONDITIONS):
                transformed = apply_condition(
                    processed.samples,
                    condition,
                    args.seed + row_number * len(CONDITIONS) + condition_index,
                )
                score = detector.score(transformed)
                records.append(
                    {
                        "row": row_number,
                        "file": path.name,
                        "source": row.get("source", "unspecified").strip() or "unspecified",
                        "label": label,
                        "condition": condition.name,
                        "score": score,
                    }
                )
        except (OSError, AudioValidationError, ValueError, RuntimeError, av.error.FFmpegError) as exc:
            raise ValueError(f"Could not evaluate manifest row {row_number} ({path}): {exc}") from exc

    if not records:
        raise ValueError(f"No evaluation rows found for partition {args.partition!r}.")

    clean_eers: dict[str, float] = {}
    grouped: dict[tuple[str, str], list[dict[str, int | float | str]]] = {}
    for record in records:
        key = (str(record["source"]), str(record["condition"]))
        grouped.setdefault(key, []).append(record)
    reports: list[dict[str, int | float | str | bool | None]] = []
    for (source, condition), group in sorted(grouped.items()):
        labels = np.asarray([int(record["label"]) for record in group])
        scores = np.asarray([float(record["score"]) for record in group])
        if len(np.unique(labels)) != 2:
            raise ValueError(f"{source}/{condition} needs both bona fide and spoof clips.")
        eer = equal_error_rate(labels, scores)
        predictions = scores >= 0
        false_positives = int(np.sum((labels == 0) & predictions))
        false_negatives = int(np.sum((labels == 1) & ~predictions))
        report: dict[str, int | float | str | bool | None] = {
            "source": source,
            "partition": args.partition,
            "condition": condition,
            "samples": len(group),
            "bona_fide": int(np.sum(labels == 1)),
            "spoof": int(np.sum(labels == 0)),
            "balanced_accuracy_at_zero": float(balanced_accuracy_score(labels, predictions)),
            "eer": eer,
            "false_positives_at_zero": false_positives,
            "false_negatives_at_zero": false_negatives,
        }
        if condition == "clean":
            clean_eers[source] = eer
        reports.append(report)

    for report in reports:
        source = str(report["source"])
        clean_eer = clean_eers.get(source)
        if clean_eer is None:
            raise ValueError(f"{source} is missing a clean baseline condition.")
        eer = float(report["eer"])
        ratio = eer / clean_eer if clean_eer > 0 else (1.0 if eer == 0 else None)
        report["clean_eer"] = clean_eer
        report["eer_ratio_vs_clean"] = ratio
        report["within_max_eer_ratio"] = (
            eer == 0 if clean_eer == 0 else ratio is not None and ratio <= args.max_eer_ratio
        )
        print(
            f"source={source} condition={report['condition']} n={report['samples']} "
            f"EER={eer:.4f} clean_EER={clean_eer:.4f} "
            f"ratio={ratio if ratio is not None else 'undefined'} "
            f"within_{args.max_eer_ratio:g}x={report['within_max_eer_ratio']}"
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(
            {
                "model": detector.model_id,
                "manifest": str(args.manifest),
                "partition": args.partition,
                "seed": args.seed,
                "score_polarity": "higher is bona fide; threshold is fixed at zero",
                "codecs": {
                    "opus": "libopus, Ogg container, 16 kbps, 16 kHz mono, in-memory encode/decode",
                    "aac": "AAC, ADTS container, 16 kbps, 16 kHz mono, in-memory encode/decode",
                },
                "noise": "Independent Gaussian noise at 20 dB and 10 dB SNR; combinations use 10 dB.",
                "max_eer_ratio": args.max_eer_ratio,
                "reports": reports,
                "limitations": [
                    "Results apply only to the labeled manifest and transformations defined by this script.",
                    "EER uses all scores for measurement; no evaluation scores are used to tune a threshold.",
                    "This report does not establish performance on live telephone networks or unseen datasets.",
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    predictions_path = args.output.with_name(f"{args.output.stem}_scores.csv")
    with predictions_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
    print(f"Saved Phase 2 report to {args.output}")
    print(f"Saved per-clip scores to {predictions_path}")


if __name__ == "__main__":
    main()
