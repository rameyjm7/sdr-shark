from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any


WIFI_24_LOW_HZ = 2_401_000_000
WIFI_24_HIGH_HZ = 2_495_000_000


class WiFiGatewayPlugin:
    """Detect 802.11 activity from SDR-Shark's shared IQ tap."""

    def __init__(self) -> None:
        self.enabled = str(os.getenv("SDR_SHARK_WIFI_PLUGIN", "1")).strip().lower() not in {"0", "false", "no"}
        self.rf_sentinel_root = Path(
            os.getenv("RF_SENTINEL_ROOT", "/home/jake/workspace/SDR/RF_Sentinel")
        ).expanduser()
        self._events: deque[dict[str, Any]] = deque(maxlen=int(os.getenv("SDR_SHARK_WIFI_EVENT_LIMIT", "200")))
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._active_key: tuple[Any, ...] | None = None
        self._last_error = ""
        self._last_start_attempt = 0.0
        self._frame_jsonl_path = Path(
            os.getenv(
                "SDR_SHARK_WIFI_FRAME_JSONL",
                "/home/jake/workspace/SDR/wifi_80211_sdr_stack/pcaps/wifi_bladerf_frames.jsonl",
            )
        ).expanduser()
        self._frame_pcap_path = Path(os.getenv("SDR_SHARK_WIFI_FRAME_PCAP", "")).expanduser()
        self._frame_jsonl_offset = self._initial_jsonl_offset(self._frame_jsonl_path)
        self._frame_pcap_count = 0
        self._next_frame_poll_at = 0.0
        self._last_activity_by_channel: dict[int, dict[str, Any]] = {}

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

    def snapshot(self, max_events: int = 50) -> dict[str, Any]:
        with self._lock:
            all_events = list(self._events)
        max_events = max(1, int(max_events))
        frame_budget = max(4, max_events // 2)
        activity_budget = max(1, max_events - frame_budget)
        frame_events = [event for event in all_events if event.get("kind") == "wifi_frame"][-frame_budget:]
        activity_events = [event for event in all_events if event.get("kind") == "wifi_activity"][-activity_budget:]
        selected = {id(event): event for event in [*activity_events, *frame_events]}
        events = sorted(selected.values(), key=lambda event: float(event.get("seen_at") or 0.0), reverse=True)[:max_events]
        channels = sorted({int(event["channel"]) for event in all_events if str(event.get("channel", "")).isdigit()})
        return {
            "enabled": self.enabled,
            "active": bool(self._thread is not None and self._thread.is_alive()),
            "event_count": len(all_events),
            "activity_count": sum(1 for event in all_events if event.get("kind") == "wifi_activity"),
            "frame_count": sum(1 for event in all_events if event.get("kind") == "wifi_frame"),
            "channels": channels,
            "events": events,
            "frame_jsonl": str(self._frame_jsonl_path),
            "frame_jsonl_offset": self._frame_jsonl_offset,
            "frame_jsonl_exists": self._frame_jsonl_path.exists(),
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
        if backend not in {"gateway", "soapy"}:
            return False
        center = int(info.get("center_freq_hz") or 0)
        rate = int(info.get("sample_rate_sps") or 0)
        if center <= 0 or rate < 10_000_000:
            return False
        low = center - (rate // 2)
        high = center + (rate // 2)
        return high >= WIFI_24_LOW_HZ and low <= WIFI_24_HIGH_HZ

    def _run_iq_tap(self, sdr: Any, info: dict[str, Any], stop: threading.Event) -> None:
        demodulator = self._build_demodulator()
        if demodulator is None:
            return

        subscriber_id = ""
        try:
            subscriber_id, chunks = sdr.subscribe_iq_tap(max_chunks=32)
            self._set_error("")
            while not stop.is_set():
                try:
                    cs8 = chunks.get(timeout=0.5)
                except Exception:
                    continue
                if not cs8:
                    break
                for event in demodulator.process_chunk(
                    raw_i8=cs8,
                    center_freq_hz=int(info.get("center_freq_hz") or 0),
                    sample_rate_sps=int(info.get("sample_rate_sps") or 0),
                    source=str(info.get("source") or "iq_tap"),
                    source_window="sdr-shark-main",
                    source_device_id=str(info.get("device_id") or ""),
                ):
                    self._append_event(event, info=info)
                self._poll_frame_sources(info)
        except Exception as exc:
            if not stop.is_set():
                self._set_error(str(exc))
        finally:
            if subscriber_id and hasattr(sdr, "release_iq_tap"):
                try:
                    sdr.release_iq_tap(subscriber_id)
                except Exception:
                    pass

    def _build_demodulator(self) -> Any | None:
        src = self.rf_sentinel_root / "rf_platform" / "plugins" / "wifi-80211" / "src"
        if src.exists() and str(src) not in sys.path:
            sys.path.insert(0, str(src))
        try:
            from wifi_80211 import WiFiActivityDemodulator
        except Exception as exc:
            self._set_error(f"WiFi plugin unavailable: {exc}")
            return None
        return WiFiActivityDemodulator(
            threshold=float(os.getenv("SDR_SHARK_WIFI_ACTIVITY_THRESHOLD", "0.55") or "0.55"),
            min_interval_s=float(os.getenv("SDR_SHARK_WIFI_DECODE_INTERVAL_MS", "250") or "250"),
        )

    def _poll_frame_sources(self, info: dict[str, Any]) -> None:
        now = time.monotonic()
        if now < self._next_frame_poll_at:
            return
        self._next_frame_poll_at = now + max(0.2, float(os.getenv("SDR_SHARK_WIFI_FRAME_POLL_MS", "1000") or "1000") / 1000.0)
        src = self.rf_sentinel_root / "rf_platform" / "plugins" / "wifi-80211" / "src"
        if src.exists() and str(src) not in sys.path:
            sys.path.insert(0, str(src))
        try:
            from wifi_80211 import read_jsonl_events, read_pcap_events
        except Exception as exc:
            self._set_error(f"WiFi frame parser unavailable: {exc}")
            return

        try:
            events, self._frame_jsonl_offset = read_jsonl_events(
                self._frame_jsonl_path,
                start_offset=self._frame_jsonl_offset,
            )
            for event in events:
                self._append_event(event, info=info)
        except Exception as exc:
            self._set_error(f"WiFi JSONL parse failed: {exc}")

        if str(self._frame_pcap_path) not in {"", "."}:
            try:
                events, self._frame_pcap_count = read_pcap_events(
                    self._frame_pcap_path,
                    skip=self._frame_pcap_count,
                    limit=50,
                )
                for event in events:
                    self._append_event(event, info=info)
            except Exception as exc:
                self._set_error(f"WiFi PCAP parse failed: {exc}")

    def _append_event(self, event: dict[str, Any], *, info: dict[str, Any] | None = None) -> None:
        item = dict(event)
        item.setdefault("seen_at", time.time())
        if item.get("kind") == "wifi_activity":
            self._decorate_activity_event(item, info or {})
            channel = self._coerce_int(item.get("channel"))
            if channel is not None:
                self._last_activity_by_channel[channel] = dict(item)
        elif item.get("kind") == "wifi_frame":
            self._decorate_frame_event(item, info or {})
            if item.get("timestamp") is not None:
                item.setdefault("packet_timestamp", item.get("timestamp"))
            item["seen_at"] = time.time()
        with self._lock:
            self._events.append(item)

    def _set_error(self, message: str) -> None:
        self._last_error = str(message or "")

    @staticmethod
    def _initial_jsonl_offset(path: Path) -> int:
        if str(os.getenv("SDR_SHARK_WIFI_FRAME_READ_EXISTING", "")).strip().lower() in {"1", "true", "yes"}:
            return 0
        backfill_bytes = int(os.getenv("SDR_SHARK_WIFI_FRAME_BACKFILL_BYTES", str(1024 * 1024)) or "0")
        try:
            size = int(path.stat().st_size)
            return max(0, size - max(0, backfill_bytes))
        except Exception:
            return 0

    def _decorate_frame_event(self, event: dict[str, Any], info: dict[str, Any]) -> None:
        channel = self._coerce_int(event.get("channel"))
        if channel is None:
            channel = self._coerce_int(os.getenv("SDR_SHARK_WIFI_FRAME_CHANNEL", ""))
        if channel is None:
            channel = self._channel_from_frequency_mhz(event.get("frequency_mhz"))
        if channel is None:
            channel = self._channel_from_frequency_hz(info.get("center_freq_hz"))
        if channel is not None:
            event["channel"] = channel

        if event.get("frequency_mhz") is None and channel is not None:
            event["frequency_mhz"] = self._frequency_mhz_from_channel(channel)

        if event.get("rssi_dbm") is None and os.getenv("SDR_SHARK_WIFI_FRAME_RSSI_DBM"):
            event["rssi_dbm"] = self._coerce_float(os.getenv("SDR_SHARK_WIFI_FRAME_RSSI_DBM"))

        if event.get("rssi_dbfs") is None and channel is not None:
            activity = self._last_activity_by_channel.get(channel)
            if activity is not None:
                event["rssi_dbfs"] = activity.get("power_dbfs")
                event.setdefault("score", activity.get("score"))
                event.setdefault("confidence", activity.get("confidence"))

    def _decorate_activity_event(self, event: dict[str, Any], info: dict[str, Any]) -> None:
        if self._coerce_int(event.get("channel")) is not None:
            return
        likely_center = self._coerce_float(event.get("likely_center_freq_hz"))
        if likely_center is not None and likely_center > 0:
            channel = self._channel_from_frequency_hz(likely_center)
            if channel is not None:
                event["channel"] = channel
                return
        tuned_center = self._coerce_float(info.get("center_freq_hz") or event.get("center_freq_hz"))
        channel = self._channel_from_frequency_hz(tuned_center)
        if channel is not None:
            event["channel"] = channel
            event["likely_center_freq_hz"] = int(tuned_center)

    @staticmethod
    def _coerce_int(value: Any) -> int | None:
        try:
            return int(float(str(value)))
        except Exception:
            return None

    @staticmethod
    def _coerce_float(value: Any) -> float | None:
        try:
            return float(value)
        except Exception:
            return None

    @classmethod
    def _channel_from_frequency_hz(cls, value: Any) -> int | None:
        freq = cls._coerce_float(value)
        if freq is None:
            return None
        return cls._channel_from_frequency_mhz(freq / 1e6)

    @staticmethod
    def _channel_from_frequency_mhz(value: Any) -> int | None:
        try:
            freq = float(value)
        except Exception:
            return None
        if 2407 <= freq <= 2472:
            channel = round((freq - 2407) / 5)
            return int(channel) if 1 <= channel <= 13 else None
        if 2482 <= freq <= 2486:
            return 14
        return None

    @staticmethod
    def _frequency_mhz_from_channel(channel: int) -> float | None:
        if 1 <= channel <= 13:
            return float(2407 + (channel * 5))
        if channel == 14:
            return 2484.0
        return None
