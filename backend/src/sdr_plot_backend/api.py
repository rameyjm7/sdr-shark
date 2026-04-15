import atexit
import threading
import time
from collections import deque
from datetime import datetime
from werkzeug.utils import secure_filename
import os
import numpy as np
import json
from flask import Blueprint, jsonify, request, current_app, Response
from numba import jit

from sdr_plot_backend.signal_utils import perform_and_refine_scan, PeakDetector  # Import the new utility
from sdr_plot_backend.utils import vars

api_blueprint = Blueprint('api', __name__)

sample_buffer = np.zeros(vars.sample_size, dtype=np.complex64)  # Increase buffer size to decrease RBW
data_buffer = deque(maxlen=vars.sdr_averagingCount())
waterfall_buffer = deque(maxlen=2000)  # Buffer for waterfall data
waterfall_buffer2 = deque(maxlen=2000)  # Buffer for waterfall data

data_lock = threading.Lock()
fft_data = {
    'original_fft': [],
    'original_fft2': [],
    'max' : [],
    'peaks': [],
    'persist': []
}
running = True
reset_max_trace = False
reset_persist_trace = False
main_fft_updated_at = 0.0
scanner_fft_updated_at = 0.0
main_frame_seq = 0
scanner_frame_seq = 0
analysis_peak_memory = {}
analysis_memory_lock = threading.Lock()


def _quantize_mhz(value, step_mhz=0.05):
    n = float(value)
    step = max(0.001, float(step_mhz))
    return round(round(n / step) * step, 3)

def _safe_int(value, default, min_value=None, max_value=None):
    try:
        n = int(float(value))
    except Exception:
        n = int(default)
    if min_value is not None:
        n = max(min_value, n)
    if max_value is not None:
        n = min(max_value, n)
    return n

def _active_sdr_key():
    """Return the currently active settings key with safe fallback."""
    key = getattr(vars, "sdr_name", None)
    if key in vars.sdr_settings:
        return key
    if "sidekiq" in vars.sdr_settings:
        return "sidekiq"
    return next(iter(vars.sdr_settings.keys()))

def _tail_deque_rows(buffer, max_rows):
    """Return the newest max_rows from a deque without materializing the whole deque."""
    if max_rows <= 0:
        return []
    rows = []
    for row in reversed(buffer):
        rows.append(row)
        if len(rows) >= max_rows:
            break
    rows.reverse()
    return rows


def _to_builtin(value):
    """Convert NumPy containers/scalars into JSON-serializable Python types."""
    if isinstance(value, dict):
        return {k: _to_builtin(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, deque)):
        return [_to_builtin(v) for v in value]
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    return value

@jit(nopython=True)
def downsample(data, target_length=256):
    if target_length <= 0:
        target_length = 1
    if target_length >= len(data):
        return data.copy()
    step = len(data) / target_length
    downsampled = np.zeros(target_length, dtype=np.float64)
    for i in range(target_length):
        start = int(i * step)
        end = int((i + 1) * step)
        chunk = data[start:end]
        avg = np.mean(chunk)
        downsampled[i] = avg
    return downsampled

def capture_samples():
    global sample_buffer
    while vars.sdr0 is None:
        time.sleep(0.1)
    sample_buffer = vars.sdr0.get_latest_samples()

def process_fft(samples):
    fft_result = np.fft.fftshift(np.fft.fft(samples))
    # Add epsilon to avoid log10(0) warnings on silent bins.
    fft_magnitude = 20 * np.log10(np.abs(fft_result) + 1e-12)
    return fft_magnitude

def generate_fft_data():
    global reset_max_trace, reset_persist_trace, main_fft_updated_at, main_frame_seq
    full_fft = []
    current_freq = vars.sweep_settings['frequency_start']
    fft_max = None
    fft_persist_data = None  # Initialize persistence trace
    persistence_decay = vars.persistence_decay  # Fetch decay factor (e.g., 0.9)
    
    while running:
        try:
            if reset_max_trace:
                fft_max = None
                reset_max_trace = False
            if reset_persist_trace:
                fft_persist_data = None
                reset_persist_trace = False

            # Capture and process FFT samples
            capture_samples()
            current_fft = process_fft(sample_buffer)

            # Suppress DC spike if enabled
            if vars.dc_suppress:
                dc_index = len(current_fft) // 2
                current_fft[dc_index] = current_fft[dc_index + 1]

            # Normalize invalid (inf) values
            current_fft = np.where(np.isinf(current_fft), -20, current_fft)

            # Update maximum FFT trace
            fft_max = current_fft if fft_max is None else np.maximum(fft_max, current_fft)

            # Handle persistence trace with decay
            if fft_persist_data is None:
                fft_persist_data = current_fft  # Initialize persistence trace
            else:
                fft_persist_data = (
                    persistence_decay * fft_persist_data + (1 - persistence_decay) * current_fft
                )
            fft_data['persist'] = fft_persist_data

            sdr_key = _active_sdr_key()
            averaging_count = max(1, int(vars.sdr_settings[sdr_key].averagingCount))

            if vars.sweeping_enabled:
                # Perform sweeping logic
                if full_fft is None:
                    full_fft = current_fft
                elif type(full_fft) is list:
                    full_fft = np.concatenate((full_fft, current_fft))
                else:
                    full_fft = np.concatenate((full_fft, current_fft))

                # Tune to the next frequency
                current_freq += 60e6
                if current_freq > vars.sweep_settings['frequency_stop']:
                    current_freq = vars.sweep_settings['frequency_start']

                    # Process completed sweep
                    averaged_fft = np.array(full_fft)

                    # Calculate noise floor using the lowest 20% of FFT values
                    noise_floor = np.mean(np.percentile(averaged_fft, 20))
                    vars.signal_stats["noise_floor"] = round(noise_floor, 3)
                    vars.signal_stats["max"] = round(float(np.max(averaged_fft)), 3)

                    # Downsample FFT data for output
                    downsampled_fft_avg = downsample(averaged_fft, len(current_fft))
                    downsampled_fft = downsample(current_fft, len(current_fft))

                    # Update shared data
                with data_lock:
                    fft_data['original_fft'] = downsampled_fft_avg.tolist()
                    waterfall_buffer.append(downsampled_fft.tolist())
                    main_fft_updated_at = time.time()
                    main_frame_seq += 1

                    # Reset for the next sweep
                    full_fft = []

                # Update SDR frequency
                vars.sdr_settings[sdr_key].frequency = current_freq
                vars.sdr0.set_frequency(current_freq)
                time.sleep(0.05)
            else:
                # Normal operation without sweeping
                if len(full_fft) == 0:
                    full_fft = current_fft
                else:
                    full_fft = (full_fft[:vars.sample_size] * (averaging_count - 1) + current_fft) / averaging_count

                # Calculate noise floor and signal stats
                noise_floor = np.mean(np.percentile(full_fft, 20))
                vars.signal_stats["noise_floor"] = round(noise_floor, 3)
                vars.signal_stats["noise_riding_threshold"] = round(noise_floor + vars.peak_threshold_minimum_dB, 3)
                vars.signal_stats['max'] = round(float(np.max(full_fft)), 3)

                # Determine maximum frequency
                max_index = np.argmax(full_fft)
                frequency_step = vars.sdr_sampleRate() / vars.sample_size
                max_freq = ((max_index * frequency_step) + (vars.sdr_frequency() - vars.sdr_sampleRate() / 2)) / 1e6
                vars.signal_stats['max_freq'] = round(float(max_freq), 3)

                # Signal detection logic
                vars.signal_stats['signal_detected'] = 1 if vars.signal_stats['max'] > vars.signal_stats["noise_riding_threshold"] else 0

                # Update shared data
                bin_count = _safe_int(vars.waterfall_bin_count, default=2048, min_value=64, max_value=max(64, vars.sample_size))
                with data_lock:
                    fft_data['original_fft'] = full_fft.tolist()
                    fft_data['max'] = fft_max.tolist()
                    fft_data['persist'] = fft_persist_data.tolist()
                    waterfall_buffer.append(
                        downsample(current_fft, bin_count).tolist()
                    )
                    main_fft_updated_at = time.time()
                    main_frame_seq += 1
            # Clear stale FFT error once a frame processes successfully.
            vars.signal_stats.pop("fft_error", None)
        except Exception as e:
            vars.signal_stats["fft_error"] = str(e)
            time.sleep(0.05)

def radio_scanner():
    global scanner_fft_updated_at, scanner_frame_seq
    nfft = 8*1024
    detector = PeakDetector(sdr=vars.sdr0, averaging_count=vars.sdr_averagingCount(), nfft=nfft)
    detector.start_receiving_data()
    sdr_name = "sidekiq"
    
    while running:
        minPeakDistance_index = int(vars.minPeakDistance * vars.sdr_sampleRate()/1e6)
        # Simulate continuous running until stopped
        time.sleep(1)  # Adjust based on how frequently you want to process data

        # Get processed scanner data outside shared lock to avoid starving FFT producer updates.
        try:
            processed_data = detector.get_processed_data()
            if processed_data:
                freq, fft_magnitude, noise_riding_threshold, signals, plot_ranges, freq_bound_left, freq_bound_right = processed_data
                with data_lock:
                    fft_data['original_fft2'] = fft_magnitude.tolist()  # Store the FFT data
                    fft_data['peaks'] = signals  # Store the detected peaks
                    if len(fft_magnitude) > 0:
                        bin_count = _safe_int(vars.waterfall_bin_count, default=2048, min_value=64, max_value=max(64, len(fft_magnitude)))
                        waterfall_buffer2.append(
                            downsample(np.array(fft_magnitude), bin_count).tolist()
                        )
                    scanner_fft_updated_at = time.time()
                    scanner_frame_seq += 1
            # Clear stale scanner error once scanner loop succeeds.
            vars.signal_stats.pop("scanner_error", None)
        except Exception as e:
            vars.signal_stats["scanner_error"] = str(e)
            time.sleep(0.1)
                    
    
    detector.stop_receiving_data()

fft_thread = threading.Thread(target=generate_fft_data)
scanner_thread = threading.Thread(target=radio_scanner)
fft_thread.start()
scanner_thread.start()

@api_blueprint.route('/api/data_ext')
def get_data_ext():
    fft_max_response = [float(x) for x in fft_data['max']]
    persistance_response = [float(x) for x in fft_data['persist']]
    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    response = {
        'max': fft_max_response,
        'persistance': persistance_response,
        'time': current_time
    }
    return jsonify(response)


@api_blueprint.route('/api/reset_fft_trace', methods=['POST'])
def reset_fft_trace():
    global reset_max_trace, reset_persist_trace
    payload = request.get_json(silent=True) or {}
    trace = str(payload.get('trace', 'all')).lower()

    with data_lock:
        if trace in ('max', 'all'):
            fft_data['max'] = []
            reset_max_trace = True
        if trace in ('persist', 'persistence', 'all'):
            fft_data['persist'] = []
            reset_persist_trace = True

    return jsonify({'status': 'success', 'trace': trace})

@api_blueprint.route('/api/data')
def get_data():
    with data_lock:
        max_waterfall_rows = _safe_int(vars.waterfall_samples, default=100, min_value=1, max_value=2000)
        max_payload_cells = 300000
        main_fft_snapshot = list(fft_data['original_fft'])
        scanner_fft_snapshot = list(fft_data['original_fft2'])
        main_waterfall_snapshot = _tail_deque_rows(waterfall_buffer, max_waterfall_rows)
        scanner_waterfall_snapshot = _tail_deque_rows(waterfall_buffer2, max_waterfall_rows)
        main_ts_snapshot = main_fft_updated_at
        scanner_ts_snapshot = scanner_fft_updated_at
        main_seq_snapshot = main_frame_seq
        scanner_seq_snapshot = scanner_frame_seq
        
        peaks_snapshot = list(fft_data['peaks'])

    source = request.args.get('source', 'main').lower()
    scanner_fresh = (time.time() - scanner_ts_snapshot) <= 3.0
    scanner_available = scanner_fresh and len(scanner_fft_snapshot) > 0
    main_available = len(main_fft_snapshot) > 0

    if source == 'scanner':
        fft_snapshot = scanner_fft_snapshot if scanner_available else (main_fft_snapshot or scanner_fft_snapshot)
        waterfall_snapshot = scanner_waterfall_snapshot if scanner_available else (main_waterfall_snapshot or scanner_waterfall_snapshot)
    elif source == 'auto':
        # Prefer live main FFT for the main UI; scanner is fallback only.
        if main_available:
            fft_snapshot = main_fft_snapshot
            waterfall_snapshot = main_waterfall_snapshot or scanner_waterfall_snapshot
        else:
            fft_snapshot = scanner_fft_snapshot or main_fft_snapshot
            waterfall_snapshot = scanner_waterfall_snapshot or main_waterfall_snapshot
    else:  # default main
        fft_snapshot = main_fft_snapshot if main_available else (scanner_fft_snapshot or main_fft_snapshot)
        waterfall_snapshot = main_waterfall_snapshot if main_waterfall_snapshot else (scanner_waterfall_snapshot or main_waterfall_snapshot)

    fft_response = [float(x) for x in fft_snapshot]

    if waterfall_snapshot:
        cols = len(waterfall_snapshot[0]) if waterfall_snapshot[0] is not None else 0
        stride = max(1, int(np.ceil((len(waterfall_snapshot) * max(1, cols)) / max_payload_cells)))
        waterfall_rows = waterfall_snapshot[::stride]
    else:
        waterfall_rows = []
    waterfall_response = [[float(y) for y in x] for x in waterfall_rows]
        
    center_freq_mhz = float(vars.sdr_frequency() / 1e6)
    peaks_response = []
    for idx, peak in enumerate(peaks_snapshot):
        rel_center = float(peak['center_freq'])
        rel_start = float(peak['start_freq'])
        rel_end = float(peak['end_freq'])
        bw_mhz = float(peak.get('bandwidth', 0.0))
        abs_center = center_freq_mhz + rel_center
        abs_start = center_freq_mhz + rel_start
        abs_end = center_freq_mhz + rel_end
        classifications = vars.classifier.classify_signal(abs_center, bw_mhz)

        peaks_response.append({
            'index': idx,
            # Keep legacy relative fields for existing plots.
            'frequency': rel_center,
            'start_freq': rel_start,
            'end_freq': rel_end,
            # Add absolute MHz fields for detection/classification.
            'absolute_frequency': abs_center,
            'absolute_start_freq': abs_start,
            'absolute_end_freq': abs_end,
            'avg_power': float(peak['avg_power']),
            'peak_power': float(peak['peak_power']),
            'bandwidth': bw_mhz,
            'classification': [
                {'label': c.get('label', 'Unknown'), 'channel': c.get('channel', 'N/A')}
                for c in classifications
            ],
        })


    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

    response = {
        'fft': fft_response,
        'peaks': peaks_response,
        'waterfall': waterfall_response,
        'waterfallRows': len(waterfall_response),
        'time': current_time,
        'settings': vars.get_settings(),
        'mainFrameSeq': int(main_seq_snapshot),
        'scannerFrameSeq': int(scanner_seq_snapshot),
        'scannerFresh': bool(scanner_available),
        'fftError': vars.signal_stats.get("fft_error"),
        'scannerError': vars.signal_stats.get("scanner_error"),
    }

    # Convert all settings values to native Python types
    settings = response['settings']
    settings['frequency'] = float(settings['frequency'])
    settings['sample_rate'] = float(settings['sample_rate'])
    settings['bandwidth'] = float(settings['bandwidth'])
    settings['gain'] = float(settings['gain'])
    settings['sweep_settings'] = {k: float(v) for k, v in settings['sweep_settings'].items()}

    if vars.sweeping_enabled:
        response['frequency_start'] = float(vars.sweep_settings['frequency_start'])
        response['frequency_stop'] = float(vars.sweep_settings['frequency_stop'])
        response['bandwidth'] = float(vars.sweep_settings['bandwidth'])
        if response['frequency_start'] < 1e6:
            print("error")

    return jsonify(response)


@api_blueprint.route('/api/analytics')
def get_analytics():
    num_digits = 3
    payload = {}
    with data_lock:
        # General classifications based on the current SDR frequency and bandwidth
        current_frequency = vars.sdr_frequency() / 1e6  # Convert to MHz
        current_bandwidth = vars.sdr_bandwidth() / 1e6  # Convert to MHz
        classifications = vars.classifier.get_signals_in_range(current_frequency, current_bandwidth)
        payload['classifications'] = classifications
        payload['signal_stats'] = vars.signal_stats
        
        # Use the processed peaks data from radio_scanner
        peaks_data = list(fft_data['peaks'])  # Retrieve peaks data
    peaks_response = []
    now = time.time()
    retention_sec = max(1.0, float(getattr(vars, "analysis_retention_sec", 10.0)))

    with analysis_memory_lock:
        for peak in peaks_data:
            freq = round(float(peak['center_freq'] + vars.sdr_frequency()/1e6), num_digits)
            freq_start = round(float(peak['start_freq'] + vars.sdr_frequency()/1e6), num_digits)
            freq_end = round(float(peak['end_freq'] + vars.sdr_frequency()/1e6), num_digits)
            peak_power = float(peak['peak_power'])
            bandwidth = round(float(peak.get('bandwidth', 0.0)), num_digits)
            avg_power = float(peak.get('avg_power', 0.0))

            classifications = vars.classifier.classify_signal(freq, bandwidth)
            classifications_list = [{"label": c['label'], "channel": c.get('channel', 'N/A')} for c in classifications]
            primary_label = classifications_list[0]["label"] if classifications_list else "N/A"
            key = (
                _quantize_mhz(freq, step_mhz=0.05),
                _quantize_mhz(max(0.0, bandwidth), step_mhz=0.05),
                primary_label,
            )

            previous = analysis_peak_memory.get(key)
            if previous is None or (now - float(previous.get("last_seen_ts", 0.0))) > retention_sec:
                seen_count = 1
                first_seen_ts = now
            else:
                seen_count = int(previous.get("seen_count", 0)) + 1
                first_seen_ts = float(previous.get("first_seen_ts", now))

            analysis_peak_memory[key] = {
                'peak': f'Peak {len(analysis_peak_memory) + 1}',
                'frequency': freq,
                'freq_start': freq_start,
                'freq_end': freq_end,
                'peak_power': peak_power,
                'avg_power': avg_power,
                'bandwidth': bandwidth,
                'classification': classifications_list,
                'seen_count': seen_count,
                'first_seen_ts': first_seen_ts,
                'last_seen_ts': now,
            }

        expired_keys = []
        for key, row in analysis_peak_memory.items():
            age_seconds = now - float(row.get("last_seen_ts", 0.0))
            if age_seconds > retention_sec:
                expired_keys.append(key)
                continue
            out = dict(row)
            out['age_seconds'] = round(age_seconds, 2)
            peaks_response.append(out)

        for key in expired_keys:
            analysis_peak_memory.pop(key, None)

    peaks_response.sort(key=lambda x: float(x.get('frequency', 0.0)))
    payload['peaks'] = peaks_response
    
    return jsonify(_to_builtin(payload))


@api_blueprint.route('/api/signal_detection', methods=['POST'])
def signal_detection():
    marker_data = request.json
    vertical_lines = marker_data.get('vertical_lines', [])
    horizontal_lines = marker_data.get('horizontal_lines', [])
    filename = marker_data.get("filename")

    if not vertical_lines:
        return jsonify({"success": "No vertical_lines markers to analyze"}), 200

    if not horizontal_lines:
        horizontal_lines = [vars.signal_stats['noise_floor']]
    
    # # Assume vertical_lines are in MHz and horizontal_lines are in dB

    # # Convert vertical line positions to FFT indexes (assuming some relationship between frequency and FFT bin)
    # x_start = min(vertical_lines) * 1024
    # x_end = max(vertical_lines) * 1024

    # # Convert horizontal line positions to amplitude bounds (assuming some dB to amplitude conversion)
    # y_start = horizontal_lines[0]  # Lower bound in dB
    # y_end = horizontal_lines[1] if len(horizontal_lines) > 1 else max(y_start, y_start + 1)  # Upper bound

    # selected_data = []

    # with data_lock:
    #     # Assuming `waterfall_buffer` contains rows of FFT data and 'y' indexes represent different time slices
    #     for i in range(len(waterfall_buffer)):
    #         row = waterfall_buffer[i]
    #         selected_row = row[int(x_start):int(x_end) + 1]
    #         if all(y_start <= value <= y_end for value in selected_row):
    #             selected_data.append(selected_row)

    # if not selected_data:
    #     return jsonify({"error": "No data found within the specified markers"}), 400

    # # Convert selected data to 16-bit signed integer format (assuming the FFT data is float)
    # iq_data = []
    # for row in selected_data:
    #     for value in row:
    #         # Scale and clip the value within the int16 range
    #         iq_value = int(np.clip(value * 32767, -32768, 32767))
    #         iq_data.append(iq_value)

    # # Define the base file name
    # timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # base_filename = f'{filename}_{timestamp}'

    # # Save the IQ data in 16T binary format
    # iq_file_path = os.path.join(vars.recordings_dir, f'{base_filename}.iq')
    
    # with open(iq_file_path, 'wb') as iq_file:
    #     iq_file.write(np.array(iq_data, dtype=np.int16).tobytes())

    # # Save the original selected data in JSON format
    # json_data_file_path = os.path.join(vars.recordings_dir, f'{base_filename}_data.json')
    # with open(json_data_file_path, 'w') as json_data_file:
    #     json.dump(selected_data, json_data_file, indent=4)

    # # Gather SDR settings
    # sdr_settings = {
    #     "frequency": vars.sdr_settings[vars.sdr_name].frequency,
    #     "bandwidth": vars.sdr_settings[vars.sdr_name].bandwidth,
    #     "sample_rate": vars.sdr_settings[vars.sdr_name].sampleRate,
    #     "gain": vars.sdr_settings[vars.sdr_name].gain,
    #     "sdr": vars.sdr_name,
    #     "timestamp": timestamp
    # }

    # # Save SDR settings to a JSON file
    # json_file_path = os.path.join(vars.recordings_dir, f'{base_filename}.json')
    # with open(json_file_path, 'w') as json_file:
    #     json.dump(sdr_settings, json_file, indent=4)

    return jsonify({
        "message": "Signal detection data saved successfully",
        # "iq_file_path": iq_file_path,
        # "json_file_path": json_file_path,
        # "json_data_file_path": json_data_file_path
    })


@api_blueprint.route('/api/noise_floor', methods=['GET'])
def get_noise_floor():
    # Retrieve the noise floor from the global signal_stats dictionary
    noise_floor = vars.signal_stats.get("noise_floor", None)
    
    if noise_floor is None:
        return jsonify({"error": "Noise floor not calculated yet"}), 500

    # Round the noise floor to two decimal places
    rounded_noise_floor = round(float(noise_floor), 2)
    return jsonify({"noise_floor": rounded_noise_floor})


@api_blueprint.route('/api/get_classifiers', methods=['GET'])
def get_classifiers():
    classifiers = vars.classifier.get_all_bands()
    return jsonify(classifiers)


@api_blueprint.route('/api/download_all_bands', methods=['GET'])
def download_all_bands():
    try:
        all_bands = vars.classifier.get_all_bands()
        # Convert to JSON format with pretty print
        json_data = json.dumps(all_bands, indent=4)

        # Create a response with the pretty-printed JSON as a file download
        response = Response(json_data, mimetype='application/json')
        response.headers['Content-Disposition'] = 'attachment; filename=all_bands.json'
        return response
    except Exception as e:
        current_app.logger.error(f"Error downloading bands: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_blueprint.route('/api/upload_classifier', methods=['POST'])
def upload_classifier():
    # Check if the post request has the file part
    if 'file' not in request.files:
        return jsonify({'status': 'error', 'message': 'No file part'}), 400

    file = request.files['file']
    
    # If user does not select a file, the browser submits an empty file without a filename
    if file.filename == '':
        return jsonify({'status': 'error', 'message': 'No selected file'}), 400

    # Ensure the file has a secure filename
    filename = secure_filename(file.filename)
    file_path = os.path.join(vars.classifiers_path, filename)
    
    # Save the file
    file.save(file_path)

    # Determine file extension to choose appropriate loading method
    file_extension = os.path.splitext(filename)[1].lower()

    try:
        if file_extension == '.csv':
            vars.classifier.load_classifier_from_csv(file_path)
        elif file_extension == '.json':
            vars.classifier.load_classifier_from_json(file_path)
        else:
            return jsonify({'status': 'error', 'message': 'Unsupported file type'}), 400
        
        return jsonify({'status': 'success', 'message': f'Classifier {filename} uploaded and loaded successfully'})
    
    except Exception as e:
        current_app.logger.error(f"Error loading classifier: {e}")
        return jsonify({'status': 'error', 'message': str(e)}), 500

@api_blueprint.route('/api/select_sdr', methods=['POST'])
def select_sdr():
    sdr_name = request.json.get('sdr_name', 'hackrf')
    result = vars.reselect_radio(sdr_name)
    return jsonify({'status': 'success', 'result': result})


@api_blueprint.route('/api/sdr_devices', methods=['GET'])
def get_sdr_devices():
    try:
        devices = vars.sdr0.list_devices()
        selected = vars.sdr0.device_id
        return jsonify(_to_builtin({'devices': devices, 'selected': selected}))
    except Exception as e:
        return jsonify({'devices': [], 'selected': None, 'error': str(e)}), 200

@api_blueprint.route('/api/get_settings', methods=['GET'])
def get_settings():
    settings = {
        'sdr': vars.sdr0.device_id or vars.radio_name,
        'frequency': vars.sdr_frequency() / 1e6,  # Convert to MHz
        'gain': vars.sdr_gain(),
        'sampleRate': vars.sdr_sampleRate() / 1e6,  # Convert to MHz
        'bandwidth': vars.sdr_bandwidth() / 1e6,  # Convert to MHz
        'averagingCount': vars.sdr_settings[vars.sdr_name].averagingCount,
        'dcSuppress': vars.dc_suppress,
        'showWaterfall': vars.show_waterfall,
        'updateInterval': vars.sleeptime * 1000,  # Convert to ms
        'waterfallSamples': vars.waterfall_samples,
        'waterfallBinCount': vars.waterfall_bin_count,
        'frequency_start': vars.sweep_settings['frequency_start'] / 1e6,
        'frequency_stop': vars.sweep_settings['frequency_stop'] / 1e6,
        'sweeping_enabled': vars.sweeping_enabled,
        'peakThreshold' : vars.peak_threshold_minimum_dB,
        'showFirstTrace': vars.showFirstTrace,
        'showSecondTrace': vars.showSecondTrace,
        'showMaxTrace': vars.showMaxTrace,
        'showPeristanceTrace': vars.showPeristanceTrace,
        'lockBandwidthSampleRate': vars.lockBandwidthSampleRate,
        'analysisRetentionSec': float(getattr(vars, "analysis_retention_sec", 10.0)),
        'signal_stats' : vars.signal_stats
    }
    return jsonify(_to_builtin(settings))

@api_blueprint.route('/api/update_settings', methods=['POST'])
def update_settings():
    try:
        settings = request.json
        if settings['frequency'] == 0 or  settings['frequency'] is None or  settings['sampleRate'] is None or settings['bandwidth'] is None:
            return jsonify(_to_builtin({'success': True, 'settings': settings}))
        new_settings = settings.copy()
        # Update vars with the new settings and save them
        new_settings['frequency'] = settings['frequency'] * 1e6
        new_settings['frequency_stop'] = settings['frequency_stop'] * 1e6
        new_settings['frequency_start'] = settings['frequency_start'] * 1e6
        new_settings['sampleRate'] = settings['sampleRate'] * 1e6
        new_settings['bandwidth'] = settings['bandwidth'] * 1e6
        vars.apply_settings(new_settings)
        vars.save_settings()

        return jsonify(_to_builtin({'success': True, 'settings': settings}))
    except Exception as e:
        print(e)
        return jsonify({'success': False, 'error': str(e)})

@api_blueprint.route('/api/start_sweep', methods=['POST'])
def start_sweep():
    vars.sweeping_enabled = True
    return jsonify({'status': 'success', 'sweeping_enabled': vars.sweeping_enabled})

@api_blueprint.route('/api/stop_sweep', methods=['POST'])
def stop_sweep():
    vars.sweeping_enabled = False
    return jsonify({'status': 'success', 'sweeping_enabled': vars.sweeping_enabled})

@atexit.register
def cleanup():
    global running
    running = False
    try:
        vars.sdr0.stop()
    except Exception:
        pass
    for thread in (fft_thread, scanner_thread):
        try:
            if thread.is_alive():
                thread.join(timeout=2)
        except BaseException:
            pass


@api_blueprint.route('/api/move', methods=['POST'])
def move_file():
    data = request.get_json()
    old_filename = data.get('old_filename')
    new_filename = data.get('new_filename')

    if not old_filename or not new_filename:
        return jsonify({"error": "Invalid filename(s) provided"}), 400

    old_iq_path = os.path.join(vars.recordings_dir, f'{old_filename}.iq')
    old_json_path = os.path.join(vars.recordings_dir, f'{old_filename}.json')
    new_iq_path = os.path.join(vars.recordings_dir, f'{new_filename}.iq')
    new_json_path = os.path.join(vars.recordings_dir, f'{new_filename}.json')

    # Rename the IQ file
    if os.path.exists(old_iq_path):
        os.rename(old_iq_path, new_iq_path)
    else:
        return jsonify({"error": "IQ file not found"}), 404

    # Rename the JSON file
    if os.path.exists(old_json_path):
        os.rename(old_json_path, new_json_path)
    else:
        return jsonify({"error": "JSON file not found"}), 404

    return jsonify({"message": "Files renamed successfully", "new_filename": new_filename})


@api_blueprint.route('/api/save_selection', methods=['POST'])
def save_selection():
    data = request.get_json()
    x_start = data.get('xStart')
    x_end = data.get('xEnd')
    y_start = data.get('yStart')
    y_end = data.get('yEnd')
    filename = data.get("filename")

    if not all([x_start, x_end, y_start, y_end]):
        return jsonify({"error": "Invalid coordinates"}), 400

    selected_data = []

    with data_lock:
        # Assuming `waterfall_buffer` contains rows of FFT data and 'y' indexes represent different time slices
        for i in range(int(y_start), int(y_end) + 1):
            if i < len(waterfall_buffer):
                row = waterfall_buffer[i]
                # Select the range of interest in each row
                selected_data.append(row[int(x_start):int(x_end) + 1])

    # Convert selected data to 16-bit signed integer format (assuming the FFT data is float)
    iq_data = []
    for row in selected_data:
        for value in row:
            # Scale and clip the value within the int16 range
            iq_value = int(np.clip(value * 32767, -32768, 32767))
            iq_data.append(iq_value)

    # Define the base file name
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_filename = f'{filename}_{timestamp}'

    # Save the IQ data in 16T binary format
    iq_file_path = os.path.join(vars.recordings_dir, f'{base_filename}.iq')
    
    with open(iq_file_path, 'wb') as iq_file:
        iq_file.write(np.array(iq_data, dtype=np.int16).tobytes())

    # Save the original selected data in JSON format
    json_data_file_path = os.path.join(vars.recordings_dir, f'{base_filename}_data.json')
    with open(json_data_file_path, 'w') as json_data_file:
        json.dump(selected_data, json_data_file, indent=4)

    # Gather SDR settings
    sdr_settings = {
        "frequency": vars.sdr_settings[vars.sdr_name].frequency,
        "bandwidth": vars.sdr_settings[vars.sdr_name].bandwidth,
        "sample_rate": vars.sdr_settings[vars.sdr_name].sampleRate,
        "gain": vars.sdr_settings[vars.sdr_name].gain,
        "sdr": vars.sdr_name,
        "timestamp": timestamp
    }

    # Save SDR settings to a JSON file
    json_file_path = os.path.join(vars.recordings_dir, f'{base_filename}.json')
    with open(json_file_path, 'w') as json_file:
        json.dump(sdr_settings, json_file, indent=4)

    return jsonify({
        "message": "Data saved successfully",
        "iq_file_path": iq_file_path,
        "json_file_path": json_file_path,
        "json_data_file_path": json_data_file_path
    })
