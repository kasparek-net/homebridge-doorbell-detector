"""
Audio recorder — PyAudio capture for doorbell detection & sample collection.

Provides ~1s audio chunks (16kHz mono float32) for both detection methods,
plus sample recording/saving for fingerprint enrollment and ML training.
"""

import io
import wave
from pathlib import Path
from typing import Generator

import numpy as np
import pyaudio

RATE = 16000
CHANNELS = 1
FORMAT = pyaudio.paInt16
CHUNK_DURATION_S = 0.975  # YAMNet frame size, works for both methods
CHUNK_SAMPLES = int(RATE * CHUNK_DURATION_S)


class AudioRecorder:
    """Captures audio from a selected input device."""

    def __init__(self, device_index: int | None = None):
        self._device_index = device_index
        self._pa: pyaudio.PyAudio | None = None
        self._stream: pyaudio.Stream | None = None

    def open(self) -> None:
        self._pa = pyaudio.PyAudio()
        self._stream = self._pa.open(
            format=FORMAT,
            channels=CHANNELS,
            rate=RATE,
            input=True,
            input_device_index=self._device_index,
            frames_per_buffer=CHUNK_SAMPLES,
        )

    def close(self) -> None:
        if self._stream is not None:
            self._stream.stop_stream()
            self._stream.close()
            self._stream = None
        if self._pa is not None:
            self._pa.terminate()
            self._pa = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def read_chunk(self) -> np.ndarray:
        """Read one ~1s chunk as float32 waveform in [-1, 1]."""
        raw = self._stream.read(CHUNK_SAMPLES, exception_on_overflow=False)
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    def stream_chunks(self) -> Generator[np.ndarray, None, None]:
        """Yield consecutive ~1s waveform chunks forever."""
        while self._stream is not None and self._stream.is_active():
            yield self.read_chunk()

    def record_sample(self, duration_s: float = 2.0) -> np.ndarray:
        """Record a fixed-length sample for training/enrollment."""
        n = int(RATE * duration_s)
        raw = self._stream.read(n, exception_on_overflow=False)
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    @staticmethod
    def save_wav(path: Path, waveform: np.ndarray) -> None:
        pcm = (waveform * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(CHANNELS)
            wf.setsampwidth(2)
            wf.setframerate(RATE)
            wf.writeframes(pcm.tobytes())
        path.write_bytes(buf.getvalue())

    @staticmethod
    def load_wav(path: Path) -> np.ndarray:
        with wave.open(str(path), "rb") as wf:
            raw = wf.readframes(wf.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    @staticmethod
    def list_devices() -> list[dict]:
        pa = pyaudio.PyAudio()
        devices = []
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i)
            if info["maxInputChannels"] > 0:
                devices.append({"index": i, "name": info["name"]})
        pa.terminate()
        return devices


def compute_rms(waveform: np.ndarray) -> float:
    return float(np.sqrt(np.mean(waveform ** 2)))


def waveform_to_pcm_bytes(waveform: np.ndarray) -> bytes:
    return (waveform * 32767).astype(np.int16).tobytes()
