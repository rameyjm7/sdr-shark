import json
import csv


class BaseSignalClassifier:
    def __init__(self):
        self.bands = []
        self._bands_prepared = False

    def get_bands(self):
        """Returns the list of bands handled by the classifier."""
        exported = []
        for band in self.bands:
            exported.append({k: v for k, v in band.items() if not k.startswith("_")})
        return exported

    def _prepare_bands(self):
        """Precompute bounds for faster classify/range calls."""
        if not hasattr(self, "_bands_prepared"):
            self._bands_prepared = False
        if not hasattr(self, "bands"):
            self.bands = []

        if self._bands_prepared:
            return

        for band in self.bands:
            bw = float(band.get("bandwidth", 0.0))
            center = float(band.get("frequency", 0.0))
            half_bw = bw * 0.5
            band["_half_bw"] = half_bw
            band["_lo"] = center - half_bw
            band["_hi"] = center + half_bw
            band["_result"] = {
                "label": band["label"],
                "frequency": band["frequency"],
                "bandwidth": band["bandwidth"],
                "channel": band.get("channel", ""),
                "metadata": band.get("metadata", ""),
            }

        self._bands_prepared = True

    def dump_bands(self, file_path, file_format='json'):
        """
        Dumps the list of bands to a file in the specified format (JSON or CSV).
        
        :param file_path: Path to the output file.
        :param file_format: Format to save the bands ('json' or 'csv').
        """
        if file_format == 'json':
            with open(file_path, 'w') as f:
                json.dump(self.bands, f, indent=4)
        elif file_format == 'csv':
            if self.bands:
                with open(file_path, 'w', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=self.bands[0].keys())
                    writer.writeheader()
                    for band in self.bands:
                        writer.writerow(band)
        else:
            raise ValueError("Unsupported file format. Use 'json' or 'csv'.")

    
    def classify_signal(self, frequency_mhz, bandwidth_mhz=None):
        self._prepare_bands()
        matches = []
        for band in self.bands:
            if band["_lo"] <= frequency_mhz <= band["_hi"]:
                matches.append(band["_result"])
        return matches

    def get_signals_in_range(self, start_freq_mhz, end_freq_mhz):
        self._prepare_bands()
        matches = []
        for band in self.bands:
            if start_freq_mhz <= band["frequency"] <= end_freq_mhz:
                matches.append(band["_result"])
        return matches
