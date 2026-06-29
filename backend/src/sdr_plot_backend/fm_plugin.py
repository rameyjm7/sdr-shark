import ctypes
import math
import os
import queue
import subprocess
import sys
import threading
import time
import wave
from io import BytesIO
from pathlib import Path

import numpy as np


FM_LOW_HZ = 87_500_000
FM_HIGH_HZ = 108_000_000
FM_GRID_START_HZ = 87_700_000
FM_GRID_STEP_HZ = 200_000


def _load_rf_sentinel_demod():
    root = Path(os.getenv("RF_SENTINEL_ROOT", "/home/jake/workspace/SDR/RF_Sentinel"))
    src = root / "rf_platform" / "plugins" / "fm-broadcast" / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    try:
        from fm_broadcast.dsp import FmQualityDemod  # type: ignore
        return FmQualityDemod
    except Exception:
        return None


_RfSentinelFmQualityDemod = _load_rf_sentinel_demod()


class _FallbackFmQualityDemod:
    """Fallback copy of RF_Sentinel's quality metrics if the plugin is not importable."""

    def __init__(self, sample_rate_sps):
        self.sample_rate_sps = int(sample_rate_sps)
        self.decim = max(1, int(round(self.sample_rate_sps / 240_000.0)))
        self.demod_rate = self.sample_rate_sps / float(self.decim)
        self.prev = np.complex64(1.0 + 0j)
        self.audio_rms = 0.0
        self.pilot_db = -120.0
        self.rds_subcarrier_db = -120.0
        self._metric_buf = np.empty(0, dtype=np.float32)

    def process_iq(self, iq):
        if iq.size < self.decim * 8:
            return
        z = iq[:: self.decim]
        if z.size < 8:
            return
        previous = np.empty_like(z)
        previous[0] = self.prev
        previous[1:] = z[:-1]
        self.prev = z[-1]
        demod = np.angle(z * np.conj(previous)).astype(np.float32)
        if demod.size < 128:
            return
        demod = demod - float(np.mean(demod))
        self.audio_rms = float((self.audio_rms * 0.8) + (np.sqrt(np.mean(demod * demod)) * 0.2))
        self._metric_buf = np.concatenate((self._metric_buf, demod))
        max_metric_samples = int(max(self.demod_rate * 1.5, 8192))
        if self._metric_buf.size > max_metric_samples:
            self._metric_buf = self._metric_buf[-max_metric_samples:]
        self._update_metrics()

    def _update_metrics(self):
        if self._metric_buf.size < 4096:
            return
        n = min(32768, self._metric_buf.size)
        samples = self._metric_buf[-n:]
        windowed = samples * np.hanning(samples.size).astype(np.float32)
        spectrum = np.abs(np.fft.rfft(windowed)) ** 2
        freqs = np.fft.rfftfreq(samples.size, d=1.0 / float(self.demod_rate))
        noise = float(np.median(spectrum)) + 1e-12

        def band_db(center_hz, width_hz):
            mask = np.abs(freqs - center_hz) <= (width_hz / 2.0)
            if not np.any(mask):
                return -120.0
            power = float(np.mean(spectrum[mask]))
            return 10.0 * math.log10((power + 1e-12) / noise)

        self.pilot_db = band_db(19_000.0, 900.0)
        self.rds_subcarrier_db = band_db(57_000.0, 3_500.0)


def _demod_class():
    return _RfSentinelFmQualityDemod or _FallbackFmQualityDemod


class _LiquidFmChannelizer:
    _lib = None
    _load_attempted = False

    @classmethod
    def _load_lib(cls):
        if cls._load_attempted:
            return cls._lib
        cls._load_attempted = True
        lib_path = Path(__file__).resolve().parent / "native" / "libfm_channelizer_liquid.so"
        if not lib_path.exists():
            build_script = lib_path.parent / "build_fm_channelizer.sh"
            if build_script.exists():
                try:
                    subprocess.run([str(build_script)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
        try:
            lib = ctypes.CDLL(str(lib_path))
            lib.sdrshark_fm_channelizer_create.argtypes = [ctypes.c_float, ctypes.c_float, ctypes.c_float]
            lib.sdrshark_fm_channelizer_create.restype = ctypes.c_void_p
            lib.sdrshark_fm_channelizer_destroy.argtypes = [ctypes.c_void_p]
            lib.sdrshark_fm_channelizer_destroy.restype = None
            lib.sdrshark_fm_channelizer_output_rate.argtypes = [ctypes.c_void_p]
            lib.sdrshark_fm_channelizer_output_rate.restype = ctypes.c_float
            lib.sdrshark_fm_channelizer_process.argtypes = [
                ctypes.c_void_p,
                ctypes.c_void_p,
                ctypes.c_uint,
                ctypes.c_void_p,
                ctypes.c_uint,
            ]
            lib.sdrshark_fm_channelizer_process.restype = ctypes.c_int
            cls._lib = lib
        except Exception:
            cls._lib = None
        return cls._lib

    @classmethod
    def available(cls):
        return cls._load_lib() is not None

    def __init__(self, sample_rate, offset_hz, channel_rate=240_000.0):
        self.lib = self._load_lib()
        if self.lib is None:
            raise RuntimeError("Liquid-DSP FM channelizer library is not available")
        self.handle = self.lib.sdrshark_fm_channelizer_create(
            ctypes.c_float(float(sample_rate)),
            ctypes.c_float(float(offset_hz)),
            ctypes.c_float(float(channel_rate)),
        )
        if not self.handle:
            raise RuntimeError("Liquid-DSP FM channelizer create failed")
        self.output_rate = float(self.lib.sdrshark_fm_channelizer_output_rate(self.handle))

    def close(self):
        if self.handle:
            self.lib.sdrshark_fm_channelizer_destroy(self.handle)
            self.handle = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def process(self, iq):
        iq = np.ascontiguousarray(iq, dtype=np.complex64)
        if iq.size == 0:
            return np.empty(0, dtype=np.complex64)
        # Safe upper bound. The decimator will usually produce far less than this.
        output_cap = int(iq.size) + 16
        out = np.empty(output_cap, dtype=np.complex64)
        produced = self.lib.sdrshark_fm_channelizer_process(
            self.handle,
            iq.ctypes.data_as(ctypes.c_void_p),
            ctypes.c_uint(int(iq.size)),
            out.ctypes.data_as(ctypes.c_void_p),
            ctypes.c_uint(int(out.size)),
        )
        if produced < 0:
            raise RuntimeError(f"Liquid-DSP FM channelizer failed: {produced}")
        return out[:produced]


class _FmPlaybackDemod:
    """AetherCast-style mono FM demodulator that emits stereo PCM16 chunks."""

    def __init__(self, in_rate, center_freq_hz, station_freq_hz, out_rate=48_000):
        self.in_rate = float(in_rate)
        self.center_freq_hz = float(center_freq_hz)
        self.station_freq_hz = float(station_freq_hz)
        self.out_rate = int(out_rate)
        self.decim = max(1, int(round(self.in_rate / 240_000.0)))
        self.demod_rate = self.in_rate / float(self.decim)
        self.offset_hz = self.station_freq_hz - self.center_freq_hz
        self.native_channelizer = None
        self.channelizer_name = "python_fft"
        try:
            self.native_channelizer = _LiquidFmChannelizer(self.in_rate, self.offset_hz, 240_000.0)
            if self.native_channelizer.output_rate > 0:
                self.demod_rate = self.native_channelizer.output_rate
            self.channelizer_name = "liquid_dsp"
        except Exception:
            self.native_channelizer = None
        self.prev = np.complex64(1.0 + 0j)
        self.channel_cutoff_hz = float(os.getenv("SDR_SHARK_FM_CHANNEL_CUTOFF_HZ", "125000") or "125000")
        self.mono_filter = self._design_lowpass(num_taps=129, cutoff_hz=15_000.0, sample_rate=self.demod_rate)
        self._mono_tail = np.zeros(max(0, self.mono_filter.size - 1), dtype=np.float32)
        self.resample_pos = 0.0
        self.sample_cursor = 0
        self._audio_scale = 1.0

    def process_iq(self, iq):
        if iq.size < 8:
            return b""
        z = np.asarray(iq, dtype=np.complex64)
        if self.native_channelizer is not None:
            z = self.native_channelizer.process(z)
        else:
            n = np.arange(z.size, dtype=np.float32) + float(self.sample_cursor)
            self.sample_cursor += int(z.size)
            phase = np.exp(np.complex64(-2j * np.pi * self.offset_hz / self.in_rate) * n).astype(np.complex64, copy=False)
            z = (z * phase).astype(np.complex64, copy=False)
            z = self._channel_filter_and_decimate(z)
        if z.size < 8:
            return b""

        z_prev = np.empty_like(z)
        z_prev[0] = self.prev
        z_prev[1:] = z[:-1]
        self.prev = z[-1]
        demod = np.angle(z * np.conj(z_prev)).astype(np.float32)
        if demod.size < 8:
            return b""
        demod = demod - float(np.mean(demod))

        mono = self._filter_float(demod, self.mono_filter, "_mono_tail")
        step = self.demod_rate / float(self.out_rate)
        positions = np.arange(self.resample_pos, mono.size - 1, step, dtype=np.float64)
        if positions.size == 0:
            self.resample_pos = float(self.resample_pos + mono.size)
            return b""
        next_pos = float(positions[-1] + step - (mono.size - 1))
        idx = np.floor(positions).astype(np.int32)
        valid = idx + 1 < mono.size
        idx = idx[valid]
        positions = positions[valid]
        if positions.size == 0:
            self.resample_pos = max(0.0, next_pos)
            return b""
        frac = positions - idx
        audio = mono[idx] * (1.0 - frac) + mono[idx + 1] * frac
        self.resample_pos = max(0.0, next_pos)

        peak = float(np.max(np.abs(audio))) if audio.size else 1.0
        target_scale = 0.85 / max(peak, 0.2)
        self._audio_scale = (self._audio_scale * 0.9) + (target_scale * 0.1)
        audio = np.clip(audio * self._audio_scale, -1.0, 1.0)
        pcm = np.empty(audio.size * 2, dtype=np.int16)
        pcm[0::2] = (audio * 32767.0).astype(np.int16)
        pcm[1::2] = pcm[0::2]
        return pcm.tobytes()

    def _design_lowpass(self, num_taps, cutoff_hz, sample_rate):
        cutoff = min(float(cutoff_hz), (float(sample_rate) / 2.0) * 0.92)
        n = np.arange(int(num_taps), dtype=np.float32) - ((int(num_taps) - 1) / 2.0)
        taps = 2.0 * cutoff / float(sample_rate) * np.sinc(2.0 * cutoff / float(sample_rate) * n)
        taps *= np.hamming(int(num_taps)).astype(np.float32)
        taps /= max(float(np.sum(taps)), 1e-12)
        return taps.astype(np.float32)

    def _channel_filter_and_decimate(self, z):
        if z.size < 16:
            return z[:: self.decim] if self.decim > 1 else z
        spectrum = np.fft.fft(z)
        freqs = np.fft.fftfreq(z.size, d=1.0 / self.in_rate)
        spectrum[np.abs(freqs) > self.channel_cutoff_hz] = 0.0
        filtered = np.fft.ifft(spectrum).astype(np.complex64, copy=False)
        return filtered[:: self.decim] if self.decim > 1 else filtered

    def _filter_float(self, x, taps, tail_name):
        if taps.size <= 1:
            return x.astype(np.float32, copy=False)
        tail = getattr(self, tail_name)
        x = x.astype(np.float32, copy=False)
        x_ext = np.concatenate((tail, x))
        filtered = np.convolve(x_ext, taps, mode="valid").astype(np.float32)
        if tail.size:
            setattr(self, tail_name, x_ext[-tail.size :].astype(np.float32))
        return filtered


class FmBroadcastPlugin:
    """Detect, classify, and demod-score FM stations from SDR-Shark's live IQ."""

    def __init__(
        self,
        min_excess_db=7.0,
        retain_seconds=120.0,
        analyze_interval_s=1.0,
        max_candidates=16,
        max_decode_candidates=8,
    ):
        self.min_excess_db = float(os.getenv("SDR_SHARK_FM_MIN_EXCESS_DB", min_excess_db))
        self.retain_seconds = float(retain_seconds)
        self.analyze_interval_s = float(analyze_interval_s)
        self.max_candidates = int(max_candidates)
        self.max_decode_candidates = int(max_decode_candidates)
        self.confirmed_demod_refresh_s = float(os.getenv("SDR_SHARK_FM_CONFIRMED_DEMOD_REFRESH_S", "300") or "300")
        self.potential_demod_retry_s = float(os.getenv("SDR_SHARK_FM_POTENTIAL_DEMOD_RETRY_S", "5") or "5")
        self.max_iq_history_samples = int(os.getenv("SDR_SHARK_FM_AUDIO_IQ_SAMPLES", "1048576") or "1048576")
        self._lock = threading.Lock()
        self._stations = {}
        self._iq_chunks = []
        self._iq_chunk_samples = 0
        self._iq_capture_key = None
        self._last_iq_capture_at = 0.0
        self._active = False
        self._last_analyze_at = 0.0
        self._demod_source = "rf_sentinel" if _RfSentinelFmQualityDemod else "fallback"
        self._playback_freq_hz = None
        self._playback_demod = None
        self._playback_capture_key = None
        self._playback_accum = bytearray()
        self._audio_queue = queue.Queue(maxsize=80)
        self._playback_thread = None
        self._playback_stop = threading.Event()
        self._playback_error = ""

    @staticmethod
    def _station_grid(low_hz, high_hz):
        first = FM_GRID_START_HZ
        if low_hz > first:
            steps = int(np.ceil((low_hz - first) / FM_GRID_STEP_HZ))
            first += max(0, steps) * FM_GRID_STEP_HZ
        freq_hz = first
        while freq_hz <= high_hz:
            yield int(freq_hz)
            freq_hz += FM_GRID_STEP_HZ

    def update_from_iq_and_fft(self, iq_samples, fft_values, center_freq_hz, sample_rate_hz):
        now = time.time()
        try:
            center_freq_hz = float(center_freq_hz)
            sample_rate_hz = float(sample_rate_hz)
        except Exception:
            self._set_active(False, now)
            return

        if sample_rate_hz <= 0:
            self._set_active(False, now)
            return

        start_hz = center_freq_hz - (sample_rate_hz / 2.0)
        stop_hz = center_freq_hz + (sample_rate_hz / 2.0)
        visible_low_hz = max(FM_LOW_HZ, start_hz)
        visible_high_hz = min(FM_HIGH_HZ, stop_hz)
        active = visible_low_hz <= visible_high_hz
        self._set_active(active, now)
        if not active:
            return

        iq = np.asarray(iq_samples, dtype=np.complex64)
        self._append_iq_chunk(iq, center_freq_hz, sample_rate_hz, now)

        with self._lock:
            if (now - self._last_analyze_at) < self.analyze_interval_s:
                self._expire_locked(now)
                return
            self._last_analyze_at = now
            iq_history = self._iq_history_locked()

        fft = np.asarray(fft_values, dtype=np.float32)
        if fft.size < 128 or iq_history.size < 8192:
            self._expire(now)
            return

        candidates = self._detect_candidates(fft, start_hz, sample_rate_hz, visible_low_hz, visible_high_hz)
        if not candidates:
            self._expire(now)
            return

        with self._lock:
            decode_candidates = []
            for candidate in candidates[: self.max_decode_candidates]:
                if self._refresh_or_should_decode_locked(candidate, now):
                    decode_candidates.append(candidate)

        decoded = [
            self._decode_candidate(candidate, iq_history, center_freq_hz, sample_rate_hz)
            for candidate in decode_candidates
        ]

        with self._lock:
            for station in decoded:
                self._upsert_station_locked(station, now)
            self._expire_locked(now)

    def _detect_candidates(self, fft, start_hz, sample_rate_hz, visible_low_hz, visible_high_hz):
        finite = np.isfinite(fft)
        if not finite.any():
            return []
        fft = np.where(finite, fft, np.nanmedian(fft[finite]))

        hz_per_bin = sample_rate_hz / float(fft.size)
        if hz_per_bin <= 0:
            return []

        signal_half_bins = max(2, int(round(90_000 / hz_per_bin)))
        noise_inner_bins = max(signal_half_bins + 1, int(round(140_000 / hz_per_bin)))
        noise_outer_bins = max(noise_inner_bins + 2, int(round(600_000 / hz_per_bin)))
        candidates = []

        for freq_hz in self._station_grid(visible_low_hz, visible_high_hz):
            idx = int(round((freq_hz - start_hz) / hz_per_bin))
            if idx < 0 or idx >= fft.size:
                continue

            signal_start = max(0, idx - signal_half_bins)
            signal_stop = min(fft.size, idx + signal_half_bins + 1)
            signal_slice = fft[signal_start:signal_stop]
            if signal_slice.size == 0:
                continue

            noise_start = max(0, idx - noise_outer_bins)
            noise_stop = min(fft.size, idx + noise_outer_bins + 1)
            left_noise = fft[noise_start:max(noise_start, idx - noise_inner_bins)]
            right_noise = fft[min(noise_stop, idx + noise_inner_bins):noise_stop]
            noise_slice = np.concatenate((left_noise, right_noise))
            if noise_slice.size < 8:
                noise_slice = fft

            peak_power = float(np.max(signal_slice))
            avg_power = float(np.mean(signal_slice))
            noise_db = float(np.median(noise_slice))
            excess_db = peak_power - noise_db
            if excess_db < self.min_excess_db:
                continue

            candidates.append({
                "freq_hz": int(freq_hz),
                "power_dbfs": round(peak_power, 1),
                "avg_power_dbfs": round(avg_power, 1),
                "noise_dbfs": round(noise_db, 1),
                "excess_db": round(excess_db, 1),
            })

        candidates.sort(key=lambda row: (row["excess_db"], row["power_dbfs"]), reverse=True)
        return candidates[: self.max_candidates]

    def _append_iq_chunk(self, iq, center_freq_hz, sample_rate_hz, now):
        if iq.size == 0:
            return
        capture_key = (int(round(center_freq_hz)), int(round(sample_rate_hz)))
        with self._lock:
            if self._iq_capture_key != capture_key:
                self._iq_capture_key = capture_key
                self._iq_chunks = []
                self._iq_chunk_samples = 0
            if (now - self._last_iq_capture_at) < 0.03:
                return
            self._last_iq_capture_at = now
            chunk = iq.astype(np.complex64, copy=True)
            self._iq_chunks.append(chunk)
            self._iq_chunk_samples += int(chunk.size)
            max_samples = max(131_072, self.max_iq_history_samples)
            while self._iq_chunks and self._iq_chunk_samples > max_samples:
                removed = self._iq_chunks.pop(0)
                self._iq_chunk_samples -= int(removed.size)

    def start_playback(self, sdr, frequency_mhz):
        try:
            freq_hz = int(round(float(frequency_mhz) * 1_000_000))
        except Exception as exc:
            raise ValueError("Invalid FM frequency") from exc
        freq_hz = int(round(freq_hz / FM_GRID_STEP_HZ) * FM_GRID_STEP_HZ)
        if not hasattr(sdr, "subscribe_iq_tap"):
            raise ValueError("Live IQ tap is not available for this SDR backend")
        with self._lock:
            station = self._playback_station_locked(freq_hz)
        self.stop_playback()
        with self._lock:
            self._playback_freq_hz = freq_hz
            self._playback_demod = None
            self._playback_capture_key = None
            self._playback_accum.clear()
            self._playback_error = ""
            self._drain_audio_queue_locked()
            self._playback_stop = threading.Event()
            self._playback_thread = threading.Thread(
                target=self._playback_tap_loop,
                args=(sdr, freq_hz, self._playback_stop),
                daemon=True,
            )
            self._playback_thread.start()
            return dict(station)

    def _playback_station_locked(self, freq_hz):
        station = self._stations.get(freq_hz)
        if station and station.get("decode_status") == "station":
            return dict(station)

        nearby = [
            row for row in self._stations.values()
            if row.get("decode_status") == "station"
            and abs(int(row.get("frequency_hz", 0) or 0) - int(freq_hz)) <= FM_GRID_STEP_HZ
        ]
        if nearby:
            return dict(min(nearby, key=lambda row: abs(int(row.get("frequency_hz", 0) or 0) - int(freq_hz))))

        if not self._active or not (FM_LOW_HZ <= int(freq_hz) <= FM_HIGH_HZ):
            raise ValueError("FM station is not verified yet")

        freq_mhz = int(freq_hz) / 1_000_000.0
        return {
            "kind": "fm_station",
            "protocol": "fm",
            "identity": f"FM {freq_mhz:.1f} MHz",
            "frequency_hz": int(freq_hz),
            "frequency_mhz": round(freq_mhz, 1),
            "decode_status": "potential",
            "confidence": 0.25,
            "detail": "Starting wideband FM monitor from the visible tuner range.",
            "playable": True,
        }

    def stop_playback(self):
        thread = None
        with self._lock:
            self._playback_stop.set()
            thread = self._playback_thread
            self._playback_freq_hz = None
            self._playback_demod = None
            self._playback_capture_key = None
            self._playback_thread = None
            self._playback_accum.clear()
            self._playback_error = ""
            self._drain_audio_queue_locked()
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)

    def audio_batch(self, count=6, timeout=0.4):
        count = max(1, min(int(count), 16))
        timeout = max(0.05, min(float(timeout), 2.0))
        chunks = []
        for idx in range(count):
            try:
                chunk = self._audio_queue.get(timeout=timeout if idx == 0 else 0.02)
            except queue.Empty:
                break
            chunks.append(chunk)
        return b"".join(chunks)

    def _playback_tap_loop(self, sdr, freq_hz, stop):
        subscriber_id = ""
        old_tap_interval = getattr(sdr, "_iq_tap_interval_s", None)
        try:
            if old_tap_interval is not None:
                setattr(sdr, "_iq_tap_interval_s", 0.0)
            subscriber_id, chunks = sdr.subscribe_iq_tap(max_chunks=256)
            while not stop.is_set():
                try:
                    cs8 = chunks.get(timeout=0.5)
                except Exception:
                    continue
                if not cs8:
                    break
                info = sdr.iq_tap_info() if hasattr(sdr, "iq_tap_info") else sdr.gateway_stream_info()
                center_freq_hz = int(info.get("center_freq_hz") or 0)
                sample_rate_hz = int(info.get("sample_rate_sps") or 0)
                if center_freq_hz <= 0 or sample_rate_hz <= 0:
                    continue
                capture_key = (center_freq_hz, sample_rate_hz, int(freq_hz))
                with self._lock:
                    if self._playback_freq_hz != freq_hz:
                        break
                    if self._playback_demod is None or self._playback_capture_key != capture_key:
                        self._playback_capture_key = capture_key
                        self._playback_demod = _FmPlaybackDemod(sample_rate_hz, center_freq_hz, freq_hz)
                        self._playback_accum.clear()
                        self._drain_audio_queue_locked()
                    demod = self._playback_demod
                iq = self._cs8_to_complex(cs8)
                pcm = demod.process_iq(iq)
                if pcm:
                    self._queue_pcm(pcm)
        except Exception as exc:
            with self._lock:
                self._playback_error = str(exc)
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

    @staticmethod
    def _cs8_to_complex(raw):
        values = np.frombuffer(raw, dtype=np.int8)
        if values.size < 2:
            return np.empty(0, dtype=np.complex64)
        if values.size % 2:
            values = values[:-1]
        i = values[0::2].astype(np.float32) / 128.0
        q = values[1::2].astype(np.float32) / 128.0
        return (i + 1j * q).astype(np.complex64, copy=False)

    def _queue_pcm(self, pcm):
        with self._lock:
            self._playback_accum.extend(pcm)
            if len(self._playback_accum) < 8192:
                return
            out = bytes(self._playback_accum)
            self._playback_accum.clear()
            try:
                self._audio_queue.put_nowait(out)
            except queue.Full:
                try:
                    self._audio_queue.get_nowait()
                except queue.Empty:
                    pass
                try:
                    self._audio_queue.put_nowait(out)
                except queue.Full:
                    pass

    def _drain_audio_queue_locked(self):
        while True:
            try:
                self._audio_queue.get_nowait()
            except queue.Empty:
                break

    def _iq_history_locked(self):
        if not self._iq_chunks:
            return np.empty(0, dtype=np.complex64)
        return np.concatenate(self._iq_chunks).astype(np.complex64, copy=False)

    def audio_wav(self, frequency_mhz, audio_rate=48_000):
        try:
            freq_hz = int(round(float(frequency_mhz) * 1_000_000))
        except Exception as exc:
            raise ValueError("Invalid FM frequency") from exc

        with self._lock:
            station = self._stations.get(freq_hz)
            capture_key = self._iq_capture_key
            iq = self._iq_history_locked()

        if not station or station.get("decode_status") != "station":
            raise ValueError("FM station is not verified yet")
        if not capture_key or iq.size < 8192:
            raise ValueError("Not enough live IQ history for FM audio yet")

        center_freq_hz, sample_rate_hz = capture_key
        audio = self._demod_audio(iq, freq_hz, float(center_freq_hz), float(sample_rate_hz), int(audio_rate))
        if audio.size < 512:
            raise ValueError("Not enough demodulated audio yet")
        return self._wav_bytes(audio, int(audio_rate))

    def _decode_candidate(self, candidate, iq, center_freq_hz, sample_rate_hz):
        freq_hz = int(candidate["freq_hz"])
        max_samples = min(iq.size, max(131_072, min(int(sample_rate_hz * 0.35), 524_288)))
        segment = iq[-max_samples:].astype(np.complex64, copy=False)
        offset_hz = float(freq_hz - center_freq_hz)
        n = np.arange(segment.size, dtype=np.float32)
        phase = np.exp(np.complex64(-2j * np.pi * offset_hz / sample_rate_hz) * n).astype(np.complex64, copy=False)
        shifted = (segment * phase).astype(np.complex64, copy=False)

        nfft = 1 << int(np.floor(np.log2(shifted.size)))
        if nfft >= 8192:
            work = shifted[-nfft:]
            spectrum = np.fft.fft(work)
            freqs = np.fft.fftfreq(nfft, d=1.0 / sample_rate_hz)
            spectrum[np.abs(freqs) > 140_000.0] = 0.0
            filtered = np.fft.ifft(spectrum).astype(np.complex64, copy=False)
        else:
            filtered = shifted

        demod = _demod_class()(int(sample_rate_hz))
        demod.process_iq(filtered)
        audio_rms = round(float(getattr(demod, "audio_rms", 0.0) or 0.0), 5)
        pilot_db = round(float(getattr(demod, "pilot_db", -120.0) or -120.0), 1)
        rds_subcarrier_db = round(float(getattr(demod, "rds_subcarrier_db", -120.0) or -120.0), 1)
        demod_good = self._is_good_demod(candidate["excess_db"], audio_rms, pilot_db, rds_subcarrier_db)

        return {
            **candidate,
            "samples": int(filtered.size),
            "audio_rms": audio_rms,
            "pilot_db": pilot_db,
            "rds_subcarrier_db": rds_subcarrier_db,
            "stereo_likely": pilot_db >= 8.0,
            "rds_likely": rds_subcarrier_db >= 6.0,
            "decode_status": "station" if demod_good else "potential",
            "demod_good": bool(demod_good),
        }

    def _refresh_or_should_decode_locked(self, candidate, now):
        freq_hz = int(candidate["freq_hz"])
        previous = self._stations.get(freq_hz)
        if not previous:
            return True

        previous_status = previous.get("decode_status")
        last_demod_at = float(previous.get("last_demod_at", 0.0) or 0.0)
        retry_s = self.confirmed_demod_refresh_s if previous_status == "station" else self.potential_demod_retry_s
        should_decode = (now - last_demod_at) >= retry_s
        if should_decode:
            previous["last_decode_attempt_at"] = now
            return True

        previous["power_dbfs"] = candidate["power_dbfs"]
        previous["rssi_dbfs"] = candidate["power_dbfs"]
        previous["avg_power_dbfs"] = candidate["avg_power_dbfs"]
        previous["noise_dbfs"] = candidate["noise_dbfs"]
        previous["excess_db"] = candidate["excess_db"]
        previous["seen_at"] = now
        previous["last_activity_at"] = now
        previous["sightings"] = int(previous.get("sightings", 0) or 0) + 1
        self._stations[freq_hz] = previous
        return False

    @staticmethod
    def _demod_audio(iq, freq_hz, center_freq_hz, sample_rate_hz, audio_rate):
        max_samples = min(iq.size, 4_194_304)
        segment = iq[-max_samples:].astype(np.complex64, copy=False)
        offset_hz = float(freq_hz - center_freq_hz)
        n = np.arange(segment.size, dtype=np.float32)
        phase = np.exp(np.complex64(-2j * np.pi * offset_hz / sample_rate_hz) * n).astype(np.complex64, copy=False)
        shifted = (segment * phase).astype(np.complex64, copy=False)

        decim = max(1, int(round(sample_rate_hz / 240_000.0)))
        z = shifted[::decim]
        if z.size < 128:
            return np.empty(0, dtype=np.float32)
        previous = np.empty_like(z)
        previous[0] = np.complex64(1.0 + 0j)
        previous[1:] = z[:-1]
        demod = np.angle(z * np.conj(previous)).astype(np.float32)
        demod = demod - float(np.mean(demod))

        demod_rate = sample_rate_hz / float(decim)
        if demod_rate > audio_rate * 1.5:
            audio_decim = max(1, int(round(demod_rate / float(audio_rate))))
            kernel_size = min(129, max(9, audio_decim * 8 + 1))
            kernel = np.ones(kernel_size, dtype=np.float32) / float(kernel_size)
            demod = np.convolve(demod, kernel, mode="same")[::audio_decim]
            demod_rate = demod_rate / float(audio_decim)

        if demod.size < 2:
            return np.empty(0, dtype=np.float32)
        if abs(demod_rate - audio_rate) > 1.0:
            x_old = np.linspace(0.0, 1.0, demod.size, endpoint=False)
            target_size = max(1, int(round(demod.size * (audio_rate / demod_rate))))
            x_new = np.linspace(0.0, 1.0, target_size, endpoint=False)
            demod = np.interp(x_new, x_old, demod).astype(np.float32)

        demod = demod - float(np.mean(demod))
        peak = float(np.max(np.abs(demod))) if demod.size else 0.0
        if peak > 0:
            demod = (demod / peak) * 0.85
        return demod.astype(np.float32, copy=False)

    @staticmethod
    def _wav_bytes(audio, audio_rate):
        pcm = np.clip(audio, -1.0, 1.0)
        pcm16 = (pcm * 32767.0).astype("<i2", copy=False)
        output = BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(int(audio_rate))
            wav.writeframes(pcm16.tobytes())
        return output.getvalue()

    @staticmethod
    def _is_good_demod(excess_db, audio_rms, pilot_db, rds_subcarrier_db):
        if pilot_db >= 6.0 or rds_subcarrier_db >= 5.0:
            return True
        return float(excess_db) >= 10.0 and 0.006 <= float(audio_rms) <= 1.2

    def _upsert_station_locked(self, station, now):
        freq_hz = int(station["freq_hz"])
        freq_mhz = freq_hz / 1_000_000.0
        previous = self._stations.get(freq_hz, {})
        sightings = int(previous.get("sightings", 0)) + 1
        status = station["decode_status"]
        payload = {
            "kind": "fm_station",
            "protocol": "fm",
            "identity": f"FM {freq_mhz:.1f} MHz",
            "frequency_hz": freq_hz,
            "frequency_mhz": round(freq_mhz, 1),
            "rssi_dbfs": station["power_dbfs"],
            "power_dbfs": station["power_dbfs"],
            "avg_power_dbfs": station["avg_power_dbfs"],
            "noise_dbfs": station["noise_dbfs"],
            "excess_db": station["excess_db"],
            "audio_rms": station["audio_rms"],
            "pilot_db": station["pilot_db"],
            "rds_subcarrier_db": station["rds_subcarrier_db"],
            "stereo_likely": station["stereo_likely"],
            "rds_likely": station["rds_likely"],
            "decode_status": status,
            "demod_good": station["demod_good"],
            "confidence": 0.86 if status == "station" else 0.42,
            "sightings": sightings,
            "seen_at": now,
            "last_activity_at": now,
            "last_demod_at": now,
            "source": "sdr_shark_fft_fm_demod",
            "demod_source": self._demod_source,
            "detail": "FM demod quality passed." if status == "station" else "FM-shaped carrier detected; demod quality has not confirmed it yet.",
            "playable": status == "station",
        }
        self._stations[freq_hz] = payload

    def _set_active(self, active, now):
        with self._lock:
            self._active = bool(active)
            if not self._active:
                self._iq_chunks = []
                self._iq_chunk_samples = 0
                self._iq_capture_key = None
                self._playback_freq_hz = None
                self._playback_demod = None
                self._playback_capture_key = None
                self._playback_accum.clear()
                self._drain_audio_queue_locked()
            self._expire_locked(now)

    def _expire(self, now):
        with self._lock:
            self._expire_locked(now)

    def _expire_locked(self, now):
        cutoff = now - self.retain_seconds
        self._stations = {
            freq_hz: station
            for freq_hz, station in self._stations.items()
            if float(station.get("seen_at", 0.0) or 0.0) >= cutoff
        }

    def snapshot(self, max_events=20):
        now = time.time()
        with self._lock:
            self._expire_locked(now)
            stations = sorted(
                self._stations.values(),
                key=lambda row: float(row.get("frequency_mhz", 0.0) or 0.0),
            )[:max(1, int(max_events))]
            confirmed = sum(1 for row in self._stations.values() if row.get("decode_status") == "station")
            potential = max(0, len(self._stations) - confirmed)
            return {
                "active": bool(self._active),
                "station_count": confirmed,
                "potential_count": potential,
                "events": [dict(station) for station in stations],
                "demod_source": self._demod_source,
                "interval_s": self.analyze_interval_s,
                "playing_frequency_mhz": round(self._playback_freq_hz / 1_000_000.0, 1) if self._playback_freq_hz else None,
                "playback_error": self._playback_error,
            }
