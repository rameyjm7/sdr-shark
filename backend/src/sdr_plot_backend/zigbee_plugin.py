from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


ZIGBEE_LOW_HZ = 2_405_000_000
ZIGBEE_HIGH_HZ = 2_480_000_000
ZIGBEE_CHANNEL_GUARD_HZ = 1_200_000


def _window_contains(center_freq_hz: int, sample_rate_sps: int, freq_hz: int, guard_hz: int = 0) -> bool:
    half_span = int(sample_rate_sps) // 2
    return (int(center_freq_hz) - half_span - int(guard_hz)) <= int(freq_hz) <= (
        int(center_freq_hz) + half_span + int(guard_hz)
    )


class ZigbeeGatewayPlugin:
    """Decode IEEE 802.15.4/Zigbee frames from SDR-Shark's shared IQ tap."""

    def __init__(self) -> None:
        self.enabled = str(os.getenv("SDR_SHARK_ZIGBEE_PLUGIN", "1")).strip().lower() not in {"0", "false", "no"}
        self.rf_sentinel_root = Path(
            os.getenv("RF_SENTINEL_ROOT", "/home/jake/workspace/SDR/RF_Sentinel")
        ).expanduser()
        self._events: deque[dict[str, Any]] = deque(maxlen=int(os.getenv("SDR_SHARK_ZIGBEE_EVENT_LIMIT", "200")))
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active_key: tuple[Any, ...] | None = None
        self._last_error = ""
        self._last_start_attempt = 0.0
        self._decode_interval_s = max(
            0.0,
            float(os.getenv("SDR_SHARK_ZIGBEE_DECODE_INTERVAL_MS", "0") or "0") / 1000.0,
        )
        self._runtime_channels: list[int] = []
        self._burst_count = 0
        self._decoded_count = 0
        self._reject_count = 0
        self._chunk_count = 0
        self._decode_chunk_count = 0
        self._last_diagnostics: dict[str, Any] = {}

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
        self._thread = threading.Thread(target=self._run_iq_tap, args=(sdr, dict(info), self._stop), daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=1.0)
        self._thread = None
        self._active_key = None
        self._runtime_channels = []

    def snapshot(self, max_events: int = 50) -> dict[str, Any]:
        with self._lock:
            events = list(self._events)[-max(1, int(max_events)):]
        channels = sorted({int(event["channel"]) for event in events if str(event.get("channel", "")).isdigit()})
        return {
            "enabled": self.enabled,
            "active": bool(self._thread is not None and self._thread.is_alive()),
            "event_count": len(events),
            "frame_count": sum(1 for event in events if event.get("kind") == "zigbee_frame"),
            "channels": channels,
            "runtime_channels": list(self._runtime_channels),
            "burst_count": int(self._burst_count),
            "decoded_count": int(self._decoded_count),
            "reject_count": int(self._reject_count),
            "chunk_count": int(self._chunk_count),
            "decode_chunk_count": int(self._decode_chunk_count),
            "last_diagnostics": dict(self._last_diagnostics),
            "events": events,
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
        if center <= 0 or rate < 4_000_000:
            return False
        low = center - (rate // 2)
        high = center + (rate // 2)
        return high >= (ZIGBEE_LOW_HZ - ZIGBEE_CHANNEL_GUARD_HZ) and low <= (ZIGBEE_HIGH_HZ + ZIGBEE_CHANNEL_GUARD_HZ)

    def _run_iq_tap(self, sdr: Any, info: dict[str, Any], stop: threading.Event) -> None:
        runtimes = self._build_runtimes(info)
        if not runtimes:
            self._set_error("No Zigbee channels overlap the current receive window")
            return
        self._runtime_channels = [int(getattr(runtime.plan, "channel", 0)) for runtime in runtimes]
        self._chunk_count = 0
        self._burst_count = 0
        self._decoded_count = 0
        self._reject_count = 0
        self._decode_chunk_count = 0
        self._last_diagnostics = {}
        aggregate_ms = max(0.0, float(os.getenv("SDR_SHARK_ZIGBEE_AGGREGATE_MS", "3.5") or "3.5"))
        sample_rate_sps = int(info.get("sample_rate_sps") or 0)
        aggregate_bytes = int(round((float(sample_rate_sps) * aggregate_ms / 1000.0) * 2.0))
        aggregate_bytes = max(2, aggregate_bytes + (aggregate_bytes % 2))
        max_aggregate_bytes = max(aggregate_bytes, int(os.getenv("SDR_SHARK_ZIGBEE_MAX_AGGREGATE_BYTES", "524288") or "524288"))
        pending = bytearray()

        subscriber_id = ""
        old_tap_interval = getattr(sdr, "_iq_tap_interval_s", None)
        try:
            if old_tap_interval is not None:
                setattr(sdr, "_iq_tap_interval_s", 0.0)
            subscriber_id, chunks = sdr.subscribe_iq_tap(max_chunks=256)
            self._set_error("")
            next_decode_at = 0.0
            while not stop.is_set():
                try:
                    cs8 = chunks.get(timeout=0.5)
                except Exception:
                    continue
                if not cs8:
                    break
                self._chunk_count += 1
                pending.extend(cs8)
                if len(pending) < aggregate_bytes:
                    continue
                if len(pending) > max_aggregate_bytes:
                    del pending[: max(0, len(pending) - max_aggregate_bytes)]
                now = time.monotonic()
                if now < next_decode_at:
                    continue
                next_decode_at = now + self._decode_interval_s
                self._process_cs8_chunk(bytes(pending), info, runtimes)
                self._decode_chunk_count += 1
                pending.clear()
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

    def _build_runtimes(self, info: dict[str, Any]) -> list[Any]:
        src = self.rf_sentinel_root / "rf_platform" / "plugins" / "zigbee-802154" / "src"
        if src.exists() and str(src) not in sys.path:
            sys.path.insert(0, str(src))
        try:
            from zigbee_802154.decoder import IEEE802154Decoder, channel_to_center_freq
            from zigbee_802154.wideband import (
                WidebandChannelPlan,
                WidebandChannelRuntime,
                WidebandDetectorConfig,
            )
        except Exception as exc:
            self._set_error(f"Zigbee plugin unavailable: {exc}")
            return []

        center_freq_hz = int(info.get("center_freq_hz") or 0)
        sample_rate_sps = int(info.get("sample_rate_sps") or 0)
        detector_config = WidebandDetectorConfig(
            pre_roll_ms=0.2,
            open_factor=float(os.getenv("SDR_SHARK_ZIGBEE_OPEN_FACTOR", "6.0") or "6.0"),
            close_factor=float(os.getenv("SDR_SHARK_ZIGBEE_CLOSE_FACTOR", "3.0") or "3.0"),
            min_burst_ms=0.05,
            max_burst_ms=5.0,
        )
        channel_rate_sps = int(os.getenv("SDR_SHARK_ZIGBEE_CHANNEL_RATE_SPS", "4000000") or "4000000")
        decimation = max(1, int(round(float(sample_rate_sps) / float(channel_rate_sps))))
        output_rate = max(1, int(round(float(sample_rate_sps) / float(decimation))))

        runtimes = []
        for channel in range(11, 27):
            freq_hz = channel_to_center_freq(channel)
            if not _window_contains(center_freq_hz, sample_rate_sps, freq_hz, guard_hz=ZIGBEE_CHANNEL_GUARD_HZ):
                continue
            plan = WidebandChannelPlan(
                channel=channel,
                center_freq_hz=freq_hz,
                freq_offset_hz=float(freq_hz - center_freq_hz),
                output_sample_rate_sps=output_rate,
                decimation=decimation,
            )
            runtimes.append(
                WidebandChannelRuntime(
                    plan,
                    detector_config=detector_config,
                    decoder=IEEE802154Decoder(
                        frequency_search_hz=(0, -25_000, 25_000),
                        waveform_pattern_corr_min=float(os.getenv("SDR_SHARK_ZIGBEE_CORR_MIN", "0.18") or "0.18"),
                        require_fcs=True,
                    ),
                )
            )
        return runtimes

    def _process_cs8_chunk(self, cs8: bytes, info: dict[str, Any], runtimes: list[Any]) -> None:
        try:
            from zigbee_802154.wideband import detect_wideband_bursts
        except Exception as exc:
            self._set_error(f"Zigbee wideband detector unavailable: {exc}")
            return

        sample_rate_sps = int(info.get("sample_rate_sps") or 0)
        min_duration_ms = float(os.getenv("SDR_SHARK_ZIGBEE_MIN_BURST_MS", "0.05") or "0.05")
        min_peak_dbfs = float(os.getenv("SDR_SHARK_ZIGBEE_MIN_PEAK_DBFS", "-80") or "-80")
        for runtime, burst in detect_wideband_bursts(
            raw_chunk=cs8,
            input_sample_rate_sps=sample_rate_sps,
            runtimes=runtimes,
        ):
            self._burst_count += 1
            duration_ms = float(burst.duration_seconds * 1000.0)
            peak_dbfs = self._dbfs(float(getattr(burst, "peak", 0.0)))
            if duration_ms < min_duration_ms or peak_dbfs < min_peak_dbfs:
                self._reject_count += 1
                continue
            try:
                frame = runtime.decoder.decode(burst)
            except Exception as exc:
                self._set_error(f"Zigbee decoder error: {exc}")
                self._reject_count += 1
                continue
            if frame is None or not bool(getattr(frame, "fcs_ok", False)):
                diag = getattr(runtime.decoder, "last_diagnostics", None)
                if diag is not None and hasattr(diag, "to_dict"):
                    try:
                        self._last_diagnostics = dict(diag.to_dict())
                    except Exception:
                        self._last_diagnostics = {}
                self._reject_count += 1
                continue
            event = json.loads(frame.to_json())
            event["protocol"] = "zigbee"
            event["kind"] = "zigbee_frame"
            event["source"] = "rf_sentinel"
            event["device_id"] = info.get("device_id", "")
            event["source_stream_id"] = info.get("stream_id", "")
            event["rssi_dbfs"] = round(peak_dbfs, 1)
            event["duration_ms"] = round(duration_ms, 3)
            decoded_text = self._printable_payload_text(str(event.get("mac", {}).get("payload_hex") or ""))
            if decoded_text:
                event["decoded_text"] = decoded_text
            self._decoded_count += 1
            self._append_event(event)

    def _append_event(self, event: dict[str, Any]) -> None:
        item = dict(event)
        item.setdefault("seen_at", float(item.get("timestamp") or time.time()))
        with self._lock:
            self._events.append(item)

    def _set_error(self, message: str) -> None:
        self._last_error = str(message or "")

    @staticmethod
    def _printable_payload_text(payload_hex: str) -> str:
        try:
            payload = bytes.fromhex(str(payload_hex or ""))
        except ValueError:
            return ""
        runs: list[bytes] = []
        current = bytearray()
        for value in payload:
            if 0x20 <= value <= 0x7E:
                current.append(value)
                continue
            if len(current) >= 3:
                runs.append(bytes(current))
            current.clear()
        if len(current) >= 3:
            runs.append(bytes(current))
        if not runs:
            return ""
        return max(runs, key=len).decode("ascii", errors="replace")

    @staticmethod
    def _dbfs(value: float) -> float:
        import math

        return 20.0 * math.log10(max(float(value), 1e-12))
