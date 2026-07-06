import json
import os
import shutil
import subprocess
import threading
import time
from collections import deque


class Rtl433Plugin:
    """Run rtl_433 for narrow sub-GHz scan stops and normalize JSON events."""

    def __init__(self):
        self.enabled = str(os.getenv("SDR_SHARK_RTL433_PLUGIN", "1")).strip().lower() not in {"0", "false", "no", "off"}
        self.binary = os.getenv("SDR_SHARK_RTL433_BIN", "rtl_433")
        self.sample_rate = os.getenv("SDR_SHARK_RTL433_SAMPLE_RATE", "1024k")
        self.extra_args = [arg for arg in os.getenv("SDR_SHARK_RTL433_ARGS", "").split() if arg]
        self.events = deque(maxlen=500)
        self._devices = {}
        self._lock = threading.Lock()
        self._thread = None
        self._proc = None
        self._stop_event = threading.Event()
        self._active_key = None
        self._message_count = 0
        self._last_error = ""
        self._last_started_at = 0.0

    def _available(self):
        return bool(shutil.which(self.binary))

    def _frequency_arg(self, frequency_hz):
        return f"{float(frequency_hz) / 1e6:.6f}M"

    def _identity_for(self, payload):
        for key in ("id", "device", "model", "type"):
            value = payload.get(key)
            if value not in (None, ""):
                return str(value)
        return ""

    def _normalize_event(self, payload, frequency_hz):
        identity = self._identity_for(payload)
        model = str(payload.get("model") or payload.get("type") or "rtl_433 device")
        event = {
            "protocol": "rtl433",
            "kind": "rtl433_event",
            "seen_at": time.time(),
            "frequency_hz": float(frequency_hz),
            "frequency_mhz": float(frequency_hz) / 1e6,
            "identity": identity or model,
            "model": model,
            "detail": model,
            "raw": payload,
        }
        for src, dst in (
            ("id", "device_id"),
            ("channel", "channel"),
            ("battery_ok", "battery_ok"),
            ("temperature_C", "temperature_c"),
            ("humidity", "humidity"),
            ("rssi", "rssi_dbfs"),
            ("snr", "snr_db"),
        ):
            if src in payload:
                event[dst] = payload[src]
        if payload.get("time"):
            event["device_time"] = payload.get("time")
        return event

    def _run(self, frequency_hz):
        cmd = [
            self.binary,
            "-F",
            "json",
            "-f",
            self._frequency_arg(frequency_hz),
            "-s",
            str(self.sample_rate),
            *self.extra_args,
        ]
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
            for line in self._proc.stdout or []:
                if self._stop_event.is_set():
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                event = self._normalize_event(payload, frequency_hz)
                with self._lock:
                    self._message_count += 1
                    self.events.append(event)
                    identity = event.get("identity") or event.get("device_id")
                    if identity:
                        self._devices[str(identity)] = event
                    self._last_error = ""
        except Exception as exc:
            with self._lock:
                self._last_error = str(exc)
        finally:
            proc = self._proc
            self._proc = None
            if proc is not None and proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=1.0)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def update(self, step, worker_manager=None):
        if not self.enabled:
            self.stop()
            return
        if not self._available():
            with self._lock:
                self._last_error = f"{self.binary} not found"
            self.stop()
            return
        frequency_hz = float(step.get("applied_center_hz") or step.get("center_hz") or 0.0)
        if frequency_hz <= 0:
            self.stop()
            return
        key = round(frequency_hz)
        if self._thread is not None and self._thread.is_alive() and self._active_key == key:
            return
        self.stop()
        if worker_manager is not None and hasattr(worker_manager, "suspend_worker_sdr"):
            worker_manager.suspend_worker_sdr("rtl_433 owns RTL-SDR for sub-GHz decode")
        self._stop_event.clear()
        self._active_key = key
        self._last_started_at = time.time()
        self._thread = threading.Thread(target=self._run, args=(frequency_hz,), daemon=True)
        self._thread.start()

    def stop(self):
        self._active_key = None
        self._stop_event.set()
        proc = self._proc
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=0.5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.5)
        self._thread = None
        self._proc = None

    def snapshot(self, max_events=20):
        with self._lock:
            rows = list(self.events)[-max_events:]
            return {
                "enabled": bool(self.enabled),
                "active": bool(self._thread is not None and self._thread.is_alive()),
                "frequency_hz": float(self._active_key or 0.0),
                "event_count": int(self._message_count),
                "device_count": len(self._devices),
                "events": rows,
                "last_error": self._last_error,
                "started_at": self._last_started_at,
            }
