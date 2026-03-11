# homebridge-doorbell-detector

Homebridge plugin pro detekci zvuku zvonku pomoci ML a FFT otisku. Bezi na Raspberry Pi 4.

[![npm](https://img.shields.io/npm/v/homebridge-doorbell-detector)](https://www.npmjs.com/package/homebridge-doorbell-detector)
[![license](https://img.shields.io/npm/l/homebridge-doorbell-detector)](LICENSE)

## Jak to funguje

Plugin posloucha mikrofon a detekuje zvuk zvonku dvema metodami:

| Metoda | Popis | Potreba vzorku | Presnost |
|--------|-------|----------------|----------|
| **Otisk (FFT)** | Spektralni korelace s ulozenym otiskem | 1 vzorek | Dobra |
| **ML (YAMNet)** | Neuronova sit fine-tuned na vase vzorky | 3+ vzorku | Vyssi |

Pri detekci posle **HomeKit doorbell notifikaci** do vaseho iPhone/Apple Watch.

## Pozadavky

- **Homebridge** >= 1.6.0
- **Node.js** >= 18
- **Python 3** >= 3.9
- **Mikrofon** pripojeny k RPi (USB nebo I2S)
- **RPi 4** (doporuceno) — trenovani ML na slabsim HW bude pomalejsi

### Systemove zavislosti (RPi / Debian)

```bash
sudo apt-get install -y python3 python3-venv python3-dev portaudio19-dev
```

## Instalace

### Pres Homebridge Config UI X

1. Otevrete Config UI X
2. Plugins → Search → `homebridge-doorbell-detector`
3. Install

### Pres prikazovy radek

```bash
sudo npm install -g homebridge-doorbell-detector
```

Python virtualenv a zavislosti se nainstalují automaticky pri `npm install`.

## Konfigurace

Plugin se konfiguruje v Config UI X. Minimalni konfigurace:

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

### Vsechny volby

| Parametr | Typ | Vychozi | Popis |
|----------|-----|---------|-------|
| `name` | string | `"Doorbell ML"` | Nazev zarizeni v HomeKit |
| `detectionMethod` | string | `"fingerprint"` | `"fingerprint"` nebo `"ml"` |
| `threshold` | number | `0.7` | Prah detekce (0.1 - 1.0) |
| `cooldown` | number | `5` | Min. sekundy mezi detekcemi |
| `audioDevice` | integer | auto | Index PyAudio zarizeni |
| `wsPort` | integer | `8581` | Port pro WebSocket stream |
| `pythonPath` | string | auto | Cesta k Python 3 binarce |
| `autoStart` | boolean | `true` | Spustit detekci pri startu |

## Pouziti

### 1. Nahrajte vzorek zvonku

Otevrete Config UI X → Doorbell Detector dashboard:

1. Kliknete **"Nahrat zvonek"** a zazvoňte
2. Kliknete **"Nahrat sumi"** pro zaznam okolnich zvuku
3. Opakujte pro lepsi presnost

### 2. Zvolte metodu detekce

- **Otisk (FFT)** — funguje hned po prvnim vzorku zvonku
- **ML (YAMNet)** — kliknete "Trenovat model" (potreba 3+ vzorku kazde tridy)

### 3. Spuste detekci

Kliknete **Start** v dashboardu. Plugin zacne poslouchat a pri detekci posle HomeKit notifikaci.

## Dashboard

Config UI X dashboard zobrazuje v realnem case:

- **Prepinac metody** — Otisk / ML
- **Confidence score** — jak moc si detektor odpovida zvuku zvonku
- **Waveform** — live prubeh zvuku
- **Spektrogram** — mel-frekvencni vizualizace
- **Detection log** — historie detekci s casem a confidence
- **Trenovani** — progress bar, epoch, accuracy, loss
- **Mikrofon indikator** — cerveny pulzujici MIC kdyz plugin posloucha

## Architektura

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

- **Python sidecar** — nahrava audio, pocita FFT/ML inferenci, posila vysledky
- **Node.js plugin** — spousti sidecar, preposila stream do UI, spravuje HomeKit accessory
- **Config UI dashboard** — zobrazuje data, ovlada detekci, spravuje vzorky a trenovani

## Bezpecnost

- **WebSocket** binds na `127.0.0.1` (jen localhost) s tokenovou autentizaci
- **Unix socket** v Homebridge storage dir s `0600` permissions
- **pip install** pouziva `--no-cache-dir` a `--only-binary`
- **Mikrofon** — jasny indikator v UI kdyz plugin posloucha, audio neopousti zarizeni

## Vyvoj

```bash
git clone https://github.com/TODO/homebridge-doorbell-detector.git
cd homebridge-doorbell-detector
npm install
npm run watch  # TypeScript watch mode
```

Pro testovani bez Homebridge:

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
