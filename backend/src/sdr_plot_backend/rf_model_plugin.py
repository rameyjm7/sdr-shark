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


DEFAULT_RF_INTELLIGENCE_ROOT = Path("/home/jake/workspace/SDR/rf-signal-intelligence-private")
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
        self._last_diagnostic: dict[str, Any] = {}
        self._native_event_offset = 0
        self._native_event_inode: tuple[int, int] | None = None
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
            "scan_rf_offsets": os.getenv("SDR_SHARK_RF_MODEL_SCAN_RF_OFFSETS", "0").strip().lower()
            not in {"0", "false", "no", "off"},
            "scan_rf_step_hz": max(
                250_000.0, float(os.getenv("SDR_SHARK_RF_MODEL_SCAN_RF_STEP_HZ", "2000000") or "2000000")
            ),
            "scan_capture_multiplier": max(
                1.0, float(os.getenv("SDR_SHARK_RF_MODEL_SCAN_CAPTURE_MULTIPLIER", "1") or "1")
            ),
            "allow_wideband_classification": os.getenv("SDR_SHARK_RF_MODEL_ALLOW_WIDEBAND", "1").strip().lower()
            not in {"0", "false", "no", "off"},
            "use_gateway_iq": os.getenv("SDR_SHARK_RF_MODEL_USE_GATEWAY_IQ", "0").strip().lower()
            in {"1", "true", "yes", "on"},
            "gateway_url": os.getenv("SDR_SHARK_RF_MODEL_GATEWAY_URL", "http://127.0.0.1:8080").strip(),
            "gateway_device_id": os.getenv("SDR_SHARK_RF_MODEL_GATEWAY_DEVICE_ID", "bladerf:0").strip(),
            "gateway_iq_format": os.getenv("SDR_SHARK_RF_MODEL_GATEWAY_IQ_FORMAT", "i8").strip().lower() or "i8",
            "gateway_lna_gain": int(os.getenv("SDR_SHARK_RF_MODEL_GATEWAY_LNA_GAIN", "24") or "24"),
            "gateway_vga_gain": int(os.getenv("SDR_SHARK_RF_MODEL_GATEWAY_VGA_GAIN", "20") or "20"),
            "gateway_capture_multiplier": max(
                1.0, float(os.getenv("SDR_SHARK_RF_MODEL_GATEWAY_CAPTURE_MULTIPLIER", "4") or "4")
            ),
            "scan_stride_samples": max(
                1, int(os.getenv("SDR_SHARK_RF_MODEL_SCAN_STRIDE_SAMPLES", "262144") or "262144")
            ),
            "submit_interval_floor_sec": max(
                0.0, float(os.getenv("SDR_SHARK_RF_MODEL_SUBMIT_INTERVAL_FLOOR_SEC", "0.02") or "0.02")
            ),
            "min_quality_factor": max(
                0.0, float(os.getenv("SDR_SHARK_RF_MODEL_MIN_QUALITY_FACTOR", "0.2") or "0.2")
            ),
            "min_snr_db": max(0.0, float(os.getenv("SDR_SHARK_RF_MODEL_MIN_SNR_DB", "5") or "5")),
            "min_signal_power_db": float(os.getenv("SDR_SHARK_RF_MODEL_MIN_SIGNAL_POWER_DB", "-45") or "-45"),
            "min_occupied_fraction": max(
                0.0, float(os.getenv("SDR_SHARK_RF_MODEL_MIN_OCCUPIED_FRACTION", "0.002") or "0.002")
            ),
            "target_guard_hz": 2_000_000.0,
            "target_guard_margin_db": 4.0,
            "rfuav_centers_path": os.getenv("SDR_SHARK_RFUAV_CENTERS_PATH", str(DEFAULT_RFUAV_CENTERS_PATH)),
            "rfuav_tolerance_hz": float(os.getenv("SDR_SHARK_RFUAV_TOLERANCE_HZ", "5000000") or 5_000_000.0),
            "debug_capture_dir": os.getenv("SDR_SHARK_RF_MODEL_DEBUG_CAPTURE_DIR", "").strip(),
            "native_event_jsonl": os.getenv("SDR_SHARK_RF_MODEL_EVENT_JSONL", "/var/log/rfiq/rfml-events.jsonl").strip(),
            "use_native_events": os.getenv("SDR_SHARK_RF_MODEL_USE_NATIVE_EVENTS", "1").strip().lower()
            not in {"0", "false", "no", "off"},
        }

    @staticmethod
    def _active_artifact(cfg: dict[str, Any]) -> tuple[str, str]:
        backend = str(cfg.get("backend", "auto") or "auto").strip().lower()
        engine_path = str(cfg.get("engine_path", "") or "")
        model_path = str(cfg.get("model_path", "") or "")
        if backend == "tensorrt" or (backend == "auto" and engine_path and Path(engine_path).expanduser().exists()):
            return "TensorRT engine", engine_path
        return "Keras model", model_path

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
        backend: str | None = None,
        engine_path: str | None = None,
    ) -> None:
        repo = str(repo_path or DEFAULT_RF_INTELLIGENCE_ROOT)
        model = str(model_path or DEFAULT_NOISY_DRONE_MODEL)
        selected_backend = str(backend or self._configured.get("backend", "auto") or "auto").strip().lower()
        engine = str(engine_path or self._configured.get("engine_path", DEFAULT_NOISY_DRONE_ENGINE))
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
                    "backend": selected_backend,
                    "engine_path": engine,
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

        if enabled and not self._native_events_enabled(self._config_snapshot()):
            self.start()
        else:
            self.stop()

    def submit_iq(self, samples: np.ndarray, *, center_freq_hz: float, sample_rate_hz: float) -> None:
        cfg = self._config_snapshot()
        if not cfg["enabled"]:
            return
        if self._native_events_enabled(cfg):
            self._ingest_native_events(cfg)
            self._set_status("native rfiq events")
            return
        target_in_passband = self._passband_contains_target(center_freq_hz, sample_rate_hz, cfg["target_freq_hz"])
        if not target_in_passband and not bool(cfg.get("auto_target")):
            self._set_status("waiting for target MHz")
            return
        if not target_in_passband:
            self._set_status("classifying tuned center")
        elif sample_rate_hz > (float(cfg["sample_rate_hz"]) * 1.05):
            if not bool(cfg.get("allow_wideband_classification")):
                self._set_status("waiting for native 20 MHz RFML IQ")
                return
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
        self._ingest_native_events(self._config_snapshot())
        acquired = self._lock.acquire(blocking=False)
        if not acquired:
            active_label, active_path = self._active_artifact(self._configured)
            return {
                "enabled": bool(self._configured.get("enabled")),
                "active": bool(self._thread is not None and self._thread.is_alive()),
                "model_loaded": bool(self._model is not None),
                "model_path": self._configured.get("model_path", ""),
                "model_backend": self._configured.get("backend", "auto"),
                "engine_path": self._configured.get("engine_path", ""),
                "active_model_label": active_label,
                "active_model_path": active_path,
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
            latest_debug = self._latest_debug_event(cfg)
            if latest_debug is not None:
                latest_seen = float(latest_debug.get("seen_at", 0.0) or 0.0)
                newest_event_seen = max(
                    [float(event.get("seen_at", 0.0) or 0.0) for event in events] or [0.0]
                )
                if latest_seen > newest_event_seen:
                    events.append(latest_debug)
            model_loaded = self._model is not None
            last_error = self._last_error
            last_label = self._last_label or str((latest_debug or {}).get("label") or "")
            last_confidence = self._last_confidence or float((latest_debug or {}).get("confidence") or 0.0)
            last_diagnostic = dict(self._last_diagnostic)
            last_inference = self._last_inference
        finally:
            self._lock.release()
        active_label, active_path = self._active_artifact(cfg)
        return {
            "enabled": bool(cfg["enabled"]),
            "active": bool(self._thread is not None and self._thread.is_alive()),
            "model_loaded": bool(model_loaded),
            "model_path": cfg["model_path"],
            "model_backend": cfg.get("backend", "auto"),
            "engine_path": cfg.get("engine_path", ""),
            "active_model_label": active_label,
            "active_model_path": active_path,
            "repo_path": cfg["repo_path"],
            "target_freq_hz": float(cfg["target_freq_hz"]),
            "target_mhz": float(cfg["target_freq_hz"]) / 1e6 if cfg["target_freq_hz"] else 0.0,
            "sample_rate_hz": float(cfg["sample_rate_hz"]),
            "bandwidth_mhz": float(cfg["sample_rate_hz"]) / 1e6 if cfg["sample_rate_hz"] else 0.0,
            "iq_source": "gateway" if bool(cfg.get("use_gateway_iq")) else "main",
            "native_event_jsonl": str(cfg.get("native_event_jsonl") or ""),
            "native_events": bool(self._native_events_enabled(cfg)),
            "confidence_threshold": float(cfg.get("confidence_threshold", 0.0) or 0.0),
            "event_count": len(events),
            "status": self._status,
            "pending_samples": int(self._pending_samples),
            "window_samples": int(cfg["window_samples"]),
            "last_error": last_error,
            "last_label": last_label,
            "last_confidence": last_confidence,
            "last_diagnostic": last_diagnostic,
            "events": events,
        }

    @staticmethod
    def _native_events_enabled(cfg: dict[str, Any]) -> bool:
        path = str(cfg.get("native_event_jsonl") or "").strip()
        return bool(cfg.get("use_native_events")) and bool(path)

    def _ingest_native_events(self, cfg: dict[str, Any]) -> None:
        if not self._native_events_enabled(cfg):
            return
        path = Path(str(cfg.get("native_event_jsonl") or "")).expanduser()
        try:
            stat = path.stat()
        except OSError:
            self._set_status("waiting for native RFML events")
            return
        inode = (int(stat.st_dev), int(stat.st_ino))
        if self._native_event_inode != inode or stat.st_size < self._native_event_offset:
            self._native_event_inode = inode
            self._native_event_offset = 0
        try:
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(self._native_event_offset)
                lines = handle.readlines()
                self._native_event_offset = handle.tell()
        except OSError as exc:
            self._set_status(f"native RFML unreadable: {exc}")
            return
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = self._native_event_to_card(payload, cfg)
            prediction = payload.get("prediction") or {}
            label = str(prediction.get("label") or "")
            confidence = float(prediction.get("confidence") or 0.0)
            with self._lock:
                self._last_error = ""
                self._last_diagnostic = {
                    "decision": str(payload.get("event_type") or "native_rfml"),
                    "label": label,
                    "confidence": confidence,
                    "source": "native_rfiq_jsonl",
                    "seen_at": time.time(),
                }
                if event is None:
                    self._status = "native RFML no alert"
                    self._last_label = ""
                    self._last_confidence = 0.0
                else:
                    self._status = "native RFML event"
                    self._last_label = str(event.get("label") or "")
                    self._last_confidence = float(event.get("confidence") or 0.0)
                    self._events.append(event)

    def _native_event_to_card(self, payload: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any] | None:
        prediction = payload.get("prediction") or {}
        source = payload.get("source") or {}
        capture_stats = payload.get("capture_stats") or {}
        timing = payload.get("timing_ms") or {}
        label = str(prediction.get("label") or "").strip()
        confidence = float(prediction.get("confidence") or 0.0)
        event_type = str(payload.get("event_type") or "")
        if not label or label.casefold() == "noise" or event_type.endswith(".no_alert"):
            return None
        if confidence < float(cfg.get("confidence_threshold", 0.0) or 0.0):
            return None
        center_hz = float(source.get("freq") or source.get("center_freq_hz") or 0.0)
        sample_rate_hz = float(source.get("sample_rate") or source.get("sample_rate_hz") or 0.0)
        top = list(prediction.get("top") or [])
        sequence = int(payload.get("sequence") or 0)
        return {
            "protocol": "rfml",
            "kind": "noisy_drone_classification",
            "identity": label,
            "label": label,
            "raw_label": str(prediction.get("raw_label") or label),
            "confidence": confidence,
            "raw_confidence": float(prediction.get("raw_confidence") or confidence),
            "target_mhz": center_hz / 1e6 if center_hz else 0.0,
            "configured_target_mhz": 0.0,
            "auto_target": True,
            "rf_offset_scan": False,
            "rf_candidate_count": int((payload.get("window") or {}).get("candidate_count") or 0),
            "rf_quality_factor": 1.0,
            "rf_quality_reason": "native rfiq TensorRT event",
            "center_freq_hz": center_hz,
            "sample_rate_hz": sample_rate_hz,
            "model_sample_rate_hz": sample_rate_hz,
            "window_start": int((payload.get("window") or {}).get("selected_start") or 0),
            "power_db": float(capture_stats.get("power_db") or -120.0),
            "peak": float(capture_stats.get("peak") or 0.0),
            "top": top,
            "decision": event_type or "native_rfiq_rfml",
            "native_sequence": sequence,
            "native_event_jsonl": str(cfg.get("native_event_jsonl") or ""),
            "timing_ms": timing,
            "seen_at": time.time(),
            "detail": (
                f"Native rfiq/TensorRT predicted {label} at {confidence * 100:.1f}% confidence"
                + (f" - {center_hz / 1e6:.3f} MHz" if center_hz else "")
            ),
        }

    def _latest_debug_event(self, cfg: dict[str, Any]) -> dict[str, Any] | None:
        directory = str(cfg.get("debug_capture_dir") or "").strip()
        if not directory:
            return None
        meta_path = Path(directory).expanduser() / "rfml_latest.json"
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        label = str(meta.get("label") or "").strip()
        if not label or label.casefold() == "noise":
            return None
        seen_at = float(meta.get("saved_at") or 0.0)
        if seen_at <= 0.0 or time.time() - seen_at > 180.0:
            return None
        confidence = float(meta.get("confidence") or 0.0)
        target_hz = float(meta.get("target_freq_hz") or 0.0)
        safe_label = "".join(ch for ch in label if ch.isalnum() or ch in {"-", "_"}) or "unknown"
        debug_capture = str(
            Path(directory).expanduser() / f"rfml_latest_{safe_label}_{target_hz / 1e6:.3f}MHz.npy"
        )
        return {
            "protocol": "rfml",
            "kind": "noisy_drone_classification",
            "identity": label,
            "label": label,
            "raw_label": label,
            "confidence": confidence,
            "raw_confidence": confidence,
            "target_mhz": float(meta.get("target_mhz") or (target_hz / 1e6 if target_hz else 0.0)),
            "configured_target_mhz": float(cfg.get("target_freq_hz", 0.0) or 0.0) / 1e6,
            "auto_target": True,
            "rf_offset_scan": False,
            "rf_candidate_count": 1,
            "rf_quality_factor": 1.0,
            "rf_quality_reason": "latest debug classification",
            "debug_capture": debug_capture,
            "center_freq_hz": target_hz,
            "sample_rate_hz": float(cfg.get("sample_rate_hz", 0.0) or 0.0),
            "model_sample_rate_hz": float(cfg.get("sample_rate_hz", 0.0) or 0.0),
            "power_db": -120.0,
            "peak": 0.0,
            "top": [{"label": label, "confidence": confidence}],
            "decision": "latest_debug_fallback",
            "seen_at": seen_at,
            "detail": f"NoisyDroneRF predicted {label} at {confidence * 100:.1f}% confidence",
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
        capture_stats = helpers["capture_stats"]
        frame_classifier_cls = helpers["NoisyDroneFrameClassifier"]
        frame_config_cls = helpers["NoisyDroneFrameConfig"]
        labels = list(helpers["LABEL_NAMES"])

        model_sample_rate_hz = float(cfg["sample_rate_hz"])
        if bool(cfg.get("use_gateway_iq")):
            self._classify_gateway_component(
                model,
                helpers,
                cfg,
                center_freq_hz=float(center_freq_hz),
                sample_rate_hz=float(sample_rate_hz),
            )
            return
        candidates = self._rf_target_candidates(cfg, center_freq_hz, sample_rate_hz)
        frame_config = frame_config_cls(
            window_samples=int(cfg["window_samples"]),
            nfft=int(cfg["nfft"]),
            hop=int(cfg["hop"]),
            time_bins=int(cfg["time_bins"]),
            scan_windows=bool(cfg.get("scan_windows")),
            scan_stride_samples=int(cfg.get("scan_stride_samples", 262144) or 262144),
            window_score_mode="raw",
            decision_mode="hybrid",
            non_noise_threshold=float(cfg["non_noise_threshold"]),
            top_k=3,
        )
        frame_classifier = frame_classifier_cls(
            lambda batch: model.predict(batch, verbose=0),
            labels=labels,
            config=frame_config,
        )
        best_result = None
        for candidate_target_freq_hz in candidates:
            model_iq, candidate_sample_rate_hz = self._channelize_for_model(
                raw,
                center_freq_hz=float(center_freq_hz),
                input_sample_rate_hz=float(sample_rate_hz),
                target_freq_hz=float(candidate_target_freq_hz),
                model_sample_rate_hz=model_sample_rate_hz,
            )
            iq = coerce_iq_array(model_iq)
            payload = frame_classifier.classify_iq(
                iq,
                target_label=None,
                signal_present=False,
            )
            start_idx = int((payload.get("scan") or {}).get("selected_start", 0) or 0)
            capture = iq[start_idx : start_idx + int(cfg["window_samples"])]
            if capture.shape[0] < int(cfg["window_samples"]):
                capture = np.pad(capture, ((0, int(cfg["window_samples"]) - capture.shape[0]), (0, 0)), mode="constant")
            raw_label = str(payload.get("raw_prediction") or payload.get("prediction") or "")
            raw_confidence = float(payload.get("raw_confidence") or payload.get("confidence") or 0.0)
            label_for_score = str(payload.get("prediction") or raw_label)
            confidence_for_score = float(payload.get("confidence") or 0.0)
            quality = self._wideband_quality_factor(capture, candidate_sample_rate_hz)
            score = (0.0 if label_for_score.casefold() == "noise" else confidence_for_score) * quality["factor"]
            if best_result is None or score > best_result[0]:
                best_result = (
                    score,
                    quality,
                    float(candidate_target_freq_hz),
                    float(candidate_sample_rate_hz),
                    capture,
                    int(start_idx),
                    raw_label,
                    float(raw_confidence),
                    payload,
                )
        if best_result is None:
            self._remember_diagnostic(
                {
                    "decision": "no_candidate_result",
                    "center_mhz": float(center_freq_hz) / 1e6 if center_freq_hz else 0.0,
                    "sample_rate_hz": float(sample_rate_hz),
                    "seen_at": time.time(),
                }
            )
            return
        (
            _score,
            quality,
            effective_target_freq_hz,
            effective_sample_rate_hz,
            capture,
            start_idx,
            raw_label,
            raw_confidence,
            payload,
        ) = best_result
        alignment = self._target_alignment_stats(capture, effective_sample_rate_hz)
        stats = capture_stats(capture)
        spectral_quality = self._spectral_quality(capture)
        label = str(payload.get("prediction") or raw_label)
        confidence = float(payload.get("confidence") or raw_confidence)
        decision = str(payload.get("decision") or "")
        top = list(payload.get("top") or [])
        if str(label).casefold() == "noise":
            with self._lock:
                self._last_error = ""
                self._status = "noise suppressed"
                self._last_label = ""
                self._last_confidence = 0.0
                self._last_diagnostic = self._diagnostic_payload(
                    decision="noise_suppressed",
                    label=label,
                    confidence=confidence,
                    raw_label=raw_label,
                    raw_confidence=raw_confidence,
                    target_freq_hz=effective_target_freq_hz,
                    center_freq_hz=center_freq_hz,
                    stats=stats,
                    quality=quality,
                    spectral_quality=spectral_quality,
                    top=top,
                )
            return
        min_signal_power_db = float(cfg.get("min_signal_power_db", -45.0) or -45.0)
        min_occupied = float(cfg.get("min_occupied_fraction", 0.002) or 0.002)
        power_db = float(stats.get("power_db", -120.0) or -120.0)
        occupied = float(spectral_quality.get("occupied_fraction", 0.0) or 0.0)
        if power_db < min_signal_power_db or occupied < min_occupied:
            with self._lock:
                self._last_error = ""
                self._status = (
                    "RFML suppressed: "
                    f"weak capture power={power_db:.1f}dB need>={min_signal_power_db:.1f}dB "
                    f"occupied={occupied:.4f} need>={min_occupied:.4f}"
                )
                self._last_label = ""
                self._last_confidence = 0.0
                self._last_diagnostic = self._diagnostic_payload(
                    decision="weak_capture_suppressed",
                    label=label,
                    confidence=confidence,
                    raw_label=raw_label,
                    raw_confidence=raw_confidence,
                    target_freq_hz=effective_target_freq_hz,
                    center_freq_hz=center_freq_hz,
                    stats=stats,
                    quality=quality,
                    spectral_quality=spectral_quality,
                    top=top,
                    gate={
                        "power_db": power_db,
                        "min_signal_power_db": min_signal_power_db,
                        "occupied_fraction": occupied,
                        "min_occupied_fraction": min_occupied,
                    },
                )
            return
        min_quality = float(cfg.get("min_quality_factor", 0.2) or 0.2)
        if float(quality.get("factor", 1.0)) < min_quality:
            with self._lock:
                self._last_error = ""
                self._status = f"low-quality RFML suppressed: {quality.get('reason', 'narrow capture')}"
                self._last_label = ""
                self._last_confidence = 0.0
                self._last_diagnostic = self._diagnostic_payload(
                    decision="low_quality_suppressed",
                    label=label,
                    confidence=confidence,
                    raw_label=raw_label,
                    raw_confidence=raw_confidence,
                    target_freq_hz=effective_target_freq_hz,
                    center_freq_hz=center_freq_hz,
                    stats=stats,
                    quality=quality,
                    spectral_quality=spectral_quality,
                    top=top,
                    gate={
                        "quality_factor": float(quality.get("factor", 1.0)),
                        "min_quality_factor": min_quality,
                        "reason": quality.get("reason", ""),
                    },
                )
            return
        debug_capture = self._save_debug_capture(
            capture,
            label=str(raw_label),
            confidence=float(raw_confidence),
            target_freq_hz=float(effective_target_freq_hz),
            cfg=cfg,
        )
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
            "rf_offset_scan": len(candidates) > 1,
            "rf_candidate_count": len(candidates),
            "rf_quality_factor": float(quality.get("factor", 1.0)),
            "rf_quality_reason": quality.get("reason", ""),
            "debug_capture": debug_capture,
            "center_freq_hz": float(center_freq_hz),
            "sample_rate_hz": float(sample_rate_hz),
            "model_sample_rate_hz": float(effective_sample_rate_hz),
            "window_start": int(start_idx),
            "power_db": float(stats.get("power_db", 0.0)),
            "peak": float(stats.get("peak", 0.0)),
            "peak_offset_hz": float(alignment.get("peak_offset_hz", 0.0)),
            "center_guard_db": float(alignment.get("center_guard_db", -120.0)),
            "off_center_db": float(alignment.get("off_center_db", -120.0)),
            "spectral_snr_db": float(spectral_quality.get("snr_db", 0.0)),
            "occupied_fraction": float(spectral_quality.get("occupied_fraction", 0.0)),
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
            self._last_diagnostic = self._diagnostic_payload(
                decision="classified",
                label=label,
                confidence=confidence,
                raw_label=raw_label,
                raw_confidence=raw_confidence,
                target_freq_hz=effective_target_freq_hz,
                center_freq_hz=center_freq_hz,
                stats=stats,
                quality=quality,
                spectral_quality=spectral_quality,
                top=top,
            )
            self._events.append(event)

    def _classify_gateway_component(
        self,
        model: Any,
        helpers: dict[str, Any],
        cfg: dict[str, Any],
        *,
        center_freq_hz: float,
        sample_rate_hz: float,
    ) -> None:
        gateway_component_cls = helpers.get("NoisyDroneGatewayClassifier")
        gateway_runtime_config_cls = helpers.get("NoisyDroneGatewayClassifierConfig")
        gateway_config_cls = helpers.get("GatewayStreamConfig")
        frame_config_cls = helpers.get("NoisyDroneFrameConfig")
        capture_stats = helpers["capture_stats"]
        labels = list(helpers["LABEL_NAMES"])
        if (
            gateway_component_cls is None
            or gateway_runtime_config_cls is None
            or gateway_config_cls is None
            or frame_config_cls is None
        ):
            self._set_status("gateway RFML component unavailable")
            return

        model_sample_rate_hz = float(cfg["sample_rate_hz"])
        target_freq_hz = self._effective_target_freq_hz(cfg, center_freq_hz, sample_rate_hz)
        capture_samples = int(
            max(4096, int(cfg.get("window_samples", 1_048_576) or 1_048_576))
            * float(cfg.get("gateway_capture_multiplier", 4.0) or 4.0)
        )
        stream_config = gateway_config_cls(
            base_url=str(cfg.get("gateway_url") or "http://127.0.0.1:8080"),
            device_id=str(cfg.get("gateway_device_id") or "bladerf:0"),
            center_freq_hz=int(round(float(target_freq_hz))),
            sample_rate_sps=int(round(model_sample_rate_hz)),
            bandwidth_hz=int(round(model_sample_rate_hz)),
            lna_gain_db=int(cfg.get("gateway_lna_gain", 24) or 24),
            vga_gain_db=int(cfg.get("gateway_vga_gain", 20) or 20),
            iq_format=str(cfg.get("gateway_iq_format") or "i8"),
        )
        frame_config = frame_config_cls(
            window_samples=int(cfg["window_samples"]),
            nfft=int(cfg["nfft"]),
            hop=int(cfg["hop"]),
            time_bins=int(cfg["time_bins"]),
            scan_windows=bool(cfg.get("scan_windows")),
            scan_stride_samples=int(cfg.get("scan_stride_samples", 262144) or 262144),
            window_score_mode="raw",
            decision_mode="hybrid",
            non_noise_threshold=float(cfg["non_noise_threshold"]),
            top_k=3,
            sample_rate_hz=model_sample_rate_hz,
        )
        runtime_config = gateway_runtime_config_cls(
            capture_samples=capture_samples,
            discard_captures=1,
            min_snr_db=float(cfg.get("min_snr_db", 5.0) or 5.0),
            min_occupied_fraction=float(cfg.get("min_occupied_fraction", 0.002) or 0.002),
            min_detection_confidence=float(cfg.get("confidence_threshold", 0.0) or 0.0),
        )
        classifier = gateway_component_cls(
            lambda batch: model.predict(batch, verbose=0),
            labels=labels,
            stream_config=stream_config,
            frame_config=frame_config,
            runtime_config=runtime_config,
        )
        self._set_status("classifying gateway RFML IQ")
        result = classifier.classify_once(target_label=None, signal_present=False)
        payload = result.payload
        capture = np.asarray(result.selected_iq, dtype=np.float32)
        raw_label = str(payload.get("raw_prediction") or payload.get("prediction") or "")
        raw_confidence = float(payload.get("raw_confidence") or payload.get("confidence") or 0.0)
        label = str(payload.get("prediction") or raw_label)
        confidence = float(payload.get("confidence") or raw_confidence)
        if label.casefold() == "noise":
            with self._lock:
                self._last_error = ""
                self._status = str(payload.get("decision") or "noise suppressed")
                self._last_label = ""
                self._last_confidence = 0.0
                self._last_diagnostic = {
                    "decision": str(payload.get("decision") or "noise_suppressed"),
                    "label": label,
                    "confidence": confidence,
                    "raw_label": raw_label,
                    "raw_confidence": raw_confidence,
                    "target_mhz": float(target_freq_hz) / 1e6 if target_freq_hz else 0.0,
                    "center_mhz": float(center_freq_hz) / 1e6 if center_freq_hz else 0.0,
                    "quality": dict(payload.get("quality") or {}),
                    "top": list(payload.get("top") or []),
                    "gate": {
                        "snr": payload.get("snr_gate") or {},
                        "power": payload.get("power_gate") or {},
                        "confidence": payload.get("detection_confidence_gate") or {},
                    },
                    "seen_at": time.time(),
                }
            return

        stats = capture_stats(capture)
        spectral_quality = dict(payload.get("quality") or {})
        alignment = self._target_alignment_stats(capture, model_sample_rate_hz)
        debug_capture = self._save_debug_capture(
            capture,
            label=raw_label,
            confidence=raw_confidence,
            target_freq_hz=float(target_freq_hz),
            cfg=cfg,
        )
        rfuav_evidence = self._rfuav_frequency_evidence(
            label,
            target_freq_hz=float(target_freq_hz),
            cfg=cfg,
        )
        scan = payload.get("scan") or {}
        event = {
            "protocol": "rfml",
            "kind": "noisy_drone_classification",
            "identity": label,
            "label": label,
            "raw_label": raw_label,
            "confidence": confidence,
            "raw_confidence": raw_confidence,
            "target_mhz": float(target_freq_hz) / 1e6 if target_freq_hz else 0.0,
            "configured_target_mhz": float(cfg["target_freq_hz"]) / 1e6 if cfg["target_freq_hz"] else 0.0,
            "auto_target": bool(float(target_freq_hz) != float(cfg["target_freq_hz"])),
            "rf_offset_scan": False,
            "rf_candidate_count": len(scan.get("candidates") or []) or 1,
            "rf_quality_factor": 1.0,
            "rf_quality_reason": "gateway component",
            "debug_capture": debug_capture,
            "center_freq_hz": float(target_freq_hz),
            "sample_rate_hz": model_sample_rate_hz,
            "model_sample_rate_hz": model_sample_rate_hz,
            "window_start": int(scan.get("selected_start", 0) or 0),
            "power_db": float(stats.get("power_db", 0.0)),
            "peak": float(stats.get("peak", 0.0)),
            "peak_offset_hz": float(alignment.get("peak_offset_hz", 0.0)),
            "center_guard_db": float(alignment.get("center_guard_db", -120.0)),
            "off_center_db": float(alignment.get("off_center_db", -120.0)),
            "spectral_snr_db": float(spectral_quality.get("snr_db", 0.0)),
            "occupied_fraction": float(spectral_quality.get("occupied_fraction", 0.0)),
            "top": list(payload.get("top") or []),
            "decision": str(payload.get("decision") or ""),
            "seen_at": time.time(),
            "rfuav": rfuav_evidence,
            "rfuav_frequency_status": rfuav_evidence.get("status", "metadata_unavailable"),
            "rfuav_frequency_match": bool(rfuav_evidence.get("frequency_match")),
            "rfuav_label_match": bool(rfuav_evidence.get("label_match")),
            "component": payload.get("component") or {},
            "gate": {
                "snr": payload.get("snr_gate") or {},
                "power": payload.get("power_gate") or {},
                "confidence": payload.get("detection_confidence_gate") or {},
            },
            "detail": self._rfuav_detail(
                label=label,
                confidence=confidence,
                target_freq_hz=float(target_freq_hz),
                rfuav=rfuav_evidence,
            ),
        }
        with self._lock:
            self._last_error = ""
            self._status = "gateway component classified"
            self._last_label = label
            self._last_confidence = confidence
            self._last_diagnostic = {
                "decision": "gateway_component_classified",
                "label": label,
                "confidence": confidence,
                "raw_label": raw_label,
                "raw_confidence": raw_confidence,
                "target_mhz": float(target_freq_hz) / 1e6 if target_freq_hz else 0.0,
                "center_mhz": float(center_freq_hz) / 1e6 if center_freq_hz else 0.0,
                "quality": spectral_quality,
                "top": list(payload.get("top") or []),
                "gate": event["gate"],
                "seen_at": time.time(),
            }
            self._events.append(event)

    def _remember_diagnostic(self, diagnostic: dict[str, Any]) -> None:
        with self._lock:
            self._last_diagnostic = dict(diagnostic)

    @staticmethod
    def _diagnostic_payload(
        *,
        decision: str,
        label: str,
        confidence: float,
        raw_label: str,
        raw_confidence: float,
        target_freq_hz: float,
        center_freq_hz: float,
        stats: dict[str, Any],
        quality: dict[str, Any],
        spectral_quality: dict[str, Any],
        top: list[Any],
        gate: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "decision": decision,
            "label": str(label),
            "confidence": float(confidence),
            "raw_label": str(raw_label),
            "raw_confidence": float(raw_confidence),
            "target_mhz": float(target_freq_hz) / 1e6 if target_freq_hz else 0.0,
            "center_mhz": float(center_freq_hz) / 1e6 if center_freq_hz else 0.0,
            "power_db": float(stats.get("power_db", -120.0) or -120.0),
            "peak": float(stats.get("peak", 0.0) or 0.0),
            "quality": {
                "factor": float(quality.get("factor", 1.0) or 1.0),
                "reason": str(quality.get("reason", "") or ""),
                "peak_offset_hz": float(quality.get("peak_offset_hz", 0.0) or 0.0),
                "occupied_fraction": float(quality.get("occupied_fraction", 0.0) or 0.0),
                "peak_over_median_db": float(quality.get("peak_over_median_db", 0.0) or 0.0),
            },
            "spectral": {
                "snr_db": float(spectral_quality.get("snr_db", 0.0) or 0.0),
                "occupied_fraction": float(spectral_quality.get("occupied_fraction", 0.0) or 0.0),
                "peak_over_floor_db": float(spectral_quality.get("peak_over_floor_db", 0.0) or 0.0),
            },
            "top": list(top or []),
            "gate": dict(gate or {}),
            "seen_at": time.time(),
        }

    def _save_debug_capture(
        self,
        capture: np.ndarray,
        *,
        label: str,
        confidence: float,
        target_freq_hz: float,
        cfg: dict[str, Any],
    ) -> str:
        directory = str(cfg.get("debug_capture_dir") or "").strip()
        if not directory:
            return ""
        try:
            path = Path(directory).expanduser()
            path.mkdir(parents=True, exist_ok=True)
            safe_label = "".join(ch for ch in str(label) if ch.isalnum() or ch in {"-", "_"}) or "unknown"
            filename = f"rfml_latest_{safe_label}_{target_freq_hz / 1e6:.3f}MHz.npy"
            target = path / filename
            np.save(target, np.asarray(capture, dtype=np.float32))
            meta = {
                "label": label,
                "confidence": float(confidence),
                "target_freq_hz": float(target_freq_hz),
                "target_mhz": float(target_freq_hz) / 1e6,
                "saved_at": time.time(),
                "shape": list(np.asarray(capture).shape),
            }
            (path / "rfml_latest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
            return str(target)
        except Exception:
            return ""

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
                candidate = self._suppress_narrow_peak(iq[start : start + window_samples])
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
        capture = self._suppress_narrow_peak(capture)
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

    def _suppress_narrow_peak(self, samples: np.ndarray) -> np.ndarray:
        complex_iq = self._complex_iq(samples)
        if complex_iq.size < 2048:
            return samples
        power = np.square(np.abs(complex_iq)).astype(np.float64)
        if not np.isfinite(power).all() or float(np.mean(power)) <= 0.0:
            return samples

        spectrum = np.fft.fftshift(np.fft.fft(complex_iq))
        mag_db = 20.0 * np.log10(np.abs(spectrum).astype(np.float64) + 1e-12)
        median_db = float(np.median(mag_db))
        peak_idx = int(np.argmax(mag_db))
        peak_over_median = float(mag_db[peak_idx] - median_db)
        occupied = float(np.mean(mag_db > (median_db + 10.0)))
        if peak_over_median < 24.0 or occupied > 0.08:
            return samples

        # Remove only the narrow carrier-like component. The drone waveform is
        # broadband enough that a tiny spectral notch is less harmful than
        # letting a center spike dominate the image classifier.
        notch_bins = max(3, min(64, complex_iq.size // 4096))
        start = max(0, peak_idx - notch_bins)
        stop = min(spectrum.size, peak_idx + notch_bins + 1)
        spectrum[start:stop] = 0.0
        cleaned = np.fft.ifft(np.fft.ifftshift(spectrum)).astype(np.complex64)
        if np.asarray(samples).ndim == 2:
            return np.stack([cleaned.real, cleaned.imag], axis=-1).astype(np.float32)
        return cleaned

    def _wideband_quality_factor(self, capture: np.ndarray, sample_rate_hz: float) -> dict[str, Any]:
        complex_iq = self._complex_iq(capture)
        if complex_iq.size < 256:
            return {"factor": 1.0, "reason": ""}
        nfft = int(min(8192, 2 ** int(np.floor(np.log2(complex_iq.size)))))
        nfft = max(256, nfft)
        segment = complex_iq[-nfft:]
        spectrum = np.fft.fftshift(np.fft.fft(segment * np.hanning(nfft).astype(np.float32), n=nfft))
        power_db = 20.0 * np.log10(np.abs(spectrum).astype(np.float64) + 1e-12)
        freqs = np.fft.fftshift(np.fft.fftfreq(nfft, d=1.0 / max(1.0, float(sample_rate_hz))))
        peak_idx = int(np.argmax(power_db))
        peak_db = float(power_db[peak_idx])
        median_db = float(np.median(power_db))
        peak_offset = float(freqs[peak_idx])
        occupied = float(np.mean(power_db > (median_db + 10.0)))
        factor = 1.0
        reasons = []
        if abs(peak_offset) > float(sample_rate_hz) * 0.42:
            factor *= 0.05
            reasons.append("edge peak")
        if peak_db > median_db + 28.0 and occupied < 0.03:
            factor *= 0.05
            reasons.append("narrow peak")
        return {
            "factor": float(factor),
            "reason": ", ".join(reasons),
            "peak_offset_hz": peak_offset,
            "occupied_fraction": occupied,
            "peak_over_median_db": float(peak_db - median_db),
        }

    def _spectral_quality(self, capture: np.ndarray) -> dict[str, float]:
        complex_iq = self._complex_iq(capture)
        if complex_iq.size < 1024:
            return {
                "snr_db": 0.0,
                "peak_over_floor_db": 0.0,
                "occupied_fraction": 0.0,
            }
        nfft = int(min(16384, 2 ** int(np.floor(np.log2(complex_iq.size)))))
        nfft = max(1024, nfft)
        segment = complex_iq[-nfft:]
        spectrum = np.fft.fftshift(np.fft.fft(segment * np.hanning(nfft).astype(np.float32), n=nfft))
        power_db = 20.0 * np.log10(np.abs(spectrum).astype(np.float64) + 1e-12)
        floor_db = float(np.median(power_db))
        peak_db = float(np.percentile(power_db, 99.7))
        return {
            "snr_db": float(peak_db - floor_db),
            "peak_over_floor_db": float(np.max(power_db) - floor_db),
            "occupied_fraction": float(np.mean(power_db > floor_db + 10.0)),
        }

    def _classification_score(self, probs: np.ndarray, labels: list[str], best_non_noise_prediction: Any) -> float:
        if best_non_noise_prediction is not None:
            label, confidence = best_non_noise_prediction(probs, labels)
            return 0.0 if str(label).casefold() == "noise" else float(confidence)
        ranking = np.argsort(probs)[::-1]
        for idx in ranking:
            label = labels[int(idx)] if int(idx) < len(labels) else ""
            if str(label).casefold() != "noise":
                return float(probs[int(idx)])
        return 0.0

    def _capture_gateway_iq(
        self,
        cfg: dict[str, Any],
        *,
        target_freq_hz: float,
        sample_rate_hz: float,
        helpers: dict[str, Any],
    ) -> np.ndarray | None:
        gateway_config_cls = helpers.get("GatewayStreamConfig")
        gateway_source_cls = helpers.get("GatewayIqSource")
        if gateway_config_cls is None or gateway_source_cls is None:
            self._set_status("gateway RFML helper unavailable")
            return None
        capture_samples = int(
            max(4096, int(cfg.get("window_samples", 1_048_576) or 1_048_576))
            * float(cfg.get("gateway_capture_multiplier", 4.0) or 4.0)
        )
        try:
            stream_config = gateway_config_cls(
                base_url=str(cfg.get("gateway_url") or "http://127.0.0.1:8080"),
                device_id=str(cfg.get("gateway_device_id") or "bladerf:0"),
                center_freq_hz=int(round(float(target_freq_hz))),
                sample_rate_sps=int(round(float(sample_rate_hz))),
                bandwidth_hz=int(round(float(sample_rate_hz))),
                lna_gain_db=int(cfg.get("gateway_lna_gain", 24) or 24),
                vga_gain_db=int(cfg.get("gateway_vga_gain", 20) or 20),
                iq_format=str(cfg.get("gateway_iq_format") or "i8"),
            )
            with gateway_source_cls(stream_config) as source:
                # Match the validated CLI path: drop one full capture so stale
                # websocket backlog does not leak into RFML decisions.
                source.read_iq_pairs(capture_samples)
                return source.read_iq_pairs(capture_samples)
        except Exception as exc:
            self._set_status(f"gateway RFML unavailable: {exc}")
            return None

    def _effective_target_freq_hz(self, cfg: dict[str, Any], center_freq_hz: float, sample_rate_hz: float) -> float:
        configured = float(cfg.get("target_freq_hz", 0.0) or 0.0)
        if configured > 0.0 and self._passband_contains_target(center_freq_hz, sample_rate_hz, configured):
            return configured
        if bool(cfg.get("auto_target")):
            return float(center_freq_hz)
        return configured

    def _rf_target_candidates(self, cfg: dict[str, Any], center_freq_hz: float, sample_rate_hz: float) -> list[float]:
        base = self._effective_target_freq_hz(cfg, center_freq_hz, sample_rate_hz)
        input_rate = max(1.0, float(sample_rate_hz))
        model_rate = max(1.0, float(cfg.get("sample_rate_hz", 20_000_000.0) or 20_000_000.0))
        if not bool(cfg.get("scan_rf_offsets")) or input_rate <= model_rate * 1.05:
            return [float(base)]
        half_span = max(0.0, (input_rate - model_rate) / 2.0)
        if half_span <= 0.0:
            return [float(base)]
        step = max(250_000.0, float(cfg.get("scan_rf_step_hz", 2_000_000.0) or 2_000_000.0))
        offsets = np.arange(-half_span, half_span + step * 0.5, step, dtype=np.float64)
        candidates = [float(center_freq_hz + offset) for offset in offsets]
        if all(abs(candidate - base) > step * 0.25 for candidate in candidates):
            candidates.append(float(base))
        candidates = [
            candidate
            for candidate in candidates
            if self._passband_contains_target(center_freq_hz, sample_rate_hz, candidate)
        ]
        return sorted(set(round(candidate, 3) for candidate in candidates)) or [float(base)]

    @staticmethod
    def _complex_iq(samples: np.ndarray) -> np.ndarray:
        array = np.asarray(samples)
        if array.ndim == 2 and array.shape[1] >= 2:
            return (array[:, 0].astype(np.float32) + 1j * array[:, 1].astype(np.float32)).astype(np.complex64)
        return np.asarray(samples, dtype=np.complex64).reshape(-1)

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

    def _target_alignment_stats(self, samples: np.ndarray, sample_rate_hz: float) -> dict[str, float]:
        complex_iq = self._complex_iq(samples)
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
            f"{target_mhz:.3f} MHz tuned" if target_mhz else "",
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
        framing_module = importlib.import_module("rf_signal_intelligence.noisy_drone_framing")
        gateway_module = importlib.import_module("rf_signal_intelligence.gateway_iq")
        try:
            gateway_component_module = importlib.import_module("rf_signal_intelligence.noisy_drone_gateway_component")
        except ModuleNotFoundError:
            # The gateway component is optional. Native Soapy/rfiq IQ paths use
            # the shared framer directly and should not fail because the legacy
            # gateway-only wrapper is not installed.
            gateway_component_module = None

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
            "NoisyDroneFrameClassifier": getattr(framing_module, "NoisyDroneFrameClassifier"),
            "NoisyDroneFrameConfig": getattr(framing_module, "NoisyDroneFrameConfig"),
            "GatewayIqSource": getattr(gateway_module, "GatewayIqSource"),
            "GatewayStreamConfig": getattr(gateway_module, "GatewayStreamConfig"),
            "NoisyDroneGatewayClassifier": (
                getattr(gateway_component_module, "NoisyDroneGatewayClassifier", None)
                if gateway_component_module is not None
                else None
            ),
            "NoisyDroneGatewayClassifierConfig": (
                getattr(gateway_component_module, "NoisyDroneGatewayClassifierConfig", None)
                if gateway_component_module is not None
                else None
            ),
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
