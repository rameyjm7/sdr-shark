import React, { useState, useEffect } from 'react';
import { Box, Typography, Select, MenuItem, IconButton, Tabs, Tab, Button, CircularProgress } from '@mui/material';
import RefreshIcon from '@mui/icons-material/Refresh';
import SaveIcon from '@mui/icons-material/Save';
import axios from 'axios';
import SDRSettings from './ControlPanel/SDRSettings';
import PlotSettings from './ControlPanel/PlotSettings';
import WaterfallSettings from './ControlPanel/WaterfallSettings';
import Analysis from './Analysis';
import Classifiers from './ControlPanel/Classifiers';
import debounce from 'lodash/debounce';
import '../App.css';

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
  const [sdr, setSdr] = useState(settings.sdr || 'hackrf');
  const [status, setStatus] = useState('Ready');
  const [saving, setSaving] = useState(false);
  const [localSettings, setLocalSettings] = useState(settings);
  const [settingsLoaded, setSettingsLoaded] = useState(false);
  const [tabIndex, setTabIndex] = useState(0);

  useEffect(() => {
    if (!settingsLoaded) {
      fetchSettings();
    }
  }, [settingsLoaded]);

  useEffect(() => {
    updateSettings(localSettings);
  }, [localSettings]);

  const fetchSettings = async () => {
    try {
      const response = await axios.get('/api/get_settings');
      const data = response.data;
      setSdr(data.sdr);

      setSettings((prevSettings) => ({
        ...prevSettings,
        showSecondTrace: data.sdr === 'hackrf',
        ...data,
      }));

      setLocalSettings(data);
      setUpdateInterval(data.updateInterval);
      setWaterfallSamples(data.waterfallSamples);
      setMinY(minY);  
      setMaxY(maxY);  
      setStatus('Settings loaded');
      setSettingsLoaded(true);
    } catch (error) {
      console.error('Error fetching settings:', error);
      setStatus('Error fetching settings');
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
      setStatus('Settings updated');

      setTimeout(fetchAndAdjustYAxis, 1000);
    } catch (error) {
      console.error('Error updating settings:', error);
      setStatus('Error updating settings');
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
    setStatus('Saving file...');

    try {
      await handleSaveSelection();
      setStatus('Selection saved successfully');
    } catch (error) {
      console.error('Error saving selection:', error);
      setStatus('Error saving selection');
    } finally {
      setSaving(false);
    }
  };

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    const newValue = type === 'checkbox' ? checked : parseFloat(value);
    const newSettings = { ...localSettings, [name]: newValue };
    setLocalSettings(newSettings);
  };

  const handleSliderChange = (e, value, name) => {
    const newSettings = { ...localSettings, [name]: value };
    setLocalSettings(newSettings);
    if (name === 'averagingCount') {
      debouncedApplySettings(newSettings);
    }
  };

  const handleSliderChangeCommitted = (e, value, name) => {
    if (name === 'averagingCount') {
      applySettings({ ...localSettings, [name]: value });
    }
  };

  const debouncedApplySettings = debounce((newSettings) => {
    applySettings(newSettings);
  }, 300);

  const handleKeyPress = (e) => {
    if (e.key === 'Enter') {
      applySettings(localSettings);
    }
  };

  const handleSdrChange = (e) => {
    const newSdr = e.target.value;
    setSdr(newSdr);
    setStatus(`Changing SDR to ${newSdr}...`);

    const updatedSettings = {
      ...localSettings,
      sdr: newSdr,
      showSecondTrace: newSdr === 'hackrf',
    };
    setSettings(updatedSettings);
    setLocalSettings(updatedSettings);

    axios.post('/api/select_sdr', { sdr_name: newSdr })
      .then(response => {
        console.log('SDR changed:', response.data);
        applySettings(updatedSettings);
        setStatus(`SDR changed to ${newSdr}`);
      })
      .catch(error => {
        console.error('Error changing SDR:', error);
        setStatus('Error changing SDR');
      });
  };

  const enforceLimits = (settings) => {
    const newSettings = { ...settings };
    const limitKeys = ['frequency', 'gain', 'sampleRate', 'bandwidth'];

    limitKeys.forEach((key) => {
      const limit = sdrLimits[sdr][key];
      if (newSettings[key] < limit.min) {
        newSettings[key] = limit.min;
      }
      if (newSettings[key] > limit.max) {
        newSettings[key] = limit.max;
      }
    });

    return newSettings;
  };

  const applySettings = async (newSettings) => {
    const enforcedSettings = enforceLimits(newSettings);
    setStatus('Updating settings...');
    
    interval = settings.updateInterval;
    // Temporarily set updateInterval to 5000
    setUpdateInterval(5000);
  
    try {
      await axios.post('/api/update_settings', enforcedSettings, {
        headers: {
          'Content-Type': 'application/json',
        },
      });
      setSettings(enforcedSettings);
      setLocalSettings(enforcedSettings);
      // After settings are applied, revert updateInterval to the original value
      setUpdateInterval(interval);
  
      setStatus('Settings updated');
    } catch (error) {
      console.error('Error updating settings:', error);
      setStatus('Error updating settings');
    }
  };
  

  const sdrLimits = {
    hackrf: {
      frequency: { min: 0, max: 7250 },
      gain: { min: 0, max: 61 },
      sampleRate: { min: 0, max: 20 },
      bandwidth: { min: 0, max: 20 },
    },
    sidekiq: {
      frequency: { min: 46.875, max: 6000 },
      gain: { min: 0, max: 76 },
      sampleRate: { min: 0.233, max: 61.233 },
      bandwidth: { min: 0.233, max: 61.233 },
    }
  };

  const handleTabChange = (event, newValue) => {
    setTabIndex(newValue);
  };

  return (
    <Box className="control-panel" sx={{ p: 2 }}>
      <Box display="flex" alignItems="center">
        <Typography variant="subtitle1" color="textSecondary" sx={{ mb: 2 }}>
          {saving ? (
            <>
              Saving file... <CircularProgress size={14} sx={{ ml: 1 }} />
            </>
          ) : (
            `Status: ${status}`
          )}
        </Typography>
        <IconButton onClick={fetchSettings} sx={{ ml: 2 }}>
          <RefreshIcon />
        </IconButton>
        <IconButton onClick={() => applySettings(localSettings)} sx={{ ml: 2 }}>
          <SaveIcon />
        </IconButton>
      </Box>
      <Tabs value={tabIndex} onChange={handleTabChange}>
        <Tab label="SDR" />
        <Tab label="Plot" />
        <Tab label="Analysis" />
        <Tab label="Classifiers" />
      </Tabs>
      <Box className="control-panel-tab-content">
        {tabIndex === 0 && (
          <>
            <Typography variant="h6">SDR Settings</Typography>
            <Typography variant="body1">Select SDR:</Typography>
            <Select value={sdr} onChange={handleSdrChange} fullWidth>
              <MenuItem value="hackrf">HackRF</MenuItem>
              <MenuItem value="sidekiq">Sidekiq</MenuItem>
            </Select>
            <SDRSettings 
                settings={localSettings} 
                handleChange={handleChange} 
                handleKeyPress={handleKeyPress} 
                setSettings={setSettings}
              />
          </>
        )}
        {tabIndex === 1 && (
          <>
            <PlotSettings
              settings={localSettings}
              setSettings={setLocalSettings}
              handleSliderChange={handleSliderChange}
              handleSliderChangeCommitted={handleSliderChangeCommitted}
              handleChange={handleChange}
              minY={minY}
              setMinY={setMinY}
              maxY={maxY}
              setMaxY={setMaxY}
            />
            <WaterfallSettings
              settings={localSettings}
              showWaterfall={showWaterfall}
              setShowWaterfall={setShowWaterfall}
            />
          </>
        )}
        {tabIndex === 2 && (
          <Analysis 
          settings={localSettings}
          setSettings={setLocalSettings}
          addVerticalLines={addVerticalLines} // Ensure vertical lines are added correctly
          clearVerticalLines={clearVerticalLines}
          addHorizontalLines={addHorizontalLines}  
          clearHorizontalLines={clearHorizontalLines}  
          />
        )}
        {tabIndex === 3 && (
          <Classifiers 
            settings={localSettings}
            setSettings={setLocalSettings}
            addVerticalLines={addVerticalLines} // Ensure vertical lines are added correctly
            clearVerticalLines={clearVerticalLines}
            addHorizontalLines={addHorizontalLines}  
            clearHorizontalLines={clearHorizontalLines}  
          />
        )}
      </Box>
    </Box>
  );
};

export default ControlPanel;
