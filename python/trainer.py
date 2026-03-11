"""
Training pipeline for the ML detector (YAMNet fine-tuning → TFLite).

Also handles fingerprint enrollment from doorbell samples.
"""

import json
import logging
import time
import wave
from pathlib import Path

import numpy as np
import tensorflow as tf

logger = logging.getLogger(__name__)

SAMPLES_DIR = Path(__file__).parent / "samples"
MODELS_DIR = Path(__file__).parent / "models"
RATE = 16000
EMBEDDING_DIM = 1024


class DoorbellTrainer:
    """Extracts YAMNet embeddings, trains a binary classification head, exports TFLite."""

    def __init__(
        self,
        samples_dir: Path = SAMPLES_DIR,
        models_dir: Path = MODELS_DIR,
        epochs: int = 50,
        batch_size: int = 16,
    ):
        self.samples_dir = samples_dir
        self.models_dir = models_dir
        self.epochs = epochs
        self.batch_size = batch_size
        self._yamnet_model = None
        self._progress_callback = None

    def set_progress_callback(self, cb):
        """Set callback(epoch, total_epochs, loss, accuracy) for UI updates."""
        self._progress_callback = cb

    # -- YAMNet --

    def _load_yamnet(self):
        if self._yamnet_model is None:
            import tensorflow_hub as hub
            self._yamnet_model = hub.load("https://tfhub.dev/google/yamnet/1")
        return self._yamnet_model

    def _extract_embedding(self, waveform: np.ndarray) -> np.ndarray:
        yamnet = self._load_yamnet()
        _, embeddings, _ = yamnet(waveform.astype(np.float32))
        return tf.reduce_mean(embeddings, axis=0).numpy()

    # -- dataset --

    def _load_samples(self) -> tuple[np.ndarray, np.ndarray]:
        embeddings, labels = [], []

        for label, class_dir in enumerate(["not_doorbell", "doorbell"]):
            sample_dir = self.samples_dir / class_dir
            if not sample_dir.exists():
                continue
            wav_files = sorted(sample_dir.glob("*.wav"))
            logger.info("Loading %d samples from %s", len(wav_files), class_dir)

            for wav_path in wav_files:
                with wave.open(str(wav_path), "rb") as wf:
                    raw = wf.readframes(wf.getnframes())
                waveform = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
                embeddings.append(self._extract_embedding(waveform))
                labels.append(label)

        return np.array(embeddings), np.array(labels)

    # -- model --

    @staticmethod
    def _build_head() -> tf.keras.Model:
        model = tf.keras.Sequential([
            tf.keras.layers.Input(shape=(EMBEDDING_DIM,)),
            tf.keras.layers.Dense(128, activation="relu"),
            tf.keras.layers.Dropout(0.3),
            tf.keras.layers.Dense(64, activation="relu"),
            tf.keras.layers.Dropout(0.2),
            tf.keras.layers.Dense(1, activation="sigmoid"),
        ])
        model.compile(
            optimizer=tf.keras.optimizers.Adam(learning_rate=1e-3),
            loss="binary_crossentropy",
            metrics=["accuracy"],
        )
        return model

    # -- training --

    def train(self) -> dict:
        """Full pipeline: load samples → embed → train → export TFLite."""
        logger.info("Starting ML training pipeline")

        X, y = self._load_samples()
        if len(X) == 0:
            raise ValueError("No training samples found")

        n_pos = int(y.sum())
        n_neg = len(y) - n_pos
        if n_pos < 3 or n_neg < 3:
            raise ValueError(
                f"Need at least 3 samples per class. "
                f"Got {n_pos} doorbell, {n_neg} not_doorbell"
            )

        # Shuffle + split 80/20
        idx = np.random.permutation(len(X))
        X, y = X[idx], y[idx]
        split = int(0.8 * len(X))
        X_train, X_val = X[:split], X[split:]
        y_train, y_val = y[:split], y[split:]

        model = self._build_head()

        class _Progress(tf.keras.callbacks.Callback):
            def __init__(self, trainer):
                self.trainer = trainer

            def on_epoch_end(self, epoch, logs=None):
                if self.trainer._progress_callback:
                    self.trainer._progress_callback(
                        epoch + 1, self.trainer.epochs,
                        logs.get("loss", 0), logs.get("accuracy", 0),
                    )

        model.fit(
            X_train, y_train,
            validation_data=(X_val, y_val),
            epochs=self.epochs,
            batch_size=self.batch_size,
            callbacks=[_Progress(self)],
            verbose=0,
        )

        val_loss, val_acc = model.evaluate(X_val, y_val, verbose=0)

        # Export
        self.models_dir.mkdir(parents=True, exist_ok=True)
        tflite_path = self._export_tflite(model)
        self._save_metadata(val_acc, val_loss, n_pos, n_neg)

        metrics = {
            "val_accuracy": float(val_acc),
            "val_loss": float(val_loss),
            "n_doorbell": n_pos,
            "n_not_doorbell": n_neg,
            "epochs": self.epochs,
            "model_path": str(tflite_path),
        }
        logger.info("Training complete: %s", metrics)
        return metrics

    def _export_tflite(self, model: tf.keras.Model) -> Path:
        converter = tf.lite.TFLiteConverter.from_keras_model(model)
        converter.optimizations = [tf.lite.Optimize.DEFAULT]
        tflite_model = converter.convert()
        path = self.models_dir / "doorbell_head.tflite"
        path.write_bytes(tflite_model)
        return path

    def _save_metadata(self, accuracy: float, loss: float, n_pos: int, n_neg: int):
        meta = {
            "trained_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "val_accuracy": float(accuracy),
            "val_loss": float(loss),
            "n_doorbell": n_pos,
            "n_not_doorbell": n_neg,
            "epochs": self.epochs,
            "model_file": "doorbell_head.tflite",
        }
        (self.models_dir / "metadata.json").write_text(json.dumps(meta, indent=2))
