from __future__ import annotations

import os
import threading
import time
from typing import Any

import numpy as np
import requests
try:
    from websocket import WebSocket, create_connection
except Exception:
    WebSocket = Any  # type: ignore[assignment]
    create_connection = None


class SDRGeneric:
    """Minimal SDR adapter backed by the local sdr-gateway API."""

    def __init__(
        self,
        name: str,
        center_freq: float,
        sample_rate: float,
        bandwidth: float,
        gain: float,
        size: int,
    ) -> None:
        self.name = name
        self.frequency = float(center_freq)
        self.sample_rate = float(sample_rate)
        self.bandwidth = float(bandwidth)
        self.gain = float(gain)
        self.size = int(size)
        self.min_frequency = 1e6
        self.max_frequency = 6e9
        self.max_sample_rate = 20e6

        base = os.getenv("SDR_SERVER_URL", "http://127.0.0.1:8080").rstrip("/")
        self.api_base = base
        self.ws_base = base.replace("http://", "ws://").replace("https://", "wss://")
        self.gateway_token = (os.getenv("SDR_GATEWAY_API_TOKEN", "") or "").strip()

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

    def _auth_headers(self) -> dict[str, str]:
        if self.gateway_token:
            return {"Authorization": f"Bearer {self.gateway_token}"}
        return {}

    def start(self) -> None:
        self._should_run = True
        self._ensure_device()
        self._start_stream()

    def stop(self) -> None:
        self._should_run = False
        self._running = False
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

    def set_frequency(self, frequency: float) -> None:
        self.frequency = float(frequency)
        self._restart_stream()

    def set_sample_rate(self, sample_rate: float) -> None:
        self.sample_rate = float(sample_rate)
        self._restart_stream()

    def set_bandwidth(self, bandwidth: float) -> None:
        self.bandwidth = float(bandwidth)

    def set_gain(self, gain: float) -> None:
        self.gain = float(gain)
        self._restart_stream()

    def get_latest_samples(self) -> np.ndarray:
        with self._lock:
            return self._latest_samples.copy()

    def list_devices(self) -> list[dict[str, Any]]:
        devices = self._fetch_devices()
        return [dict(d) for d in devices]

    def select_device(self, selector: str) -> bool:
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
            # Prefer real HackRF; fallback to first listed device.
            device = next((d for d in devices if d.get("driver") == "hackrf"), devices[0])

        self.device_id = device["id"]
        self._apply_device_limits(device)

    def _stream_payload(self) -> dict[str, Any]:
        gain = int(round(self.gain))
        lna_gain = max(0, min(40, gain - (gain % 8)))
        vga_gain = max(0, min(62, gain))
        sample_rate = int(max(2_000_000, min(self.max_sample_rate, round(self.sample_rate))))

        return {
            "device_id": self.device_id,
            "center_freq_hz": int(round(self.frequency)),
            "sample_rate_sps": sample_rate,
            "lna_gain_db": lna_gain,
            "vga_gain_db": vga_gain,
            "amp_enable": True,
            "baseband_filter_hz": int(round(min(sample_rate, max(1_750_000, self.bandwidth)))),
        }

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
        self.stream_id = r.json()["stream_id"]

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
        time.sleep(0.05)
        self.start()

    def _rx_loop(self) -> None:
        assert self._ws is not None
        while self._running and self._should_run:
            try:
                frame = self._ws.recv()
                if not isinstance(frame, (bytes, bytearray)):
                    continue
                iq_i8 = np.frombuffer(frame, dtype=np.int8)
                if iq_i8.size < 2:
                    continue

                i = iq_i8[0::2].astype(np.float32)
                q = iq_i8[1::2].astype(np.float32)
                iq = (i + 1j * q) / 128.0

                if iq.size >= self.size:
                    out = iq[: self.size]
                else:
                    out = np.zeros(self.size, dtype=np.complex64)
                    out[: iq.size] = iq

                with self._lock:
                    self._latest_samples = out.astype(np.complex64, copy=False)
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
