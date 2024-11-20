import React from 'react';
import { Box, Typography, Slider, FormControlLabel, Switch } from '@mui/material';

const WaterfallSettings = ({ settings, showWaterfall, setShowWaterfall }) => {
  return (
    <Box>
      <Typography variant="h6" sx={{ mt: 2 }}>Waterfall Settings</Typography>

      <Box display="flex" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Box sx={{ flex: 1, mr: 2 }}>
          <Typography gutterBottom>Update Interval (ms): {settings.updateInterval}</Typography>
          <Slider
            min={10}
            max={1000}
            value={settings.updateInterval}
            onChange={(e, value) =>
              setShowWaterfall({ ...settings, updateInterval: value }
              )}
            valueLabelDisplay="auto"
            step={10}
            marks={[
              { value: 10, label: '10 ms' },
              { value: 500, label: '500 ms' },
              { value: 1000, label: '1000 ms' },
            ]}
          />
        </Box>

        <Box sx={{ flex: 1, ml: 2 }}>
          <Typography gutterBottom>Waterfall Samples: {settings.waterfallSamples}</Typography>
          <Slider
            min={25}
            max={1000}
            value={settings.waterfallSamples}
            onChange={(e, value) => setShowWaterfall({ ...settings, waterfallSamples: value })}
            valueLabelDisplay="auto"
            step={25}
            marks={[
              { value: 25, label: '25' },
              { value: 500, label: '500' },
              { value: 1000, label: '1000' },
            ]}
          />
        </Box>
      </Box>

      <FormControlLabel
        control={
          <Switch
            checked={showWaterfall}
            onChange={() => {
              setShowWaterfall(!showWaterfall);
              const newSettings = { ...settings, showWaterfall: !showWaterfall };
              setShowWaterfall(newSettings);
            }}
            name="showWaterfall"
            color="primary"
          />
        }
        label="Enable Waterfall"
      />
    </Box>
  );
};

export default WaterfallSettings;
