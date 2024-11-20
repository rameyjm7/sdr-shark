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
waterfall_buffer = deque(maxlen=100)  # Buffer for waterfall data
waterfall_buffer2 = deque(maxlen=100)  # Buffer for waterfall data

data_lock = threading.Lock()
fft_data = {
    'original_fft': [],
    'original_fft2': [],
    'max' : [],
    'peaks': [],
    'persist': []
}
running = True

@jit(nopython=True)
def downsample(data, target_length=256):
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
    fft_magnitude = 20 * np.log10(np.abs(fft_result))
    return fft_magnitude

def generate_fft_data():
    full_fft = []
    sdr_name = "sidekiq"
    current_freq = vars.sweep_settings['frequency_start']
    fft_max = None
    fft_persist_data = None  # Initialize persistence trace
    persistence_decay = vars.persistence_decay  # Fetch decay factor (e.g., 0.9)
    
    while running:
        start_time = time.time()
        
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

        if vars.sweeping_enabled:
            # Perform sweeping logic
            if not full_fft:
                full_fft = current_fft
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
                
                # Reset for the next sweep
                full_fft = []
            
            # Update SDR frequency
            vars.sdr_settings[sdr_name].frequency = current_freq
            vars.sdr0.set_frequency(current_freq)
            time.sleep(0.05)
        else:
            # Normal operation without sweeping
            if len(full_fft) == 0:
                full_fft = current_fft
            else:
                full_fft = (full_fft[:vars.sample_size] * (vars.sdr_settings[sdr_name].averagingCount - 1) + current_fft) / vars.sdr_settings[sdr_name].averagingCount
            
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
            with data_lock:
                fft_data['original_fft'] = full_fft.tolist()
                fft_data['max'] = fft_max.tolist()
                fft_data['persist'] = fft_persist_data.tolist()
                waterfall_buffer.append(downsample(current_fft).tolist())

def radio_scanner():
    nfft = 8*1024
    detector = PeakDetector(sdr=vars.sdr0, averaging_count=vars.sdr_averagingCount(), nfft=nfft)
    detector.start_receiving_data()
    sdr_name = "sidekiq"
    
    while running:
        minPeakDistance_index = int(vars.minPeakDistance * vars.sdr_sampleRate()/1e6)
        # Simulate continuous running until stopped
        time.sleep(1)  # Adjust based on how frequently you want to process data

        # Process the peaks
        with data_lock:
            processed_data = detector.get_processed_data()
            if processed_data:
                freq, fft_magnitude, noise_riding_threshold, signals, plot_ranges, freq_bound_left, freq_bound_right = processed_data
                fft_data['original_fft2'] = fft_magnitude.tolist()  # Store the FFT data
                fft_data['peaks'] = signals  # Store the detected peaks
                waterfall_buffer2.append(downsample(np.array(fft_magnitude)).tolist())
    
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

@api_blueprint.route('/api/data')
def get_data():
    with data_lock:
        if vars.sdr_name == "hackrf":
            fft_response = [float(x) for x in fft_data['original_fft2']]
            waterfall_response = [[float(y) for y in x] for x in waterfall_buffer2]
            
        else:
            fft_response = [float(x) for x in fft_data['original_fft']]
            waterfall_response = [[float(y) for y in x] for x in waterfall_buffer]
            
            
        # fft_response = [float(x) for x in fft_data['original_fft']]
        
        # Dynamically add an index or remove if not needed
        peaks_response = [{
            'index': idx,  # Dynamically generate index
            'frequency': float(peak['center_freq']),
            'avg_power': float(peak['avg_power']),
            'peak_power': float(peak['peak_power']),
            'start_freq': float(peak['start_freq']),
            'end_freq': float(peak['end_freq']),
            'bandwidth': float(peak.get('bandwidth', 0.0))  # Handle missing 'bandwidth' key
        } for idx, peak in enumerate(fft_data['peaks'])]


    current_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

    response = {
        'fft': fft_response,
        'peaks': peaks_response,
        'waterfall': waterfall_response,
        'time': current_time,
        'settings': vars.get_settings()
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

        for peak in peaks_data:
            freq = round(float(peak['center_freq'] + vars.sdr_frequency()/1e6), num_digits)
            freq_start = round(float(peak['start_freq'] + vars.sdr_frequency()/1e6), num_digits)
            freq_end = round(float(peak['end_freq'] + vars.sdr_frequency()/1e6), num_digits)
            peak_power = float(peak['peak_power'])
            bandwidth = round(float(peak.get('bandwidth', 0.0)), num_digits)
            avg_power = float(peak.get('avg_power', 0.0))

            # Classify the signal
            classifications = vars.classifier.classify_signal(freq, bandwidth)
            classifications_list = [{"label": c['label'], "channel": c.get('channel', 'N/A')} for c in classifications]

            peaks_response.append({
                'peak': f'Peak {peaks_data.index(peak) + 1}',
                'frequency': freq,
                'freq_start': freq_start,
                'freq_end': freq_end,
                'peak_power': peak_power,
                'avg_power': avg_power,
                'bandwidth': bandwidth,
                'classification': classifications_list,
            })

        payload['peaks'] = peaks_response
    
    return jsonify(payload)


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

@api_blueprint.route('/api/get_settings', methods=['GET'])
def get_settings():
    settings = {
        'sdr': vars.radio_name,
        'frequency': vars.sdr_frequency() / 1e6,  # Convert to MHz
        'gain': vars.sdr_gain(),
        'sampleRate': vars.sdr_sampleRate() / 1e6,  # Convert to MHz
        'bandwidth': vars.sdr_bandwidth() / 1e6,  # Convert to MHz
        'averagingCount': vars.sdr_settings[vars.sdr_name].averagingCount,
        'dcSuppress': vars.dc_suppress,
        'showWaterfall': vars.show_waterfall,
        'updateInterval': vars.sleeptime * 1000,  # Convert to ms
        'waterfallSamples': vars.waterfall_samples,
        'frequency_start': vars.sweep_settings['frequency_start'] / 1e6,
        'frequency_stop': vars.sweep_settings['frequency_stop'] / 1e6,
        'sweeping_enabled': vars.sweeping_enabled,
        'peakThreshold' : vars.peak_threshold_minimum_dB,
        'showFirstTrace': vars.showFirstTrace,
        'showSecondTrace': vars.showSecondTrace,
        'showMaxTrace': vars.showMaxTrace,
        'showPeristanceTrace': vars.showPeristanceTrace,
        'lockBandwidthSampleRate': vars.lockBandwidthSampleRate,
        'signal_stats' : vars.signal_stats
    }
    return jsonify(settings)

@api_blueprint.route('/api/update_settings', methods=['POST'])
def update_settings():
    try:
        settings = request.json
        if settings['frequency'] == 0 or  settings['frequency'] is None or  settings['sampleRate'] is None or settings['bandwidth'] is None:
            return jsonify({'success': True, 'settings': settings})
        new_settings = settings.copy()
        # Update vars with the new settings and save them
        new_settings['frequency'] = settings['frequency'] * 1e6
        new_settings['frequency_stop'] = settings['frequency_stop'] * 1e6
        new_settings['frequency_start'] = settings['frequency_start'] * 1e6
        new_settings['sampleRate'] = settings['sampleRate'] * 1e6
        new_settings['bandwidth'] = settings['bandwidth'] * 1e6
        vars.apply_settings(new_settings)
        vars.save_settings()

        return jsonify({'success': True, 'settings': settings})
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
    vars.sdr0.stop()
    vars.sdr1.stop()
    fft_thread.join()
    scanner_thread.join()


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
