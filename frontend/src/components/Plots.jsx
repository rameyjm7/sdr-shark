import React, { useState, useEffect } from 'react';
import { Box } from '@mui/material';
import axios from 'axios';
import ChartComponent from './ChartComponent';
import '../App.css';

const Plots = ({ settings, updateInterval, showSecondTrace, showWaterfall, minY, maxY, setMinY, setMaxY, addVerticalLines, verticalLines, addHorizontalLines, horizontalLines }) => {
  const [sweepSettings, setSweepSettings] = useState({
    frequency_start: 100,
    frequency_stop: 200,
    sweeping_enabled: false,
    bandwidth: 16,
  });

  useEffect(() => {
    fetchInitialSettings();
    const interval = setInterval(fetchSettings, 5000);
    return () => clearInterval(interval);
  }, []);

  const fetchInitialSettings = async () => {
    try {
      const response = await axios.get('/api/get_settings');
      const data = response.data;

      setSweepSettings({
        frequency_start: data.frequency_start,
        frequency_stop: data.frequency_stop,
        sweeping_enabled: data.sweeping_enabled,
        bandwidth: data.sweeping_enabled ? data.frequency_stop - data.frequency_start : data.bandwidth,
      });
    } catch (error) {
      console.error('Error fetching initial settings:', error);
    }
  };

  const fetchSettings = async () => {
    try {
      const response = await axios.get('/api/get_settings');
      const data = response.data;
      setSweepSettings({
        frequency_start: data.frequency_start,
        frequency_stop: data.frequency_stop,
        sweeping_enabled: data.sweeping_enabled,
        bandwidth: data.sweeping_enabled ? data.frequency_stop - data.frequency_start : data.bandwidth,
      });
    } catch (error) {
      console.error('Error fetching settings:', error);
    }
  };

  return (
    <div className="plots_container">
      <Box className="plots">
        <ChartComponent
          settings={settings}
          sweepSettings={sweepSettings}
          setSweepSettings={setSweepSettings}
          minY={minY}
          maxY={maxY}
          updateInterval={updateInterval}
          showWaterfall={showWaterfall}
          showSecondTrace={showSecondTrace}
          verticalLines={verticalLines}  // Pass verticalLines to ChartComponent
          horizontalLines={horizontalLines}
        />
      </Box>
    </div>
  );
};

export default Plots;
