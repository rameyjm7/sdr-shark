# Stage 1: Build the React frontend
FROM node:22 AS build-frontend

# Set the working directory
WORKDIR /frontend

# Copy the frontend package.json and yarn.lock
COPY ./frontend/package.json ./frontend/yarn.lock ./

# Install frontend dependencies
RUN yarn install

# Copy the rest of the frontend code
COPY ./frontend ./

# Build the frontend
RUN yarn build

# Stage 2: Set up the Python backend
# 3.9 doesn't support `str | None`-style union type hints used in
# rf_model_plugin.py (PEP 604, Python 3.10+); the original 3.9-slim base
# crashed on import with `TypeError: unsupported operand type(s) for |`.
# Pinned to 3.10 specifically (not just >=3.10) because the SoapySDR python
# bindings below are a compiled CPython extension (_SoapySDR.so) built
# against the station host's system python3.10 - it won't import under a
# different minor version.
FROM python:3.10-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
      usbutils libusb-1.0-0 libbladerf2 bladerf \
    && rm -rf /var/lib/apt/lists/*

# Direct SoapySDR support: apt has no SoapySDR dev/python package for this
# arch/repo (built from source on the station host), and apt's libbladerf2
# (0.2021.10-2) can't properly drive these radios after their firmware/FPGA
# update - same finding as the passive-shield and rf-sentinel images.
# Overwrite the apt libbladeRF at its own path so ld.so.cache doesn't keep
# preferring the broken version over /usr/local/lib.
COPY docker/soapy-build/include/SoapySDR /usr/local/include/SoapySDR
COPY docker/soapy-build/SoapySDR.pc /usr/local/lib/pkgconfig/SoapySDR.pc
COPY docker/soapy-build/libSoapySDR.so.0.8-3 /usr/local/lib/libSoapySDR.so.0.8-3
COPY docker/soapy-build/libbladeRFSupport.so /usr/local/lib/SoapySDR/modules0.8-3/libbladeRFSupport.so
COPY docker/soapy-build/libbladeRF.so.2 /usr/lib/aarch64-linux-gnu/libbladeRF.so.2
COPY docker/soapy-build/SoapySDR.py /usr/local/lib/python3.10/dist-packages/SoapySDR.py
COPY docker/soapy-build/_SoapySDR.so /usr/local/lib/python3.10/dist-packages/_SoapySDR.so
RUN ln -sf /usr/local/lib/libSoapySDR.so.0.8-3 /usr/local/lib/libSoapySDR.so.0.8 \
    && ln -sf /usr/local/lib/libSoapySDR.so.0.8-3 /usr/local/lib/libSoapySDR.so \
    && ldconfig

WORKDIR /app

# Copy the rest of the application code
COPY ./requirements.txt ./requirements.txt
COPY ./backend ./backend

# backend has no [build-system] table in pyproject.toml, so `hatch build`
# tries to spin up its own isolated build env - that mechanism is broken
# here (hatch/virtualenv version mismatch: "module 'virtualenv.discovery
# .builtin' has no attribute 'propose_interpreters'"). Also: because
# backend/pyproject.toml has a [project] table (even with no explicit
# dependencies listed), modern setuptools treats that as authoritative over
# setup.py's install_requires and silently drops it - `pip install
# ./backend` alone installs the package with *zero* dependencies.
# bluetooth_demod (listed in both backend/setup.py and pyproject.toml) is
# not on PyPI and isn't actually imported anywhere in this codebase -
# dropped here rather than fixed at the source, since that's outside the
# scope of getting this image running.
#
# Deliberately NOT pip-installing ./backend as a package: __main__.py
# locates the frontend build via `Path(__file__).parents[3] / "frontend" /
# "build"`, which assumes running from a source checkout
# (repo/backend/src/sdr_plot_backend/__main__.py -> repo/frontend/build).
# Installing into site-packages breaks that path. Run from source with
# PYTHONPATH instead, matching this container's /app/backend + /app/frontend
# layout so the relative path resolves correctly.
RUN pip install --no-cache-dir -r requirements.txt
ENV PYTHONPATH=/app/backend/src:/usr/local/lib/python3.10/dist-packages

# Copy the built frontend files from the first stage - path matters here,
# see the note above (must be a sibling of backend/, not inside it).
COPY --from=build-frontend /frontend/build /app/frontend/build

# Default backend is rfiq: talks to the station's rfiq_daemon instance(s)
# (one per BladeRF, unix sockets under /tmp - needs -v /tmp:/tmp, not USB
# passthrough) rather than opening SoapySDR itself, so it shares hardware
# through the same daemon rf-sentinel also uses instead of directly
# competing for the radios. Override with -e SDR_BACKEND=soapy for direct
# SoapySDR access (needs USB passthrough instead - see docker/README.md),
# or -e SDR_BACKEND=gateway for the older sdr-gateway HTTP path.
ENV SDR_BACKEND=rfiq
ENV SDR_SERVER_URL=http://127.0.0.1:8080
# __main__.py also spawns a dev frontend server by default unless told not
# to - the build above is already production static output.
ENV SDR_SHARK_AUTO_START_FRONTEND=0

# Expose port 5000 for the Flask server
EXPOSE 5000

# The app self-hosts via app.run() in __main__.py's `if __name__ ==
# "__main__"` block - it's not meant to be run through the `flask` CLI
# (there's no `app`/`create_app` in __init__.py for `flask run` to find).
CMD ["python", "-m", "sdr_plot_backend"]
