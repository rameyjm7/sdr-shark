// Author: Jacob M. Ramey

import React, { useMemo, useState, useEffect } from 'react';
import { Typography, CssBaseline, Box, Chip, Button, Dialog, DialogContent, DialogTitle, IconButton } from '@mui/material';
import { createTheme, ThemeProvider } from '@mui/material/styles';
import CloseIcon from '@mui/icons-material/Close';
import GpsFixedIcon from '@mui/icons-material/GpsFixed';
import GpsNotFixedIcon from '@mui/icons-material/GpsNotFixed';
import SettingsIcon from '@mui/icons-material/Settings';
import Split from 'split.js';
import ControlPanel from './components/ControlPanel';
import GpsDialog from './components/GpsDialog';
import Scanner from './components/Scanner';
import Plots from './components/Plots';
import Analysis from './components/Analysis';
import Classifiers from './components/ControlPanel/Classifiers';
import DecodedEventsPanel from './components/DecodedEventsPanel';
import axios from 'axios';
import './App.css';

const ACTIVITY_LOG_RETENTION_STORAGE_KEY = 'sdrshark_activity_log_retention_sec_v1';
const LAST_SDR_STORAGE_KEY = 'sdrshark_last_selected_sdr_v1';

const initialActivityLogRetentionSec = () => {
  const saved = Number(localStorage.getItem(ACTIVITY_LOG_RETENTION_STORAGE_KEY));
  return Number.isFinite(saved) ? Math.max(60, Math.min(3600, saved)) : 600;
};

const readLastSdr = () => {
  try {
    return localStorage.getItem(LAST_SDR_STORAGE_KEY) || '';
  } catch (error) {
    return '';
  }
};

const writeLastSdr = (sdrId) => {
  try {
    if (sdrId) localStorage.setItem(LAST_SDR_STORAGE_KEY, sdrId);
  } catch (error) {
    // Non-fatal: storage may be disabled.
  }
};

const toFinite = (value, fallback) => {
  const n = Number(value);
  return Number.isFinite(n) ? n : fallback;
};

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
    activityLogRetentionSec: initialActivityLogRetentionSec(),
    updateInterval: 500
  });
  const [showSecondTrace, setShowSecondTrace] = useState(false);
  const [minY, setMinY] = useState(-120);
  const [maxY, setMaxY] = useState(0);
  // const [waterfallSamples, setWaterfallSamples] = useState(100);
  const [showWaterfall, setShowWaterfall] = useState(true);
  const [modalView, setModalView] = useState(null);
  const [plotWidth, setPlotWidth] = useState(60); // Initial plot width in percentage
  const [verticalLines, setVerticalLines] = useState([]);  // State for vertical lines
  const [horizontalLines, setHorizontalLines] = useState([]);  // State for horizontal lines
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [gpsOpen, setGpsOpen] = useState(false);
  const [telemetry, setTelemetry] = useState({
    sdr: 'n/a',
    hzPerBin: 0,
    frameTime: '',
    fps: 0,
    latencyMs: 0,
    droppedFrames: 0,
    staleMs: 0,
    sweepEnabled: false,
    scannerMode: null,
    mainFrameSeq: 0,
    scannerFrameSeq: 0,
    scannerFresh: false,
    fftError: null,
    scannerError: null,
    waterfallRows: 0,
    renderEngine: 'CPU',
    peaks: [],
    bluetooth: null,
    fm: null,
    wifi: null,
    zigbee: null,
    gps: null,
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

  useEffect(() => {
    let cancelled = false;

    const bootstrapSdr = async () => {
      try {
        const savedSdr = readLastSdr();
        let response = await axios.get('/api/get_settings');
        let data = response.data || {};
        const currentSdr = data.sdr || '';
        const targetSdr = savedSdr || currentSdr;

        if (savedSdr && savedSdr !== currentSdr) {
          try {
            const selectResponse = await axios.post('/api/select_sdr', { sdr_name: savedSdr });
            if (selectResponse?.data?.result) {
              writeLastSdr(savedSdr);
              response = await axios.get('/api/get_settings');
              data = response.data || {};
            }
          } catch (error) {
            console.error('Error selecting saved SDR on startup:', error);
          }
        } else if (targetSdr) {
          writeLastSdr(targetSdr);
        }

        if (cancelled) return;
        const selectedSdr = data.sdr || targetSdr || savedSdr || 'hackrf';
        setSettings((prev) => ({
          ...prev,
          ...data,
          sdr: selectedSdr,
          frequency: toFinite(data.frequency, prev.frequency || 751),
          gain: toFinite(data.gain, prev.gain || 10),
          sampleRate: toFinite(data.sampleRate, prev.sampleRate || 20),
          bandwidth: toFinite(data.bandwidth, prev.bandwidth || 20),
          frequency_start: toFinite(data.frequency_start, prev.frequency_start || 700),
          frequency_stop: toFinite(data.frequency_stop, prev.frequency_stop || 820),
          waterfallBinCount: toFinite(data.waterfallBinCount, prev.waterfallBinCount || 2048),
          waterfallSamples: toFinite(data.waterfallSamples, prev.waterfallSamples || 200),
          updateInterval: toFinite(data.updateInterval, prev.updateInterval || 500),
          activityLogRetentionSec: prev.activityLogRetentionSec,
        }));
        setShowSecondTrace(selectedSdr === 'hackrf');
      } catch (error) {
        console.error('Error bootstrapping SDR settings:', error);
      }
    };

    bootstrapSdr();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const retentionSec = Number(settings.activityLogRetentionSec);
    if (!Number.isFinite(retentionSec)) {
      return;
    }
    localStorage.setItem(
      ACTIVITY_LOG_RETENTION_STORAGE_KEY,
      String(Math.max(60, Math.min(3600, retentionSec))),
    );
  }, [settings.activityLogRetentionSec]);


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
  const bluetoothEvents = Array.isArray(telemetry.bluetooth?.events) ? telemetry.bluetooth.events : [];
  const bluetoothAdvCount = bluetoothEvents.filter((event) => event?.kind === 'ble_adv').length;
  const bluetoothBtcCount = bluetoothEvents.filter((event) => String(event?.protocol || '').toLowerCase() === 'btc').length;
  const fmStationCount = Number(telemetry.fm?.station_count || 0);
  const fmPotentialCount = Number(telemetry.fm?.potential_count || 0);
  const wifiActivityCount = Number(telemetry.wifi?.activity_count || telemetry.wifi?.event_count || 0);
  const wifiFrameCount = Number(telemetry.wifi?.frame_count || 0);
  const zigbeeFrameCount = Number(telemetry.zigbee?.frame_count || telemetry.zigbee?.event_count || 0);
  const zigbeeBurstCount = Number(telemetry.zigbee?.burst_count || 0);
  const zigbeeChunkCount = Number(telemetry.zigbee?.chunk_count || 0);
  const zigbeeRuntimeChannels = Array.isArray(telemetry.zigbee?.runtime_channels) ? telemetry.zigbee.runtime_channels : [];
  const zigbeeChannelLabel = zigbeeRuntimeChannels.length > 0
    ? `${zigbeeRuntimeChannels[0]}-${zigbeeRuntimeChannels[zigbeeRuntimeChannels.length - 1]}`
    : '-';
  const scannerModeActive = Boolean(telemetry.scannerMode?.active);
  const scannerModeLabel = telemetry.scannerMode?.step?.label || 'Idle';
  const gpsLock = telemetry.gps?.lock || 'NO';
  const gpsConnected = Boolean(telemetry.gps?.connected);
  const GpsIcon = gpsConnected && gpsLock !== 'NO' ? GpsFixedIcon : GpsNotFixedIcon;
  const modalTitles = {
    scanner: 'Scanner',
    analysis: 'Analysis',
    classifiers: 'Classifiers',
    about: 'About SDR Shark',
  };
  const navButtons = [
    { key: 'scanner', label: 'Scanner' },
    { key: 'analysis', label: 'Analysis' },
    { key: 'classifiers', label: 'Classifiers' },
    { key: 'about', label: 'About' },
  ];

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
            justifyContent: 'flex-end',
            gap: 1,
            padding: '0 16px',
            width: '100%',
          }}
        >
          {navButtons.map((item) => (
            <Button
              key={item.key}
              size="small"
              variant="text"
              onClick={() => setModalView(item.key)}
              sx={{
                color: '#d9f0ff',
                borderRadius: '3px',
                px: 1.25,
                '&:hover': {
                  bgcolor: 'rgba(144, 202, 249, 0.10)',
                },
              }}
            >
              {item.label}
            </Button>
          ))}
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 1,
            }}
          >
            <Button
              size="small"
              variant="outlined"
              startIcon={<GpsIcon />}
              onClick={() => setGpsOpen(true)}
              sx={{
                borderColor: gpsConnected ? '#3f7f6f' : '#3d556d',
                color: gpsConnected ? '#c9fff0' : '#d9f0ff',
              }}
            >
              GPS {gpsConnected ? gpsLock : 'OFF'}
            </Button>
            <Button
              size="small"
              variant="outlined"
              startIcon={<SettingsIcon />}
              onClick={() => setSettingsOpen(true)}
              sx={{ borderColor: '#3d556d', color: '#d9f0ff' }}
            >
              Settings
            </Button>
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
          <Chip
            size="small"
            sx={telemetryChipSx}
            color={telemetry.renderEngine === 'GPU' ? 'success' : 'default'}
            label={`Render: ${telemetry.renderEngine || 'CPU'}`}
          />
          <Chip size="small" sx={telemetryChipSx} label={`Latency: ${Math.round(telemetry.latencyMs || 0)} ms`} />
          <Chip size="small" sx={telemetryWideChipSx} color={(telemetry.staleMs || 0) > 3000 ? 'error' : 'default'} label={`Last data age: ${Math.round(telemetry.staleMs || 0)} ms`} />
          <Chip size="small" sx={telemetryWideChipSx} label={`Time: ${telemetry.frameTime || 'n/a'}`} />
          <Chip
            size="small"
            sx={telemetryWideChipSx}
            color={scannerModeActive ? 'success' : (telemetry.sweepEnabled ? 'primary' : 'default')}
            label={scannerModeActive ? `Scan: ${scannerModeLabel}` : `Sweep: ${telemetry.sweepEnabled ? 'On' : 'Off'}`}
          />
          <Chip size="small" sx={telemetryChipSx} label={`Main seq: ${telemetry.mainFrameSeq || 0}`} />
          <Chip size="small" sx={telemetryChipSx} label={`Scanner seq: ${telemetry.scannerFrameSeq || 0}`} />
          <Chip
            size="small"
            sx={telemetryWideChipSx}
            color={telemetry.bluetooth?.active ? 'success' : 'default'}
            label={`BT: ${telemetry.bluetooth?.active ? 'on' : 'off'} BLE ${bluetoothAdvCount} BTC ${bluetoothBtcCount}`}
          />
          <Chip
            size="small"
            sx={telemetryWideChipSx}
            color={telemetry.fm?.active ? 'success' : 'default'}
            label={`FM: ${telemetry.fm?.active ? 'on' : 'off'} ${fmStationCount} stn ${fmPotentialCount} pot`}
          />
          <Chip
            size="small"
            sx={telemetryWideChipSx}
            color={telemetry.wifi?.active ? 'success' : 'default'}
            label={`WiFi: ${telemetry.wifi?.active ? 'on' : 'off'} ${wifiActivityCount} act ${wifiFrameCount} frm`}
          />
          <Chip
            size="small"
            sx={telemetryWideChipSx}
            color={telemetry.zigbee?.active ? 'success' : 'default'}
            label={`ZB: ${telemetry.zigbee?.active ? 'on' : 'off'} ch ${zigbeeChannelLabel} ${zigbeeFrameCount}f ${zigbeeBurstCount}b ${zigbeeChunkCount}iq`}
          />
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
            <Box sx={{ flex: 1, minHeight: 0, overflow: 'hidden' }}>
              <DecodedEventsPanel telemetry={telemetry} settings={settings} />
            </Box>
          </Box>
        </Box>

        <Dialog
          open={Boolean(modalView)}
          onClose={() => setModalView(null)}
          fullWidth
          maxWidth={modalView === 'about' ? 'sm' : 'lg'}
          PaperProps={{
            sx: {
              height: modalView === 'about' ? 'auto' : '82vh',
              maxHeight: '88vh',
              bgcolor: '#101418',
              backgroundImage: 'linear-gradient(145deg, rgba(20, 40, 50, 0.96), rgba(6, 8, 10, 0.98))',
              border: '1px solid rgba(144,202,249,0.18)',
            },
          }}
        >
          <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', pb: 1 }}>
            <Box>
              <Typography variant="overline" color="text.secondary">SDR Shark</Typography>
              <Typography variant="h6">{modalTitles[modalView] || 'SDR Shark'}</Typography>
            </Box>
            <IconButton onClick={() => setModalView(null)} aria-label="Close panel">
              <CloseIcon />
            </IconButton>
          </DialogTitle>
          <DialogContent sx={{ minHeight: 0, overflow: modalView === 'about' ? 'auto' : 'hidden', px: 1.5, pb: 1.5 }}>
            {modalView === 'scanner' && (
              <Box sx={{ height: '100%', minHeight: 0, overflow: 'hidden' }}>
                <Scanner settings={settings} setSettings={setSettings} onClose={() => setModalView(null)} />
              </Box>
            )}
            {modalView === 'analysis' && (
              <Box sx={{ height: '100%', minHeight: 0, overflow: 'auto', pr: 1 }}>
                <Analysis
                  settings={settings}
                  setSettings={setSettings}
                  addVerticalLines={addVerticalLines}
                  clearVerticalLines={clearVerticalLines}
                  addHorizontalLines={addHorizontalLines}
                  clearHorizontalLines={clearHorizontalLines}
                />
              </Box>
            )}
            {modalView === 'classifiers' && (
              <Box sx={{ height: '100%', minHeight: 0, overflow: 'auto', pr: 1 }}>
                <Classifiers
                  settings={settings}
                  setSettings={setSettings}
                  addVerticalLines={addVerticalLines}
                  clearVerticalLines={clearVerticalLines}
                  addHorizontalLines={addHorizontalLines}
                  clearHorizontalLines={clearHorizontalLines}
                />
              </Box>
            )}
            {modalView === 'about' && (
              <Box sx={{ textAlign: 'center', py: 2 }}>
                <img
                  src="shark_icon.png"
                  alt="Shark Icon"
                  style={{ width: '150px', height: '150px', marginBottom: '20px' }}
                />
                <Typography variant="body1" sx={{ mb: 1 }}>
                  Author: Jacob M. Ramey
                </Typography>
                <Typography variant="body1" sx={{ mb: 1 }}>
                  Github Repo: <a href="https://github.com/rameyjm7/SDR-Shark" target="_blank" rel="noopener noreferrer">https://github.com/rameyjm7/SDR-Shark</a>
                </Typography>
                <Typography variant="body1" sx={{ mb: 1 }}>
                  Github: <a href="https://github.com/rameyjm7" target="_blank" rel="noopener noreferrer">https://github.com/rameyjm7</a>
                </Typography>
                <Typography variant="body1" sx={{ mb: 1 }}>
                  LinkedIn: <a href="https://www.linkedin.com/in/rameyjm/" target="_blank" rel="noopener noreferrer">https://www.linkedin.com/in/rameyjm/</a>
                </Typography>
                <Typography variant="body1" sx={{ mb: 1 }}>
                  License: <a href="https://github.com/rameyjm7/SDR-Shark/blob/main/LICENSE" target="_blank" rel="noopener noreferrer">View License</a>
                </Typography>
                <Typography variant="body2" sx={{ mt: 2 }}>
                  Copyright (c) 2024 Jacob M. Ramey
                </Typography>
              </Box>
            )}
          </DialogContent>
        </Dialog>

        <Dialog
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          fullWidth
          maxWidth="lg"
          PaperProps={{
            sx: {
              height: '86vh',
              bgcolor: '#101418',
              backgroundImage: 'linear-gradient(145deg, rgba(24, 45, 54, 0.96), rgba(8, 10, 12, 0.98))',
              border: '1px solid rgba(144,202,249,0.18)',
            },
          }}
        >
          <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', pb: 1 }}>
            <Box>
              <Typography variant="overline" color="text.secondary">SDR Shark</Typography>
              <Typography variant="h6">Settings</Typography>
            </Box>
            <IconButton onClick={() => setSettingsOpen(false)} aria-label="Close settings">
              <CloseIcon />
            </IconButton>
          </DialogTitle>
          <DialogContent sx={{ minHeight: 0, overflow: 'hidden', px: 1.5, pb: 1.5 }}>
            <Box sx={{ height: '100%', minHeight: 0, overflow: 'auto', pr: 1 }}>
              <ControlPanel
                settings={settings}
                setSettings={setSettings}
                minY={minY}
                setMinY={setMinY}
                maxY={maxY}
                setMaxY={setMaxY}
                setUpdateInterval={setUpdateInterval}
                showWaterfall={showWaterfall}
                setShowWaterfall={setShowWaterfall}
                addVerticalLines={addVerticalLines}
                clearVerticalLines={clearVerticalLines}
                addHorizontalLines={addHorizontalLines}
                clearHorizontalLines={clearHorizontalLines}
                verticalLines={verticalLines}
              />
            </Box>
          </DialogContent>
        </Dialog>

        <GpsDialog open={gpsOpen} onClose={() => setGpsOpen(false)} />

      </Box>
    </ThemeProvider>
  );
};

export default App;
