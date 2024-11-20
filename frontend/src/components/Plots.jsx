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
    console.log('Plots.js: horizontalLines passed to Plots:', horizontalLines);
  }, [horizontalLines]);

  useEffect(() => {
    console.log('Plots.js: verticalLines passed to Plots:', verticalLines);
  }, [verticalLines]);

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

  const updateSettings = async (newSettings) => {
    try {
      await axios.post('/api/update_settings', newSettings);
    } catch (error) {
      console.error('Error updating settings:', error);
    }
  };


  // Handle adding horizontal lines
  useEffect(() => {
    if (addHorizontalLines) {
      addHorizontalLines((power) => {
        addHorizontalLines((prevLines) => [...prevLines, { power: power }]);
        console.log(`Horizontal lines added at ${power} dB`);
      });
    }
  }, [addHorizontalLines]);

  // Handle adding vertical lines
  useEffect(() => {
    if (addVerticalLines) {
      addVerticalLines((frequency, bandwidth) => {
        const lowerBound = frequency - bandwidth / 2;
        const upperBound = frequency + bandwidth / 2;
        setVerticalLines((prevLines) => [...prevLines, { frequency: lowerBound }, { frequency: upperBound }]);
        console.log(`Vertical lines added at ${frequency} MHz ± ${bandwidth / 2} MHz`);
      });
    }
  }, [addVerticalLines]);

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
