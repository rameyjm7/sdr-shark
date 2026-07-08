# %% [markdown]
# Converted from test_find_peaks.ipynb.
# Keep this as a script so GitHub Linguist does not count notebooks in the repo language breakdown.

# %%
import numpy as np
import matplotlib.pyplot as plt
from sdr_plot_backend.sdr_generic import SDRGeneric
from sdr_plot_backend.signal_utils import detect_signal_peaks
import time
import pickle

# Constants
center_freq = 102e6  # Center frequency in Hz
sample_rate = 20e6   # Sample rate in Hz
fft_size = 1024 * 8  # Size of the FFT
num_captures = 20    # Number of captures to average
output_filename = "iq_data.pkl"  # Output file for pickle

# Initialize the SDR
sdr = SDRGeneric(
    sdr_type="sidekiq",
    center_freq=center_freq,
    sample_rate=sample_rate,
    bandwidth=sample_rate,
    gain=60,
    size=fft_size
)
sdr.start()

# Capture and average the FFTs
fft_magnitude_sum = np.zeros(fft_size)
iq_data_list = []
for _ in range(num_captures):
    iq_data = sdr.get_latest_samples()
    iq_data_list.append(iq_data)
    fft_result = np.fft.fftshift(np.fft.fft(iq_data, fft_size))
    fft_magnitude = np.abs(fft_result)
    fft_magnitude_sum += fft_magnitude

fft_magnitude_avg = fft_magnitude_sum / num_captures

# Convert magnitude to dB
fft_magnitude_db = 20 * np.log10(fft_magnitude_avg)

# Detect peaks and bandwidths using the function from signal_utils
signal_peaks, signal_bandwidths = detect_signal_peaks(
    fft_magnitude_db, center_freq, sample_rate, fft_size, min_peak_distance=10 * 8, threshold_offset=5
)

# Save the wideband IQ data, FFT data, and SDR settings to a pickle
with open(output_filename, 'wb') as f:
    pickle.dump({
        'center_freq': center_freq,
        'sample_rate': sample_rate,
        'bandwidth': sample_rate,
        'gain': 60,
        'iq_data_list': iq_data_list,
        'fft_magnitude_db': fft_magnitude_db,
        'frequencies': np.linspace(center_freq - sample_rate / 2, center_freq + sample_rate / 2, fft_size),
        'signal_peaks': signal_peaks,
        'signal_bandwidths': signal_bandwidths
    }, f)

# Plotting the initial FFT with detected peaks and bandwidths
plt.figure(figsize=(10, 6))
frequencies = np.linspace(center_freq - sample_rate / 2, center_freq + sample_rate / 2, fft_size) / 1e6
plt.plot(frequencies, fft_magnitude_db, color='yellow', label="Averaged FFT Magnitude (dB)")

# Plot the detected peaks and bandwidths
for i, (peak_freq, bandwidth_mhz) in enumerate(zip(signal_peaks, signal_bandwidths)):
    plt.axvline(peak_freq, color='red', linestyle='--', label=f"Peak Frequency {i+1}" if i == 0 else None)
    plt.axvline(peak_freq - bandwidth_mhz / 2, color='green', linestyle='--', label=f"Signal Start {i+1}" if i == 0 else None)
    plt.axvline(peak_freq + bandwidth_mhz / 2, color='green', linestyle='--', label=f"Signal Stop {i+1}" if i == 0 else None)
    plt.text(peak_freq, np.max(fft_magnitude_db) + 5, f"BW: {bandwidth_mhz:.2f} MHz", color='white', ha='center')

plt.axhline(np.median(fft_magnitude_db), color='gray', linestyle='--', label="Noise Floor")
plt.axhline(np.median(fft_magnitude_db) + 1, color='orange', linestyle='--', label="Adaptive Threshold")
plt.title("Detected Signals")
plt.xlabel("Frequency (MHz)")
plt.ylabel("Magnitude (dB)")
plt.grid(True)
plt.legend(loc='center left', bbox_to_anchor=(1, 0.5), fontsize='small')
plt.gca().set_facecolor('black')
plt.show()

# Print the detected peaks and bandwidths
print(f"{'Peak Frequency (MHz)':<20} {'Bandwidth (MHz)':<20}")
print("=" * 40)
for peak, bandwidth in zip(signal_peaks, signal_bandwidths):
    print(f"{peak:<20.3f} {bandwidth:<20.3f}")

    # Refine the bandwidth using a fixed 1 MHz sample rate
    # Set the SDR to the narrowband settings
    sdr.set_frequency(peak * 1e6)
    sdr.set_sample_rate(1e6)
    sdr.set_bandwidth(1e6)
    time.sleep(0.1)

    # Capture and average the FFTs for the refined settings
    fft_captures = []
    iq_data_refined_list = []
    for _ in range(num_captures):
        iq_data_refined = sdr.get_latest_samples()
        iq_data_refined_list.append(iq_data_refined)
        fft_result = np.fft.fftshift(np.fft.fft(iq_data_refined, 1024))
        fft_magnitude = np.abs(fft_result)
        fft_captures.append(fft_magnitude)

    refined_fft_magnitude_avg = np.mean(fft_captures, axis=0)

    # Convert magnitude to dB for refined FFT
    refined_fft_magnitude_db = 20 * np.log10(refined_fft_magnitude_avg)

    # Recalculate refined bandwidth
    noise_floor = np.median(refined_fft_magnitude_db)
    adaptive_threshold = noise_floor + 5  # Adjust threshold if necessary
    above_threshold = np.where(refined_fft_magnitude_db > adaptive_threshold)[0]
    if len(above_threshold) > 0:
        left_idx = above_threshold[0]
        right_idx = above_threshold[-1]
        refined_bandwidth_mhz = (right_idx - left_idx) * (1e6 / 1024)
    else:
        refined_bandwidth_mhz = 0.0  # No signal detected

    # Save the refined IQ data and FFT data to the same pickle
    with open(output_filename, 'ab') as f:
        pickle.dump({
            'peak_freq': peak,
            'refined_fft_magnitude_db': refined_fft_magnitude_db,
            'refined_frequencies': np.linspace(peak * 1e6 - 0.5 * 1e6, peak * 1e6 + 0.5 * 1e6, len(refined_fft_magnitude_db)),
            'refined_bandwidth_mhz': refined_bandwidth_mhz,
            'refined_iq_data_list': iq_data_refined_list
        }, f)

    # Plotting the refined FFT with detected bandwidth, centered around the refined peak
    plt.figure(figsize=(10, 6))
    plt.plot(np.linspace(peak * 1e6 - 0.5 * 1e6, peak * 1e6 + 0.5 * 1e6, len(refined_fft_magnitude_db)) / 1e6, refined_fft_magnitude_db, color='yellow')
    plt.axvline(peak, color='red', linestyle='--', label="Peak Frequency")
    plt.axvline((peak - refined_bandwidth_mhz / 2) / 1e6, color='green', linestyle='--', label="Signal Start")
    plt.axvline((peak + refined_bandwidth_mhz / 2) / 1e6, color='green', linestyle='--', label="Signal Stop")
    plt.title(f"Center Freq: {peak:.3f} MHz, Power: {np.max(refined_fft_magnitude_db):.2f} dB, BW: {refined_bandwidth_mhz:.3f} MHz")
    plt.xlabel("Frequency (MHz)")
    plt.ylabel("Magnitude (dB)")
    plt.grid(True)
    plt.legend()
    plt.gca().set_facecolor('black')
    plt.xlim(peak - 0.5, peak + 0.5)  # Center the plot around the refined peak
    plt.show()

    print(f"Refined Bandwidth: {refined_bandwidth_mhz:.3f} MHz")

sdr.stop()

