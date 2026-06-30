from __future__ import annotations

import os
import json
import math
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np


WIFI_24_LOW_HZ = 2_401_000_000
WIFI_24_HIGH_HZ = 2_495_000_000
WIFI_24_CHANNELS_HZ = {
    channel: 2_412_000_000 + ((channel - 1) * 5_000_000)
    for channel in range(1, 14)
}
WIFI_24_CHANNELS_HZ[14] = 2_484_000_000
WIFI_DECODE_RATE_SPS = 20_000_000


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
        self._mac_decoders: dict[int, subprocess.Popen[bytes]] = {}
        self._mac_decoder_readers: dict[int, threading.Thread] = {}
        self._mac_decoder_backoff_until: dict[int, float] = {}
        self._channelizer_sample_index = 0

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
        self._stop_mac_decoders()

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
            "mac_decoder_channels": sorted(self._mac_decoders.keys()),
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
                self._feed_mac_decoders(cs8, info)
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
            self._stop_mac_decoders()

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

    def _feed_mac_decoders(self, raw_i8: bytes, info: dict[str, Any]) -> None:
        if str(os.getenv("SDR_SHARK_WIFI_MAC_DECODER", "1")).strip().lower() in {"0", "false", "no"}:
            return
        sample_rate = int(info.get("sample_rate_sps") or 0)
        center_freq = int(info.get("center_freq_hz") or 0)
        if sample_rate < WIFI_DECODE_RATE_SPS or center_freq <= 0:
            return
        active_channels = self._active_decode_channels(info)
        self._ensure_mac_decoders(active_channels, info)
        if not self._mac_decoders:
            return

        samples = self._cs8_to_complex(raw_i8)
        if samples.size < 1024:
            return
        start_index = self._channelizer_sample_index
        self._channelizer_sample_index += int(samples.size)
        for channel, proc in list(self._mac_decoders.items()):
            if proc.poll() is not None or proc.stdin is None:
                self._close_mac_decoder(channel)
                continue
            channel_freq = WIFI_24_CHANNELS_HZ.get(channel)
            if channel_freq is None:
                continue
            try:
                fc32 = self._extract_wifi_channel(samples, sample_rate, center_freq, channel_freq, start_index)
                if fc32.size:
                    proc.stdin.write(fc32.astype(np.complex64, copy=False).tobytes())
            except BrokenPipeError:
                self._close_mac_decoder(channel)
            except Exception as exc:
                self._set_error(f"WiFi channelizer CH {channel} failed: {exc}")

    def _active_decode_channels(self, info: dict[str, Any]) -> list[int]:
        now = time.time()
        ttl = float(os.getenv("SDR_SHARK_WIFI_MAC_DECODER_ACTIVITY_TTL", "20") or "20")
        channels = [
            channel
            for channel, event in self._last_activity_by_channel.items()
            if now - float(event.get("seen_at") or 0.0) <= ttl
        ]
        center_channel = self._channel_from_frequency_hz(info.get("center_freq_hz"))
        if center_channel is not None:
            channels.append(center_channel)
        visible = set(self._visible_wifi_channels(info))
        deduped = sorted({int(channel) for channel in channels if int(channel) in visible})
        max_decoders = max(1, int(os.getenv("SDR_SHARK_WIFI_MAC_DECODER_MAX_CHANNELS", "4") or "4"))
        center_freq = float(info.get("center_freq_hz") or 0)
        deduped.sort(key=lambda channel: abs(float(WIFI_24_CHANNELS_HZ[channel]) - center_freq))
        return deduped[:max_decoders]

    def _visible_wifi_channels(self, info: dict[str, Any]) -> list[int]:
        center_freq = float(info.get("center_freq_hz") or 0)
        sample_rate = float(info.get("sample_rate_sps") or 0)
        if center_freq <= 0 or sample_rate <= 0:
            return []
        low = center_freq - (sample_rate / 2.0)
        high = center_freq + (sample_rate / 2.0)
        return [
            channel
            for channel, freq in WIFI_24_CHANNELS_HZ.items()
            if (freq - 8_000_000) >= low and (freq + 8_000_000) <= high
        ]

    def _ensure_mac_decoders(self, channels: list[int], info: dict[str, Any]) -> None:
        wanted = set(channels)
        for channel in list(self._mac_decoders.keys()):
            if channel not in wanted:
                self._close_mac_decoder(channel)
        for channel in channels:
            if time.time() < self._mac_decoder_backoff_until.get(channel, 0.0):
                continue
            proc = self._mac_decoders.get(channel)
            if proc is not None and proc.poll() is None:
                continue
            self._start_mac_decoder(channel, info)

    def _start_mac_decoder(self, channel: int, info: dict[str, Any]) -> None:
        src = self.rf_sentinel_root / "rf_platform" / "plugins" / "wifi-80211" / "src"
        stack_root = Path(os.getenv("WIFI_80211_STACK_ROOT", "/home/jake/workspace/SDR/wifi_80211_sdr_stack")).expanduser()
        env = os.environ.copy()
        stack_python = stack_root / "local" / "lib" / "python3.12" / "dist-packages"
        stack_lib = stack_root / "local" / "lib" / "x86_64-linux-gnu"
        env["PYTHONPATH"] = f"{src}:{stack_root}:{stack_python}:{env.get('PYTHONPATH', '')}".rstrip(":")
        env["LD_LIBRARY_PATH"] = f"{stack_lib}:{env.get('LD_LIBRARY_PATH', '')}".rstrip(":")
        env["WIFI_80211_STACK_ROOT"] = str(stack_root)
        log_dir = Path(os.getenv("SDR_SHARK_LOG_DIR", "/var/log/sdr-shark")).expanduser()
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            log_dir = Path("/tmp")
        channel_freq = WIFI_24_CHANNELS_HZ[channel]
        pcap_path = log_dir / f"wifi-ch{channel}-{os.getpid()}.pcap"
        decoder_script = Path(
            os.getenv("SDR_SHARK_WIFI_DECODER_SCRIPT", str(stack_root / "scripts" / "wifi_rx_bladerf_gr.py"))
        ).expanduser()
        cmd = [
            sys.executable,
            str(decoder_script),
            "--freq",
            str(float(channel_freq)),
            "--rate",
            str(float(WIFI_DECODE_RATE_SPS)),
            "--bandwidth",
            str(float(WIFI_DECODE_RATE_SPS)),
            "--input-stdin",
            "--pcap",
            str(pcap_path),
            "--jsonl",
            str(self._frame_jsonl_path),
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(stack_root),
                env=env,
                bufsize=0,
            )
        except Exception as exc:
            self._set_error(f"WiFi MAC decoder CH {channel} start failed: {exc}")
            self._mac_decoder_backoff_until[channel] = time.time() + 30.0
            return
        self._mac_decoders[channel] = proc
        reader = threading.Thread(target=self._read_mac_decoder_stdout, args=(channel, proc, dict(info)), daemon=True)
        self._mac_decoder_readers[channel] = reader
        reader.start()

    def _read_mac_decoder_stdout(self, channel: int, proc: subprocess.Popen[bytes], info: dict[str, Any]) -> None:
        if proc.stdout is None:
            return
        for raw_line in proc.stdout:
            text = raw_line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            if not text.startswith("{"):
                if str(os.getenv("SDR_SHARK_WIFI_MAC_DECODER_LOG", "0")).strip() in {"1", "true", "yes"}:
                    self._set_error(f"WiFi MAC CH {channel}: {text[:160]}")
                continue
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if "decoded_frames" in event:
                continue
            event.setdefault("channel", channel)
            event.setdefault("frequency_mhz", float(WIFI_24_CHANNELS_HZ[channel]) / 1e6)
            try:
                from wifi_80211 import normalize_frame_event
                event = normalize_frame_event(event, source=f"wifi_mac_ch{channel}")
            except Exception:
                event.update({"protocol": "wifi", "kind": "wifi_frame", "source": f"wifi_mac_ch{channel}"})
            self._append_event(event, info=info)
        code = proc.poll()
        if code not in {None, 0}:
            self._set_error(f"WiFi MAC decoder CH {channel} exited with {code}")
            self._mac_decoder_backoff_until[channel] = time.time() + 30.0

    def _close_mac_decoder(self, channel: int) -> None:
        proc = self._mac_decoders.pop(channel, None)
        self._mac_decoder_readers.pop(channel, None)
        if proc is None:
            return
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except Exception:
            pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1.0)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _stop_mac_decoders(self) -> None:
        for channel in list(self._mac_decoders.keys()):
            self._close_mac_decoder(channel)

    @staticmethod
    def _cs8_to_complex(raw_i8: bytes) -> np.ndarray:
        raw = np.frombuffer(raw_i8, dtype=np.int8)
        if raw.size < 2:
            return np.empty(0, dtype=np.complex64)
        raw = raw[: raw.size - (raw.size % 2)].astype(np.float32)
        return ((raw[0::2] + 1j * raw[1::2]) / 128.0).astype(np.complex64, copy=False)

    @staticmethod
    def _extract_wifi_channel(
        samples: np.ndarray,
        sample_rate_sps: int,
        center_freq_hz: int,
        channel_freq_hz: int,
        start_index: int,
    ) -> np.ndarray:
        offset_hz = float(channel_freq_hz - center_freq_hz)
        if abs(offset_hz) > 1.0:
            n = np.arange(samples.size, dtype=np.float64) + float(start_index)
            samples = samples * np.exp((-2j * math.pi * offset_hz / float(sample_rate_sps)) * n)
        if int(sample_rate_sps) == WIFI_DECODE_RATE_SPS:
            return samples.astype(np.complex64, copy=False)
        out_len = max(1, int(round(samples.size * (WIFI_DECODE_RATE_SPS / float(sample_rate_sps)))))
        return WiFiGatewayPlugin._resample_complex_fft(samples, out_len)

    @staticmethod
    def _resample_complex_fft(samples: np.ndarray, out_len: int) -> np.ndarray:
        in_len = int(samples.size)
        out_len = int(out_len)
        if in_len <= 0 or out_len <= 0:
            return np.empty(0, dtype=np.complex64)
        if in_len == out_len:
            return samples.astype(np.complex64, copy=False)
        spectrum = np.fft.fftshift(np.fft.fft(samples))
        if out_len < in_len:
            start = (in_len - out_len) // 2
            spectrum = spectrum[start : start + out_len]
        else:
            pad_before = (out_len - in_len) // 2
            pad_after = out_len - in_len - pad_before
            spectrum = np.pad(spectrum, (pad_before, pad_after), mode="constant")
        return (np.fft.ifft(np.fft.ifftshift(spectrum)) * (out_len / in_len)).astype(np.complex64, copy=False)

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
