// Author: Jacob M. Ramey

import React, { useMemo, useState, useEffect } from 'react';
import { Typography, CssBaseline, Tabs, Tab, Box, Chip } from '@mui/material';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import Split from 'split.js';
import ControlPanel from './components/ControlPanel';
import Scanner from './components/Scanner';
import Plots from './components/Plots';
import Analysis from './components/Analysis';
import Classifiers from './components/ControlPanel/Classifiers';
import MiniSpectrum from './components/MiniSpectrum';
import axios from 'axios';
import './App.css';

const theme = createTheme({
  palette: {
    mode: 'dark',
    background: {
      default: '#000',
      paper: '#121212',
    },
    text: {
      primary: '#fff',
    },
    primary: {
      main: '#90caf9',
    },
    secondary: {
      main: '#f48fb1',
    },
  },
});

function TabPanel(props) {
  const { children, value, index, ...other } = props;

  return (
    <div
      role="tabpanel"
      hidden={value !== index}
      id={`tabpanel-${index}`}
      aria-labelledby={`tab-${index}`}
      {...other}
      style={{ height: '100%', minHeight: 0, overflow: 'hidden' }}
    >
      {value === index && (
        <Box sx={{ p: 1, height: '100%', minHeight: 0, overflow: 'hidden' }}>
          {children}
        </Box>
      )}
    </div>
  );
}

const App = () => {
  const [settings, setSettings] = useState({
    frequency: 0,
    gain: 10,
    sampleRate: 1,
    bandwidth: 1,
    averagingCount: 10,
    dcSuppress: true,
    peakDetection: true,
    minPeakDistance: 0.1,
    numberOfPeaks: 5,
    showWaterfall: true,
    waterfallSamples: 200,
    waterfallBinCount: 2048,
    updateInterval: 500
  });
  const [showSecondTrace, setShowSecondTrace] = useState(false);
  const [minY, setMinY] = useState(-120);
  const [maxY, setMaxY] = useState(0);
  // const [waterfallSamples, setWaterfallSamples] = useState(100);
  const [showWaterfall, setShowWaterfall] = useState(true);
  const [tabValue, setTabValue] = useState(0);
  const [files, setFiles] = useState([]);
  const [currentPath, setCurrentPath] = useState('/');
  const [metadata, setMetadata] = useState(null);
  const [fftData, setFftData] = useState([]);
  const [plotWidth, setPlotWidth] = useState(60); // Initial plot width in percentage
  const [verticalLines, setVerticalLines] = useState([]);  // State for vertical lines
  const [horizontalLines, setHorizontalLines] = useState([]);  // State for horizontal lines
  const [telemetry, setTelemetry] = useState({
    sdr: 'n/a',
    hzPerBin: 0,
    frameTime: '',
    fps: 0,
    latencyMs: 0,
    droppedFrames: 0,
    staleMs: 0,
    sweepEnabled: false,
    mainFrameSeq: 0,
    scannerFrameSeq: 0,
    scannerFresh: false,
    fftError: null,
    scannerError: null,
    waterfallRows: 0,
    peaks: [],
  });


  const setUpdateInterval = (interval) => {
    setSettings(prevSettings => ({
      ...prevSettings,
      updateInterval: interval
    }));
  };

  useEffect(() => {
    if (typeof settings.showWaterfall === 'boolean') {
      setShowWaterfall(settings.showWaterfall);
    }
  }, [settings.showWaterfall]);


  const addVerticalLines = (frequency, bandwidth) => {
    if (typeof frequency == "string") {
      frequency = parseFloat(frequency);
    }
    if (typeof bandwidth == "string") {
      bandwidth = parseFloat(bandwidth);
    }
    // Check if frequency and bandwidth are numbers
    if (typeof frequency !== 'number' || typeof bandwidth !== 'number') {
      return;
    }

    // Calculate lower and upper bounds
    const lowerBound = frequency - bandwidth / 2;
    const upperBound = frequency + bandwidth / 2;

    // Check if the calculated bounds are numbers
    if (isNaN(lowerBound) || isNaN(upperBound)) {
      console.error('Calculated bounds are NaN:', { lowerBound, upperBound });
      return;
    }

    setVerticalLines((prevLines) => [
      ...prevLines,
      { frequency: lowerBound, label: `${lowerBound.toFixed(2)} MHz` },
      { frequency: upperBound, label: `${upperBound.toFixed(2)} MHz` },
    ]);
    sendMarkersToBackend(verticalLines, horizontalLines); // Send to backend

  };

  const clearVerticalLines = () => {
    setVerticalLines((prevLines) => []);
    sendMarkersToBackend(verticalLines, horizontalLines); // Send to backend
  };

  const addHorizontalLines = (power) => {
    // Check if power is a number
    if (typeof power !== 'number') {
      return;
    }

    setHorizontalLines((prevLines) => [
      ...prevLines,
      { power: power, label: `${power.toFixed(2)} dB` },
    ]);
    sendMarkersToBackend(verticalLines, horizontalLines); // Send to backend
  };

  const clearHorizontalLines = () => {
    setHorizontalLines((prevLines) => []);
    sendMarkersToBackend(verticalLines, []); // Send to backend
  };


  const sendMarkersToBackend = (verticalLines, horizontalLines) => {
    // Prepare data for backend
    const markerData = {
      vertical_lines: verticalLines,
      horizontal_lines: horizontalLines,
    };

    // Make a POST request to the backend
    axios.post('/api/signal_detection', markerData)
      .then(response => {
      })
      .catch(error => {
        console.error('Error sending markers to backend:', error);
      });
  };

  const handleTabChange = (event, newValue) => {
    setTabValue(newValue);
  };

  const handleAnalyze = (file) => {
    const relativePath = currentPath + file.name;

    axios.get(`/api/file_manager/files/metadata`, {
      params: {
        path: file.name,
        current_dir: currentPath,
      }
    })
      .then(response => {
        setMetadata(response.data.metadata);
        setFftData(response.data.fft_data);
        setTabValue(2);  // Switch to Analysis tab
      })
      .catch(error => {
        console.error('Error analyzing file:', error);
      });
  };

  useEffect(() => {
    const adjustPlotWidth = () => {
      const leftPanelWidth = document.getElementById('leftPanel').clientWidth;
      const totalWidth = document.getElementById('plotsContainer').clientWidth;
      const newPlotWidth = (leftPanelWidth / totalWidth) * 100;

      setPlotWidth(newPlotWidth);
    };

    const splitInstance = Split(['#leftPanel', '#rightPanel'], {
      sizes: [74, 26], // Keep the right panel narrower by default.
      minSize: 100,    // Allow more shrinking of panels
      gutterSize: 10,  // Size of the gutter (resize handle)
      cursor: 'col-resize',
      onDrag: adjustPlotWidth,
    });

    adjustPlotWidth(); // Adjust the width on initial load

    window.addEventListener('resize', adjustPlotWidth);

    return () => {
      splitInstance.destroy();
      window.removeEventListener('resize', adjustPlotWidth);
    };
  }, []);

  const telemetryChipSx = {
    width: 132,
    minWidth: 132,
    maxWidth: 132,
    flex: '0 0 132px',
    justifyContent: 'center',
    '& .MuiChip-label': {
      width: '100%',
      px: 1,
      textAlign: 'center',
      whiteSpace: 'nowrap',
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      fontVariantNumeric: 'tabular-nums',
      fontFeatureSettings: '"tnum"',
      fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
    },
  };
  const telemetryWideChipSx = { ...telemetryChipSx, width: 170, minWidth: 170, maxWidth: 170, flex: '0 0 170px' };
  const telemetryDetectChipSx = { ...telemetryChipSx, width: 280, minWidth: 280, maxWidth: 280, flex: '0 0 280px' };

  const detectedSignalLabel = useMemo(() => {
    const normalizeMHz = (value) => {
      const n = Number(value);
      if (!Number.isFinite(n)) return NaN;
      return Math.abs(n) > 1e6 ? (n / 1e6) : n;
    };
    const peaks = Array.isArray(telemetry.peaks) ? telemetry.peaks : [];
    if (!peaks.length) return 'Detected: none';

    const strongest = [...peaks].sort(
      (a, b) => Number(b?.peak_power || -999) - Number(a?.peak_power || -999),
    )[0];
    const absCenterMHz = normalizeMHz(strongest?.absolute_frequency);
    const relCenterMHz = normalizeMHz(strongest?.frequency);
    const centerMHz = Number.isFinite(absCenterMHz)
      ? absCenterMHz
      : (Number.isFinite(relCenterMHz) ? relCenterMHz + Number(settings.frequency || 0) : NaN);
    const bwMHz = normalizeMHz(strongest?.bandwidth);
    if (!Number.isFinite(centerMHz) || !Number.isFinite(bwMHz)) return 'Detected: unknown';

    const classes = Array.isArray(strongest?.classification) ? strongest.classification : [];
    if (classes.length) {
      const top = classes[0] || {};
      const label = String(top.label || 'Signal');
      const channel = String(top.channel || '').trim();
      const channelText = channel && channel !== 'N/A' ? ` ${channel}` : '';
      return `Detected: ${label}${channelText} (${centerMHz.toFixed(2)} MHz, ${bwMHz.toFixed(2)} MHz)`;
    }

    if (Math.abs(centerMHz - 2402) <= 3 && bwMHz >= 8 && bwMHz <= 14) {
      return `Detected: WiFi Ch1 (${centerMHz.toFixed(2)} MHz, ${bwMHz.toFixed(2)} MHz)`;
    }
    return `Detected: ${centerMHz.toFixed(2)} MHz / ${bwMHz.toFixed(2)} MHz`;
  }, [telemetry.peaks]);

  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <Box
        id="plotsContainer"
        sx={{
          p: 0,
          m: 0,
          width: '100%',
          height: '100vh',
          display: 'flex',
          flexDirection: 'column',
          overflow: 'hidden',
        }}
      >

        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            padding: '0 16px',
            width: '100%',
          }}
        >
          {/* Tabs on the left */}
          <Tabs
            value={tabValue}
            onChange={handleTabChange}
            variant="scrollable"
            scrollButtons="auto"
            sx={{
              flexGrow: 1, // Ensure tabs take up available space
            }}
          >
            <Tab label="MAIN" />
            <Tab label="SCANNER" />
            <Tab label="ANALYSIS" />
            <Tab label="CLASSIFIERS" />
            <Tab label="ABOUT" />
          </Tabs>

          {/* SDR Shark text and icon on the right */}
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              marginLeft: 'auto', // Push to the right
            }}
          >
            <Typography variant="h6" sx={{ marginRight: '10px' }}>
              SDR Shark
            </Typography>
            <img
              src="shark_icon.png"
              alt="Shark Icon"
              style={{ width: '30px', height: '30px' }}
            />
          </Box>
        </Box>

        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            alignContent: 'flex-start',
            flexWrap: 'wrap',
            gap: 1,
            px: 1.5,
            py: 0.75,
            borderTop: '1px solid #222',
            borderBottom: '1px solid #222',
            bgcolor: '#0b0b0b',
            overflowX: 'hidden',
            overflowY: 'auto',
            maxHeight: 76,
          }}
        >
          <Chip size="small" sx={telemetryWideChipSx} label={`SDR: ${telemetry.sdr || 'n/a'}`} />
          <Chip size="small" sx={telemetryWideChipSx} label={`Hz/bin: ${Number.isFinite(telemetry.hzPerBin) ? Math.round(telemetry.hzPerBin).toLocaleString() : 'n/a'}`} />
          <Chip size="small" sx={telemetryChipSx} label={`FPS: ${Number.isFinite(telemetry.fps) ? telemetry.fps.toFixed(1) : '0.0'}`} />
          <Chip size="small" sx={telemetryChipSx} label={`Latency: ${Math.round(telemetry.latencyMs || 0)} ms`} />
          <Chip size="small" sx={telemetryChipSx} color={telemetry.droppedFrames > 0 ? 'warning' : 'default'} label={`Drops: ${telemetry.droppedFrames || 0}`} />
          <Chip size="small" sx={telemetryWideChipSx} color={(telemetry.staleMs || 0) > 3000 ? 'error' : 'default'} label={`Last data age: ${Math.round(telemetry.staleMs || 0)} ms`} />
          <Chip size="small" sx={telemetryWideChipSx} label={`Time: ${telemetry.frameTime || 'n/a'}`} />
          <Chip size="small" sx={telemetryChipSx} label={`Sweep: ${telemetry.sweepEnabled ? 'On' : 'Off'}`} />
          <Chip size="small" sx={telemetryChipSx} label={`Main seq: ${telemetry.mainFrameSeq || 0}`} />
          <Chip size="small" sx={telemetryChipSx} label={`Scanner seq: ${telemetry.scannerFrameSeq || 0}`} />
          <Chip size="small" sx={telemetryWideChipSx} color={telemetry.scannerFresh ? 'success' : 'default'} label={`Scanner fresh: ${telemetry.scannerFresh ? 'yes' : 'no'}`} />
          <Chip size="small" sx={telemetryChipSx} label={`WF rows: ${telemetry.waterfallRows || 0}`} />
          <Chip size="small" sx={telemetryDetectChipSx} color={detectedSignalLabel.includes('WiFi Ch1') ? 'success' : 'default'} label={detectedSignalLabel} />
          {telemetry.fftError ? <Chip size="small" sx={telemetryChipSx} color="error" label={`FFT err`} /> : null}
          {telemetry.scannerError ? <Chip size="small" sx={telemetryChipSx} color="error" label={`Scanner err`} /> : null}
        </Box>


        <Box sx={{ display: 'flex', flex: 1, minHeight: 0, overflow: 'hidden' }}>
          <Box
            id="leftPanel"
            sx={{
              pr: '10px',
              borderRight: '2px solid #444',
              height: '100%',
              minHeight: 0,
              overflow: 'hidden',
              flex: '0 1 auto',
            }}
          >
            <TabPanel value={tabValue} index={0}>
              <Plots
                settings={settings}
                setSettings={setSettings}
                minY={minY}
                maxY={maxY}
                setMinY={setMinY}
                setMaxY={setMaxY}
                // updateInterval={updateInterval}
                // waterfallSamples={waterfallSamples}
                showWaterfall={showWaterfall}
                showSecondTrace={showSecondTrace}
                plotWidth={plotWidth}
                addVerticalLines={addVerticalLines}
                verticalLines={verticalLines}
                addHorizontalLines={addHorizontalLines}
                horizontalLines={horizontalLines}
                onTelemetryUpdate={setTelemetry}
              />
            </TabPanel>
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', minHeight: 0, textAlign: 'center', overflow: 'hidden' }}>
            <TabPanel
              value={tabValue}
              index={4}
              style={{
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                textAlign: 'center',
                height: '100%',
                padding: '20px'
              }}
            >
              <Typography variant="h6" sx={{ marginBottom: '20px' }}>
                About SDR Shark
              </Typography>

              <img
                src="shark_icon.png"
                alt="Shark Icon"
                style={{ width: '150px', height: '150px', marginBottom: '20px' }}
              />

              <Typography variant="body1" style={{ marginBottom: '10px' }}>
                Author: Jacob M. Ramey
              </Typography>
              <Typography variant="body1" style={{ marginBottom: '10px' }}>
                Github Repo: <a href="https://github.com/rameyjm7/SDR-Shark" target="_blank" rel="noopener noreferrer">https://github.com/rameyjm7/SDR-Shark</a>
              </Typography>
              <Typography variant="body1" style={{ marginBottom: '10px' }}>
                Github: <a href="https://github.com/rameyjm7" target="_blank" rel="noopener noreferrer">https://github.com/rameyjm7</a>
              </Typography>
              <Typography variant="body1" style={{ marginBottom: '10px' }}>
                LinkedIn: <a href="https://www.linkedin.com/in/rameyjm/" target="_blank" rel="noopener noreferrer">https://www.linkedin.com/in/rameyjm/</a>
              </Typography>
              <Typography variant="body1" style={{ marginBottom: '10px' }}>
                License: <a href="https://github.com/rameyjm7/SDR-Shark/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">View License</a>
              </Typography>
              <Typography variant="body2" style={{ marginTop: '20px' }}>
                Copyright (c) 2024 Jacob M. Ramey
              </Typography>
            </TabPanel>

            <TabPanel
              value={tabValue}
              index={1}
              style={{
                display: 'flex',
                flexDirection: 'column',
                justifyContent: 'flex-start', // Start content at the top
                alignItems: 'stretch', // Stretch content to full width
                height: '100%', // Occupy full height of the tab
                width: '100%', // Occupy full width of the tab
                padding: '0', // Remove extra padding
                boxSizing: 'border-box',
                overflow: 'hidden', // Prevent unwanted scrollbars
              }}
            >
              <Scanner settings={settings} setSettings={setSettings} />
            </TabPanel>
            <TabPanel value={tabValue} index={2}>
              <Analysis
                settings={settings}
                setSettings={setSettings}
                addVerticalLines={addVerticalLines}
                clearVerticalLines={clearVerticalLines}
                addHorizontalLines={addHorizontalLines}
                clearHorizontalLines={clearHorizontalLines}
              />
            </TabPanel>
            <TabPanel value={tabValue} index={3}>
              <Classifiers
                settings={settings}
                setSettings={setSettings}
                addVerticalLines={addVerticalLines}
                clearVerticalLines={clearVerticalLines}
                addHorizontalLines={addHorizontalLines}
                clearHorizontalLines={clearHorizontalLines}
              />
            </TabPanel>


            </Box>
          </Box>
          <Box
            id="rightPanel"
            sx={{
              pl: '10px',
              height: '100%',
              minHeight: 0,
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column',
              flex: '0 1 auto',
            }}
          >
            {tabValue !== 0 && (
              <MiniSpectrum
                settings={settings}
                minY={minY}
                maxY={maxY}
                verticalLines={verticalLines}
                horizontalLines={horizontalLines}
              />
            )}
            <Box sx={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
              <ControlPanel
                settings={settings}
                setSettings={setSettings}
                minY={minY}
                setMinY={setMinY}
                maxY={maxY}
                setMaxY={setMaxY}
                // updateInterval={updateInterval}
                setUpdateInterval={setUpdateInterval}
                // waterfallSamples={waterfallSamples}
                // setWaterfallSamples={setWaterfallSamples}
                showWaterfall={showWaterfall}
                setShowWaterfall={setShowWaterfall}
                addVerticalLines={addVerticalLines}
                clearVerticalLines={clearVerticalLines}
                addHorizontalLines={addHorizontalLines}
                clearHorizontalLines={clearHorizontalLines}
                verticalLines={verticalLines}
              />
            </Box>
          </Box>
        </Box>

      </Box>
    </ThemeProvider>
  );
};

export default App;
