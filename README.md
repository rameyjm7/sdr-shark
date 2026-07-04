# SDR-Shark

![Python](https://img.shields.io/badge/Python-Backend-blue)
![React](https://img.shields.io/badge/React-Frontend-61dafb)
![SDR](https://img.shields.io/badge/SDR-SoapySDR-green)
![ML](https://img.shields.io/badge/Applied%20ML-RF%20Signal%20Analysis-orange)

SDR-Shark is an applied RF signal-intelligence platform and web-based software defined radio console for live spectrum monitoring, waterfall visualization, protocol-aware signal activity, and decoder-assisted RF discovery. It combines a Python/Flask backend with a React frontend and can receive samples either directly through SoapySDR or through `sdr-gateway`.

The project demonstrates the system layer around RF ML: live device streaming, browser visualization, signal feature extraction, decoder plugin orchestration, service deployment, and integration points for models from [ML-wireless-signal-classification](https://github.com/rameyjm7/ML-wireless-signal-classification).

The project is intended for lawful RF engineering, lab validation, education, spectrum monitoring, and passive signal-awareness workflows. Operators are responsible for complying with all applicable radio, privacy, and computer misuse laws in their jurisdiction.

## What It Does

- Displays a live spectrum trace and waterfall with GPU-backed rendering support.
- Supports direct SoapySDR receive and gateway-backed receive through `sdr-gateway`.
- Shares one wideband IQ stream with multiple decoder plugins.
- Shows decoded signal activity cards for Bluetooth Low Energy, Bluetooth Classic, WiFi/802.11, Zigbee/802.15.4, FM broadcast, ADS-B, and GPS status.
- Provides scanner mode for repeated dwell plans across 2.4 GHz ISM, FM, sub-GHz, ADS-B, LTE awareness bands, and WiFi 5.8 GHz.
- Tracks Pattern-of-Life information in the activity panel, including multi-day seen/streak pills.
- Provides optional FM station verification and playback from the live wideband IQ stream.
- Supports top-right modal workflows for settings, scanner, analysis, classifiers, GPS, and related controls.
- Provides integration points for RF/IQ classifiers and streaming inference.

## Architecture

SDR-Shark has three main layers:

- `frontend/`: React UI for the live plot, waterfall, scanner, settings dialogs, GPS dialog, and decoded signal activity panel.
- `backend/src/sdr_plot_backend/`: Flask API, SDR adapter, scanner controller, protocol plugin adapters, and shared IQ tap plumbing.
- `scripts/`: local start, service management, gateway mode, and one-script installation helpers.

Receive modes:

- `SDR_BACKEND=gateway`: SDR-Shark connects to `sdr-gateway` using `SDR_SERVER_URL` and an optional `SDR_GATEWAY_API_TOKEN`. This is the packaged default because it supports shared-radio deployments.
- `SDR_BACKEND=soapy`: SDR-Shark opens the radio locally using SoapySDR.
- `replay`: internal/plugin workflows can consume recorded or replayed IQ where supported.

## Hardware

The direct SoapySDR path can work with any radio supported by the installed SoapySDR modules. Common devices include HackRF, bladeRF, RTL-SDR, Airspy, Sidekiq, and other Soapy-compatible receivers.

High-rate features such as 60 MHz 2.4 GHz scanning require hardware and host I/O that can sustain the requested bandwidth. For narrowband features such as ADS-B, FM, and sub-GHz monitoring, lower bandwidth devices may be sufficient.

## Quick Install

On Debian/Ubuntu-like systems, the recommended one-script install is:

```bash
cd /home/jake/workspace/SDR/SDR-Shark
chmod +x scripts/install.sh
./scripts/install.sh
```

This installs common system dependencies when `apt-get` is available, creates `.venv`, installs the backend, installs frontend packages, and builds the frontend.

To install and start SDR-Shark as a systemd service in one command:

```bash
cd /home/jake/workspace/SDR/SDR-Shark
./scripts/install.sh --enable-service
```

To skip system package installation, for example on a prepared machine:

```bash
./scripts/install.sh --no-system-packages
```

## Manual Install

Install system dependencies:

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential cargo curl git gpsd gpsd-clients gunicorn libliquid-dev \
  npm python3-dev python3-pip python3-venv soapysdr-tools sox tshark wireshark-common
```

Create and populate the Python environment:

```bash
cd /home/jake/workspace/SDR/SDR-Shark
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r requirements.txt
python -m pip install -e backend
```

Install and build the frontend:

```bash
cd /home/jake/workspace/SDR/SDR-Shark/frontend
npm install
npm run build
```

Optional FM channelizer build:

```bash
cd /home/jake/workspace/SDR/SDR-Shark
bash backend/src/sdr_plot_backend/native/build_fm_channelizer.sh
```

## Running Locally

Start the backend from the repository root:

```bash
cd /home/jake/workspace/SDR/SDR-Shark
source .venv/bin/activate
./scripts/start.sh
```

For frontend development:

```bash
cd /home/jake/workspace/SDR/SDR-Shark/frontend
npm start
```

The development frontend normally runs on `http://localhost:3000` and proxies API calls to the backend. The backend normally listens on `0.0.0.0:5000`.

## Running as a systemd Service

Install or refresh the service:

```bash
cd /home/jake/workspace/SDR/SDR-Shark
./scripts/sdr-shark-service.sh install
```

Enable and start it:

```bash
./scripts/sdr-shark-service.sh enable
./scripts/sdr-shark-service.sh start
```

Common service commands:

```bash
./scripts/sdr-shark-service.sh status
./scripts/sdr-shark-service.sh logs
./scripts/sdr-shark-service.sh restart
./scripts/sdr-shark-service.sh stop
```

The helper writes the unit to `/etc/systemd/system/sdr-shark.service` and service defaults to `/etc/default/sdr-shark`.

Example `/etc/default/sdr-shark`:

```bash
SDR_BACKEND='gateway'
SDR_SHARK_LOG_DIR='/var/log/sdr-shark'
SDR_SHARK_BLUETOOTH_LOG_DIR='/var/log/sdr-shark'
GPSD_HOST='127.0.0.1'
GPSD_PORT='2948'
```

For direct SoapySDR mode:

```bash
SDR_BACKEND='soapy'
```

For gateway mode with a non-default gateway URL or authenticated gateway:

```bash
SDR_BACKEND='gateway'
SDR_SERVER_URL='http://127.0.0.1:8080'
SDR_GATEWAY_API_TOKEN='replace-with-your-token-if-required'
```

After editing `/etc/default/sdr-shark`, restart:

```bash
sudo systemctl restart sdr-shark
```

## SDR Backend Configuration

Direct SoapySDR mode:

```bash
SDR_BACKEND=soapy ./scripts/start.sh
```

Verify SoapySDR:

```bash
SoapySDRUtil --find
python -c "import SoapySDR; print('SoapySDR ok')"
```

Limit device probing:

```bash
export SDR_SOAPY_DRIVERS=hackrf,bladerf,rtlsdr,airspy,sidekiq
```

Gateway mode:

```bash
SDR_BACKEND=gateway SDR_SERVER_URL=http://127.0.0.1:8080 ./scripts/start.sh
```

Or use the helper:

```bash
./scripts/run_gateway.sh
```

SoapySDR warnings and vendor output are written to `/var/log/sdr-shark/soapysdr.log` by default. To show them on stderr while debugging:

```bash
export SDR_SOAPY_LOG_STDERR=1
```

## Plugin Installation

Decoder plugins have additional dependencies. See [Plugin Installation](docs/plugin-installation.md) for detailed setup of:

- RF Sentinel-backed Bluetooth, Zigbee, WiFi, and FM support.
- WiFi MAC frame decoding through GNU Radio, gr-ieee802-11, tshark/pyshark-compatible JSONL/PCAP flows.
- ADS-B Rust decoder support.
- GPSD service setup.
- Liquid-DSP FM channelizer support.

Most plugins are enabled by default and become active only when the tuned receive window overlaps their supported frequency range.

## GPSD Setup

Install GPSD:

```bash
sudo apt-get install -y gpsd gpsd-clients
```

Configure `/etc/default/gpsd`. A common USB GPS setup is:

```bash
START_DAEMON="true"
USBAUTO="true"
DEVICES=""
GPSD_OPTIONS="-n"
```

Enable and start GPSD:

```bash
sudo systemctl enable --now gpsd
```

Verify GPSD:

```bash
gpspipe -w
ss -ltnp | grep gpsd
```

Configure SDR-Shark in `/etc/default/sdr-shark`:

```bash
GPSD_HOST='127.0.0.1'
GPSD_PORT='2948'
```

Some GPSD installs listen on `2947`; set `GPSD_PORT='2947'` if that is what your system exposes.

Disable the GPS plugin:

```bash
SDR_SHARK_GPS_PLUGIN='0'
```

## Scanner Mode

Open `Scanner` from the top-right toolbar. Scanner mode builds a repeated dwell plan and retunes SDR-Shark while decoders run.

Key behavior:

- 2.4 GHz ISM protocols share one dwell percentage because Zigbee, Thread, WiFi 2.4 GHz, Bluetooth Classic, and BLE overlap.
- 2.4 GHz ISM scanning uses two wideband passes: low and high portions of the band at up to 60 MHz bandwidth.
- FM discovery is capped at 5% of the scan cycle so broadcast discovery does not dominate multi-protocol scanning.
- Other selected bands keep individual percentages.
- The scan plan table shows order, center frequency, bandwidth, protocols, dwell time, and revisit period.
- When scanner mode retunes, SDR-Shark auto-levels the Y axis for the new band.

## Signal Activity and Pattern of Life

The Signal Activity panel groups decoded protocol activity into cards. Device-oriented detections can be folded by protocol and manufacturer/type. SDR-Shark also records a browser-local Pattern-of-Life cache that tracks compatible fields such as `seen_days`, `seen_day_count`, `first_seen_date`, and `last_seen_date`.

Pattern-of-Life pills show whether something was seen today, how many recent days it appeared, and whether it has a multi-day streak. This is intended as a field-use convenience and can later be backed by a shared database or RF Sentinel-compatible history store.

## Logs and Runtime Data

Default log locations:

- Service logs: `journalctl -u sdr-shark -f`
- SDR-Shark logs: `/var/log/sdr-shark`
- SoapySDR vendor output: `/var/log/sdr-shark/soapysdr.log`
- Bluetooth events: `/var/log/sdr-shark/bluetooth-events-current.jsonl`
- Bluetooth archive: `/var/log/sdr-shark/archive/<date-time>/`
- WiFi decoder PCAP/JSONL paths: configurable; see [Plugin Installation](docs/plugin-installation.md)

## Development Workflow

Backend development:

```bash
cd /home/jake/workspace/SDR/SDR-Shark
source .venv/bin/activate
python3 -m sdr_plot_backend
```

Frontend development:

```bash
cd /home/jake/workspace/SDR/SDR-Shark/frontend
npm start
```

Production frontend build check:

```bash
cd /home/jake/workspace/SDR/SDR-Shark/frontend
npm run build
```

Recommended before submitting changes:

```bash
git status --short
npm run build
```

## Collaborating

Contributions should be scoped, testable, and respectful of existing operator workflows. Good changes usually include:

- A short description of the operational problem being solved.
- A clear default behavior that does not break existing receivers or services.
- Environment variables for hardware- or site-specific settings.
- Documentation updates for new service, plugin, or decoder requirements.
- A build or smoke-test result in the pull request notes.

When working on SDR or decoder changes, avoid assuming exclusive access to a radio. SDR-Shark often runs alongside `sdr-gateway` and other consumers, so shared IQ tap behavior and clean shutdown are important.

## Future Improvements

Potential areas for future work:

- Persistent backend Pattern-of-Life storage shared with RF Sentinel.
- More complete WiFi MAC management frame enrichment and SSID/BSSID history.
- Additional protocol plugins for cellular awareness, LoRa, TPMS, VLF/LF/MF, and other ISM bands.
- Better replay/session recording tools for detector validation.
- More GPU rendering options and waterfall palette controls.
- Role-based access control for multi-user deployments.
- Packaged releases for Debian/Ubuntu and containerized lab deployments.
- Automated hardware capability detection for bandwidth, gain ranges, and safe scanner plans.

## Commercial Licensing

For commercial licensing, integration, or support inquiries, contact:

- rameyjm7@gmail.com
- jake.rtgllc@gmail.com
