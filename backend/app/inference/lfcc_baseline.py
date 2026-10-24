from pathlib import Path

import joblib
import numpy as np
from scipy.fft import dct


def extract_lfcc(samples: np.ndarray, sample_rate: int = 16_000) -> np.ndarray:
    """Return mean/std summaries of 20 linear-frequency cepstral coefficients."""
    frame_length = int(sample_rate * 0.025)
    hop_length = int(sample_rate * 0.010)
    if len(samples) < frame_length:
        raise ValueError("Audio is too short to extract LFCC features.")

    frame_count = 1 + (len(samples) - frame_length) // hop_length
    indices = np.arange(frame_length)[None, :] + hop_length * np.arange(frame_count)[:, None]
    frames = samples[indices].astype(np.float64, copy=False)
    frames *= np.hamming(frame_length)[None, :]

    fft_size = 1 << (frame_length - 1).bit_length()
    power = np.abs(np.fft.rfft(frames, n=fft_size, axis=1)) ** 2
    frequencies = np.linspace(0, sample_rate / 2, power.shape[1])
    band_edges = np.linspace(0, sample_rate / 2, 22)
    filterbank = np.zeros((20, len(frequencies)), dtype=np.float64)
    for band in range(20):
        left, center, right = band_edges[band : band + 3]
        filterbank[band] = np.maximum(
            0.0,
            np.minimum(
                (frequencies - left) / max(center - left, 1e-12),
                (right - frequencies) / max(right - center, 1e-12),
            ),
        )

    log_energy = np.log(np.maximum(power @ filterbank.T, 1e-10))
    coefficients = dct(log_energy, type=2, norm="ortho", axis=1)[:, :20]
    return np.concatenate((coefficients.mean(axis=0), coefficients.std(axis=0))).astype(
        np.float32
    )


class LFCCBaseline:
    model_id = "LFCC + scikit-learn baseline"

    def __init__(self, path: Path):
        bundle = joblib.load(path)
        if not isinstance(bundle, dict) or "classifier" not in bundle:
            raise ValueError(f"Model file {path} is not a VoiceShield LFCC model bundle.")
        self.classifier = bundle["classifier"]
        self.model_id = str(bundle.get("model_id", self.model_id))

    def score(self, samples: np.ndarray) -> float:
        features = extract_lfcc(samples).reshape(1, -1)
        classes = list(self.classifier.classes_)
        if 1 not in classes:
            raise ValueError("The local classifier must use label 1 for bona fide audio.")
        return float(self.classifier.decision_function(features)[0])
