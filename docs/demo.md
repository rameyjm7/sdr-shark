# SDR-Shark Public Demo Mode

SDR-Shark can run a hardware-free public demo using deterministic synthetic IQ replay data. The demo data is generated locally and is public-safe: it contains synthetic tones, noise-floor movement, and burst activity, not captured RF from a real environment.

## Quick Start

From the repository root:

```bash
./scripts/demo.sh
```

Then open:

```text
http://127.0.0.1:5000
```

Stop the demo with:

```bash
./scripts/demo_stop.sh
```

## What The Demo Shows

- A replay-backed SDR source centered at 2.437 GHz.
- A live FFT trace with a moving noise floor and multiple synthetic carriers.
- Waterfall activity with short burst clusters.
- Max-hold and persistence behavior from repeatable signal movement.
- The same `/api/iq/replay/*` flow used for recorded sessions, without requiring SDR hardware.

## Generated Files

The demo writes generated local artifacts under:

```text
.demo/
frontend/build/
```

Those directories are ignored by git. Do not commit generated IQ sessions unless a future reviewed fixture is intentionally created for public distribution.

## Useful API Checks

```bash
curl http://127.0.0.1:5000/api/iq/sessions
curl http://127.0.0.1:5000/api/iq/replay/status
curl http://127.0.0.1:5000/api/data
```

Successful demo replay should show `active: true` from `/api/iq/replay/status` and non-empty `fft` and `waterfall` arrays from `/api/data`.

## Troubleshooting

- If port `5000` is busy, run with another port:

  ```bash
  SDR_SHARK_DEMO_PORT=5010 ./scripts/demo.sh
  ```

  Stop that instance with the same port:

  ```bash
  SDR_SHARK_DEMO_PORT=5010 ./scripts/demo_stop.sh
  ```

- If dependencies are missing, `demo.sh` creates an ignored local `.venv`, installs backend dependencies, and builds the React frontend for same-origin serving.
- If the server fails to start, inspect:

  ```text
  .demo/sdr-shark-demo-127.0.0.1-5000.log
  ```

- To regenerate the synthetic session:

  ```bash
  ./.venv/bin/python scripts/generate_demo_iq_session.py --force
  ```
