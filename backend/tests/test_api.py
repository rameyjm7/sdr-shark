import pytest



def test_find_peaks():
    import numpy as np
    from sdr_plot_backend.sdr_generic import SDRGeneric
    from sdr_plot_backend.signal_utils import perform_and_refine_scan  # Import the function
    # Constants
    center_freq = 102e6  # Center frequency in Hz
    sample_rate = 20e6   # Sample rate in Hz
    fft_size = 1024 * 8  # Size of the FFT
    num_captures = 20    # Number of captures to average

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

    # Perform the scan and refinement
    refined_signals = perform_and_refine_scan(
        sdr=sdr,
        wide_sample_rate=sample_rate,
        wide_fft_size=fft_size,
        num_captures=num_captures,
    )

    # Stop the SDR
    sdr.stop()

    # Print the refined signals
    print(f"{'Center Frequency (MHz)':<20} {'Power (dB)':<20} {'Refined Bandwidth (MHz)':<20}")
    print("=" * 60)
    for freq, power, bandwidth in refined_signals:
        print(f"{freq:<20.3f} {power:<20.2f} {bandwidth:<20.3f}")

