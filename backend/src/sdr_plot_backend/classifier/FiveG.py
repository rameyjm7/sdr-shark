from sdr_plot_backend.classifier.Base import BaseSignalClassifier

class FiveGClassifier(BaseSignalClassifier):
    def __init__(self):
        super().__init__()
        self.bands = [
            {"label": "5G", "frequency": 680.5, "bandwidth": 36, "channel": "n71", "metadata": "Downlink"},
            {"label": "5G", "frequency": 722.5, "bandwidth": 12, "channel": "n29", "metadata": "Downlink"},
            {"label": "5G", "frequency": 707.5, "bandwidth": 18, "channel": "n12", "metadata": "Uplink"},
            {"label": "5G", "frequency": 737.5, "bandwidth": 18, "channel": "n12", "metadata": "Downlink"},
            {"label": "5G", "frequency": 836.5, "bandwidth": 26, "channel": "n5", "metadata": "Uplink"},
            {"label": "5G", "frequency": 881.5, "bandwidth": 26, "channel": "n5", "metadata": "Downlink"},
            {"label": "5G", "frequency": 1702.5, "bandwidth": 16, "channel": "n70", "metadata": "Uplink"},
            {"label": "5G", "frequency": 2007.5, "bandwidth": 16, "channel": "n70", "metadata": "Downlink"},
            {"label": "5G", "frequency": 1732.5, "bandwidth": 46, "channel": "n4", "metadata": "Uplink"},
            {"label": "5G", "frequency": 2132.5, "bandwidth": 46, "channel": "n4", "metadata": "Downlink"},
            {"label": "5G", "frequency": 1745, "bandwidth": 71, "channel": "n66", "metadata": "Uplink"},
            {"label": "5G", "frequency": 2155, "bandwidth": 71, "channel": "n66", "metadata": "Downlink"},
            {"label": "5G", "frequency": 1880, "bandwidth": 61, "channel": "n2", "metadata": "Uplink"},
            {"label": "5G", "frequency": 1960, "bandwidth": 61, "channel": "n2", "metadata": "Downlink"},
            {"label": "5G", "frequency": 1882.5, "bandwidth": 66, "channel": "n25", "metadata": "Uplink"},
            {"label": "5G", "frequency": 1962.5, "bandwidth": 66, "channel": "n25", "metadata": "Downlink"},
            {"label": "5G", "frequency": 2350, "bandwidth": 100, "channel": "n40", "metadata": "Downlink"},
            {"label": "5G", "frequency": 2593, "bandwidth": 195, "channel": "n41", "metadata": "Downlink"},
            {"label": "5G", "frequency": 2595, "bandwidth": 50, "channel": "n38", "metadata": "Downlink"},
            {"label": "5G", "frequency": 3715, "bandwidth": 530, "channel": "n77", "metadata": "Downlink"},
            {"label": "5G", "frequency": 25875, "bandwidth": 3250, "channel": "n258", "metadata": "Downlink"},
            {"label": "5G", "frequency": 27925, "bandwidth": 850, "channel": "n261", "metadata": "Downlink"},
            {"label": "5G", "frequency": 38500, "bandwidth": 3000, "channel": "n260", "metadata": "Downlink"},
            {"label": "5G", "frequency": 47710, "bandwidth": 1000, "channel": "n262", "metadata": "Downlink"},
        ]
        self._prepare_bands()

    def classify_signal(self, frequency_mhz, bandwidth_mhz=None):
        return super().classify_signal(frequency_mhz, bandwidth_mhz)

    def get_signals_in_range(self, start_freq_mhz, end_freq_mhz):
        return super().get_signals_in_range(start_freq_mhz, end_freq_mhz)


class NRClassifier(BaseSignalClassifier):
    def __init__(self):
        super().__init__()
        self.bands = [
            {"label": "5G NR", "frequency": 2140, "bandwidth": 60, "channel": "n1", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1960, "bandwidth": 60, "channel": "n2", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1807.5, "bandwidth": 75, "channel": "n3", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 881.5, "bandwidth": 25, "channel": "n5", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2655, "bandwidth": 70, "channel": "n7", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 942.5, "bandwidth": 35, "channel": "n8", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 737.5, "bandwidth": 18, "channel": "n12", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 751.5, "bandwidth": 10, "channel": "n13", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 758, "bandwidth": 10, "channel": "n14", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 867.5, "bandwidth": 15, "channel": "n18", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 806, "bandwidth": 40, "channel": "n20", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1542.75, "bandwidth": 70, "channel": "n24", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1962.5, "bandwidth": 65, "channel": "n25", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 876.5, "bandwidth": 35, "channel": "n26", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 780.5, "bandwidth": 45, "channel": "n28", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 722.5, "bandwidth": 10, "channel": "n29", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2355, "bandwidth": 10, "channel": "n30", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 462.5, "bandwidth": 5, "channel": "n31", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2017.5, "bandwidth": 15, "channel": "n34", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2595, "bandwidth": 50, "channel": "n38", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1900, "bandwidth": 40, "channel": "n39", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2350, "bandwidth": 100, "channel": "n40", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2593, "bandwidth": 194, "channel": "n41", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 5537.5, "bandwidth": 775, "channel": "n46", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 5890, "bandwidth": 70, "channel": "n47", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 3625, "bandwidth": 150, "channel": "n48", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1474.5, "bandwidth": 85, "channel": "n50", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1429.5, "bandwidth": 5, "channel": "n51", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2489.25, "bandwidth": 11.5, "channel": "n53", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1672.5, "bandwidth": 5, "channel": "n54", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2115, "bandwidth": 90, "channel": "n65", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2155, "bandwidth": 70, "channel": "n66", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 748, "bandwidth": 20, "channel": "n67", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2007.5, "bandwidth": 50, "channel": "n70", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 680.5, "bandwidth": 35, "channel": "n71", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 463.5, "bandwidth": 5, "channel": "n72", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1496.5, "bandwidth": 43, "channel": "n74", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1474.5, "bandwidth": 85, "channel": "n75", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1430.5, "bandwidth": 5, "channel": "n76", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 3750, "bandwidth": 100, "channel": "n77", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 3650, "bandwidth": 100, "channel": "n78", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 4700, "bandwidth": 100, "channel": "n79", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1747.5, "bandwidth": 75, "channel": "n80", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 897.5, "bandwidth": 35, "channel": "n81", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 847, "bandwidth": 30, "channel": "n82", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 725.5, "bandwidth": 45, "channel": "n83", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1950, "bandwidth": 60, "channel": "n84", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 707, "bandwidth": 36, "channel": "n85", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1745, "bandwidth": 75, "channel": "n86", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 836.5, "bandwidth": 25, "channel": "n89", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2593, "bandwidth": 194, "channel": "n90", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1544.5, "bandwidth": 70, "channel": "n91", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1474.5, "bandwidth": 85, "channel": "n92", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1429.5, "bandwidth": 5, "channel": "n93", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1474.5, "bandwidth": 85, "channel": "n94", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2017.5, "bandwidth": 15, "channel": "n95", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 6525, "bandwidth": 100, "channel": "n96", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2350, "bandwidth": 100, "channel": "n97", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1900, "bandwidth": 40, "channel": "n98", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1644, "bandwidth": 35, "channel": "n99", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 898.7, "bandwidth": 5, "channel": "n100", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1905, "bandwidth": 10, "channel": "n101", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 6175, "bandwidth": 250, "channel": "n102", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 6775, "bandwidth": 350, "channel": "n104", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 677.5, "bandwidth": 45, "channel": "n105", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 917.5, "bandwidth": 7.5, "channel": "n106", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1490, "bandwidth": 60, "channel": "n109", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2800, "bandwidth": 400, "channel": "n257", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2650, "bandwidth": 400, "channel": "n258", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 4150, "bandwidth": 400, "channel": "n259", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 3850, "bandwidth": 400, "channel": "n260", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2805, "bandwidth": 400, "channel": "n261", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 4770, "bandwidth": 200, "channel": "n262", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 6400, "bandwidth": 1600, "channel": "n263", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2441.75, "bandwidth": 16.75, "channel": "n254", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 1542, "bandwidth": 35.5, "channel": "n255", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2185, "bandwidth": 20, "channel": "n256", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2367.5, "bandwidth": 345, "channel": "n510", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2436.75, "bandwidth": 320, "channel": "n511", "metadata": "Downlink"},
            {"label": "5G NR", "frequency": 2436.75, "bandwidth": 345, "channel": "n512", "metadata": "Downlink"},
            # Add additional bands as needed
        ]
        self._prepare_bands()
    
    def classify_signal(self, frequency_mhz, bandwidth_mhz=None):
        return super().classify_signal(frequency_mhz, bandwidth_mhz)

    def get_signals_in_range(self, start_freq_mhz, end_freq_mhz):
        return super().get_signals_in_range(start_freq_mhz, end_freq_mhz)
