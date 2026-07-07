import importlib
import json
import os
import tempfile
import sys
import threading
import time
import zipfile
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
DEFAULT_NOISY_DRONE_ENGINE = (
    DEFAULT_RF_INTELLIGENCE_ROOT
    / "models"
    / "noisy_drone_rf_v2"
    / "noisy_drone_rf_v2_vgg_full_complex_spectrogram_fp16.engine"
)
DEFAULT_NOISY_DRONE_LABELS = (
    DEFAULT_RF_INTELLIGENCE_ROOT
    / "models"
    / "noisy_drone_rf_v2"
    / "labels.json"
)
DEFAULT_RFUAV_CENTERS_PATH = Path(__file__).resolve().parent / "data" / "rfuav_centers.json"


class _TensorRtPredictAdapter:
    """Expose the TensorRT runner through the Keras-like predict() API."""

    def __init__(self, runner: Any) -> None:
        self._runner = runner

    def predict(self, x: np.ndarray, verbose: int = 0) -> np.ndarray:
        batch = np.asarray(x, dtype=np.float32)
        if batch.ndim == 3:
            batch = batch[None, ...]
        outputs = [self._runner.infer(batch[idx : idx + 1])[0] for idx in range(batch.shape[0])]
        return np.stack(outputs, axis=0).astype(np.float32, copy=False)


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
        self._rfuav_db: dict[str, Any] | None = None
        self._rfuav_db_path = ""
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
            "backend": os.getenv("SDR_SHARK_RF_MODEL_BACKEND", "auto").strip().lower() or "auto",
            "engine_path": os.getenv("SDR_SHARK_RF_MODEL_ENGINE_PATH", str(DEFAULT_NOISY_DRONE_ENGINE)),
            "labels_path": os.getenv("SDR_SHARK_RF_MODEL_LABELS_PATH", str(DEFAULT_NOISY_DRONE_LABELS)),
            "target_freq_hz": 2_399_000_000.0,
            "sample_rate_hz": 20_000_000.0,
            "interval_sec": 1.0,
            "window_samples": 1_048_576,
            "nfft": 1024,
            "hop": 1024,
            "time_bins": 1024,
            "confidence_threshold": 0.45,
            "non_noise_threshold": 0.55,
            "auto_target": os.getenv("SDR_SHARK_RF_MODEL_AUTO_TARGET", "1").strip().lower()
            not in {"0", "false", "no", "off"},
            "scan_windows": os.getenv("SDR_SHARK_RF_MODEL_SCAN_WINDOWS", "1").strip().lower()
            not in {"0", "false", "no", "off"},
            "scan_capture_multiplier": max(
                1.0, float(os.getenv("SDR_SHARK_RF_MODEL_SCAN_CAPTURE_MULTIPLIER", "2") or "2")
            ),
            "scan_stride_samples": max(
                1, int(os.getenv("SDR_SHARK_RF_MODEL_SCAN_STRIDE_SAMPLES", "262144") or "262144")
            ),
            "submit_interval_floor_sec": max(
                0.0, float(os.getenv("SDR_SHARK_RF_MODEL_SUBMIT_INTERVAL_FLOOR_SEC", "0.02") or "0.02")
            ),
            "target_guard_hz": 2_000_000.0,
            "target_guard_margin_db": 4.0,
            "rfuav_centers_path": os.getenv("SDR_SHARK_RFUAV_CENTERS_PATH", str(DEFAULT_RFUAV_CENTERS_PATH)),
            "rfuav_tolerance_hz": float(os.getenv("SDR_SHARK_RFUAV_TOLERANCE_HZ", "5000000") or 5_000_000.0),
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
            previous_key = self._model_config_key()
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
            next_key = self._model_config_key()
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
        target_in_passband = self._passband_contains_target(center_freq_hz, sample_rate_hz, cfg["target_freq_hz"])
        if not target_in_passband and not bool(cfg.get("auto_target")):
            self._set_status("waiting for target MHz")
            return
        if not target_in_passband:
            self._set_status("classifying tuned center")
        elif sample_rate_hz > (float(cfg["sample_rate_hz"]) * 1.05):
            self._set_status("channelizing wide IQ")
        else:
            self._set_status("collecting IQ")
        now = time.monotonic()
        submit_interval = max(
            float(cfg.get("submit_interval_floor_sec", 0.02) or 0.02),
            float(cfg["interval_sec"]) / 32.0,
        )
        if now - self._last_submit < submit_interval:
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
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            return {
                "enabled": bool(self._configured.get("enabled")),
                "active": bool(self._thread is not None and self._thread.is_alive()),
                "model_loaded": bool(self._model is not None),
                "model_path": self._configured.get("model_path", ""),
                "model_backend": self._configured.get("backend", "auto"),
                "engine_path": self._configured.get("engine_path", ""),
                "repo_path": self._configured.get("repo_path", ""),
                "target_freq_hz": float(self._configured.get("target_freq_hz", 0.0) or 0.0),
                "target_mhz": float(self._configured.get("target_freq_hz", 0.0) or 0.0) / 1e6,
                "sample_rate_hz": float(self._configured.get("sample_rate_hz", 0.0) or 0.0),
                "bandwidth_mhz": float(self._configured.get("sample_rate_hz", 0.0) or 0.0) / 1e6,
                "event_count": 0,
                "status": "busy",
                "pending_samples": int(self._pending_samples),
                "window_samples": int(self._configured.get("window_samples", 0) or 0),
                "last_error": "",
                "last_label": "",
                "last_confidence": 0.0,
                "events": [],
            }
        try:
            cfg = dict(self._configured)
            events = list(self._events)[-max(1, int(max_events)):]
            model_loaded = self._model is not None
            last_error = self._last_error
            last_label = self._last_label
            last_confidence = self._last_confidence
            last_inference = self._last_inference
        finally:
            self._lock.release()
        return {
            "enabled": bool(cfg["enabled"]),
            "active": bool(self._thread is not None and self._thread.is_alive()),
            "model_loaded": bool(model_loaded),
            "model_path": cfg["model_path"],
            "model_backend": cfg.get("backend", "auto"),
            "engine_path": cfg.get("engine_path", ""),
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
            model_window_samples = max(4096, input_window_samples)
            capture_samples = model_window_samples
            if bool(cfg.get("scan_windows")):
                capture_samples = int(round(capture_samples * float(cfg.get("scan_capture_multiplier", 1.0) or 1.0)))
            max_pending = max(model_window_samples * 2, capture_samples)
            while pending_samples > max_pending and pending:
                removed = pending.pop(0)
                pending_samples -= int(removed.size)

            now = time.monotonic()
            if pending_samples < capture_samples:
                if input_rate > model_rate * 1.05:
                    self._set_status(f"collecting wide IQ {pending_samples}/{capture_samples}")
                else:
                    self._set_status(f"collecting IQ {pending_samples}/{capture_samples}")
                continue
            if now - self._last_inference < float(cfg["interval_sec"]):
                continue

            raw = np.concatenate(pending, axis=0).astype(np.complex64, copy=False)
            if raw.size > max_pending:
                raw = raw[-max_pending:]
            classify_window = raw[-capture_samples:]
            pending = [classify_window]
            pending_samples = int(pending[0].size)
            self._last_inference = now
            try:
                self._set_status("classifying")
                self._classify(classify_window, center_freq_hz=last_center, sample_rate_hz=last_rate, cfg=cfg)
            except Exception as exc:
                self._set_error(str(exc))

    def _classify(self, raw: np.ndarray, *, center_freq_hz: float, sample_rate_hz: float, cfg: dict[str, Any]) -> None:
        model, helpers = self._load_model_and_helpers(cfg["repo_path"], cfg["model_path"])
        coerce_iq_array = helpers["coerce_iq_array"]
        select_high_power_window = helpers["select_high_power_window"]
        classify_iq = helpers["classify_iq"]
        choose_final_prediction = helpers["choose_final_prediction"]
        capture_stats = helpers["capture_stats"]
        candidate_window_starts = helpers.get("candidate_window_starts")
        best_non_noise_prediction = helpers.get("best_non_noise_prediction")
        labels = list(helpers["LABEL_NAMES"])

        model_sample_rate_hz = float(cfg["sample_rate_hz"])
        effective_target_freq_hz = self._effective_target_freq_hz(cfg, center_freq_hz, sample_rate_hz)
        model_iq, effective_sample_rate_hz = self._channelize_for_model(
            raw,
            center_freq_hz=float(center_freq_hz),
            input_sample_rate_hz=float(sample_rate_hz),
            target_freq_hz=float(effective_target_freq_hz),
            model_sample_rate_hz=model_sample_rate_hz,
        )
        iq = coerce_iq_array(model_iq)
        capture, start_idx, raw_label, raw_confidence, probs = self._select_and_classify_window(
            iq,
            model=model,
            classify_iq=classify_iq,
            select_high_power_window=select_high_power_window,
            candidate_window_starts=candidate_window_starts,
            best_non_noise_prediction=best_non_noise_prediction,
            labels=labels,
            cfg=cfg,
        )
        alignment = self._target_alignment_stats(capture, effective_sample_rate_hz)
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
        rfuav_evidence = self._rfuav_frequency_evidence(
            str(label),
            target_freq_hz=float(effective_target_freq_hz),
            cfg=cfg,
        )
        event = {
            "protocol": "rfml",
            "kind": "noisy_drone_classification",
            "identity": str(label),
            "label": label,
            "raw_label": raw_label,
            "confidence": float(confidence),
            "raw_confidence": float(raw_confidence),
            "target_mhz": float(effective_target_freq_hz) / 1e6 if effective_target_freq_hz else 0.0,
            "configured_target_mhz": float(cfg["target_freq_hz"]) / 1e6 if cfg["target_freq_hz"] else 0.0,
            "auto_target": bool(effective_target_freq_hz != float(cfg["target_freq_hz"])),
            "center_freq_hz": float(center_freq_hz),
            "sample_rate_hz": float(sample_rate_hz),
            "model_sample_rate_hz": float(effective_sample_rate_hz),
            "window_start": int(start_idx),
            "power_db": float(stats.get("power_db", 0.0)),
            "peak": float(stats.get("peak", 0.0)),
            "peak_offset_hz": float(alignment.get("peak_offset_hz", 0.0)),
            "center_guard_db": float(alignment.get("center_guard_db", -120.0)),
            "off_center_db": float(alignment.get("off_center_db", -120.0)),
            "top": top,
            "decision": decision or "",
            "seen_at": time.time(),
            "rfuav": rfuav_evidence,
            "rfuav_frequency_status": rfuav_evidence.get("status", "metadata_unavailable"),
            "rfuav_frequency_match": bool(rfuav_evidence.get("frequency_match")),
            "rfuav_label_match": bool(rfuav_evidence.get("label_match")),
            "detail": self._rfuav_detail(
                label=str(label),
                confidence=float(confidence),
                target_freq_hz=float(effective_target_freq_hz),
                rfuav=rfuav_evidence,
            ),
        }
        with self._lock:
            self._last_error = ""
            self._last_label = label
            self._last_confidence = float(confidence)
            if float(confidence) >= float(cfg["confidence_threshold"]):
                self._events.append(event)

    def _select_and_classify_window(
        self,
        iq: np.ndarray,
        *,
        model: Any,
        classify_iq: Any,
        select_high_power_window: Any,
        candidate_window_starts: Any,
        best_non_noise_prediction: Any,
        labels: list[str],
        cfg: dict[str, Any],
    ) -> tuple[np.ndarray, int, str, float, np.ndarray]:
        window_samples = int(cfg["window_samples"])
        if (
            bool(cfg.get("scan_windows"))
            and candidate_window_starts is not None
            and best_non_noise_prediction is not None
            and len(iq) > window_samples
        ):
            best = None
            for start in candidate_window_starts(
                len(iq),
                window_samples,
                int(cfg.get("scan_stride_samples", 262144) or 262144),
            ):
                candidate = iq[start : start + window_samples]
                raw_label, raw_confidence, probs = classify_iq(
                    model,
                    candidate,
                    nfft=int(cfg["nfft"]),
                    hop=int(cfg["hop"]),
                    time_bins=int(cfg["time_bins"]),
                    labels=labels,
                    phase_tta=1,
                )
                non_noise_label, non_noise_confidence = best_non_noise_prediction(probs, labels)
                score = float(non_noise_confidence)
                if str(non_noise_label).casefold() == "noise":
                    score = 0.0
                if best is None or score > best[0]:
                    best = (score, int(start), candidate, raw_label, raw_confidence, probs)
            if best is not None:
                _score, start_idx, capture, raw_label, raw_confidence, probs = best
                return capture, start_idx, raw_label, float(raw_confidence), probs

        capture, start_idx = select_high_power_window(
            iq,
            window_samples=window_samples,
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
        return capture, int(start_idx), raw_label, float(raw_confidence), probs

    def _effective_target_freq_hz(self, cfg: dict[str, Any], center_freq_hz: float, sample_rate_hz: float) -> float:
        configured = float(cfg.get("target_freq_hz", 0.0) or 0.0)
        if configured > 0.0 and self._passband_contains_target(center_freq_hz, sample_rate_hz, configured):
            return configured
        if bool(cfg.get("auto_target")):
            return float(center_freq_hz)
        return configured

    @staticmethod
    def _should_suppress_rfuav_mismatch(rfuav: dict[str, Any]) -> bool:
        status = str(rfuav.get("status") or "")
        if status in {"metadata_unavailable", "class_and_frequency"}:
            return False
        return not bool(rfuav.get("frequency_match"))

    @staticmethod
    def _rfuav_suppression_status(label: str, rfuav: dict[str, Any]) -> str:
        status = str(rfuav.get("status") or "metadata_unavailable")
        nearest = rfuav.get("nearest_label_center") or rfuav.get("nearest_known_center") or {}
        nearest_mhz = nearest.get("center_frequency_mhz")
        offset_mhz = nearest.get("offset_mhz")
        if nearest_mhz is not None and offset_mhz is not None:
            return f"suppressed {label} {status} nearest {float(nearest_mhz):.1f} MHz ({float(offset_mhz):.1f} away)"
        return f"suppressed {label} {status}"

    @staticmethod
    def _target_alignment_stats(samples: np.ndarray, sample_rate_hz: float) -> dict[str, float]:
        array = np.asarray(samples)
        if array.ndim == 2 and array.shape[1] >= 2:
            complex_iq = (array[:, 0].astype(np.float32) + 1j * array[:, 1].astype(np.float32)).astype(np.complex64)
        else:
            complex_iq = np.asarray(samples, dtype=np.complex64).reshape(-1)
        if complex_iq.size < 32:
            return {
                "peak_offset_hz": 0.0,
                "center_guard_db": -120.0,
                "off_center_db": -120.0,
            }
        nfft = int(min(16384, 2 ** int(np.floor(np.log2(complex_iq.size)))))
        nfft = max(32, nfft)
        segment = complex_iq[-nfft:]
        window = np.hanning(nfft).astype(np.float32)
        spectrum = np.fft.fftshift(np.fft.fft(segment * window, n=nfft))
        power = np.square(np.abs(spectrum)).astype(np.float64) + 1e-18
        freqs = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / max(1.0, float(sample_rate_hz))))
        power_db = 10.0 * np.log10(power)
        peak_idx = int(np.argmax(power))
        center_mask = np.abs(freqs) <= 1_000_000.0
        off_mask = np.abs(freqs) >= 2_000_000.0
        center_guard_db = float(np.max(power_db[center_mask])) if np.any(center_mask) else -120.0
        off_center_db = float(np.max(power_db[off_mask])) if np.any(off_mask) else -120.0
        return {
            "peak_offset_hz": float(freqs[peak_idx]),
            "center_guard_db": center_guard_db,
            "off_center_db": off_center_db,
        }

    @staticmethod
    def _should_suppress_off_target(alignment: dict[str, float], cfg: dict[str, Any]) -> bool:
        peak_offset_hz = abs(float(alignment.get("peak_offset_hz", 0.0)))
        center_db = float(alignment.get("center_guard_db", -120.0))
        off_db = float(alignment.get("off_center_db", -120.0))
        guard_hz = float(cfg.get("target_guard_hz", 2_000_000.0) or 2_000_000.0)
        margin_db = float(cfg.get("target_guard_margin_db", 4.0) or 4.0)
        return peak_offset_hz > guard_hz and off_db > center_db + margin_db

    def _rfuav_frequency_evidence(
        self,
        label: str,
        *,
        target_freq_hz: float,
        cfg: dict[str, Any],
    ) -> dict[str, Any]:
        db = self._load_rfuav_db(str(cfg.get("rfuav_centers_path") or ""))
        if not db:
            return {
                "status": "metadata_unavailable",
                "label_match": False,
                "frequency_match": False,
                "tolerance_hz": float(cfg.get("rfuav_tolerance_hz", 5_000_000.0) or 5_000_000.0),
            }

        tolerance_hz = float(cfg.get("rfuav_tolerance_hz", db.get("default_tolerance_hz", 5_000_000.0)) or 5_000_000.0)
        target = float(target_freq_hz or 0.0)
        label_centers = list(self._rfuav_label_centers(db, label))
        all_centers = list(db.get("centers") or [])
        nearest_label = self._nearest_rfuav_center(label_centers, target)
        nearest_global = self._nearest_rfuav_center(all_centers, target)
        label_match = bool(label_centers)
        label_frequency_match = bool(nearest_label and nearest_label.get("offset_hz", float("inf")) <= tolerance_hz)
        global_frequency_match = bool(nearest_global and nearest_global.get("offset_hz", float("inf")) <= tolerance_hz)

        if label_frequency_match:
            status = "class_and_frequency"
        elif label_match:
            status = "classification_only"
        elif global_frequency_match:
            status = "known_center_other_family"
        else:
            status = "metadata_no_label"

        matched = nearest_label if label_frequency_match else (nearest_global if global_frequency_match else None)
        return {
            "status": status,
            "label": str(label),
            "label_match": label_match,
            "frequency_match": label_frequency_match,
            "known_center_match": global_frequency_match,
            "target_frequency_hz": target,
            "target_mhz": target / 1e6 if target else 0.0,
            "tolerance_hz": tolerance_hz,
            "nearest_label_center": nearest_label,
            "nearest_known_center": nearest_global,
            "matched_center": matched,
            "label_centers_mhz": [
                float(center.get("center_frequency_mhz", 0.0))
                for center in label_centers
                if center.get("center_frequency_mhz") is not None
            ],
        }

    def _load_rfuav_db(self, path: str) -> dict[str, Any] | None:
        db_path = str(Path(path or DEFAULT_RFUAV_CENTERS_PATH).expanduser())
        with self._lock:
            if self._rfuav_db is not None and self._rfuav_db_path == db_path:
                return self._rfuav_db
        try:
            loaded = json.loads(Path(db_path).read_text())
        except Exception:
            loaded = None
        with self._lock:
            self._rfuav_db = loaded if isinstance(loaded, dict) else None
            self._rfuav_db_path = db_path
            return self._rfuav_db

    @classmethod
    def _rfuav_label_centers(cls, db: dict[str, Any], label: str) -> list[dict[str, Any]]:
        by_label = db.get("by_label") or {}
        label_text = str(label or "").strip()
        candidates = [
            label_text,
            label_text.replace(" ", ""),
            label_text.replace("-", ""),
            label_text.upper(),
        ]
        for candidate in candidates:
            if candidate in by_label:
                return list(by_label.get(candidate) or [])

        family = cls._rfuav_family_for_label(label_text)
        by_family = db.get("by_family") or {}
        return list(by_family.get(family) or []) if family else []

    @staticmethod
    def _rfuav_family_for_label(label: str) -> str:
        normalized = "".join(ch for ch in str(label or "").upper() if ch.isalnum())
        if normalized.startswith("DJI"):
            return "DJI"
        if normalized.startswith("FUTABA"):
            return "FUTABA"
        if normalized.startswith("FLYSKY"):
            return "FLYSKY"
        if normalized.startswith("RADIOMASTER"):
            return "RADIOMASTER"
        if normalized.startswith("RADIOLINK"):
            return "RADIOLINK"
        if normalized.startswith("SIYI"):
            return "SIYI"
        if normalized.startswith("SKYDROID"):
            return "SKYDROID"
        if normalized.startswith("WFLY"):
            return "WFLY"
        if normalized.startswith("YUNZHUO"):
            return "YUNZHUO"
        if normalized.startswith("JUMPER"):
            return "JUMPER"
        if normalized.startswith("JRPROPO"):
            return "JRPROPO"
        return ""

    @staticmethod
    def _nearest_rfuav_center(centers: list[dict[str, Any]], target_freq_hz: float) -> dict[str, Any] | None:
        target = float(target_freq_hz or 0.0)
        if target <= 0.0 or not centers:
            return None
        best = None
        best_offset = float("inf")
        for center in centers:
            center_hz = float(center.get("center_frequency_hz") or 0.0)
            if center_hz <= 0.0:
                continue
            offset_hz = abs(center_hz - target)
            if offset_hz < best_offset:
                best_offset = offset_hz
                best = {
                    "center_frequency_hz": center_hz,
                    "center_frequency_mhz": float(center.get("center_frequency_mhz") or center_hz / 1e6),
                    "offset_hz": float(offset_hz),
                    "offset_mhz": float(offset_hz / 1e6),
                    "clip_count": int(center.get("clip_count") or 0),
                    "drones": list(center.get("drones") or [])[:8],
                    "families": list(center.get("families") or [])[:8],
                }
        return best

    @staticmethod
    def _rfuav_detail(*, label: str, confidence: float, target_freq_hz: float, rfuav: dict[str, Any]) -> str:
        target_mhz = float(target_freq_hz or 0.0) / 1e6
        parts = [
            f"NoisyDroneRF predicted {label} at {confidence * 100:.1f}% confidence",
            f"{target_mhz:.3f} MHz target" if target_mhz else "",
        ]
        status = str(rfuav.get("status") or "")
        nearest_label = rfuav.get("nearest_label_center") or {}
        nearest_known = rfuav.get("nearest_known_center") or {}
        if status == "class_and_frequency" and nearest_label:
            parts.append(
                f"RFUAV center match {float(nearest_label.get('center_frequency_mhz', 0.0)):.1f} MHz"
            )
        elif status == "classification_only":
            if nearest_label:
                parts.append(
                    f"RFUAV class match only; nearest {float(nearest_label.get('center_frequency_mhz', 0.0)):.1f} MHz is {float(nearest_label.get('offset_mhz', 0.0)):.1f} MHz away"
                )
            else:
                parts.append("RFUAV class match only; no center match nearby")
        elif status == "known_center_other_family" and nearest_known:
            parts.append(
                f"near RFUAV center {float(nearest_known.get('center_frequency_mhz', 0.0)):.1f} MHz, but not this classifier family"
            )
        elif status == "metadata_no_label":
            parts.append("RFUAV metadata has no matching family for this classifier label")
        elif status == "metadata_unavailable":
            parts.append("RFUAV metadata unavailable")
        return " · ".join(part for part in parts if part)

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
            return self._boxcar_decimate(samples, rounded_ratio), input_rate / rounded_ratio

        return self._resample_linear(samples, input_rate, model_rate), model_rate

    @staticmethod
    def _boxcar_decimate(samples: np.ndarray, decimation: int) -> np.ndarray:
        decimation = max(1, int(decimation))
        if decimation <= 1:
            return samples.astype(np.complex64, copy=False)
        usable = (samples.size // decimation) * decimation
        if usable < decimation:
            return samples.astype(np.complex64, copy=False)
        trimmed = samples[-usable:]
        # The RFML model only needs a target-centered 20 MHz view. After mixing
        # to baseband, a short boxcar keeps this path much cheaper than a full
        # FFT channelizer and avoids starving the live UI.
        return trimmed.reshape(-1, decimation).mean(axis=1).astype(np.complex64)

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
        key = self._model_config_key()
        with self._lock:
            if self._model is not None and self._active_config == key and self._helpers:
                return self._model, self._helpers

        repo = Path(repo_path).expanduser()
        src = repo / "src"
        if not src.exists():
            raise FileNotFoundError(f"RF signal intelligence src path not found: {src}")
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        if str(src) not in sys.path:
            sys.path.insert(0, str(src))

        os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
        cfg = self._config_snapshot()
        backend = str(cfg.get("backend") or "auto").strip().lower()
        engine_file = Path(str(cfg.get("engine_path") or DEFAULT_NOISY_DRONE_ENGINE)).expanduser()
        labels_file = Path(str(cfg.get("labels_path") or DEFAULT_NOISY_DRONE_LABELS)).expanduser()
        use_tensorrt = backend == "tensorrt" or (backend == "auto" and engine_file.exists())
        if use_tensorrt:
            os.environ.setdefault("CUDA_VISIBLE_DEVICES", "0")
        else:
            os.environ.setdefault("KERAS_BACKEND", "tensorflow")
            os.environ.setdefault("CUDA_VISIBLE_DEVICES", "-1")
        module = importlib.import_module("rf_signal_intelligence.live_noisy_drone_rf_classifier")

        if use_tensorrt:
            if not engine_file.exists():
                raise FileNotFoundError(f"NoisyDrone TensorRT engine file not found: {engine_file}")
            self._set_status("loading TensorRT engine")
            runner_module = importlib.import_module("deploy.run_tensorrt_engine_inference")
            runner = runner_module.TensorRtRunner(engine_file)
            model = _TensorRtPredictAdapter(runner)
            labels = self._load_labels(labels_file, default=list(getattr(module, "LABEL_NAMES")))
            ready_status = "TensorRT ready"
        else:
            try:
                from keras.models import load_model  # type: ignore
            except Exception:
                from tensorflow.keras.models import load_model

            model_file = Path(model_path).expanduser()
            if not model_file.exists():
                raise FileNotFoundError(f"NoisyDrone model file not found: {model_file}")
            self._set_status("loading model")
            try:
                model = load_model(model_file, compile=False)
            except TypeError as exc:
                if "quantization_config" not in str(exc):
                    raise
                model = load_model(self._keras3_compat_model_path(model_file), compile=False)
            labels = list(getattr(module, "LABEL_NAMES"))
            ready_status = "model ready"
        helpers = {
            "LABEL_NAMES": labels,
            "coerce_iq_array": getattr(module, "coerce_iq_array"),
            "select_high_power_window": getattr(module, "select_high_power_window"),
            "classify_iq": getattr(module, "classify_iq"),
            "choose_final_prediction": getattr(module, "choose_final_prediction"),
            "capture_stats": getattr(module, "capture_stats"),
            "candidate_window_starts": getattr(module, "candidate_window_starts", None),
            "best_non_noise_prediction": getattr(module, "best_non_noise_prediction", None),
        }
        with self._lock:
            self._model = model
            self._helpers = helpers
            self._active_config = key
            self._last_error = ""
            self._status = ready_status
        return model, helpers

    def _model_config_key(self) -> tuple[str, str, str, str, str]:
        return (
            str(self._configured.get("repo_path", "")),
            str(self._configured.get("model_path", "")),
            str(self._configured.get("backend", "auto")),
            str(self._configured.get("engine_path", "")),
            str(self._configured.get("labels_path", "")),
        )

    @staticmethod
    def _load_labels(labels_file: Path, *, default: list[str]) -> list[str]:
        try:
            loaded = json.loads(Path(labels_file).expanduser().read_text(encoding="utf-8"))
        except Exception:
            return list(default)
        if isinstance(loaded, list) and all(isinstance(item, str) for item in loaded):
            return list(loaded)
        return list(default)

    def _keras3_compat_model_path(self, model_file: Path) -> Path:
        """Create a Keras 3.12-compatible copy of newer .keras archives."""
        source = Path(model_file).expanduser()
        cache_dir = Path(tempfile.gettempdir()) / "sdr-shark-rfml"
        cache_dir.mkdir(parents=True, exist_ok=True)
        target = cache_dir / f"{source.stem}.keras3-compat-{int(source.stat().st_mtime)}.keras"
        if target.exists() and target.stat().st_size > 0:
            return target

        def strip_incompatible_config(value):
            if isinstance(value, dict):
                return {
                    key: strip_incompatible_config(item)
                    for key, item in value.items()
                    if key != "quantization_config"
                }
            if isinstance(value, list):
                return [strip_incompatible_config(item) for item in value]
            return value

        with zipfile.ZipFile(source, "r") as src_zip, zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as dst_zip:
            for info in src_zip.infolist():
                payload = src_zip.read(info.filename)
                if info.filename == "config.json":
                    config = json.loads(payload.decode("utf-8"))
                    payload = json.dumps(strip_incompatible_config(config), separators=(",", ":")).encode("utf-8")
                dst_zip.writestr(info, payload)
        return target

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
