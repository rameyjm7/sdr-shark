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
