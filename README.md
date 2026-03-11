# homebridge-doorbell-detector

Homebridge plugin for doorbell sound detection using ML and FFT fingerprinting. Runs on Raspberry Pi 4.

[![npm](https://img.shields.io/npm/v/homebridge-doorbell-detector)](https://www.npmjs.com/package/homebridge-doorbell-detector)
[![license](https://img.shields.io/npm/l/homebridge-doorbell-detector)](LICENSE)

## How it works

The plugin listens via microphone and detects doorbell sounds using two methods:

| Method | Description | Samples needed | Accuracy |
|--------|-------------|----------------|----------|
| **Fingerprint (FFT)** | Spectral correlation with a stored fingerprint | 1 sample | Good |
| **ML (YAMNet)** | Neural network fine-tuned on your samples | 3+ samples | Higher |

When a doorbell is detected, it sends a **HomeKit doorbell notification** to your iPhone/Apple Watch.

## Requirements

- **Homebridge** >= 1.6.0
- **Node.js** >= 18
- **Python 3** >= 3.9
- **Microphone** connected to RPi (USB or I2S)
- **RPi 4** (recommended) — ML training on weaker hardware will be slower

### System dependencies (RPi / Debian)

```bash
sudo apt-get install -y python3 python3-venv python3-dev portaudio19-dev
```

## Installation

### Via Homebridge Config UI X

1. Open Config UI X
2. Plugins → Search → `homebridge-doorbell-detector`
3. Install

### Via command line

```bash
sudo npm install -g homebridge-doorbell-detector
```

Python virtualenv and dependencies are installed automatically during `npm install`.

## Configuration

The plugin is configured via Config UI X. Minimal configuration:

```json
{
  "platforms": [
    {
      "platform": "DoorbellML",
      "name": "Doorbell Detector"
    }
  ]
}
```

### All options

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `name` | string | `"Doorbell ML"` | Device name in HomeKit |
| `detectionMethod` | string | `"fingerprint"` | `"fingerprint"` or `"ml"` |
| `threshold` | number | `0.7` | Detection threshold (0.1 - 1.0) |
| `cooldown` | number | `5` | Min. seconds between detections |
| `audioDevice` | integer | auto | PyAudio device index |
| `wsPort` | integer | `8581` | WebSocket stream port |
| `pythonPath` | string | auto | Path to Python 3 binary |
| `autoStart` | boolean | `true` | Start detection on launch |

## Usage

### 1. Record a doorbell sample

Open Config UI X → Doorbell Detector dashboard:

1. Click **"Record doorbell"** and ring your doorbell
2. Click **"Record noise"** to capture ambient sounds
3. Repeat for better accuracy

### 2. Choose a detection method

- **Fingerprint (FFT)** — works immediately after the first doorbell sample
- **ML (YAMNet)** — click "Train model" (requires 3+ samples per class)

### 3. Start detection

Click **Start** in the dashboard. The plugin will begin listening and send HomeKit notifications upon detection.

## Dashboard

The Config UI X dashboard displays in real time:

- **Method toggle** — Fingerprint / ML
- **Confidence score** — how closely the sound matches the doorbell
- **Waveform** — live audio waveform
- **Spectrogram** — mel-frequency visualization
- **Detection log** — detection history with timestamps and confidence
- **Training** — progress bar, epoch, accuracy, loss
- **Microphone indicator** — red pulsing MIC icon when the plugin is listening

## Architecture

```
┌──────────────┐  Unix socket   ┌──────────────┐  WebSocket   ┌───────────┐
│   Python     │◄══ ndjson ════►│   Node.js    │◄════════════►│ Config UI │
│   sidecar    │                │   plugin     │              │ dashboard │
│              │                │              │              │           │
│ PyAudio      │  audio_frame   │ sidecar-mgr  │  waveform    │ canvas    │
│ YAMNet/FFT   │  detection ──►│ ws-bridge ──►│  spectrogram │ controls  │
│ TFLite       │                │ accessory ──►│  HomeKit     │ log       │
└──────────────┘                └──────────────┘              └───────────┘
```

- **Python sidecar** — records audio, computes FFT/ML inference, sends results
- **Node.js plugin** — launches sidecar, forwards stream to UI, manages HomeKit accessory
- **Config UI dashboard** — displays data, controls detection, manages samples and training

## Security

- **WebSocket** binds to `127.0.0.1` (localhost only) with token authentication
- **Unix socket** in Homebridge storage dir with `0600` permissions
- **pip install** uses `--no-cache-dir` and `--only-binary`
- **Microphone** — clear indicator in UI when the plugin is listening, audio never leaves the device

## Development

```bash
git clone https://github.com/kasparek-net/homebridge-doorbell-detector.git
cd homebridge-doorbell-detector
npm install
npm run watch  # TypeScript watch mode
```

For testing without Homebridge:

```bash
# Terminal 1: Python sidecar
cd python
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python sidecar.py

# Terminal 2: Node.js plugin
npm run build
```

## License

MIT
