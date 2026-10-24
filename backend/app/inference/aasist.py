from pathlib import Path

import numpy as np
import onnxruntime as ort


class AASISTDetector:
    model_id = "AASIST (ASVspoof 2019 LA pretrained)"
    window_samples = 64_600

    def __init__(self, path: Path):
        options = ort.SessionOptions()
        options.intra_op_num_threads = 2
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(path),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if (
            len(inputs) != 1
            or inputs[0].shape[-1] != self.window_samples
            or len(outputs) != 1
            or outputs[0].shape[-1] != 2
        ):
            raise ValueError(f"Model file {path} does not have the expected AASIST input/output shape.")
        self.input_name = inputs[0].name
        self.output_name = outputs[0].name

    def score(self, samples: np.ndarray) -> float:
        if samples.size == 0:
            raise ValueError("Audio is empty.")
        samples = np.asarray(samples, dtype=np.float32).reshape(-1)
        if len(samples) <= self.window_samples:
            windows = [samples]
        else:
            stride = self.window_samples // 2
            starts = list(range(0, len(samples) - self.window_samples + 1, stride))
            last_start = len(samples) - self.window_samples
            if starts[-1] != last_start:
                starts.append(last_start)
            windows = [samples[start : start + self.window_samples] for start in starts]
        return float(np.mean(self.score_batch(windows)))

    def score_batch(self, windows: list[np.ndarray]) -> np.ndarray:
        if not windows:
            raise ValueError("At least one audio window is required.")
        scores: list[np.ndarray] = []
        for start in range(0, len(windows), 4):
            batch = []
            for samples in windows[start : start + 4]:
                if samples.size == 0:
                    raise ValueError("Audio window is empty.")
                window = np.asarray(samples, dtype=np.float32).reshape(-1)
                if len(window) < self.window_samples:
                    repeats = (self.window_samples + len(window) - 1) // len(window)
                    window = np.tile(window, repeats)[: self.window_samples]
                elif len(window) > self.window_samples:
                    window = window[: self.window_samples]
                batch.append(window)
            logits = self.session.run(
                [self.output_name],
                {self.input_name: np.asarray(batch, dtype=np.float32)},
            )[0]
            if logits.shape != (len(batch), 2) or not np.isfinite(logits).all():
                raise RuntimeError("AASIST returned invalid logits.")
            scores.append((logits[:, 1] - logits[:, 0]).astype(np.float32))
        return np.concatenate(scores)
