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

from sdr_plot_backend.adsb_plugin import AdsbGatewayPlugin
from sdr_plot_backend.bluetooth_plugin import BluetoothGatewayPlugin
from sdr_plot_backend.fm_plugin import FmBroadcastPlugin
from sdr_plot_backend.gps_plugin import GpsdPlugin
from sdr_plot_backend.iq_session import IQReplaySDR, IQSessionRecorder
from sdr_plot_backend.rf_model_plugin import NoisyDroneModelPlugin
from sdr_plot_backend.rtl433_plugin import Rtl433Plugin
from sdr_plot_backend.signal_utils import perform_and_refine_scan, PeakDetector  # Import the new utility
from sdr_plot_backend.utils import vars
from sdr_plot_backend.wifi_plugin import WiFiGatewayPlugin
from sdr_plot_backend.zigbee_plugin import ZigbeeGatewayPlugin

api_blueprint = Blueprint('api', __name__)

sample_buffer = np.zeros(vars.sample_size, dtype=np.complex64)  # Increase buffer size to decrease RBW
secondary_sample_buffer = np.zeros(vars.sample_size, dtype=np.complex64)
data_buffer = deque(maxlen=vars.sdr_averagingCount())
waterfall_buffer = deque(maxlen=2000)  # Buffer for waterfall data
waterfall_buffer2 = deque(maxlen=2000)  # Buffer for waterfall data

data_lock = threading.Lock()
fft_data = {
    'original_fft': [],
    'secondary_fft': [],
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
scanner_center_hz = 0.0
scanner_sample_rate_hz = 0.0
analysis_peak_memory = {}
analysis_memory_lock = threading.Lock()
settings_update_lock = threading.Lock()
fft_failure_count = 0
scanner_failure_count = 0
scanner_plan_lock = threading.Lock()
scanner_plan = {
    'active': False,
    'steps': [],
    'config': {},
    'index': -1,
    'dwell_until': 0.0,
    'started_at': 0.0,
    'last_step': None,
    'receiver_states': {},
    'error': None,
}
bluetooth_plugin = BluetoothGatewayPlugin()
fm_plugin = FmBroadcastPlugin()
wifi_plugin = WiFiGatewayPlugin()
zigbee_plugin = ZigbeeGatewayPlugin()
adsb_plugin = AdsbGatewayPlugin()
rtl433_plugin = Rtl433Plugin()
gps_plugin = GpsdPlugin()
noisy_drone_plugin = NoisyDroneModelPlugin()
iq_recorder = IQSessionRecorder()
live_sdr = None
replay_sdr = None
live_state = None
replay_lock = threading.Lock()


def _quantize_mhz(value, step_mhz=0.05):
    n = float(value)
    step = max(0.001, float(step_mhz))
    return round(round(n / step) * step, 3)


def _effective_peak_bw_mhz(raw_bw_mhz):
    """Enforce a floor for peak bandwidth based on configured min peak distance."""
    min_bw = max(0.001, float(getattr(vars, "minPeakDistance", 0.1)))
    try:
        bw = float(raw_bw_mhz)
    except Exception:
        bw = min_bw
    if not np.isfinite(bw):
        bw = min_bw
    return max(min_bw, bw)


def _normalize_peak_mhz(value):
    """Normalize potentially mixed Hz/MHz peak values into MHz."""
    try:
        n = float(value)
    except Exception:
        return 0.0
    if not np.isfinite(n):
        return 0.0
    # Some peak sources can leak Hz units. Treat huge values as Hz.
    if abs(n) > 1e6:
        return n / 1e6
    return n

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

def _scanner_status():
    with scanner_plan_lock:
        step = dict(scanner_plan.get('last_step') or {})
        receiver_states = {
            key: {
                **dict(value or {}),
                'last_step': dict((value or {}).get('last_step') or {}),
            }
            for key, value in dict(scanner_plan.get('receiver_states') or {}).items()
        }
        return {
            'active': bool(scanner_plan.get('active')),
            'index': int(scanner_plan.get('index') or 0),
            'count': len(scanner_plan.get('steps') or []),
            'dwellUntil': float(scanner_plan.get('dwell_until') or 0.0),
            'startedAt': float(scanner_plan.get('started_at') or 0.0),
            'step': step,
            'receiverStates': receiver_states,
            'config': dict(scanner_plan.get('config') or {}),
            'error': scanner_plan.get('error'),
            'worker': vars.worker_sdr_info() if hasattr(vars, "worker_sdr_info") else {},
        }

def _active_scan_step():
    with scanner_plan_lock:
        if not scanner_plan.get('active'):
            return {}
        return dict(scanner_plan.get('last_step') or {})

def _active_scan_step_for_receiver(receiver):
    with scanner_plan_lock:
        if not scanner_plan.get('active'):
            return {}
        states = dict(scanner_plan.get('receiver_states') or {})
        return dict((states.get(receiver) or {}).get('last_step') or {})

def _sanitize_scan_step(raw):
    raw = raw or {}
    label = str(raw.get('label') or raw.get('protocol') or 'Scan step').strip()[:80]
    center_mhz = float(raw.get('centerMhz', raw.get('frequencyMhz', raw.get('frequency', 0.0))) or 0.0)
    sample_rate_mhz = float(raw.get('sampleRateMhz', raw.get('bandwidthMhz', raw.get('bandwidth', 20.0))) or 20.0)
    bandwidth_mhz = float(raw.get('bandwidthMhz', sample_rate_mhz) or sample_rate_mhz)
    dwell_sec = float(raw.get('dwellSec', raw.get('dwell', 5.0)) or 5.0)
    if not np.isfinite(center_mhz) or center_mhz <= 0:
        raise ValueError(f"Invalid center frequency for scan step '{label}'")
    if not np.isfinite(sample_rate_mhz) or sample_rate_mhz <= 0:
        sample_rate_mhz = 20.0
    if not np.isfinite(bandwidth_mhz) or bandwidth_mhz <= 0:
        bandwidth_mhz = sample_rate_mhz
    if not np.isfinite(dwell_sec) or dwell_sec <= 0:
        dwell_sec = 5.0
    protocols = raw.get('protocols') or raw.get('protocol') or []
    if isinstance(protocols, str):
        protocols = [protocols]
    receiver = str(raw.get('receiver') or '').strip().lower()
    if not receiver:
        receiver = 'worker' if max(sample_rate_mhz, bandwidth_mhz) <= (float(getattr(vars, "worker_sdr_max_bandwidth", 3e6)) / 1e6) else 'main'
    return {
        'label': label,
        'center_hz': center_mhz * 1e6,
        'sample_rate_hz': sample_rate_mhz * 1e6,
        'bandwidth_hz': bandwidth_mhz * 1e6,
        'dwell_sec': max(0.5, min(3600.0, dwell_sec)),
        'protocols': [str(p).strip().lower() for p in protocols if str(p).strip()],
        'receiver': receiver if receiver in {'main', 'worker'} else 'main',
    }

def _can_worker_handle_step(step):
    max_bw = float(getattr(vars, "worker_sdr_max_bandwidth", 3e6) or 3e6)
    return (
        str(step.get('receiver') or '').lower() == 'worker'
        and float(step.get('sample_rate_hz') or 0.0) <= max_bw
        and float(step.get('bandwidth_hz') or 0.0) <= max_bw
    )

def _apply_worker_scan_step(step):
    worker = vars.ensure_worker_sdr() if hasattr(vars, "ensure_worker_sdr") else None
    if worker is None:
        raise RuntimeError(f"Worker SDR unavailable: {getattr(vars, 'worker_sdr_error', 'unknown error')}")
    max_sr = float(getattr(worker, "max_sample_rate", getattr(vars, "worker_sdr_max_bandwidth", 3e6)) or 3e6)
    max_worker_bw = float(getattr(vars, "worker_sdr_max_bandwidth", 3e6) or 3e6)
    min_freq = float(getattr(worker, "min_frequency", 1e6) or 1e6)
    max_freq = float(getattr(worker, "max_frequency", 1.8e9) or 1.8e9)
    requested_frequency = float(step['center_hz'])
    sample_rate = max(250_000.0, min(float(step['sample_rate_hz']), max_sr, max_worker_bw))
    bandwidth = max(200_000.0, min(float(step['bandwidth_hz']), sample_rate, max_worker_bw))
    frequency = max(min_freq, min(requested_frequency, max_freq))
    if abs(frequency - requested_frequency) > max(1e6, bandwidth / 2):
        raise ValueError(
            f"Worker scan step '{step.get('label')}' requests {requested_frequency / 1e6:.1f} MHz, "
            f"outside worker SDR range {min_freq / 1e6:.1f}-{max_freq / 1e6:.1f} MHz"
        )
    worker.configure_receiver(
        frequency=frequency,
        sample_rate=sample_rate,
        bandwidth=bandwidth,
        gain=float(vars.sdr_settings.get('rtlsdr_worker', vars.sdr_settings[_active_sdr_key()]).gain),
    )
    applied_step = dict(step)
    applied_step.update({
        'receiver': 'worker',
        'applied_center_hz': frequency,
        'applied_sample_rate_hz': sample_rate,
        'applied_bandwidth_hz': bandwidth,
        'worker_device_id': getattr(worker, "device_id", None),
    })
    vars.signal_stats['scanner_mode'] = f"{applied_step.get('label')} (worker)"
    return applied_step

def _apply_scan_step(step):
    protocols = {str(protocol).strip().lower() for protocol in step.get('protocols') or [] if str(protocol).strip()}
    if ({'rtl433', 'subghz'} & protocols) and str(step.get('receiver') or '').lower() == 'worker':
        applied_step = dict(step)
        applied_step.update({
            'receiver': 'worker',
            'applied_center_hz': float(step['center_hz']),
            'applied_sample_rate_hz': float(step.get('sample_rate_hz') or 0.0),
            'applied_bandwidth_hz': float(step.get('bandwidth_hz') or 0.0),
            'worker_device_id': 'rtl_433',
        })
        vars.signal_stats['scanner_mode'] = f"{applied_step.get('label')} (rtl_433)"
        return applied_step
    if not ({'rtl433', 'subghz'} & protocols) and getattr(vars, "worker_sdr_suspended", False):
        rtl433_plugin.stop()
        vars.resume_worker_sdr()
    if _can_worker_handle_step(step):
        return _apply_worker_scan_step(step)
    sdr_key = _active_sdr_key()
    max_sr = float(getattr(vars.sdr0, "max_sample_rate", vars.sdr_sampleRate()) or vars.sdr_sampleRate())
    min_freq = float(getattr(vars.sdr0, "min_frequency", 1e6) or 1e6)
    max_freq = float(getattr(vars.sdr0, "max_frequency", 6e9) or 6e9)
    requested_frequency = float(step['center_hz'])
    sample_rate = max(250_000.0, min(float(step['sample_rate_hz']), max_sr))
    bandwidth = max(200_000.0, min(float(step['bandwidth_hz']), sample_rate, max_sr))
    frequency = max(min_freq, min(requested_frequency, max_freq))
    if abs(frequency - requested_frequency) > max(1e6, bandwidth / 2):
        raise ValueError(
            f"Scan step '{step.get('label')}' requests {requested_frequency / 1e6:.1f} MHz, "
            f"outside active SDR range {min_freq / 1e6:.1f}-{max_freq / 1e6:.1f} MHz"
        )

    vars.sweeping_enabled = False
    vars.sdr_settings[sdr_key].frequency = frequency
    vars.sdr_settings[sdr_key].sampleRate = sample_rate
    vars.sdr_settings[sdr_key].bandwidth = bandwidth
    vars.sdr0.configure_receiver(
        frequency=frequency,
        sample_rate=sample_rate,
        bandwidth=bandwidth,
        gain=vars.sdr_gain(),
    )
    with data_lock:
        fft_data['original_fft'] = []
        waterfall_buffer.clear()
    applied_step = dict(step)
    applied_step.update({
        'receiver': 'main',
        'applied_center_hz': frequency,
        'applied_sample_rate_hz': sample_rate,
        'applied_bandwidth_hz': bandwidth,
    })
    vars.signal_stats['scanner_mode'] = applied_step.get('label')
    return applied_step

def _advance_scanner_plan(force=False):
    now = time.time()
    with scanner_plan_lock:
        if not scanner_plan.get('active'):
            return
        steps = scanner_plan.get('steps') or []
        if not steps:
            scanner_plan['active'] = False
            return
        states = dict(scanner_plan.get('receiver_states') or {})
        receivers = sorted({str(step.get('receiver') or 'main').lower() for step in steps})
        pending = []
        for receiver in receivers:
            receiver_steps = [
                (index, dict(step))
                for index, step in enumerate(steps)
                if str(step.get('receiver') or 'main').lower() == receiver
            ]
            if not receiver_steps:
                continue
            state = dict(states.get(receiver) or {'index': -1, 'dwell_until': 0.0, 'last_step': None})
            if not force and state.get('last_step') is not None and now < float(state.get('dwell_until') or 0.0):
                continue
            if not force and len(receiver_steps) == 1 and state.get('last_step') is not None:
                state['dwell_until'] = now + float(receiver_steps[0][1].get('dwell_sec') or 5.0)
                states[receiver] = state
                continue
            current_index = int(state.get('index') if state.get('index') is not None else -1)
            positions = [idx for idx, _step in receiver_steps]
            if current_index in positions:
                current_position = positions.index(current_index)
                next_pair = receiver_steps[(current_position + 1) % len(receiver_steps)]
            else:
                next_pair = receiver_steps[0]
            pending.append((receiver, next_pair[0], next_pair[1]))
        if not pending:
            scanner_plan['receiver_states'] = states
            return

    if not settings_update_lock.acquire(timeout=0.05):
        return
    try:
        applied_steps = []
        errors = []
        for receiver, next_index, step in pending:
            try:
                applied_step = _apply_scan_step(step)
                applied_steps.append((receiver, next_index, step, applied_step))
            except Exception as exc:
                errors.append(f"{receiver}: {exc}")
        with scanner_plan_lock:
            states = dict(scanner_plan.get('receiver_states') or {})
            for receiver, next_index, step, applied_step in applied_steps:
                dwell_until = time.time() + float(step.get('dwell_sec') or 5.0)
                states[receiver] = {
                    'index': next_index,
                    'dwell_until': dwell_until,
                    'last_step': applied_step,
                }
                scanner_plan['index'] = next_index
                scanner_plan['last_step'] = applied_step
                scanner_plan['dwell_until'] = dwell_until
            scanner_plan['receiver_states'] = states
            scanner_plan['error'] = '; '.join(errors) if errors else None
    except Exception as exc:
        with scanner_plan_lock:
            scanner_plan['error'] = str(exc)
    finally:
        settings_update_lock.release()

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


def _stop_protocol_plugins():
    bluetooth_plugin.stop()
    wifi_plugin.stop()
    zigbee_plugin.stop()
    adsb_plugin.stop()
    rtl433_plugin.stop()
    fm_plugin.stop()
    noisy_drone_plugin.stop()

def _active_decoder_protocols() -> set[str]:
    all_protocols = {'btc', 'btle', 'bluetooth', 'wifi', 'zigbee', 'thread', 'adsb', 'fm', 'rtl433', 'subghz'}
    if bool(getattr(vars, 'decoders_always_enabled', False)):
        return all_protocols
    with scanner_plan_lock:
        scanner_active = bool(scanner_plan.get('active'))
        states = dict(scanner_plan.get('receiver_states') or {})
        step = dict(scanner_plan.get('last_step') or {})
    if not scanner_active:
        return set()
    protocol_rows = []
    if states:
        protocol_rows = [
            dict((state or {}).get('last_step') or {})
            for state in states.values()
            if (state or {}).get('last_step')
        ]
    if not protocol_rows:
        protocol_rows = [step]
    protocols = {
        str(protocol).strip().lower()
        for row in protocol_rows
        for protocol in row.get('protocols') or []
        if str(protocol).strip()
    }
    if 'thread' in protocols:
        protocols.add('zigbee')
    return protocols

def _receiver_protocols(receiver) -> set[str]:
    all_protocols = {'btc', 'btle', 'bluetooth', 'wifi', 'zigbee', 'thread', 'adsb', 'fm', 'rtl433', 'subghz'}
    if bool(getattr(vars, 'decoders_always_enabled', False)) and receiver == 'main':
        return all_protocols
    step = _active_scan_step_for_receiver(receiver)
    protocols = {str(protocol).strip().lower() for protocol in step.get('protocols') or [] if str(protocol).strip()}
    if 'thread' in protocols:
        protocols.add('zigbee')
    return protocols

def _decoder_enabled(protocols: set[str], *names: str) -> bool:
    return any(str(name).lower() in protocols for name in names)

def _decoder_sdr_for_receiver(receiver):
    if receiver == 'worker':
        worker = vars.ensure_worker_sdr() if hasattr(vars, "ensure_worker_sdr") else None
        return worker
    return vars.sdr0

def _decoder_context_for(*names):
    for receiver in ('main', 'worker'):
        protocols = _receiver_protocols(receiver)
        if _decoder_enabled(protocols, *names):
            sdr = _decoder_sdr_for_receiver(receiver)
            if sdr is not None:
                return protocols, sdr, _active_scan_step_for_receiver(receiver), receiver
    return set(), None, {}, ''

def _decoder_step_for(*names):
    for receiver in ('main', 'worker'):
        protocols = _receiver_protocols(receiver)
        if _decoder_enabled(protocols, *names):
            return protocols, _active_scan_step_for_receiver(receiver), receiver
    return set(), {}, ''


def downsample(data, target_length=256):
    data = np.asarray(data, dtype=np.float32)
    n = data.size
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    if target_length <= 0:
        target_length = 1
    if target_length >= n:
        return data.copy()

    edges = np.linspace(0, n, target_length + 1, dtype=np.int64)
    downsampled = np.empty(target_length, dtype=np.float32)
    for i in range(target_length):
        start = int(edges[i])
        end = int(edges[i + 1])
        if end <= start:
            end = min(start + 1, n)
        chunk = data[start:end]
        downsampled[i] = float(np.mean(chunk)) if chunk.size else float(data[min(start, n - 1)])
    return downsampled

def capture_samples():
    global sample_buffer, secondary_sample_buffer
    while vars.sdr0 is None:
        time.sleep(0.1)
    sample_buffer = vars.sdr0.get_latest_samples()
    secondary_samples = None
    if hasattr(vars.sdr0, "get_latest_samples_secondary"):
        secondary_samples = vars.sdr0.get_latest_samples_secondary()
    if secondary_samples is None:
        secondary_sample_buffer = np.zeros(vars.sample_size, dtype=np.complex64)
    else:
        secondary_sample_buffer = secondary_samples

def process_fft(samples):
    fft_result = np.fft.fftshift(np.fft.fft(samples))
    # Add epsilon to avoid log10(0) warnings on silent bins.
    fft_magnitude = 20 * np.log10(np.abs(fft_result) + 1e-12)
    return fft_magnitude

def generate_fft_data():
    global reset_max_trace, reset_persist_trace, main_fft_updated_at, main_frame_seq, fft_failure_count
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

            _advance_scanner_plan()

            # Capture and process FFT samples
            capture_samples()
            noisy_drone_plugin.configure(
                enabled=bool(getattr(vars, 'rf_model_classifier_enabled', False)),
                repo_path=getattr(vars, 'rf_model_classifier_repo_path', None),
                model_path=getattr(vars, 'rf_model_classifier_model_path', None),
                target_freq_hz=float(getattr(vars, 'rf_model_classifier_target_mhz', 2399.0) or 0.0) * 1e6,
                sample_rate_hz=float(getattr(vars, 'rf_model_classifier_bandwidth_mhz', 20.0) or 20.0) * 1e6,
                interval_sec=float(getattr(vars, 'rf_model_classifier_interval_sec', 1.0) or 1.0),
                confidence_threshold=float(getattr(vars, 'rf_model_classifier_threshold', 0.45) or 0.45),
            )
            noisy_drone_plugin.submit_iq(
                sample_buffer,
                center_freq_hz=float(vars.sdr_frequency()),
                sample_rate_hz=float(vars.sdr_sampleRate()),
            )
            bluetooth_protocols, bluetooth_sdr, _bluetooth_step, _bluetooth_receiver = _decoder_context_for('btc', 'btle', 'bluetooth')
            if bluetooth_sdr is not None and _decoder_enabled(bluetooth_protocols, 'btc', 'btle', 'bluetooth'):
                bluetooth_plugin.update(bluetooth_sdr)
            else:
                bluetooth_plugin.stop()
            wifi_protocols, wifi_sdr, _wifi_step, _wifi_receiver = _decoder_context_for('wifi')
            if wifi_sdr is not None and _decoder_enabled(wifi_protocols, 'wifi'):
                wifi_plugin.update(wifi_sdr)
            else:
                wifi_plugin.stop()
            zigbee_protocols, zigbee_sdr, _zigbee_step, _zigbee_receiver = _decoder_context_for('zigbee', 'thread')
            if zigbee_sdr is not None and _decoder_enabled(zigbee_protocols, 'zigbee', 'thread'):
                zigbee_plugin.update(zigbee_sdr)
            else:
                zigbee_plugin.stop()
            adsb_protocols, adsb_sdr, _adsb_step, _adsb_receiver = _decoder_context_for('adsb')
            if adsb_sdr is not None and _decoder_enabled(adsb_protocols, 'adsb'):
                adsb_plugin.update(adsb_sdr)
            else:
                adsb_plugin.stop()
            rtl433_protocols, rtl433_step, _rtl433_receiver = _decoder_step_for('rtl433', 'subghz')
            if _decoder_enabled(rtl433_protocols, 'rtl433', 'subghz') and rtl433_step:
                rtl433_plugin.update(rtl433_step, vars)
            else:
                rtl433_plugin.stop()
                if getattr(vars, "worker_sdr_suspended", False):
                    vars.resume_worker_sdr()
            current_fft = process_fft(sample_buffer)
            secondary_fft = []
            mimo_info = vars.sdr0.mimo_info() if hasattr(vars.sdr0, "mimo_info") else {}
            if bool(mimo_info.get("enabled")) and secondary_sample_buffer.size:
                secondary_fft = process_fft(secondary_sample_buffer)
            fm_protocols, fm_sdr, _fm_step, _fm_receiver = _decoder_context_for('fm')
            if fm_sdr is not None and _decoder_enabled(fm_protocols, 'fm'):
                fm_samples = sample_buffer
                fm_fft = current_fft
                fm_center_hz = vars.sdr_frequency()
                fm_sample_rate_hz = vars.sdr_sampleRate()
                if fm_sdr is not vars.sdr0:
                    try:
                        fm_samples = fm_sdr.get_latest_samples()
                        fm_fft = process_fft(fm_samples)
                        fm_center_hz = float(getattr(fm_sdr, "frequency", fm_center_hz) or fm_center_hz)
                        fm_sample_rate_hz = float(getattr(fm_sdr, "sample_rate", fm_sample_rate_hz) or fm_sample_rate_hz)
                    except Exception:
                        fm_samples = sample_buffer
                        fm_fft = current_fft
                fm_plugin.update_from_iq_and_fft(
                    fm_samples,
                    fm_fft,
                    center_freq_hz=fm_center_hz,
                    sample_rate_hz=fm_sample_rate_hz,
                )
            else:
                fm_plugin.stop()

            # Suppress DC spike if enabled
            if vars.dc_suppress:
                dc_index = len(current_fft) // 2
                current_fft[dc_index] = current_fft[dc_index + 1]
                if isinstance(secondary_fft, np.ndarray) and len(secondary_fft) > dc_index + 1:
                    secondary_fft[dc_index] = secondary_fft[dc_index + 1]

            # Normalize invalid (inf) values
            current_fft = np.where(np.isinf(current_fft), -20, current_fft)
            if isinstance(secondary_fft, np.ndarray):
                secondary_fft = np.where(np.isinf(secondary_fft), -20, secondary_fft)

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

                # Tune to the next frequency using current sample rate as sweep step.
                step_hz = max(float(vars.sdr_sampleRate()), 1e6)
                current_freq += step_hz
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
                    fft_data['secondary_fft'] = secondary_fft.tolist() if isinstance(secondary_fft, np.ndarray) else []
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
                    fft_data['secondary_fft'] = secondary_fft.tolist() if isinstance(secondary_fft, np.ndarray) else []
                    fft_data['max'] = fft_max.tolist()
                    fft_data['persist'] = fft_persist_data.tolist()
                    waterfall_buffer.append(
                        downsample(current_fft, bin_count).tolist()
                    )
                    main_fft_updated_at = time.time()
                    main_frame_seq += 1
            # Clear stale FFT error once a frame processes successfully.
            fft_failure_count = 0
            vars.signal_stats.pop("fft_error", None)
            vars.signal_stats.pop("fft_error_ts", None)
        except Exception as e:
            fft_failure_count += 1
            if fft_failure_count >= 3:
                vars.signal_stats["fft_error"] = str(e)
                vars.signal_stats["fft_error_ts"] = time.time()
            time.sleep(0.05)

def radio_scanner():
    global scanner_fft_updated_at, scanner_frame_seq, scanner_failure_count, scanner_center_hz, scanner_sample_rate_hz
    nfft = 8*1024
    detector = None
    detector_sdr = None
    
    while running:
        # Simulate continuous running until stopped
        time.sleep(1)

        # Get processed scanner data outside shared lock to avoid starving FFT producer updates.
        try:
            scanner_sdr = vars.ensure_worker_sdr() if hasattr(vars, "ensure_worker_sdr") else None
            if scanner_sdr is None:
                scanner_sdr = vars.sdr0
            if scanner_sdr is None:
                raise RuntimeError("SDR not ready")

            if detector is None or detector_sdr is not scanner_sdr:
                if detector is not None:
                    detector.stop_receiving_data()
                detector = PeakDetector(sdr=scanner_sdr, averaging_count=vars.sdr_averagingCount(), nfft=nfft)
                detector.start_receiving_data()
                detector_sdr = scanner_sdr

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
                    scanner_center_hz = float(getattr(scanner_sdr, "frequency", 0.0) or 0.0)
                    scanner_sample_rate_hz = float(getattr(scanner_sdr, "sample_rate", 0.0) or 0.0)
                    scanner_fft_updated_at = time.time()
                    scanner_frame_seq += 1
            # Clear stale scanner error once scanner loop succeeds.
            scanner_failure_count = 0
            vars.signal_stats.pop("scanner_error", None)
            vars.signal_stats.pop("scanner_error_ts", None)
        except Exception as e:
            scanner_failure_count += 1
            if detector is not None:
                detector.stop_receiving_data()
                detector = None
                detector_sdr = None
            if scanner_failure_count >= 3:
                vars.signal_stats["scanner_error"] = str(e)
                vars.signal_stats["scanner_error_ts"] = time.time()
            time.sleep(0.1)
                    
    
    if detector is not None:
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

def _build_data_payload(source='main', waterfall_mode='history', include_secondary=False):
    source = str(source or 'main').lower()
    waterfall_mode = str(waterfall_mode or 'history').lower()
    include_secondary = bool(include_secondary)
    with data_lock:
        max_waterfall_rows = _safe_int(vars.waterfall_samples, default=100, min_value=1, max_value=2000)
        # Keep live responses lightweight for lower-latency UI updates.
        max_payload_cells = 150000
        main_fft_snapshot = list(fft_data['original_fft'])
        secondary_fft_snapshot = list(fft_data.get('secondary_fft') or []) if include_secondary else []
        scanner_fft_snapshot = list(fft_data['original_fft2'])
        waterfall_rows_requested = 0 if waterfall_mode in ('none', 'derive') else (1 if waterfall_mode == 'latest' else max_waterfall_rows)
        main_waterfall_snapshot = _tail_deque_rows(waterfall_buffer, waterfall_rows_requested)
        scanner_waterfall_snapshot = _tail_deque_rows(waterfall_buffer2, waterfall_rows_requested)
        main_ts_snapshot = main_fft_updated_at
        scanner_ts_snapshot = scanner_fft_updated_at
        main_seq_snapshot = main_frame_seq
        scanner_seq_snapshot = scanner_frame_seq
        scanner_center_snapshot = scanner_center_hz
        scanner_sample_rate_snapshot = scanner_sample_rate_hz
        
        peaks_snapshot = list(fft_data['peaks'])

    scanner_fresh = (time.time() - scanner_ts_snapshot) <= 3.0
    scanner_available = scanner_fresh and len(scanner_fft_snapshot) > 0
    main_available = len(main_fft_snapshot) > 0
    secondary_source = 'mimo' if secondary_fft_snapshot else ''
    secondary_center_snapshot = float(vars.sdr_frequency())
    secondary_sample_rate_snapshot = float(vars.sdr_sampleRate())
    if include_secondary and not secondary_fft_snapshot and scanner_available:
        secondary_fft_snapshot = scanner_fft_snapshot
        secondary_source = 'worker'
        secondary_center_snapshot = float(scanner_center_snapshot or 0.0)
        secondary_sample_rate_snapshot = float(scanner_sample_rate_snapshot or 0.0)

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
    secondary_fft_response = [float(x) for x in secondary_fft_snapshot] if secondary_fft_snapshot else []

    if waterfall_snapshot:
        cols = len(waterfall_snapshot[0]) if waterfall_snapshot[0] is not None else 0
        stride = max(1, int(np.ceil((len(waterfall_snapshot) * max(1, cols)) / max_payload_cells)))
        waterfall_rows = waterfall_snapshot[::stride]
    else:
        waterfall_rows = []
    waterfall_response = [[float(y) for y in x] for x in waterfall_rows]
        
    if scanner_available and scanner_center_snapshot > 0:
        center_freq_mhz = float(scanner_center_snapshot / 1e6)
    else:
        center_freq_mhz = float(vars.sdr_frequency() / 1e6)
    peaks_response = []
    for idx, peak in enumerate(peaks_snapshot):
        rel_center = _normalize_peak_mhz(peak.get('center_freq', 0.0))
        rel_start = _normalize_peak_mhz(peak.get('start_freq', 0.0))
        rel_end = _normalize_peak_mhz(peak.get('end_freq', 0.0))
        raw_bw = _normalize_peak_mhz(peak.get('bandwidth', 0.0))
        bw_mhz = _effective_peak_bw_mhz(raw_bw)
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

    fft_error = vars.signal_stats.get("fft_error")
    scanner_error = vars.signal_stats.get("scanner_error")
    now_ts = time.time()
    fft_err_ts = float(vars.signal_stats.get("fft_error_ts", 0.0) or 0.0)
    scanner_err_ts = float(vars.signal_stats.get("scanner_error_ts", 0.0) or 0.0)
    if not fft_err_ts or (now_ts - fft_err_ts) > 3.0:
        fft_error = None
    if not scanner_err_ts or (now_ts - scanner_err_ts) > 3.0:
        scanner_error = None

    response = {
        'fft': fft_response,
        'secondaryFft': secondary_fft_response,
        'secondaryMeta': {
            'source': secondary_source,
            'centerHz': secondary_center_snapshot,
            'sampleRateHz': secondary_sample_rate_snapshot,
        },
        'peaks': peaks_response,
        'waterfall': waterfall_response,
        'waterfallMode': waterfall_mode,
        'waterfallRows': len(waterfall_response),
        'time': current_time,
        'settings': vars.get_settings(),
        'mainFrameSeq': int(main_seq_snapshot),
        'scannerFrameSeq': int(scanner_seq_snapshot),
        'scannerFresh': bool(scanner_available),
        'scannerCenterHz': float(scanner_center_snapshot or 0.0),
        'scannerSampleRateHz': float(scanner_sample_rate_snapshot or 0.0),
        'fftError': fft_error,
        'scannerError': scanner_error,
        'decodersAlwaysEnabled': bool(getattr(vars, 'decoders_always_enabled', False)),
        'mimo': vars.sdr0.mimo_info() if hasattr(vars.sdr0, "mimo_info") else {'enabled': False, 'channels': [0]},
        'workerSdr': vars.worker_sdr_info() if hasattr(vars, "worker_sdr_info") else {},
        'bluetooth': bluetooth_plugin.snapshot(max_events=20),
        'fm': fm_plugin.snapshot(max_events=20),
        'wifi': wifi_plugin.snapshot(max_events=20),
        'zigbee': zigbee_plugin.snapshot(max_events=20),
        'adsb': adsb_plugin.snapshot(max_events=20),
        'rtl433': rtl433_plugin.snapshot(max_events=20),
        'rfModel': noisy_drone_plugin.snapshot(max_events=20),
        'gps': gps_plugin.snapshot(),
        'scannerMode': _scanner_status(),
        'iqSession': {
            'recording': iq_recorder.status(),
            'replay': replay_sdr.iq_tap_info() if replay_sdr is not None else None,
        },
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

    return response


def _stream_sequence_for_source(source):
    source = str(source or 'main').lower()
    with data_lock:
        if source == 'scanner':
            return int(scanner_frame_seq)
        if source == 'auto':
            return max(int(main_frame_seq), int(scanner_frame_seq))
        return int(main_frame_seq)


@api_blueprint.route('/api/data')
def get_data():
    include_secondary = str(request.args.get('secondary', '0')).lower() in {'1', 'true', 'yes', 'on'}
    return jsonify(_build_data_payload(
        source=request.args.get('source', 'main'),
        waterfall_mode=request.args.get('waterfall', 'history'),
        include_secondary=include_secondary,
    ))


@api_blueprint.route('/api/data_stream')
def stream_data():
    source = request.args.get('source', 'main')
    waterfall_mode = request.args.get('waterfall', 'latest')
    min_interval_ms = _safe_int(request.args.get('interval', 50), default=50, min_value=20, max_value=2000)
    include_secondary = str(request.args.get('secondary', '0')).lower() in {'1', 'true', 'yes', 'on'}

    def event_stream():
        last_seq = -1
        heartbeat_at = time.time()
        while running:
            seq = _stream_sequence_for_source(source)
            now = time.time()
            if seq != last_seq:
                payload = _build_data_payload(
                    source=source,
                    waterfall_mode=waterfall_mode,
                    include_secondary=include_secondary,
                )
                last_seq = seq
                heartbeat_at = now
                yield f"id: {seq}\nevent: frame\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
            elif now - heartbeat_at >= 10:
                heartbeat_at = now
                yield ": keepalive\n\n"
            time.sleep(min_interval_ms / 1000.0)

    headers = {
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive',
    }
    return Response(event_stream(), mimetype='text/event-stream', headers=headers)


@api_blueprint.route('/api/bluetooth/events')
def bluetooth_events():
    max_events = _safe_int(request.args.get('limit', 50), default=50, min_value=1, max_value=200)
    return jsonify(_to_builtin(bluetooth_plugin.snapshot(max_events=max_events)))


@api_blueprint.route('/api/fm/play', methods=['POST'])
def fm_play():
    payload = request.get_json(silent=True) or {}
    try:
        frequency_mhz = float(payload.get('frequency_mhz'))
    except Exception:
        return jsonify({'ok': False, 'error': 'Invalid FM frequency'}), 400

    try:
        fm_plugin.stop_playback()
        station = fm_plugin.start_playback(vars.sdr0, frequency_mhz)
    except Exception as exc:
        fm_plugin.stop_playback()
        return jsonify({'ok': False, 'error': str(exc)}), 400
    return jsonify({
        'ok': True,
        'station': _to_builtin(station),
        'mode': 'wideband',
    })


@api_blueprint.route('/api/fm/stop', methods=['POST'])
def fm_stop():
    fm_plugin.stop_playback()
    return jsonify({'ok': True})


@api_blueprint.route('/api/fm/audio/batch')
def fm_audio_batch():
    count = _safe_int(request.args.get('count', 6), default=6, min_value=1, max_value=16)
    try:
        timeout = max(0.05, min(float(request.args.get('timeout', 0.4)), 2.0))
    except Exception:
        timeout = 0.4
    pcm = fm_plugin.audio_batch(count=count, timeout=timeout)
    if not pcm:
        return Response(b'', mimetype='application/octet-stream', status=204)
    return Response(pcm, mimetype='application/octet-stream')


@api_blueprint.route('/api/iq/record/start', methods=['POST'])
def iq_record_start():
    payload = request.get_json(silent=True) or {}
    label = str(payload.get('label') or '').strip()
    max_seconds = max(0.0, float(payload.get('max_seconds') or 0.0))
    max_mb = max(0.0, float(payload.get('max_mb') or 0.0))
    try:
        status = iq_recorder.start(vars.sdr0, label=label, max_seconds=max_seconds, max_mb=max_mb)
    except Exception as exc:
        return jsonify({'ok': False, 'error': str(exc)}), 400
    return jsonify({'ok': True, 'recording': _to_builtin(status)})


@api_blueprint.route('/api/iq/record/stop', methods=['POST'])
def iq_record_stop():
    return jsonify({'ok': True, 'recording': _to_builtin(iq_recorder.stop())})


@api_blueprint.route('/api/iq/record/status')
def iq_record_status():
    return jsonify({'ok': True, 'recording': _to_builtin(iq_recorder.status())})


@api_blueprint.route('/api/iq/sessions')
def iq_sessions():
    return jsonify({'ok': True, 'sessions': _to_builtin(iq_recorder.list_sessions())})


@api_blueprint.route('/api/iq/replay/start', methods=['POST'])
def iq_replay_start():
    global live_sdr, replay_sdr, live_state
    payload = request.get_json(silent=True) or {}
    session_id = str(payload.get('id') or payload.get('session_id') or '').strip()
    loop = bool(payload.get('loop', True))
    speed = max(0.01, min(float(payload.get('speed') or 1.0), 20.0))
    if not session_id:
        return jsonify({'ok': False, 'error': 'Missing session id'}), 400

    sessions = {str(session.get('id') or ''): session for session in iq_recorder.list_sessions()}
    session = sessions.get(session_id)
    if not session:
        return jsonify({'ok': False, 'error': f'Unknown IQ session {session_id}'}), 404

    with replay_lock:
        try:
            iq_recorder.stop()
            _stop_protocol_plugins()
            if replay_sdr is not None:
                replay_sdr.stop()
                replay_sdr = None
            if live_sdr is None and getattr(vars.sdr0, 'backend', '') != 'replay':
                live_sdr = vars.sdr0
                live_state = {
                    'sdr_name': vars.sdr_name,
                    'radio_name': vars.radio_name,
                }
            replay_sdr = IQReplaySDR(
                session_dir=os.path.abspath(str(session.get('path'))),
                loop=loop,
                speed=speed,
                size=vars.sample_size,
            )
            vars._ensure_radio_settings('replay')
            vars.sdr_name = 'replay'
            vars.sdr_settings['replay'].frequency = replay_sdr.frequency
            vars.sdr_settings['replay'].sampleRate = replay_sdr.sample_rate
            vars.sdr_settings['replay'].bandwidth = replay_sdr.bandwidth
            vars.sdr_settings['replay'].gain = replay_sdr.gain
            vars.sdr0 = replay_sdr
            vars.radio_name = replay_sdr.device_id
            replay_sdr.start()
        except Exception as exc:
            return jsonify({'ok': False, 'error': str(exc)}), 400
    return jsonify({'ok': True, 'replay': _to_builtin(replay_sdr.iq_tap_info())})


@api_blueprint.route('/api/iq/replay/stop', methods=['POST'])
def iq_replay_stop():
    global live_sdr, replay_sdr, live_state
    with replay_lock:
        _stop_protocol_plugins()
        if replay_sdr is not None:
            replay_sdr.stop()
            replay_sdr = None
        if live_sdr is not None:
            vars.sdr0 = live_sdr
            if live_state is not None:
                vars.sdr_name = live_state.get('sdr_name') or vars.sdr_name
                vars.radio_name = live_state.get('radio_name') or vars.radio_name
            else:
                vars.radio_name = live_sdr.device_id or live_sdr.name or vars.sdr_name
            live_sdr = None
            live_state = None
    return jsonify({'ok': True})


@api_blueprint.route('/api/iq/replay/status')
def iq_replay_status():
    with replay_lock:
        replay = replay_sdr.iq_tap_info() if replay_sdr is not None else None
    return jsonify({'ok': True, 'active': replay is not None, 'replay': _to_builtin(replay)})


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
            bandwidth = round(_effective_peak_bw_mhz(peak.get('bandwidth', 0.0)), num_digits)
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
    supported_drivers = {'hackrf', 'sidekiq', 'airspy', 'bladerf', 'rtlsdr', 'mock', 'antsdre200', 'replay'}
    driver = str(sdr_name).split(':', 1)[0]
    if driver not in supported_drivers:
        return jsonify({
            'status': 'error',
            'result': 0,
            'message': f"SDR '{sdr_name}' is discovered but not stream-capable in SDR Shark yet."
        }), 400
    if driver == 'replay':
        return jsonify({'status': 'success', 'result': int(getattr(vars.sdr0, "backend", "") == "replay")})
    result = vars.reselect_radio(sdr_name)
    if result:
        return jsonify({'status': 'success', 'result': result})
    return jsonify({'status': 'error', 'result': result, 'message': f'Failed to switch SDR to {sdr_name}'}), 400


@api_blueprint.route('/api/sdr_devices', methods=['GET'])
def get_sdr_devices():
    supported_drivers = {'hackrf', 'sidekiq', 'airspy', 'bladerf', 'rtlsdr', 'mock', 'antsdre200', 'replay'}

    def _filter_supported(devices):
        return [d for d in devices if str(d.get('driver', '')).lower() in supported_drivers]

    try:
        all_devices = vars.sdr0.list_devices()
        devices = _filter_supported(all_devices)
        selected = vars.sdr0.device_id
        if selected and str(selected).split(':', 1)[0] not in supported_drivers:
            selected = devices[0]['id'] if devices else None
        return jsonify(_to_builtin({'devices': devices, 'selected': selected}))
    except Exception as e:
        cached = _filter_supported(getattr(vars.sdr0, "_devices_cache", []) or [])
        selected = vars.sdr0.device_id if getattr(vars, "sdr0", None) else None
        if selected and str(selected).split(':', 1)[0] not in supported_drivers:
            selected = cached[0]['id'] if cached else None
        return jsonify({'devices': _to_builtin(cached), 'selected': selected, 'error': str(e)}), 200

@api_blueprint.route('/api/get_settings', methods=['GET'])
def get_settings():
    settings = {
        'sdr': vars.sdr0.device_id or vars.radio_name,
        'sdrBackend': getattr(vars.sdr0, 'backend', 'gateway'),
        'frequency': vars.sdr_frequency() / 1e6,  # Convert to MHz
        'gain': vars.sdr_gain(),
        'sampleRate': vars.sdr_sampleRate() / 1e6,  # Convert to MHz
        'bandwidth': vars.sdr_bandwidth() / 1e6,  # Convert to MHz
        'averagingCount': vars.sdr_settings[vars.sdr_name].averagingCount,
        'dcSuppress': vars.dc_suppress,
        'showWaterfall': vars.show_waterfall,
        'decodersAlwaysEnabled': bool(getattr(vars, 'decoders_always_enabled', False)),
        'rfModelClassifierEnabled': bool(getattr(vars, 'rf_model_classifier_enabled', False)),
        'rfModelClassifierRepoPath': getattr(vars, 'rf_model_classifier_repo_path', ''),
        'rfModelClassifierModelPath': getattr(vars, 'rf_model_classifier_model_path', ''),
        'rfModelClassifierTargetMHz': float(getattr(vars, 'rf_model_classifier_target_mhz', 2399.0) or 0.0),
        'rfModelClassifierBandwidthMHz': float(getattr(vars, 'rf_model_classifier_bandwidth_mhz', 20.0) or 20.0),
        'rfModelClassifierIntervalSec': float(getattr(vars, 'rf_model_classifier_interval_sec', 1.0) or 1.0),
        'rfModelClassifierThreshold': float(getattr(vars, 'rf_model_classifier_threshold', 0.45) or 0.45),
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
        'scannerMode': _scanner_status(),
        'signal_stats' : vars.signal_stats
    }
    return jsonify(_to_builtin(settings))

@api_blueprint.route('/api/update_settings', methods=['POST'])
def update_settings():
    if not settings_update_lock.acquire(timeout=0.1):
        return jsonify({
            'success': True,
            'busy': True,
            'message': 'Settings update already in progress; duplicate request ignored'
        }), 202
    try:
        settings = request.json
        if settings['frequency'] == 0 or  settings['frequency'] is None or  settings['sampleRate'] is None or settings['bandwidth'] is None:
            return jsonify(_to_builtin({'success': True, 'settings': settings}))
        requested_sdr = str(settings.get('sdr') or '').strip()
        if requested_sdr and requested_sdr != (vars.sdr0.device_id or vars.radio_name):
            supported_drivers = {'hackrf', 'sidekiq', 'airspy', 'bladerf', 'rtlsdr', 'mock', 'antsdre200', 'replay'}
            driver = requested_sdr.split(':', 1)[0]
            if driver not in supported_drivers:
                return jsonify({
                    'success': False,
                    'error': f"SDR '{requested_sdr}' is not stream-capable in SDR Shark."
                }), 400
            if not vars.reselect_radio(requested_sdr):
                return jsonify({
                    'success': False,
                    'error': f"Failed to switch SDR to {requested_sdr}"
                }), 400
        new_settings = settings.copy()
        # Update vars with the new settings and save them
        new_settings['frequency'] = settings['frequency'] * 1e6
        new_settings['frequency_stop'] = settings['frequency_stop'] * 1e6
        new_settings['frequency_start'] = settings['frequency_start'] * 1e6
        new_settings['sampleRate'] = settings['sampleRate'] * 1e6
        new_settings['bandwidth'] = settings['bandwidth'] * 1e6
        current_tuning = (
            float(vars.sdr_frequency()),
            float(vars.sdr_sampleRate()),
            float(vars.sdr_bandwidth()),
        )
        requested_tuning = (
            float(new_settings['frequency']),
            float(new_settings['sampleRate']),
            float(new_settings['bandwidth']),
        )
        tuning_changed = any(abs(left - right) > 1.0 for left, right in zip(current_tuning, requested_tuning))
        if tuning_changed:
            with scanner_plan_lock:
                scanner_plan['active'] = False
                scanner_plan['dwell_until'] = 0.0
                scanner_plan['receiver_states'] = {}
            vars.signal_stats.pop('scanner_mode', None)
        vars.apply_settings(new_settings)
        vars.save_settings()

        return jsonify(_to_builtin({'success': True, 'settings': settings}))
    except Exception as e:
        print(e)
        return jsonify({'success': False, 'error': str(e)})
    finally:
        settings_update_lock.release()

@api_blueprint.route('/api/start_sweep', methods=['POST'])
def start_sweep():
    vars.sweeping_enabled = True
    with scanner_plan_lock:
        scanner_plan['active'] = False
        scanner_plan['receiver_states'] = {}
    return jsonify({'status': 'success', 'sweeping_enabled': vars.sweeping_enabled})

@api_blueprint.route('/api/stop_sweep', methods=['POST'])
def stop_sweep():
    vars.sweeping_enabled = False
    return jsonify({'status': 'success', 'sweeping_enabled': vars.sweeping_enabled})

@api_blueprint.route('/api/scanner/start', methods=['POST'])
def start_scanner_plan():
    payload = request.get_json(silent=True) or {}
    raw_steps = payload.get('steps') or []
    if not isinstance(raw_steps, list) or not raw_steps:
        return jsonify({'success': False, 'error': 'No scan steps selected'}), 400
    try:
        default_dwell = float(payload.get('dwellSec', 5.0) or 5.0)
        steps = []
        for raw_step in raw_steps:
            merged = dict(raw_step or {})
            merged.setdefault('dwellSec', default_dwell)
            steps.append(_sanitize_scan_step(merged))
        with scanner_plan_lock:
            receiver_states = {
                receiver: {'index': -1, 'dwell_until': 0.0, 'last_step': None}
                for receiver in {str(step.get('receiver') or 'main').lower() for step in steps}
            }
            scanner_plan.update({
                'active': True,
                'steps': steps,
                'config': dict(payload.get('config') or {}),
                'index': -1,
                'dwell_until': 0.0,
                'started_at': time.time(),
                'last_step': None,
                'receiver_states': receiver_states,
                'error': None,
            })
        _advance_scanner_plan(force=True)
        return jsonify({'success': True, 'scanner': _to_builtin(_scanner_status())})
    except Exception as exc:
        with scanner_plan_lock:
            scanner_plan['active'] = False
            scanner_plan['error'] = str(exc)
        return jsonify({'success': False, 'error': str(exc)}), 400

@api_blueprint.route('/api/scanner/stop', methods=['POST'])
def stop_scanner_plan():
    with scanner_plan_lock:
        scanner_plan['active'] = False
        scanner_plan['receiver_states'] = {}
    rtl433_plugin.stop()
    if getattr(vars, "worker_sdr_suspended", False):
        vars.resume_worker_sdr()
    vars.signal_stats.pop('scanner_mode', None)
    return jsonify({'success': True, 'scanner': _to_builtin(_scanner_status())})

@api_blueprint.route('/api/scanner/status', methods=['GET'])
def scanner_plan_status():
    return jsonify({'success': True, 'scanner': _to_builtin(_scanner_status())})

@api_blueprint.route('/api/gps/status', methods=['GET'])
def gps_status():
    return jsonify({'success': True, 'gps': _to_builtin(gps_plugin.snapshot())})

@atexit.register
def cleanup():
    global running
    running = False
    iq_recorder.stop()
    bluetooth_plugin.stop()
    wifi_plugin.stop()
    zigbee_plugin.stop()
    adsb_plugin.stop()
    rtl433_plugin.stop()
    gps_plugin.stop()
    if replay_sdr is not None:
        try:
            replay_sdr.stop()
        except Exception:
            pass
    try:
        vars.sdr0.stop()
    except Exception:
        pass
    worker = getattr(vars, "worker_sdr", None)
    if worker is not None:
        try:
            worker.stop()
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
