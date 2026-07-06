from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import queue
import threading
import time
import sys
from typing import Any

import numpy as np
import requests
try:
    from websocket import WebSocket, create_connection
except Exception:
    WebSocket = Any  # type: ignore[assignment]
    create_connection = None


def _default_log_file(filename: str) -> Path:
    for directory in (Path("/var/log/sdr-shark"), Path.home() / ".sdr-shark" / "logs"):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            probe = directory / ".write-test"
            probe.touch(exist_ok=True)
            probe.unlink(missing_ok=True)
            return directory / filename
        except Exception:
            continue
    return Path.home() / ".sdr-shark" / "logs" / filename


class SDRGeneric:
    """Minimal SDR adapter backed by sdr-gateway or direct SoapySDR."""

    def __init__(
        self,
        name: str | None = None,
        center_freq: float = 102.1e6,
        sample_rate: float = 20e6,
        bandwidth: float = 20e6,
        gain: float = 30,
        size: int = 8192,
        **kwargs: Any,
    ) -> None:
        if name is None:
            name = kwargs.pop("sdr_type", None) or kwargs.pop("sdr_name", None) or "hackrf"
        self.name = name
        self.frequency = float(center_freq)
        self.sample_rate = float(sample_rate)
        self.bandwidth = float(bandwidth)
        self.gain = float(gain)
        self.size = int(size)
        self.min_frequency = 1e6
        self.max_frequency = 6e9
        self.max_sample_rate = 20e6
        self.backend = os.getenv("SDR_BACKEND", "soapy").strip().lower()

        base = os.getenv("SDR_SERVER_URL", "http://127.0.0.1:8080").rstrip("/")
        self.api_base = base
        self.ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
        self.gateway_token = (os.getenv("SDR_GATEWAY_API_TOKEN", "") or "").strip()
        self.gateway_requested_iq_format = (os.getenv("SDR_GATEWAY_IQ_FORMAT", "native") or "native").strip().lower()
        self.gateway_iq_format = self.gateway_requested_iq_format

        self._devices_cache: list[dict[str, Any]] = []
        self._selected_device_hint: str | None = None
        self.device_id: str | None = None
        self.stream_id: str | None = None
        self._ws: WebSocket | None = None
        self._rx_thread: threading.Thread | None = None
        self._should_run = False
        self._running = False
        self._lock = threading.Lock()
        self._latest_samples = np.zeros(self.size, dtype=np.complex64)
        self._latest_samples_secondary = np.zeros(self.size, dtype=np.complex64)
        self._iq_tap_lock = threading.Lock()
        self._iq_tap_subscribers: dict[str, queue.Queue[bytes | None]] = {}
        self._iq_tap_last_publish = 0.0
        self._iq_tap_interval_s = max(0.0, float(os.getenv("SDR_SHARK_IQ_TAP_INTERVAL_MS", "100") or "100") / 1000.0)
        self._soapy: Any = None
        self._soapy_device: Any = None
        self._soapy_stream: Any = None
        self._soapy_stream_format = ""
        self._soapy_channels: list[int] = [0]
        self._gateway_channels: list[int] = [0]
        self._soapy_device_args: dict[str, Any] | None = None
        default_log = _default_log_file("soapysdr.log")
        self._soapy_log_file = os.getenv("SDR_SOAPY_LOG_FILE", str(default_log))

    def _auth_headers(self) -> dict[str, str]:
        if self.gateway_token:
            return {"Authorization": f"Bearer {self.gateway_token}"}
        return {}

    def start(self) -> None:
        self._should_run = True
        if self.backend == "soapy":
            self._ensure_soapy_device()
            self._start_soapy_stream()
        else:
            self._ensure_device()
            self._start_stream()

    def stop(self) -> None:
        self._should_run = False
        self._running = False
        self._close_iq_taps()
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        if (
            self._rx_thread
            and self._rx_thread.is_alive()
            and self._rx_thread is not threading.current_thread()
        ):
            self._rx_thread.join(timeout=2)
        self._stop_stream()
        self._stop_soapy_stream()

    def set_frequency(self, frequency: float) -> None:
        self.frequency = float(frequency)
        if self.backend == "soapy" and self._soapy_device is not None:
            try:
                with self._quiet_soapy():
                    for channel in self._soapy_channels:
                        self._soapy_device.setFrequency(self._soapy.SOAPY_SDR_RX, channel, self.frequency)
                return
            except Exception:
                pass
        self._restart_stream()

    def set_sample_rate(self, sample_rate: float) -> None:
        self.sample_rate = float(sample_rate)
        self._restart_stream()

    def set_bandwidth(self, bandwidth: float) -> None:
        self.bandwidth = float(bandwidth)
        if self.backend == "soapy" and self._soapy_device is not None:
            try:
                with self._quiet_soapy():
                    for channel in self._soapy_channels:
                        self._soapy_device.setBandwidth(self._soapy.SOAPY_SDR_RX, channel, self.bandwidth)
            except Exception:
                pass

    def set_gain(self, gain: float) -> None:
        self.gain = float(gain)
        if self.backend == "soapy" and self._soapy_device is not None:
            self._apply_soapy_gain(self._soapy_device, self._soapy_driver(), self.gain)
            return
        self._restart_stream()

    def configure_receiver(
        self,
        *,
        frequency: float | None = None,
        sample_rate: float | None = None,
        bandwidth: float | None = None,
        gain: float | None = None,
    ) -> None:
        """Apply related tuning settings with at most one stream restart."""
        old_sample_rate = self.sample_rate
        if frequency is not None:
            self.frequency = float(frequency)
        if sample_rate is not None:
            self.sample_rate = float(sample_rate)
        if bandwidth is not None:
            self.bandwidth = float(bandwidth)
        if gain is not None:
            self.gain = float(gain)

        if self.backend != "soapy":
            self._restart_stream()
            return

        needs_restart = self._soapy_device is None or abs(float(old_sample_rate) - float(self.sample_rate)) > 1.0
        if needs_restart:
            self._restart_stream()
            return

        try:
            with self._quiet_soapy():
                for channel in self._soapy_channels:
                    self._soapy_device.setFrequency(self._soapy.SOAPY_SDR_RX, channel, self.frequency)
        except Exception:
            self._restart_stream()
            return
        try:
            with self._quiet_soapy():
                for channel in self._soapy_channels:
                    self._soapy_device.setBandwidth(self._soapy.SOAPY_SDR_RX, channel, self.bandwidth)
        except Exception:
            pass
        self._apply_soapy_gain(self._soapy_device, self._soapy_driver(), self.gain)

    def get_latest_samples(self) -> np.ndarray:
        with self._lock:
            return self._latest_samples.copy()

    def get_latest_samples_secondary(self) -> np.ndarray | None:
        with self._lock:
            active_channels = self._soapy_channels if self.backend == "soapy" else self._gateway_channels
            if len(active_channels) < 2:
                return None
            return self._latest_samples_secondary.copy()

    def mimo_info(self) -> dict[str, Any]:
        active_channels = self._soapy_channels if self.backend == "soapy" else self._gateway_channels
        return {
            "enabled": bool(len(active_channels) > 1),
            "channels": list(active_channels),
            "backend": self.backend,
            "device_id": self.device_id or self.name or "",
        }

    def iq_tap_info(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "source": "soapy_tap",
            "device_id": self.device_id or self.name or "",
            "iq_format": "i8",
            "center_freq_hz": int(round(self.frequency)),
            "sample_rate_sps": int(round(self.sample_rate)),
            "bandwidth_hz": int(round(self.bandwidth)),
            "gain_db": float(self.gain),
        }

    def subscribe_iq_tap(self, max_chunks: int = 32):
        subscriber_id = f"tap-{time.time_ns()}"
        chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=max(1, int(max_chunks)))
        with self._iq_tap_lock:
            self._iq_tap_subscribers[subscriber_id] = chunks
        return subscriber_id, chunks

    def release_iq_tap(self, subscriber_id: str) -> None:
        with self._iq_tap_lock:
            self._iq_tap_subscribers.pop(subscriber_id, None)

    def gateway_stream_info(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "api_base": self.api_base,
            "ws_base": self.ws_base,
            "token": self.gateway_token,
            "device_id": self.device_id or "",
            "stream_id": self.stream_id or "",
            "iq_format": self.gateway_iq_format,
            "center_freq_hz": int(round(self.frequency)),
            "sample_rate_sps": int(round(self.sample_rate)),
            "bandwidth_hz": int(round(self.bandwidth)),
            "gain_db": float(self.gain),
            "rx_channels": list(self._gateway_channels),
        }

    def list_devices(self) -> list[dict[str, Any]]:
        if self.backend == "soapy":
            devices = self._fetch_soapy_devices()
            return [dict(d) for d in devices]
        devices = self._fetch_devices()
        return [dict(d) for d in devices]

    def select_device(self, selector: str) -> bool:
        if self.backend == "soapy":
            return self._select_soapy_device(selector)

        devices = self._fetch_devices()
        if not devices:
            return False

        previous_hint = self._selected_device_hint
        previous_device_id = self.device_id
        previous_limits = (self.min_frequency, self.max_frequency, self.max_sample_rate)
        was_running = self._running

        selected = None
        for d in devices:
            if selector == d.get("id"):
                selected = d
                break
        if selected is None:
            for d in devices:
                if selector == d.get("driver"):
                    selected = d
                    break
        if selected is None:
            return False

        self._selected_device_hint = selected.get("id")
        self._apply_device_limits(selected)
        self.device_id = selected.get("id")

        # Changing devices must be atomic from the caller perspective.
        # If new stream startup fails (e.g. discovery-only backend), roll back.
        try:
            if was_running:
                self._restart_stream()
            return True
        except Exception:
            self._selected_device_hint = previous_hint
            self.device_id = previous_device_id
            self.min_frequency, self.max_frequency, self.max_sample_rate = previous_limits
            if was_running and previous_device_id is not None:
                try:
                    self._restart_stream()
                except Exception:
                    pass
            raise

    @contextmanager
    def _quiet_soapy(self):
        """Route noisy Soapy/vendor stdout/stderr output to a dedicated log file."""
        if os.getenv("SDR_SOAPY_LOG_STDERR", "").lower() in {"1", "true", "yes"}:
            yield
            return
        log_path = Path(self._soapy_log_file).expanduser()
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "ab", buffering=0) as log:
                log.write(f"\n--- SoapySDR {time.strftime('%Y-%m-%d %H:%M:%S')} ---\n".encode("utf-8"))
                saved_stdout = os.dup(1)
                saved_stderr = os.dup(2)
                try:
                    os.dup2(log.fileno(), 1)
                    os.dup2(log.fileno(), 2)
                    yield
                finally:
                    try:
                        sys.stdout.flush()
                        sys.stderr.flush()
                    except Exception:
                        pass
                    os.dup2(saved_stdout, 1)
                    os.dup2(saved_stderr, 2)
                    os.close(saved_stdout)
                    os.close(saved_stderr)
        except Exception:
            # If logging itself fails, do not break SDR operation.
            yield

    def _import_soapy(self):
        if self._soapy is not None:
            return self._soapy
        try:
            with self._quiet_soapy():
                import SoapySDR  # type: ignore
        except Exception as exc:
            # Common case: SDR-Shark is in a venv while SoapySDR is installed
            # into system or /usr/local site-packages by distro packages.
            ver = f"{sys.version_info.major}.{sys.version_info.minor}"
            for candidate in (
                Path(f"/usr/local/lib/python{ver}/site-packages"),
                Path(f"/usr/lib/python{ver}/dist-packages"),
            ):
                if candidate.exists() and str(candidate) not in sys.path:
                    sys.path.append(str(candidate))
            try:
                with self._quiet_soapy():
                    import SoapySDR  # type: ignore
            except Exception as retry_exc:
                raise RuntimeError(
                    "SDR_BACKEND=soapy requires the SoapySDR Python bindings. "
                    "Install your distro's python3-soapysdr package, then verify "
                    "`python -c 'import SoapySDR'` works."
                ) from retry_exc or exc
        self._soapy = SoapySDR
        return self._soapy

    def _soapy_drivers(self) -> list[str]:
        configured = os.getenv("SDR_SOAPY_DRIVERS", "hackrf,sidekiq,airspy,bladerf,rtlsdr,antsdre200")
        return [d.strip().lower() for d in configured.split(",") if d.strip()]

    def _fetch_soapy_devices(self) -> list[dict[str, Any]]:
        soapy = self._import_soapy()
        devices: list[dict[str, Any]] = []
        for driver in self._soapy_drivers():
            try:
                with self._quiet_soapy():
                    matches = soapy.Device.enumerate({"driver": driver})
            except Exception:
                matches = []
            for idx, item in enumerate(matches):
                info = dict(item)
                actual_driver = str(info.get("driver", driver)).lower()
                label = info.get("label") or info.get("serial") or info.get("serialnum") or f"{actual_driver}:{idx}"
                devices.append({
                    "id": f"{actual_driver}:{idx}",
                    "driver": actual_driver,
                    "label": str(label),
                    "serial": info.get("serial") or info.get("serialnum") or "",
                    "backend": "soapy",
                    "soapy_args": info,
                    "freq_min_hz": 1e6,
                    "freq_max_hz": 6e9,
                    "max_sample_rate_sps": self._default_max_sample_rate(actual_driver),
                    "notes": "Direct SoapySDR backend",
                })
        self._devices_cache = devices
        return devices

    def _default_max_sample_rate(self, driver: str) -> float:
        return {
            "sidekiq": 60e6,
            "hackrf": 20e6,
            "airspy": 10e6,
            "bladerf": 60e6,
            "rtlsdr": 2.4e6,
            "antsdre200": 20e6,
        }.get(str(driver).lower(), 20e6)

    def _select_soapy_device(self, selector: str) -> bool:
        devices = self._fetch_soapy_devices()
        if not devices:
            return False
        selected = next((d for d in devices if selector == d.get("id")), None)
        if selected is None:
            selected = next((d for d in devices if selector == d.get("driver")), None)
        if selected is None:
            return False

        was_running = self._running
        previous_id = self.device_id
        previous_args = self._soapy_device_args
        self.device_id = str(selected["id"])
        self._selected_device_hint = self.device_id
        self._soapy_device_args = dict(selected.get("soapy_args") or {"driver": selected["driver"]})
        self._apply_device_limits(selected)

        try:
            if was_running:
                self._restart_stream()
            return True
        except Exception:
            self.device_id = previous_id
            self._selected_device_hint = previous_id
            self._soapy_device_args = previous_args
            raise

    def _ensure_soapy_device(self) -> None:
        devices = self._fetch_soapy_devices()
        if not devices:
            raise RuntimeError("No SoapySDR devices found. Try `SoapySDRUtil --find`.")
        device = None
        if self._selected_device_hint:
            device = next((d for d in devices if d.get("id") == self._selected_device_hint), None)
        if device is None:
            requested = str(self.name).split(":", 1)[0].lower()
            device = next((d for d in devices if d.get("driver") == requested), None)
        if device is None:
            device = devices[0]
        self.device_id = str(device["id"])
        self._selected_device_hint = self.device_id
        self._soapy_device_args = dict(device.get("soapy_args") or {"driver": device["driver"]})
        self._apply_device_limits(device)

    def _soapy_driver(self) -> str:
        if self.device_id:
            return str(self.device_id).split(":", 1)[0].lower()
        if self._soapy_device_args:
            return str(self._soapy_device_args.get("driver", self.name)).lower()
        return str(self.name).split(":", 1)[0].lower()

    def _range_bounds(self, rng, default_min: float, default_max: float) -> tuple[float, float]:
        try:
            if isinstance(rng, (tuple, list)) and len(rng) >= 2:
                return float(rng[0]), float(rng[1])
            if hasattr(rng, "minimum") and hasattr(rng, "maximum"):
                min_fn = getattr(rng, "minimum")
                max_fn = getattr(rng, "maximum")
                return float(min_fn() if callable(min_fn) else min_fn), float(max_fn() if callable(max_fn) else max_fn)
            if hasattr(rng, "min") and hasattr(rng, "max"):
                return float(getattr(rng, "min")), float(getattr(rng, "max"))
        except Exception:
            pass
        return float(default_min), float(default_max)

    def _clip_soapy_gain(self, dev, value: float) -> float:
        try:
            lo, hi = self._range_bounds(dev.getGainRange(self._soapy.SOAPY_SDR_RX, 0), 0.0, 76.0)
        except Exception:
            lo, hi = 0.0, 76.0
        return float(min(max(value, lo), hi))

    def _apply_soapy_gain(self, dev, driver: str, gain: float) -> None:
        for channel in self._soapy_channels:
            try:
                with self._quiet_soapy():
                    dev.setGainMode(self._soapy.SOAPY_SDR_RX, channel, False)
            except Exception:
                pass
            try:
                with self._quiet_soapy():
                    dev.setGain(self._soapy.SOAPY_SDR_RX, channel, self._clip_soapy_gain(dev, float(gain)))
                continue
            except Exception:
                pass
            try:
                names = list(dev.listGains(self._soapy.SOAPY_SDR_RX, channel))
            except Exception:
                names = []
            if not names:
                continue
            # Basic staged gain fallback: split user gain across exposed elements.
            per_stage = float(gain) / max(1, len(names))
            for name in names:
                try:
                    lo, hi = self._range_bounds(dev.getGainRange(self._soapy.SOAPY_SDR_RX, channel, name), 0.0, 76.0)
                    with self._quiet_soapy():
                        dev.setGain(self._soapy.SOAPY_SDR_RX, channel, name, float(min(max(per_stage, lo), hi)))
                except Exception:
                    continue

    def _desired_soapy_channels(self, dev) -> list[int]:
        mode = os.getenv("SDR_SHARK_MIMO", "0").strip().lower()
        if mode in {"0", "false", "no", "off", "disabled"}:
            return [0]
        try:
            channel_count = int(dev.getNumChannels(self._soapy.SOAPY_SDR_RX))
        except Exception:
            channel_count = 1
        if channel_count < 2:
            return [0]
        driver = self._soapy_driver()
        if mode in {"1", "true", "yes", "on", "enabled"}:
            return [0, 1]
        return [0]

    def _configure_soapy_device(self, dev) -> None:
        soapy = self._soapy
        sample_rate = float(max(1, min(self.max_sample_rate, self.sample_rate)))
        channels = self._desired_soapy_channels(dev)
        with self._quiet_soapy():
            for channel in channels:
                dev.setSampleRate(soapy.SOAPY_SDR_RX, channel, sample_rate)
                dev.setFrequency(soapy.SOAPY_SDR_RX, channel, float(self.frequency))
        try:
            with self._quiet_soapy():
                for channel in channels:
                    dev.setBandwidth(soapy.SOAPY_SDR_RX, channel, float(min(sample_rate, max(1, self.bandwidth))))
        except Exception:
            pass
        self._soapy_channels = channels
        self._apply_soapy_gain(dev, self._soapy_driver(), self.gain)

        try:
            ranges = dev.getFrequencyRange(soapy.SOAPY_SDR_RX, 0)
            if ranges:
                lows, highs = zip(*(self._range_bounds(r, self.min_frequency, self.max_frequency) for r in ranges))
                self.min_frequency = min(lows)
                self.max_frequency = max(highs)
        except Exception:
            pass
        try:
            rates = [float(x) for x in dev.listSampleRates(soapy.SOAPY_SDR_RX, 0)]
            if rates:
                self.max_sample_rate = max(rates)
        except Exception:
            pass

    def _start_soapy_stream(self) -> None:
        soapy = self._import_soapy()
        if self._soapy_device_args is None:
            self._ensure_soapy_device()
        with self._quiet_soapy():
            dev = soapy.Device(dict(self._soapy_device_args or {}))
        self._configure_soapy_device(dev)

        stream = None
        stream_format = ""
        desired_channels = list(self._soapy_channels)
        for fmt in ("SOAPY_SDR_CS16", "SOAPY_SDR_CF32"):
            if not hasattr(soapy, fmt):
                continue
            for channels in (desired_channels, [0]):
                try:
                    with self._quiet_soapy():
                        stream = dev.setupStream(soapy.SOAPY_SDR_RX, getattr(soapy, fmt), channels)
                    self._soapy_channels = list(channels)
                    stream_format = fmt
                    break
                except Exception:
                    stream = None
                    continue
            if stream is not None:
                break
        if stream is None:
            raise RuntimeError("Unable to setup a SoapySDR RX stream using CS16 or CF32")

        with self._quiet_soapy():
            dev.activateStream(stream)
        self._soapy_device = dev
        self._soapy_stream = stream
        self._soapy_stream_format = stream_format
        self._running = True
        self._rx_thread = threading.Thread(target=self._soapy_rx_loop, daemon=True)
        self._rx_thread.start()

    def _stop_soapy_stream(self) -> None:
        if self._soapy_device is None or self._soapy_stream is None:
            self._soapy_device = None
            self._soapy_stream = None
            return
        try:
            with self._quiet_soapy():
                self._soapy_device.deactivateStream(self._soapy_stream)
        except Exception:
            pass
        try:
            with self._quiet_soapy():
                self._soapy_device.closeStream(self._soapy_stream)
        except Exception:
            pass
        self._soapy_device = None
        self._soapy_stream = None
        self._soapy_channels = [0]
        self._close_iq_taps()

    def _soapy_rx_loop(self) -> None:
        soapy = self._soapy
        dev = self._soapy_device
        stream = self._soapy_stream
        if dev is None or stream is None:
            return
        chunk_samples = max(self.size, 4096)
        channel_count = max(1, len(self._soapy_channels))
        if self._soapy_stream_format == "SOAPY_SDR_CF32":
            rx_bufs = [np.empty(chunk_samples, dtype=np.complex64) for _ in range(channel_count)]
        else:
            rx_bufs = [np.empty(chunk_samples * 2, dtype=np.int16) for _ in range(channel_count)]

        while self._running and self._should_run:
            try:
                with self._quiet_soapy():
                    result = dev.readStream(stream, rx_bufs, chunk_samples, timeoutUs=200_000)
                n = int(getattr(result, "ret", result))
                if n <= 0:
                    continue
                if self._soapy_stream_format == "SOAPY_SDR_CF32":
                    iq = rx_bufs[0][:n].astype(np.complex64, copy=True)
                    secondary_iq = (
                        rx_bufs[1][:n].astype(np.complex64, copy=True)
                        if channel_count > 1 else None
                    )
                    if self._should_publish_iq_tap():
                        self._publish_iq_tap(self._complex_to_cs8(iq))
                else:
                    raw_iq16 = rx_bufs[0][: n * 2]
                    if self._should_publish_iq_tap():
                        self._publish_iq_tap(self._cs16_to_cs8(raw_iq16))
                    iq = self._cs16_to_complex(raw_iq16)
                    secondary_iq = (
                        self._cs16_to_complex(rx_bufs[1][: n * 2])
                        if channel_count > 1 else None
                    )
                if iq.size >= self.size:
                    out = iq[: self.size]
                else:
                    out = np.zeros(self.size, dtype=np.complex64)
                    out[: iq.size] = iq
                if secondary_iq is not None:
                    if secondary_iq.size >= self.size:
                        secondary_out = secondary_iq[: self.size]
                    else:
                        secondary_out = np.zeros(self.size, dtype=np.complex64)
                        secondary_out[: secondary_iq.size] = secondary_iq
                else:
                    secondary_out = np.zeros(self.size, dtype=np.complex64)
                with self._lock:
                    self._latest_samples = out.astype(np.complex64, copy=False)
                    self._latest_samples_secondary = secondary_out.astype(np.complex64, copy=False)
            except Exception:
                if not self._should_run:
                    break
                while self._should_run:
                    try:
                        self._restart_stream()
                        break
                    except Exception:
                        time.sleep(0.5)
                return

    def _should_publish_iq_tap(self) -> bool:
        with self._iq_tap_lock:
            if not self._iq_tap_subscribers:
                return False
        now = time.monotonic()
        if self._iq_tap_interval_s > 0 and (now - self._iq_tap_last_publish) < self._iq_tap_interval_s:
            return False
        self._iq_tap_last_publish = now
        return True

    def _publish_iq_tap(self, chunk: bytes) -> None:
        if not chunk:
            return
        with self._iq_tap_lock:
            subscribers = list(self._iq_tap_subscribers.values())
        for chunks in subscribers:
            try:
                chunks.put_nowait(chunk)
                continue
            except queue.Full:
                pass
            try:
                chunks.get_nowait()
                chunks.task_done()
            except queue.Empty:
                pass
            try:
                chunks.put_nowait(chunk)
            except queue.Full:
                pass

    def _close_iq_taps(self) -> None:
        with self._iq_tap_lock:
            subscribers = list(self._iq_tap_subscribers.values())
            self._iq_tap_subscribers.clear()
        for chunks in subscribers:
            try:
                chunks.put_nowait(None)
            except queue.Full:
                pass

    def _cs16_to_cs8(self, raw_iq16: np.ndarray) -> bytes:
        divisor = float(os.getenv("SDR_SHARK_SOAPY_TAP_CS16_TO_I8_DIVISOR", "256") or "256")
        return np.clip(np.rint(raw_iq16.astype(np.float32) / divisor), -128, 127).astype(np.int8).tobytes()

    def _cs16_to_complex(self, raw_iq16: np.ndarray) -> np.ndarray:
        iq16 = raw_iq16.astype(np.float32)
        return ((iq16[0::2] + 1j * iq16[1::2]) / 32768.0).astype(np.complex64, copy=False)

    def _complex_to_cs8(self, iq: np.ndarray) -> bytes:
        interleaved = np.empty(iq.size * 2, dtype=np.float32)
        interleaved[0::2] = iq.real
        interleaved[1::2] = iq.imag
        return np.clip(np.rint(interleaved * 127.0), -128, 127).astype(np.int8).tobytes()

    def _fetch_devices(self) -> list[dict[str, Any]]:
        try:
            r = requests.get(
                f"{self.api_base}/devices",
                headers=self._auth_headers(),
                timeout=5,
            )
            r.raise_for_status()
            devices = r.json()
            if not isinstance(devices, list):
                return list(self._devices_cache)
            self._devices_cache = devices
            return devices
        except Exception:
            # Keep UI usable during gateway restarts/auth race by serving last known list.
            if self._devices_cache:
                return list(self._devices_cache)
            raise

    def _apply_device_limits(self, device: dict[str, Any]) -> None:
        self.min_frequency = float(device.get("freq_min_hz", self.min_frequency))
        self.max_frequency = float(device.get("freq_max_hz", self.max_frequency))
        self.max_sample_rate = float(device.get("max_sample_rate_sps", self.max_sample_rate))

    def _ensure_device(self) -> None:
        devices = self._fetch_devices()
        if not devices:
            raise RuntimeError("No SDR devices found from sdr-gateway /devices")

        device = None
        if self._selected_device_hint:
            device = next((d for d in devices if d.get("id") == self._selected_device_hint), None)
        if device is None:
            requested = str(self.name).split(":", 1)[0].lower()
            device = next((d for d in devices if str(d.get("driver", "")).lower() == requested), None)
        if device is None:
            # Prefer real HackRF for legacy default; fallback to first listed device.
            device = next((d for d in devices if d.get("driver") == "hackrf"), devices[0])

        self.device_id = device["id"]
        self._apply_device_limits(device)

    def _stream_payload(self) -> dict[str, Any]:
        gain = int(round(self.gain))
        lna_gain = max(0, min(40, gain - (gain % 8)))
        vga_gain = max(0, min(62, gain))
        sample_rate = int(max(2_000_000, min(self.max_sample_rate, round(self.sample_rate))))
        rx_channels = self._desired_gateway_channels()

        payload = {
            "device_id": self.device_id,
            "center_freq_hz": int(round(self.frequency)),
            "sample_rate_sps": sample_rate,
            "lna_gain_db": lna_gain,
            "vga_gain_db": vga_gain,
            "amp_enable": True,
            "replace_existing": True,
            "baseband_filter_hz": int(round(min(sample_rate, max(1_750_000, self.bandwidth)))),
            "iq_format": self.gateway_requested_iq_format,
        }
        if rx_channels:
            payload["rx_channels"] = rx_channels
        self._gateway_channels = rx_channels or [0]
        return payload

    def _desired_gateway_channels(self) -> list[int]:
        mode = os.getenv("SDR_SHARK_MIMO", "0").strip().lower()
        if mode in {"0", "false", "no", "off", "disabled"}:
            return [0]
        device_id = str(self.device_id or self.name or "").lower()
        driver = device_id.split(":", 1)[0]
        if mode in {"1", "true", "yes", "on", "enabled"}:
            return [0, 1]
        return [0]

    def _start_stream(self) -> None:
        if create_connection is None:
            raise RuntimeError(
                "websocket-client is not available. "
                "Install with: pip uninstall -y websocket && pip install websocket-client"
            )

        if self.device_id is None:
            self._ensure_device()

        r = requests.post(
            f"{self.api_base}/streams/start",
            json=self._stream_payload(),
            headers=self._auth_headers(),
            timeout=10,
        )
        r.raise_for_status()
        stream_state = r.json()
        self.stream_id = stream_state["stream_id"]
        stream_channels = stream_state.get("config", {}).get("rx_channels")
        if isinstance(stream_channels, list) and stream_channels:
            self._gateway_channels = [int(ch) for ch in stream_channels]
        self.gateway_iq_format = (
            stream_state.get("config", {}).get("iq_format") or self.gateway_iq_format or "i8"
        ).strip().lower()

        ws_headers = None
        if self.gateway_token:
            ws_headers = [f"Authorization: Bearer {self.gateway_token}"]
        self._ws = create_connection(
            f"{self.ws_base}/ws/iq/{self.stream_id}",
            header=ws_headers,
            timeout=5,
            enable_multithread=True,
        )
        self._running = True
        self._rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self._rx_thread.start()

    def _stop_stream(self) -> None:
        if not self.stream_id:
            return
        try:
            requests.post(
                f"{self.api_base}/streams/{self.stream_id}/stop",
                json={},
                headers=self._auth_headers(),
                timeout=5,
            )
        except Exception:
            pass
        self.stream_id = None

    def _restart_stream(self) -> None:
        if not self._should_run:
            return
        # Keep desired running state true during automatic reconnect attempts.
        self._running = False
        if self._ws is not None:
            try:
                self._ws.close()
            except Exception:
                pass
            self._ws = None
        self._stop_stream()
        self._stop_soapy_stream()
        time.sleep(0.05)
        self.start()

    def _rx_loop(self) -> None:
        assert self._ws is not None
        while self._running and self._should_run:
            try:
                frame = self._ws.recv()
                if not isinstance(frame, (bytes, bytearray)):
                    continue
                if self._should_publish_iq_tap():
                    self._publish_iq_tap(self._gateway_frame_to_cs8(frame))
                iq, secondary_iq = self._decode_gateway_frame_pair(frame)

                if iq.size >= self.size:
                    out = iq[: self.size]
                else:
                    out = np.zeros(self.size, dtype=np.complex64)
                    out[: iq.size] = iq
                if secondary_iq is not None:
                    if secondary_iq.size >= self.size:
                        secondary_out = secondary_iq[: self.size]
                    else:
                        secondary_out = np.zeros(self.size, dtype=np.complex64)
                        secondary_out[: secondary_iq.size] = secondary_iq
                else:
                    secondary_out = np.zeros(self.size, dtype=np.complex64)

                with self._lock:
                    self._latest_samples = out.astype(np.complex64, copy=False)
                    self._latest_samples_secondary = secondary_out.astype(np.complex64, copy=False)
            except Exception:
                if not self._should_run:
                    break
                # Recover automatically when websocket stalls/drops or gateway restarts.
                while self._should_run:
                    try:
                        self._restart_stream()
                        break
                    except Exception:
                        time.sleep(0.5)
                # A new stream spawns a new RX thread; exit this one.
                return
                break

    def _decode_gateway_frame(self, frame: bytes | bytearray) -> np.ndarray:
        primary, _ = self._decode_gateway_frame_pair(frame)
        return primary

    def _decode_gateway_frame_pair(self, frame: bytes | bytearray) -> tuple[np.ndarray, np.ndarray | None]:
        iq_format = self.gateway_iq_format
        if iq_format == "cs16":
            iq_raw = np.frombuffer(frame, dtype=np.int16)
            scale = 32768.0
            values_per_complex = 2
        else:
            iq_raw = np.frombuffer(frame, dtype=np.int8)
            scale = 128.0
            values_per_complex = 2

        if iq_raw.size < 2:
            return np.empty(0, dtype=np.complex64), None

        channel_count = max(1, len(self._gateway_channels))
        values_per_frame = values_per_complex * channel_count
        usable = iq_raw.size - (iq_raw.size % values_per_frame)
        if usable <= 0:
            return np.empty(0, dtype=np.complex64), None
        iq_raw = iq_raw[:usable]

        if channel_count > 1:
            framed = iq_raw.reshape(-1, channel_count, values_per_complex)
            primary_raw = framed[:, 0, :].reshape(-1)
            secondary_raw = framed[:, 1, :].reshape(-1)
        else:
            primary_raw = iq_raw
            secondary_raw = None

        primary = self._interleaved_iq_to_complex(primary_raw, scale)
        secondary = self._interleaved_iq_to_complex(secondary_raw, scale) if secondary_raw is not None else None
        return primary, secondary

    def _interleaved_iq_to_complex(self, iq_raw: np.ndarray | None, scale: float) -> np.ndarray:
        if iq_raw is None or iq_raw.size < 2:
            return np.empty(0, dtype=np.complex64)
        if iq_raw.size % 2:
            iq_raw = iq_raw[:-1]
        i = iq_raw[0::2].astype(np.float32)
        q = iq_raw[1::2].astype(np.float32)
        return ((i + 1j * q) / scale).astype(np.complex64, copy=False)

    def _gateway_frame_to_cs8(self, frame: bytes | bytearray) -> bytes:
        if len(self._gateway_channels) > 1:
            primary, _ = self._decode_gateway_frame_pair(frame)
            return self._complex_to_cs8(primary)
        if self.gateway_iq_format == "cs16":
            return self._cs16_to_cs8(np.frombuffer(frame, dtype=np.int16))
        if self.gateway_iq_format == "cf32":
            return self._complex_to_cs8(np.frombuffer(frame, dtype=np.complex64))
        return bytes(frame)
