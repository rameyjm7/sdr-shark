from sdr_plot_backend.classifier.Base import BaseSignalClassifier

class FmRadioClassifier(BaseSignalClassifier):
    def __init__(self):
        super().__init__()
        self.bands = [
            {"label": "FM Radio", "frequency": 98.0, "bandwidth": 20.0, "channel": "", "metadata": "88-108 MHz"},
        ]
        self._prepare_bands()

    def classify_signal(self, frequency_mhz, bandwidth_mhz=None):
        return super().classify_signal(frequency_mhz, bandwidth_mhz)

    def get_signals_in_range(self, start_freq_mhz, end_freq_mhz):
        if 88 <= end_freq_mhz and start_freq_mhz <= 108:
            return super().get_signals_in_range(start_freq_mhz, end_freq_mhz)
        return []
