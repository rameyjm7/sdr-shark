
import numpy as np
import pandas as pd

def process_fft_data(records, metadata, threshold_dB = 10, func = np.mean):
    """
    Process FFT data to calculate noise floor, detect signals, and prepare signal information.
    Returns: 
        - freq (array): Frequency array
        - fft_magnitude (array): Averaged FFT magnitude
        - noise_riding_threshold (float): Noise riding threshold value
        - signals (list): List of detected signals with details
        - plot ranges (tuple): Lower and upper plot range for plotting purposes
        - freq_bound_left (float): Left bound frequency for plotting
        - freq_bound_right (float): Right bound frequency for plotting
    """
    # Calculate the noise floor based on the averaging of all records
    all_fft_data = np.array([record['fft_magnitude'] for record in records])
    averaged_fft_data = func(all_fft_data, axis=0)
    noise_floor = np.mean(np.sort(averaged_fft_data)[:int(len(averaged_fft_data) * 0.2)])  # Use the lowest 20% of FFT values
    noise_riding_threshold = noise_floor + threshold_dB  # 10 dB above the noise floor

    # Set the plot ranges
    lower_plot_range = noise_floor - 20  # 20 dB below the noise floor
    upper_plot_range = noise_floor + 60  # 60 dB above the noise floor

    # Calculate the left and right frequency bounds
    freq_bound_left = metadata['frequency'] / 1e6 - metadata['sample_rate'] / 2e6
    freq_bound_right = metadata['frequency'] / 1e6 + metadata['sample_rate'] / 2e6

    # Calculate the frequency array (centered at 0)
    fft_magnitude = np.mean(np.array([record['fft_magnitude'] for record in records]), axis=0)
    freq = np.fft.fftshift(np.fft.fftfreq(len(fft_magnitude), d=1/metadata['sample_rate'])) / 1e6

    # Identify signals above the noise riding threshold
    signal_indices = np.where(fft_magnitude > noise_riding_threshold)[0]

    signals = []
    if len(signal_indices) > 0:
        current_signal = [signal_indices[0]]

        for i in range(1, len(signal_indices)):
            if signal_indices[i] - signal_indices[i - 1] <= 100:  # Merge peaks within 100 FFT bins
                current_signal.append(signal_indices[i])
            else:
                # Calculate the center frequency and bandwidth of the current signal
                start_freq = freq[current_signal[0]]
                end_freq = freq[current_signal[-1]]
                center_freq = (start_freq + end_freq) / 2
                bandwidth = end_freq - start_freq
                peak_power = np.max(fft_magnitude[current_signal])
                avg_power = np.mean(fft_magnitude[current_signal])

                signals.append({
                    "center_freq": center_freq,
                    "bandwidth": bandwidth,
                    "peak_power": peak_power,
                    "avg_power": avg_power,
                    "start_freq": start_freq,
                    "end_freq": end_freq
                })

                current_signal = [signal_indices[i]]

        # Handle the last signal
        if len(current_signal) > 0:
            start_freq = freq[current_signal[0]]
            end_freq = freq[current_signal[-1]]
            center_freq = (start_freq + end_freq) / 2
            bandwidth = end_freq - start_freq
            peak_power = np.max(fft_magnitude[current_signal])
            avg_power = np.mean(fft_magnitude[current_signal])

            signals.append({
                "center_freq": center_freq,
                "bandwidth": bandwidth,
                "peak_power": peak_power,
                "avg_power": avg_power,
                "start_freq": start_freq,
                "end_freq": end_freq
            })

    return freq, fft_magnitude, noise_riding_threshold, signals, (lower_plot_range, upper_plot_range), freq_bound_left, freq_bound_right
