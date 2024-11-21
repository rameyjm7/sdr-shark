import React, { useState, useEffect } from "react";
import Plot from "react-plotly.js";
import axios from "axios";
import {
  Box,
  Typography,
  Button,
  Snackbar,
  Alert,
  FormGroup,
  FormControlLabel,
  Checkbox,
  Grid,
} from "@mui/material";

const Scanner = () => {
  const [sweepData, setSweepData] = useState({ x: [], y: [] });
  const [isSweeping, setIsSweeping] = useState(false);
  const [showToast, setShowToast] = useState(false);
  const [selectedAreas, setSelectedAreas] = useState([]);

  const areasOfInterest = {
    "FM Radio": [87.7, 107.7],
    "315MHz ISM": [315, 316],
    "433MHz Band": [433, 434],
    "462.5MHz PTT": [462.5, 463.5],
    "WiFi 2.4GHz": [2400, 2483.5],
    "WiFi 5.8GHz": [5725, 5850],
    "LTE Band 2": [1850, 1910],
    "LTE Band 4 (AWS)": [1710, 1755],
    "LTE Band 12": [699, 716],
    "LTE Band 13": [746, 756],
  };

  useEffect(() => {
    const fetchSweepData = async () => {
      try {
        const response = await axios.get("/api/sweep"); // Replace with your API endpoint
        const { x, y } = response.data;
        setSweepData({ x, y });
      } catch (error) {
        console.error("Error fetching sweep data:", error);
      }
    };

    fetchSweepData();
  }, []);

  const handleToggleSweep = () => {
    if (isSweeping) {
      setShowToast(true);
      setIsSweeping(false);
    } else {
      setIsSweeping(true);
      setShowToast(true);
      axios.post("/api/start_sweep").catch((err) => console.error(err));
    }
  };

  const handleCloseToast = () => {
    setShowToast(false);
  };

  const handleCheckboxChange = (area) => {
    setSelectedAreas((prevSelected) => {
      if (prevSelected.includes(area)) {
        return prevSelected.filter((item) => item !== area);
      }
      return [...prevSelected, area];
    });
  };

  const handleAddAll = () => {
    setSelectedAreas(Object.keys(areasOfInterest));
  };

  const handleClearAll = () => {
    setSelectedAreas([]);
  };

  const generateMarkers = () => {
    const markers = [];
    selectedAreas.forEach((label) => {
      const range = areasOfInterest[label];
      markers.push(
        {
          type: "line",
          x0: range[0],
          x1: range[0],
          y0: -20,
          y1: 60,
          line: { color: "red", dash: "dash", width: 1 },
          name: `${label} Start`,
        },
        {
          type: "line",
          x0: range[1],
          x1: range[1],
          y0: -20,
          y1: 60,
          line: { color: "green", dash: "dash", width: 1 },
          name: `${label} Stop`,
        }
      );
    });
    return markers;
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%", // Full height of the parent container
        width: "100%", // Full width of the parent container
        overflow: "hidden", // Prevent scrollbars
      }}
    >
      <Typography variant="h4" gutterBottom>
        Sweep Visualization
      </Typography>

      <Box
        sx={{
          display: "flex",
          flexDirection: "row",
          gap: "16px", // Spacing between checkbox and plot
          height: "100%", // Full height for both columns
        }}
      >
        {/* Left column for checkboxes */}
        <Box
          sx={{
            width: "300px", // Fixed width for the checkbox area
            overflowY: "auto",
            backgroundColor: "#121212",
            padding: "16px",
            borderRadius: "8px",
          }}
        >
          <Typography variant="h6" gutterBottom>
            Select Areas of Interest
          </Typography>
          <FormGroup>
            {Object.keys(areasOfInterest).map((area) => (
              <FormControlLabel
                key={area}
                control={
                  <Checkbox
                    checked={selectedAreas.includes(area)}
                    onChange={() => handleCheckboxChange(area)}
                  />
                }
                label={area}
              />
            ))}
          </FormGroup>
          <Box sx={{ display: "flex", gap: "8px", marginTop: "16px" }}>
            <Button variant="contained" color="primary" onClick={handleAddAll}>
              Add All
            </Button>
            <Button variant="contained" color="secondary" onClick={handleClearAll}>
              Clear All
            </Button>
          </Box>
          <Button
            variant="contained"
            color={isSweeping ? "error" : "primary"}
            onClick={handleToggleSweep}
            sx={{ marginTop: "16px", width: "100%" }}
          >
            {isSweeping ? "Stop Sweep" : "Start Sweep"}
          </Button>
        </Box>

        {/* Right column for the plot */}
        <Box
          sx={{
            flex: 1, // Take up remaining space
            overflow: "hidden", // Prevent scrollbars
            display: "flex",
            flexDirection: "column",
            justifyContent: "center",
            alignItems: "center",
          }}
        >
          <Plot
            data={[
              {
                x: sweepData.x,
                y: sweepData.y,
                type: "scatter",
                mode: "lines",
                line: { color: "yellow" },
                name: "FFT",
              },
            ]}
            layout={{
              title: {
                text: "Sweep from 20.0 to 6020.0 MHz",
                font: { color: "white" },
              },
              xaxis: {
                title: { text: "Frequency (MHz)", font: { color: "white" } },
                tickcolor: "white",
                showgrid: true,
                gridcolor: "gray",
              },
              yaxis: {
                title: { text: "Power (dB)", font: { color: "white" } },
                tickcolor: "white",
                range: [-20, 60],
                showgrid: true,
                gridcolor: "gray",
              },
              shapes: generateMarkers(),
              legend: {
                x: 1,
                y: 1,
                traceorder: "normal",
                font: { size: 10, color: "white" },
                bgcolor: "rgba(0, 0, 0, 0.5)",
              },
              paper_bgcolor: "#000",
              plot_bgcolor: "#000",
            }}
            config={{ responsive: true }}
            style={{ width: "100%", height: "100%" }}
          />
        </Box>
      </Box>
    </Box>
  );
};

export default Scanner;
