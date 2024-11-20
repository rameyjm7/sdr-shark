import React, { useState } from 'react';
import { Box, Typography, Slider, FormControlLabel, Switch, Checkbox } from '@mui/material';

const PlotSettings = ({
  settings,
  setSettings,
  setUpdateInterval,
  handleSliderChange,
  handleSliderChangeCommitted,
  handleChange,
  minY,
  maxY,
  setMinY,
  setMaxY,
}) => {
  const averagingCount = settings.averagingCount || 10;
  const numTicks = settings.numTicks || 5;
  const dcSuppress = settings.dcSuppress || false;
  const showMaxTrace = settings.showMaxTrace || false;
  const showPersistanceTrace = settings.showPersistanceTrace || false; // Corrected the spelling

  const [lockYAxisRange, setLockYAxisRange] = useState(true); // Locked by default

  const handleMinYChange = (e, value) => {
    setMinY(value);
    if (lockYAxisRange) {
      setMaxY(value + 60); // Ensure the difference stays the same
    }
  };

  const handleMaxYChange = (e, value) => {
    setMaxY(value);
    if (lockYAxisRange) {
      setMinY(value - 60); // Ensure the difference stays the same
    }
  };

  const handleCheckboxChange = (e) => {
    console.log(e.target.name);
    setSettings(prevSettings => ({
      ...prevSettings,
      [e.target.name]: e.target.checked,
    }));
  };

  return (
    <Box>
      <Typography variant="h6" sx={{ mt: 2 }}>Plot Settings</Typography>

      <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Box sx={{ flex: 1, mr: 2 }}>
          <Typography gutterBottom>Averaging Count: {averagingCount}</Typography>
          <Slider
            min={1}
            max={100}
            value={averagingCount}
            onChange={(e, value) => handleSliderChange(e, value, 'averagingCount')}
            onChangeCommitted={(e, value) => handleSliderChangeCommitted(e, value, 'averagingCount')}
            valueLabelDisplay="auto"
            step={1}
            marks={[
              { value: 1, label: '1' },
              { value: 50, label: '50' },
              { value: 100, label: '100' },
            ]}
          />
        </Box>

        <Box sx={{ flex: 1, mr: 2 }}>
          <Typography gutterBottom>Number of X-Axis Ticks: {numTicks}</Typography>
          <Slider
            min={2}
            max={20}
            value={numTicks}
            onChange={(e, value) => handleSliderChange(e, value, 'numTicks')}
            onChangeCommitted={(e, value) => handleSliderChangeCommitted(e, value, 'numTicks')}
            valueLabelDisplay="auto"
            step={1}
            marks={[
              { value: 2, label: '2' },
              { value: 10, label: '10' },
              { value: 20, label: '20' },
            ]}
          />
        </Box>

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

      {/* Checkbox for enabling/disabling Max Trace */}
      <FormControlLabel
        control={
          <Checkbox
            checked={showMaxTrace}
            onChange={handleCheckboxChange}
            name="showMaxTrace"
            color="primary"
          />
        }
        label="Show Max Trace"
      />


      {/* Checkbox for enabling/disabling Persistance Trace */}
      <FormControlLabel
        control={
          <Checkbox
            checked={showPersistanceTrace} // Corrected the spelling
            onChange={handleCheckboxChange}
            name="showPersistanceTrace" // Corrected the spelling
            color="primary"
          />
        }
        label="Show Persistance Trace"
      />

      <Box sx={{ flex: 1, mr: 2 }}>
        <Typography gutterBottom>Update Interval (ms): {settings.updateInterval}</Typography>
        <Slider
          min={10}
          max={1000}
          value={settings.updateInterval}
          onChange={(e, value) => {
            setUpdateInterval(value); // Update the independent updateInterval state
            setSettings((prevSettings) => ({
              ...prevSettings,
              updateInterval: value, // Update updateInterval in the settings state
            }));
            console.log("interval set to " + value);
          }}
          
          valueLabelDisplay="auto"
          step={10}
          marks={[
            { value: 10, label: '10 ms' },
            { value: 500, label: '500 ms' },
            { value: 1000, label: '1000 ms' },
          ]}
        />
      </Box>

      {/* Y Axis Limits Sub-Panel */}
      <Box
        sx={{
          backgroundColor: 'inherit', // Same background color as the rest of the panel
          padding: 3, // Padding for spacing
          borderRadius: 2, // Rounded edges
          border: '2px solid white', // White border for distinction
          mt: 3,
        }}
      >
        <Typography variant="h6" sx={{ mb: 2 }}>Y Axis Limits</Typography>

        <FormControlLabel
          control={
            <Checkbox
              checked={lockYAxisRange}
              onChange={() => setLockYAxisRange(!lockYAxisRange)}
              color="primary"
            />
          }
          label="Lock Y-Axis Ranges"
        />

        <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mt: 2 }}>
          <Box sx={{ flex: 1, mr: 2 }}>
            <Typography gutterBottom>Min Y-Axis Range: {minY.toFixed(2)} dB</Typography>
            <Slider
              min={-60}
              max={0}
              value={minY}
              onChange={handleMinYChange}
              valueLabelDisplay="auto"
              step={1}
              marks={[
                { value: -60, label: '-60 dB' },
                { value: 0, label: '0 dB' },
              ]}
            />
          </Box>
          <Box sx={{ flex: 1, ml: 2 }}>
            <Typography gutterBottom>Max Y-Axis Range: {maxY.toFixed(2)} dB</Typography>
            <Slider
              min={-60}
              max={60}
              value={maxY}
              onChange={handleMaxYChange}
              valueLabelDisplay="auto"
              step={1}
              marks={[
                { value: -60, label: '-60 dB' },
                { value: 60, label: '60 dB' },
              ]}
            />
          </Box>
        </Box>
      </Box>

    </Box>
  );
};

export default PlotSettings;
