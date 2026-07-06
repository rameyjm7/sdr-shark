import React, { useEffect, useState } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  Button,
  Chip,
  FormControlLabel,
  MenuItem,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';
import axios from 'axios';

const SDRSettings = ({ settings, selectedDevice, handleChange, handleKeyPress, setSettings }) => {
  const toFinite = (value, fallback) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  const frequencyStart = toFinite(settings.frequency_start, 700);
  const frequencyStop = toFinite(settings.frequency_stop, 820);
  const totalBandwidth = frequencyStop - frequencyStart;
  const centerFrequency = (frequencyStart + frequencyStop) / 2;

  // Ensure all settings have valid defaults to avoid uncontrolled to controlled warnings
  const frequency = toFinite(settings.frequency, 751);
  const gain = toFinite(settings.gain, 10);
  const sampleRate = toFinite(settings.sampleRate, 20);
  const bandwidth = toFinite(settings.bandwidth, 20);
  const lockBandwidthSampleRate = typeof settings.lockBandwidthSampleRate === 'boolean' ? settings.lockBandwidthSampleRate : false;
  const dcSuppress = typeof settings.dcSuppress === 'boolean' ? settings.dcSuppress : false;
  const decodersAlwaysEnabled = typeof settings.decodersAlwaysEnabled === 'boolean' ? settings.decodersAlwaysEnabled : false;
  const rfModelClassifierEnabled = typeof settings.rfModelClassifierEnabled === 'boolean' ? settings.rfModelClassifierEnabled : false;
  const rfModelClassifierRepoPath = settings.rfModelClassifierRepoPath || '/home/jake/workspace/SDR/rf-signal-intelligence';
  const rfModelClassifierModelPath = settings.rfModelClassifierModelPath || `${rfModelClassifierRepoPath}/models/noisy_drone_rf_v2/noisy_drone_rf_v2_vgg_full_complex_spectrogram_best.keras`;
  const rfModelClassifierTargetMHz = toFinite(settings.rfModelClassifierTargetMHz, 2399);
  const rfModelClassifierBandwidthMHz = toFinite(settings.rfModelClassifierBandwidthMHz, 20);
  const rfModelClassifierIntervalSec = toFinite(settings.rfModelClassifierIntervalSec, 1);
  const rfModelClassifierThreshold = toFinite(settings.rfModelClassifierThreshold, 0.45);
  const sweepingEnabled = typeof settings.sweeping_enabled === 'boolean' ? settings.sweeping_enabled : false;
  const [iqSessions, setIqSessions] = useState([]);
  const [selectedIqSession, setSelectedIqSession] = useState('');
  const [iqStatus, setIqStatus] = useState(null);
  const [iqBusy, setIqBusy] = useState(false);
  const selectedMaxSampleRateMHz = Math.max(
    0.25,
    toFinite(selectedDevice ? Number(selectedDevice.max_sample_rate_sps) / 1e6 : sampleRate, 20),
  );

  // Effect to handle bandwidth update when sample rate changes
  useEffect(() => {
    if (lockBandwidthSampleRate && bandwidth !== sampleRate) {
      handleChange({
        target: {
          name: 'bandwidth',
          value: sampleRate,
        },
      });
    }
  }, [sampleRate, lockBandwidthSampleRate, bandwidth, handleChange]);

  const handleSecondTraceToggle = (e) => {
    setSettings((prevSettings) => ({
      ...prevSettings,
      showSecondTrace: e.target.checked,
    }));
  };

  const loadIqSessions = async () => {
    try {
      const [sessionsResponse, statusResponse, replayResponse] = await Promise.all([
        axios.get('/api/iq/sessions'),
        axios.get('/api/iq/record/status'),
        axios.get('/api/iq/replay/status'),
      ]);
      const sessions = sessionsResponse.data?.sessions || [];
      setIqSessions(sessions);
      if (!selectedIqSession && sessions.length > 0) {
        setSelectedIqSession(sessions[0].id || '');
      }
      setIqStatus({
        recording: statusResponse.data?.recording || null,
        replay: replayResponse.data?.replay || null,
      });
    } catch (error) {
      console.error('Error loading IQ sessions:', error);
    }
  };

  useEffect(() => {
    loadIqSessions();
    const timer = setInterval(loadIqSessions, 2500);
    return () => clearInterval(timer);
  }, []);

  const startIqRecording = async () => {
    setIqBusy(true);
    try {
      await axios.post('/api/iq/record/start', {
        label: `${selectedDevice?.id || settings.sdr || 'sdr'}-${Math.round(frequency)}MHz`,
        max_seconds: 0,
        max_mb: 0,
      });
      await loadIqSessions();
    } catch (error) {
      console.error('Error starting IQ recording:', error);
    } finally {
      setIqBusy(false);
    }
  };

  const stopIqRecording = async () => {
    setIqBusy(true);
    try {
      await axios.post('/api/iq/record/stop');
      await loadIqSessions();
    } catch (error) {
      console.error('Error stopping IQ recording:', error);
    } finally {
      setIqBusy(false);
    }
  };

  const startIqReplay = async () => {
    if (!selectedIqSession) return;
    setIqBusy(true);
    try {
      await axios.post('/api/iq/replay/start', { id: selectedIqSession, loop: true, speed: 1 });
      await loadIqSessions();
    } catch (error) {
      console.error('Error starting IQ replay:', error);
    } finally {
      setIqBusy(false);
    }
  };

  const stopIqReplay = async () => {
    setIqBusy(true);
    try {
      await axios.post('/api/iq/replay/stop');
      await loadIqSessions();
    } catch (error) {
      console.error('Error stopping IQ replay:', error);
    } finally {
      setIqBusy(false);
    }
  };

  const recordingActive = Boolean(iqStatus?.recording?.active);
  const replayActive = Boolean(iqStatus?.replay);

  return (
    <Box>
      <Accordion defaultExpanded disableGutters sx={{ mt: 1, borderRadius: 2, overflow: 'hidden' }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ px: 2 }}>
          <Typography variant="h6">SDR Settings</Typography>
        </AccordionSummary>
        <AccordionDetails sx={{ px: 1.5, pb: 1.5 }}>
          <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <TextField
              size="small"
              margin="dense"
              label="Frequency (MHz)"
              name="frequency"
              type="number"
              value={sweepingEnabled ? centerFrequency : frequency}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 0.1 }}
              disabled={sweepingEnabled}
              sx={{ flex: 1, mr: 2 }}
            />
            <TextField
              size="small"
              margin="dense"
              label="Gain (dB)"
              name="gain"
              type="number"
              value={gain}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 1 }}
              sx={{ flex: 1, ml: 2 }}
            />
          </Box>
          <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <TextField
              size="small"
              margin="dense"
              label="Sample Rate (MHz)"
              name="sampleRate"
              type="number"
              value={sweepingEnabled ? selectedMaxSampleRateMHz : sampleRate}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 0.1 }}
              disabled={sweepingEnabled}
              sx={{ flex: 1, mr: 2 }}
            />
            <TextField
              size="small"
              margin="dense"
              label="Bandwidth (MHz)"
              name="bandwidth"
              type="number"
              value={sweepingEnabled ? Math.min(totalBandwidth, selectedMaxSampleRateMHz) : bandwidth}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 0.1 }}
              disabled={sweepingEnabled || lockBandwidthSampleRate}
              sx={{ flex: 1, ml: 2 }}
            />
          </Box>
          <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <FormControlLabel
              control={
                <Switch
                  checked={lockBandwidthSampleRate}
                  onChange={handleChange}
                  name="lockBandwidthSampleRate"
                  color="primary"
                />
              }
              label="Lock Bandwidth to Sample Rate"
            />
            <FormControlLabel
              control={
                <Switch
                  checked={dcSuppress}
                  onChange={handleChange}
                  name="dcSuppress"
                  color="primary"
                />
              }
              label="Suppress DC Spike"
            />
          </Box>
          <Box
            sx={{
              mt: 1,
              p: 1.25,
              borderRadius: 2,
              border: '1px solid rgba(144, 202, 249, 0.18)',
              bgcolor: decodersAlwaysEnabled ? 'rgba(100, 240, 210, 0.08)' : 'rgba(255, 255, 255, 0.025)',
            }}
          >
            <FormControlLabel
              control={
                <Switch
                  checked={decodersAlwaysEnabled}
                  onChange={handleChange}
                  name="decodersAlwaysEnabled"
                  color="primary"
                />
              }
              label="Keep decoders enabled while manually tuned"
            />
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', ml: 5 }}>
              When enabled, protocol decoders run whenever your current receive window overlaps their bands. Scanner mode still uses its selected protocol plan.
            </Typography>
          </Box>
        </AccordionDetails>
      </Accordion>

      <Accordion disableGutters sx={{ mt: 1, borderRadius: 2, overflow: 'hidden' }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ px: 2 }}>
          <Typography variant="h6">RF Model Classifier</Typography>
        </AccordionSummary>
        <AccordionDetails sx={{ px: 1.5, pb: 1.5 }}>
          <Box
            sx={{
              p: 1.25,
              borderRadius: 2,
              border: '1px solid rgba(144, 202, 249, 0.18)',
              bgcolor: rfModelClassifierEnabled ? 'rgba(100, 240, 210, 0.08)' : 'rgba(255, 255, 255, 0.025)',
              mb: 1,
            }}
          >
            <FormControlLabel
              control={
                <Switch
                  checked={rfModelClassifierEnabled}
                  onChange={handleChange}
                  name="rfModelClassifierEnabled"
                  color="primary"
                />
              }
              label="Run NoisyDroneRF classifier on the live IQ stream"
            />
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', ml: 5 }}>
              Uses the current SDR-Shark samples when the configured target frequency is inside the receive passband. The TensorFlow model runs in a background thread.
            </Typography>
          </Box>
          <TextField
            fullWidth
            size="small"
            margin="dense"
            label="RF signal intelligence repo"
            name="rfModelClassifierRepoPath"
            value={rfModelClassifierRepoPath}
            onChange={handleChange}
            onKeyPress={handleKeyPress}
            variant="outlined"
            InputLabelProps={{ shrink: true }}
          />
          <TextField
            fullWidth
            size="small"
            margin="dense"
            label="Model path"
            name="rfModelClassifierModelPath"
            value={rfModelClassifierModelPath}
            onChange={handleChange}
            onKeyPress={handleKeyPress}
            variant="outlined"
            InputLabelProps={{ shrink: true }}
          />
          <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mt: 0.5, gap: 1 }}>
            <TextField
              size="small"
              margin="dense"
              label="Target (MHz)"
              name="rfModelClassifierTargetMHz"
              type="number"
              value={rfModelClassifierTargetMHz}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 0.001 }}
              sx={{ flex: 1 }}
            />
            <TextField
              size="small"
              margin="dense"
              label="Interval (sec)"
              name="rfModelClassifierIntervalSec"
              type="number"
              value={rfModelClassifierIntervalSec}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 0.25, min: 0.25 }}
              sx={{ flex: 1 }}
            />
            <TextField
              size="small"
              margin="dense"
              label="Model BW (MHz)"
              name="rfModelClassifierBandwidthMHz"
              type="number"
              value={rfModelClassifierBandwidthMHz}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 1, min: 1 }}
              sx={{ flex: 1 }}
            />
            <TextField
              size="small"
              margin="dense"
              label="Min confidence"
              name="rfModelClassifierThreshold"
              type="number"
              value={rfModelClassifierThreshold}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 0.05, min: 0, max: 1 }}
              sx={{ flex: 1 }}
            />
          </Box>
        </AccordionDetails>
      </Accordion>

      <Accordion disableGutters sx={{ mt: 1, borderRadius: 2, overflow: 'hidden' }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ px: 2 }}>
          <Typography variant="h6">IQ Capture / Replay</Typography>
        </AccordionSummary>
        <AccordionDetails sx={{ px: 1.5, pb: 1.5 }}>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, alignItems: 'center', mb: 1 }}>
            <Chip
              size="small"
              color={recordingActive ? 'error' : 'default'}
              label={recordingActive ? `recording ${(Number(iqStatus?.recording?.bytes || 0) / 1048576).toFixed(1)} MB` : 'recorder idle'}
            />
            <Chip
              size="small"
              color={replayActive ? 'success' : 'default'}
              label={replayActive ? 'replay active' : 'live source'}
            />
          </Box>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 1 }}>
            <Button size="small" variant="contained" disabled={iqBusy || recordingActive || replayActive} onClick={startIqRecording}>
              Record Session
            </Button>
            <Button size="small" variant="outlined" disabled={iqBusy || !recordingActive} onClick={stopIqRecording}>
              Stop Recording
            </Button>
            <Button size="small" variant="outlined" disabled={iqBusy} onClick={loadIqSessions}>
              Refresh
            </Button>
          </Box>
          <TextField
            select
            fullWidth
            size="small"
            label="Replay Session"
            value={selectedIqSession}
            onChange={(event) => setSelectedIqSession(event.target.value)}
            sx={{ mb: 1 }}
            InputLabelProps={{ shrink: true }}
          >
            {iqSessions.length === 0 ? (
              <MenuItem value="">No IQ sessions recorded</MenuItem>
            ) : iqSessions.map((session) => (
              <MenuItem key={session.id} value={session.id}>
                {session.id} · {(Number(session.bytes || 0) / 1048576).toFixed(1)} MB
              </MenuItem>
            ))}
          </TextField>
          <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
            <Button size="small" variant="contained" disabled={iqBusy || !selectedIqSession || recordingActive || replayActive} onClick={startIqReplay}>
              Replay Into SDR Shark
            </Button>
            <Button size="small" variant="outlined" disabled={iqBusy || !replayActive} onClick={stopIqReplay}>
              Return To Live Radio
            </Button>
          </Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            Saves raw CS8 IQ plus metadata under ~/.sdr-shark/iq-sessions for offline decoder verification.
          </Typography>
        </AccordionDetails>
      </Accordion>

      <Accordion defaultExpanded disableGutters sx={{ mt: 1, borderRadius: 2, overflow: 'hidden' }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ px: 2 }}>
          <Typography variant="h6">Sweep Settings</Typography>
        </AccordionSummary>
        <AccordionDetails sx={{ px: 1.5, pb: 1.5 }}>
          <FormControlLabel
            control={
              <Switch
                checked={sweepingEnabled}
                onChange={handleChange}
                name="sweeping_enabled"
                color="primary"
              />
            }
            label="Enable Sweep"
          />
          <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <TextField
              size="small"
              margin="dense"
              label="Start Frequency (MHz)"
              name="frequency_start"
              type="number"
              value={frequencyStart}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 0.1 }}
              sx={{ flex: 1, mr: 2 }}
            />
            <TextField
              size="small"
              margin="dense"
              label="Stop Frequency (MHz)"
              name="frequency_stop"
              type="number"
              value={frequencyStop}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 0.1 }}
              sx={{ flex: 1, ml: 2 }}
            />
          </Box>
          <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mb: 0.5 }}>
            <TextField
              size="small"
              margin="dense"
              label="Total Bandwidth (MHz)"
              name="total_bandwidth"
              type="number"
              value={totalBandwidth}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 0.1 }}
              sx={{ flex: 1, mr: 2 }}
              disabled
            />
            <TextField
              size="small"
              margin="dense"
              label="Sweep Steps"
              name="sweep_steps"
              type="number"
              value={toFinite(settings.sweep_steps, Math.max(1, Math.ceil(totalBandwidth / Math.max(0.1, bandwidth))))}
              onChange={handleChange}
              onKeyPress={handleKeyPress}
              variant="outlined"
              InputLabelProps={{ shrink: true }}
              inputProps={{ step: 1 }}
              sx={{ flex: 1, ml: 2 }}
            />
          </Box>
        </AccordionDetails>
      </Accordion>
    </Box>
  );
};

export default SDRSettings;
