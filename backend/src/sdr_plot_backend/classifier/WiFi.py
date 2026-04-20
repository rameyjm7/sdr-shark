from sdr_plot_backend.classifier.Base import BaseSignalClassifier

class WiFi24GHzClassifier(BaseSignalClassifier):
    def __init__(self):
        super().__init__()
        self.bands = [
            {"label": "WiFi 2.4G", "channel": 1, "frequency": 2412, "bandwidth": 22, "metadata": ""},
            {"label": "WiFi 2.4G", "channel": 2, "frequency": 2417, "bandwidth": 22, "metadata": ""},
            {"label": "WiFi 2.4G", "channel": 3, "frequency": 2422, "bandwidth": 22, "metadata": ""},
            {"label": "WiFi 2.4G", "channel": 4, "frequency": 2427, "bandwidth": 22, "metadata": ""},
            {"label": "WiFi 2.4G", "channel": 5, "frequency": 2432, "bandwidth": 22, "metadata": ""},
            {"label": "WiFi 2.4G", "channel": 6, "frequency": 2437, "bandwidth": 22, "metadata": ""},
            {"label": "WiFi 2.4G", "channel": 7, "frequency": 2442, "bandwidth": 22, "metadata": ""},
            {"label": "WiFi 2.4G", "channel": 8, "frequency": 2447, "bandwidth": 22, "metadata": ""},
            {"label": "WiFi 2.4G", "channel": 9, "frequency": 2452, "bandwidth": 22, "metadata": ""},
            {"label": "WiFi 2.4G", "channel": 10, "frequency": 2457, "bandwidth": 22, "metadata": ""},
            {"label": "WiFi 2.4G", "channel": 11, "frequency": 2462, "bandwidth": 22, "metadata": ""},
            {"label": "WiFi 2.4G", "channel": 12, "frequency": 2467, "bandwidth": 22, "metadata": "Low Power Only (US)"},
            {"label": "WiFi 2.4G", "channel": 13, "frequency": 2472, "bandwidth": 22, "metadata": "Low Power Only (US)"},
            {"label": "WiFi 2.4G", "channel": 14, "frequency": 2484, "bandwidth": 22, "metadata": "Japan Only"},
        ]
        self._prepare_bands()

    def classify_signal(self, frequency_mhz, bandwidth_mhz=None):
        # Measurements rarely land exactly on channel center; allow small tolerance.
        tolerance_mhz = 1.0
        for channel in self.bands:
            if abs(frequency_mhz - channel["frequency"]) <= tolerance_mhz:
                return [{
                    "label": "WiFi 2.4 GHz",
                    "frequency": channel["frequency"],
                    "bandwidth": channel["bandwidth"],
                    "channel": f"{channel['channel']}",
                    "metadata": channel["metadata"]
                }]
        return []

    def get_signals_in_range(self, start_freq_mhz, end_freq_mhz):
        matches = []
        for channel in self.bands:
            if start_freq_mhz <= channel["frequency"] <= end_freq_mhz:
                matches.append({
                    "label": "WiFi 2.4 GHz",
                    "frequency": channel["frequency"],
                    "bandwidth": channel["bandwidth"],
                    "channel": f"{channel['channel']}",
                    "metadata": channel["metadata"]
                })
        return matches

class WiFi5GHzClassifier(BaseSignalClassifier):
    def __init__(self):
        super().__init__()
        self.bands = [
            {"label": "WiFi 5G", "channel": 36, "frequency": 5180, "bandwidth": 20, "metadata": ""},
            {"label": "WiFi 5G", "channel": 40, "frequency": 5200, "bandwidth": 20, "metadata": ""},
            {"label": "WiFi 5G", "channel": 44, "frequency": 5220, "bandwidth": 20, "metadata": ""},
            {"label": "WiFi 5G", "channel": 48, "frequency": 5240, "bandwidth": 20, "metadata": ""},
            {"label": "WiFi 5G", "channel": 52, "frequency": 5260, "bandwidth": 20, "metadata": "DFS"},
            {"label": "WiFi 5G", "channel": 56, "frequency": 5280, "bandwidth": 20, "metadata": "DFS"},
            {"label": "WiFi 5G", "channel": 60, "frequency": 5300, "bandwidth": 20, "metadata": "DFS"},
            {"label": "WiFi 5G", "channel": 64, "frequency": 5320, "bandwidth": 20, "metadata": "DFS"},
            {"label": "WiFi 5G", "channel": 149, "frequency": 5745, "bandwidth": 20, "metadata": ""},
            {"label": "WiFi 5G", "channel": 153, "frequency": 5765, "bandwidth": 20, "metadata": ""},
            {"label": "WiFi 5G", "channel": 157, "frequency": 5785, "bandwidth": 20, "metadata": ""},
            {"label": "WiFi 5G", "channel": 161, "frequency": 5805, "bandwidth": 20, "metadata": ""},
            {"label": "WiFi 5G", "channel": 165, "frequency": 5825, "bandwidth": 20, "metadata": ""}
        ]
        self._prepare_bands()

    def classify_signal(self, frequency_mhz, bandwidth_mhz=None):
        return super().classify_signal(frequency_mhz, bandwidth_mhz)

    def get_signals_in_range(self, start_freq_mhz, end_freq_mhz):
        return super().get_signals_in_range(start_freq_mhz, end_freq_mhz)
