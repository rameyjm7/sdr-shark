import threading
import json
import os
from sdr_plot_backend.sdr_generic import SDRGeneric
import numpy as np
from sdr_plot_backend.classifier import SignalClassifier


class SdrSettings:
    
    def __init__(self, name):
        self.name = name
        self.frequency = 102.1e6  # Center frequency in Hz
        self.sampleRate = 16e6   # Sample rate in Hz
        self.bandwidth = 16e6
        self.gain = 30            # Gain in dB
        self.averagingCount = 20
        pass

class sdr_scheduler_config:

    def __init__(self) -> None:
        home_dir = os.path.expanduser("~")
        self.app_root = os.getenv("SDR_SHARK_APP_ROOT", os.path.join(home_dir, ".sdr-shark"))
        self.settings_file = os.getenv(
            "SDR_SHARK_SETTINGS_FILE",
            os.path.join(self.app_root, "configurations", "sdr_scheduler_config.json"),
        )
        self.sdr_settings = {
            "hackrf" : SdrSettings("hackrf"),
            "sidekiq" : SdrSettings("sidekiq"),
            "antsdre200" : SdrSettings("antsdre200"),
            "rtlsdr_worker" : SdrSettings("rtlsdr_worker"),
        }
        self.sdr_name = "sidekiq"
        # Default settings
        self.sdr_settings['hackrf'].frequency = 102.1e6  # Center frequency in Hz
        self.sdr_settings['hackrf'].bandwidth = 20e6     # Bandwidth in Hz
        self.sdr_settings['hackrf'].sampleRate = 20e6    # Sample Rate in Hz
        self.sdr_settings['hackrf'].gain = 30
        self.sdr_settings['hackrf'].averagingCount = 20
        
        self.sdr_settings['sidekiq'].frequency = 102.1e6  # Center frequency in Hz
        self.sdr_settings['sidekiq'].bandwidth = 60e6     # Bandwidth in Hz
        self.sdr_settings['sidekiq'].sampleRate = 60e6    # Sample Rate in Hz
        self.sdr_settings['sidekiq'].gain = 30
        self.sdr_settings['sidekiq'].averagingCount = 20

        self.sdr_settings['antsdre200'].frequency = 102.1e6
        self.sdr_settings['antsdre200'].bandwidth = 20e6
        self.sdr_settings['antsdre200'].sampleRate = 20e6
        self.sdr_settings['antsdre200'].gain = 30
        self.sdr_settings['antsdre200'].averagingCount = 20

        self.sdr_settings['rtlsdr_worker'].frequency = 102.1e6
        self.sdr_settings['rtlsdr_worker'].bandwidth = 2.4e6
        self.sdr_settings['rtlsdr_worker'].sampleRate = 2.4e6
        self.sdr_settings['rtlsdr_worker'].gain = 30
        self.sdr_settings['rtlsdr_worker'].averagingCount = 10
        self.tasks = []
        self.task_lock = threading.Lock()
        self.sleeptime = 0.01
        self.sample_size = 8 * 1024  # Adjust sample size to receive more data
        self.peak_threshold_minimum_dB = 3 
        self.sweep_settings = {
            'frequency_start': 700e6,
            'frequency_stop': 820e6,
            'bandwidth': 20e6
        }
        self.sweeping_enabled = False
        self.dc_suppress = True
        self.show_waterfall = True
        self.decoders_always_enabled = False
        self.rf_model_classifier_enabled = False
        self.rf_model_classifier_repo_path = os.getenv(
            "SDR_SHARK_RF_MODEL_REPO",
            "/home/jake/workspace/SDR/rf-signal-intelligence",
        )
        self.rf_model_classifier_model_path = os.getenv(
            "SDR_SHARK_RF_MODEL_PATH",
            os.path.join(
                self.rf_model_classifier_repo_path,
                "models",
                "noisy_drone_rf_v2",
                "noisy_drone_rf_v2_vgg_full_complex_spectrogram_best.keras",
            ),
        )
        self.rf_model_classifier_target_mhz = float(os.getenv("SDR_SHARK_RF_MODEL_TARGET_MHZ", "2399") or "2399")
        self.rf_model_classifier_bandwidth_mhz = float(os.getenv("SDR_SHARK_RF_MODEL_BW_MHZ", "20") or "20")
        self.rf_model_classifier_interval_sec = float(os.getenv("SDR_SHARK_RF_MODEL_INTERVAL_SEC", "1.0") or "1.0")
        self.rf_model_classifier_threshold = float(os.getenv("SDR_SHARK_RF_MODEL_THRESHOLD", "0.45") or "0.45")
        self.waterfall_samples = 200
        self.waterfall_bin_count = 2048
        self.persistence_decay  = 0.5
        self.number_of_peaks = 5
        self.showFirstTrace = True
        self.showSecondTrace = False
        self.showMaxTrace = True
        self.showPeristanceTrace = True
        self.minPeakDistance = 0.1 # MHz
        self.analysis_retention_sec = 10.0
        self.recordings_dir = os.getenv(
            "SDR_SHARK_RECORDINGS_DIR",
            os.path.join(self.app_root, "datascience", "recordings"),
        )
        self.classifiers_path = os.getenv(
            "SDR_SHARK_CLASSIFIERS_PATH",
            os.path.join(self.app_root, "datascience", "band_dictionaries"),
        )
        self.lockBandwidthSampleRate = False  # Default setting for lock
        self.radio_name = "sidekiq"

        os.makedirs(os.path.dirname(self.settings_file), exist_ok=True)
        os.makedirs(self.recordings_dir, exist_ok=True)
        os.makedirs(self.classifiers_path, exist_ok=True)
        
        # Initialize SDRs
        self.sdr0 = SDRGeneric("sidekiq", 
                               center_freq=self.sdr_settings['sidekiq'].frequency,
                               sample_rate=self.sdr_settings['sidekiq'].sampleRate,
                               bandwidth=self.sdr_settings['sidekiq'].bandwidth,
                               gain=self.sdr_settings['sidekiq'].gain,
                               size=self.sample_size)
        try:
            self.sdr0.start()
        except Exception as exc:
            # Do not crash backend startup when gateway has no currently available SDR.
            # The app can still start and recover once a device becomes available.
            print(f"Warning: SDR init failed at startup: {exc}")
        self.worker_sdr = None
        self.worker_sdr_error = ""
        self.worker_sdr_suspended = False
        self.worker_sdr_suspend_reason = ""
        self.worker_sdr_enabled = str(os.getenv("SDR_SHARK_WORKER_SDR", "1")).strip().lower() not in {"0", "false", "no", "off"}
        self.worker_sdr_max_bandwidth = float(os.getenv("SDR_SHARK_WORKER_MAX_BW_HZ", "3000000") or "3000000")
        self.ensure_worker_sdr()
        # self.sdr1 = SDRGeneric("hackrf",
        #                        center_freq=self.sdr_settings['hackrf'].frequency,
        #                        sample_rate=self.sdr_settings['hackrf'].sampleRate,
        #                        bandwidth=self.sdr_settings['hackrf'].bandwidth,
        #                        gain=self.sdr_settings['hackrf'].gain,
        #                        size=self.sample_size)
        # self.sdr1.start()
        
        # Load settings from file
        self.load_settings()
        self.classifier = SignalClassifier()
        
        self.signal_stats = {
            "noise_floor" : -255,
            "max" : -255
        }

    def ensure_worker_sdr(self):
        if not self.worker_sdr_enabled:
            self.worker_sdr_error = "Worker SDR disabled"
            return None
        if self.worker_sdr_suspended:
            self.worker_sdr_error = self.worker_sdr_suspend_reason or "Worker SDR suspended"
            return None
        if self.worker_sdr is not None:
            return self.worker_sdr
        try:
            settings = self.sdr_settings['rtlsdr_worker']
            worker = SDRGeneric(
                os.getenv("SDR_SHARK_WORKER_SDR_NAME", "rtlsdr"),
                center_freq=settings.frequency,
                sample_rate=settings.sampleRate,
                bandwidth=settings.bandwidth,
                gain=settings.gain,
                size=self.sample_size,
            )
            worker.start()
            self.worker_sdr = worker
            self.worker_sdr_error = ""
            return self.worker_sdr
        except Exception as exc:
            self.worker_sdr = None
            self.worker_sdr_error = str(exc)
            print(f"Warning: worker SDR init failed: {exc}")
            return None

    def suspend_worker_sdr(self, reason="Worker SDR temporarily assigned to decoder"):
        self.worker_sdr_suspended = True
        self.worker_sdr_suspend_reason = str(reason or "Worker SDR suspended")
        worker = self.worker_sdr
        self.worker_sdr = None
        self.worker_sdr_error = self.worker_sdr_suspend_reason
        if worker is not None:
            try:
                worker.stop()
            except Exception as exc:
                self.worker_sdr_error = f"{self.worker_sdr_suspend_reason}: {exc}"

    def resume_worker_sdr(self):
        if not self.worker_sdr_suspended:
            return self.worker_sdr
        self.worker_sdr_suspended = False
        self.worker_sdr_suspend_reason = ""
        self.worker_sdr_error = ""
        return self.ensure_worker_sdr()

    def worker_sdr_available(self):
        return self.ensure_worker_sdr() is not None

    def worker_sdr_info(self):
        worker = self.worker_sdr
        return {
            "enabled": bool(self.worker_sdr_enabled),
            "suspended": bool(self.worker_sdr_suspended),
            "suspend_reason": self.worker_sdr_suspend_reason,
            "available": bool(worker is not None),
            "device_id": getattr(worker, "device_id", None) if worker is not None else None,
            "backend": getattr(worker, "backend", None) if worker is not None else None,
            "frequency": float(getattr(worker, "frequency", 0.0) or 0.0) if worker is not None else 0.0,
            "sample_rate": float(getattr(worker, "sample_rate", 0.0) or 0.0) if worker is not None else 0.0,
            "bandwidth": float(getattr(worker, "bandwidth", 0.0) or 0.0) if worker is not None else 0.0,
            "max_bandwidth": float(self.worker_sdr_max_bandwidth),
            "error": self.worker_sdr_error,
        }

    def _ensure_radio_settings(self, key: str):
        """Ensure we have a settings bucket for a discovered radio driver."""
        if key in self.sdr_settings:
            return
        seed = self.sdr_settings.get(self.sdr_name, next(iter(self.sdr_settings.values())))
        s = SdrSettings(key)
        s.frequency = float(seed.frequency)
        s.sampleRate = float(seed.sampleRate)
        s.bandwidth = float(seed.bandwidth)
        s.gain = float(seed.gain)
        s.averagingCount = int(seed.averagingCount)
        self.sdr_settings[key] = s

    def load_settings(self):
        """Load settings from a JSON file. If the file doesn't exist, create it with default values."""
        if os.path.exists(self.settings_file):
            try:
                with open(self.settings_file, 'r') as f:
                    settings = json.load(f)
                    self.apply_settings(settings)
            except Exception as e:
                print(f"Error loading settings: {e}")
                self.create_default_settings()
                pass
        else:
            self.create_default_settings()

    def create_default_settings(self):
        print(f"Settings file '{self.settings_file}' not found. Creating with default settings.")
        settings = self.get_default_config()
        self.save_settings(settings)
        self.apply_settings(settings)

    def save_settings(self, settings_ = None):
        """Save current settings to a JSON file."""
        if settings_:
            settings = settings_
        else:
            settings = self.get_settings()
        try:
            with open(self.settings_file, 'w') as f:
                json.dump(settings, f, indent=4)
        except Exception as e:
            print(f"Error saving settings: {e}")

    def validate_settings(self):
        """Validate and correct the settings to ensure they are within acceptable limits."""
        # Define acceptable limits for the settings
        MIN_FREQUENCY = 50e6  # Example minimum frequency
        MAX_FREQUENCY = 6000e6   # Example maximum frequency
        MIN_SAMPLE_RATE = 0.250e6  # Example minimum sample rate
        MAX_SAMPLE_RATE = 61.44e6  # Example maximum sample rate
        MIN_BANDWIDTH = 200e3   # Example minimum bandwidth
        MAX_BANDWIDTH = 61.44e6   # Example maximum bandwidth
        MIN_GAIN = 0            # Example minimum gain
        MAX_GAIN = 76           # Example maximum gain

        def validate_value(value, min_value, max_value):
            if value:
                if not np.isfinite(value) or value < min_value or value > max_value:
                    return min_value  # Default to minimum if out of range or not finite
            else:
                return min_value
            return value

        self.sdr_settings[self.sdr_name].frequency = validate_value(self.sdr_frequency(), MIN_FREQUENCY, MAX_FREQUENCY)
        self.sdr_settings[self.sdr_name].sampleRate = validate_value(self.sdr_sampleRate(), MIN_SAMPLE_RATE, MAX_SAMPLE_RATE)
        self.sdr_settings[self.sdr_name].bandwidth = validate_value(self.sdr_bandwidth(), MIN_BANDWIDTH, MAX_BANDWIDTH)
        self.sdr_settings[self.sdr_name].gain = validate_value(self.sdr_gain(), MIN_GAIN, MAX_GAIN)

        self.sweep_settings['frequency_start'] = validate_value(self.sweep_settings['frequency_start'], MIN_FREQUENCY, MAX_FREQUENCY)
        self.sweep_settings['frequency_stop'] = validate_value(self.sweep_settings['frequency_stop'], MIN_FREQUENCY, MAX_FREQUENCY)
        self.sweep_settings['bandwidth'] = validate_value(self.sweep_settings['bandwidth'], MIN_BANDWIDTH, MAX_BANDWIDTH)

    def get_settings(self):
        """Get current settings as a dictionary."""
        settings = {
            "frequency": self.sdr_frequency(),
            "sample_rate": self.sdr_sampleRate(),
            "bandwidth": self.sdr_bandwidth(),
            "gain": self.sdr_gain(),
            "averagingCount": self.sdr_averagingCount(),
            "sweep_settings": self.sweep_settings,
            "sweeping_enabled": self.sweeping_enabled,
            "peak_threshold_minimum_dB": self.peak_threshold_minimum_dB,
            "dc_suppress": self.dc_suppress,
            "show_waterfall": self.show_waterfall,
            "decodersAlwaysEnabled": self.decoders_always_enabled,
            "rfModelClassifierEnabled": self.rf_model_classifier_enabled,
            "rfModelClassifierRepoPath": self.rf_model_classifier_repo_path,
            "rfModelClassifierModelPath": self.rf_model_classifier_model_path,
            "rfModelClassifierTargetMHz": self.rf_model_classifier_target_mhz,
            "rfModelClassifierBandwidthMHz": self.rf_model_classifier_bandwidth_mhz,
            "rfModelClassifierIntervalSec": self.rf_model_classifier_interval_sec,
            "rfModelClassifierThreshold": self.rf_model_classifier_threshold,
            "waterfall_samples": self.waterfall_samples,
            "waterfall_bin_count": self.waterfall_bin_count,
            "number_of_peaks": self.number_of_peaks,
            "recordings_dir": self.recordings_dir,
            "lockBandwidthSampleRate": self.lockBandwidthSampleRate,
            "minPeakDistance": self.minPeakDistance,
            "analysisRetentionSec": self.analysis_retention_sec,
            "radio_name": self.radio_name,
            "showFirstTrace": self.showFirstTrace,
            "showSecondTrace": self.showSecondTrace,
            "showMaxTrace" : self.showMaxTrace,
            'showPeristanceTrace': self.showPeristanceTrace
        }
        return settings

    def apply_settings(self, settings):
        """Apply settings from a dictionary with validation."""
        
        try:
            requested_radio = str(settings.get("radio_name", self.radio_name) or "").strip()
            if requested_radio and requested_radio != getattr(self.sdr0, "device_id", None):
                self.reselect_radio(requested_radio)
            previous_receiver = (
                float(self.sdr_frequency()),
                float(self.sdr_sampleRate()),
                float(self.sdr_bandwidth()),
                float(self.sdr_gain()),
            )
            self.sdr_settings[self.sdr_name].frequency = settings.get("frequency", self.sdr_frequency())
            self.sdr_settings[self.sdr_name].sampleRate = settings.get("sampleRate", self.sdr_sampleRate())
            self.sdr_settings[self.sdr_name].bandwidth = settings.get("bandwidth", self.sdr_bandwidth())
            self.sdr_settings[self.sdr_name].gain = settings.get("gain", self.sdr_gain())
            self.sdr_settings[self.sdr_name].averagingCount = settings.get("averagingCount", self.sdr_averagingCount())
            
            self.sweep_settings = settings.get("sweep_settings", self.sweep_settings)
            self.sweep_settings['frequency_start'] = settings.get("frequency_start", self.sweep_settings['frequency_start'])
            self.sweep_settings['frequency_stop'] = settings.get("frequency_stop", self.sweep_settings['frequency_stop'])
            self.sweeping_enabled = settings.get("sweeping_enabled", self.sweeping_enabled)
            self.peak_threshold_minimum_dB = settings.get("peakThreshold", self.peak_threshold_minimum_dB)
            self.dc_suppress = settings.get("dcSuppress", self.dc_suppress)
            self.show_waterfall = settings.get("showWaterfall", self.show_waterfall)
            self.decoders_always_enabled = bool(settings.get("decodersAlwaysEnabled", self.decoders_always_enabled))
            self.rf_model_classifier_enabled = bool(settings.get("rfModelClassifierEnabled", self.rf_model_classifier_enabled))
            self.rf_model_classifier_repo_path = str(settings.get("rfModelClassifierRepoPath", self.rf_model_classifier_repo_path) or "")
            self.rf_model_classifier_model_path = str(settings.get("rfModelClassifierModelPath", self.rf_model_classifier_model_path) or "")
            self.rf_model_classifier_target_mhz = float(settings.get("rfModelClassifierTargetMHz", self.rf_model_classifier_target_mhz) or 0.0)
            self.rf_model_classifier_bandwidth_mhz = float(settings.get("rfModelClassifierBandwidthMHz", self.rf_model_classifier_bandwidth_mhz) or 20.0)
            self.rf_model_classifier_interval_sec = float(settings.get("rfModelClassifierIntervalSec", self.rf_model_classifier_interval_sec) or 1.0)
            self.rf_model_classifier_threshold = float(settings.get("rfModelClassifierThreshold", self.rf_model_classifier_threshold) or 0.45)
            self.waterfall_samples = settings.get("waterfallSamples", self.waterfall_samples)
            self.waterfall_bin_count = settings.get("waterfallBinCount", self.waterfall_bin_count)
            self.number_of_peaks = settings.get("number_of_peaks", self.number_of_peaks)
            self.recordings_dir = settings.get("recordings_dir", self.recordings_dir)
            self.lockBandwidthSampleRate = settings.get("lockBandwidthSampleRate", self.lockBandwidthSampleRate)
            self.showFirstTrace = settings.get("showFirstTrace", self.showFirstTrace)
            self.showSecondTrace = settings.get("showSecondTrace", self.showSecondTrace)
            self.showMaxTrace = settings.get("showMaxTrace", self.showMaxTrace)
            self.showPeristanceTrace = settings.get("showPeristanceTrace", self.showPeristanceTrace)
            self.minPeakDistance = settings.get("minPeakDistance", self.minPeakDistance)
            self.analysis_retention_sec = float(settings.get("analysisRetentionSec", self.analysis_retention_sec))
            self.radio_name = settings.get("radio_name", self.radio_name)

            # Validate the settings after applying them
            self.validate_settings()


            sr = self.sdr_sampleRate()
            next_receiver = (
                float(self.sdr_settings[self.sdr_name].frequency),
                float(sr),
                float(sr),
                float(self.sdr_gain()),
            )
            receiver_changed = any(abs(left - right) > 1.0 for left, right in zip(previous_receiver, next_receiver))
            if receiver_changed:
                self.sdr0.configure_receiver(
                    frequency=self.sdr_settings[self.sdr_name].frequency,
                    sample_rate=sr,
                    bandwidth=sr,
                    gain=self.sdr_gain(),
                )
        except Exception as e:
            print(e)
            pass

    def sdr_gain(self):
        return self.sdr_settings[self.sdr_name].gain
    def sdr_sampleRate(self):
        return self.sdr_settings[self.sdr_name].sampleRate
    def sdr_bandwidth(self):
        return self.sdr_settings[self.sdr_name].bandwidth
    def sdr_frequency(self):
        return self.sdr_settings[self.sdr_name].frequency
    def sdr_averagingCount(self):
        return self.sdr_settings[self.sdr_name].averagingCount

    def reselect_radio(self, name: str) -> int:
        try:
            driver = str(name).split(":", 1)[0].lower()
            self._ensure_radio_settings(driver)
            selected = self.sdr0.select_device(name)
            if selected:
                self.sdr_name = driver
                self.radio_name = name
                # Apply sensible per-radio defaults, then clamp to device limits.
                preferred_sr = {
                    "hackrf": 20e6,
                    "sidekiq": 60e6,
                    "airspy": 10e6,
                    "bladerf": 60e6,
                    "rtlsdr": 2.4e6,
                    "antsdre200": 20e6,
                }.get(driver, self.sdr_settings[self.sdr_name].sampleRate)
                max_sr = float(getattr(self.sdr0, "max_sample_rate", self.sdr_sampleRate()) or self.sdr_sampleRate())
                sr = min(float(preferred_sr), max_sr)
                self.sdr_settings[self.sdr_name].sampleRate = sr
                self.sdr_settings[self.sdr_name].bandwidth = sr
                self.sdr_settings[self.sdr_name].frequency = min(
                    max(self.sdr_settings[self.sdr_name].frequency, float(getattr(self.sdr0, "min_frequency", 1e6))),
                    float(getattr(self.sdr0, "max_frequency", 6e9)),
                )
                self.sdr0.configure_receiver(
                    frequency=self.sdr_settings[self.sdr_name].frequency,
                    sample_rate=self.sdr_settings[self.sdr_name].sampleRate,
                    bandwidth=self.sdr_settings[self.sdr_name].bandwidth,
                    gain=self.sdr_settings[self.sdr_name].gain,
                )
                return 1
            return 0
        except Exception as e:
            print(e)
            return 0
    
    def get_default_config(self):
        return {
            "frequency": 751000000.0,
            "sampleRate": 20000000.0,
            "bandwidth": 20000000.0,
            "gain": 10,
            "sweep_settings": {
                "frequency_start": 700000000.0,
                "frequency_stop": 820000000.0,
                "bandwidth": 16000000.0,
            },
            "sweeping_enabled": False,
            "peakThreshold": -25,
            "averagingCount": 1,
            "dcSuppress": True,
            "showWaterfall": True,
            "rfModelClassifierEnabled": False,
            "rfModelClassifierRepoPath": self.rf_model_classifier_repo_path,
            "rfModelClassifierModelPath": self.rf_model_classifier_model_path,
            "rfModelClassifierTargetMHz": self.rf_model_classifier_target_mhz,
            "rfModelClassifierBandwidthMHz": self.rf_model_classifier_bandwidth_mhz,
            "rfModelClassifierIntervalSec": self.rf_model_classifier_interval_sec,
            "rfModelClassifierThreshold": self.rf_model_classifier_threshold,
            "waterfallSamples": 200,
            "waterfallBinCount": 2048,
            "number_of_peaks": 5,
            "recordings_dir": self.recordings_dir,
            "lockBandwidthSampleRate": True,
            "radio_name": "sidekiq",
            "showFirstTrace": True,
            "showSecondTrace": True,
            "showMaxTrace": True,
            "showPeristanceTrace": True,
            "analysisRetentionSec": 10.0,
        }

# Instantiate the configuration
vars = sdr_scheduler_config()
