from sdr_plot_backend.classifier.Base import BaseSignalClassifier

class LTEClassifier(BaseSignalClassifier):
    def __init__(self):
        super().__init__()
        self.bands = [
            {"label": "LTE", "frequency": 663, "bandwidth": 36, "channel": "71", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 617, "bandwidth": 36, "channel": "71", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 722.5, "bandwidth": 12, "channel": "29", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 707.5, "bandwidth": 18, "channel": "12", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 737.5, "bandwidth": 18, "channel": "12", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 710, "bandwidth": 12, "channel": "17", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 740, "bandwidth": 12, "channel": "17", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 782, "bandwidth": 10, "channel": "13", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 751, "bandwidth": 10, "channel": "13", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 793, "bandwidth": 11, "channel": "14", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 763, "bandwidth": 11, "channel": "14", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 836.5, "bandwidth": 25, "channel": "5", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 881.5, "bandwidth": 25, "channel": "5", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 831.5, "bandwidth": 35, "channel": "26", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 876.5, "bandwidth": 35, "channel": "26", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 1732.5, "bandwidth": 45, "channel": "4", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 2132.5, "bandwidth": 45, "channel": "4", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 1745, "bandwidth": 70, "channel": "66", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 2155, "bandwidth": 70, "channel": "66", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 1880, "bandwidth": 60, "channel": "2", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 1960, "bandwidth": 60, "channel": "2", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 1882.5, "bandwidth": 65, "channel": "25", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 1962.5, "bandwidth": 65, "channel": "25", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 2310, "bandwidth": 10, "channel": "30", "metadata": "Uplink"},
            {"label": "LTE", "frequency": 2355, "bandwidth": 10, "channel": "30", "metadata": "Downlink"},
            {"label": "LTE", "frequency": 2593, "bandwidth": 194, "channel": "41", "metadata": "Uplink/Downlink"},
            {"label": "LTE", "frequency": 2595, "bandwidth": 50, "channel": "38", "metadata": "Uplink/Downlink"},
            {"label": "LTE", "frequency": 3625, "bandwidth": 150, "channel": "48", "metadata": "Uplink/Downlink"},
            {"label": "LTE", "frequency": 5537.5, "bandwidth": 775, "channel": "46", "metadata": "Uplink/Downlink"},
        ]
        self._prepare_bands()
    
    def classify_signal(self, frequency_mhz, bandwidth_mhz=None):
        return super().classify_signal(frequency_mhz, bandwidth_mhz)

    def get_signals_in_range(self, start_freq_mhz, end_freq_mhz):
        return super().get_signals_in_range(start_freq_mhz, end_freq_mhz)
