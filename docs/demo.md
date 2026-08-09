# SDR-Shark Public Demo Mode

SDR-Shark can run a hardware-free public demo using deterministic synthetic IQ replay data. The demo data is generated locally and is public-safe: it contains a stationary receiver view of a Bluetooth-like frequency-hopping emitter, synthetic tones, noise-floor movement, and burst activity, not captured RF from a real environment.

Public case study: [RTG Spectrum SDR-Shark](https://rtgspectrum.com/sdr-shark/)

## Quick Start

From the repository root:

```bash
docker compose -f docker-compose.demo.yml up --build
```

The Docker demo builds the React UI, generates deterministic synthetic IQ data inside the container, starts the backend, and activates replay automatically.

Open the LAN URL for this machine, usually:

```text
http://<machine-ip>:8080
```

Stop the Docker demo with:

```bash
docker compose -f docker-compose.demo.yml down
```

## Local Source Demo

From the repository root:

```bash
./scripts/demo.sh
```

The script binds to `0.0.0.0`, tries port `80` first, and falls back to `8080` when low-port bind is unavailable without elevated permissions. Open the LAN URL printed by the script, usually:

```text
http://<machine-ip>:8080
```

Stop the demo with:

```bash
./scripts/demo_stop.sh
```

## What The Demo Shows

- A replay-backed SDR source centered at 2.437 GHz.
- A stationary receiver view of a Bluetooth-like frequency-hopping 2.4 GHz emitter.
- A live FFT trace with a moving noise floor and multiple synthetic carriers.
- Waterfall activity with short burst clusters.
- Max-hold and persistence behavior from repeatable signal movement.
- The same `/api/iq/replay/*` flow used for recorded sessions, without requiring SDR hardware.

## Generated Files

The local source demo writes generated local artifacts under:

```text
.demo/
frontend/build/
```

Those directories are ignored by git. Do not commit generated IQ sessions unless a future reviewed fixture is intentionally created for public distribution.

The Docker demo generates its IQ replay session inside the container filesystem and does not commit or mount real RF captures.

## Useful API Checks

```bash
curl http://127.0.0.1:8080/api/iq/sessions
curl http://127.0.0.1:8080/api/iq/replay/status
curl http://127.0.0.1:8080/api/data
```

Successful demo replay should show `active: true` from `/api/iq/replay/status` and non-empty `fft` and `waterfall` arrays from `/api/data`.

## Public Demo Media

The checked-in demo media was captured from the Docker demo using the synthetic `public-demo-2p4ghz` replay session. The receiver stays centered while the synthetic Bluetooth-like emitter hops across the passband:

- [Replay GIF](media/sdr-shark-demo.gif)
- [Full dashboard screenshot](media/sdr-shark-public-demo-dashboard.png)
- [Spectrum and waterfall screenshot](media/sdr-shark-public-demo-spectrum-waterfall.png)
- [Signal activity panel screenshot](media/sdr-shark-public-demo-signal-activity.png)

## Docker Options

Run in the background:

```bash
docker compose -f docker-compose.demo.yml up -d --build
```

Use a different host port:

```bash
SDR_SHARK_DEMO_PORT=8090 docker compose -f docker-compose.demo.yml up --build
```

Use a shorter generated session for quick smoke testing:

```bash
SDR_SHARK_DEMO_DURATION=5 docker compose -f docker-compose.demo.yml up --build
```

## Troubleshooting

- The demo is externally reachable on the local network by default because it binds to `0.0.0.0`. To limit it to the current machine:

  ```bash
  SDR_SHARK_DEMO_HOST=127.0.0.1 ./scripts/demo.sh
  ```

- Port `80` is attempted first and `8080` is the default fallback. To choose a different fallback:

  ```bash
  SDR_SHARK_DEMO_FALLBACK_PORT=5000 ./scripts/demo.sh
  ```

  To force a specific port:

  ```bash
  SDR_SHARK_DEMO_PORT=5000 ./scripts/demo.sh
  ```

- If dependencies are missing, `demo.sh` creates an ignored local `.venv`, installs backend dependencies, and builds the React frontend for same-origin serving.
- If the server fails to start, inspect:

  ```text
  .demo/sdr-shark-demo-127.0.0.1-8080.log
  ```

- To regenerate the synthetic session:

  ```bash
  ./.venv/bin/python scripts/generate_demo_iq_session.py --force
  ```
