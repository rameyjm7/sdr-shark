import threading
import json
import os
from sdrfly.sdr.sdr_generic import SDRGeneric
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
        # self.app_root = "/mnt/samba_share/datascience/"
        self.app_root = "/root"
        self.settings_file = "/root/configurations/sdr_scheduler_config.json"
        self.sdr_settings = {
            "hackrf" : SdrSettings("hackrf"),
            "sidekiq" : SdrSettings("sidekiq")
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
        self.waterfall_samples = 100
        self.persistence_decay  = 0.5
        self.number_of_peaks = 5
        self.showFirstTrace = True
        self.showSecondTrace = False
        self.showMaxTrace = True
        self.showPeristanceTrace = True
        self.minPeakDistance = 0.1 # MHz
        self.recordings_dir = f"{self.app_root}/datascience/recordings"
        self.classifiers_path = f"{self.app_root}/datascience/band_dictionaries/"
        self.lockBandwidthSampleRate = False  # Default setting for lock
        self.radio_name = "sidekiq"
        
        # Initialize SDRs
        self.sdr0 = SDRGeneric("sidekiq", 
                               center_freq=self.sdr_settings['sidekiq'].frequency,
                               sample_rate=self.sdr_settings['sidekiq'].sampleRate,
                               bandwidth=self.sdr_settings['sidekiq'].bandwidth,
                               gain=self.sdr_settings['sidekiq'].gain,
                               size=self.sample_size)
        self.sdr0.start()
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
        self.save_settings(self.get_default_config())
        with open(self.settings_file, 'r') as f:
                settings = json.load(f)
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
            "waterfall_samples": self.waterfall_samples,
            "number_of_peaks": self.number_of_peaks,
            "recordings_dir": self.recordings_dir,
            "lockBandwidthSampleRate": self.lockBandwidthSampleRate,
            "minPeakDistance": self.minPeakDistance,
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
            self.waterfall_samples = settings.get("waterfallSamples", self.waterfall_samples)
            self.number_of_peaks = settings.get("number_of_peaks", self.number_of_peaks)
            self.recordings_dir = settings.get("recordings_dir", self.recordings_dir)
            self.lockBandwidthSampleRate = settings.get("lockBandwidthSampleRate", self.lockBandwidthSampleRate)
            self.showFirstTrace = settings.get("showFirstTrace", self.showFirstTrace)
            self.showSecondTrace = settings.get("showSecondTrace", self.showSecondTrace)
            self.showMaxTrace = settings.get("showMaxTrace", self.showMaxTrace)
            self.showPeristanceTrace = settings.get("showPeristanceTrace", self.showPeristanceTrace)
            self.minPeakDistance = settings.get("minPeakDistance", self.minPeakDistance)
            self.radio_name = settings.get("radio_name", self.radio_name)

            # Validate the settings after applying them
            self.validate_settings()


            if self.sdr_name in 'hackrf':
                self.sdr1.set_frequency(self.sdr_frequency())
                sr = self.sdr_sampleRate()
                self.sdr1.set_sample_rate(20e6 if sr > 20e6 else sr)
                self.sdr1.set_bandwidth(20e6 if sr > 20e6 else sr)
                self.sdr1.set_gain(self.sdr_settings[self.sdr_name].gain)
            else:
                self.sdr0.set_frequency(self.sdr_settings[self.sdr_name].frequency)
                sr = self.sdr_sampleRate()
                self.sdr0.set_sample_rate(sr)
                self.sdr0.set_bandwidth(sr)
                self.sdr0.set_gain(self.sdr_gain())
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
        """Temporarily disabled due to the use of both radios."""
        print("Radio switching is currently disabled.")
        return 1
    
    def get_default_config(self):
        return """{
    "frequency": 751000000.0,
    "sample_rate": 20000000.0,
    "bandwidth": 20000000.0,
    "gain": 10,
    "sweep_settings": {
        "frequency_start": 700000000.0,
        "frequency_stop": 820000000.0,
        "bandwidth": 16000000.0
    },
    "sweeping_enabled": false,
    "peak_threshold_minimum_dB": -25,
    "averagingCount": 1,
    "dc_suppress": true,
    "show_waterfall": true,
    "waterfall_samples": 100,
    "number_of_peaks": 5,
    "recordings_dir": "/root/workspace/data/recordings",
    "lockBandwidthSampleRate": true,
    "radio_name": "sidekiq",
    "showFirstTrace": true,
    "showSecondTrace": true
        }"""

# Instantiate the configuration
vars = sdr_scheduler_config()
