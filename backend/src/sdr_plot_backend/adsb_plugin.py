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

import numpy as np


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
        self._normalize_iq = str(os.getenv("SDR_SHARK_ADSB_NORMALIZE", "1")).strip().lower() not in {"0", "false", "no"}
        self._target_peak = float(os.getenv("SDR_SHARK_ADSB_TARGET_PEAK", "72"))
        self._last_iq_stats: dict[str, Any] = {}
        self._mixer_phase = 0.0

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
        self._mixer_phase = 0.0
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
            "iq_stats": dict(self._last_iq_stats),
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
        max_rate = int(float(os.getenv("SDR_SHARK_ADSB_MAX_INPUT_RATE", "2500000") or "2500000"))
        if rate > max_rate:
            self._last_iq_stats = {
                "disabled_reason": f"ADS-B Python tap is capped at {max_rate} SPS input",
                "input_rate_sps": int(rate),
                "required": "Tune ADS-B with ~2 MHz sample rate or use a native channelizer",
            }
            self._set_error("ADS-B tap disabled: input sample rate is too high for Python channelizer")
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
            if str(os.getenv("SDR_SHARK_ADSB_AGGRESSIVE", "1")).strip().lower() not in {"0", "false", "no"}:
                cmd.append("--aggressive")
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
                    decoder_chunk = self._prepare_decoder_chunk(cs8, info)
                    if not decoder_chunk:
                        continue
                    proc.stdin.write(decoder_chunk)
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

    def _prepare_decoder_chunk(self, cs8: bytes, info: dict[str, Any]) -> bytes:
        if not self._normalize_iq or len(cs8) < 2048:
            return cs8
        sample_count = len(cs8) // 2
        if sample_count <= 0:
            return cs8
        arr = np.frombuffer(cs8[: sample_count * 2], dtype=np.int8)
        input_clip_pct = float(np.count_nonzero((arr <= -127) | (arr >= 126)) * 100.0 / max(1, arr.size))
        input_iq = arr.reshape(-1, 2).astype(np.float32, copy=False)
        iq_complex = (input_iq[:, 0] + 1j * input_iq[:, 1]).astype(np.complex64, copy=False)

        center_hz = float(info.get("center_freq_hz") or ADSB_CENTER_HZ)
        input_rate = float(info.get("sample_rate_sps") or 2_000_000)
        freq_offset_hz = float(ADSB_CENTER_HZ) - center_hz
        decim = max(1, int(round(input_rate / 2_000_000.0)))
        output_rate = input_rate / decim if decim else input_rate
        if abs(freq_offset_hz) > input_rate * 0.48:
            self._set_error("ADS-B center is too close to the edge of the current passband")
            return b""

        if abs(freq_offset_hz) > 1.0:
            n = np.arange(iq_complex.size, dtype=np.float32)
            step = float(2.0 * np.pi * freq_offset_hz / input_rate)
            phase = self._mixer_phase + step * n
            iq_complex = iq_complex * np.exp(-1j * phase).astype(np.complex64)
            self._mixer_phase = float((self._mixer_phase + step * iq_complex.size) % (2.0 * np.pi))

        if decim > 1:
            usable = (iq_complex.size // decim) * decim
            if usable < decim:
                return b""
            # Boxcar decimation is intentionally simple here: ADS-B is an
            # envelope/pulse decoder and benefits more from sane timing than
            # from feeding full-rate wideband samples into a 2 MSPS detector.
            iq_complex = iq_complex[:usable].reshape(-1, decim).mean(axis=1).astype(np.complex64, copy=False)

        iq = np.empty((iq_complex.size, 2), dtype=np.float32)
        iq[:, 0] = iq_complex.real
        iq[:, 1] = iq_complex.imag

        i_mean = float(np.mean(iq[:, 0]))
        q_mean = float(np.mean(iq[:, 1]))
        iq[:, 0] -= i_mean
        iq[:, 1] -= q_mean

        abs_iq = np.abs(iq).reshape(-1)
        p995 = float(np.percentile(abs_iq, 99.5)) if abs_iq.size else 0.0
        rms = float(np.sqrt(np.mean(iq[:, 0] * iq[:, 0] + iq[:, 1] * iq[:, 1]))) if iq.size else 0.0

        gain = 1.0
        if p995 > 1.0:
            gain = max(0.05, min(8.0, self._target_peak / p995))
            iq *= gain

        out = np.clip(np.rint(iq), -128, 127).astype(np.int8, copy=False)
        output_clip_pct = float(np.count_nonzero((out <= -127) | (out >= 126)) * 100.0 / max(1, out.size))
        preambles = self._count_mode_s_preambles(out)
        self._last_iq_stats = {
            "normalize": True,
            "i_dc": round(i_mean, 2),
            "q_dc": round(q_mean, 2),
            "p995": round(p995, 2),
            "rms": round(rms, 2),
            "gain": round(gain, 3),
            "input_clip_pct": round(input_clip_pct, 3),
            "output_clip_pct": round(output_clip_pct, 3),
            "preamble_candidates": int(preambles),
            "input_rate_sps": int(input_rate),
            "output_rate_sps": int(output_rate),
            "decimation": int(decim),
            "freq_offset_hz": int(freq_offset_hz),
        }

        return out.tobytes()

    @staticmethod
    def _count_mode_s_preambles(cs8: np.ndarray) -> int:
        if cs8.size < 280:
            return 0
        iq = cs8.reshape(-1, 2).astype(np.float32, copy=False)
        mag = np.sqrt(iq[:, 0] * iq[:, 0] + iq[:, 1] * iq[:, 1])
        if mag.size > 16_384:
            mag = mag[:16_384]
        if mag.size < 240:
            return 0
        # Same 2 MSPS preamble shape the Rust decoder expects, with a small
        # absolute SNR guard so random noise does not look like activity.
        noise = float(np.percentile(mag, 35))
        spread = max(1.0, float(np.percentile(mag, 95)) - noise)
        threshold = noise + spread * 0.35
        count = 0
        limit = mag.size - 240
        j = 0
        while j < limit:
            if (
                mag[j] > mag[j + 1]
                and mag[j + 1] < mag[j + 2]
                and mag[j + 2] > mag[j + 3]
                and mag[j + 3] < mag[j]
                and mag[j + 4] < mag[j]
                and mag[j + 5] < mag[j]
                and mag[j + 6] < mag[j]
                and mag[j + 7] > mag[j + 8]
                and mag[j + 8] < mag[j + 9]
                and mag[j + 9] > mag[j + 6]
                and max(mag[j], mag[j + 2], mag[j + 7], mag[j + 9]) > threshold
            ):
                high = (mag[j] + mag[j + 2] + mag[j + 7] + mag[j + 9]) / 6.0
                if mag[j + 4] < high and mag[j + 5] < high and mag[j + 11] < high and mag[j + 12] < high:
                    count += 1
                    j += 16
                    continue
            j += 1
        return count

    def _ensure_binary(self) -> bool:
        if self.binary.exists() and os.access(self.binary, os.X_OK):
            return True
        cargo = self._find_cargo()
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

    @staticmethod
    def _find_cargo() -> str | None:
        candidates = [
            os.getenv("CARGO_BIN", ""),
            shutil.which("cargo") or "",
            str(Path.home() / ".cargo" / "bin" / "cargo"),
            "/usr/bin/cargo",
            "/usr/local/bin/cargo",
        ]
        for candidate in candidates:
            if candidate and Path(candidate).exists() and os.access(candidate, os.X_OK):
                return candidate
        return None
