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
  const toFinite = (value, fallback) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };
  const averagingCount = toFinite(settings.averagingCount, 10);
  const numTicks = toFinite(settings.numTicks, 5);
  const dcSuppress = typeof settings.dcSuppress === 'boolean' ? settings.dcSuppress : false;
  const showMaxTrace = typeof settings.showMaxTrace === 'boolean' ? settings.showMaxTrace : false;
  const showPersistanceTrace = typeof settings.showPersistanceTrace === 'boolean' ? settings.showPersistanceTrace : false;

  const [lockYAxisRange, setLockYAxisRange] = useState(true); // Locked by default

  const sliderSx = {
    mt: 0.5,
    '& .MuiSlider-thumb': { width: 18, height: 18 },
    '& .MuiSlider-rail': { opacity: 0.35 },
  };

  const rangeHint = (left, middle, right) => (
    <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 0.25, px: 0.5 }}>
      <Typography variant="caption" color="text.secondary">{left}</Typography>
      <Typography variant="caption" color="text.secondary">{middle}</Typography>
      <Typography variant="caption" color="text.secondary">{right}</Typography>
    </Box>
  );

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
    setSettings(prevSettings => ({
      ...prevSettings,
      [e.target.name]: e.target.checked,
    }));
  };

  return (
    <Box>
      <Typography variant="h6" sx={{ mt: 1, mb: 1 }}>Plot Settings</Typography>

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
          gap: 2,
          alignItems: 'start',
        }}
      >
        <Box>
          <Typography variant="body2" gutterBottom>Averaging Count: {averagingCount}</Typography>
          <Slider
            min={1}
            max={100}
            value={averagingCount}
            onChange={(e, value) => handleSliderChange(e, value, 'averagingCount')}
            onChangeCommitted={(e, value) => handleSliderChangeCommitted(e, value, 'averagingCount')}
            valueLabelDisplay="auto"
            step={1}
            sx={sliderSx}
          />
          {rangeHint('1', '50', '100')}
        </Box>

        <Box>
          <Typography variant="body2" gutterBottom>Number of X-Axis Ticks: {numTicks}</Typography>
          <Slider
            min={2}
            max={20}
            value={numTicks}
            onChange={(e, value) => handleSliderChange(e, value, 'numTicks')}
            onChangeCommitted={(e, value) => handleSliderChangeCommitted(e, value, 'numTicks')}
            valueLabelDisplay="auto"
            step={1}
            sx={sliderSx}
          />
          {rangeHint('2', '10', '20')}
        </Box>
      </Box>

      <Box sx={{ display: 'flex', flexWrap: 'wrap', columnGap: 2, rowGap: 0.5, mt: 0.5 }}>
        <FormControlLabel
          sx={{ m: 0 }}
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
        <FormControlLabel
          sx={{ m: 0 }}
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
        <FormControlLabel
          sx={{ m: 0 }}
          control={
            <Checkbox
              checked={showPersistanceTrace}
              onChange={handleCheckboxChange}
              name="showPersistanceTrace"
              color="primary"
            />
          }
          label="Show Persistence Trace"
        />
      </Box>

      <Box sx={{ mt: 1.5 }}>
        <Typography variant="body2" gutterBottom>Update Interval (ms): {toFinite(settings.updateInterval, 500)}</Typography>
        <Slider
          min={10}
          max={1000}
          value={toFinite(settings.updateInterval, 500)}
          onChange={(e, value) => {
            const safeValue = Array.isArray(value) ? value[0] : value;
            if (!Number.isFinite(safeValue)) return;
            setUpdateInterval(value); // Update the independent updateInterval state
            setSettings((prevSettings) => ({
              ...prevSettings,
              updateInterval: safeValue, // Update updateInterval in the settings state
            }));
          }}
          valueLabelDisplay="auto"
          step={10}
          sx={sliderSx}
        />
        {rangeHint('10 ms', '500 ms', '1000 ms')}
      </Box>

      <Box
        sx={{
          backgroundColor: 'inherit',
          p: 2,
          borderRadius: 2,
          border: '1px solid #3a3a3a',
          mt: 2,
        }}
      >
        <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 1 }}>
          <Typography variant="h6">Y Axis Limits</Typography>
          <FormControlLabel
            sx={{ m: 0 }}
            control={
              <Checkbox
                checked={lockYAxisRange}
                onChange={() => setLockYAxisRange(!lockYAxisRange)}
                color="primary"
              />
            }
            label="Lock Y-Axis Ranges"
          />
        </Box>

        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
            gap: 2,
            alignItems: 'start',
          }}
        >
          <Box>
            <Typography variant="body2" gutterBottom>Min Y-Axis: {minY.toFixed(2)} dB</Typography>
            <Slider
              min={-60}
              max={0}
              value={minY}
              onChange={handleMinYChange}
              valueLabelDisplay="auto"
              step={1}
              sx={sliderSx}
            />
            {rangeHint('-60 dB', '', '0 dB')}
          </Box>
          <Box>
            <Typography variant="body2" gutterBottom>Max Y-Axis: {maxY.toFixed(2)} dB</Typography>
            <Slider
              min={-60}
              max={60}
              value={maxY}
              onChange={handleMaxYChange}
              valueLabelDisplay="auto"
              step={1}
              sx={sliderSx}
            />
            {rangeHint('-60 dB', '', '60 dB')}
          </Box>
        </Box>
      </Box>

    </Box>
  );
};

export default PlotSettings;
