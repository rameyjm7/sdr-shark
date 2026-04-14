import React, { useEffect } from 'react';
import {
  Accordion,
  AccordionDetails,
  AccordionSummary,
  Box,
  FormControlLabel,
  Switch,
  TextField,
  Typography,
} from '@mui/material';
import ExpandMoreIcon from '@mui/icons-material/ExpandMore';

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

  return (
    <Box>
      <Accordion defaultExpanded disableGutters sx={{ mt: 2, borderRadius: 2, overflow: 'hidden' }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ px: 2 }}>
          <Typography variant="h6">SDR Settings</Typography>
        </AccordionSummary>
        <AccordionDetails sx={{ px: 2, pb: 2 }}>
          <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
            <TextField
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
          <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
            <TextField
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

      <Accordion defaultExpanded disableGutters sx={{ mt: 2, borderRadius: 2, overflow: 'hidden' }}>
        <AccordionSummary expandIcon={<ExpandMoreIcon />} sx={{ px: 2 }}>
          <Typography variant="h6">Sweep Settings</Typography>
        </AccordionSummary>
        <AccordionDetails sx={{ px: 2, pb: 2 }}>
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
          <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
            <TextField
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
          <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
            <TextField
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
