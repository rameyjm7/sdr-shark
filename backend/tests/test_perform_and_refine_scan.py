# perform_and_refine_scan.ipynb

import numpy as np
import matplotlib.pyplot as plt
from sdr_plot_backend.sdr_generic import SDRGeneric
from sdr_plot_backend.signal_utils import detect_signal_peaks, refine_peak_bandwidth
import time
import pickle

# Define the perform_and_refine_scan function
def perform_and_refine_scan(sdr, center_freq, sample_rate, fft_size, num_captures, output_filename="scan_data.pkl"):
    """
    Perform a wideband scan and refine the detected peaks.

    Args:
        sdr (SDRGeneric): The SDR object to perform the scan with.
        center_freq (float): Center frequency for the wideband scan in Hz.
        sample_rate (float): Sample rate for the wideband scan in Hz.
        fft_size (int): The size of the FFT.
        num_captures (int): Number of captures to average.
        output_filename (str): The filename to save the scan data.

    Returns:
        None
    """

    # Start the wideband scan
    sdr.set_frequency(center_freq)
    sdr.set_sample_rate(sample_rate)
    sdr.set_bandwidth(sample_rate)
    time.sleep(0.1)
    
    fft_magnitude_sum = np.zeros(fft_size)
    for _ in range(num_captures):
        iq_data = sdr.get_latest_samples()
        fft_result = np.fft.fftshift(np.fft.fft(iq_data, fft_size))
        fft_magnitude = np.abs(fft_result)
        fft_magnitude_sum += fft_magnitude

    fft_magnitude_avg = fft_magnitude_sum / num_captures
    fft_magnitude_db = 20 * np.log10(fft_magnitude_avg)

    # Detect peaks and bandwidths
    signal_peaks, signal_bandwidths = detect_signal_peaks(
        fft_magnitude_db, center_freq, sample_rate, fft_size, min_peak_distance=10 * 8, threshold_offset=5
    )

    # Save the wideband FFT data to a pickle file
    with open(output_filename, 'wb') as f:
        pickle.dump({
            'center_freq': center_freq,
            'sample_rate': sample_rate,
            'fft_magnitude_db': fft_magnitude_db,
            'frequencies': np.linspace(center_freq - sample_rate / 2, center_freq + sample_rate / 2, fft_size),
            'signal_peaks': signal_peaks,
            'signal_bandwidths': signal_bandwidths
        }, f)

    # Refine each detected peak
    for peak in signal_peaks:
        sdr.set_frequency(peak * 1e6)
        sdr.set_sample_rate(1e6)
        sdr.set_bandwidth(1e6)
        time.sleep(0.1)
        
        fft_captures = []
        for _ in range(num_captures):
            iq_data = sdr.get_latest_samples()
            fft_result = np.fft.fftshift(np.fft.fft(iq_data, 1024))
            fft_magnitude = np.abs(fft_result)
            fft_captures.append(fft_magnitude)

        refined_fft_magnitude_avg = np.mean(fft_captures, axis=0)
        refined_fft_magnitude_db = 20 * np.log10(refined_fft_magnitude_avg)

        noise_floor = np.median(refined_fft_magnitude_db)
        adaptive_threshold = noise_floor + 5
        above_threshold = np.where(refined_fft_magnitude_db > adaptive_threshold)[0]
        if len(above_threshold) > 0:
            left_idx = above_threshold[0]
            right_idx = above_threshold[-1]
            refined_bandwidth_mhz = (right_idx - left_idx) * (1e6 / 1024)
        else:
            refined_bandwidth_mhz = 0.0

        # Save the refined FFT data to the same pickle file
        with open(output_filename, 'ab') as f:
            pickle.dump({
                'peak_freq': peak,
                'refined_fft_magnitude_db': refined_fft_magnitude_db,
                'refined_frequencies': np.linspace(peak * 1e6 - 0.5 * 1e6, peak * 1e6 + 0.5 * 1e6, len(refined_fft_magnitude_db)),
                'refined_bandwidth_mhz': refined_bandwidth_mhz
            }, f)

# Test the perform_and_refine_scan function
# You can adjust the parameters as needed

# Initialize the SDR
sdr = SDRGeneric(
    sdr_type="sidekiq",
    center_freq=102e6,
    sample_rate=20e6,
    bandwidth=20e6,
    gain=60,
    size=1024 * 8
)
sdr.start()

# Perform the scan and refine process
perform_and_refine_scan(
    sdr,
    center_freq=102e6,
    sample_rate=20e6,
    fft_size=1024 * 8,
    num_captures=20,
    output_filename="scan_data.pkl"
)

sdr.stop()

# Load and visualize the results
with open("scan_data.pkl", 'rb') as f:
    data_list = []
    try:
        while True:
            data = pickle.load(f)
            data_list.append(data)
    except EOFError:
        pass

# Plot the wideband scan
wideband_data = data_list[0]
plt.figure(figsize=(10, 6))
plt.plot(wideband_data['frequencies'] / 1e6, wideband_data['fft_magnitude_db'], color='yellow')
plt.title("Wideband Scan")
plt.xlabel("Frequency (MHz)")
plt.ylabel("Magnitude (dB)")
plt.grid(True)
plt.gca().set_facecolor('black')
plt.show()

# Plot the refined scans
for refined_data in data_list[1:]:
    plt.figure(figsize=(10, 6))
    plt.plot(refined_data['refined_frequencies'] / 1e6, refined_data['refined_fft_magnitude_db'], color='yellow')
    plt.axvline(refined_data['peak_freq'], color='red', linestyle='--', label="Peak Frequency")
    plt.axvline((refined_data['peak_freq'] - refined_data['refined_bandwidth_mhz'] * 1e6 / 2) / 1e6, color='green', linestyle='--', label="Signal Start")
    plt.axvline((refined_data['peak_freq'] + refined_data['refined_bandwidth_mhz'] * 1e6 / 2) / 1e6, color='green', linestyle='--', label="Signal Stop")
    plt.title(f"Center Freq: {refined_data['peak_freq']:.3f} MHz, Power: {np.max(refined_data['refined_fft_magnitude_db']):.2f} dB, BW: {refined_data['refined_bandwidth_mhz']:.3f} MHz")
    plt.xlabel("Frequency (MHz)")
    plt.ylabel("Magnitude (dB)")
    plt.grid(True)
    plt.legend()
    plt.gca().set_facecolor('black')
    plt.show()
