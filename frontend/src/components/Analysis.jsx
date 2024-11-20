import React, { useEffect, useState } from 'react';
import axios from 'axios';
import Box from '@mui/material/Box';
import { RichTreeView } from '@mui/x-tree-view/RichTreeView';
import { DataGrid } from '@mui/x-data-grid';
import { FormControlLabel, Slider, Switch, Typography, Menu, MenuItem, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, Paper, Button } from '@mui/material';

const Analysis = ({ settings, setSettings, addVerticalLines, clearVerticalLines, addHorizontalLines, clearHorizontalLines }) => {
  const [peaks, setPeaks] = useState([]);
  const [generalClassifications, setGeneralClassifications] = useState([]);
  const [contextMenu, setContextMenu] = useState(null);
  const [lastSelectedItem, setLastSelectedItem] = useState(null);
  const [signalStats, setSignalStats] = useState({ noise_floor: -255 });
  const [markers, setMarkers] = useState({ verticalLines: [], horizontalLines: [] }); // State to store markers

  const updateMarkers = (markerData) => {
    setMarkers(markerData);
  };
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
      console.log('Updating settings:', newSettings);
      await axios.post('/api/update_settings', newSettings);
      console.log('Settings updated successfully!');
    } catch (error) {
      console.error('Error updating settings:', error);
    }
  };

  const handleContextMenu = (event, item) => {
    event.preventDefault();
    setContextMenu(
      contextMenu === null
        ? { mouseX: event.clientX - 2, mouseY: event.clientY - 4 }
        : null,
    );
  };

  const handleMenuClose = () => {
    setContextMenu(null);
  };

  const handleTuneToFreq = () => {
    if (lastSelectedItem) {
      const [groupIndex, itemIndex] = lastSelectedItem.split('-').slice(1).map(Number);
      const classification = generalClassifications[groupIndex];

      if (classification) {
        const frequency = classification.frequency;
        if (frequency) {
          const frequencyInHz = convertToHz(frequency);
          const newSettings = { ...settings, frequency: parseFloat(frequency) };
          setSettings(newSettings);
          updateSettings(newSettings).then(() => {
            console.log(`Successfully tuned to ${frequency} MHz`);
          });
        } else {
          console.warn('Could not find frequency for the selected item.');
        }
      } else {
        console.warn('Could not find the selected item in generalClassifications.');
      }
    } else {
      console.warn('No item selected for tuning.');
    }
    handleMenuClose();
  };

  const handleTuneToFreqBandwidth = () => {
    if (lastSelectedItem) {
      const [groupIndex, itemIndex] = lastSelectedItem.split('-').slice(1).map(Number);
      const classification = generalClassifications[groupIndex];

      if (classification) {
        const frequency = classification.frequency;
        const bandwidth = classification.bandwidth;
        if (frequency && bandwidth) {
          const newSettings = {
            ...settings,
            frequency: parseFloat(frequency),
            sampleRate: parseFloat(bandwidth),
            bandwidth: parseFloat(bandwidth)
          };
          setSettings(newSettings);
          updateSettings(newSettings).then(() => {
            console.log(`Successfully tuned to ${frequency} MHz and set bandwidth to ${bandwidth} MHz`);
          });
        } else {
          console.warn('Could not find frequency or bandwidth for the selected item.');
        }
      } else {
        console.warn('Could not find the selected item in generalClassifications.');
      }
    } else {
      console.warn('No item selected for tuning.');
    }
    handleMenuClose();
  };

  const handleAddVerticalLines = () => {
    if (lastSelectedItem) {
      const [groupIndex, itemIndex] = lastSelectedItem.split('-').slice(1).map(Number);
      const classification = generalClassifications[groupIndex];

      const frequency = classification.frequency;
      const bandwidth = classification.bandwidth;
      console.log(classification);
      console.log(frequency);
      console.log(bandwidth);
      if (frequency && bandwidth) {
        addVerticalLines(frequency, bandwidth);  // Call the function with frequency and bandwidth
        console.log(`Vertical lines added at ${frequency} MHz ± ${bandwidth / 2} MHz`);
      } else {
        console.warn('No frequency or bandwidth found for the selected item.');
      }
    }
    handleMenuClose();
  };

  const handleAddLine = (key, value) => {
    if (typeof value === 'number') {
      if (key.toLowerCase().includes('freq')) {
        addVerticalLines(value, 0.001);
        console.log(`Vertical line added at: ${value} MHz`);
      } else {
        addHorizontalLines(value);
        console.log(`Horizontal line added at: ${value} dB`);
      }
    } else {
      console.warn('Cannot add a line for a non-numeric value.');
    }
  };

  const handleMarkNoiseFloor = () => {
    const noiseFloor = signalStats.noise_floor;
    if (noiseFloor !== undefined) {
      addHorizontalLines(noiseFloor);  // Call the function with noise floor value
      console.log(`Horizontal line added at noise floor level: ${noiseFloor} dB`);
    } else {
      console.warn('Noise floor value is not available.');
    }
  };
  // Function to handle adding markers
  const handleAddMarkers = (frequency, bandwidth) => {
    // Logic to add vertical markers on the plot
    console.log(`Adding markers at frequency: ${frequency} MHz and bandwidth: ${bandwidth} MHz`);
    addVerticalLines(frequency, bandwidth);
    // You can add your actual plotting logic here
  };

  const peakColumns = [
    { field: 'frequency', headerName: 'Frequency (MHz)', width: 180 },
    { field: 'peak_power', headerName: 'Power Peak (dB)', width: 140 },
    { field: 'avg_power', headerName: 'Power Avg. (dB)', width: 140 },
    { field: 'bandwidth', headerName: 'Bandwidth (MHz)', width: 140 },
    { field: 'classification', headerName: 'Classifications', width: 200 },
    { field: 'freq_start', headerName: 'Frequency Start (MHz)', width: 180 },
    { field: 'freq_end', headerName: 'Frequency End (MHz)', width: 180 },
    {
      field: 'actions',
      headerName: 'Actions',
      width: 150,
      renderCell: (params) => (
        <Button
          variant="contained"
          color="primary"
          onClick={() => handleAddMarkers(params.row.frequency, params.row.bandwidth)}
        >
          Add Markers
        </Button>
      ),
    },
  ];

  const peakRows = peaks.map((peak, index) => ({
    id: index,
    frequency: peak.frequency !== undefined ? peak.frequency.toFixed(3) : 'N/A',
    peak_power: peak.peak_power !== undefined ? peak.peak_power.toFixed(3) : 'N/A',
    avg_power: peak.avg_power !== undefined ? peak.avg_power.toFixed(3) : 'N/A',
    bandwidth: peak.bandwidth !== undefined ? peak.bandwidth.toFixed(3) : 'N/A',
    classification: peak.classification?.map(c => `${c.label} (${c.channel})`).join(', ') || 'N/A',
    freq_start: peak.freq_start !== undefined ? peak.freq_start.toFixed(3) : 'N/A',
    freq_end: peak.freq_end !== undefined ? peak.freq_end.toFixed(3) : 'N/A',
  }));

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
      const newSettings = { ...settings, peakDetection: true };
      setSettings(newSettings);
      updateSettings(newSettings);
    }
  }, [settings, setSettings]);

  // Group classifications by label
  const groupedClassifications = generalClassifications.reduce((acc, classification) => {
    if (!acc[classification.label]) {
      acc[classification.label] = [];
    }
    acc[classification.label].push(classification);
    return acc;
  }, {});

  // Convert grouped classifications to TreeViewBaseItem[]
  const classificationItems = Object.entries(groupedClassifications).reduce((acc, [label, classifications], groupIndex) => {
    const groupId = `group-${groupIndex}`;
    const groupItem = {
      id: groupId,
      label: label,
      children: classifications.map((classification, index) => {
        // Sequentially define the item ID
        const itemId = `item-${acc.nextId}`;
        acc.nextId += 1;  // Increment the ID for the next item

        // Build the label dynamically based on available fields
        const labelParts = [];
        if (classification.channel !== undefined) {
          labelParts.push(`Channel: ${classification.channel}`);
        }
        if (classification.frequency !== undefined) {
          labelParts.push(`Frequency: ${classification.frequency} MHz`);
        }
        if (classification.bandwidth !== undefined) {
          labelParts.push(`Bandwidth: ${classification.bandwidth} MHz`);
        }
        if (classification.metadata !== undefined) {
          labelParts.push(`Metadata: ${classification.metadata}`);
        }

        return {
          id: itemId,
          label: labelParts.join(', '),  // Join the parts with a comma
        };
      }),
    };
    acc.items.push(groupItem);
    return acc;
  }, { nextId: 0, items: [] }).items;

  const handleItemSelectionToggle = (event, itemId, isSelected) => {
    if (isSelected) {
      setLastSelectedItem(itemId);
    }
  };

  return (
    <Box>
      <Box display="flex" flexDirection="column" alignItems="center">
        <FormControlLabel
          control={
            <Switch
              checked={settings.peakDetection ?? false} // Ensure this is always defined
              onChange={handleChange}
              name="peakDetection"
              color="primary"
            />
          }
          label="Annotate Peaks"
        />
        <>
          <Box display="flex" justifyContent="space-between" width="100%">
            <Box flex={1} mx={1}>
              <Typography gutterBottom>Min Distance Between Peaks (MHz): {settings.minPeakDistance}</Typography>
              <Slider
                min={0.01}
                max={1.0}
                value={settings.minPeakDistance}
                onChange={(e, value) => handleSliderChange(e, value, 'minPeakDistance')}
                valueLabelDisplay="auto"
                step={0.01}
                marks={[
                  { value: 0.01, label: '0.01 MHz' },
                  { value: 0.5, label: '0.5 MHz' },
                  { value: 1.0, label: '1 MHz' }
                ]}
              />
            </Box>
            <Box flex={1} mx={1}>
              <Typography gutterBottom>Noise offset Peak Threshold (dB): {settings.peakThreshold}</Typography>
              <Slider
                min={0}
                max={50}
                value={settings.peakThreshold}
                onChange={(e, value) => handleSliderChange(e, value, 'peakThreshold')}
                valueLabelDisplay="auto"
                step={1}
                marks={[
                  { value: 0, label: '0 dB' },
                  { value: 25, label: '25 dB' },
                  { value: 50, label: '50 dB' }
                ]}
              />
            </Box>
          </Box>
        </>
      </Box>
      <Box sx={{ height: 400, width: '100%', mt: 2 }}>
        <Typography variant="h6" gutterBottom>Detected Peaks</Typography>
        <DataGrid
          rows={peakRows}
          columns={peakColumns}
          pageSize={5}
        />
      </Box>
      <Box sx={{ mt: 4 }}>
        <Typography variant="h6" gutterBottom>Signal Statistics</Typography>
        <TableContainer component={Paper} style={{ maxWidth: '600px', margin: '0 auto' }}>
          <Table aria-label="simple table" style={{ width: 'auto', tableLayout: 'auto' }}>
            <TableHead>
              <TableRow>
                <TableCell style={{ padding: '8px 12px' }}>Statistic</TableCell>
                <TableCell align="right" style={{ padding: '8px 12px' }}>Value</TableCell>
                <TableCell align="right" style={{ padding: '8px 12px' }}>Action</TableCell> {/* New column for the button */}
              </TableRow>
            </TableHead>
            <TableBody>
              {Object.entries(signalStats).map(([key, value]) => {
                // Capitalize each word in the key
                const formattedKey = key.replace(/_/g, ' ').replace(/\b\w/g, char => char.toUpperCase());

                // Add (MHz) if the key contains 'freq'
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
                      {value}
                    </TableCell>
                    <TableCell align="right" style={{ padding: '8px 12px' }}>
                      {typeof value === 'number' && (
                        <Button
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
        <Box sx={{ mt: 4 }}>
          <Typography variant="h6" gutterBottom>Markers</Typography>
          <Box display="flex" justifyContent="center" sx={{ mt: 2 }}>
            <Button variant="contained" color="secondary" onClick={clearVerticalLines}>
              Clear Vertical Markers
            </Button>
            <Button variant="contained" color="secondary" onClick={clearHorizontalLines}>
              Clear Horizontal Markers
            </Button>
          </Box>
          {/* New section to display markers */}
          <TableContainer component={Paper} sx={{ mt: 2 }}>
            <Table aria-label="Markers Table">
              <TableHead>
                <TableRow>
                  <TableCell>Marker Type</TableCell>
                  <TableCell align="right">Value</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {markers.verticalLines.map((line, index) => (
                  <TableRow key={`vertical-${index}`}>
                    <TableCell component="th" scope="row">
                      Vertical Marker
                    </TableCell>
                    <TableCell align="right">{line.label}</TableCell>
                  </TableRow>
                ))}
                {markers.horizontalLines.map((line, index) => (
                  <TableRow key={`horizontal-${index}`}>
                    <TableCell component="th" scope="row">
                      Horizontal Marker
                    </TableCell>
                    <TableCell align="right">{line.label}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </Box>
      </Box>
      <Box sx={{ mt: 4 }}>
        <Typography variant="h6" gutterBottom>General Classifications</Typography>
        <RichTreeView
          items={classificationItems}
          onItemSelectionToggle={handleItemSelectionToggle}
          onContextMenu={(event, item) => handleContextMenu(event, item)}
        />
        <Menu
          open={contextMenu !== null}
          onClose={handleMenuClose}
          anchorReference="anchorPosition"
          anchorPosition={
            contextMenu !== null
              ? { top: contextMenu.mouseY, left: contextMenu.mouseX }
              : undefined
          }
        >
          <MenuItem onClick={handleTuneToFreq}>Tune to Freq</MenuItem>
          <MenuItem onClick={handleTuneToFreqBandwidth}>Tune to Freq, Bandwidth</MenuItem>
          <MenuItem onClick={handleAddVerticalLines}>Mark Signal Bounds</MenuItem> {/* New menu item */}
        </Menu>
      </Box>
    </Box>
  );
};

export default Analysis;
