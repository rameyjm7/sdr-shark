from __future__ import annotations

import json
import os
import socket
import threading
import time
from typing import Any


class GpsdPlugin:
    """Small gpsd JSON watcher with cached status for the UI."""

    def __init__(self) -> None:
        self.enabled = str(os.getenv("SDR_SHARK_GPS_PLUGIN", "1")).strip().lower() not in {"0", "false", "no"}
        self.host = os.getenv("GPSD_HOST", "127.0.0.1")
        self.port = int(os.getenv("GPSD_PORT", "2948"))
        self.reconnect_sec = float(os.getenv("GPSD_RECONNECT_SEC", "3"))
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._status: dict[str, Any] = {
            "enabled": self.enabled,
            "connected": False,
            "mode": 0,
            "lock": "NO",
            "status": "idle" if self.enabled else "disabled",
            "sats_seen": None,
            "sats_used": None,
            "lat": None,
            "lon": None,
            "alt": None,
            "speed": None,
            "time": None,
            "device_paths": [],
            "last_update": None,
            "error": None,
        }
        if self.enabled:
            self.start()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="gpsd-plugin", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._status)

    def _update(self, **patch: Any) -> None:
        with self._lock:
            self._status.update(patch)
            self._status["enabled"] = self.enabled

    @staticmethod
    def _lock_label(mode: int) -> str:
        if mode >= 3:
            return "3D"
        if mode == 2:
            return "2D"
        if mode == 1:
            return "1D"
        return "NO"

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._watch_once()
            except Exception as exc:
                self._update(
                    connected=False,
                    status="error",
                    error=str(exc),
                    last_update=time.time(),
                )
                self._stop.wait(self.reconnect_sec)

    def _watch_once(self) -> None:
        with socket.create_connection((self.host, self.port), timeout=5) as sock:
            sock.settimeout(2)
            sock.sendall(b'?WATCH={"enable":true,"json":true};\n')
            self._update(connected=True, status="connected", error=None, last_update=time.time())
            buffer = ""
            while not self._stop.is_set():
                try:
                    chunk = sock.recv(4096)
                except (TimeoutError, socket.timeout):
                    continue
                if not chunk:
                    raise ConnectionError("gpsd connection closed")

                buffer += chunk.decode("utf-8", errors="replace")
                if "\n" not in buffer:
                    continue

                lines = buffer.splitlines(keepends=True)
                buffer = ""
                if lines and not lines[-1].endswith("\n"):
                    buffer = lines.pop()

                for line in lines:
                    try:
                        msg = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._handle_message(msg)

    def _handle_message(self, msg: dict[str, Any]) -> None:
        cls = msg.get("class")
        if cls == "DEVICES":
            devices = msg.get("devices", [])
            self._update(
                device_paths=[d.get("path", "?") for d in devices if isinstance(d, dict)],
                last_update=time.time(),
            )
        elif cls == "SKY":
            sats = msg.get("satellites", [])
            if not isinstance(sats, list):
                sats = []
            self._update(
                sats_seen=len(sats),
                sats_used=sum(1 for sat in sats if isinstance(sat, dict) and sat.get("used")),
                last_update=time.time(),
            )
        elif cls == "TPV":
            mode = int(msg.get("mode", 0) or 0)
            lock = self._lock_label(mode)
            self._update(
                connected=True,
                mode=mode,
                lock=lock,
                status="fix" if mode >= 2 else "no-fix-yet",
                lat=msg.get("lat"),
                lon=msg.get("lon"),
                alt=msg.get("alt"),
                speed=msg.get("speed"),
                time=msg.get("time"),
                last_update=time.time(),
                error=None,
            )
