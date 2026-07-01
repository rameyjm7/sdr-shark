from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


ADSB_CENTER_HZ = 1_090_000_000
ADSB_GUARD_HZ = 1_200_000


class AdsbGatewayPlugin:
    """Run the vendored adsb-rx decoder from SDR-Shark's shared IQ tap."""

    def __init__(self) -> None:
        self.enabled = str(os.getenv("SDR_SHARK_ADSB_PLUGIN", "1")).strip().lower() not in {"0", "false", "no"}
        self.plugin_root = Path(__file__).resolve().parent / "plugins" / "adsb_rx" / "adsb-rx"
        self.binary = Path(os.getenv("SDR_SHARK_ADSB_RX_BIN", self.plugin_root / "target" / "release" / "adsb-rx")).expanduser()
        self._events: deque[dict[str, Any]] = deque(maxlen=int(os.getenv("SDR_SHARK_ADSB_EVENT_LIMIT", "200")))
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active_key: tuple[Any, ...] | None = None
        self._last_error = ""
        self._last_start_attempt = 0.0
        self._message_count = 0
        self._chunk_count = 0
        self._byte_count = 0
        self._decoder_alive = False

    def update(self, sdr: Any) -> None:
        if not self.enabled:
            self.stop()
            return
        info = self._stream_info(sdr)
        if not self._should_decode(info) or not hasattr(sdr, "subscribe_iq_tap"):
            self.stop()
            return

        key = (
            info.get("backend"),
            info.get("source"),
            info.get("device_id"),
            info.get("stream_id"),
            int(info.get("center_freq_hz") or 0),
            int(info.get("sample_rate_sps") or 0),
        )
        if self._thread is not None and self._thread.is_alive() and key == self._active_key:
            return

        now = time.time()
        if now - self._last_start_attempt < 5.0:
            return
        self._last_start_attempt = now
        self.stop()
        self._stop = threading.Event()
        self._active_key = key
        self._chunk_count = 0
        self._byte_count = 0
        self._decoder_alive = False
        self._thread = threading.Thread(target=self._run_iq_tap, args=(sdr, dict(info), self._stop), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._active_key = None

    def snapshot(self, max_events: int = 50) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)[-max(1, int(max_events)):]
        aircraft = sorted({str(event.get("icao") or "") for event in events if event.get("icao")})
        return {
            "enabled": self.enabled,
            "active": bool(self._thread is not None and self._thread.is_alive()),
            "event_count": len(events),
            "message_count": int(self._message_count),
            "chunk_count": int(self._chunk_count),
            "byte_count": int(self._byte_count),
            "decoder_alive": bool(self._decoder_alive),
            "aircraft_count": len(aircraft),
            "aircraft": aircraft,
            "events": events,
            "binary": str(self.binary),
            "last_error": self._last_error,
        }

    def _stream_info(self, sdr: Any) -> dict[str, Any]:
        if hasattr(sdr, "iq_tap_info"):
            info = dict(sdr.iq_tap_info())
            if hasattr(sdr, "gateway_stream_info"):
                gateway_info = dict(sdr.gateway_stream_info())
                info.setdefault("stream_id", gateway_info.get("stream_id", ""))
            info.setdefault("source", "iq_tap")
            return info
        if hasattr(sdr, "gateway_stream_info"):
            return dict(sdr.gateway_stream_info())
        return {}

    def _should_decode(self, info: dict[str, Any]) -> bool:
        backend = info.get("backend")
        if backend not in {"gateway", "soapy", "replay"}:
            return False
        center = int(info.get("center_freq_hz") or 0)
        rate = int(info.get("sample_rate_sps") or 0)
        if center <= 0 or rate < 1_800_000:
            return False
        low = center - (rate // 2)
        high = center + (rate // 2)
        return low <= (ADSB_CENTER_HZ + ADSB_GUARD_HZ) and high >= (ADSB_CENTER_HZ - ADSB_GUARD_HZ)

    def _run_iq_tap(self, sdr: Any, info: dict[str, Any], stop: threading.Event) -> None:
        if not self._ensure_binary():
            return
        proc: subprocess.Popen[bytes] | None = None
        subscriber_id = ""
        old_tap_interval = getattr(sdr, "_iq_tap_interval_s", None)
        try:
            if old_tap_interval is not None:
                setattr(sdr, "_iq_tap_interval_s", 0.0)
            cmd = [
                str(self.binary),
                "--ifile",
                "-",
                "--ifile-format",
                "cs8",
                "--json",
                "--min-messages",
                os.getenv("SDR_SHARK_ADSB_MIN_MESSAGES", "1"),
            ]
            proc = subprocess.Popen(
                cmd,
                cwd=str(self.plugin_root),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                bufsize=0,
            )
            reader = threading.Thread(target=self._read_stdout, args=(proc, dict(info), stop), daemon=True)
            reader.start()
            subscriber_id, chunks = sdr.subscribe_iq_tap(max_chunks=128)
            self._decoder_alive = True
            self._set_error("")
            while not stop.is_set() and proc.poll() is None:
                try:
                    cs8 = chunks.get(timeout=0.5)
                except Exception:
                    continue
                if not cs8:
                    break
                try:
                    self._chunk_count += 1
                    self._byte_count += len(cs8)
                    assert proc.stdin is not None
                    proc.stdin.write(cs8)
                    proc.stdin.flush()
                except Exception as exc:
                    self._set_error(f"ADS-B decoder stdin failed: {exc}")
                    break
        except Exception as exc:
            if not stop.is_set():
                self._set_error(str(exc))
        finally:
            if subscriber_id and hasattr(sdr, "release_iq_tap"):
                try:
                    sdr.release_iq_tap(subscriber_id)
                except Exception:
                    pass
            if old_tap_interval is not None:
                try:
                    setattr(sdr, "_iq_tap_interval_s", old_tap_interval)
                except Exception:
                    pass
            if proc is not None:
                exit_code = proc.poll()
                try:
                    if proc.stdin:
                        proc.stdin.close()
                except Exception:
                    pass
                if proc.poll() is None:
                    proc.terminate()
                    try:
                        proc.wait(timeout=1.0)
                    except Exception:
                        proc.kill()
                elif exit_code not in (None, 0) and not stop.is_set():
                    self._set_error(f"ADS-B decoder exited with code {exit_code}")
            self._decoder_alive = False

    def _read_stdout(self, proc: subprocess.Popen[bytes], info: dict[str, Any], stop: threading.Event) -> None:
        stdout = proc.stdout
        if stdout is None:
            return
        while not stop.is_set():
            line = stdout.readline()
            if not line:
                break
            try:
                event = json.loads(line.decode("utf-8", errors="replace"))
            except Exception:
                continue
            if isinstance(event, dict):
                self._append_event(event, info)

    def _append_event(self, event: dict[str, Any], info: dict[str, Any]) -> None:
        now = time.time()
        event.setdefault("kind", "adsb_aircraft")
        event["protocol"] = "adsb"
        event.setdefault("seen_at", now)
        event.setdefault("center_freq_hz", int(info.get("center_freq_hz") or 0))
        event.setdefault("sample_rate_sps", int(info.get("sample_rate_sps") or 0))
        event.setdefault("source", info.get("source") or "iq_tap")
        with self._lock:
            self._message_count += 1
            self._events.append(event)

    def _ensure_binary(self) -> bool:
        if self.binary.exists() and os.access(self.binary, os.X_OK):
            return True
        cargo = shutil.which("cargo")
        if not cargo:
            self._set_error("ADS-B decoder is not built and cargo is not installed")
            return False
        try:
            result = subprocess.run(
                [cargo, "build", "--release"],
                cwd=str(self.plugin_root),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
                check=False,
            )
            if result.returncode != 0:
                self._set_error((result.stderr or "cargo build failed").strip()[-300:])
                return False
            return self.binary.exists() and os.access(self.binary, os.X_OK)
        except Exception as exc:
            self._set_error(f"ADS-B decoder build failed: {exc}")
            return False

    def _set_error(self, error: str) -> None:
        self._last_error = str(error or "")
