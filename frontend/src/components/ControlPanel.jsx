import React, { useState, useEffect } from 'react';
import { Box, Typography, Select, MenuItem, IconButton, Tabs, Tab, Button, CircularProgress, Slider, FormControlLabel, Switch, TextField, Chip } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import SaveIcon from '@mui/icons-material/Save';
import axios from 'axios';
import SDRSettings from './ControlPanel/SDRSettings';
import PlotSettings from './ControlPanel/PlotSettings';
import debounce from 'lodash/debounce';
import '../App.css';
import Actions from './Actions';

const PROFILE_STORAGE_KEY = 'sdrshark_ui_profiles_v1';
const RECENT_FREQ_STORAGE_KEY = 'sdrshark_recent_frequencies_v1';

const ControlPanel = ({
  settings,
  setSettings,
  minY,
  setMinY,
  maxY,
  setMaxY,
  updateInterval,
  setUpdateInterval,
  waterfallSamples,
  setWaterfallSamples,
  showWaterfall,
  setShowWaterfall,
  addVerticalLines,
  clearVerticalLines,
  addHorizontalLines,
  clearHorizontalLines,
  handleSaveSelection,
  verticalLines, // Add this prop to receive vertical lines
}) => {
  const toFinite = (value, fallback) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  const [sdr, setSdr] = useState(settings.sdr || 'hackrf');
  const [availableSdrs, setAvailableSdrs] = useState([]);
  const [statusState, setStatusState] = useState({ text: 'Ready', level: 'info', count: 1 });
  const [saving, setSaving] = useState(false);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [tabIndex, setTabIndex] = useState(0);
  const [tasks, setTasks] = useState([]);
  const [currentTaskIndex, setCurrentTaskIndex] = useState(0);
  const [profiles, setProfiles] = useState({});
  const [selectedProfile, setSelectedProfile] = useState('');
  const [profileName, setProfileName] = useState('');
  const [recentFrequencies, setRecentFrequencies] = useState([]);
  const selectedDevice = availableSdrs.find((d) => d.id === sdr) || null;

  const updateStatus = (text, level = 'info') => {
    setStatusState((prev) => {
      if (prev.text === text && prev.level === level) {
        return { ...prev, count: prev.count + 1 };
      }
      return { text, level, count: 1 };
    });
  };

  useEffect(() => {
    if (!settingsLoaded) {
      fetchSettings();
    }
  }, [settingsLoaded]);

  useEffect(() => {
    try {
      const savedProfiles = JSON.parse(localStorage.getItem(PROFILE_STORAGE_KEY) || '{}');
      if (savedProfiles && typeof savedProfiles === 'object') {
        setProfiles(savedProfiles);
      }
      const savedRecent = JSON.parse(localStorage.getItem(RECENT_FREQ_STORAGE_KEY) || '[]');
      if (Array.isArray(savedRecent)) {
        setRecentFrequencies(savedRecent);
      }
    } catch (error) {
      console.error('Error loading local UI state:', error);
    }
  }, []);

  useEffect(() => {
    const freq = Number(settings.frequency);
    if (!Number.isFinite(freq) || freq <= 0) {
      return;
    }
    setRecentFrequencies((prev) => {
      const next = [freq, ...prev.filter((x) => Math.abs(x - freq) > 1e-6)].slice(0, 10);
      try {
        localStorage.setItem(RECENT_FREQ_STORAGE_KEY, JSON.stringify(next));
      } catch (error) {
        console.error('Error saving recent frequencies:', error);
      }
      return next;
    });
  }, [settings.frequency]);

  const fetchDevices = async () => {
    try {
      const response = await axios.get('/api/sdr_devices');
      const payload = response.data || {};
      const devices = Array.isArray(payload.devices) ? payload.devices : [];
      setAvailableSdrs(devices);
      if (payload.selected) {
        setSdr(payload.selected);
      } else if (devices.length > 0) {
        setSdr(devices[0].id);
      }
      return { devices, selected: payload.selected };
    } catch (error) {
      console.error('Error fetching SDR devices:', error);
      setAvailableSdrs([]);
      return { devices: [], selected: null };
    }
  };


  // Function to delete a task
  const deleteTask = (index) => {
    const updatedTasks = tasks.filter((_, taskIndex) => taskIndex !== index);
    setTasks(updatedTasks);

    // Adjust currentTaskIndex if necessary
    if (currentTaskIndex === index) {
      setCurrentTaskIndex(null); // Reset currentTaskIndex if deleted
    } else if (currentTaskIndex > index) {
      setCurrentTaskIndex((prev) => prev - 1); // Adjust if a prior task was deleted
    }
  };

  // Function to duplicate a task
  const duplicateTask = (index) => {
    const taskToDuplicate = tasks[index];
    const duplicatedTask = { ...taskToDuplicate }; // Create a shallow copy
    const updatedTasks = [...tasks];
    updatedTasks.splice(index + 1, 0, duplicatedTask); // Insert duplicated task
    setTasks(updatedTasks);
  };

  const fetchSettings = async () => {
    try {
      const { devices, selected } = await fetchDevices();
      const response = await axios.get('/api/get_settings');
      const data = response.data;
      const selectedSdr = selected || data.sdr || (devices[0] && devices[0].id) || 'hackrf';
      setSdr(selectedSdr);

      const sanitized = {
        ...data,
        sdr: selectedSdr,
        frequency: toFinite(data.frequency, 751),
        gain: toFinite(data.gain, 10),
        sampleRate: toFinite(data.sampleRate, 20),
        bandwidth: toFinite(data.bandwidth, 20),
        frequency_start: toFinite(data.frequency_start, 700),
        frequency_stop: toFinite(data.frequency_stop, 820),
        waterfallBinCount: toFinite(data.waterfallBinCount, 2048),
        updateInterval: toFinite(data.updateInterval, 500),
        showSecondTrace: data.sdr === 'hackrf',
        dcSuppress: typeof data.dcSuppress === 'boolean' ? data.dcSuppress : true,
        sweeping_enabled: typeof data.sweeping_enabled === 'boolean' ? data.sweeping_enabled : false,
      };

      setSettings((prevSettings) => ({ ...prevSettings, ...sanitized }));
      setUpdateInterval(sanitized.updateInterval);
      updateStatus('Settings loaded', 'success');
      setSettingsLoaded(true);
      setTimeout(fetchAndAdjustYAxis, 1000);

    } catch (error) {
      console.error('Error fetching settings:', error);
      updateStatus('Error fetching settings', 'error');
    }
  };

  const updateSettings = async (newSettings) => {
    try {
      await axios.post('/api/update_settings', newSettings, {
        headers: {
          'Content-Type': 'application/json',
        },
      });
      setSettings(newSettings);
      updateStatus('Settings updated', 'success');

      setTimeout(fetchAndAdjustYAxis, 1000);
    } catch (error) {
      console.error('Error updating settings:', error);
      updateStatus('Error updating settings', 'error');
    }
  };

  const fetchAndAdjustYAxis = async () => {
    try {
      const response = await axios.get('/api/noise_floor');
      const noiseFloor = response.data.noise_floor;

      const newMinY = noiseFloor - 30;
      const newMaxY = noiseFloor + 70;
      setMinY(newMinY);
      setMaxY(newMaxY);
    } catch (error) {
      console.error('Error fetching noise floor:', error);
    }
  };

  const handleSaveSelectionClick = async () => {
    setSaving(true);
    updateStatus('Saving file...', 'info');

    try {
      await handleSaveSelection();
      updateStatus('Selection saved successfully', 'success');
    } catch (error) {
      console.error('Error saving selection:', error);
      updateStatus('Error saving selection', 'error');
    } finally {
      setSaving(false);
    }
  };

  const handleChange = async (e) => {
    const { name, value, type, checked } = e.target;
    const parsed = parseFloat(value);
    const newValue = type === 'checkbox' ? checked : (Number.isFinite(parsed) ? parsed : settings[name]);
    const newSettings = { ...settings, [name]: newValue };
    setSettings(newSettings);

    // Sweep toggle should apply immediately to avoid requiring a manual save.
    if (name === 'sweeping_enabled') {
      try {
        await axios.post(newValue ? '/api/start_sweep' : '/api/stop_sweep');
      } catch (error) {
        console.error('Error toggling sweep state:', error);
      }
      await applySettings(newSettings);
    }
  };

  const handleSliderChange = (e, value, name) => {
    const sliderValue = Array.isArray(value) ? value[0] : value;
    const safeValue = Number.isFinite(sliderValue) ? sliderValue : settings[name];
    const newSettings = { ...settings, [name]: safeValue };
    setSettings(newSettings);
    // if (name === 'averagingCount') {
    //   debouncedApplySettings(newSettings);
    // }
  };

  const handleSliderChangeCommitted = (e, value, name) => {
    const sliderValue = Array.isArray(value) ? value[0] : value;
    const safeValue = Number.isFinite(sliderValue) ? sliderValue : settings[name];
    if (name === 'averagingCount') {
      applySettings({ ...settings, [name]: safeValue });
    }
  };

  const debouncedApplySettings = debounce((newSettings) => {
    applySettings(newSettings);
  }, 300);

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      applySettings(settings);
    }
  };

  const handleSdrChange = async (e) => {
    const newSdr = e.target.value;
    const prevSdr = sdr;
    const prevSettings = { ...settings };
    setSdr(newSdr);
    updateStatus(`Changing SDR to ${newSdr}...`, 'info');

    const updatedSettings = {
      ...settings,
      sdr: newSdr,
      showSecondTrace: newSdr === 'hackrf',
    };
    setSettings(updatedSettings);

    try {
      const response = await axios.post('/api/select_sdr', { sdr_name: newSdr });
      if (!response?.data?.result) {
        throw new Error(response?.data?.message || `Failed to switch SDR to ${newSdr}`);
      }
      await applySettings(updatedSettings);
      updateStatus(`SDR changed to ${newSdr}`, 'success');
    } catch (error) {
      console.error('Error changing SDR:', error);
      setSdr(prevSdr);
      setSettings(prevSettings);
      updateStatus(`Error changing SDR to ${newSdr}`, 'error');
    }
  };

  const enforceLimits = (settings) => {
    const newSettings = { ...settings };
    const device = availableSdrs.find((d) => d.id === sdr);
    const freqMinMHz = device ? Number(device.freq_min_hz) / 1e6 : 1;
    const freqMaxMHz = device ? Number(device.freq_max_hz) / 1e6 : 6000;
    const srMaxMHz = device ? Number(device.max_sample_rate_sps) / 1e6 : 20;

    const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

    newSettings.frequency = clamp(toFinite(newSettings.frequency, freqMinMHz), freqMinMHz, freqMaxMHz);
    newSettings.gain = clamp(toFinite(newSettings.gain, 10), 0, 62);
    newSettings.sampleRate = clamp(toFinite(newSettings.sampleRate, srMaxMHz), 0.25, srMaxMHz);
    newSettings.bandwidth = clamp(toFinite(newSettings.bandwidth, newSettings.sampleRate), 0.2, srMaxMHz);
    newSettings.frequency_start = clamp(toFinite(newSettings.frequency_start, freqMinMHz), freqMinMHz, freqMaxMHz);
    newSettings.frequency_stop = clamp(toFinite(newSettings.frequency_stop, freqMaxMHz), freqMinMHz, freqMaxMHz);
    if (newSettings.frequency_stop < newSettings.frequency_start) {
      newSettings.frequency_stop = newSettings.frequency_start;
    }

    return newSettings;
  };

  const applySettings = async (newSettings) => {
    const enforcedSettings = enforceLimits(newSettings);
    const preservedInterval = toFinite(settings.updateInterval, toFinite(updateInterval, 500));
    if (!Number.isFinite(enforcedSettings.updateInterval)) {
      enforcedSettings.updateInterval = preservedInterval;
    }
    updateStatus('Updating settings...', 'info');

    try {
      await axios.post('/api/update_settings', enforcedSettings, {
        headers: {
          'Content-Type': 'application/json',
        },
      });
      setSettings(enforcedSettings);
      setUpdateInterval(toFinite(enforcedSettings.updateInterval, preservedInterval));

      updateStatus('Settings updated', 'success');
    } catch (error) {
      console.error('Error updating settings:', error);
      updateStatus('Error updating settings', 'error');
    }
  };

  const handleTabChange = (event, newValue) => {
    setTabIndex(newValue);
  };

  const getProfilePayload = () => ({
    sdr: settings.sdr,
    frequency: toFinite(settings.frequency, 751),
    gain: toFinite(settings.gain, 10),
    sampleRate: toFinite(settings.sampleRate, 20),
    bandwidth: toFinite(settings.bandwidth, 20),
    frequency_start: toFinite(settings.frequency_start, 700),
    frequency_stop: toFinite(settings.frequency_stop, 820),
    sweeping_enabled: typeof settings.sweeping_enabled === 'boolean' ? settings.sweeping_enabled : false,
    lockBandwidthSampleRate: typeof settings.lockBandwidthSampleRate === 'boolean' ? settings.lockBandwidthSampleRate : true,
    dcSuppress: typeof settings.dcSuppress === 'boolean' ? settings.dcSuppress : true,
    waterfallBinCount: toFinite(settings.waterfallBinCount, 2048),
    waterfallSamples: toFinite(settings.waterfallSamples, 100),
  });

  const saveProfile = () => {
    const resolvedName = (profileName || selectedProfile || '').trim();
    if (!resolvedName) {
      updateStatus('Enter a profile name first', 'warning');
      return;
    }
    const nextProfiles = { ...profiles, [resolvedName]: getProfilePayload() };
    setProfiles(nextProfiles);
    setSelectedProfile(resolvedName);
    setProfileName('');
    try {
      localStorage.setItem(PROFILE_STORAGE_KEY, JSON.stringify(nextProfiles));
      updateStatus(`Profile saved: ${resolvedName}`, 'success');
    } catch (error) {
      console.error('Error saving profile:', error);
      updateStatus('Error saving profile', 'error');
    }
  };

  const loadProfile = async () => {
    if (!selectedProfile || !profiles[selectedProfile]) {
      updateStatus('Select a profile to load', 'warning');
      return;
    }
    const merged = { ...settings, ...profiles[selectedProfile] };
    setSettings(merged);
    await applySettings(merged);
    updateStatus(`Profile loaded: ${selectedProfile}`, 'success');
  };

  const tuneRecentFrequency = async (freqMHz) => {
    const merged = { ...settings, frequency: freqMHz };
    setSettings(merged);
    await applySettings(merged);
  };

  return (
    <Box className="control-panel" sx={{ p: 1.5 }}>
      <Box display="flex" alignItems="center">
        <Typography
          variant="subtitle2"
          color={
            statusState.level === 'error'
              ? 'error.main'
              : statusState.level === 'warning'
                ? 'warning.main'
                : statusState.level === 'success'
                  ? 'success.main'
                  : 'textSecondary'
          }
          sx={{ mb: 1 }}
        >
          {saving ? (
            <>
              Saving file... <CircularProgress size={14} sx={{ ml: 1 }} />
            </>
          ) : (
            `Status: ${statusState.text}${statusState.count > 1 ? ` (x${statusState.count})` : ''}`
          )}
        </Typography>
        <IconButton onClick={fetchSettings} sx={{ ml: 2 }}>
          <RefreshIcon />
        </IconButton>
        <IconButton onClick={() => applySettings(settings)} sx={{ ml: 2 }}>
          <SaveIcon />
        </IconButton>
      </Box>
      <Tabs
        value={tabIndex}
        onChange={handleTabChange}
        variant="scrollable"
        scrollButtons="auto"
        sx={{ minHeight: 36, '& .MuiTab-root': { minHeight: 36, py: 0.5 } }}
      >
        <Tab label="SDR" />
        <Tab label="Plot" />
        <Tab label="Actions" />
      </Tabs>
      <Box className="control-panel-tab-content">
        {tabIndex === 0 && (
          <>
            <Typography variant="body1">Select SDR:</Typography>
            <Select value={sdr || ''} onChange={handleSdrChange} fullWidth disabled={availableSdrs.length === 0}>
              {availableSdrs.map((device) => (
                <MenuItem key={device.id} value={device.id}>
                  {device.label || device.id}
                </MenuItem>
              ))}
            </Select>
            <Box sx={{ display: 'flex', gap: 1, mt: 1, alignItems: 'center' }}>
              <Select
                size="small"
                value={selectedProfile}
                onChange={(e) => setSelectedProfile(e.target.value)}
                displayEmpty
                sx={{ minWidth: 180, flex: 1 }}
              >
                <MenuItem value="">Saved profile...</MenuItem>
                {Object.keys(profiles).sort().map((name) => (
                  <MenuItem key={name} value={name}>{name}</MenuItem>
                ))}
              </Select>
              <Button size="small" variant="outlined" onClick={loadProfile}>Load</Button>
            </Box>
            <Box sx={{ display: 'flex', gap: 1, mt: 1 }}>
              <TextField
                size="small"
                fullWidth
                label="Profile name"
                value={profileName}
                onChange={(e) => setProfileName(e.target.value)}
              />
              <Button size="small" variant="contained" onClick={saveProfile}>Save Current</Button>
            </Box>
            <Box sx={{ display: 'flex', gap: 0.75, mt: 1, flexWrap: 'wrap' }}>
              {recentFrequencies.map((freq) => (
                <Chip
                  key={freq}
                  size="small"
                  label={`${freq.toFixed(3)} MHz`}
                  onClick={() => tuneRecentFrequency(freq)}
                  variant="outlined"
                />
              ))}
            </Box>
            <SDRSettings
              settings={settings}
              selectedDevice={selectedDevice}
              handleChange={handleChange}
              handleKeyPress={handleKeyPress}
              setSettings={setSettings}
            />
          </>
        )}
        {tabIndex === 1 && (
          <>
            <PlotSettings
              settings={settings}
              setSettings={setSettings}
              setUpdateInterval={setUpdateInterval}
              handleSliderChange={handleSliderChange}
              handleSliderChangeCommitted={handleSliderChangeCommitted}
              handleChange={handleChange}
              minY={minY}
              setMinY={setMinY}
              maxY={maxY}
              setMaxY={setMaxY}
            />
            {/* <WaterfallSettings
              settings={settings}
              setSettings={setSettings}
              showWaterfall={showWaterfall}
              setShowWaterfall={setShowWaterfall}
            /> */}

            <Box>
              <Typography variant="h6" sx={{ mt: 2, mb: 1 }}>Waterfall Settings</Typography>

              <Box
                sx={{
                  display: 'grid',
                  gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
                  gap: 2,
                  alignItems: 'start',
                }}
              >
                <Box>
                  <Typography variant="body2" gutterBottom>Waterfall Samples: {toFinite(settings.waterfallSamples, 100)}</Typography>
                  <Slider
                    min={25}
                    max={2000}
                    value={toFinite(settings.waterfallSamples, 100)}
                    onChange={(e, value) => setSettings({ ...settings, waterfallSamples: value })}
                    valueLabelDisplay="auto"
                    step={25}
                    sx={{ '& .MuiSlider-thumb': { width: 18, height: 18 }, '& .MuiSlider-rail': { opacity: 0.35 } }}
                  />
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 0.25, px: 0.5 }}>
                    <Typography variant="caption" color="text.secondary">25</Typography>
                    <Typography variant="caption" color="text.secondary">1000</Typography>
                    <Typography variant="caption" color="text.secondary">2000</Typography>
                  </Box>
                </Box>
                <Box>
                  <Typography variant="body2" gutterBottom>Waterfall Bin Count: {toFinite(settings.waterfallBinCount, 2048)}</Typography>
                  <Slider
                    min={256}
                    max={4096}
                    value={toFinite(settings.waterfallBinCount, 2048)}
                    onChange={(e, value) => setSettings({ ...settings, waterfallBinCount: value })}
                    valueLabelDisplay="auto"
                    step={128}
                    sx={{ '& .MuiSlider-thumb': { width: 18, height: 18 }, '& .MuiSlider-rail': { opacity: 0.35 } }}
                  />
                  <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 0.25, px: 0.5 }}>
                    <Typography variant="caption" color="text.secondary">256</Typography>
                    <Typography variant="caption" color="text.secondary">2048</Typography>
                    <Typography variant="caption" color="text.secondary">4096</Typography>
                  </Box>
                </Box>
              </Box>

              <FormControlLabel
                sx={{ mt: 0.5 }}
                control={
                  <Switch
                    checked={showWaterfall}
                    onChange={() => {
                      const newSettings = { ...settings, showWaterfall: !showWaterfall };
                      setSettings(newSettings);
                      setShowWaterfall(!showWaterfall);
                    }}
                    name="showWaterfall"
                    color="primary"
                  />
                }
                label="Enable Waterfall"
              />
            </Box>

          </>
        )}
        {tabIndex === 2 && (
          <Actions
            settings={settings}
            setSettings={setSettings}
            deleteTask={deleteTask}
            duplicateTask={duplicateTask}
            currentTaskIndex={currentTaskIndex}
            tasks={tasks}
            setTasks={(newTasks) => {
              setTasks(newTasks);

              // Extract center frequency and bandwidth from the new tasks
              if (newTasks.length > 0) {
                const latestTask = newTasks[newTasks.length - 1];

                if (latestTask.frequency && latestTask.bandwidth) {
                  const newSettings = {
                    ...settings,
                    centerFrequency: latestTask.frequency,
                    bandwidth: latestTask.bandwidth,
                  };

                  // Update settings with the extracted values
                  setSettings(newSettings);

                  // Optionally log the update for debugging
                }
              }
            }}
          />
        )}



      </Box>
    </Box>
  );
};

export default ControlPanel;
