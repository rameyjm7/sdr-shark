import React, { useEffect, useState } from 'react';
import axios from 'axios';
import Box from '@mui/material/Box';
import { RichTreeView } from '@mui/x-tree-view/RichTreeView';
import { Typography, Menu, MenuItem } from '@mui/material';
import ClassifierUploader from './ClassifierUploader';  // Import your new component

const Classifiers = ({ settings, setSettings, addVerticalLines, clearVerticalLines }) => {
  const [classifiers, setClassifiers] = useState([]);
  const [contextMenu, setContextMenu] = useState(null);
  const [lastSelectedItem, setLastSelectedItem] = useState(null);

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
        return {
          id: itemId,
          label: `Channel: ${classification.channel}, Frequency: ${classification.frequency} MHz, Bandwidth: ${classification.bandwidth} MHz, Metadata: ${classification.metadata}`,
          frequency: classification.frequency,
          bandwidth: classification.bandwidth,
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

  const handleContextMenu = (event, item) => {
    event.preventDefault();
    setContextMenu(
      contextMenu === null
        ? { mouseX: event.clientX - 2, mouseY: event.clientY - 4, item }
        : null,
    );
  };

  const handleMenuClose = () => {
    setContextMenu(null);
  };

  const convertToHz = (valueInMHz) => valueInMHz * 1e6;

  const handleTuneToFreq = () => {
    if (lastSelectedItem) {
      clearVerticalLines();
      const itemIndex = lastSelectedItem.split('-').slice(1).map(Number);
      const classification = classifiers[itemIndex];
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
    if (lastSelectedItem) {
      const itemIndex = lastSelectedItem.split('-').slice(1).map(Number);
      const classification = classifiers[itemIndex];
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
    if (lastSelectedItem) {
      const itemIndex = lastSelectedItem.split('-').slice(1).map(Number);
      const classification = classifiers[itemIndex];
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
        <MenuItem onClick={handleTuneToFreqBandwidth}>Tune to Freq, BW</MenuItem>
        <MenuItem onClick={handleAddVerticalLines}>Mark Signal Bounds</MenuItem> {/* New menu item */}
      </Menu>

      {/* File upload section */}
      <Box mt={3}>
        <ClassifierUploader onUploadSuccess={handleUploadSuccess} />
      </Box>
    </Box>
  );
};

export default Classifiers;
