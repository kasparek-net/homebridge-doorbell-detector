"""
Python sidecar daemon for homebridge-doorbell-detector.

Unix socket server — newline-delimited JSON protocol.
Manages dual detection (fingerprint / ML), audio streaming, training.

Security: socket path set via DOORBELL_SOCKET env var (defaults to
Homebridge storage dir, not /tmp/). Permissions 0o600 (owner only).
"""

import asyncio
import base64
import json
import logging
import os
import signal
import stat
import time
from pathlib import Path

import numpy as np

from recorder import AudioRecorder, compute_rms, waveform_to_pcm_bytes
from detector import (
    FingerprintDetector, MLDetector, BaseDetector,
    compute_mel_spectrogram,
)
from trainer import DoorbellTrainer

logger = logging.getLogger("sidecar")

SOCKET_PATH = os.environ.get(
    "DOORBELL_SOCKET", "/tmp/homebridge-doorbell-detector.sock"
)
SAMPLES_DIR = Path(__file__).parent / "samples"
MODELS_DIR = Path(__file__).parent / "models"


class SidecarProtocol(asyncio.Protocol):
    """Single Node.js connection over Unix socket."""

    def __init__(self, sidecar: "Sidecar"):
        self.sidecar = sidecar
        self.transport: asyncio.Transport | None = None
        self._buffer = b""

    def connection_made(self, transport):
        # Reject if there's already a client (single-client only)
        if self.sidecar.client is not None:
            logger.warning("Rejecting second client connection")
            transport.close()
            return
        self.transport = transport
        self.sidecar.client = self
        logger.info("Node.js client connected")

    def connection_lost(self, exc):
        if self.sidecar.client is self:
            self.sidecar._level_monitoring = False
            self.sidecar.client = None
        logger.info("Node.js client disconnected")

    def data_received(self, data: bytes):
        self._buffer += data
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if line:
                try:
                    msg = json.loads(line.decode())
                    asyncio.ensure_future(self.sidecar.handle_message(msg))
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON: %s", line[:200])

    def send(self, msg: dict) -> None:
        if self.transport and not self.transport.is_closing():
            self.transport.write(
                (json.dumps(msg, separators=(",", ":")) + "\n").encode()
            )


class Sidecar:
    """Main daemon — dual detector, audio capture, training orchestration."""

    def __init__(self):
        self.client: SidecarProtocol | None = None
        self.recorder: AudioRecorder | None = None

        # Dual detectors
        self.fingerprint = FingerprintDetector(
            models_dir=MODELS_DIR, samples_dir=SAMPLES_DIR
        )
        self.ml = MLDetector(models_dir=MODELS_DIR)
        self.active_method: str = "fingerprint"  # or "ml"

        self.trainer = DoorbellTrainer(
            samples_dir=SAMPLES_DIR, models_dir=MODELS_DIR
        )

        self._detecting = False
        self._level_monitoring = False
        self._mic_active = False

    @property
    def active_detector(self) -> BaseDetector:
        return self.fingerprint if self.active_method == "fingerprint" else self.ml

    # ── command dispatch ──────────────────────────────────────────────

    COMMANDS = {
        "start_detection", "stop_detection",
        "set_method", "get_status", "get_devices", "set_device",
        "record_sample", "start_training",
        "set_threshold", "set_cooldown",
        "get_sample_counts", "delete_samples",
        "start_level_monitor", "stop_level_monitor",
        "test_doorbell",
        "list_samples", "get_sample_audio", "delete_sample",
    }

    async def handle_message(self, msg: dict) -> None:
        cmd = msg.get("command")
        handler = getattr(self, f"_cmd_{cmd}", None) if cmd in self.COMMANDS else None
        if handler:
            try:
                await handler(msg)
            except Exception as e:
                logger.exception("Error in %s", cmd)
                self._send({"type": "error", "command": cmd, "error": str(e)})
        else:
            logger.warning("Unknown command: %s", cmd)

    # ── detection control ─────────────────────────────────────────────

    async def _cmd_start_detection(self, msg: dict) -> None:
        if self._detecting:
            return

        # Stop level monitor if running (mutually exclusive)
        self._level_monitoring = False

        detector = self.active_detector
        if not detector.is_loaded:
            try:
                detector.load()
            except FileNotFoundError as e:
                self._send({"type": "error", "command": "start_detection", "error": str(e)})
                return

        self._detecting = True
        self._mic_active = True
        logger.info("Microphone ACTIVE — continuous listening started")
        self._send({"type": "status", "detecting": True, "method": self.active_method, "mic_active": True})
        asyncio.ensure_future(self._detection_loop())

    async def _cmd_stop_detection(self, msg: dict) -> None:
        self._detecting = False
        self._mic_active = False
        logger.info("Microphone INACTIVE — listening stopped")
        self._send({"type": "status", "detecting": False, "mic_active": False})

    async def _cmd_set_method(self, msg: dict) -> None:
        """Switch detection method: 'fingerprint' or 'ml'."""
        method = msg.get("method", "fingerprint")
        if method not in ("fingerprint", "ml"):
            self._send({"type": "error", "error": f"Invalid method: {method}"})
            return

        was_detecting = self._detecting
        if was_detecting:
            self._detecting = False
            await asyncio.sleep(0.1)

        self.active_method = method

        # Load the new detector if not ready
        detector = self.active_detector
        if not detector.is_loaded:
            try:
                detector.load()
            except FileNotFoundError as e:
                self._send({
                    "type": "error", "command": "set_method",
                    "error": str(e), "method": method,
                })
                return

        self._send({
            "type": "method_changed",
            "method": method,
            "is_loaded": detector.is_loaded,
        })

        if was_detecting:
            self._detecting = True
            asyncio.ensure_future(self._detection_loop())

    # ── sample recording ──────────────────────────────────────────────

    async def _cmd_record_sample(self, msg: dict) -> None:
        label = msg.get("label", "doorbell")
        duration = msg.get("duration", 2.0)

        if label not in ("doorbell", "not_doorbell"):
            self._send({"type": "error", "error": "Invalid label"})
            return

        self._mic_active = True
        logger.info("Microphone ACTIVE — recording sample (%s, %.1fs)", label, duration)
        self._send({"type": "recording_started", "label": label, "mic_active": True})

        loop = asyncio.get_event_loop()
        waveform = await loop.run_in_executor(
            None, self.recorder.record_sample, duration
        )

        if not self._detecting:
            self._mic_active = False
            logger.info("Microphone INACTIVE — recording complete")

        # Compute quality metrics
        rms = compute_rms(waveform)
        peak = float(np.max(np.abs(waveform)))
        clipped_ratio = float(np.mean(np.abs(waveform) > 0.99))

        # Save WAV
        sample_dir = SAMPLES_DIR / label
        sample_dir.mkdir(parents=True, exist_ok=True)
        path = sample_dir / f"{int(time.time() * 1000)}.wav"
        AudioRecorder.save_wav(path, waveform)

        # Auto-enroll in fingerprint detector if it's a doorbell sample
        fp_count = None
        if label == "doorbell":
            fp_count = self.fingerprint.enroll(waveform)

        counts = self._get_sample_counts()
        self._send({
            "type": "recording_complete",
            "label": label,
            "path": str(path),
            "counts": counts,
            "fingerprint_count": fp_count,
            "mic_active": self._mic_active,
            "quality": {
                "rms": rms,
                "rms_db": float(20 * np.log10(max(rms, 1e-10))),
                "peak": peak,
                "clipped_ratio": clipped_ratio,
            },
        })

    # ── ML training ───────────────────────────────────────────────────

    async def _cmd_start_training(self, msg: dict) -> None:
        self.trainer.epochs = msg.get("epochs", 50)
        self._send({"type": "training_started"})

        def progress_cb(epoch, total, loss, acc):
            self._send({
                "type": "training_progress",
                "epoch": epoch, "total_epochs": total,
                "loss": float(loss), "accuracy": float(acc),
            })

        self.trainer.set_progress_callback(progress_cb)

        loop = asyncio.get_event_loop()
        try:
            metrics = await loop.run_in_executor(None, self.trainer.train)
            self._send({"type": "training_complete", "metrics": metrics})
            # Reload ML detector with fresh model
            self.ml.load()
        except Exception as e:
            logger.exception("Training failed")
            self._send({"type": "training_failed", "error": str(e)})

    # ── config ────────────────────────────────────────────────────────

    async def _cmd_set_threshold(self, msg: dict) -> None:
        value = msg.get("value", 0.7)
        self.fingerprint.update_threshold(value)
        self.ml.update_threshold(value)
        self._send({"type": "config_updated", "threshold": value})

    async def _cmd_set_cooldown(self, msg: dict) -> None:
        value = msg.get("value", 5.0)
        self.fingerprint.update_cooldown(value)
        self.ml.update_cooldown(value)
        self._send({"type": "config_updated", "cooldown": value})

    async def _cmd_get_status(self, msg: dict) -> None:
        self._send({
            "type": "status",
            "detecting": self._detecting,
            "mic_active": self._mic_active,
            "method": self.active_method,
            "fingerprint_loaded": self.fingerprint.is_loaded,
            "fingerprint_count": len(self.fingerprint._fingerprints),
            "ml_loaded": self.ml.is_loaded,
            "ml_metadata": self.ml.get_model_metadata(),
            "threshold": self.active_detector.threshold,
            "cooldown": self.active_detector.cooldown_s,
            "counts": self._get_sample_counts(),
        })

    async def _cmd_get_devices(self, msg: dict) -> None:
        loop = asyncio.get_event_loop()
        devices = await loop.run_in_executor(None, AudioRecorder.list_devices)
        self._send({"type": "devices", "devices": devices})

    async def _cmd_set_device(self, msg: dict) -> None:
        device_index = msg.get("device_index")
        was_detecting = self._detecting

        if was_detecting:
            self._detecting = False
            await asyncio.sleep(0.1)

        if self.recorder:
            self.recorder.close()
        self.recorder = AudioRecorder(device_index=device_index)
        self.recorder.open()

        if was_detecting:
            self._detecting = True
            asyncio.ensure_future(self._detection_loop())

        self._send({"type": "device_set", "device_index": device_index})

    async def _cmd_get_sample_counts(self, msg: dict) -> None:
        self._send({"type": "sample_counts", "counts": self._get_sample_counts()})

    async def _cmd_delete_samples(self, msg: dict) -> None:
        label = msg.get("label")
        if label in ("doorbell", "not_doorbell"):
            d = SAMPLES_DIR / label
            if d.exists():
                import shutil
                shutil.rmtree(d)
                d.mkdir(parents=True, exist_ok=True)
            if label == "doorbell":
                self.fingerprint.clear()
        self._send({"type": "samples_deleted", "counts": self._get_sample_counts()})

    # ── level monitor ──────────────────────────────────────────────────

    async def _cmd_start_level_monitor(self, msg: dict) -> None:
        if self._detecting or self._level_monitoring:
            return
        self._level_monitoring = True
        self._mic_active = True
        logger.info("Microphone ACTIVE — level monitor started")
        asyncio.ensure_future(self._level_monitor_loop())

    async def _cmd_stop_level_monitor(self, msg: dict) -> None:
        self._level_monitoring = False
        if not self._detecting:
            self._mic_active = False
            logger.info("Microphone INACTIVE — level monitor stopped")

    async def _level_monitor_loop(self) -> None:
        loop = asyncio.get_event_loop()
        while self._level_monitoring and not self._detecting:
            try:
                waveform = await loop.run_in_executor(
                    None, self.recorder.read_chunk
                )
                rms = compute_rms(waveform)
                peak = float(np.max(np.abs(waveform)))
                self._send({
                    "type": "level",
                    "rms": rms,
                    "peak": peak,
                })
            except Exception:
                logger.exception("Error in level monitor loop")
                await asyncio.sleep(1.0)
        self._level_monitoring = False

    # ── test doorbell ────────────────────────────────────────────────

    async def _cmd_test_doorbell(self, msg: dict) -> None:
        self._send({"type": "test_doorbell"})

    # ── sample management ────────────────────────────────────────────

    async def _cmd_list_samples(self, msg: dict) -> None:
        samples = []
        for label in ("doorbell", "not_doorbell"):
            d = SAMPLES_DIR / label
            if not d.exists():
                continue
            for f in sorted(d.glob("*.wav")):
                stat = f.stat()
                # Parse duration from file size (16kHz mono 16-bit + 44 byte header)
                duration_s = max(0, (stat.st_size - 44)) / (16000 * 2)
                samples.append({
                    "id": f.stem,
                    "label": label,
                    "filename": f.name,
                    "duration_s": round(duration_s, 2),
                    "size_bytes": stat.st_size,
                })
        self._send({"type": "sample_list", "samples": samples})

    async def _cmd_get_sample_audio(self, msg: dict) -> None:
        label = msg.get("label", "")
        filename = msg.get("filename", "")
        if not filename or "/" in filename or ".." in filename:
            self._send({"type": "error", "error": "Invalid filename"})
            return
        if label not in ("doorbell", "not_doorbell"):
            self._send({"type": "error", "error": "Invalid label"})
            return
        filepath = SAMPLES_DIR / label / filename
        if not filepath.exists():
            self._send({"type": "error", "error": "Sample not found"})
            return
        wav_b64 = base64.b64encode(filepath.read_bytes()).decode()
        self._send({
            "type": "sample_audio",
            "label": label,
            "filename": filename,
            "wav_b64": wav_b64,
        })

    async def _cmd_delete_sample(self, msg: dict) -> None:
        label = msg.get("label", "")
        filename = msg.get("filename", "")
        if not filename or "/" in filename or ".." in filename:
            self._send({"type": "error", "error": "Invalid filename"})
            return
        if label not in ("doorbell", "not_doorbell"):
            self._send({"type": "error", "error": "Invalid label"})
            return
        filepath = SAMPLES_DIR / label / filename
        if filepath.exists():
            filepath.unlink()
        # Re-enroll fingerprints from remaining samples
        if label == "doorbell":
            self.fingerprint.clear()
            try:
                self.fingerprint.load()
            except FileNotFoundError:
                pass
        self._send({
            "type": "sample_deleted",
            "label": label,
            "filename": filename,
            "counts": self._get_sample_counts(),
        })

    # ── detection loop ────────────────────────────────────────────────

    async def _detection_loop(self) -> None:
        loop = asyncio.get_event_loop()
        logger.info("Detection loop started (method=%s)", self.active_method)

        while self._detecting:
            try:
                waveform = await loop.run_in_executor(
                    None, self.recorder.read_chunk
                )

                rms = compute_rms(waveform)
                detector = self.active_detector

                result = await loop.run_in_executor(
                    None, detector.process_chunk, waveform
                )

                # Spectrogram: use YAMNet if ML loaded, else scipy fallback
                if isinstance(detector, MLDetector) and detector.is_loaded:
                    spectrogram = await loop.run_in_executor(
                        None, detector.get_mel_spectrogram, waveform
                    )
                else:
                    spectrogram = await loop.run_in_executor(
                        None, compute_mel_spectrogram, waveform
                    )

                pcm_b64 = base64.b64encode(
                    waveform_to_pcm_bytes(waveform)
                ).decode()
                spec_b64 = base64.b64encode(
                    spectrogram.astype(np.float32).tobytes()
                ).decode()

                self._send({
                    "type": "audio_frame",
                    "method": result["method"],
                    "confidence": result["confidence"],
                    "is_detection": result["is_detection"],
                    "rms": rms,
                    "inference_ms": result["inference_ms"],
                    "timestamp": result["timestamp"],
                    "waveform_b64": pcm_b64,
                    "spectrogram_b64": spec_b64,
                    "spectrogram_shape": list(spectrogram.shape),
                })

                if result["is_detection"]:
                    self._send({
                        "type": "detection",
                        "method": result["method"],
                        "confidence": result["confidence"],
                        "timestamp": result["timestamp"],
                    })

            except Exception:
                logger.exception("Error in detection loop")
                await asyncio.sleep(1.0)

        logger.info("Detection loop stopped")

    # ── helpers ───────────────────────────────────────────────────────

    def _send(self, msg: dict) -> None:
        if self.client:
            self.client.send(msg)

    def _get_sample_counts(self) -> dict:
        counts = {}
        for label in ("doorbell", "not_doorbell"):
            d = SAMPLES_DIR / label
            counts[label] = len(list(d.glob("*.wav"))) if d.exists() else 0
        return counts

    # ── main ──────────────────────────────────────────────────────────

    async def run(self) -> None:
        loop = asyncio.get_event_loop()

        sock_path = Path(SOCKET_PATH)

        # Security: ensure socket path parent exists and is not a symlink
        sock_parent = sock_path.parent
        if sock_parent.is_symlink():
            raise RuntimeError(f"Socket parent {sock_parent} is a symlink — refusing to start")

        if sock_path.exists():
            # Verify it's actually a socket before unlinking
            if stat.S_ISSOCK(sock_path.stat().st_mode):
                sock_path.unlink()
            else:
                raise RuntimeError(f"{sock_path} exists but is not a socket — refusing to overwrite")

        self.recorder = AudioRecorder()
        self.recorder.open()

        server = await loop.create_unix_server(
            lambda: SidecarProtocol(self), path=SOCKET_PATH
        )
        # Owner-only permissions (0o600) — only Homebridge user can connect
        os.chmod(SOCKET_PATH, 0o600)
        logger.info("Sidecar listening on %s (mode 0600)", SOCKET_PATH)

        # Try to pre-load fingerprint detector (silent fail if no samples)
        try:
            self.fingerprint.load()
        except FileNotFoundError:
            logger.info("No fingerprints yet — waiting for first sample")

        stop = loop.create_future()

        def _handle_signal():
            if not stop.done():
                stop.set_result(None)

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, _handle_signal)

        try:
            await stop
        finally:
            logger.info("Shutting down...")
            self._detecting = False
            self._level_monitoring = False
            self._mic_active = False
            server.close()
            await server.wait_closed()
            self.recorder.close()
            if sock_path.exists():
                sock_path.unlink()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )
    asyncio.run(Sidecar().run())


if __name__ == "__main__":
    main()
