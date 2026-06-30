from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np


def _default_session_root() -> Path:
    return Path(os.getenv("SDR_SHARK_IQ_SESSION_DIR", str(Path.home() / ".sdr-shark" / "iq-sessions"))).expanduser()


def _safe_slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-")
    return text[:80] or "iq-session"


def _cs8_to_complex(raw: bytes) -> np.ndarray:
    values = np.frombuffer(raw, dtype=np.int8)
    if values.size < 2:
        return np.empty(0, dtype=np.complex64)
    if values.size % 2:
        values = values[:-1]
    i = values[0::2].astype(np.float32)
    q = values[1::2].astype(np.float32)
    return ((i + 1j * q) / 128.0).astype(np.complex64, copy=False)


class IQSessionRecorder:
    """Record raw cs8 IQ tap chunks with enough metadata for deterministic replay."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _default_session_root()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active: dict[str, Any] | None = None
        self._last_error = ""

    def start(self, sdr: Any, *, label: str = "", max_seconds: float = 0.0, max_mb: float = 0.0) -> dict[str, Any]:
        if not hasattr(sdr, "subscribe_iq_tap"):
            raise RuntimeError("Current SDR does not expose an IQ tap")
        self.stop()
        info = self._stream_info(sdr)
        started = datetime.utcnow()
        session_id = f"{started.strftime('%Y%m%d-%H%M%S')}-{_safe_slug(label or info.get('device_id') or 'capture')}"
        session_dir = self.root / session_id
        session_dir.mkdir(parents=True, exist_ok=False)
        capture_path = session_dir / "chunks.cs8"
        index_path = session_dir / "chunks.jsonl"
        metadata_path = session_dir / "metadata.json"
        metadata = {
            "id": session_id,
            "label": label,
            "created_at": started.isoformat(timespec="milliseconds") + "Z",
            "format": "cs8",
            "sample_bytes": 2,
            "capture_file": capture_path.name,
            "index_file": index_path.name,
            "stream": info,
            "max_seconds": float(max_seconds or 0.0),
            "max_mb": float(max_mb or 0.0),
            "status": "recording",
            "chunks": 0,
            "bytes": 0,
            "duration_seconds": 0.0,
        }
        metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

        self._stop = threading.Event()
        with self._lock:
            self._active = {
                "id": session_id,
                "path": str(session_dir),
                "metadata_path": str(metadata_path),
                "started_monotonic": time.monotonic(),
                "chunks": 0,
                "bytes": 0,
                "status": "recording",
            }
        self._thread = threading.Thread(
            target=self._record_loop,
            args=(sdr, metadata, capture_path, index_path, metadata_path, self._stop),
            daemon=True,
        )
        self._thread.start()
        return self.status()

    def stop(self) -> dict[str, Any]:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=2.0)
        self._thread = None
        with self._lock:
            if self._active is not None:
                self._active["status"] = "stopped"
        return self.status()

    def status(self) -> dict[str, Any]:
        with self._lock:
            active = dict(self._active or {})
        active.setdefault("active", bool(self._thread is not None and self._thread.is_alive()))
        active.setdefault("last_error", self._last_error)
        return active

    def list_sessions(self) -> list[dict[str, Any]]:
        self.root.mkdir(parents=True, exist_ok=True)
        sessions: list[dict[str, Any]] = []
        for metadata_path in sorted(self.root.glob("*/metadata.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            session_dir = metadata_path.parent
            capture_path = session_dir / str(metadata.get("capture_file") or "chunks.cs8")
            metadata["path"] = str(session_dir)
            metadata["bytes"] = int(metadata.get("bytes") or (capture_path.stat().st_size if capture_path.exists() else 0))
            sessions.append(metadata)
        return sessions

    def _record_loop(
        self,
        sdr: Any,
        metadata: dict[str, Any],
        capture_path: Path,
        index_path: Path,
        metadata_path: Path,
        stop: threading.Event,
    ) -> None:
        subscriber_id = ""
        old_tap_interval = getattr(sdr, "_iq_tap_interval_s", None)
        started_monotonic = time.monotonic()
        byte_count = 0
        chunk_count = 0
        max_seconds = max(0.0, float(metadata.get("max_seconds") or 0.0))
        max_bytes = max(0, int(float(metadata.get("max_mb") or 0.0) * 1024 * 1024))
        try:
            if old_tap_interval is not None:
                setattr(sdr, "_iq_tap_interval_s", 0.0)
            subscriber_id, chunks = sdr.subscribe_iq_tap(max_chunks=512)
            with capture_path.open("ab") as capture, index_path.open("a", encoding="utf-8") as index:
                while not stop.is_set():
                    if max_seconds and (time.monotonic() - started_monotonic) >= max_seconds:
                        break
                    if max_bytes and byte_count >= max_bytes:
                        break
                    try:
                        raw = chunks.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if not raw:
                        break
                    offset = byte_count
                    capture.write(raw)
                    byte_count += len(raw)
                    chunk_count += 1
                    index.write(json.dumps({
                        "chunk": chunk_count,
                        "offset": offset,
                        "bytes": len(raw),
                        "timestamp_offset_seconds": round(time.monotonic() - started_monotonic, 6),
                    }, separators=(",", ":")) + "\n")
                    if chunk_count % 16 == 0:
                        capture.flush()
                        index.flush()
                    with self._lock:
                        if self._active is not None:
                            self._active["chunks"] = chunk_count
                            self._active["bytes"] = byte_count
        except Exception as exc:
            self._last_error = str(exc)
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
            metadata["status"] = "complete"
            metadata["chunks"] = chunk_count
            metadata["bytes"] = byte_count
            metadata["duration_seconds"] = round(time.monotonic() - started_monotonic, 6)
            if self._last_error:
                metadata["last_error"] = self._last_error
            metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
            with self._lock:
                if self._active is not None and self._active.get("id") == metadata.get("id"):
                    self._active.update({
                        "status": "complete",
                        "chunks": chunk_count,
                        "bytes": byte_count,
                        "active": False,
                    })

    @staticmethod
    def _stream_info(sdr: Any) -> dict[str, Any]:
        if hasattr(sdr, "iq_tap_info"):
            return dict(sdr.iq_tap_info())
        if hasattr(sdr, "gateway_stream_info"):
            return dict(sdr.gateway_stream_info())
        return {}


class IQReplaySDR:
    """SDR-like replay source backed by a recorded IQ session."""

    backend = "replay"

    def __init__(self, session_dir: Path, *, loop: bool = True, speed: float = 1.0, size: int = 8192) -> None:
        self.session_dir = Path(session_dir)
        metadata_path = self.session_dir / "metadata.json"
        self.metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        stream = dict(self.metadata.get("stream") or {})
        self.name = "replay"
        self.device_id = f"replay:{self.metadata.get('id') or self.session_dir.name}"
        self.stream_id = str(self.metadata.get("id") or self.session_dir.name)
        self.frequency = float(stream.get("center_freq_hz") or 0)
        self.sample_rate = float(stream.get("sample_rate_sps") or 0)
        self.bandwidth = float(stream.get("bandwidth_hz") or self.sample_rate)
        self.gain = float(stream.get("gain_db") or 0)
        self.size = int(size)
        self.loop = bool(loop)
        self.speed = max(0.01, float(speed or 1.0))
        self._capture_path = self.session_dir / str(self.metadata.get("capture_file") or "chunks.cs8")
        self._index_path = self.session_dir / str(self.metadata.get("index_file") or "chunks.jsonl")
        self._latest_samples = np.zeros(self.size, dtype=np.complex64)
        self._lock = threading.Lock()
        self._iq_tap_lock = threading.Lock()
        self._iq_tap_subscribers: dict[str, queue.Queue[bytes | None]] = {}
        self._thread: threading.Thread | None = None
        self._running = False
        self._should_run = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._should_run = True
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._should_run = False
        self._running = False
        self._close_iq_taps()
        if self._thread is not None and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None

    def get_latest_samples(self) -> np.ndarray:
        with self._lock:
            return self._latest_samples.copy()

    def configure_receiver(self, **_: Any) -> None:
        return

    def set_frequency(self, frequency: float) -> None:
        self.frequency = float(frequency)

    def set_sample_rate(self, sample_rate: float) -> None:
        self.sample_rate = float(sample_rate)

    def set_bandwidth(self, bandwidth: float) -> None:
        self.bandwidth = float(bandwidth)

    def set_gain(self, gain: float) -> None:
        self.gain = float(gain)

    def iq_tap_info(self) -> dict[str, Any]:
        return {
            "backend": "replay",
            "source": "iq_replay",
            "device_id": self.device_id,
            "stream_id": self.stream_id,
            "iq_format": "i8",
            "center_freq_hz": int(round(self.frequency)),
            "sample_rate_sps": int(round(self.sample_rate)),
            "bandwidth_hz": int(round(self.bandwidth)),
            "gain_db": float(self.gain),
            "session_path": str(self.session_dir),
        }

    def gateway_stream_info(self) -> dict[str, Any]:
        return self.iq_tap_info()

    def list_devices(self) -> list[dict[str, Any]]:
        return [{
            "id": self.device_id,
            "driver": "replay",
            "label": f"IQ replay {self.metadata.get('id') or self.session_dir.name}",
            "max_sample_rate_sps": int(round(self.sample_rate)),
        }]

    def select_device(self, selector: str) -> bool:
        return str(selector) == self.device_id

    def subscribe_iq_tap(self, max_chunks: int = 32):
        subscriber_id = f"replay-tap-{time.time_ns()}"
        chunks: queue.Queue[bytes | None] = queue.Queue(maxsize=max(1, int(max_chunks)))
        with self._iq_tap_lock:
            self._iq_tap_subscribers[subscriber_id] = chunks
        return subscriber_id, chunks

    def release_iq_tap(self, subscriber_id: str) -> None:
        with self._iq_tap_lock:
            self._iq_tap_subscribers.pop(subscriber_id, None)

    def _run(self) -> None:
        while self._should_run:
            any_chunk = False
            for raw in self._iter_chunks():
                if not self._should_run:
                    break
                any_chunk = True
                iq = _cs8_to_complex(raw)
                if iq.size >= self.size:
                    out = iq[: self.size]
                else:
                    out = np.zeros(self.size, dtype=np.complex64)
                    out[: iq.size] = iq
                with self._lock:
                    self._latest_samples = out.astype(np.complex64, copy=False)
                self._publish_iq_tap(raw)
                if self.sample_rate > 0:
                    time.sleep(max(0.0, (len(raw) // 2) / float(self.sample_rate) / self.speed))
            if not self.loop or not any_chunk:
                break
        self._running = False

    def _iter_chunks(self):
        if self._index_path.exists():
            with self._capture_path.open("rb") as capture, self._index_path.open("r", encoding="utf-8") as index:
                for line in index:
                    try:
                        entry = json.loads(line)
                    except Exception:
                        continue
                    capture.seek(int(entry.get("offset") or 0))
                    raw = capture.read(int(entry.get("bytes") or 0))
                    if raw:
                        yield raw
            return
        chunk_bytes = max(2, int(self.size) * 2)
        with self._capture_path.open("rb") as capture:
            while True:
                raw = capture.read(chunk_bytes)
                if not raw:
                    break
                yield raw

    def _publish_iq_tap(self, chunk: bytes) -> None:
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
