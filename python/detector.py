"""
Dual doorbell detector — Fingerprint (FFT correlation) + ML (YAMNet/TFLite).

Both classes share the same interface (BaseDetector) so the sidecar can
switch between them at runtime via a single UI toggle.
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
from scipy import signal as scipy_signal

logger = logging.getLogger(__name__)

MODELS_DIR = Path(__file__).parent / "models"
SAMPLES_DIR = Path(__file__).parent / "samples"
RATE = 16000

DEFAULT_THRESHOLD = 0.7
COOLDOWN_S = 5.0


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Base interface
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class BaseDetector(ABC):
    """Common interface for both detection methods."""

    def __init__(
        self,
        threshold: float = DEFAULT_THRESHOLD,
        cooldown_s: float = COOLDOWN_S,
    ):
        self.threshold = threshold
        self.cooldown_s = cooldown_s
        self._last_detection_time = 0.0

    @abstractmethod
    def load(self) -> None:
        """Load model / fingerprints. May raise FileNotFoundError."""

    @property
    @abstractmethod
    def is_loaded(self) -> bool: ...

    @property
    @abstractmethod
    def method_name(self) -> str: ...

    @abstractmethod
    def compute_confidence(self, waveform: np.ndarray) -> float:
        """Return confidence score 0–1 for a ~1s audio chunk."""

    def process_chunk(self, waveform: np.ndarray) -> dict:
        """Full pipeline: confidence → detection decision → result dict."""
        t0 = time.monotonic()
        confidence = self.compute_confidence(waveform)
        now = time.time()

        is_detection = (
            confidence >= self.threshold
            and (now - self._last_detection_time) >= self.cooldown_s
        )
        if is_detection:
            self._last_detection_time = now
            logger.info(
                "%s: DOORBELL DETECTED (confidence=%.3f)",
                self.method_name, confidence,
            )

        return {
            "confidence": confidence,
            "is_detection": is_detection,
            "method": self.method_name,
            "timestamp": now,
            "inference_ms": (time.monotonic() - t0) * 1000,
        }

    def update_threshold(self, value: float) -> None:
        self.threshold = max(0.0, min(1.0, value))

    def update_cooldown(self, value: float) -> None:
        self.cooldown_s = max(0.0, value)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  1) Fingerprint detector — FFT cross-correlation
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class FingerprintDetector(BaseDetector):
    """
    Compares live audio against stored doorbell fingerprints using
    spectral cross-correlation. Works with a single sample — no training.

    Algorithm:
    1. Compute magnitude spectrum (FFT) of the audio chunk
    2. Normalize to unit energy
    3. Cross-correlate with each stored fingerprint spectrum
    4. Confidence = max correlation across all fingerprints
    """

    FINGERPRINTS_FILE = "fingerprints.json"

    def __init__(
        self,
        models_dir: Path = MODELS_DIR,
        samples_dir: Path = SAMPLES_DIR,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.models_dir = models_dir
        self.samples_dir = samples_dir
        self._fingerprints: list[np.ndarray] = []

    @property
    def method_name(self) -> str:
        return "fingerprint"

    @property
    def is_loaded(self) -> bool:
        return len(self._fingerprints) > 0

    def load(self) -> None:
        """Load fingerprints from disk, or build from doorbell samples."""
        fp_path = self.models_dir / self.FINGERPRINTS_FILE
        if fp_path.exists():
            data = json.loads(fp_path.read_text())
            self._fingerprints = [np.array(fp) for fp in data["fingerprints"]]
            logger.info("Loaded %d fingerprints from %s", len(self._fingerprints), fp_path)
        else:
            self._build_from_samples()

        if not self._fingerprints:
            raise FileNotFoundError(
                "No fingerprints available. Record at least one doorbell sample."
            )

    def _build_from_samples(self) -> None:
        """Build fingerprints from doorbell WAV samples."""
        from recorder import AudioRecorder

        sample_dir = self.samples_dir / "doorbell"
        if not sample_dir.exists():
            return

        for wav_path in sorted(sample_dir.glob("*.wav")):
            waveform = AudioRecorder.load_wav(wav_path)
            fp = self._compute_spectrum(waveform)
            self._fingerprints.append(fp)

        if self._fingerprints:
            self._save_fingerprints()
            logger.info("Built %d fingerprints from samples", len(self._fingerprints))

    def enroll(self, waveform: np.ndarray) -> int:
        """Add a new fingerprint from a waveform. Returns total count."""
        fp = self._compute_spectrum(waveform)
        self._fingerprints.append(fp)
        self._save_fingerprints()
        logger.info("Enrolled fingerprint #%d", len(self._fingerprints))
        return len(self._fingerprints)

    def clear(self) -> None:
        """Remove all fingerprints."""
        self._fingerprints.clear()
        fp_path = self.models_dir / self.FINGERPRINTS_FILE
        if fp_path.exists():
            fp_path.unlink()

    def _save_fingerprints(self) -> None:
        self.models_dir.mkdir(parents=True, exist_ok=True)
        data = {"fingerprints": [fp.tolist() for fp in self._fingerprints]}
        (self.models_dir / self.FINGERPRINTS_FILE).write_text(
            json.dumps(data)
        )

    @staticmethod
    def _compute_spectrum(waveform: np.ndarray) -> np.ndarray:
        """Compute normalized magnitude spectrum for correlation."""
        # Windowed FFT
        window = np.hanning(len(waveform))
        spectrum = np.abs(np.fft.rfft(waveform * window))
        # Log-magnitude (perceptual scale)
        spectrum = np.log1p(spectrum)
        # Normalize to unit energy
        norm = np.linalg.norm(spectrum)
        if norm > 0:
            spectrum /= norm
        return spectrum

    def compute_confidence(self, waveform: np.ndarray) -> float:
        if not self._fingerprints:
            return 0.0

        live_spectrum = self._compute_spectrum(waveform)

        # Correlate with each fingerprint, take max
        best = 0.0
        for fp in self._fingerprints:
            # Ensure same length (trim to shorter)
            min_len = min(len(live_spectrum), len(fp))
            corr = np.dot(live_spectrum[:min_len], fp[:min_len])
            best = max(best, corr)

        return float(np.clip(best, 0.0, 1.0))


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  2) ML detector — YAMNet embeddings + TFLite head
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

class MLDetector(BaseDetector):
    """
    Two-stage ML detector:
    1. YAMNet (TF Hub) extracts 1024-d embedding from ~1s audio
    2. TFLite classification head → doorbell probability

    Requires training (3+ samples per class) before use.
    """

    def __init__(self, models_dir: Path = MODELS_DIR, **kwargs):
        super().__init__(**kwargs)
        self.models_dir = models_dir
        self._yamnet_model = None
        self._tflite_interpreter = None
        self._input_details = None
        self._output_details = None

    @property
    def method_name(self) -> str:
        return "ml"

    @property
    def is_loaded(self) -> bool:
        return self._yamnet_model is not None and self._tflite_interpreter is not None

    def load(self) -> None:
        self._load_yamnet()
        self._load_tflite()
        logger.info("ML detector loaded")

    def _load_yamnet(self):
        import tensorflow_hub as hub
        self._yamnet_model = hub.load("https://tfhub.dev/google/yamnet/1")

    def _load_tflite(self):
        model_path = self.models_dir / "doorbell_head.tflite"
        if not model_path.exists():
            raise FileNotFoundError(
                f"TFLite model not found at {model_path}. Train first."
            )

        try:
            from tflite_runtime.interpreter import Interpreter
        except ImportError:
            import tensorflow as tf
            Interpreter = tf.lite.Interpreter

        self._tflite_interpreter = Interpreter(model_path=str(model_path))
        self._tflite_interpreter.allocate_tensors()
        self._input_details = self._tflite_interpreter.get_input_details()
        self._output_details = self._tflite_interpreter.get_output_details()

    def extract_embedding(self, waveform: np.ndarray) -> np.ndarray:
        import tensorflow as tf
        _, embeddings, _ = self._yamnet_model(waveform.astype(np.float32))
        return tf.reduce_mean(embeddings, axis=0).numpy()

    def get_mel_spectrogram(self, waveform: np.ndarray) -> np.ndarray:
        """Extract YAMNet's mel spectrogram (for UI visualization)."""
        _, _, spectrogram = self._yamnet_model(waveform.astype(np.float32))
        return spectrogram.numpy()

    def compute_confidence(self, waveform: np.ndarray) -> float:
        embedding = self.extract_embedding(waveform)
        inp = embedding.reshape(1, -1).astype(np.float32)
        self._tflite_interpreter.set_tensor(self._input_details[0]["index"], inp)
        self._tflite_interpreter.invoke()
        output = self._tflite_interpreter.get_tensor(self._output_details[0]["index"])
        return float(output[0][0])

    def get_model_metadata(self) -> dict | None:
        meta_path = self.models_dir / "metadata.json"
        if meta_path.exists():
            return json.loads(meta_path.read_text())
        return None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Mel spectrogram fallback (for fingerprint mode UI)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def compute_mel_spectrogram(waveform: np.ndarray, n_mels: int = 64) -> np.ndarray:
    """
    Compute a mel spectrogram using scipy (no TF needed).
    Used in fingerprint mode where YAMNet isn't loaded.
    """
    nperseg = 400  # 25ms at 16kHz
    noverlap = 240  # 15ms at 16kHz
    f, t, Sxx = scipy_signal.spectrogram(
        waveform, fs=RATE, nperseg=nperseg, noverlap=noverlap,
    )

    # Simple mel approximation: warp frequency axis with log spacing
    mel_freqs = np.linspace(0, 2595 * np.log10(1 + (RATE / 2) / 700), n_mels + 1)
    mel_freqs = 700 * (10 ** (mel_freqs / 2595) - 1)

    mel_spec = np.zeros((n_mels, Sxx.shape[1]))
    for i in range(n_mels):
        lo = mel_freqs[i]
        hi = mel_freqs[i + 1]
        mask = (f >= lo) & (f < hi)
        if mask.any():
            mel_spec[i] = Sxx[mask].mean(axis=0)

    return np.log1p(mel_spec * 1000)  # Log scale for visualization
