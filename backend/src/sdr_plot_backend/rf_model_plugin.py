import importlib
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any

import numpy as np


DEFAULT_RF_INTELLIGENCE_ROOT = Path("/home/jake/workspace/SDR/rf-signal-intelligence")
DEFAULT_NOISY_DRONE_MODEL = (
    DEFAULT_RF_INTELLIGENCE_ROOT
    / "models"
    / "noisy_drone_rf_v2"
    / "noisy_drone_rf_v2_vgg_full_complex_spectrogram_best.keras"
)


class NoisyDroneModelPlugin:
    """Run the NoisyDroneRF model on SDR-Shark IQ without blocking the FFT loop."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queue: Queue[tuple[np.ndarray, float, float]] = Queue(maxsize=4)
        self._events: deque[dict[str, Any]] = deque(maxlen=200)
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._model = None
        self._helpers: dict[str, Any] = {}
        self._active_config: tuple[str, str] | None = None
        self._last_submit = 0.0
        self._last_inference = 0.0
        self._last_error = ""
        self._status = "idle"
        self._pending_samples = 0
        self._last_label = ""
        self._last_confidence = 0.0
        self._configured = {
            "enabled": False,
            "repo_path": str(DEFAULT_RF_INTELLIGENCE_ROOT),
            "model_path": str(DEFAULT_NOISY_DRONE_MODEL),
            "target_freq_hz": 2_399_000_000.0,
            "sample_rate_hz": 20_000_000.0,
            "interval_sec": 1.0,
            "window_samples": 262_144,
            "nfft": 1024,
            "hop": 1024,
            "time_bins": 1024,
            "confidence_threshold": 0.45,
            "non_noise_threshold": 0.55,
        }

    def configure(
        self,
        *,
        enabled: bool,
        repo_path: str | None,
        model_path: str | None,
        target_freq_hz: float | None,
        sample_rate_hz: float | None,
        interval_sec: float | None,
        confidence_threshold: float | None,
    ) -> None:
        repo = str(repo_path or DEFAULT_RF_INTELLIGENCE_ROOT)
        model = str(model_path or DEFAULT_NOISY_DRONE_MODEL)
        target = float(target_freq_hz or 0.0)
        model_rate = max(1.0, float(sample_rate_hz or 20_000_000.0))
        interval = max(0.25, float(interval_sec or 1.0))
        threshold = max(0.0, min(1.0, float(confidence_threshold if confidence_threshold is not None else 0.45)))
        with self._lock:
            previous_key = (self._configured["repo_path"], self._configured["model_path"])
            self._configured.update(
                {
                    "enabled": bool(enabled),
                    "repo_path": repo,
                    "model_path": model,
                    "target_freq_hz": target,
                    "sample_rate_hz": model_rate,
                    "interval_sec": interval,
                    "confidence_threshold": threshold,
                }
            )
            next_key = (repo, model)
            if previous_key != next_key:
                self._model = None
                self._helpers = {}
                self._active_config = None
                self._last_error = ""
                self._last_label = ""
                self._last_confidence = 0.0
                self._status = "configured"
                self._drain_queue()

        if enabled:
            self.start()
        else:
            self.stop()

    def submit_iq(self, samples: np.ndarray, *, center_freq_hz: float, sample_rate_hz: float) -> None:
        cfg = self._config_snapshot()
        if not cfg["enabled"]:
            return
        if not self._passband_contains_target(center_freq_hz, sample_rate_hz, cfg["target_freq_hz"]):
            self._set_status("waiting for target MHz")
            return
        if sample_rate_hz > (float(cfg["sample_rate_hz"]) * 1.05):
            self._set_status("channelizing wide IQ")
        else:
            self._set_status("collecting IQ")
        now = time.monotonic()
        if now - self._last_submit < max(0.05, float(cfg["interval_sec"]) / 8.0):
            return
        self._last_submit = now
        try:
            chunk = np.asarray(samples, dtype=np.complex64).copy()
            self._queue.put_nowait((chunk, float(center_freq_hz), float(sample_rate_hz)))
        except Full:
            return
        except Exception as exc:
            self._set_error(str(exc))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="noisy-drone-model-classifier", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=0.75)
        self._thread = None
        self._drain_queue()

    def snapshot(self, max_events: int = 20) -> dict[str, Any]:
        with self._lock:
            cfg = dict(self._configured)
            events = list(self._events)[-max(1, int(max_events)):]
            model_loaded = self._model is not None
            last_error = self._last_error
            last_label = self._last_label
            last_confidence = self._last_confidence
            last_inference = self._last_inference
        return {
            "enabled": bool(cfg["enabled"]),
            "active": bool(self._thread is not None and self._thread.is_alive()),
            "model_loaded": bool(model_loaded),
            "model_path": cfg["model_path"],
            "repo_path": cfg["repo_path"],
            "target_freq_hz": float(cfg["target_freq_hz"]),
            "target_mhz": float(cfg["target_freq_hz"]) / 1e6 if cfg["target_freq_hz"] else 0.0,
            "sample_rate_hz": float(cfg["sample_rate_hz"]),
            "bandwidth_mhz": float(cfg["sample_rate_hz"]) / 1e6 if cfg["sample_rate_hz"] else 0.0,
            "event_count": len(events),
            "status": self._status,
            "pending_samples": int(self._pending_samples),
            "window_samples": int(cfg["window_samples"]),
            "last_error": last_error,
            "last_label": last_label,
            "last_confidence": last_confidence,
            "events": events,
        }

    def _run(self) -> None:
        pending: list[np.ndarray] = []
        pending_samples = 0
        last_center = 0.0
        last_rate = 0.0
        while not self._stop.is_set():
            try:
                chunk, center_hz, sample_rate_hz = self._queue.get(timeout=0.25)
            except Empty:
                continue

            cfg = self._config_snapshot()
            if not cfg["enabled"]:
                pending.clear()
                pending_samples = 0
                continue
            pending.append(chunk)
            pending_samples += int(chunk.size)
            with self._lock:
                self._pending_samples = int(pending_samples)
            last_center = float(center_hz)
            last_rate = float(sample_rate_hz)
            model_rate = max(1.0, float(cfg["sample_rate_hz"]))
            input_rate = max(1.0, float(last_rate))
            input_window_samples = int(np.ceil(max(4096, int(cfg["window_samples"])) * input_rate / model_rate))
            window_samples = max(4096, input_window_samples)
            max_pending = window_samples * 2
            while pending_samples > max_pending and pending:
                removed = pending.pop(0)
                pending_samples -= int(removed.size)

            now = time.monotonic()
            if pending_samples < window_samples:
                if input_rate > model_rate * 1.05:
                    self._set_status(f"collecting wide IQ {pending_samples}/{window_samples}")
                else:
                    self._set_status(f"collecting IQ {pending_samples}/{window_samples}")
                continue
            if now - self._last_inference < float(cfg["interval_sec"]):
                continue

            raw = np.concatenate(pending, axis=0).astype(np.complex64, copy=False)
            if raw.size > max_pending:
                raw = raw[-max_pending:]
            pending = [raw[-window_samples:]]
            pending_samples = int(pending[0].size)
            self._last_inference = now
            try:
                self._set_status("classifying")
                self._classify(raw, center_freq_hz=last_center, sample_rate_hz=last_rate, cfg=cfg)
            except Exception as exc:
                self._set_error(str(exc))

    def _classify(self, raw: np.ndarray, *, center_freq_hz: float, sample_rate_hz: float, cfg: dict[str, Any]) -> None:
        model, helpers = self._load_model_and_helpers(cfg["repo_path"], cfg["model_path"])
        coerce_iq_array = helpers["coerce_iq_array"]
        select_high_power_window = helpers["select_high_power_window"]
        classify_iq = helpers["classify_iq"]
        choose_final_prediction = helpers["choose_final_prediction"]
        capture_stats = helpers["capture_stats"]
        labels = list(helpers["LABEL_NAMES"])

        model_sample_rate_hz = float(cfg["sample_rate_hz"])
        model_iq, effective_sample_rate_hz = self._channelize_for_model(
            raw,
            center_freq_hz=float(center_freq_hz),
            input_sample_rate_hz=float(sample_rate_hz),
            target_freq_hz=float(cfg["target_freq_hz"]),
            model_sample_rate_hz=model_sample_rate_hz,
        )
        iq = coerce_iq_array(model_iq)
        capture, start_idx = select_high_power_window(
            iq,
            window_samples=int(cfg["window_samples"]),
            smooth_samples=512,
        )
        raw_label, raw_confidence, probs = classify_iq(
            model,
            capture,
            nfft=int(cfg["nfft"]),
            hop=int(cfg["hop"]),
            time_bins=int(cfg["time_bins"]),
            labels=labels,
            phase_tta=1,
        )
        stats = capture_stats(capture)
        label, confidence, decision = choose_final_prediction(
            raw_label,
            raw_confidence,
            probs,
            labels,
            decision_mode="hybrid",
            non_noise_threshold=float(cfg["non_noise_threshold"]),
            signal_present=True,
            target_label=None,
        )
        ranking = np.argsort(probs)[::-1][:3]
        top = [{"label": labels[int(idx)], "confidence": float(probs[int(idx)])} for idx in ranking]
        if str(label).casefold() == "noise":
            with self._lock:
                self._last_error = ""
                self._status = "noise suppressed"
                self._last_label = ""
                self._last_confidence = 0.0
            return
        event = {
            "protocol": "rfml",
            "kind": "noisy_drone_classification",
            "identity": str(label),
            "label": label,
            "raw_label": raw_label,
            "confidence": float(confidence),
            "raw_confidence": float(raw_confidence),
            "target_mhz": float(cfg["target_freq_hz"]) / 1e6 if cfg["target_freq_hz"] else 0.0,
            "center_freq_hz": float(center_freq_hz),
            "sample_rate_hz": float(sample_rate_hz),
            "model_sample_rate_hz": float(effective_sample_rate_hz),
            "window_start": int(start_idx),
            "power_db": float(stats.get("power_db", 0.0)),
            "peak": float(stats.get("peak", 0.0)),
            "top": top,
            "decision": decision or "",
            "seen_at": time.time(),
            "detail": f"NoisyDroneRF live model predicted {label} at {float(confidence) * 100:.1f}% confidence.",
        }
        with self._lock:
            self._last_error = ""
            self._last_label = label
            self._last_confidence = float(confidence)
            if float(confidence) >= float(cfg["confidence_threshold"]):
                self._events.append(event)

    def _channelize_for_model(
        self,
        raw: np.ndarray,
        *,
        center_freq_hz: float,
        input_sample_rate_hz: float,
        target_freq_hz: float,
        model_sample_rate_hz: float,
    ) -> tuple[np.ndarray, float]:
        samples = np.asarray(raw, dtype=np.complex64)
        if samples.size == 0:
            return samples, model_sample_rate_hz

        input_rate = max(1.0, float(input_sample_rate_hz))
        model_rate = max(1.0, float(model_sample_rate_hz))
        target_offset_hz = float(target_freq_hz) - float(center_freq_hz)

        if abs(target_offset_hz) > input_rate / 2.0:
            return samples, input_rate

        if abs(target_offset_hz) > 1.0:
            n = np.arange(samples.size, dtype=np.float64)
            mixer = np.exp(-2j * np.pi * (target_offset_hz / input_rate) * n).astype(np.complex64)
            samples = samples * mixer

        if input_rate <= model_rate * 1.05:
            if abs(input_rate - model_rate) <= max(100_000.0, model_rate * 0.05):
                return samples, input_rate
            return self._resample_linear(samples, input_rate, model_rate), model_rate

        ratio = input_rate / model_rate
        rounded_ratio = int(round(ratio))
        if rounded_ratio >= 2 and abs(ratio - rounded_ratio) <= 0.05:
            return self._fft_channel_decimate(samples, rounded_ratio), input_rate / rounded_ratio

        return self._resample_linear(samples, input_rate, model_rate), model_rate

    @staticmethod
    def _fft_channel_decimate(samples: np.ndarray, decimation: int) -> np.ndarray:
        decimation = max(1, int(decimation))
        if decimation <= 1:
            return samples.astype(np.complex64, copy=False)
        usable = (samples.size // decimation) * decimation
        if usable < decimation:
            return samples.astype(np.complex64, copy=False)
        trimmed = samples[-usable:]
        spectrum = np.fft.fftshift(np.fft.fft(trimmed))
        out_len = max(1, usable // decimation)
        center = usable // 2
        half = out_len // 2
        start = max(0, center - half)
        stop = min(usable, start + out_len)
        channel = spectrum[start:stop]
        if channel.size < out_len:
            channel = np.pad(channel, (0, out_len - channel.size), mode="constant")
        return np.fft.ifft(np.fft.ifftshift(channel)).astype(np.complex64)

    @staticmethod
    def _resample_linear(samples: np.ndarray, input_rate: float, output_rate: float) -> np.ndarray:
        if samples.size == 0:
            return samples.astype(np.complex64, copy=False)
        output_len = max(1, int(round(samples.size * float(output_rate) / max(1.0, float(input_rate)))))
        if output_len == samples.size:
            return samples.astype(np.complex64, copy=False)
        src_x = np.linspace(0.0, 1.0, samples.size, endpoint=False, dtype=np.float64)
        dst_x = np.linspace(0.0, 1.0, output_len, endpoint=False, dtype=np.float64)
        real = np.interp(dst_x, src_x, samples.real).astype(np.float32)
        imag = np.interp(dst_x, src_x, samples.imag).astype(np.float32)
        return (real + 1j * imag).astype(np.complex64)

    def _load_model_and_helpers(self, repo_path: str, model_path: str):
        key = (str(repo_path), str(model_path))
        with self._lock:
            if self._model is not None and self._active_config == key and self._helpers:
                return self._model, self._helpers

        repo = Path(repo_path).expanduser()
        src = repo / "src"
        if not src.exists():
            raise FileNotFoundError(f"RF signal intelligence src path not found: {src}")
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))

        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
        os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
        module = importlib.import_module("rf_signal_intelligence.live_noisy_drone_rf_classifier")
        from tensorflow.keras.models import load_model

        model_file = Path(model_path).expanduser()
        if not model_file.exists():
            raise FileNotFoundError(f"NoisyDrone model file not found: {model_file}")
        self._set_status("loading model")
        model = load_model(model_file, compile=False)
        helpers = {
            "LABEL_NAMES": list(getattr(module, "LABEL_NAMES")),
            "coerce_iq_array": getattr(module, "coerce_iq_array"),
            "select_high_power_window": getattr(module, "select_high_power_window"),
            "classify_iq": getattr(module, "classify_iq"),
            "choose_final_prediction": getattr(module, "choose_final_prediction"),
            "capture_stats": getattr(module, "capture_stats"),
        }
        with self._lock:
            self._model = model
            self._helpers = helpers
            self._active_config = key
            self._last_error = ""
            self._status = "model ready"
        return model, helpers

    def _config_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._configured)

    def _set_error(self, message: str) -> None:
        with self._lock:
            self._last_error = message
            self._status = "error"

    def _set_status(self, status: str) -> None:
        with self._lock:
            if not self._last_error:
                self._status = str(status)

    def _drain_queue(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                return

    @staticmethod
    def _passband_contains_target(center_freq_hz: float, sample_rate_hz: float, target_freq_hz: float) -> bool:
        if target_freq_hz <= 0:
            return True
        half = max(1.0, float(sample_rate_hz) / 2.0)
        return abs(float(target_freq_hz) - float(center_freq_hz)) <= half
