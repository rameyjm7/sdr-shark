import React, { useState, useEffect } from 'react';
import { Box, Typography, TextField, Switch, FormControlLabel } from '@mui/material';
import axios from 'axios';

const SETTINGS_POST_CONFIG = {
  headers: { 'Content-Type': 'application/json' },
  timeout: 8000,
};

const SweepSettings = ({ settings, setSettings, setStatus }) => {
  const [localSettings, setLocalSettings] = useState({
    frequency_start: settings.frequency_start,
    frequency_stop: settings.frequency_stop,
    bandwidth: settings.sdr === 'sidekiq' ? 60 : 20,
    sweeping_enabled: settings.sweeping_enabled,
  });

  const fetchSettings = async () => {
    setStatus('Fetching settings...');
    try {
      const response = await axios.get('/api/get_settings');
      const data = response.data;
      setLocalSettings({
        frequency_start: data.frequency_start,
        frequency_stop: data.frequency_stop,
        bandwidth: data.sdr === 'sidekiq' ? 60 : 20,
        sweeping_enabled: data.sweeping_enabled,
      });
      setStatus('Settings loaded');
    } catch (error) {
      console.error('Error fetching settings:', error);
      setStatus('Error fetching settings');
    }
  };

  // Effect to fetch settings when component mounts
  useEffect(() => {
    fetchSettings();
  }, []);

  useEffect(() => {
    if (localSettings.sweeping_enabled) {
      setLocalSettings((prevSettings) => ({
        ...prevSettings,
        bandwidth: settings.sdr === 'sidekiq' ? 60 : 20,
        sampleRate: settings.sdr === 'sidekiq' ? 60 : 20,
      }));
    }
  }, [localSettings.sweeping_enabled, settings.sdr]);

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    const newValue = type === 'checkbox' ? checked : parseFloat(value);
    setLocalSettings((prevSettings) => ({
      ...prevSettings,
      [name]: newValue,
    }));

    if (name === 'sweeping_enabled') {
      updateSweepingEnabled(newValue);
    }
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      applySettings();
    }
  };

  const updateSweepingEnabled = async (sweepingEnabled) => {
    setStatus('Updating settings...');
    try {
      const newSettings = {
        ...settings,
        sweeping_enabled: sweepingEnabled,
        bandwidth: settings.sdr === 'sidekiq' ? 60 : 20,
        sampleRate: settings.sdr === 'sidekiq' ? 60 : 20,
        center_freq: (localSettings.frequency_start + localSettings.frequency_stop) / 2,
      };

      const response = await axios.post('/api/update_settings', newSettings, SETTINGS_POST_CONFIG);
      if (response?.data?.success === false) {
        throw new Error(response.data.error || 'Settings update failed');
      }

      setSettings(newSettings);
      setStatus('Settings updated');
    } catch (error) {
      console.error('Error updating settings:', error);
      setStatus('Error updating settings');
    }
  };

  const applySettings = async () => {
    setStatus('Updating settings...');
    try {
      const newSettings = {
        ...settings,
        frequency_start: localSettings.frequency_start,
        frequency_stop: localSettings.frequency_stop,
        bandwidth: settings.sdr === 'sidekiq' ? 60 : 20, // Ensure bandwidth is always set based on SDR
        sampleRate: settings.sdr === 'sidekiq' ? 60 : 20, // Ensure sample rate is always set based on SDR
        sweeping_enabled: localSettings.sweeping_enabled,
        center_freq: (localSettings.frequency_start + localSettings.frequency_stop) / 2,
      };

      const response = await axios.post('/api/update_settings', newSettings, SETTINGS_POST_CONFIG);
      if (response?.data?.success === false) {
        throw new Error(response.data.error || 'Settings update failed');
      }

      setSettings(newSettings);
      setStatus('Settings updated');
    } catch (error) {
      console.error('Error updating settings:', error);
      setStatus('Error updating settings');
    }
  };

  const totalBandwidth = localSettings.frequency_stop - localSettings.frequency_start;
  const sweepSteps = Math.ceil(totalBandwidth / localSettings.bandwidth);

  return (
    <Box>
      <FormControlLabel
        control={
          <Switch
            checked={localSettings.sweeping_enabled}
            onChange={handleChange}
            name="sweeping_enabled"
            color="primary"
          />
        }
        label="Enable Sweep"
      />
      <Box display="flex" justifyContent="space-between">
        <TextField
          fullWidth
          margin="dense"
          label="Start Frequency (MHz)"
          name="frequency_start"
          type="number"
          value={localSettings.frequency_start}
          onChange={handleChange}
          onKeyPress={handleKeyPress}
          variant="outlined"
          InputLabelProps={{ shrink: true }}
          inputProps={{ step: 0.1 }}
        />
        <TextField
          fullWidth
          margin="dense"
          label="Stop Frequency (MHz)"
          name="frequency_stop"
          type="number"
          value={localSettings.frequency_stop}
          onChange={handleChange}
          onKeyPress={handleKeyPress}
          variant="outlined"
          InputLabelProps={{ shrink: true }}
          inputProps={{ step: 0.1 }}
        />
      </Box>
      <Box display="flex" justifyContent="space-between">
        <TextField
          fullWidth
          margin="dense"
          label="Total Bandwidth (MHz)"
          name="total_bandwidth"
          type="number"
          value={totalBandwidth}
          variant="outlined"
          InputLabelProps={{ shrink: true }}
          disabled
        />
        <TextField
          fullWidth
          margin="dense"
          label="Sweep Steps"
          name="sweep_steps"
          type="number"
          value={sweepSteps}
          variant="outlined"
          InputLabelProps={{ shrink: true }}
          disabled
        />
      </Box>
    </Box>
  );
};

export default SweepSettings;
