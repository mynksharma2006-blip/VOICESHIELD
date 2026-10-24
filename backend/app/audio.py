from dataclasses import dataclass
from io import BytesIO

import numpy as np
import av
import soundfile as sf
from scipy.signal import resample_poly

from app.config import MAX_DURATION_S, MIN_DURATION_S, SAMPLE_RATE


class AudioValidationError(ValueError):
    pass


@dataclass
class ProcessedAudio:
    samples: np.ndarray
    duration_s: float
    warnings: list[str]
    quality_blockers: list[str]


def _decode_with_av(content: bytes) -> tuple[np.ndarray, int]:
    with av.open(BytesIO(content), mode="r") as container:
        stream = next((item for item in container.streams if item.type == "audio"), None)
        if stream is None:
            raise ValueError("The file does not contain an audio stream.")

        sample_rate = stream.codec_context.sample_rate or stream.rate
        if not sample_rate:
            raise ValueError("The audio stream has an invalid sample rate.")

        resampler = av.AudioResampler(format="flt", layout="mono", rate=sample_rate)
        chunks = [
            frame.to_ndarray().reshape(-1)
            for decoded in container.decode(stream)
            for frame in resampler.resample(decoded)
        ]
        chunks.extend(frame.to_ndarray().reshape(-1) for frame in resampler.resample(None))

    if not chunks:
        return np.empty(0, dtype=np.float32), sample_rate
    return np.concatenate(chunks), sample_rate


def inspect_quality(samples: np.ndarray, sample_rate: int) -> tuple[list[str], list[str]]:
    warnings: list[str] = []
    blockers: list[str] = []
    peak = float(np.max(np.abs(samples)))
    clipped_fraction = float(np.mean(np.abs(samples) >= 0.999))
    if clipped_fraction > 0.001:
        warnings.append("Audio clipping detected; distorted peaks can make the result less reliable.")
        if clipped_fraction > 0.02:
            blockers.append("Severe clipping makes this clip inconclusive.")

    frame_length = max(1, int(sample_rate * 0.020))
    hop_length = max(1, int(sample_rate * 0.010))
    if len(samples) < frame_length:
        blockers.append("There is not enough audio to check for speech activity.")
        return warnings, blockers

    frame_count = 1 + (len(samples) - frame_length) // hop_length
    indices = np.arange(frame_length)[None, :] + hop_length * np.arange(frame_count)[:, None]
    frames = samples[indices].astype(np.float64, copy=False)
    frame_rms = np.sqrt(np.mean(np.square(frames), axis=1))
    maximum_rms = float(frame_rms.max(initial=0.0))
    if maximum_rms < 1e-5:
        blockers.append("No speech-like audio activity was detected; the result is inconclusive.")
        return warnings, blockers

    threshold = max(10 ** (-45 / 20), maximum_rms * 10 ** (-35 / 20))
    active = frame_rms >= threshold
    active_duration_s = float(active.sum() * hop_length / sample_rate)
    if active_duration_s < 0.5:
        blockers.append("Very little speech-like activity was detected; the result is inconclusive.")
    if peak < 1e-4:
        warnings.append("This clip is very quiet or mostly silent; the result may be unreliable.")
    elif active.any():
        noise_rms = float(np.percentile(frame_rms[~active], 75)) if (~active).any() else 0.0
        speech_rms = float(np.median(frame_rms[active]))
        signal_to_noise_db = (
            20 * np.log10(max(speech_rms, 1e-12) / noise_rms)
            if noise_rms > 0
            else float("inf")
        )
        if signal_to_noise_db < 8:
            warnings.append("Possible heavy background noise; it may make the result less reliable.")
        if signal_to_noise_db < 5:
            blockers.append("Background noise is too strong for a reliable voice check; the result is inconclusive.")
    windowed = frames * np.hamming(frame_length)[None, :]
    power = np.abs(np.fft.rfft(windowed, axis=1)) ** 2
    frequencies = np.fft.rfftfreq(frame_length, 1 / sample_rate)
    speech_band = power[:, (frequencies >= 300) & (frequencies <= min(7000, sample_rate / 2))]
    if speech_band.shape[1] > 0:
        flatness = np.exp(np.mean(np.log(np.maximum(speech_band, 1e-20)), axis=1)) / np.maximum(
            np.mean(speech_band, axis=1), 1e-20
        )
        median_flatness = float(np.median(flatness[active]))
        if median_flatness > 0.5:
            warnings.append(
                "Possible broadband background noise; this approximate quality check may be less reliable."
            )
            if median_flatness > 0.8:
                blockers.append("Broadband noise overwhelms this clip; the result is inconclusive.")
    return warnings, blockers


def decode_audio(content: bytes) -> tuple[np.ndarray, int]:
    try:
        samples, sample_rate = sf.read(BytesIO(content), dtype="float32", always_2d=True)
    except (RuntimeError, ValueError, sf.SoundFileRuntimeError) as exc:
        try:
            mono, sample_rate = _decode_with_av(content)
        except (av.error.FFmpegError, ValueError, StopIteration) as fallback_exc:
            raise AudioValidationError(
                "Could not decode this audio. Try a WAV, FLAC, or OGG file."
            ) from fallback_exc
        samples = mono[:, np.newaxis]

    if samples.size == 0 or sample_rate <= 0:
        raise AudioValidationError("The uploaded audio is empty or has an invalid sample rate.")
    if not np.isfinite(samples).all():
        raise AudioValidationError("The audio contains invalid numeric samples.")

    mono = samples.mean(axis=1)
    duration_s = len(mono) / sample_rate
    if duration_s < MIN_DURATION_S:
        raise AudioValidationError(
            f"This clip is {duration_s:.2f} seconds; at least {MIN_DURATION_S:g} seconds are required."
        )
    if duration_s > MAX_DURATION_S:
        raise AudioValidationError(
            f"This clip is {duration_s:.2f} seconds; the maximum is {MAX_DURATION_S:g} seconds."
        )

    warnings, quality_blockers = inspect_quality(mono, sample_rate)

    if sample_rate != SAMPLE_RATE:
        divisor = np.gcd(sample_rate, SAMPLE_RATE)
        mono = resample_poly(mono, SAMPLE_RATE // divisor, sample_rate // divisor).astype(
            np.float32
        )
    else:
        mono = mono.astype(np.float32, copy=False)

    peak = float(np.max(np.abs(mono)))
    if peak > 0:
        mono = mono / peak

    return ProcessedAudio(
        samples=mono,
        duration_s=duration_s,
        warnings=warnings,
        quality_blockers=quality_blockers,
    )
