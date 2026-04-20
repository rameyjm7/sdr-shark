import csv
import json
from bisect import bisect_right
from collections import OrderedDict
from sdr_plot_backend.classifier.Base import BaseSignalClassifier
from sdr_plot_backend.classifier import FM, AM, Bluetooth, WiFi, LTE, FiveG, LoRaWAN, FRS, AeronauticalNavigation, AviationService
from sdr_plot_backend.classifier import BroadbandRadioService, BroadcastTV, EarthExpSatellite, FederalPart15, MURS, NOAAWeather, UHF, PublicSafety
from sdr_plot_backend.classifier import F460_470MHz, AircraftRadio, SiriusXM, HDRadio, DMR, P25
from sdr_plot_backend.classifier import GenericClassifier

class SignalClassifier:
    def __init__(self):
        self._cache_limit = 512
        self._classify_cache = OrderedDict()
        self._range_cache = OrderedDict()
        self.classifiers = [
            WiFi.WiFi24GHzClassifier(),
            WiFi.WiFi5GHzClassifier(),
            FM.FmRadioClassifier(),
            AM.AmRadioClassifier(),
            Bluetooth.BluetoothClassicClassifier(),
            Bluetooth.BluetoothLowEnergyClassifier(),
            LTE.LTEClassifier(),
            FiveG.FiveGClassifier(),
            FiveG.NRClassifier(),
            LoRaWAN.LoRaWANClassifier(),
            FRS.FRSClassifier(),
            AeronauticalNavigation.AeronauticalNavigationClassifier(),
            AviationService.AviationServiceClassifier(),
            BroadbandRadioService.BroadbandRadioServiceClassifier(),
            BroadcastTV.USBroadcastTVAndLPTVClassifier(),
            EarthExpSatellite.EarthExpSatelliteClassifier(),
            FederalPart15.FederalPart15Classifier(),
            MURS.MURSClassifier(),
            NOAAWeather.NOAAWeatherRadioClassifier(),
            UHF.UHFFrequenciesClassifier(),
            PublicSafety.PublicSafetyClassifier(),
            F460_470MHz.FourSixtyToFourSeventyMHzClassifier(),
            AircraftRadio.AircraftRadioClassifier(),
            SiriusXM.SiriusXMRadioClassifier(),
            HDRadio.HDRadioClassifier(),
            DMR.DMRClassifier(),
            P25.P25Classifier(),
            GenericClassifier.GenericClassifier() # includes a lot of smaller classifiers with less overhead
            
        ]
        self._indexed_bands = []
        self._indexed_los = []
        self._indexed_by_freq = []
        self._fallback_classifiers = []
        self._rebuild_index()

    def _label_priority(self, label):
        # Prefer legacy cellular naming when multiple standards overlap the same band.
        if label == "LTE":
            return 0
        if label == "5G NR":
            return 1
        if label == "5G":
            return 2
        return 10

    def _rank_results(self, results, frequency_mhz, bandwidth_mhz):
        bw = None if bandwidth_mhz is None else float(bandwidth_mhz)
        if bw is not None and bw <= 0:
            bw = None

        def rank_key(item):
            label = str(item.get("label", ""))
            center = float(item.get("frequency", frequency_mhz))
            band_bw = float(item.get("bandwidth", 0.0))
            # Prefer candidates whose nominal bandwidth resembles the detected bandwidth.
            bw_delta = abs((bw - band_bw)) if bw is not None and band_bw > 0 else 9999.0
            center_delta = abs(frequency_mhz - center)
            return (bw_delta, center_delta, self._label_priority(label), label)

        return sorted(results, key=rank_key)

    def _cache_get(self, cache, key):
        value = cache.get(key)
        if value is None:
            return None
        cache.move_to_end(key)
        return value

    def _cache_set(self, cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self._cache_limit:
            cache.popitem(last=False)

    def _rebuild_index(self):
        indexed = []
        fallback = []
        for classifier in self.classifiers:
            bands = getattr(classifier, "bands", [])
            if not bands:
                fallback.append(classifier)
                continue

            classifier._prepare_bands()
            for band in bands:
                indexed.append(band)

        indexed.sort(key=lambda b: b["_lo"])
        self._indexed_bands = indexed
        self._indexed_los = [b["_lo"] for b in indexed]
        self._indexed_by_freq = sorted(indexed, key=lambda b: b["frequency"])
        self._fallback_classifiers = fallback
        self._classify_cache.clear()
        self._range_cache.clear()

    def _iter_band_matches(self, frequency_mhz):
        if not self._indexed_bands:
            return []

        idx = bisect_right(self._indexed_los, frequency_mhz)
        matches = []
        for i in range(idx - 1, -1, -1):
            band = self._indexed_bands[i]
            if band["_lo"] <= frequency_mhz <= band["_hi"]:
                matches.append(band)
        return matches

    def classify_signal(self, frequency_mhz, bandwidth_mhz=None):
        key = (round(float(frequency_mhz), 4), None if bandwidth_mhz is None else round(float(bandwidth_mhz), 4))
        cached = self._cache_get(self._classify_cache, key)
        if cached is not None:
            return list(cached)

        results = []
        for band in self._iter_band_matches(frequency_mhz):
            results.append(band["_result"])

        for classifier in self._fallback_classifiers:
            results.extend(classifier.classify_signal(frequency_mhz, bandwidth_mhz))

        ranked = self._rank_results(results, float(frequency_mhz), bandwidth_mhz)
        self._cache_set(self._classify_cache, key, tuple(ranked))
        return ranked

    def get_signals_in_range(self, center_freq_mhz, bandwidth_mhz):
        key = (round(float(center_freq_mhz), 4), round(float(bandwidth_mhz), 4))
        cached = self._cache_get(self._range_cache, key)
        if cached is not None:
            return list(cached)

        start_freq_mhz = center_freq_mhz - bandwidth_mhz / 2
        end_freq_mhz = center_freq_mhz + bandwidth_mhz / 2
        signals_in_range = []

        for band in self._indexed_by_freq:
            if band["frequency"] < start_freq_mhz:
                continue
            if band["frequency"] > end_freq_mhz:
                break
            signals_in_range.append(band["_result"])

        for classifier in self._fallback_classifiers:
            signals_in_range.extend(classifier.get_signals_in_range(start_freq_mhz, end_freq_mhz))

        self._cache_set(self._range_cache, key, tuple(signals_in_range))
        return signals_in_range
    
    def get_all_bands(self):
        """Returns a list of all bands across all classifiers."""
        all_bands = []
        for classifier in self.classifiers:
            all_bands.extend(classifier.get_bands())
        return all_bands
    
    def dump_all_bands(self, file_path, format="json"):
        """Dump all bands from all classifiers into a JSON or CSV file."""
        all_bands = self.get_all_bands()
        
        if format == "json":
            with open(file_path, 'w') as json_file:
                json.dump(all_bands, json_file, indent=4)
        elif format == "csv":
            with open(file_path, 'w', newline='') as csv_file:
                fieldnames = ["label", "frequency", "bandwidth", "channel", "metadata"]
                writer = csv.DictWriter(csv_file, fieldnames=fieldnames)

                writer.writeheader()
                for band in all_bands:
                    writer.writerow(band)
        else:
            raise ValueError(f"Unsupported format: {format}. Use 'json' or 'csv'.")
        
    def load_classifier_from_csv(self, file_path):
        """Load a classifier from a CSV file and add it to the list of classifiers."""
        class CustomClassifier(BaseSignalClassifier):
            def __init__(self, bands):
                super().__init__()
                self.bands = bands

        bands = []
        with open(file_path, mode='r') as csv_file:
            csv_reader = csv.DictReader(csv_file)
            for row in csv_reader:
                band = {
                    "label": row["label"],
                    "frequency": float(row["frequency"]),
                    "bandwidth": float(row["bandwidth"]),
                    "channel": row.get("channel", ""),
                    "metadata": row.get("metadata", "")
                }
                bands.append(band)

        new_classifier = CustomClassifier(bands)
        self.classifiers.append(new_classifier)
        self._rebuild_index()

    def load_classifier_from_json(self, file_path):
        """Load a classifier from a JSON file and add it to the list of classifiers."""
        class CustomClassifier(BaseSignalClassifier):
            def __init__(self, bands):
                super().__init__()
                self.bands = bands

        with open(file_path, mode='r') as json_file:
            bands = json.load(json_file)

        new_classifier = CustomClassifier(bands)
        self.classifiers.append(new_classifier)
        self._rebuild_index()

# Example usage
if __name__ == "__main__":
    classifier = SignalClassifier()
    classifier.load_classifier_from_csv('signals.csv')
    classifier.load_classifier_from_json('signals.json')
    potential_signals = classifier.classify_signal(462.5625)  # Test with FRS frequency
    for signal in potential_signals:
        print(f"Potential Signal: {signal['label']} at {signal['frequency']} MHz, Bandwidth: {signal['bandwidth']} MHz, Channel: {signal.get('channel', 'N/A')}")
