import React, { useEffect, useState } from 'react';
import axios from 'axios';
import Box from '@mui/material/Box';
import { RichTreeView } from '@mui/x-tree-view/RichTreeView';
import { Typography, Menu, MenuItem, Paper, Button, Stack } from '@mui/material';
import ClassifierUploader from './ClassifierUploader';  // Import your new component

const Classifiers = ({ settings, setSettings, addVerticalLines, clearVerticalLines }) => {
  const [classifiers, setClassifiers] = useState([]);
  const [contextMenu, setContextMenu] = useState(null);
  const [selectedItemId, setSelectedItemId] = useState(null);

  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await axios.get('/api/get_classifiers');
        const data = response.data;
        setClassifiers(data);
      } catch (error) {
        console.error('Error fetching classifiers:', error);
      }
    };

    fetchData();
  }, []);

  // Group classifications by label
  const groupedClassifications = classifiers.reduce((acc, classification) => {
    if (!acc[classification.label]) {
      acc[classification.label] = [];
    }
    acc[classification.label].push(classification);
    return acc;
  }, {});

  const itemToClassification = {};
  const classificationItems = Object.entries(groupedClassifications)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([label, group], groupIndex) => {
      const groupId = `group-${groupIndex}`;
      return {
        id: groupId,
        label: `${label} (${group.length})`,
        children: group.map((classification, index) => {
          const itemId = `item-${groupIndex}-${index}`;
          itemToClassification[itemId] = classification;
          const freq = Number(classification.frequency);
          const bw = Number(classification.bandwidth);
          const freqText = Number.isFinite(freq) ? freq.toFixed(3) : classification.frequency;
          const bwText = Number.isFinite(bw) ? bw.toFixed(3) : classification.bandwidth;
          const channel = classification.channel || 'n/a';
          const metadata = classification.metadata ? ` | ${classification.metadata}` : '';
          return {
            id: itemId,
            label: `Ch ${channel} | ${freqText} MHz | BW ${bwText} MHz${metadata}`,
          };
        }),
      };
    });

  const getSelectedClassification = () => {
    if (!selectedItemId) return null;
    return itemToClassification[selectedItemId] || null;
  };

  const handleItemSelectionToggle = (event, itemId, isSelected) => {
    if (isSelected && itemId.startsWith('item-')) {
      setSelectedItemId(itemId);
    }
  };

  const handleContextMenu = (event, item) => {
    event.preventDefault();
    if (item?.id?.startsWith('item-')) {
      setSelectedItemId(item.id);
    }
    setContextMenu(
      contextMenu === null
        ? { mouseX: event.clientX - 2, mouseY: event.clientY - 4, item }
        : null,
    );
  };

  const handleMenuClose = () => {
    setContextMenu(null);
  };

  const handleTuneToFreq = () => {
    const classification = getSelectedClassification();
    if (classification) {
      clearVerticalLines();
      const frequency = classification.frequency;
      if (frequency) {
        const newSettings = { ...settings, frequency: parseFloat(frequency) };
        setSettings(newSettings);
        updateSettings(newSettings).then(() => {
          console.log(`Successfully tuned to ${frequency} MHz`);
        });
      } else {
        console.warn('No frequency found for the selected item.');
      }
    }
    handleMenuClose();
  };

  const handleTuneToFreqBandwidth = () => {
    const classification = getSelectedClassification();
    if (classification) {
      const frequency = classification.frequency;
      const bandwidth = classification.bandwidth;
      if (frequency && bandwidth) {
        const newSettings = {
          ...settings,
          frequency: parseFloat(frequency),
          sampleRate: parseFloat(bandwidth),
          bandwidth: parseFloat(bandwidth),
        };
        setSettings(newSettings);
        updateSettings(newSettings).then(() => {
          console.log(`Successfully tuned to ${frequency} MHz and set bandwidth to ${bandwidth} kHz`);
        });
      } else {
        console.warn('No frequency or bandwidth found for the selected item.');
      }
    }
    handleMenuClose();
  };

  const handleAddVerticalLines = () => {
    const classification = getSelectedClassification();
    if (classification) {
      const frequency = classification.frequency;
      const bandwidth = classification.bandwidth;
      if (frequency && bandwidth) {
        addVerticalLines(frequency, bandwidth);  // Call the function with frequency and bandwidth
        console.log(`Vertical lines added at ${frequency} MHz ± ${bandwidth / 2} MHz`);
      } else {
        console.warn('No frequency or bandwidth found for the selected item.');
      }
    }
    handleMenuClose();
  };

  const updateSettings = async (newSettings) => {
    try {
      await axios.post('/api/update_settings', newSettings);
      console.log('Settings updated successfully!');
    } catch (error) {
      console.error('Error updating settings:', error);
    }
  };

  const handleUploadSuccess = async () => {
    // Refetch classifiers to update the list
    const response = await axios.get('/api/get_classifiers');
    setClassifiers(response.data);
  };

  return (
    <Box>
      <Typography variant="h6" gutterBottom>Signal Classifiers</Typography>

      <Paper sx={{ p: 1, mb: 1 }}>
        <Stack direction={{ xs: 'column', md: 'row' }} spacing={1} alignItems={{ xs: 'stretch', md: 'center' }}>
          <Typography variant="body2" sx={{ flex: 1, color: 'text.secondary' }}>
            {selectedItemId
              ? `Selected: ${itemToClassification[selectedItemId]?.label || 'Classifier'}`
              : 'Select a classifier entry to tune or mark bounds'}
          </Typography>
          <Stack direction="row" spacing={1}>
            <Button size="small" variant="contained" onClick={handleTuneToFreq} disabled={!selectedItemId}>Tune</Button>
            <Button size="small" variant="contained" onClick={handleTuneToFreqBandwidth} disabled={!selectedItemId}>Tune + BW</Button>
            <Button size="small" variant="outlined" onClick={handleAddVerticalLines} disabled={!selectedItemId}>Mark Bounds</Button>
          </Stack>
        </Stack>
      </Paper>

      <Paper sx={{ p: 0.5, maxHeight: 420, overflowY: 'auto' }}>
        <RichTreeView
          items={classificationItems}
          onItemSelectionToggle={handleItemSelectionToggle}
          onContextMenu={(event, item) => handleContextMenu(event, item)}
          sx={{
            '& .MuiTreeItem-content': { py: 0.25, borderRadius: 1 },
            '& .MuiTreeItem-label': { fontSize: '0.9rem' },
            '& .MuiTreeItem-groupTransition': { ml: 1.5 },
          }}
        />
      </Paper>

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
        <MenuItem onClick={handleTuneToFreqBandwidth}>Tune to Freq, BW</MenuItem>
        <MenuItem onClick={handleAddVerticalLines}>Mark Signal Bounds</MenuItem> {/* New menu item */}
      </Menu>

      {/* File upload section */}
      <Box mt={2}>
        <ClassifierUploader onUploadSuccess={handleUploadSuccess} />
      </Box>
    </Box>
  );
};

export default Classifiers;
