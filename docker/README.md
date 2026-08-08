# SDR-Shark Docker image

Deployed to station1 (`10.139.1.160:5000`) for testing. Build from the
repo root:

```
docker build -t sdr-shark:latest .
```

(The root `Dockerfile`, not anything under `docker/` — the old
`docker/Dockerfile` depended on a gitignored `code.zip`, Debian buster
with expired GPG keys, and supervisor running a dev frontend server, and
has been removed. `docker/soapy-build/` here is a build asset, not an
alternate Dockerfile.)

## Deploy (default: `rfiq`, the actual production config)

`SDR_BACKEND` defaults to `rfiq` (see the section below) - this is what's
actually running on station1 and dev-desktop:

```
docker run -d \
  --name sdr-shark \
  --restart unless-stopped \
  --network host \
  -v /tmp:/tmp \
  sdr-shark:latest
```

## Deploy (direct SoapySDR, `-e SDR_BACKEND=soapy`)

Only needed if you're bypassing `rfiq_daemon` and want this container to
open the BladeRF itself - competes directly with anything else (like
rf-sentinel) also trying to hold the radio, unlike the default `rfiq`
mode which shares it through the daemon:

```
docker run -d \
  --name sdr-shark \
  --restart unless-stopped \
  --network host \
  -v /dev/bus/usb:/dev/bus/usb \
  --device-cgroup-rule="c 189:* rmw" \
  --group-add plugdev \
  -e SDR_BACKEND=soapy \
  sdr-shark:latest
```

Or for the shared-gateway path instead (talks to `sdr-gateway` over HTTP,
no USB passthrough needed, but subject to whatever else is holding the
radios): `-e SDR_BACKEND=gateway`.

## Bugs found and fixed getting this running

None of these were "pick the working Dockerfile" - both original
Dockerfiles were broken in different ways, discovered one at a time:

1. `hatch build` failed: `virtualenv`/`hatch` version incompatibility
   (`module 'virtualenv.discovery.builtin' has no attribute
   'propose_interpreters'`). Replaced with a direct `pip install`.
2. `backend/pyproject.toml` has a `[project]` table (even with no
   `dependencies` listed) - modern setuptools treats that as authoritative
   over `setup.py`'s `install_requires` and silently drops it, so the
   package installed with **zero** dependencies. Deps are now installed
   explicitly from the root `requirements.txt`.
3. `bluetooth_demod` (listed in both `setup.py` and `pyproject.toml`)
   isn't on PyPI and isn't actually imported anywhere in this codebase -
   dropped from the build rather than fixed at the source.
4. Both original Dockerfiles ran `flask run`, but there's no
   `app`/`create_app` in `__init__.py` for Flask's CLI to find - the app
   self-hosts via `app.run()` in `__main__.py`'s `if __name__ ==
   "__main__"` block. `CMD` is now `python -m sdr_plot_backend`.
5. `python:3.9-slim` crashed on `str | None` union-type syntax in
   `rf_model_plugin.py` (needs 3.10+). Bumped to `python:3.10-slim`
   specifically (not just "3.10+") - see next point for why the exact
   version matters here.
6. `__main__.py` locates the frontend build via
   `Path(__file__).parents[3] / "frontend" / "build"`, which assumes
   running from a source checkout
   (`repo/backend/src/sdr_plot_backend/__main__.py` ->
   `repo/frontend/build`). Installing the backend into site-packages (the
   normal `pip install ./backend` outcome) breaks that path. Fixed by
   *not* installing it as a package - run from source via
   `PYTHONPATH=/app/backend/src` instead, with the built frontend copied
   to `/app/frontend/build` (a sibling of `backend/`, matching what the
   path resolution expects).

## `docker/soapy-build/`

Same finding as `passive-shield/station1-docker/` and
`rf-sentinel/docker/soapy-build/`, reused here: apt has no SoapySDR
dev/python package for this arch/repo (built from source on the station
host), and apt's `libbladerf2` (0.2021.10-2) can't properly drive
station1's radios after their firmware/FPGA update. The base image is
pinned to `python:3.10-slim` specifically because `_SoapySDR.so` here is
a compiled CPython extension built against the station host's system
python3.10 - it won't import under a different minor version.

## Two-SDR checkbox bug (fixed, `1e6e...`-era)

The backend always sent a full-length `secondaryFft` array (raw linear
magnitudes, *not* the dB scale the chart is plotted in) regardless of
whether a real second RX channel was active (`mimo.enabled`). With no
real second SDR, checking "2nd SDR" in the UI painted a solid
wrong-scale block across the whole chart. Fixed on both ends:

- Frontend (`ChartComponent.jsx`): only trust `secondaryFft` when
  `mimo.enabled` is true; the checkbox is now disabled/greyed out and
  auto-unchecks itself when there's no real second channel.
- Also fixed while in there: retuning center frequency didn't reset the
  Max-hold/persist FFT traces (`backend/src/sdr_plot_backend/api.py`,
  `update_settings()`), so stale bins from the previous span could
  linger misaligned against the new frequency axis after a retune.

## Default backend is now `rfiq`, not `soapy`

`SDR_BACKEND` defaults to `rfiq`: talks to `rfiq_daemon` (see
`rf-iq-gateway`) over unix sockets under `/tmp` rather than opening
SoapySDR directly, so it shares the radios with rf-sentinel through the
same daemon instead of both processes independently competing for direct
USB access. Deploy with `-v /tmp:/tmp` instead of the USB
passthrough/`--device-cgroup-rule` flags shown above for direct mode.
Both configured radios are exposed as separate selectable devices
(`rfiq:0`/`rfiq:1`, via `SDR_RFIQ_SOCKET_0`/`_1` +
`SDR_RFIQ_CONTROL_SOCKET_0`/`_1`, defaulting to `/tmp/rfiq0*.sock` and
`/tmp/rfiq1*.sock`), and retuning/gain changes made through the UI are
pushed to the daemon's control socket live - this was originally
receive-only and has been fixed to be fully interactive.

Override with `-e SDR_BACKEND=soapy` (direct SoapySDR, needs USB
passthrough - see the top of this file) or `-e SDR_BACKEND=gateway`
(the older `sdr-gateway` HTTP path) if you need those instead.
