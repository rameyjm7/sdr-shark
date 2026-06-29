from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

try:
    from websocket import create_connection
except Exception:
    create_connection = None


BT_LOW_HZ = 2_402_000_000
BT_HIGH_HZ = 2_480_000_000
DEFAULT_LOG_DIR = Path("/var/log/sdr-shark")


def _log_dir_from_env() -> Path:
    configured = os.getenv("SDR_SHARK_BLUETOOTH_LOG_DIR")
    candidates = [Path(configured).expanduser()] if configured else [DEFAULT_LOG_DIR]
    candidates.append(Path.home() / ".sdr-shark" / "logs")
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.touch(exist_ok=True)
            probe.unlink(missing_ok=True)
            return candidate
        except Exception:
            continue
    return candidates[-1]


class BluetoothGatewayPlugin:
    def __init__(self) -> None:
        self.enabled = str(os.getenv("SDR_SHARK_BLUETOOTH_PLUGIN", "1")).strip().lower() not in {"0", "false", "no"}
        self.rf_sentinel_root = Path(
            os.getenv("RF_SENTINEL_ROOT", "/home/jake/workspace/SDR/RF_Sentinel")
        ).expanduser()
        self._events: deque[dict[str, Any]] = deque(maxlen=int(os.getenv("SDR_SHARK_BLUETOOTH_EVENT_LIMIT", "200")))
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active_key: tuple[Any, ...] | None = None
        self._last_error = ""
        self._last_start_attempt = 0.0
        self._event_log_path = self._init_event_log()

    def update(self, sdr: Any) -> None:
        if not self.enabled:
            self.stop()
            return
        info = self._stream_info(sdr)
        if not self._should_decode(info):
            self.stop()
            return

        key = (
            info.get("backend"),
            info.get("source"),
            info.get("device_id"),
            info.get("stream_id"),
            info.get("iq_format"),
            int(info.get("center_freq_hz") or 0),
            int(info.get("sample_rate_sps") or 0),
        )
        if self._thread is not None and self._thread.is_alive() and key == self._active_key:
            return

        # Avoid a hard restart loop if the optional RF Sentinel pieces are missing.
        now = time.time()
        if now - self._last_start_attempt < 5.0:
            return
        self._last_start_attempt = now
        self.stop()
        self._stop = threading.Event()
        self._active_key = key
        self._thread = threading.Thread(target=self._run, args=(sdr, dict(info), self._stop), daemon=True)
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
        return {
            "enabled": self.enabled,
            "active": bool(self._thread is not None and self._thread.is_alive()),
            "event_count": len(events),
            "events": events,
            "last_error": self._last_error,
        }

    def _stream_info(self, sdr: Any) -> dict[str, Any]:
        if getattr(sdr, "backend", "") == "soapy" and hasattr(sdr, "iq_tap_info"):
            return dict(sdr.iq_tap_info())
        if hasattr(sdr, "gateway_stream_info"):
            return dict(sdr.gateway_stream_info())
        return {}

    def _should_decode(self, info: dict[str, Any]) -> bool:
        backend = info.get("backend")
        if backend not in {"gateway", "soapy"}:
            return False
        if backend == "gateway" and not info.get("stream_id"):
            return False
        center = int(info.get("center_freq_hz") or 0)
        rate = int(info.get("sample_rate_sps") or 0)
        if center <= 0 or rate <= 0:
            return False
        low = center - (rate // 2)
        high = center + (rate // 2)
        return high >= BT_LOW_HZ and low <= BT_HIGH_HZ

    def _run(self, sdr: Any, info: dict[str, Any], stop: threading.Event) -> None:
        if info.get("source") == "soapy_tap":
            self._run_soapy_tap(sdr, info, stop)
            return
        self._run_gateway_stream(info, stop)

    def _run_soapy_tap(self, sdr: Any, info: dict[str, Any], stop: threading.Event) -> None:
        if not hasattr(sdr, "subscribe_iq_tap"):
            self._set_error("Soapy IQ tap is not available")
            return

        fifo_path = self._make_fifo_path()
        btc_proc = self._start_btc_decoder(info, input_fifo=fifo_path)
        btc_writer = self._open_fifo_writer(fifo_path, btc_proc)
        btc_reader = None
        if btc_proc is not None:
            btc_reader = threading.Thread(target=self._read_btc_events, args=(btc_proc, stop), daemon=True)
            btc_reader.start()

        detector = self._build_ble_detector(info)
        subscriber_id = ""
        try:
            subscriber_id, chunks = sdr.subscribe_iq_tap(max_chunks=64)
            self._set_error("")
            while not stop.is_set():
                try:
                    cs8 = chunks.get(timeout=0.5)
                except Exception:
                    continue
                if not cs8:
                    break
                self._process_cs8_chunk(cs8, info, btc_writer, detector)
        except Exception as exc:
            if not stop.is_set():
                self._set_error(str(exc))
        finally:
            stop.set()
            if subscriber_id and hasattr(sdr, "release_iq_tap"):
                try:
                    sdr.release_iq_tap(subscriber_id)
                except Exception:
                    pass
            if btc_proc is not None and btc_proc.poll() is None:
                btc_proc.terminate()
            if btc_writer is not None:
                try:
                    btc_writer.close()
                except Exception:
                    pass
            try:
                fifo_path.unlink(missing_ok=True)
            except Exception:
                pass
            if btc_reader is not None:
                btc_reader.join(timeout=1.0)

    def _run_gateway_stream(self, info: dict[str, Any], stop: threading.Event) -> None:
        if create_connection is None:
            self._set_error("websocket-client is not available")
            return

        fifo_path = self._make_fifo_path()
        btc_proc = self._start_btc_decoder(info, input_fifo=fifo_path)
        btc_writer = self._open_fifo_writer(fifo_path, btc_proc)
        btc_reader = None
        if btc_proc is not None:
            btc_reader = threading.Thread(target=self._read_btc_events, args=(btc_proc, stop), daemon=True)
            btc_reader.start()

        detector = self._build_ble_detector(info)
        ws = None
        try:
            ws_headers = [f"Authorization: Bearer {info['token']}"] if info.get("token") else None
            ws = create_connection(
                f"{info['ws_base']}/ws/iq/{info['stream_id']}?keep=1",
                header=ws_headers,
                timeout=5,
                enable_multithread=True,
            )
            self._set_error("")
            while not stop.is_set():
                frame = ws.recv()
                if not isinstance(frame, (bytes, bytearray)):
                    continue
                cs8 = self._frame_to_cs8(frame, str(info.get("iq_format") or "i8"))
                if not cs8:
                    continue
                self._process_cs8_chunk(cs8, info, btc_writer, detector)
        except Exception as exc:
            if not stop.is_set():
                self._set_error(str(exc))
        finally:
            stop.set()
            if ws is not None:
                try:
                    ws.close()
                except Exception:
                    pass
            if btc_proc is not None and btc_proc.poll() is None:
                btc_proc.terminate()
            if btc_writer is not None:
                try:
                    btc_writer.close()
                except Exception:
                    pass
            try:
                fifo_path.unlink(missing_ok=True)
            except Exception:
                pass
            if btc_reader is not None:
                btc_reader.join(timeout=1.0)

    def _process_cs8_chunk(self, cs8: bytes, info: dict[str, Any], btc_writer: Any | None, detector: Any | None) -> None:
        if btc_writer is not None:
            try:
                btc_writer.write(cs8)
                btc_writer.flush()
            except Exception:
                pass
        if detector is not None:
            try:
                _, ble_events = detector.process_iq_i8(cs8)
                for event in ble_events:
                    if event.get("kind") not in {"ble_adv", "ble_burst"}:
                        continue
                    item = dict(event)
                    item.setdefault("protocol", "ble")
                    item.setdefault("source", "rf_sentinel")
                    item.setdefault("device_id", info.get("device_id", ""))
                    self._append_event(item)
            except Exception as exc:
                self._set_error(f"BLE decoder error: {exc}")

    def _build_ble_detector(self, info: dict[str, Any]) -> Any | None:
        src = self.rf_sentinel_root / "rf_platform" / "plugins" / "bluetooth-lowenergy" / "src"
        if src.exists() and str(src) not in sys.path:
            sys.path.insert(0, str(src))
        try:
            from bluetooth_lowenergy.detector import WideBLEAdvertisingDetector
        except Exception as exc:
            self._set_error(f"BLE plugin unavailable: {exc}")
            return None
        return WideBLEAdvertisingDetector(
            sample_rate_sps=int(info.get("sample_rate_sps") or 20_000_000),
            center_freq_hz=int(info.get("center_freq_hz") or 2_442_000_000),
        )

    def _start_btc_decoder(self, info: dict[str, Any], input_fifo: Path | None = None) -> subprocess.Popen[bytes] | None:
        binary = Path(os.getenv("SDR_SHARK_BTC_SNIFFER", "")).expanduser()
        if not str(binary) or str(binary) == ".":
            binary = self.rf_sentinel_root / "rf_platform" / "plugins" / "bluetooth-classic" / "build" / "btcexplorer-sniffer-gateway"
        if not binary.exists():
            fallback = shutil.which("btcexplorer-sniffer-gateway")
            if fallback:
                binary = Path(fallback)
        if not binary.exists():
            self._set_error(f"BTC sniffer unavailable: {binary}")
            return None

        center_mhz = float(info.get("center_freq_hz") or 2_442_000_000) / 1e6
        bandwidth_mhz = max(1, int(round(float(info.get("sample_rate_sps") or 20_000_000) / 1e6)))
        driver = str(info.get("device_id") or "gateway").split(":", 1)[0] or "gateway"
        log_dir = _log_dir_from_env()
        stdbuf = shutil.which("stdbuf")
        cmd = ([stdbuf, "-oL", "-eL"] if stdbuf else []) + [
            str(binary),
            "--driver",
            driver,
            "--freq-mhz",
            f"{center_mhz:.3f}MHz",
            "--bandwidth-mhz",
            f"{bandwidth_mhz}MHz",
            "--seconds",
            os.getenv("SDR_SHARK_BTC_SECONDS", "0.25"),
            "--log",
            str(log_dir / "btcexplorer-sniffer.log"),
            "--jsonl-stdout",
        ]
        if input_fifo is not None:
            cmd.extend(["--input-fifo", str(input_fifo), "--input-format", "cs8"])
        else:
            cmd.extend(["--input-stdin", "--input-format", "cs8"])
        try:
            return subprocess.Popen(
                cmd,
                cwd=str(binary.parent.parent if binary.parent.name == "build" else self.rf_sentinel_root),
                stdin=None if input_fifo is not None else subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=0,
            )
        except Exception as exc:
            self._set_error(f"BTC decoder start failed: {exc}")
            return None

    def _make_fifo_path(self) -> Path:
        fifo_dir = Path(os.getenv("SDR_SHARK_BLUETOOTH_FIFO_DIR", "/tmp")).expanduser()
        fifo_dir.mkdir(parents=True, exist_ok=True)
        fifo_path = fifo_dir / f"sdr-shark-bt-{os.getpid()}-{time.time_ns()}.cs8"
        os.mkfifo(fifo_path, 0o600)
        return fifo_path

    def _open_fifo_writer(self, fifo_path: Path, proc: subprocess.Popen[bytes] | None):
        if proc is None:
            try:
                fifo_path.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if proc.poll() is not None:
                return None
            try:
                fd = os.open(fifo_path, os.O_WRONLY | os.O_NONBLOCK)
                return os.fdopen(fd, "wb", buffering=0)
            except OSError:
                time.sleep(0.05)
        self._set_error(f"BTC decoder did not open FIFO: {fifo_path}")
        return None

    def _read_btc_events(self, proc: subprocess.Popen[bytes], stop: threading.Event) -> None:
        if proc.stdout is None:
            return
        for raw_line in proc.stdout:
            if stop.is_set():
                break
            text = raw_line.decode("utf-8", errors="replace").strip()
            if not text or not text.startswith("{"):
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if str(event.get("type") or "") in {"metrics", "config", "debug_bin_energy"}:
                continue
            event.setdefault("protocol", "btc")
            event.setdefault("source", "rf_sentinel")
            self._append_event(event)

    def _frame_to_cs8(self, frame: bytes | bytearray, iq_format: str) -> bytes:
        if iq_format == "cs16":
            raw = np.frombuffer(frame, dtype=np.int16)
            if raw.size < 2:
                return b""
            divisor = float(os.getenv("SDR_SHARK_BT_CS16_TO_I8_DIVISOR", "64") or "64")
            return np.clip(np.rint(raw.astype(np.float32) / divisor), -128, 127).astype(np.int8).tobytes()
        if iq_format == "cf32":
            raw = np.frombuffer(frame, dtype=np.complex64)
            if raw.size < 1:
                return b""
            interleaved = np.empty(raw.size * 2, dtype=np.float32)
            interleaved[0::2] = raw.real
            interleaved[1::2] = raw.imag
            return np.clip(np.rint(interleaved * 127.0), -128, 127).astype(np.int8).tobytes()
        raw = bytes(frame)
        return raw[:-1] if len(raw) % 2 else raw

    def _append_event(self, event: dict[str, Any]) -> None:
        item = dict(event)
        item.setdefault("seen_at", time.time())
        with self._lock:
            self._events.append(item)
        self._write_event_log(item)

    def _set_error(self, message: str) -> None:
        self._last_error = str(message or "")

    def _init_event_log(self) -> Path | None:
        log_dir = _log_dir_from_env()
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            path = log_dir / f"bluetooth-events-{os.getpid()}.jsonl"
            current = log_dir / "bluetooth-events-current.jsonl"
            with open(path, "a", encoding="utf-8") as log:
                log.write(json.dumps({
                    "type": "session_start",
                    "pid": os.getpid(),
                    "seen_at": time.time(),
                }, sort_keys=True) + "\n")
            try:
                current.unlink(missing_ok=True)
                current.symlink_to(path.name)
            except Exception:
                # Symlinks may be unavailable on some filesystems; keep a normal current file.
                with open(current, "a", encoding="utf-8") as log:
                    log.write(json.dumps({
                        "type": "session_start",
                        "pid": os.getpid(),
                        "seen_at": time.time(),
                    }, sort_keys=True) + "\n")
                return current
            return path
        except Exception:
            return None

    def _write_event_log(self, event: dict[str, Any]) -> None:
        if self._event_log_path is None:
            return
        try:
            with open(self._event_log_path, "a", encoding="utf-8") as log:
                log.write(json.dumps(event, sort_keys=True, default=str) + "\n")
        except Exception:
            pass
