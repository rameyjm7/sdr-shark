import React, { useEffect, useState } from "react";
import axios from "axios";
import {
  Box,
  Typography,
  Button,
  Snackbar,
  Alert,
  Checkbox,
  Chip,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
} from "@mui/material";
import BluetoothIcon from "@mui/icons-material/Bluetooth";
import RadioIcon from "@mui/icons-material/Radio";
import SensorsIcon from "@mui/icons-material/Sensors";
import WifiIcon from "@mui/icons-material/Wifi";

const SCANNER_CONFIG_STORAGE_KEY = "sdrShark.scannerConfig.v1";

const loadSavedScannerConfig = () => {
  if (typeof window === "undefined") return {};
  try {
    return JSON.parse(window.localStorage.getItem(SCANNER_CONFIG_STORAGE_KEY) || "{}") || {};
  } catch (_err) {
    return {};
  }
};

const Scanner = ({ onClose }) => {
  const savedScannerConfig = loadSavedScannerConfig();
  const [isScanning, setIsScanning] = useState(false);
  const [showToast, setShowToast] = useState(false);
  const [toastSeverity, setToastSeverity] = useState("success");
  const [toastMessage, setToastMessage] = useState("");
  const [selectedAreas, setSelectedAreas] = useState(Array.isArray(savedScannerConfig.selectedAreas) ? savedScannerConfig.selectedAreas : []);
  const [scannerActive, setScannerActive] = useState(false);
  const [scannerDirty, setScannerDirty] = useState(false);
  const [cycleSec, setCycleSec] = useState(savedScannerConfig.cycleSec || 60);
  const [dwellPercentages, setDwellPercentages] = useState(
    savedScannerConfig.dwellPercentages && typeof savedScannerConfig.dwellPercentages === "object"
      ? savedScannerConfig.dwellPercentages
      : {}
  );

  const ism24Areas = [
    { label: "Zigbee", range: [2405, 2480], centerMhz: 2442, bandwidthMhz: 60, sampleRateMhz: 60, protocols: ["zigbee"], icon: SensorsIcon, color: "#b084ff" },
    { label: "Thread", range: [2405, 2480], centerMhz: 2442, bandwidthMhz: 60, sampleRateMhz: 60, protocols: ["thread", "zigbee"], icon: SensorsIcon, color: "#a3e635" },
    { label: "WiFi 2.4GHz", range: [2400, 2483.5], centerMhz: 2442, bandwidthMhz: 60, sampleRateMhz: 60, protocols: ["wifi"], icon: WifiIcon, color: "#6ecbff" },
    { label: "Bluetooth Classic", range: [2402, 2480], centerMhz: 2442, bandwidthMhz: 60, sampleRateMhz: 60, protocols: ["btc"], icon: BluetoothIcon, color: "#ffd166" },
    { label: "Bluetooth Low Energy", range: [2402, 2480], centerMhz: 2442, bandwidthMhz: 60, sampleRateMhz: 60, protocols: ["btle"], icon: BluetoothIcon, color: "#64f0d2" },
  ];
  const otherAreas = [
    { label: "FM Radio", range: [87.7, 107.7], centerMhz: 97.7, bandwidthMhz: 20, sampleRateMhz: 20, protocols: ["fm"], icon: RadioIcon, color: "#ffb347" },
    { label: "315MHz ISM", range: [315, 316], centerMhz: 315.5, bandwidthMhz: 2, sampleRateMhz: 2, protocols: ["rf"], icon: SensorsIcon, color: "#64f0d2" },
    { label: "433MHz Band", range: [433, 434], centerMhz: 433.5, bandwidthMhz: 2, sampleRateMhz: 2, protocols: ["rf"], icon: SensorsIcon, color: "#64f0d2" },
    { label: "462.5MHz PTT", range: [462.5, 463.5], centerMhz: 463, bandwidthMhz: 2, sampleRateMhz: 2, protocols: ["rf"], icon: RadioIcon, color: "#ffd166" },
    { label: "ADS-B 1090", range: [1089, 1091], centerMhz: 1090, bandwidthMhz: 2, sampleRateMhz: 2, protocols: ["adsb"], icon: SensorsIcon, color: "#ff6b6b" },
    { label: "LTE Band 2", range: [1850, 1910], centerMhz: 1880, bandwidthMhz: 60, sampleRateMhz: 60, protocols: ["cellular"], icon: SensorsIcon, color: "#b084ff" },
    { label: "LTE Band 4 (AWS)", range: [1710, 1755], centerMhz: 1732.5, bandwidthMhz: 45, sampleRateMhz: 45, protocols: ["cellular"], icon: SensorsIcon, color: "#b084ff" },
    { label: "LTE Band 12", range: [699, 716], centerMhz: 707.5, bandwidthMhz: 17, sampleRateMhz: 17, protocols: ["cellular"], icon: SensorsIcon, color: "#b084ff" },
    { label: "LTE Band 13", range: [746, 756], centerMhz: 751, bandwidthMhz: 10, sampleRateMhz: 10, protocols: ["cellular"], icon: SensorsIcon, color: "#b084ff" },
    { label: "WiFi 5.8GHz", range: [5725, 5850], centerMhz: 5787.5, bandwidthMhz: 60, sampleRateMhz: 60, protocols: ["wifi"], icon: WifiIcon, color: "#6ecbff" },
  ];
  const scanGroups = [
    { title: "2.4 GHz ISM", description: "Wideband scan: 60 MHz low pass, then 60 MHz high pass.", areas: ism24Areas },
    { title: "Everything Else", description: "Broadcast, sub-GHz, cellular, and other bands.", areas: otherAreas },
  ];
  const areasOfInterest = scanGroups.flatMap((group) => group.areas);
  const scanOrder = [
    "FM Radio",
    "315MHz ISM",
    "433MHz Band",
    "462.5MHz PTT",
    "ADS-B 1090",
    "LTE Band 12",
    "LTE Band 13",
    "LTE Band 4 (AWS)",
    "LTE Band 2",
    "WiFi 5.8GHz",
  ];
  const ISM_24_WEIGHT = "2.4 GHz ISM";

  const weightingLabelsForSelection = (selection = selectedAreas) => {
    const labels = otherAreas
      .filter((area) => selection.includes(area.label))
      .map((area) => area.label);
    if (ism24Areas.some((area) => selection.includes(area.label))) {
      labels.push(ISM_24_WEIGHT);
    }
    return labels;
  };

  const rebalancePercentages = (labels, anchorLabel = null, anchorValue = null, previous = dwellPercentages) => {
    if (!labels.length) return {};
    const uniqueLabels = Array.from(new Set(labels));
    if (anchorLabel && uniqueLabels.includes(anchorLabel)) {
      const anchor = Math.max(0, Math.min(100, Number(anchorValue) || 0));
      const others = uniqueLabels.filter((label) => label !== anchorLabel);
      if (!others.length) return { [anchorLabel]: 100 };
      const remaining = Math.max(0, 100 - anchor);
      const previousOtherTotal = others.reduce((sum, label) => sum + Math.max(0, Number(previous[label]) || 0), 0);
      const next = { [anchorLabel]: Math.round(anchor * 10) / 10 };
      others.forEach((label, index) => {
        const share = previousOtherTotal > 0
          ? remaining * (Math.max(0, Number(previous[label]) || 0) / previousOtherTotal)
          : remaining / others.length;
        next[label] = index === others.length - 1
          ? Math.round((100 - Object.values(next).reduce((sum, value) => sum + value, 0)) * 10) / 10
          : Math.round(share * 10) / 10;
      });
      return next;
    }
    const equalShare = 100 / uniqueLabels.length;
    const next = {};
    uniqueLabels.forEach((label, index) => {
      next[label] = index === uniqueLabels.length - 1
        ? Math.round((100 - Object.values(next).reduce((sum, value) => sum + value, 0)) * 10) / 10
        : Math.round(equalShare * 10) / 10;
    });
    return next;
  };

  const percentageFor = (label) => {
    if (label === ISM_24_WEIGHT && !ism24Areas.some((area) => selectedAreas.includes(area.label))) return 0;
    if (label !== ISM_24_WEIGHT && !selectedAreas.includes(label)) return 0;
    return Number(dwellPercentages[label] ?? 0);
  };

  const buildScanPlan = () => {
    const selected2G4 = ism24Areas.filter((area) => selectedAreas.includes(area.label));
    const selectedOther = otherAreas.filter((area) => selectedAreas.includes(area.label));
    const orderedOther = scanOrder
      .map((label) => selectedOther.find((area) => area.label === label))
      .filter(Boolean);
    const selected2G4Protocols = Array.from(new Set(selected2G4.flatMap((area) => area.protocols || [])));
    const cycle = Math.max(1, Math.min(3600, Number(cycleSec) || 60));
    const stepFor = (area) => ({
      ...area,
      percent: percentageFor(area.label),
      dwellSec: Math.max(0.5, cycle * (percentageFor(area.label) / 100)),
      revisitSec: cycle,
    });
    const ismPercent = percentageFor(ISM_24_WEIGHT);
    const ismDwellTotal = cycle * (ismPercent / 100);
    return [
      ...orderedOther.map(stepFor),
      ...(selected2G4.length > 0 ? [
        {
          label: "2.4 GHz ISM Low",
          centerMhz: 2430,
          bandwidthMhz: 60,
          sampleRateMhz: 60,
          protocols: selected2G4Protocols,
          percent: ismPercent / 2,
          dwellSec: Math.max(0.5, ismDwellTotal / 2),
          revisitSec: cycle,
        },
        {
          label: "2.4 GHz ISM High",
          centerMhz: 2450,
          bandwidthMhz: 60,
          sampleRateMhz: 60,
          protocols: selected2G4Protocols,
          percent: ismPercent / 2,
          dwellSec: Math.max(0.5, ismDwellTotal / 2),
          revisitSec: cycle,
        },
      ] : []),
    ];
  };

  useEffect(() => {
    let cancelled = false;
    axios.get("/api/scanner/status")
      .then((response) => {
        if (!cancelled) {
          const scanner = response?.data?.scanner || {};
          const config = scanner.config || {};
          if (Array.isArray(config.selectedAreas)) {
            setSelectedAreas(config.selectedAreas);
          }
          if (config.dwellPercentages && typeof config.dwellPercentages === "object") {
            setDwellPercentages(config.dwellPercentages);
          }
          if (config.cycleSec) {
            setCycleSec(config.cycleSec);
          }
          setScannerActive(Boolean(scanner.active));
          setScannerDirty(false);
        }
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (typeof window === "undefined") return;
    window.localStorage.setItem(
      SCANNER_CONFIG_STORAGE_KEY,
      JSON.stringify({
        selectedAreas,
        dwellPercentages,
        cycleSec,
      })
    );
  }, [selectedAreas, dwellPercentages, cycleSec]);

  const handleStartScan = async () => {
    const selectedSteps = areasOfInterest.filter((area) => selectedAreas.includes(area.label));
    if (!selectedSteps.length) {
      setToastSeverity("warning");
      setToastMessage("Select at least one band or protocol to scan.");
      setShowToast(true);
      return;
    }
    const plannedSteps = buildScanPlan();
    setIsScanning(true);
    try {
      await axios.post("/api/scanner/start", {
        dwellSec: Math.max(1, Math.min(3600, Number(cycleSec) || 60)),
        config: {
          selectedAreas,
          dwellPercentages,
          cycleSec: Math.max(1, Math.min(3600, Number(cycleSec) || 60)),
        },
        steps: plannedSteps.map((area) => ({
          label: area.label,
          centerMhz: area.centerMhz,
          bandwidthMhz: area.bandwidthMhz,
          sampleRateMhz: area.sampleRateMhz,
          dwellSec: area.dwellSec,
          protocols: area.protocols,
        })),
      });
      setToastSeverity("success");
      setToastMessage(`Scanner started with ${plannedSteps.length} dwell steps.`);
      setScannerActive(true);
      setScannerDirty(false);
      setShowToast(true);
      if (typeof onClose === "function") {
        onClose();
      }
    } catch (err) {
      setToastSeverity("error");
      setToastMessage(err?.response?.data?.error || "Failed to start scanner.");
      setShowToast(true);
    } finally {
      setIsScanning(false);
    }
  };

  const handleStopScan = async () => {
    setIsScanning(true);
    try {
      await axios.post("/api/scanner/stop");
      setScannerActive(false);
      setScannerDirty(false);
      setToastSeverity("success");
      setToastMessage("Scanner stopped.");
      setShowToast(true);
    } catch (err) {
      setToastSeverity("error");
      setToastMessage(err?.response?.data?.error || "Failed to stop scanner.");
      setShowToast(true);
    } finally {
      setIsScanning(false);
    }
  };

  const handleCloseToast = () => {
    setShowToast(false);
  };

  const handleCheckboxChange = (area) => {
    setScannerDirty(true);
    setSelectedAreas((prevSelected) => {
      if (prevSelected.includes(area)) {
        const nextSelected = prevSelected.filter((item) => item !== area);
        setDwellPercentages(rebalancePercentages(weightingLabelsForSelection(nextSelected), null, null, dwellPercentages));
        return nextSelected;
      }
      const nextSelected = [...prevSelected, area];
      setDwellPercentages(rebalancePercentages(weightingLabelsForSelection(nextSelected), null, null, dwellPercentages));
      return nextSelected;
    });
  };

  const handleAddAll = () => {
    const labels = areasOfInterest.map((area) => area.label);
    setScannerDirty(true);
    setSelectedAreas(labels);
    setDwellPercentages(rebalancePercentages(weightingLabelsForSelection(labels)));
  };

  const handleClearAll = () => {
    setScannerDirty(true);
    setSelectedAreas([]);
    setDwellPercentages({});
  };

  const groupSelectionState = (areas) => {
    const labels = areas.map((area) => area.label);
    const selectedCount = labels.filter((label) => selectedAreas.includes(label)).length;
    return {
      checked: selectedCount === labels.length && labels.length > 0,
      indeterminate: selectedCount > 0 && selectedCount < labels.length,
    };
  };

  const handleToggleGroup = (areas) => {
    setScannerDirty(true);
    const labels = areas.map((area) => area.label);
    const allSelected = labels.every((label) => selectedAreas.includes(label));
    setSelectedAreas((prevSelected) => {
      if (allSelected) {
        const nextSelected = prevSelected.filter((label) => !labels.includes(label));
        setDwellPercentages(rebalancePercentages(weightingLabelsForSelection(nextSelected), null, null, dwellPercentages));
        return nextSelected;
      }
      const nextSelected = Array.from(new Set([...prevSelected, ...labels]));
      setDwellPercentages(rebalancePercentages(weightingLabelsForSelection(nextSelected), null, null, dwellPercentages));
      return nextSelected;
    });
  };

  const handlePercentChange = (label, value) => {
    setScannerDirty(true);
    setDwellPercentages((previous) => rebalancePercentages(weightingLabelsForSelection(), label, value, previous));
  };

  const renderAreaCard = ({ label, range, icon: AreaIcon, color }) => {
    const checked = selectedAreas.includes(label);
    const isIsm24Area = ism24Areas.some((area) => area.label === label);
    const percent = percentageFor(label);
    return (
      <Box
        key={label}
        onClick={() => handleCheckboxChange(label)}
        sx={{
          display: "flex",
          alignItems: "center",
          gap: 1.25,
          p: 1.25,
          borderRadius: "8px",
          cursor: "pointer",
          border: checked ? "1px solid rgba(144, 202, 249, 0.82)" : "1px solid rgba(255, 255, 255, 0.10)",
          bgcolor: checked ? "rgba(144, 202, 249, 0.13)" : "rgba(255, 255, 255, 0.035)",
          transition: "border-color 120ms ease, background-color 120ms ease, transform 120ms ease",
          "&:hover": {
            borderColor: "rgba(144, 202, 249, 0.62)",
            bgcolor: "rgba(144, 202, 249, 0.09)",
            transform: "translateY(-1px)",
          },
        }}
      >
        <Checkbox
          checked={checked}
          onChange={() => handleCheckboxChange(label)}
          onClick={(event) => event.stopPropagation()}
          sx={{ p: 0.25 }}
        />
        {AreaIcon ? (
          <AreaIcon
            fontSize="small"
            sx={{
              color,
              filter: checked ? `drop-shadow(0 0 7px ${color})` : "none",
              opacity: checked ? 1 : 0.72,
            }}
          />
        ) : null}
        <Box sx={{ minWidth: 0, flex: 1 }}>
          <Typography variant="subtitle2" sx={{ lineHeight: 1.2 }}>
            {label}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {range[0]}-{range[1]} MHz
          </Typography>
        </Box>
        {checked && !isIsm24Area ? (
          <TextField
            label="%"
            type="number"
            size="small"
            value={percent}
            onChange={(event) => handlePercentChange(label, event.target.value)}
            onClick={(event) => event.stopPropagation()}
            inputProps={{ min: 0, max: 100, step: 1 }}
            sx={{
              width: 82,
              '& input': { textAlign: 'right' },
            }}
          />
        ) : null}
      </Box>
    );
  };
  const previewPlan = buildScanPlan();
  const previewCycle = Math.max(1, Math.min(3600, Number(cycleSec) || 60));
  const totalPercent = weightingLabelsForSelection().reduce((sum, label) => sum + percentageFor(label), 0);
  const primaryStopsScan = scannerActive && (!scannerDirty || selectedAreas.length === 0);
  const primaryScanLabel = primaryStopsScan ? "Stop Scan" : (scannerActive ? "Update Scan" : "Start Scan");
  const primaryScanDisabled = isScanning || (!scannerActive && selectedAreas.length === 0);
  const handlePrimaryScanAction = () => {
    if (primaryStopsScan) {
      handleStopScan();
    } else {
      handleStartScan();
    }
  };

  return (
    <Box
      sx={{
        display: "flex",
        flexDirection: "column",
        height: "100%", // Full height of the parent container
        width: "100%", // Full width of the parent container
        boxSizing: "border-box",
        overflow: "auto",
        p: 1,
      }}
    >
      <Box
        sx={{
          width: "100%",
          minHeight: 0,
          overflowY: "auto",
          backgroundColor: "rgba(10, 14, 17, 0.72)",
          border: "1px solid rgba(144, 202, 249, 0.14)",
          padding: "18px",
          borderRadius: "8px",
        }}
      >
        <Box sx={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 2, mb: 2, flexWrap: "wrap" }}>
          <Box>
            <Typography variant="h5" sx={{ lineHeight: 1.1 }}>
              Scanner Controls
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mt: 0.75 }}>
              Choose protocols and bands to dwell on while decoders run.
            </Typography>
          </Box>
          <Box sx={{ display: "flex", alignItems: "center", gap: 1, flexWrap: "wrap" }}>
            <TextField
              label="Cycle (sec)"
              type="number"
              size="small"
              value={cycleSec}
              onChange={(event) => {
                setCycleSec(event.target.value);
                setScannerDirty(true);
              }}
              inputProps={{ min: 1, max: 3600, step: 1 }}
              sx={{ width: 130 }}
            />
            <Chip
              size="small"
              color={selectedAreas.length ? (Math.abs(totalPercent - 100) < 0.01 ? "primary" : "warning") : "default"}
              label={`${selectedAreas.length} selected / ${totalPercent.toFixed(0)}%`}
              sx={{ flexShrink: 0 }}
            />
          </Box>
        </Box>

        {scanGroups.map((group) => (
          <Box key={group.title} sx={{ mt: 2.25 }}>
            <Box sx={{ display: "flex", alignItems: "center", gap: 1, mb: 1, flexWrap: "wrap" }}>
              <Checkbox
                checked={groupSelectionState(group.areas).checked}
                indeterminate={groupSelectionState(group.areas).indeterminate}
                onChange={() => handleToggleGroup(group.areas)}
                sx={{ p: 0.25 }}
              />
              <Box sx={{ display: "flex", alignItems: "baseline", gap: 1, flexWrap: "wrap", flex: 1 }}>
                <Typography variant="h6" sx={{ lineHeight: 1.1 }}>
                  {group.title}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {group.description}
                </Typography>
              </Box>
              {group.title === ISM_24_WEIGHT && group.areas.some((area) => selectedAreas.includes(area.label)) ? (
                <TextField
                  label="2.4 GHz %"
                  type="number"
                  size="small"
                  value={percentageFor(ISM_24_WEIGHT)}
                  onChange={(event) => handlePercentChange(ISM_24_WEIGHT, event.target.value)}
                  inputProps={{ min: 0, max: 100, step: 1 }}
                  sx={{
                    width: 120,
                    '& input': { textAlign: 'right' },
                  }}
                />
              ) : null}
            </Box>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
                gap: 1.25,
              }}
            >
              {group.areas.map(renderAreaCard)}
            </Box>
          </Box>
        ))}
        <Box sx={{ mt: 2.5 }}>
          <Typography variant="h6" sx={{ mb: 1 }}>
            Scan Plan
          </Typography>
          <TableContainer
            sx={{
              border: "1px solid rgba(144, 202, 249, 0.14)",
              borderRadius: "8px",
              bgcolor: "rgba(0, 0, 0, 0.22)",
            }}
          >
            <Table size="small" stickyHeader>
              <TableHead>
                <TableRow>
                  <TableCell>#</TableCell>
                  <TableCell>Step</TableCell>
                  <TableCell>Center</TableCell>
                  <TableCell>BW</TableCell>
                  <TableCell>Protocols</TableCell>
                  <TableCell>Share</TableCell>
                  <TableCell>Dwell</TableCell>
                  <TableCell>Looks every</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {previewPlan.length > 0 ? previewPlan.map((step, index) => (
                  <TableRow key={`${step.label}-${index}`}>
                    <TableCell>{index + 1}</TableCell>
                    <TableCell>{step.label}</TableCell>
                    <TableCell>{Number(step.centerMhz).toFixed(1)} MHz</TableCell>
                    <TableCell>{Number(step.bandwidthMhz).toFixed(0)} MHz</TableCell>
                    <TableCell>{(step.protocols || []).join(", ").toUpperCase() || "RF"}</TableCell>
                    <TableCell>{Number(step.percent || 0).toFixed(1)}%</TableCell>
                    <TableCell>{Number(step.dwellSec || 0).toFixed(1)}s</TableCell>
                    <TableCell>{Number(step.revisitSec || previewCycle).toFixed(1)}s</TableCell>
                  </TableRow>
                )) : (
                  <TableRow>
                    <TableCell colSpan={8} sx={{ color: "text.secondary" }}>
                      Select one or more bands to preview the repeated scan sequence.
                    </TableCell>
                  </TableRow>
                )}
              </TableBody>
            </Table>
          </TableContainer>
          {previewPlan.length > 0 ? (
            <Typography variant="caption" color="text.secondary" sx={{ display: "block", mt: 0.75 }}>
              This sequence repeats continuously until Stop Scan is pressed.
            </Typography>
          ) : null}
        </Box>
        <Box sx={{ display: "flex", gap: "8px", marginTop: "16px", flexWrap: "wrap" }}>
          <Button variant="contained" color="primary" onClick={handleAddAll} sx={{ minWidth: 120 }}>
            Add All
          </Button>
          <Button variant="contained" color="secondary" onClick={handleClearAll} sx={{ minWidth: 120 }}>
            Clear All
          </Button>
          <Box sx={{ flex: 1 }} />
          <Button
            variant="contained"
            color={scannerActive && !scannerDirty ? "error" : "primary"}
            disabled={primaryScanDisabled}
            onClick={handlePrimaryScanAction}
            sx={{ minWidth: 180 }}
          >
            {isScanning ? "Working..." : primaryScanLabel}
          </Button>
        </Box>
      </Box>
      <Snackbar open={showToast} autoHideDuration={3000} onClose={handleCloseToast}>
        <Alert onClose={handleCloseToast} severity={toastSeverity} sx={{ width: "100%" }}>
          {toastMessage}
        </Alert>
      </Snackbar>
    </Box>
  );
};

export default Scanner;
