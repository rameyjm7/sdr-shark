# %% [markdown]
# Converted from test_peak_detector.ipynb.
# Keep this as a script so GitHub Linguist does not count notebooks in the repo language breakdown.

# %%
import numpy as np
import matplotlib.pyplot as plt
from sdr_plot_backend.signal_utils import PeakDetector  # Replace with actual import path
from sdr_plot_backend.sdr_generic import SDRGeneric

def sweep_and_detect_peaks(start_freq, stop_freq, sample_rate, bandwidth, sdr_type="hackrf", averaging_count=30):
    fft_size = 1024 * 8
    sdr = SDRGeneric(sdr_type, center_freq=start_freq, sample_rate=sample_rate, bandwidth=bandwidth, gain=30, size=fft_size)
    sdr.start()

    detector = PeakDetector(sdr=sdr, averaging_count=averaging_count)

    full_fft = np.zeros(fft_size)
    current_freq = start_freq

    while current_freq <= stop_freq:
        # Receive data once (no thread)
        detector.receive_data(once=True)

        # Detect peaks using the latest FFT data
        detected_peaks = detector.detect_signal_peaks(
            center_freq=current_freq,
            sample_rate=sample_rate,
            fft_size=fft_size,
            min_peak_distance=80,
            threshold_offset=3
        )

        # Accumulate FFT results for plotting
        with detector.fft_lock:
            if detector.fft_results:
                full_fft += np.mean(detector.fft_results, axis=0)

        # Move to the next frequency
        current_freq += bandwidth
        sdr.set_frequency(current_freq)

    sdr.stop()

    # Average the accumulated FFT data
    averaged_fft_db = full_fft / ((stop_freq - start_freq) / bandwidth + 1)

    # Plot the results in dark mode
    frequencies = np.linspace(start_freq, stop_freq, fft_size)

    plt.figure(figsize=(10, 6))
    plt.plot(frequencies / 1e6, averaged_fft_db, color='yellow')
    for peak in detected_peaks:
        plt.axvline(peak['frequency'] / 1e6, color='red', linestyle='--')
        plt.text(peak['frequency'] / 1e6, peak['power'], f'{peak["frequency"] / 1e6:.2f} MHz\n{peak["power"]:.2f} dB', color='white')
    plt.title("Detected Peaks")
    plt.xlabel("Frequency (MHz)")
    plt.ylabel("Magnitude (dB)")
    plt.grid(True, color='gray')
    plt.gca().set_facecolor('black')
    plt.show()

    # Print out the detected peaks and their power levels
    print("Detected peaks:")
    for peak in detected_peaks:
        print(f"Frequency: {peak['frequency'] / 1e6:.2f} MHz, Power: {peak['power']:.2f} dB")

# Sweep settings
start_freq = 80e6  # Start frequency in Hz
stop_freq = 150e6  # Stop frequency in Hz
sample_rate = 60e6  # Sample rate in Hz
bandwidth = 60e6  # Bandwidth in Hz

# Perform the sweep and detect peaks
sweep_and_detect_peaks(start_freq, stop_freq, sample_rate, bandwidth)

