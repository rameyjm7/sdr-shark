# SDR-Shark
A React application with a Python backend for SIGINT applications with applied ML using PyTorch and TensorFlow

Signal Intelligence platform at your fingertips...
<img width="1866" height="904" alt="image" src="https://github.com/user-attachments/assets/d6798fa3-953b-47dd-bf76-a702983a7d4c" />


# How to install

apt install python3-venv

python3-venv /home/eng/python

source /home/eng/python/bin/activate


#cd to the <repository root>/backend/ folder

pip install .


#for the frontend, in a new terminal, go the frontend folder and run these command to install the dependencies and run the frontend

yarn install

# How to run

**Option 1:** (Recommended for speed): Gunicorn using scripts/start.sh

From the repository root after installing the dependencies (backend and frontend), run ./scripts/start.sh to run the gunicorn server.

Systemd service helper:

Use ./scripts/sdr-shark-service.sh to install/manage a service:

- install
- enable
- disable
- start
- stop
- restart
- status
- logs

If your `sdr-gateway` lives somewhere other than `http://127.0.0.1:8080`, set `SDR_SERVER_URL` before installing the service and the helper will write it into the service environment file alongside `SDR_GATEWAY_API_TOKEN`.

SDR backend options:

- Default: `SDR_BACKEND=soapy` uses local SoapySDR Python bindings directly and does not require `sdr-gateway`.
- Gateway mode: `SDR_BACKEND=gateway` uses `sdr-gateway` at `SDR_SERVER_URL`.
- Gateway helper: `./scripts/run_gateway.sh` starts SDR-Shark with `SDR_BACKEND=gateway` and requests the gateway's native IQ stream format.

RF Sentinel Bluetooth decoding:

- Enabled by default with `SDR_SHARK_BLUETOOTH_PLUGIN=1`; disable with `SDR_SHARK_BLUETOOTH_PLUGIN=0`.
- In direct SoapySDR mode, SDR-Shark owns the radio and mirrors live IQ as `cs8` chunks to RF Sentinel. Bluetooth Classic is fed through a temporary FIFO in `/tmp`, while BLE uses the same mirrored chunks in-process.
- The decoder starts only when the active receive window overlaps the Bluetooth range, 2402-2480 MHz.
- Set `RF_SENTINEL_ROOT=/path/to/RF_Sentinel` if RF Sentinel is not at `/home/jake/workspace/SDR/RF_Sentinel`.
- Bluetooth/BTC events are written to `/var/log/sdr-shark/bluetooth-events-current.jsonl`, and the BTC sniffer startup log is `/var/log/sdr-shark/btcexplorer-sniffer.log`. Override with `SDR_SHARK_BLUETOOTH_LOG_DIR=/path/to/logs`.

Scanner mode:

- Open `Scanner` from the top-right toolbar to configure a repeating dwell scan plan without hiding the live spectrum/waterfall.
- The 2.4 GHz ISM protocols share one dwell percentage because Zigbee, Thread, WiFi 2.4 GHz, Bluetooth Classic, and BLE overlap in spectrum. SDR-Shark scans this as two 60 MHz wideband passes over the low and high portions of the band.
- Other selected bands keep individual percentages. The Scan Plan table shows the exact order, center frequency, bandwidth, protocols, dwell time, and how often each band is revisited.
- While scanner mode retunes between bands, the spectrum Y range is automatically re-leveled once for the new band.

GPSD integration:

- SDR-Shark can read live GPS status from `gpsd` and expose it in the top-right `GPS` dialog.
- Install gpsd and client tools on Debian/Ubuntu with `sudo apt install gpsd gpsd-clients`, then enable it with `sudo systemctl enable --now gpsd`.
- Configure gpsd devices in `/etc/default/gpsd`; for many USB GPS receivers use `USBAUTO="true"` and `GPSD_OPTIONS="-n"`.
- Set SDR-Shark service defaults in `/etc/default/sdr-shark`, for example:

```bash
export GPSD_HOST=127.0.0.1
export GPSD_PORT=2948
```

- Stock gpsd commonly listens on TCP port `2947`; use `GPSD_PORT=2947` instead if that is what your service exposes.
- Verify the listener with `ss -ltnp | grep gpsd` or test the feed with `gpspipe -w`.
- Disable the GPS plugin with `SDR_SHARK_GPS_PLUGIN=0`.

For direct SoapySDR mode, install SoapySDR and the driver packages for your radio, then verify:

```bash
SoapySDRUtil --find
python -c "import SoapySDR; print('SoapySDR ok')"
SDR_BACKEND=soapy ./scripts/start.sh
```

Optional: limit direct discovery with `SDR_SOAPY_DRIVERS=hackrf,rtlsdr,airspy,bladerf,sidekiq`.

SoapySDR/vendor probe and overflow warnings are written to `/var/log/sdr-shark/soapysdr.log` by default instead of the service console. Override with `SDR_SOAPY_LOG_FILE=/path/to/soapysdr.log`, or set `SDR_SOAPY_LOG_STDERR=1` while debugging to show them on stderr again.

Backend startup auto-launches the React dev frontend when `frontend/node_modules` exists. If port `3000` is already in use, it skips auto-start instead of prompting for another port. Set `SDR_SHARK_AUTO_START_FRONTEND=0` to disable this behavior, or `SDR_SHARK_FRONTEND_PORT=3001` to use a different frontend port.


**Option 2:** If you want to make changes and develop

#run the frontend from the frontend folder
yarn start

#run the backend using python3
python3 -m sdr_plot_backend

Then, Browse to the IP Address of your PC, port 3000. i.e. 10.139.1.86:3000
