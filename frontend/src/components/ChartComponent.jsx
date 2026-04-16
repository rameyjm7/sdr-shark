import React, { useEffect, useState, useRef } from 'react';
import axios from 'axios';
import Plot from 'react-plotly.js';
import '../App.css';

const ChartComponent = ({
  settings,
  setSettings,
  sweepSettings,
  setSweepSettings,
  minY,
  maxY,
  setMinY,
  setMaxY,
  updateInterval,
  showWaterfall,
  plotWidth,
  verticalLines,
  horizontalLines,
  onTelemetryUpdate,
}) => {
  const toFinite = (value, fallback) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  const [fftData, setFftData] = useState([]);
  const [fftMaxData, setFftMaxData] = useState([]);
  const [persistanceData, setPersistanceData] = useState([]);
  const [waterfallData, setWaterfallData] = useState([]);
  const [waterfallNoSignal, setWaterfallNoSignal] = useState(false);
  const [spectrumNoSignal, setSpectrumNoSignal] = useState(false);
  const [time, setTime] = useState('');
  const [peaks, setPeaks] = useState([]);
  const prevTickValsRef = useRef([]);
  const prevTickTextRef = useRef([]);
  const [currentFrequency, setCurrentFrequency] = useState(0);
  const [plotHeight, setPlotHeight] = useState(35); // Start with a default value
  const [contextMenu, setContextMenu] = useState(null);
  const [quickStepMHz, setQuickStepMHz] = useState(1);
  const [waterfallColorScale, setWaterfallColorScale] = useState('Jet');
  const [waterfallDbWindow, setWaterfallDbWindow] = useState(80);
  const [waterfallLevelOffset, setWaterfallLevelOffset] = useState(0);
  const [quickCenterMHz, setQuickCenterMHz] = useState(0);
  const [quickSpanMHz, setQuickSpanMHz] = useState(0);
  const [showWaterfallToolbar, setShowWaterfallToolbar] = useState(false);
  const [autoscaleMode, setAutoscaleMode] = useState('manual');
  const [markerPrimary, setMarkerPrimary] = useState(null);
  const [markerSecondary, setMarkerSecondary] = useState(null);
  const [traceStyles, setTraceStyles] = useState({
    live: { visible: true, width: 1, opacity: 1.0 },
    max: { visible: true, width: 1, opacity: 0.9 },
    persist: { visible: true, width: 1, opacity: 0.25 },
  });
  const lastFrameTsRef = useRef(null);
  const lastDataTsRef = useRef(Date.now());
  const droppedFramesRef = useRef(0);
  const lastMainSeqRef = useRef(null);
  const staleSeqCountRef = useRef(0);
  const lastFftSnapshotRef = useRef([]);
  const spectrumPlotRef = useRef(null);
  const lastTuneRef = useRef({
    frequency: Number(settings.frequency),
    sampleRate: Number(settings.sampleRate),
    bandwidth: Number(settings.bandwidth),
  });

  const hasMeaningfulFftChange = (next, prev) => {
    if (!Array.isArray(next) || next.length === 0) return false;
    if (!Array.isArray(prev) || prev.length !== next.length) return true;
    const samplePoints = 32;
    const step = Math.max(1, Math.floor(next.length / samplePoints));
    let diffSum = 0;
    let count = 0;
    for (let i = 0; i < next.length; i += step) {
      diffSum += Math.abs((Number(next[i]) || 0) - (Number(prev[i]) || 0));
      count += 1;
    }
    const avgDiff = count > 0 ? diffSum / count : 0;
    return avgDiff > 0.15;
  };

  useEffect(() => {
    const adjustPlotHeight = () => {
      const containerHeight = window.innerHeight;
      const availableHeight = containerHeight - 100; // Adjust based on the height of other elements (like the control panel)
      const calculatedHeight = (availableHeight * 0.4) / containerHeight * 100; // Set to 40% of the available height
      setPlotHeight(calculatedHeight);
    };

    adjustPlotHeight();
    window.addEventListener('resize', adjustPlotHeight);

    return () => {
      window.removeEventListener('resize', adjustPlotHeight);
    };
  }, []);

  useEffect(() => {
    const fetchData = async () => {
      const start = performance.now();
      try {
        const response = await axios.get('/api/data', {
          params: {
            source: 'main',
            _ts: Date.now(),
          },
        });
        const data = response.data;
        const mainSeq = Number(data?.mainFrameSeq || 0);
        const prevSeq = lastMainSeqRef.current;
        const frameAdvanced = prevSeq === null ? true : mainSeq !== prevSeq;
        lastMainSeqRef.current = mainSeq;
        const rawFft = Array.isArray(data.fft) ? data.fft : [];
        const rawWaterfall = Array.isArray(data.waterfall) ? data.waterfall : [];
        // Replace NaN values in FFT data
        const sanitizedFftData = rawFft.map(value => isNaN(value) ? -255 : value);
        const fftChanged = hasMeaningfulFftChange(sanitizedFftData, lastFftSnapshotRef.current);
        const fftFinite = sanitizedFftData.filter((v) => Number.isFinite(v));
        const fftMin = fftFinite.length ? Math.min(...fftFinite) : -255;
        const fftMax = fftFinite.length ? Math.max(...fftFinite) : -255;
        const fftRange = fftMax - fftMin;
        const fftFlatNoSignal = fftFinite.length > 8 && fftMax < -200 && fftRange < 0.5;
        lastFftSnapshotRef.current = sanitizedFftData;
        setFftData(sanitizedFftData);
        if ((frameAdvanced || fftChanged) && !fftFlatNoSignal) {
          staleSeqCountRef.current = 0;
          setWaterfallNoSignal(false);
          setSpectrumNoSignal(false);
        } else {
          staleSeqCountRef.current += 1;
          if (staleSeqCountRef.current >= 4) {
            setWaterfallNoSignal(true);
            setSpectrumNoSignal(true);
          }
        }
        const resampleRow = (arr, targetBins) => {
          if (!Array.isArray(arr) || arr.length === 0) return [];
          if (arr.length === targetBins) return arr;
          const bins = Math.max(1, targetBins);
          const step = arr.length / bins;
          const out = new Array(bins);
          for (let i = 0; i < bins; i += 1) {
            const startIdx = Math.floor(i * step);
            const endIdx = Math.max(startIdx + 1, Math.floor((i + 1) * step));
            let sum = 0;
            let count = 0;
            for (let j = startIdx; j < endIdx && j < arr.length; j += 1) {
              sum += arr[j];
              count += 1;
            }
            out[i] = count > 0 ? sum / count : arr[Math.min(startIdx, arr.length - 1)];
          }
          return out;
        };
        // Replace NaN values in Waterfall data
        const sanitizedWaterfallData = rawWaterfall
          .filter((row) => Array.isArray(row))
          .map(row =>
            row.map(value => isNaN(value) ? -255 : value)
        );
        const safeWaterfallSamples = Math.max(1, Math.min(2000, toFinite(settings.waterfallSamples, 100)));
        const noSignalRow = (bins) => Array.from({ length: bins }, () => -255);
        if (sanitizedWaterfallData.length > 0 && (frameAdvanced || fftChanged)) {
          setWaterfallData(sanitizedWaterfallData.slice(-safeWaterfallSamples));
        } else if (sanitizedFftData.length > 0 && (frameAdvanced || fftChanged)) {
          const targetBins = Math.max(64, Math.min(8192, toFinite(settings.waterfallBinCount, sanitizedFftData.length)));
          const row = resampleRow(sanitizedFftData, targetBins);
          setWaterfallData((prev) => [...prev, row].slice(-safeWaterfallSamples));
        } else if (!frameAdvanced && !fftChanged && staleSeqCountRef.current >= 4) {
          const targetBins = Math.max(
            64,
            Math.min(
              8192,
              toFinite(settings.waterfallBinCount, (waterfallData[0] && waterfallData[0].length) || sanitizedFftData.length || 2048),
            ),
          );
          setWaterfallData((prev) => {
            const bins = (Array.isArray(prev[0]) && prev[0].length > 0) ? prev[0].length : targetBins;
            return [...prev, noSignalRow(bins)].slice(-safeWaterfallSamples);
          });
        }
        setTime(data.time);
        const backendPeaks = fftFlatNoSignal ? [] : (Array.isArray(data.peaks) ? data.peaks : []);
        let telemetryPeaks = backendPeaks;
        if (backendPeaks.length === 0 && sanitizedFftData.length > 0) {
          const bins = sanitizedFftData.length;
          let maxIdx = 0;
          let maxVal = sanitizedFftData[0];
          for (let i = 1; i < bins; i += 1) {
            if (sanitizedFftData[i] > maxVal) {
              maxVal = sanitizedFftData[i];
              maxIdx = i;
            }
          }
          const centerMHz = Number(data?.settings?.frequency ?? settings.frequency ?? 0);
          const sampleRateMHz = Math.max(0.1, Number(data?.settings?.sample_rate ?? ((settings.sampleRate || 1) * 1e6)) / 1e6);
          const binBwMHz = sampleRateMHz / Math.max(1, bins);
          const absFreqMHz = (centerMHz - (sampleRateMHz / 2)) + (maxIdx * binBwMHz);
          telemetryPeaks = [{
            index: 0,
            frequency: absFreqMHz - centerMHz,
            absolute_frequency: absFreqMHz,
            bandwidth: binBwMHz,
            peak_power: maxVal,
            avg_power: maxVal,
            classification: [],
          }];
        }
        setPeaks(telemetryPeaks);
        if (data.settings.sweeping_enabled) {
          setSweepSettings({
            frequency_start: data.settings.sweep_settings.frequency_start,
            frequency_stop: data.settings.sweep_settings.frequency_stop,
            sweeping_enabled: data.settings.sweeping_enabled,
            bandwidth: data.settings.sweep_settings.frequency_stop - data.settings.sweep_settings.frequency_start,
          });

          const currentFreq = data.settings.center_freq;
          setCurrentFrequency(currentFreq);
        } else {
          setCurrentFrequency(settings.frequency * 1e6);
        }

        const now = Date.now();
        const lastTs = lastFrameTsRef.current;
        const fps = lastTs ? 1000 / Math.max(1, now - lastTs) : 0;
        lastFrameTsRef.current = now;
        lastDataTsRef.current = now;
        const latencyMs = performance.now() - start;
        const safeSdr = data?.settings?.sdr || settings?.sdr || 'n/a';
        const safeSampleRateHz = Math.max(1, (Number(settings.sampleRate) || 1) * 1e6);
        const safeBinsTelemetry = Math.max(1, sanitizedFftData.length || 1);

        if (typeof onTelemetryUpdate === 'function') {
          onTelemetryUpdate({
            sdr: safeSdr,
            hzPerBin: safeSampleRateHz / safeBinsTelemetry,
            frameTime: data?.time || '',
            fps: Number.isFinite(fps) ? fps : 0,
            latencyMs: Number.isFinite(latencyMs) ? latencyMs : 0,
            droppedFrames: droppedFramesRef.current,
            staleMs: Date.now() - lastDataTsRef.current,
            sweepEnabled: Boolean(data?.settings?.sweeping_enabled),
            mainFrameSeq: Number(data?.mainFrameSeq || 0),
            scannerFrameSeq: Number(data?.scannerFrameSeq || 0),
            scannerFresh: Boolean(data?.scannerFresh),
            fftError: data?.fftError || null,
            scannerError: data?.scannerError || null,
            waterfallRows: Number(data?.waterfallRows || 0),
            peaks: telemetryPeaks.slice(0, 16),
          });
        }
      } catch (error) {
        console.error('Error fetching data:', error);
        droppedFramesRef.current += 1;
        staleSeqCountRef.current += 1;
        if (staleSeqCountRef.current >= 2) {
          setWaterfallNoSignal(true);
          setSpectrumNoSignal(true);
        }
        const safeWaterfallSamples = Math.max(1, Math.min(2000, toFinite(settings.waterfallSamples, 100)));
        setWaterfallData((prev) => {
          const bins = (Array.isArray(prev[0]) && prev[0].length > 0)
            ? prev[0].length
            : Math.max(64, Math.min(8192, toFinite(settings.waterfallBinCount, 2048)));
          const row = Array.from({ length: bins }, () => -255);
          return [...prev, row].slice(-safeWaterfallSamples);
        });
        if (typeof onTelemetryUpdate === 'function') {
          onTelemetryUpdate((prev) => ({
            ...(prev || {}),
            droppedFrames: droppedFramesRef.current,
            staleMs: Date.now() - lastDataTsRef.current,
          }));
        }
      }
    };
    const safeInterval = Math.max(50, toFinite(settings.updateInterval, 500));
    fetchData();
    const interval = setInterval(fetchData, safeInterval);
    return () => clearInterval(interval);
  }, [settings.updateInterval, settings.waterfallSamples, setSweepSettings, settings.frequency, settings.sampleRate, settings.sdr, onTelemetryUpdate]);



  useEffect(() => {
    const fetchData = async () => {
      try {
        const response = await axios.get('/api/data_ext', {
          params: { _ts: Date.now() },
        });
        const data = response.data;
        const maxData = Array.isArray(data.max) ? data.max : [];
        const persistData = Array.isArray(data.persistance) ? data.persistance : [];
        // Replace NaN values in FFT data
        const sanitizedMaxFftData = maxData.map(value => isNaN(value) ? -255 : value);
        setFftMaxData(sanitizedMaxFftData);
        const sanitizedPersistanceData = persistData.map(value => isNaN(value) ? -255 : value);
        setPersistanceData(sanitizedPersistanceData);
      } catch (error) {
        console.error('Error fetching data:', error);
      }
    };
    fetchData();
    const interval = setInterval(fetchData, 500);
    return () => clearInterval(interval);
  }, [updateInterval, settings.frequency, settings.sampleRate]);

  useEffect(() => {
    setQuickCenterMHz(toFinite(settings.frequency, 0));
    setQuickSpanMHz(Math.max(0.1, toFinite(settings.sampleRate, 1)));
  }, [settings.frequency, settings.sampleRate]);

  useEffect(() => {
    setTraceStyles((prev) => ({
      ...prev,
      max: { ...prev.max, visible: typeof settings.showMaxTrace === 'boolean' ? settings.showMaxTrace : prev.max.visible },
      persist: {
        ...prev.persist,
        visible: typeof settings.showPersistanceTrace === 'boolean' ? settings.showPersistanceTrace : prev.persist.visible,
      },
    }));
  }, [settings.showMaxTrace, settings.showPersistanceTrace]);

  const generateColor = (value) => {
    if (value >= 0) {
      return 'rgb(0, 255, 0)';
    } else if (value >= -10) {
      const ratio = (value + 10) / 10;
      const red = Math.floor(255 * (1 - ratio));
      const green = 255;
      const blue = 0;
      return `rgb(${red}, ${green}, ${blue})`;
    } else if (value >= -20) {
      const ratio = (value + 20) / 10;
      const red = 255;
      const green = Math.floor(255 * ratio);
      const blue = 0;
      return `rgb(${red}, ${green}, ${blue})`;
    } else {
      return 'rgb(255, 0, 0)';
    }
  };

  const generateAnnotations = (peaks, baseFreq, freqStep) => {
    const startFreq = baseFreq;
    const endFreq = baseFreq + freqStep * (fftData.length - 1);
    if (!settings.peakDetection) return [];
    return peaks
      .filter((peak) => {
        const absFreq = Number(peak?.absolute_frequency);
        const relFreq = Number(peak?.frequency);
        const freqHz = Number.isFinite(absFreq)
          ? absFreq * 1e6
          : (Number.isFinite(relFreq) ? relFreq * 1e6 : NaN);
        return Number.isFinite(freqHz) && freqHz >= startFreq && freqHz <= endFreq;
      })
      .map((peak) => {
        const absFreq = Number(peak?.absolute_frequency);
        const relFreq = Number(peak?.frequency);
        const freqMHz = Number.isFinite(absFreq)
          ? absFreq
          : (safeFrequencyMHz + relFreq);
        const freq = freqMHz * 1e6;
        const power = peak.peak_power.toFixed(2);
        const powerColor = generateColor(power);
        return {
          x: freq,
          y: parseFloat(power),
          xref: 'x',
          yref: 'y',
          text: `${(freq / 1e6).toFixed(2)} MHz<br><span style="color:${powerColor}">${power} dB</span>`,
          showarrow: true,
          arrowhead: 2,
          ax: 0,
          ay: -40,
          font: {
            size: 12,
            color: 'white',
          },
          align: 'center',
        };
      });
  };

  const generateSignalNameAnnotations = (peaks) => {
    if (!settings.peakDetection) return [];
    if (!Array.isArray(peaks) || peaks.length === 0) return [];

    return peaks
      .map((peak, idx) => {
        const absFreq = Number(peak?.absolute_frequency);
        const relFreq = Number(peak?.frequency);
        const freqMHz = Number.isFinite(absFreq)
          ? absFreq
          : (Number.isFinite(relFreq) ? safeFrequencyMHz + relFreq : NaN);
        const power = Number(peak?.peak_power);
        if (!Number.isFinite(freqMHz) || !Number.isFinite(power)) return null;

        const classes = Array.isArray(peak?.classification) ? peak.classification : [];
        if (!classes.length) return null;

        const top = classes[0] || {};
        const label = String(top.label || 'Signal');
        const channel = String(top.channel || '').trim();
        const tag = channel && channel !== 'N/A' ? `${label} ${channel}` : label;

        return {
          x: freqMHz * 1e6,
          y: power + 4 + (idx % 2) * 2,
          xref: 'x',
          yref: 'y',
          text: tag,
          showarrow: false,
          bgcolor: 'rgba(8, 16, 24, 0.82)',
          bordercolor: '#7ec8ff',
          borderwidth: 1,
          borderpad: 2,
          font: {
            size: 10,
            color: '#cfefff',
          },
          align: 'center',
        };
      })
      .filter(Boolean)
      .slice(0, 4);
  };

  const safeFrequencyMHz = toFinite(settings.frequency, 751);
  const safeSampleRateMHz = Math.max(0.1, toFinite(settings.sampleRate, 20));
  const safeSweepStartMHz = toFinite(sweepSettings.frequency_start, safeFrequencyMHz - safeSampleRateMHz / 2);
  const safeSweepStopMHz = toFinite(sweepSettings.frequency_stop, safeFrequencyMHz + safeSampleRateMHz / 2);
  const safeBandwidthHz = Math.max(
    1,
    toFinite(
      sweepSettings.sweeping_enabled
        ? (safeSweepStopMHz - safeSweepStartMHz) * 1e6
        : safeSampleRateMHz * 1e6,
      safeSampleRateMHz * 1e6,
    ),
  );
  const safeBins = Math.max(1, fftData.length);
  const safeWaterfallBins = Math.max(
    1,
    Array.isArray(waterfallData) && waterfallData.length > 0 && Array.isArray(waterfallData[0])
      ? waterfallData[0].length
      : toFinite(settings.waterfallBinCount, safeBins),
  );
  const baseFreq = sweepSettings.sweeping_enabled
    ? safeSweepStartMHz * 1e6
    : (safeFrequencyMHz - safeSampleRateMHz / 2) * 1e6;
  const freqStep = safeBandwidthHz / safeBins;
  const waterfallFreqStep = safeBandwidthHz / safeWaterfallBins;
  const fftX = Array.from({ length: safeBins }, (_, index) => baseFreq + index * freqStep);
  const waterfallX = Array.from(
    { length: safeWaterfallBins },
    (_, index) => baseFreq + index * waterfallFreqStep,
  );
  const maxWaterfallCells = 500000;
  const waterfallRows = waterfallData.length;
  const waterfallCols = safeWaterfallBins;
  const cellCount = waterfallRows * waterfallCols;
  const rowStride = cellCount > maxWaterfallCells ? Math.ceil(cellCount / maxWaterfallCells) : 1;
  const renderedWaterfallData = rowStride > 1
    ? waterfallData.filter((_, idx) => idx % rowStride === 0)
    : waterfallData;
  const peakAnnotations = generateAnnotations(peaks, baseFreq, freqStep);
  const peakNameAnnotations = generateSignalNameAnnotations(peaks);
  const waterfallCenterDb = ((Number(minY) + Number(maxY)) / 2) + waterfallLevelOffset;
  const waterfallZMin = waterfallCenterDb - (waterfallDbWindow / 2);
  const waterfallZMax = waterfallCenterDb + (waterfallDbWindow / 2);

  useEffect(() => {
    if (!Array.isArray(fftData) || fftData.length === 0) {
      return;
    }
    if (autoscaleMode === 'manual' || autoscaleMode === 'hold') {
      return;
    }
    if (typeof setMinY !== 'function' || typeof setMaxY !== 'function') {
      return;
    }
    const sorted = [...fftData].sort((a, b) => a - b);
    const p20 = sorted[Math.floor(sorted.length * 0.2)] ?? minY;
    const p99 = sorted[Math.floor(sorted.length * 0.99)] ?? maxY;
    const peak = sorted[sorted.length - 1] ?? maxY;
    if (autoscaleMode === 'auto_peak') {
      setMinY(Math.round((p20 - 20) * 10) / 10);
      setMaxY(Math.round((peak + 8) * 10) / 10);
    } else if (autoscaleMode === 'noise_follow') {
      setMinY(Math.round((p20 - 15) * 10) / 10);
      setMaxY(Math.round((p99 + 10) * 10) / 10);
    }
  }, [fftData, autoscaleMode, minY, maxY, setMinY, setMaxY]);

  const generateTickValsAndLabels = (startFreq, stopFreq) => {
    const numTicks = settings.numTicks || 5; // Default to 5 if not set
    const totalBandwidth = stopFreq - startFreq;
    const step = totalBandwidth / (numTicks - 1); // Adjust step calculation for numTicks

    const tickVals = [];
    const tickText = [];
    for (let i = 0; i < numTicks; i++) {
      const freq = startFreq + i * step;
      tickVals.push(freq);
      tickText.push((freq / 1e6).toFixed(2)); // Convert to MHz
    }
    return { tickVals, tickText };
  };

  const { tickVals, tickText } = generateTickValsAndLabels(
    baseFreq,
    baseFreq + Math.max(1, freqStep * Math.max(1, safeBins - 1)),
  );

  // Ensure tick values are within a valid range
  const isValidTickVals = tickVals.every((val) => Number.isFinite(val) && val >= 1e6 && val <= 1e10);

  if (isValidTickVals) {
    if (
      JSON.stringify(tickVals) !== JSON.stringify(prevTickValsRef.current) ||
      JSON.stringify(tickText) !== JSON.stringify(prevTickTextRef.current)
    ) {
      prevTickValsRef.current = tickVals;
      prevTickTextRef.current = tickText;
    }
  }

  // Initialize verticalLineTraces before usage
  let verticalLineTraces = [];

  if (verticalLines && verticalLines.length > 0) {
    verticalLineTraces = verticalLines
      .filter(({ frequency }) => Number.isFinite(Number(frequency)))
      .map(({ frequency }) => {
      const f = Number(frequency);
      const lineColor = 'rgb(255, 0, 0)'; // Red color for vertical lines
      return {
        x: [f * 1e6, f * 1e6], // Fixed frequency for both x points
        y: [minY, maxY],           // Span the full y-axis range
        type: 'scatter',
        mode: 'lines',
        line: { color: lineColor, width: 2 },
        hoverinfo: 'x',             // Show frequency on hover
        name: `${f.toFixed(2)} MHz`, // Label for the legend
      };
    });
  }

  // Initialize horizontalLineTraces before usage
  let horizontalLineTraces = [];

  if (horizontalLines && horizontalLines.length > 0) {
    horizontalLineTraces = horizontalLines
      .filter(({ power }) => Number.isFinite(Number(power)))
      .map(({ power }) => {
      const p = Number(power);
      const lineColor = 'rgb(255, 0, 0)'; // Red color for horizontal lines
      return {
        x: [baseFreq, baseFreq + freqStep * (fftData.length - 1)], // Span the entire frequency range
        y: [p, p],           // Fixed power level for both y points
        type: 'scatter',
        mode: 'lines',
        line: { color: lineColor, width: 2 },
        hoverinfo: 'y',             // Show power on hover
        name: `${p.toFixed(2)} dB`, // Label for the legend
      };
    });
  }

  // this is called when a selection is made, allowing us to get the coordinates and send it to the backend to extract that waterfall
  const handleRelayout = (eventData) => {
    if (eventData['xaxis.range[0]'] && eventData['xaxis.range[1]'] &&
      eventData['yaxis.range[0]'] && eventData['yaxis.range[1]']) {

      // Extract the box selection coordinates
      const xStart = eventData['xaxis.range[0]'];
      const xEnd = eventData['xaxis.range[1]'];
      const yStart = eventData['yaxis.range[0]'];
      const yEnd = eventData['yaxis.range[1]'];

      // Assuming frequency and sampleRate are part of your SDR settings
      const frequency = settings.frequency; // Adjust based on your actual settings object structure
      const sampleRate = settings.sampleRate; // Adjust based on your actual settings object structure

      // Prepare the coordinates data to be sent to the backend
      const coordinates = {
        xStart,
        xEnd,
        yStart,
        yEnd,
        filename: `${frequency}_${sampleRate}`, // Default filename
      };

      // Send the initial save request with the default filename
      fetch('/api/save_selection', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(coordinates),
      })
        .then(response => response.json())
        .then(data => {
          // After the initial save, prompt the user to confirm or change the filename
          const userFilename = prompt('Enter filename (leave blank to keep default):', coordinates.filename);

          if (userFilename && userFilename !== coordinates.filename) {
            // Send the rename request if the filename is different
            const renameData = {
              old_filename: coordinates.filename,
              new_filename: userFilename,
            };

            fetch('/api/move', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json',
              },
              body: JSON.stringify(renameData),
            })
              .then(response => response.json())
              .then(() => {})
              .catch((error) => {
                console.error('Rename error:', error);
              });
          }
        })
        .catch((error) => {
          console.error('Initial save error:', error);
        });
    }
  };

  const openContextMenu = (event) => {
    event.preventDefault();
    setContextMenu({ x: event.clientX, y: event.clientY });
  };

  const closeContextMenu = () => {
    setContextMenu(null);
  };

  const clearTraceData = async (trace) => {
    try {
      await axios.post('/api/reset_fft_trace', { trace });
      if (trace === 'max' || trace === 'all') {
        setFftMaxData([]);
      }
      if (trace === 'persist' || trace === 'persistence' || trace === 'all') {
        setPersistanceData([]);
      }
    } catch (error) {
      console.error('Error clearing FFT trace:', error);
    } finally {
      closeContextMenu();
    }
  };

  const clearTraceDataSilent = async (trace) => {
    try {
      await axios.post('/api/reset_fft_trace', { trace });
      if (trace === 'max' || trace === 'all') {
        setFftMaxData([]);
      }
      if (trace === 'persist' || trace === 'persistence' || trace === 'all') {
        setPersistanceData([]);
      }
    } catch (error) {
      console.error('Error clearing FFT trace:', error);
    }
  };

  const pushSettings = async (patch) => {
    const nextSettings = { ...settings, ...patch };
    if (typeof setSettings === 'function') {
      setSettings(nextSettings);
    }
    try {
      await axios.post('/api/update_settings', nextSettings, {
        headers: { 'Content-Type': 'application/json' },
      });
    } catch (error) {
      console.error('Error updating settings patch:', error);
    }
  };

  const applyQuickTune = async (nextCenterMHz, nextSpanMHz) => {
    const safeCenterMHz = Math.max(1, toFinite(nextCenterMHz, safeFrequencyMHz));
    const safeSpanMHz = Math.max(0.2, toFinite(nextSpanMHz, safeSampleRateMHz));
    const lockBw = Boolean(settings.lockBandwidthSampleRate);
    const patch = {
      frequency: safeCenterMHz,
      sampleRate: safeSpanMHz,
      bandwidth: lockBw ? safeSpanMHz : Math.min(toFinite(settings.bandwidth, safeSpanMHz), safeSpanMHz),
    };
    setQuickCenterMHz(safeCenterMHz);
    setQuickSpanMHz(safeSpanMHz);
    await clearTraceDataSilent('all');
    await pushSettings(patch);
  };

  useEffect(() => {
    const prev = lastTuneRef.current;
    const curr = {
      frequency: Number(settings.frequency),
      sampleRate: Number(settings.sampleRate),
      bandwidth: Number(settings.bandwidth),
    };
    if (
      Number.isFinite(prev.frequency) &&
      Number.isFinite(prev.sampleRate) &&
      Number.isFinite(curr.frequency) &&
      Number.isFinite(curr.sampleRate) &&
      (
        Math.abs(curr.frequency - prev.frequency) > 1e-9 ||
        Math.abs(curr.sampleRate - prev.sampleRate) > 1e-9 ||
        Math.abs((curr.bandwidth || 0) - (prev.bandwidth || 0)) > 1e-9
      )
    ) {
      clearTraceDataSilent('all');
    }
    lastTuneRef.current = curr;
  }, [settings.frequency, settings.sampleRate, settings.bandwidth]);

  const nudgeFrequency = (deltaMHz) => {
    applyQuickTune(quickCenterMHz + deltaMHz, quickSpanMHz);
  };

  const markerDelta =
    markerPrimary && markerSecondary
      ? {
        dfMHz: Math.abs(markerSecondary.x - markerPrimary.x) / 1e6,
        dDb: Math.abs(markerSecondary.y - markerPrimary.y),
      }
      : null;

  const clearMarkers = () => {
    setMarkerPrimary(null);
    setMarkerSecondary(null);
  };

  const clearMarkersFromMenu = () => {
    clearMarkers();
    closeContextMenu();
  };

  const handlePlotClick = (event) => {
    const point = event?.points?.[0];
    if (!point) return;
    const marker = {
      x: Number(point.x),
      y: Number(point.y),
    };
    const shiftPressed = Boolean(event?.event?.shiftKey);
    if (shiftPressed && markerPrimary) {
      setMarkerSecondary(marker);
    } else {
      setMarkerPrimary(marker);
      setMarkerSecondary(null);
    }
  };

  const resetZoom = () => {
    if (spectrumPlotRef.current && window.Plotly) {
      window.Plotly.relayout(spectrumPlotRef.current, {
        'xaxis.autorange': true,
        'yaxis.autorange': true,
      });
    }
  };

  useEffect(() => {
    const handleKeyDown = (event) => {
      const tag = (event.target?.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;

      if (event.key === ' ') {
        event.preventDefault();
        const nextSweep = !Boolean(sweepSettings.sweeping_enabled);
        axios.post(nextSweep ? '/api/start_sweep' : '/api/stop_sweep').catch(() => {});
        if (typeof setSettings === 'function') {
          setSettings({ ...settings, sweeping_enabled: nextSweep });
        }
      } else if (event.key === '[') {
        event.preventDefault();
        nudgeFrequency(-quickStepMHz);
      } else if (event.key === ']') {
        event.preventDefault();
        nudgeFrequency(quickStepMHz);
      } else if (event.key.toLowerCase() === 'm') {
        event.preventDefault();
        clearTraceData('all');
      } else if (event.key.toLowerCase() === 'r') {
        event.preventDefault();
        resetZoom();
      } else if (event.key.toLowerCase() === 'g') {
        event.preventDefault();
        const nextGain = prompt('Set gain (dB):', String(toFinite(settings.gain, 10)));
        if (nextGain !== null) {
          const parsed = toFinite(nextGain, settings.gain);
          pushSettings({ gain: parsed });
        }
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [settings, sweepSettings.sweeping_enabled, quickStepMHz]);

  return (
    <div onContextMenu={openContextMenu} onClick={closeContextMenu} style={{ position: 'relative' }}>
      <div style={quickTuneBarStyle}>
        <label style={quickTuneLabelStyle}>Center (MHz)</label>
        <input
          type="number"
          step="0.1"
          value={quickCenterMHz}
          onChange={(e) => setQuickCenterMHz(toFinite(e.target.value, quickCenterMHz))}
          onBlur={() => applyQuickTune(quickCenterMHz, quickSpanMHz)}
          style={quickTuneInputStyle}
        />
        <label style={quickTuneLabelStyle}>Span (MHz)</label>
        <input
          type="number"
          step="0.1"
          value={quickSpanMHz}
          onChange={(e) => setQuickSpanMHz(Math.max(0.2, toFinite(e.target.value, quickSpanMHz)))}
          onBlur={() => applyQuickTune(quickCenterMHz, quickSpanMHz)}
          style={quickTuneInputStyle}
        />
        <label style={quickTuneLabelStyle}>Step</label>
        <select
          value={quickStepMHz}
          onChange={(e) => setQuickStepMHz(toFinite(e.target.value, 1))}
          style={quickTuneSelectStyle}
        >
          {[0.025, 0.1, 0.5, 1, 2, 5, 10].map((step) => (
            <option key={step} value={step}>{step} MHz</option>
          ))}
        </select>
        <button type="button" style={quickTuneButtonStyle} onClick={() => nudgeFrequency(-quickStepMHz)}> -Step </button>
        <button type="button" style={quickTuneButtonStyle} onClick={() => nudgeFrequency(quickStepMHz)}> +Step </button>
        <span style={{ ...quickTuneLabelStyle, marginLeft: 8 }}>
          RBW est: {(safeBandwidthHz / Math.max(1, safeBins) / 1e3).toFixed(1)} kHz/bin
        </span>
        <label style={{ ...quickTuneLabelStyle, marginLeft: 8 }}>Y-Auto</label>
        <select
          value={autoscaleMode}
          onChange={(e) => setAutoscaleMode(e.target.value)}
          style={quickTuneSelectStyle}
        >
          <option value="manual">Manual</option>
          <option value="auto_peak">Auto peak</option>
          <option value="noise_follow">Noise-follow</option>
          <option value="hold">Hold</option>
        </select>
        <span style={{ ...quickTuneLabelStyle, marginLeft: 6 }}>Keys: Space [ ] G R M</span>
      </div>
      <div style={{ position: 'relative' }}>
        <div style={traceToolbarStyle}>
          <label style={quickTuneLabelStyle}>
            <input
              type="checkbox"
              checked={traceStyles.live.visible}
              onChange={(e) => setTraceStyles((prev) => ({ ...prev, live: { ...prev.live, visible: e.target.checked } }))}
            />
            Live
          </label>
          <label style={quickTuneLabelStyle}>
            <input
              type="checkbox"
              checked={traceStyles.max.visible}
              onChange={(e) => {
                const checked = e.target.checked;
                setTraceStyles((prev) => ({ ...prev, max: { ...prev.max, visible: checked } }));
                if (typeof setSettings === 'function') setSettings({ ...settings, showMaxTrace: checked });
              }}
            />
            Max
          </label>
          <label style={quickTuneLabelStyle}>
            <input
              type="checkbox"
              checked={traceStyles.persist.visible}
              onChange={(e) => {
                const checked = e.target.checked;
                setTraceStyles((prev) => ({ ...prev, persist: { ...prev.persist, visible: checked } }));
                if (typeof setSettings === 'function') setSettings({ ...settings, showPersistanceTrace: checked });
              }}
            />
            Persist
          </label>
          <label style={quickTuneLabelStyle}>
            <input
              type="checkbox"
              checked={Boolean(showWaterfall)}
              onChange={(e) => {
                const checked = Boolean(e.target.checked);
                pushSettings({ showWaterfall: checked });
              }}
            />
            WF
          </label>
          <label style={quickTuneLabelStyle}>W</label>
          <input
            type="range"
            min="1"
            max="4"
            step="1"
            value={traceStyles.live.width}
            onChange={(e) => {
              const width = toFinite(e.target.value, 1);
              setTraceStyles((prev) => ({
                live: { ...prev.live, width },
                max: { ...prev.max, width },
                persist: { ...prev.persist, width },
              }));
            }}
          />
          <label style={quickTuneLabelStyle}>Op</label>
          <input
            type="range"
            min="0.1"
            max="1"
            step="0.05"
            value={traceStyles.live.opacity}
            onChange={(e) => setTraceStyles((prev) => ({ ...prev, live: { ...prev.live, opacity: toFinite(e.target.value, 1) } }))}
          />
        </div>
        {(markerPrimary || markerSecondary) && (
          <div style={markerReadoutStyle}>
            {markerPrimary && <div style={quickTuneLabelStyle}>f1 { (markerPrimary.x / 1e6).toFixed(6) } MHz | p1 { markerPrimary.y.toFixed(2) } dB</div>}
            {markerSecondary && <div style={quickTuneLabelStyle}>f2 { (markerSecondary.x / 1e6).toFixed(6) } MHz | p2 { markerSecondary.y.toFixed(2) } dB</div>}
            {markerDelta && <div style={quickTuneLabelStyle}>df {markerDelta.dfMHz.toFixed(6)} MHz | ddB {markerDelta.dDb.toFixed(2)}</div>}
            <button type="button" style={quickTuneButtonStyle} onClick={clearMarkers}>Clear Markers</button>
          </div>
        )}
        <Plot
          data={[
            traceStyles.live.visible && {
              x: Array.isArray(fftData) ? fftX : [],
              y: Array.isArray(fftData) ? fftData : [],
              type: 'scatter',
              mode: 'lines',
              marker: { color: 'orange' },
              opacity: traceStyles.live.opacity,
              line: { shape: 'spline', width: traceStyles.live.width },
              showlegend: false,
            },
            traceStyles.max.visible && settings.showMaxTrace && {  // Conditionally add the Max FFT trace
            x: Array.isArray(fftMaxData)
              ? Array.from({ length: fftMaxData.length }, (_, index) => baseFreq + index * freqStep)
              : [],
            y: Array.isArray(fftMaxData) ? fftMaxData : [],
            type: 'scatter',
            mode: 'lines',
            marker: { color: 'green' },
            opacity: traceStyles.max.opacity,
            line: { shape: 'spline', width: traceStyles.max.width },
            showlegend: false, // Show this trace in the legend
            name: 'Max FFT Data', // Label for the legend
          },
          traceStyles.persist.visible && settings.showPersistanceTrace && {  // Conditionally add the Persistence Trace
            x: Array.isArray(persistanceData)
              ? Array.from(
                { length: persistanceData.length },
                (_, index) => baseFreq + index * freqStep,
              )
              : [],
            y: Array.isArray(persistanceData) ? persistanceData : [],
            type: 'scatter',
            mode: 'lines',
            fill: 'tozeroy', // Fill area under the trace
            fillcolor: 'rgba(0, 0, 255, 0.08)', // Blue fill with 20% transparency
            opacity: traceStyles.persist.opacity,
            line: {
              color: 'rgba(0, 0, 255, 0.1)', // Semi-transparent blue line
              shape: 'spline', // Smooth line shape
              width: traceStyles.persist.width,
            },
            showlegend: false, // Hide horizontal lines from the legend
          },
          markerPrimary && {
            x: [markerPrimary.x, markerPrimary.x],
            y: [minY, maxY],
            type: 'scatter',
            mode: 'lines',
            line: { color: '#63b3ff', width: 1.5, dash: 'dot' },
            showlegend: false,
            hoverinfo: 'skip',
          },
          markerSecondary && {
            x: [markerSecondary.x, markerSecondary.x],
            y: [minY, maxY],
            type: 'scatter',
            mode: 'lines',
            line: { color: '#b38bff', width: 1.5, dash: 'dot' },
            showlegend: false,
            hoverinfo: 'skip',
          },
          
          ...verticalLineTraces.map(trace => ({
            ...trace,
            showlegend: false, // Hide vertical lines from the legend
          })),
          ...horizontalLineTraces.map(trace => ({
            ...trace,
            showlegend: false, // Hide horizontal lines from the legend
          })),
        ].filter(Boolean)} // Filter out false/null traces
          layout={{
          title: '',
          xaxis: {
            title: 'Frequency (MHz)',
            color: 'white',
            gridcolor: '#444',
            tickvals: prevTickValsRef.current,
            ticktext: prevTickTextRef.current,
          },
          yaxis: {
            title: 'Amplitude (dB)',
            range: [minY, maxY],
            color: 'white',
            gridcolor: '#444',
            zeroline: false // Remove the white line across the 0 mark
          },
          margin: {
            l: 50,
            r: 50,
            b: 50,
            t: 50,
            pad: 4
          },
          autosize: true,  // Let Plotly auto size
            paper_bgcolor: '#000',
            plot_bgcolor: '#000',
            font: {
              color: 'white',
            },
            annotations: [
              ...peakAnnotations,
              ...peakNameAnnotations,
              ...(spectrumNoSignal ? [{
                xref: 'paper',
                yref: 'paper',
                x: 0.5,
                y: 0.5,
                text: '[NO SIGNAL]',
                showarrow: false,
                font: { size: 20, color: '#ff8080' },
                bgcolor: 'rgba(0,0,0,0.55)',
                bordercolor: 'rgba(255,128,128,0.75)',
                borderwidth: 1,
                borderpad: 5,
              }] : []),
            ].filter(Boolean),
          }}
          style={{ width: `${plotWidth}vw`, height: showWaterfall ? '42vh' : '78vh' }}
          onClick={handlePlotClick}
          onInitialized={(figure, graphDiv) => {
            spectrumPlotRef.current = graphDiv;
          }}
          onUpdate={(figure, graphDiv) => {
            spectrumPlotRef.current = graphDiv;
          }}
        />
      </div>
      {showWaterfall && (
        <div style={{ position: 'relative' }}>
          <div style={waterfallDrawerContainerStyle}>
            <button
              type="button"
              style={waterfallToggleButtonStyle(showWaterfallToolbar)}
              onClick={() => setShowWaterfallToolbar((prev) => !prev)}
              title={showWaterfallToolbar ? 'Hide waterfall tools' : 'Show waterfall tools'}
            >
              {showWaterfallToolbar ? '>' : '< WF'}
            </button>
            <div style={waterfallToolbarStyle(showWaterfallToolbar)}>
              <label style={quickTuneLabelStyle}>Palette</label>
              <select
                value={waterfallColorScale}
                onChange={(e) => setWaterfallColorScale(e.target.value)}
                style={quickTuneSelectStyle}
              >
                {['Jet', 'Viridis', 'Cividis', 'Turbo', 'Hot', 'Portland'].map((palette) => (
                  <option key={palette} value={palette}>{palette}</option>
                ))}
              </select>
              <label style={quickTuneLabelStyle}>Range</label>
              <input
                type="range"
                min="20"
                max="120"
                value={waterfallDbWindow}
                onChange={(e) => setWaterfallDbWindow(toFinite(e.target.value, 80))}
              />
              <label style={quickTuneLabelStyle}>Contrast</label>
              <input
                type="range"
                min="-40"
                max="40"
                value={waterfallLevelOffset}
                onChange={(e) => setWaterfallLevelOffset(toFinite(e.target.value, 0))}
              />
              <label style={quickTuneLabelStyle}>Speed</label>
              <input
                type="range"
                min="50"
                max="1000"
                step="10"
                value={toFinite(settings.updateInterval, 500)}
                onChange={(e) => {
                  const nextInterval = toFinite(e.target.value, 500);
                  setSettings({ ...settings, updateInterval: nextInterval });
                }}
                onMouseUp={(e) => {
                  const nextInterval = toFinite(e.target.value, 500);
                  pushSettings({ updateInterval: nextInterval });
                }}
              />
              <label style={quickTuneLabelStyle}>Bins</label>
              <select
                value={toFinite(settings.waterfallBinCount, 2048)}
                onChange={(e) => {
                  const bins = toFinite(e.target.value, 2048);
                  pushSettings({ waterfallBinCount: bins });
                }}
                style={quickTuneSelectStyle}
              >
                {[512, 1024, 2048, 3072, 4096].map((bins) => (
                  <option key={bins} value={bins}>{bins}</option>
                ))}
              </select>
              <label style={quickTuneLabelStyle}>Duration</label>
              <select
                value={toFinite(settings.waterfallSamples, 100)}
                onChange={(e) => {
                  const samples = toFinite(e.target.value, 100);
                  pushSettings({ waterfallSamples: samples });
                }}
                style={quickTuneSelectStyle}
              >
                {[100, 200, 400, 800, 1200, 1600].map((samples) => (
                  <option key={samples} value={samples}>{samples}</option>
                ))}
              </select>
              <button type="button" style={quickTuneButtonStyle} onClick={() => setWaterfallData([])}>Clear</button>
            </div>
          </div>
          <Plot
            data={[
              {
                x: waterfallX,
                z: renderedWaterfallData,
                type: 'heatmap',
                colorscale: waterfallColorScale,
                zsmooth: false,
                zmin: waterfallZMin,
                zmax: waterfallZMax,
                showscale: false, // Remove the color scale
              },
            ]}
            layout={{
              title: '',
              xaxis: {
                title: 'Frequency (MHz)',
                color: 'white',
                gridcolor: '#444',
                zeroline: false, // Remove the white line across the 0 mark
                tickvals: prevTickValsRef.current,
                ticktext: prevTickTextRef.current,
              },
              yaxis: {
                title: 'Samples',
                color: 'white',
                gridcolor: '#444',
              },
              margin: {
                l: 50,
                r: 50,
                b: 50,
                t: 0,
                pad: 4
              },
              autosize: true,  // Let Plotly auto size
              paper_bgcolor: '#000',
              plot_bgcolor: '#000',
              font: {
                color: 'white',
              },
              annotations: waterfallNoSignal
                ? [{
                  xref: 'paper',
                  yref: 'paper',
                  x: 0.5,
                  y: 0.5,
                  text: '[NO SIGNAL]',
                  showarrow: false,
                  font: { size: 18, color: '#ff8080' },
                  bgcolor: 'rgba(0,0,0,0.45)',
                  bordercolor: 'rgba(255,128,128,0.75)',
                  borderwidth: 1,
                  borderpad: 4,
                }]
                : [],
            }}
            config={{
              displayModeBar: false, // Hide the mode bar
            }}
            style={{ width: `${plotWidth}vw` }}
            onRelayout={handleRelayout} // Attach the relayout event handler
          />
        </div>
      )}
      {contextMenu && (
        <div
          style={{
            position: 'fixed',
            top: contextMenu.y,
            left: contextMenu.x,
            background: '#121212',
            border: '1px solid #444',
            borderRadius: 8,
            padding: 6,
            zIndex: 9999,
            minWidth: 200,
            boxShadow: '0 8px 24px rgba(0, 0, 0, 0.45)',
          }}
          onClick={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            style={menuButtonStyle}
            onClick={() => clearTraceData('max')}
          >
            Clear Max FFT Data
          </button>
          <button
            type="button"
            style={menuButtonStyle}
            onClick={() => clearTraceData('persist')}
          >
            Clear Persistence Data
          </button>
          <button
            type="button"
            style={menuButtonStyle}
            onClick={() => clearTraceData('all')}
          >
            Clear All FFT Traces
          </button>
          <button
            type="button"
            style={menuButtonStyle}
            onClick={clearMarkersFromMenu}
          >
            Clear Markers
          </button>
        </div>
      )}
    </div>
  );
};

const menuButtonStyle = {
  display: 'block',
  width: '100%',
  background: '#1f1f1f',
  color: '#f0f0f0',
  border: '1px solid #333',
  borderRadius: 6,
  padding: '8px 10px',
  textAlign: 'left',
  cursor: 'pointer',
  marginBottom: 6,
};

const quickTuneBarStyle = {
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  marginBottom: 6,
  padding: '6px 8px',
  border: '1px solid #222',
  borderRadius: 8,
  background: '#0b0b0b',
  flexWrap: 'wrap',
};

const waterfallDrawerContainerStyle = {
  position: 'absolute',
  top: '50%',
  right: 0,
  transform: 'translateY(-50%)',
  zIndex: 11,
  display: 'flex',
  alignItems: 'stretch',
  gap: 0,
};

const waterfallToolbarStyle = (open) => ({
  display: 'flex',
  flexDirection: 'column',
  alignItems: 'stretch',
  gap: 6,
  padding: open ? '6px 8px' : '0',
  borderRadius: 8,
  border: open ? '1px solid #333' : '1px solid transparent',
  background: 'rgba(12, 12, 12, 0.92)',
  backdropFilter: 'blur(2px)',
  minWidth: open ? 180 : 0,
  maxWidth: open ? 180 : 0,
  width: open ? 180 : 0,
  opacity: open ? 1 : 0,
  overflow: 'hidden',
  transition: 'max-width 180ms ease, width 180ms ease, opacity 120ms ease, padding 180ms ease, border-color 180ms ease',
});

const waterfallToggleButtonStyle = (open) => ({
  background: '#1b1b1b',
  color: '#f0f0f0',
  border: '1px solid #333',
  borderRight: open ? '1px solid #333' : '1px solid #444',
  borderRadius: open ? '6px 0 0 6px' : '6px 0 0 6px',
  minWidth: 42,
  width: 42,
  padding: '8px 4px',
  cursor: 'pointer',
  alignSelf: 'center',
  height: 40,
});

const quickTuneLabelStyle = {
  color: '#ddd',
  fontSize: 12,
};

const quickTuneInputStyle = {
  width: 96,
  background: '#171717',
  color: '#f2f2f2',
  border: '1px solid #333',
  borderRadius: 6,
  padding: '4px 6px',
};

const quickTuneSelectStyle = {
  background: '#171717',
  color: '#f2f2f2',
  border: '1px solid #333',
  borderRadius: 6,
  padding: '4px 6px',
};

const quickTuneButtonStyle = {
  background: '#1f1f1f',
  color: '#f0f0f0',
  border: '1px solid #333',
  borderRadius: 6,
  padding: '6px 10px',
  cursor: 'pointer',
};

const traceToolbarStyle = {
  position: 'absolute',
  top: 8,
  right: 10,
  zIndex: 10,
  display: 'flex',
  alignItems: 'center',
  gap: 8,
  padding: '6px 8px',
  borderRadius: 8,
  border: '1px solid #333',
  background: 'rgba(12, 12, 12, 0.85)',
};

const markerReadoutStyle = {
  position: 'absolute',
  top: 42,
  right: 10,
  zIndex: 10,
  display: 'flex',
  flexDirection: 'column',
  gap: 4,
  padding: '8px 10px',
  borderRadius: 8,
  border: '1px solid #2a2a2a',
  background: 'rgba(9, 9, 9, 0.88)',
};

export default ChartComponent;
