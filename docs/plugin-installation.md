# Plugin Installation

This page documents optional decoder and integration dependencies for SDR-Shark. The top-level README covers the base application install; this page covers the protocol-specific tools that improve decoded activity.

Most SDR-Shark plugins are enabled by default, but they only run when the current tuned receive window overlaps the plugin's supported frequency range.

## Common System Packages

On Debian/Ubuntu systems:

```bash
sudo apt-get update
sudo apt-get install -y \
  build-essential cargo git gpsd gpsd-clients libliquid-dev python3-dev \
  python3-pip python3-venv soapysdr-tools sox tshark wireshark-common
```

Optional radio-specific packages vary by distribution. Typical examples include:

```bash
sudo apt-get install -y soapysdr-module-rtlsdr soapysdr-module-hackrf soapysdr-module-airspy
```

bladeRF, Sidekiq, and some vendor radios may require vendor packages, udev rules, firmware, FPGA images, or proprietary SDK steps outside this repository.

## SoapySDR

SDR-Shark can receive directly through SoapySDR with:

```bash
export SDR_BACKEND=soapy
./scripts/start.sh
```

Verify device discovery:

```bash
SoapySDRUtil --find
python -c "import SoapySDR; print('SoapySDR ok')"
```

Useful environment variables:

```bash
SDR_SOAPY_DRIVERS='hackrf,bladerf,rtlsdr,airspy,sidekiq'
SDR_SOAPY_LOG_FILE='/var/log/sdr-shark/soapysdr.log'
SDR_SOAPY_LOG_STDERR='0'
```

`SDR_SOAPY_DRIVERS` limits probing to specific driver names. This is useful on systems where a vendor driver is noisy or slow to enumerate.

## sdr-gateway Mode

Gateway mode is useful when another service owns the radio and SDR-Shark consumes a shared stream.

```bash
export SDR_BACKEND=gateway
export SDR_SERVER_URL='http://127.0.0.1:8080'
export SDR_GATEWAY_API_TOKEN='replace-if-required'
./scripts/start.sh
```

The helper below starts SDR-Shark in gateway mode and requests the gateway's native IQ stream format:

```bash
./scripts/run_gateway.sh
```

If using the systemd service, place gateway settings in `/etc/default/sdr-shark`.

## RF Sentinel-Backed Plugins

SDR-Shark can reuse RF Sentinel plugin code for several decoders. By default it looks for:

```bash
/home/jake/workspace/SDR/RF_Sentinel
```

Override the path:

```bash
export RF_SENTINEL_ROOT='/path/to/RF_Sentinel'
```

Expected RF Sentinel plugin source paths:

- Bluetooth Low Energy: `rf_platform/plugins/bluetooth-lowenergy/src`
- Bluetooth Classic: `rf_platform/plugins/bluetooth-classic`
- Zigbee / IEEE 802.15.4: `rf_platform/plugins/zigbee-802154/src`
- WiFi / 802.11 activity parser: `rf_platform/plugins/wifi-80211/src`
- FM quality demod: `rf_platform/plugins/fm-broadcast/src`

If these paths are missing, SDR-Shark will continue running but the related plugin may show an unavailable/error state.

## Bluetooth Low Energy and Bluetooth Classic

The Bluetooth plugin runs when the receive window overlaps approximately `2402-2480 MHz`.

Environment:

```bash
SDR_SHARK_BLUETOOTH_PLUGIN='1'
RF_SENTINEL_ROOT='/home/jake/workspace/SDR/RF_Sentinel'
SDR_SHARK_BLUETOOTH_LOG_DIR='/var/log/sdr-shark'
```

Disable:

```bash
SDR_SHARK_BLUETOOTH_PLUGIN='0'
```

Build the Bluetooth Classic sniffer if RF Sentinel requires it:

```bash
cd /home/jake/workspace/SDR/RF_Sentinel/rf_platform/plugins/bluetooth-classic
mkdir -p build
cd build
cmake ..
make -j"$(nproc)"
```

The expected binary is:

```bash
/home/jake/workspace/SDR/RF_Sentinel/rf_platform/plugins/bluetooth-classic/build/btcexplorer-sniffer-gateway
```

Runtime logs:

- Current Bluetooth events: `/var/log/sdr-shark/bluetooth-events-current.jsonl`
- Bluetooth Classic startup log: `/var/log/sdr-shark/btcexplorer-sniffer.log`
- Archived older event files: `/var/log/sdr-shark/archive/<date-time>/`

## Zigbee / IEEE 802.15.4

The Zigbee plugin runs when the receive window overlaps approximately `2405-2480 MHz`. It channelizes visible Zigbee channels from the shared IQ stream and attempts IEEE 802.15.4 frame decode.

Environment:

```bash
SDR_SHARK_ZIGBEE_PLUGIN='1'
RF_SENTINEL_ROOT='/home/jake/workspace/SDR/RF_Sentinel'
SDR_SHARK_ZIGBEE_CHANNEL_RATE_SPS='4000000'
SDR_SHARK_ZIGBEE_CORR_MIN='0.18'
SDR_SHARK_ZIGBEE_AGGREGATE_MS='3.5'
```

Disable:

```bash
SDR_SHARK_ZIGBEE_PLUGIN='0'
```

Operational notes:

- Zigbee channels 11-26 are centered from `2405 MHz` through `2480 MHz`.
- The plugin selects only channels visible inside the current receive span.
- Wideband 2.4 GHz scanning with a 60 MHz receiver can cover multiple Zigbee channels without retuning for each individual channel.

## WiFi / 802.11

The WiFi plugin has two layers:

- Activity detection from the shared IQ stream.
- Optional MAC frame decoding through an external GNU Radio/gr-ieee802-11 stack that emits JSONL and/or PCAP data.

Environment:

```bash
SDR_SHARK_WIFI_PLUGIN='1'
RF_SENTINEL_ROOT='/home/jake/workspace/SDR/RF_Sentinel'
WIFI_80211_STACK_ROOT='/home/jake/workspace/SDR/wifi_80211_sdr_stack'
SDR_SHARK_WIFI_MAC_DECODER='1'
SDR_SHARK_WIFI_FRAME_JSONL='/home/jake/workspace/SDR/wifi_80211_sdr_stack/pcaps/wifi_bladerf_frames.jsonl'
```

Disable:

```bash
SDR_SHARK_WIFI_PLUGIN='0'
```

Disable only the external MAC decoder:

```bash
SDR_SHARK_WIFI_MAC_DECODER='0'
```

Recommended external stack layout:

```bash
/home/jake/workspace/SDR/wifi_80211_sdr_stack
  scripts/wifi_rx_bladerf_gr.py
  local/lib/python3.12/dist-packages
  local/lib/x86_64-linux-gnu
  pcaps/
```

The decoder script must support stdin IQ input:

```bash
./scripts/wifi_rx_bladerf_gr.py \
  --freq 2.412e9 \
  --rate 20e6 \
  --bandwidth 20e6 \
  --input-stdin \
  --pcap pcaps/ch1.pcap \
  --jsonl pcaps/wifi_frames.jsonl
```

SDR-Shark starts this decoder per active visible WiFi channel when `SDR_SHARK_WIFI_MAC_DECODER=1`.

Useful controls:

```bash
SDR_SHARK_WIFI_ACTIVITY_THRESHOLD='0.55'
SDR_SHARK_WIFI_DECODE_INTERVAL_MS='250'
SDR_SHARK_WIFI_FRAME_POLL_MS='1000'
SDR_SHARK_WIFI_MAC_DECODER_MAX_CHANNELS='4'
SDR_SHARK_WIFI_MAC_DECODER_ACTIVITY_TTL='20'
SDR_SHARK_WIFI_DECODER_SCRIPT='/home/jake/workspace/SDR/wifi_80211_sdr_stack/scripts/wifi_rx_bladerf_gr.py'
```

Tools:

```bash
tshark -r /var/log/sdr-shark/wifi-ch1-<pid>.pcap
```

Install `pyshark` if local scripts require it:

```bash
source .venv/bin/activate
python -m pip install pyshark
```

## FM Broadcast

The FM plugin detects carriers in the `87.5-108 MHz` range, verifies stations using demod quality, and can play verified stations from the live wideband IQ stream.

Liquid-DSP improves channelizer performance:

```bash
sudo apt-get install -y libliquid-dev
cd /home/jake/workspace/SDR/SDR-Shark
bash backend/src/sdr_plot_backend/native/build_fm_channelizer.sh
```

RF Sentinel's FM demod quality code is used when available:

```bash
export RF_SENTINEL_ROOT='/home/jake/workspace/SDR/RF_Sentinel'
```

If RF Sentinel is unavailable, SDR-Shark falls back to an internal quality demod implementation.

Scanner note: FM discovery is capped to 5% of a scanner cycle.

## ADS-B

The ADS-B plugin runs when the receive window overlaps `1090 MHz`. It feeds shared `cs8` IQ into the vendored Rust decoder.

Install Rust/Cargo:

```bash
sudo apt-get install -y cargo
```

Build manually if desired:

```bash
cd /home/jake/workspace/SDR/SDR-Shark/backend/src/sdr_plot_backend/plugins/adsb_rx/adsb-rx
cargo build --release
```

Environment:

```bash
SDR_SHARK_ADSB_PLUGIN='1'
SDR_SHARK_ADSB_MIN_MESSAGES='1'
SDR_SHARK_ADSB_RX_BIN='/path/to/adsb-rx'
```

Disable:

```bash
SDR_SHARK_ADSB_PLUGIN='0'
```

The default vendored binary path is:

```bash
backend/src/sdr_plot_backend/plugins/adsb_rx/adsb-rx/target/release/adsb-rx
```

## GPSD

Install:

```bash
sudo apt-get install -y gpsd gpsd-clients
```

Configure `/etc/default/gpsd`:

```bash
START_DAEMON="true"
USBAUTO="true"
DEVICES=""
GPSD_OPTIONS="-n"
```

Enable and verify:

```bash
sudo systemctl enable --now gpsd
gpspipe -w
ss -ltnp | grep gpsd
```

Configure SDR-Shark:

```bash
GPSD_HOST='127.0.0.1'
GPSD_PORT='2948'
SDR_SHARK_GPS_PLUGIN='1'
```

Some systems expose GPSD on `2947`; use the port your service reports.

Disable:

```bash
SDR_SHARK_GPS_PLUGIN='0'
```

## Service Environment Example

`/etc/default/sdr-shark`:

```bash
SDR_BACKEND='soapy'
SDR_SHARK_LOG_DIR='/var/log/sdr-shark'
SDR_SHARK_BLUETOOTH_LOG_DIR='/var/log/sdr-shark'
RF_SENTINEL_ROOT='/home/jake/workspace/SDR/RF_Sentinel'
WIFI_80211_STACK_ROOT='/home/jake/workspace/SDR/wifi_80211_sdr_stack'
GPSD_HOST='127.0.0.1'
GPSD_PORT='2948'
SDR_SHARK_WIFI_MAC_DECODER='1'
```

Restart after changes:

```bash
sudo systemctl restart sdr-shark
```

## Troubleshooting

Check service logs:

```bash
journalctl -u sdr-shark -f
```

Check SDR devices:

```bash
SoapySDRUtil --find
```

Check GPS:

```bash
gpspipe -w
```

Check WiFi PCAP output:

```bash
tshark -r /path/to/file.pcap
```

Check Bluetooth logs:

```bash
tail -f /var/log/sdr-shark/btcexplorer-sniffer.log
tail -f /var/log/sdr-shark/bluetooth-events-current.jsonl
```

If a plugin is not producing cards, confirm:

- The tuned receive window overlaps the protocol band.
- The receiver sample rate is wide enough for the plugin.
- `RF_SENTINEL_ROOT` and optional external stack paths are correct.
- The related plugin is not disabled by environment variable.
- The service user can read/write the configured log and output paths.
