import React, { useEffect, useState } from 'react';
import axios from 'axios';
import Box from '@mui/material/Box';
import { DataGrid } from '@mui/x-data-grid';
import { FormControlLabel, Slider, Switch, Typography, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper, Button } from '@mui/material';

const SETTINGS_POST_CONFIG = { timeout: 8000 };

const FoldableSection = ({ title, defaultOpen = true, children }) => (
  <Box
    sx={{
      border: '1px solid #2c2c2c',
      borderRadius: 1,
      mb: 1.25,
      backgroundColor: '#0b0b0b',
      overflow: 'hidden',
    }}
  >
    <details open={defaultOpen} style={{ width: '100%' }}>
      <summary
        style={{
          cursor: 'pointer',
          listStyle: 'none',
          padding: '10px 12px',
          borderBottom: '1px solid #242424',
          fontWeight: 600,
          fontSize: '1.05rem',
          userSelect: 'none',
        }}
      >
        {title}
      </summary>
      <Box sx={{ p: 1.25 }}>{children}</Box>
    </details>
  </Box>
);

const Analysis = ({ settings, setSettings, addVerticalLines, clearVerticalLines, addHorizontalLines, clearHorizontalLines }) => {
  const [peaks, setPeaks] = useState([]);
  const [generalClassifications, setGeneralClassifications] = useState([]);
  const [signalStats, setSignalStats] = useState({ noise_floor: -255 });
  const convertToHz = (valueInMHz) => valueInMHz * 1e6;
  const convertToMHz = (valueInHz) => valueInHz / 1e6;

  const handleChange = (e) => {
    const { name, value, type, checked } = e.target;
    let newValue = type === 'checkbox' ? checked : parseFloat(value);

    if (name === 'frequency' || name === 'sampleRate' || name === 'bandwidth') {
      newValue = convertToHz(newValue);
    }

    const newSettings = {
      ...settings,
      [name]: newValue,
    };
    setSettings(newSettings);
    updateSettings(newSettings);
  };

  const handleSliderChange = (e, value, name) => {
    let newValue = value;

    if (name === 'frequency' || name === 'sampleRate' || name === 'bandwidth') {
      newValue = convertToHz(newValue);
    }

    const newSettings = { ...settings, [name]: newValue };
    setSettings(newSettings);
    updateSettings(newSettings);
  };

  const updateSettings = async (newSettings) => {
    try {
      const response = await axios.post('/api/update_settings', newSettings, SETTINGS_POST_CONFIG);
      if (response?.data?.success === false) {
        throw new Error(response.data.error || 'Settings update failed');
      }
    } catch (error) {
      console.error('Error updating settings:', error);
    }
  };

  const handleTuneToClassification = async (classification, includeBandwidth = false) => {
    const freq = Number(classification?.frequency);
    if (!Number.isFinite(freq)) return;
    const bw = Number(classification?.bandwidth);

    const newSettings = { ...settings, frequency: freq };
    if (includeBandwidth && Number.isFinite(bw) && bw > 0) {
      newSettings.sampleRate = bw;
      newSettings.bandwidth = bw;
    }
    setSettings(newSettings);
    await updateSettings(newSettings);
  };

  const handleMarkClassificationBounds = (classification) => {
    const freq = Number(classification?.frequency);
    const bw = Number(classification?.bandwidth);
    if (Number.isFinite(freq) && Number.isFinite(bw) && bw > 0) {
      addVerticalLines(freq, bw);
    }
  };

  const handleAddLine = (key, value) => {
    if (typeof value === 'number') {
      if (key.toLowerCase().includes('freq')) {
        addVerticalLines(value, 0.001);
      } else {
        addHorizontalLines(value);
      }
    } else {
      console.warn('Cannot add a line for a non-numeric value.');
    }
  };

  const handleMarkNoiseFloor = () => {
    const noiseFloor = signalStats.noise_floor;
    if (noiseFloor !== undefined) {
      addHorizontalLines(noiseFloor);  // Call the function with noise floor value
    } else {
      console.warn('Noise floor value is not available.');
    }
  };
  const handleAddMarkers = (frequency, bandwidth) => {
    addVerticalLines(frequency, bandwidth);
  };

  const peakColumns = [
    { field: 'frequency', headerName: 'Frequency (MHz)', width: 180 },
    { field: 'seen_count', headerName: 'Seen', width: 82 },
    { field: 'age_seconds', headerName: 'Age (s)', width: 90 },
    { field: 'peak_power', headerName: 'Power Peak (dB)', width: 140 },
    { field: 'avg_power', headerName: 'Power Avg. (dB)', width: 140 },
    { field: 'bandwidth', headerName: 'Bandwidth (MHz)', width: 140 },
    {
      field: 'classification',
      headerName: 'Classifications',
      flex: 1,
      minWidth: 180,
      renderCell: (params) => (
        <Typography variant="caption" noWrap title={params.value}>
          {params.value}
        </Typography>
      ),
    },
    // { field: 'freq_start', headerName: 'Frequency Start (MHz)', width: 180 },
    // { field: 'freq_end', headerName: 'Frequency End (MHz)', width: 180 },
    {
      field: 'actions',
      headerName: 'Action',
      width: 104,
      sortable: false,
      filterable: false,
      disableColumnMenu: true,
      renderCell: (params) => (
        <Button
          size="small"
          variant="contained"
          color="primary"
          onClick={() => handleAddMarkers(params.row.frequency, params.row.bandwidth)}
          sx={{ minWidth: 0, px: 0.9, py: 0.45, fontSize: '0.68rem', lineHeight: 1, whiteSpace: 'nowrap' }}
        >
          ADD
        </Button>
      ),
    },
  ];

  const peakRows = peaks.map((peak, index) => ({
    id: index,
    frequency: peak.frequency !== undefined ? peak.frequency.toFixed(3) : 'N/A',
    seen_count: Number.isFinite(Number(peak.seen_count)) ? Number(peak.seen_count) : 1,
    age_seconds: Number.isFinite(Number(peak.age_seconds)) ? Number(peak.age_seconds).toFixed(1) : '0.0',
    peak_power: peak.peak_power !== undefined ? peak.peak_power.toFixed(3) : 'N/A',
    avg_power: peak.avg_power !== undefined ? peak.avg_power.toFixed(3) : 'N/A',
    bandwidth: peak.bandwidth !== undefined ? peak.bandwidth.toFixed(5) : 'N/A',
    classification: peak.classification?.map(c => `${c.label} (${c.channel})`).join(', ') || 'N/A',
    // freq_start: peak.freq_start !== undefined ? peak.freq_start.toFixed(3) : 'N/A',
    // freq_end: peak.freq_end !== undefined ? peak.freq_end.toFixed(3) : 'N/A',
  }));

  const statEntries = Object.entries(signalStats || {});
  const compactStatEntries = statEntries.filter(([key, value]) => {
    if (key.toLowerCase().includes('error')) return false;
    if (typeof value === 'string' && value.length > 120) return false;
    return true;
  });

  const formatStatValue = (value) => {
    if (typeof value === 'number' && Number.isFinite(value)) {
      return Number(value.toFixed(5));
    }
    return value;
  };

  useEffect(() => {
    const fetchAnalytics = async () => {
      try {
        const response = await axios.get('/api/analytics');
        const data = response.data;
        setPeaks(data.peaks);
        setGeneralClassifications(data.classifications);
        // Set signal stats from the status data
        if (data.signal_stats) {
          setSignalStats(data.signal_stats);
        }
      } catch (error) {
        console.error('Error fetching analytics:', error);
      }
    };

    const interval = setInterval(fetchAnalytics, 250);
    return () => clearInterval(interval);
  }, [setSettings]);

  useEffect(() => {
    if (settings.peakDetection === undefined) {
      const newSettings = {
        ...settings,
        peakDetection: true,
      };
      setSettings(newSettings);
      updateSettings(newSettings);
    }
  }, [settings, setSettings]);

  useEffect(() => {
    if (!Number.isFinite(Number(settings.analysisRetentionSec))) {
      const newSettings = { ...settings, analysisRetentionSec: 10 };
      setSettings(newSettings);
      updateSettings(newSettings);
    }
  }, [settings, setSettings]);

  const groupedClassifications = generalClassifications.reduce((acc, classification) => {
    if (!acc[classification.label]) {
      acc[classification.label] = [];
    }
    acc[classification.label].push(classification);
    return acc;
  }, {});

  return (
    <Box sx={{ height: '100%', overflowY: 'auto', pr: 1 }}>
      <FoldableSection title="Peak + Marker Controls" defaultOpen>
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: { xs: '1fr', md: '1fr 1fr', xl: '220px minmax(220px, 1fr) minmax(220px, 1fr) minmax(220px, 1fr) 240px' },
            gap: 0.9,
            alignItems: 'center',
          }}
        >
          <Box sx={{ display: 'flex', justifyContent: 'center' }}>
            <FormControlLabel
              control={
                <Switch
                  checked={settings.peakDetection ?? false}
                  onChange={handleChange}
                  name="peakDetection"
                  color="primary"
                />
              }
              label="Annotate Peaks"
              sx={{ m: 0 }}
            />
          </Box>

          <Box sx={{ minWidth: 0 }}>
            <Typography sx={{ mb: 0.2, textAlign: 'center' }}>
              Min Distance (MHz): {settings.minPeakDistance}
            </Typography>
            <Slider
              size="small"
              min={0.01}
              max={1.0}
              value={settings.minPeakDistance}
              onChange={(e, value) => handleSliderChange(e, value, 'minPeakDistance')}
              valueLabelDisplay="auto"
              step={0.01}
              marks={[
                { value: 0.01, label: '0.01' },
                { value: 0.5, label: '0.5' },
                { value: 1.0, label: '1.0' }
              ]}
              sx={{ mt: 0, mb: 0, maxWidth: 320, mx: 'auto' }}
            />
          </Box>

          <Box sx={{ minWidth: 0 }}>
            <Typography sx={{ mb: 0.2, textAlign: 'center' }}>
              Noise Offset (dB): {settings.peakThreshold}
            </Typography>
            <Slider
              size="small"
              min={0}
              max={50}
              value={settings.peakThreshold}
              onChange={(e, value) => handleSliderChange(e, value, 'peakThreshold')}
              valueLabelDisplay="auto"
              step={1}
              marks={[
                { value: 0, label: '0' },
                { value: 25, label: '25' },
                { value: 50, label: '50' }
              ]}
              sx={{ mt: 0, mb: 0, maxWidth: 320, mx: 'auto' }}
            />
          </Box>

          <Box sx={{ minWidth: 0 }}>
            <Typography sx={{ mb: 0.2, textAlign: 'center' }}>
              Drop-off (s): {Number(settings.analysisRetentionSec ?? 10).toFixed(1)}
            </Typography>
            <Slider
              size="small"
              min={1}
              max={60}
              value={Number(settings.analysisRetentionSec ?? 10)}
              onChange={(e, value) => handleSliderChange(e, value, 'analysisRetentionSec')}
              valueLabelDisplay="auto"
              step={1}
              marks={[
                { value: 1, label: '1' },
                { value: 10, label: '10' },
                { value: 60, label: '60' }
              ]}
              sx={{ mt: 0, mb: 0, maxWidth: 320, mx: 'auto' }}
            />
          </Box>

          <Box sx={{ display: 'flex', gap: 0.6, justifyContent: { xs: 'flex-start', xl: 'center' }, flexWrap: 'wrap' }}>
            <Button size="small" variant="contained" color="secondary" onClick={clearVerticalLines} sx={{ minWidth: 0, px: 1.1 }}>
              Clear Vertical
            </Button>
            <Button size="small" variant="contained" color="secondary" onClick={clearHorizontalLines} sx={{ minWidth: 0, px: 1.1 }}>
              Clear Horizontal
            </Button>
          </Box>
        </Box>
      </FoldableSection>

      <Box
        sx={{
          display: 'grid',
          gridTemplateColumns: { xs: '1fr', xl: 'minmax(0, 1.8fr) minmax(320px, 1fr)' },
          gap: 1.5,
          alignItems: 'start',
        }}
      >
        <Box>
          <FoldableSection title="Detected Peaks" defaultOpen>
            <Box sx={{ width: '100%' }}>
              <DataGrid
                rows={peakRows}
                columns={peakColumns}
                pageSize={5}
                autoHeight
                rowHeight={36}
                columnHeaderHeight={40}
                density="compact"
                disableRowSelectionOnClick
                sx={{
                  '& .MuiDataGrid-columnHeaderTitle': { fontSize: '0.86rem' },
                  '& .MuiDataGrid-cell': { fontSize: '0.83rem' },
                  '& .MuiDataGrid-cell:focus, & .MuiDataGrid-columnHeader:focus': { outline: 'none' },
                }}
              />
            </Box>
          </FoldableSection>
        </Box>

        <Box>
          <FoldableSection title="Signal Statistics" defaultOpen>
            <TableContainer component={Paper} sx={{ maxWidth: 460 }}>
              <Table aria-label="signal statistics table">
                <TableHead>
                  <TableRow>
                    <TableCell style={{ padding: '8px 12px' }}>Statistic</TableCell>
                    <TableCell align="right" style={{ padding: '8px 12px' }}>Value</TableCell>
                    <TableCell align="right" style={{ padding: '8px 12px' }}>Action</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {compactStatEntries.map(([key, value]) => {
                    const formattedKey = key.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());
                    const formattedLabel = key.includes('freq')
                      ? `${formattedKey} (MHz)`
                      : key.includes('power') || key.includes('max') || key.includes('noise')
                        ? `${formattedKey} (dB)`
                        : formattedKey;

                    return (
                      <TableRow key={key}>
                        <TableCell component="th" scope="row" style={{ padding: '8px 12px' }}>
                          {formattedLabel}
                        </TableCell>
                        <TableCell align="right" style={{ padding: '8px 12px' }}>
                          {formatStatValue(value)}
                        </TableCell>
                        <TableCell align="right" style={{ padding: '8px 12px' }}>
                          {typeof value === 'number' && (
                            <Button
                              size="small"
                              variant="contained"
                              color="primary"
                              onClick={() => handleAddLine(key, value)}
                              style={{ padding: '4px 8px', fontSize: '0.75rem', minWidth: 'auto' }}
                            >
                              Add Line
                            </Button>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          </FoldableSection>
        </Box>
      </Box>

      <FoldableSection title="Classifications" defaultOpen>
        <Box sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr', xl: '1fr 1fr 1fr' }, gap: 1.25 }}>
          {Object.entries(groupedClassifications).map(([label, classifications]) => (
            <Paper key={label} sx={{ p: 1, backgroundColor: '#121212' }}>
              <details open={false}>
                <summary style={{ cursor: 'pointer', userSelect: 'none', fontWeight: 600 }}>
                  {label} ({classifications.length})
                </summary>
              {classifications.map((classification, idx) => (
                <Box
                  key={`${label}-${idx}`}
                  sx={{
                    p: 0.75,
                    mb: 0.5,
                    border: '1px solid #2e2e2e',
                    borderRadius: 1,
                    backgroundColor: '#101010',
                  }}
                >
                  <Typography variant="caption">
                    Freq: {formatStatValue(Number(classification.frequency))} MHz
                    {classification.bandwidth !== undefined ? ` | BW: ${formatStatValue(Number(classification.bandwidth))} MHz` : ''}
                    {classification.channel ? ` | Ch: ${classification.channel}` : ''}
                  </Typography>
                  {classification.metadata ? (
                    <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block', mt: 0.25 }}>
                      {classification.metadata}
                    </Typography>
                  ) : null}
                  <Box sx={{ display: 'flex', gap: 0.5, mt: 0.5, flexWrap: 'wrap' }}>
                    <Button size="small" variant="contained" onClick={() => handleTuneToClassification(classification, false)}>
                      Tune
                    </Button>
                    <Button size="small" variant="contained" onClick={() => handleTuneToClassification(classification, true)}>
                      Tune + BW
                    </Button>
                    <Button size="small" variant="outlined" onClick={() => handleMarkClassificationBounds(classification)}>
                      Mark Bounds
                    </Button>
                  </Box>
                </Box>
              ))}
              </details>
            </Paper>
          ))}
        </Box>
      </FoldableSection>
    </Box>
  );
};

export default Analysis;
