import React, { useEffect, useMemo, useState, useRef } from 'react';
import axios from 'axios';
import Plot from 'react-plotly.js';
import GpuSpectrum from './GpuSpectrum';
import GpuWaterfall from './GpuWaterfall';
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
  const settingsPostConfig = {
    headers: { 'Content-Type': 'application/json' },
    timeout: 8000,
  };
  const toFinite = (value, fallback) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };
  const maxWaterfallSamples = 375;
  const chartRendererMode = String(process.env.REACT_APP_SDR_SHARK_CHART_RENDERER || 'gpu').trim().toLowerCase();
  const useGpuCharts = ['gpu', 'webgl', 'opengl'].includes(chartRendererMode);
  const signalClassificationOverlaysEnabled = false;
  const signalMarkerHoldMs = 15000;
  const plotMargin = {
    l: 50,
    r: 50,
    b: 50,
    t: 50,
    pad: 4,
    autoexpand: false,
  };
  const waterfallMargin = {
    ...plotMargin,
    t: 0,
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
  const [heldClassifiedSignalMarkers, setHeldClassifiedSignalMarkers] = useState([]);
  const [traceStyles, setTraceStyles] = useState({
    live: { visible: true, width: 1, opacity: 1.0 },
    max: { visible: true, width: 1, opacity: 0.9 },
    persist: { visible: true, width: 1, opacity: 0.25 },
  });
  const [gpuRenderer, setGpuRenderer] = useState({
    mode: useGpuCharts ? 'GPU' : 'CPU',
    detail: useGpuCharts ? 'WebGL initializing' : 'Plotly scattergl',
  });
  const renderEngine = useGpuCharts ? (gpuRenderer.mode || 'CPU') : 'GPU';
  const lastFrameTsRef = useRef(null);
  const lastDataTsRef = useRef(Date.now());
  const droppedFramesRef = useRef(0);
  const lastMainSeqRef = useRef(null);
  const staleSeqCountRef = useRef(0);
  const lastFftSnapshotRef = useRef([]);
  const dataRequestInFlightRef = useRef(false);
  const queuedSsePayloadRef = useRef(null);
  const sseHealthyRef = useRef(false);
  const sseLastMessageAtRef = useRef(0);
  const spectrumPlotRef = useRef(null);
  const waterfallEnabledAtRef = useRef(Date.now());
  const startupAutoscaleDoneRef = useRef(false);
  const startupAutoscaleAttemptsRef = useRef(0);
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

  const usableFftValues = (values) => (
    Array.isArray(values)
      ? values.filter((value) => Number.isFinite(value) && value > -220 && value < 200)
      : []
  );

  const rangeFromFftValues = (values) => {
    const usable = usableFftValues(values);
    if (usable.length < 32) return null;
    const sorted = [...usable].sort((a, b) => a - b);
    const p20 = sorted[Math.floor(sorted.length * 0.2)];
    const p99 = sorted[Math.floor(sorted.length * 0.99)];
    const peak = sorted[sorted.length - 1];
    if (![p20, p99, peak].every(Number.isFinite)) return null;
    if ((peak - p20) < 1.5) return null;
    const nextMin = Math.floor((p20 - 18) / 5) * 5;
    const nextMax = Math.ceil((Math.max(p99 + 12, peak + 6)) / 5) * 5;
    if (!Number.isFinite(nextMin) || !Number.isFinite(nextMax) || nextMax <= nextMin) return null;
    return {
      min: Math.max(-180, nextMin),
      max: Math.min(120, nextMax),
    };
  };

  const signalFootprintLabel = (protocol) => {
    if (protocol === 'BTC') return '~1 MHz hop';
    if (protocol === 'BTLE') return '~2 MHz ch';
    if (protocol === 'WiFi') return '~22 MHz ch';
    return '';
  };

  const classifiedMarkerKey = (marker) => (
    `${marker.protocol || 'signal'}-${Math.round(Number(marker.freqHz || 0) / 500000)}-${marker.label || ''}`
  );

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
      if (dataRequestInFlightRef.current) {
        droppedFramesRef.current += 1;
        return;
      }
      dataRequestInFlightRef.current = true;
      const start = performance.now();
      try {
        const queuedPayload = queuedSsePayloadRef.current;
        let data = null;
        if (queuedPayload) {
          queuedSsePayloadRef.current = null;
          data = queuedPayload;
        } else if (sseHealthyRef.current && Date.now() - sseLastMessageAtRef.current < 2000) {
          return;
        } else {
          const response = await axios.get('/api/data', {
            params: {
              source: 'main',
              waterfall: showWaterfall ? 'derive' : 'none',
              _ts: Date.now(),
            },
          });
          data = response.data;
        }
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
        const safeWaterfallSamples = Math.max(1, Math.min(maxWaterfallSamples, toFinite(settings.waterfallSamples, 200)));
        const safeInterval = Math.max(30, toFinite(settings.updateInterval, 500));
        // Faster UI motion at lower update intervals without increasing backend load.
        const waterfallBurst = Math.max(1, Math.min(4, Math.round(120 / safeInterval)));
        const noSignalRow = (bins) => Array.from({ length: bins }, () => -255);
        const appendRowBurst = (prev, row, bins) => {
          if (!Array.isArray(row) || row.length === 0) return prev;
          const targetBins = Math.max(1, bins);
          const normalized = row.length === targetBins ? row : resampleRow(row, targetBins);
          const next = [...prev];
          for (let i = 0; i < waterfallBurst; i += 1) {
            next.push([...normalized]);
          }
          return next.slice(-safeWaterfallSamples);
        };
        if (showWaterfall) {
          if (sanitizedWaterfallData.length > 0 && data?.waterfallMode !== 'derive' && (frameAdvanced || fftChanged)) {
            const latestServerRow = sanitizedWaterfallData[sanitizedWaterfallData.length - 1];
            const targetBins = Math.max(64, Math.min(8192, toFinite(settings.waterfallBinCount, latestServerRow?.length || 2048)));
            setWaterfallData((prev) => appendRowBurst(prev, latestServerRow, targetBins));
          } else if (sanitizedFftData.length > 0 && (frameAdvanced || fftChanged)) {
            const targetBins = data?.waterfallMode === 'derive'
              ? sanitizedFftData.length
              : Math.max(64, Math.min(8192, toFinite(settings.waterfallBinCount, sanitizedFftData.length)));
            const row = resampleRow(sanitizedFftData, targetBins);
            setWaterfallData((prev) => appendRowBurst(prev, row, targetBins));
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
              return appendRowBurst(prev, noSignalRow(bins), bins);
            });
          }
        } else if (waterfallData.length > 0) {
          setWaterfallData([]);
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
            renderEngine,
            peaks: telemetryPeaks.slice(0, 16),
            bluetooth: data?.bluetooth || null,
            fm: data?.fm || null,
            wifi: data?.wifi || null,
            zigbee: data?.zigbee || null,
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
        const safeWaterfallSamples = Math.max(1, Math.min(maxWaterfallSamples, toFinite(settings.waterfallSamples, 200)));
        if (showWaterfall) {
          setWaterfallData((prev) => {
            const bins = (Array.isArray(prev[0]) && prev[0].length > 0)
              ? prev[0].length
              : Math.max(64, Math.min(8192, toFinite(settings.waterfallBinCount, 2048)));
            const row = Array.from({ length: bins }, () => -255);
            return [...prev, row].slice(-safeWaterfallSamples);
          });
        } else {
          setWaterfallData([]);
        }
        if (typeof onTelemetryUpdate === 'function') {
          onTelemetryUpdate((prev) => ({
            ...(prev || {}),
            droppedFrames: droppedFramesRef.current,
            staleMs: Date.now() - lastDataTsRef.current,
          }));
        }
      } finally {
        dataRequestInFlightRef.current = false;
      }
    };
    const safeInterval = Math.max(50, toFinite(settings.updateInterval, 500));
    let eventSource = null;
    if (typeof window !== 'undefined' && typeof window.EventSource === 'function') {
      const streamParams = new URLSearchParams({
        source: 'main',
        waterfall: showWaterfall ? 'derive' : 'none',
        interval: String(safeInterval),
      });
      eventSource = new window.EventSource(`/api/data_stream?${streamParams.toString()}`);
      eventSource.addEventListener('frame', (event) => {
        try {
          queuedSsePayloadRef.current = JSON.parse(event.data);
          sseHealthyRef.current = true;
          sseLastMessageAtRef.current = Date.now();
          fetchData();
        } catch (error) {
          console.error('Error parsing stream data:', error);
        }
      });
      eventSource.onerror = () => {
        sseHealthyRef.current = false;
      };
    }
    fetchData();
    const interval = setInterval(fetchData, safeInterval);
    return () => {
      clearInterval(interval);
      if (eventSource) {
        eventSource.close();
      }
      sseHealthyRef.current = false;
      queuedSsePayloadRef.current = null;
    };
  }, [settings.updateInterval, setSweepSettings, settings.frequency, settings.sampleRate, settings.sdr, showWaterfall, onTelemetryUpdate]);



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
    if (!showWaterfall) {
      setWaterfallData([]);
      setWaterfallNoSignal(false);
      return;
    }
    waterfallEnabledAtRef.current = Date.now();
    setWaterfallData([]);
  }, [showWaterfall]);

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
        const positionRatio = (freq - startFreq) / Math.max(1, endFreq - startFreq);
        const edgeAnchor = positionRatio > 0.82 ? 'right' : (positionRatio < 0.18 ? 'left' : 'center');
        return {
          x: freq,
          y: parseFloat(power),
          xref: 'x',
          yref: 'y',
          text: `${(freq / 1e6).toFixed(2)} MHz<br><span style="color:${powerColor}">${power} dB</span>`,
          showarrow: true,
          arrowhead: 2,
          ax: edgeAnchor === 'right' ? -36 : (edgeAnchor === 'left' ? 36 : 0),
          ay: -40,
          xanchor: edgeAnchor,
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
        const labelLower = label.toLowerCase();
        const annotationProtocol = labelLower.includes('wifi')
          ? 'WiFi'
          : (labelLower.includes('classic') || labelLower.includes('btc') ? 'BTC' : (labelLower.includes('bluetooth') || labelLower.includes('ble') ? 'BTLE' : ''));
        const footprintLabel = signalFootprintLabel(annotationProtocol);
        const channel = String(top.channel || '').trim();
        const tag = channel && channel !== 'N/A' ? `${label} ${channel}` : label;
        const text = footprintLabel ? `${tag}<br>${footprintLabel}` : tag;
        const startFreq = baseFreq;
        const endFreq = baseFreq + freqStep * (fftData.length - 1);
        const xHz = freqMHz * 1e6;
        const positionRatio = (xHz - startFreq) / Math.max(1, endFreq - startFreq);
        const edgeAnchor = positionRatio > 0.82 ? 'right' : (positionRatio < 0.18 ? 'left' : 'center');

        return {
          x: xHz,
          y: power + 4 + (idx % 2) * 2,
          xref: 'x',
          yref: 'y',
          text,
          showarrow: false,
          xanchor: edgeAnchor,
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

  const getClassifiedSignalMarkers = (peaks) => {
    if (!Array.isArray(peaks) || peaks.length === 0) return [];

    return peaks
      .flatMap((peak) => {
        const classes = Array.isArray(peak?.classification) ? peak.classification : [];
        const absFreq = Number(peak?.absolute_frequency);
        const relFreq = Number(peak?.frequency);
        const freqMHz = Number.isFinite(absFreq)
          ? absFreq
          : (Number.isFinite(relFreq) ? safeFrequencyMHz + relFreq : NaN);
        if (!Number.isFinite(freqMHz)) return [];

        const matched = classes
          .map((item) => {
            const classLabel = String(item?.label || '');
            const classLabelLower = classLabel.toLowerCase();
            const isWifi = classLabelLower.includes('wifi');
            const isBluetooth = classLabelLower.includes('bluetooth') || classLabelLower.includes('ble') || classLabelLower.includes('btc');
            if (!isWifi && !isBluetooth) return null;

            const protocol = isWifi
              ? 'WiFi'
              : (classLabelLower.includes('classic') || classLabelLower.includes('btc') ? 'BTC' : 'BTLE');
            const channel = String(item?.channel || '').replace(/^Channel\s+/i, 'Ch ');
            const fallbackBandwidthMHz = protocol === 'WiFi' ? 22 : (protocol === 'BTLE' ? 2 : 1);
            const bandwidthMHz = protocol === 'WiFi'
              ? Math.max(8, toFinite(item?.bandwidth ?? peak?.bandwidth, fallbackBandwidthMHz))
              : fallbackBandwidthMHz;
            const footprintLabel = signalFootprintLabel(protocol);

            return {
              freqHz: freqMHz * 1e6,
              bandwidthHz: bandwidthMHz * 1e6,
              power: Number(peak?.peak_power),
              protocol,
              label: channel && channel !== 'N/A' ? `${protocol} ${channel}` : protocol,
              footprintLabel,
              color: protocol === 'WiFi' ? '#ff9f6e' : (protocol === 'BTC' ? '#ffd166' : '#7cf7d4'),
              fillColor: protocol === 'WiFi'
                ? 'rgba(255, 159, 110, 0.12)'
                : (protocol === 'BTC' ? 'rgba(255, 209, 102, 0.14)' : 'rgba(124, 247, 212, 0.14)'),
              bgColor: protocol === 'WiFi'
                ? 'rgba(59, 25, 5, 0.86)'
                : (protocol === 'BTC' ? 'rgba(56, 37, 0, 0.86)' : 'rgba(0, 43, 48, 0.86)'),
              minScore: protocol === 'WiFi' ? 4 : 6,
              minPeakOffset: protocol === 'WiFi' ? 5 : 8,
              maxMarks: protocol === 'WiFi' ? 2 : 4,
              yPadRatio: protocol === 'WiFi' ? 0.035 : 0.018,
              xPadHz: protocol === 'WiFi'
                ? (bandwidthMHz * 1e6) / 2
                : (bandwidthMHz * 1e6) / 2,
            };
          })
          .filter(Boolean);

        const hasWifi = matched.some((item) => item.protocol === 'WiFi');
        const hasBluetooth = matched.some((item) => item.protocol === 'BTC' || item.protocol === 'BTLE');
        const wifiCenters = [2412, 2417, 2422, 2427, 2432, 2437, 2442, 2447, 2452, 2457, 2462, 2467, 2472, 2484];
        const nearestWifi = wifiCenters
          .map((center, idx) => ({ center, channel: idx + 1, delta: Math.abs(freqMHz - center) }))
          .sort((a, b) => a.delta - b.delta)[0];
        const estimatedBandwidthMHz = toFinite(peak?.bandwidth, 0);
        if (!hasWifi && !hasBluetooth && nearestWifi && nearestWifi.delta <= 3 && estimatedBandwidthMHz >= 12) {
          matched.push({
            freqHz: nearestWifi.center * 1e6,
            bandwidthHz: 22e6,
            power: Number(peak?.peak_power),
            protocol: 'WiFi',
            label: `WiFi Ch ${nearestWifi.channel}`,
            footprintLabel: signalFootprintLabel('WiFi'),
            color: '#ff9f6e',
            fillColor: 'rgba(255, 159, 110, 0.12)',
            bgColor: 'rgba(59, 25, 5, 0.86)',
            minScore: 4,
            minPeakOffset: 5,
            maxMarks: 2,
            yPadRatio: 0.035,
            xPadHz: 11e6,
          });
        }

        return matched;
      })
      .filter(Boolean)
      .sort((a, b) => (Number(b.power) || -999) - (Number(a.power) || -999))
      .slice(0, 10);
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
  const xAxisRangeHz = useMemo(
    () => [baseFreq, baseFreq + safeBandwidthHz],
    [baseFreq, safeBandwidthHz],
  );
  const freqStep = safeBandwidthHz / safeBins;
  const waterfallFreqStep = safeBandwidthHz / safeWaterfallBins;
  const fftX = useMemo(
    () => Array.from({ length: safeBins }, (_, index) => baseFreq + index * freqStep),
    [safeBins, baseFreq, freqStep],
  );
  const maxFftX = useMemo(
    () => Array.from({ length: fftMaxData.length }, (_, index) => baseFreq + index * freqStep),
    [fftMaxData.length, baseFreq, freqStep],
  );
  const persistenceX = useMemo(
    () => Array.from({ length: persistanceData.length }, (_, index) => baseFreq + index * freqStep),
    [persistanceData.length, baseFreq, freqStep],
  );
  const waterfallX = useMemo(
    () => Array.from(
      { length: safeWaterfallBins },
      (_, index) => baseFreq + index * waterfallFreqStep,
    ),
    [safeWaterfallBins, baseFreq, waterfallFreqStep],
  );
  const waterfallRows = waterfallData.length;
  const waterfallCols = safeWaterfallBins;
  const sharedPlotStyle = {
    width: `${plotWidth}vw`,
    maxWidth: '100%',
  };
  const cellCount = waterfallRows * waterfallCols;
  const requestedWaterfallRows = Math.max(1, Math.min(maxWaterfallSamples, toFinite(settings.waterfallSamples, 200)));
  const maxWaterfallCells = Math.max(1800000, requestedWaterfallRows * waterfallCols);
  const rowStride = cellCount > maxWaterfallCells ? Math.ceil(cellCount / maxWaterfallCells) : 1;
  const renderedWaterfallData = rowStride > 1
    ? waterfallData.filter((_, idx) => idx % rowStride === 0)
    : waterfallData;
  const renderedWaterfallCellCount = renderedWaterfallData.length * waterfallCols;
  const maxClassifiedWaterfallCells = 4_000_000;
  const waterfallSmooth = cellCount <= 1800000 ? 'best' : false;
  const waterfallWarmup = showWaterfall && (Date.now() - waterfallEnabledAtRef.current) < 1200;
  const peakAnnotations = generateAnnotations(peaks, baseFreq, freqStep);
  const peakNameAnnotations = signalClassificationOverlaysEnabled ? generateSignalNameAnnotations(peaks) : [];
  const classifiedSignalMarkers = useMemo(
    () => (signalClassificationOverlaysEnabled ? getClassifiedSignalMarkers(peaks) : []),
    [peaks, safeFrequencyMHz],
  );
  useEffect(() => {
    const now = Date.now();
    if (!classifiedSignalMarkers.length) return;
    setHeldClassifiedSignalMarkers((prev) => {
      const byKey = new Map();
      prev
        .filter((marker) => now - Number(marker.seenAtMs || 0) <= signalMarkerHoldMs)
        .forEach((marker) => byKey.set(classifiedMarkerKey(marker), marker));
      classifiedSignalMarkers.forEach((marker) => {
        const key = classifiedMarkerKey(marker);
        const existing = byKey.get(key) || {};
        byKey.set(key, {
          ...existing,
          ...marker,
          seenAtMs: now,
        });
      });
      return Array.from(byKey.values())
        .sort((a, b) => Number(b.seenAtMs || 0) - Number(a.seenAtMs || 0))
        .slice(0, 16);
    });
  }, [classifiedSignalMarkers]);
  useEffect(() => {
    const interval = setInterval(() => {
      const now = Date.now();
      setHeldClassifiedSignalMarkers((prev) => (
        prev.filter((marker) => now - Number(marker.seenAtMs || 0) <= signalMarkerHoldMs)
      ));
    }, 1000);
    return () => clearInterval(interval);
  }, []);
  const activeClassifiedSignalMarkers = heldClassifiedSignalMarkers.length
    ? heldClassifiedSignalMarkers
    : classifiedSignalMarkers;
  const waterfallCenterDb = ((Number(minY) + Number(maxY)) / 2) + waterfallLevelOffset;
  const waterfallZMin = waterfallCenterDb - (waterfallDbWindow / 2);
  const waterfallZMax = waterfallCenterDb + (waterfallDbWindow / 2);
  const classifiedWaterfallMarks = useMemo(() => {
    if (
      !showWaterfall ||
      waterfallWarmup ||
      renderedWaterfallCellCount > maxClassifiedWaterfallCells ||
      !activeClassifiedSignalMarkers.length ||
      !renderedWaterfallData.length ||
      waterfallFreqStep <= 0
    ) {
      return [];
    }

    const rowCount = renderedWaterfallData.length;
    return activeClassifiedSignalMarkers.flatMap((marker) => {
      const centerBin = Math.round((marker.freqHz - baseFreq) / waterfallFreqStep);
      if (centerBin < 0 || centerBin >= safeWaterfallBins) return [];

      const halfBins = Math.max(2, Math.ceil((marker.bandwidthHz / 2) / waterfallFreqStep));
      const startBin = Math.max(0, centerBin - halfBins);
      const endBin = Math.min(safeWaterfallBins - 1, centerBin + halfBins);
      const candidates = renderedWaterfallData
        .map((row, rowIdx) => {
          if (!Array.isArray(row) || row.length === 0) return null;
          const values = row
            .slice(startBin, endBin + 1)
            .filter((value) => Number.isFinite(Number(value)))
            .map(Number);
          if (!values.length) return null;
          const localPeak = Math.max(...values);
          const sampled = [];
          const sampleStep = Math.max(1, Math.floor(row.length / 96));
          for (let i = 0; i < row.length; i += sampleStep) {
            const value = Number(row[i]);
            if (Number.isFinite(value)) sampled.push(value);
          }
          sampled.sort((a, b) => a - b);
          const noise = sampled[Math.floor(sampled.length * 0.55)] ?? waterfallZMin;
          const score = localPeak - noise;
          return { rowIdx, score, localPeak };
        })
        .filter((item) => item && item.score >= marker.minScore && item.localPeak >= waterfallZMin + marker.minPeakOffset)
        .sort((a, b) => b.score - a.score);

      const picked = [];
      for (const candidate of candidates) {
        const minRowSpacing = marker.protocol === 'WiFi' ? 18 : 8;
        if (picked.every((existing) => Math.abs(existing.rowIdx - candidate.rowIdx) > minRowSpacing)) {
          picked.push(candidate);
        }
        if (picked.length >= marker.maxMarks) break;
      }

      return picked.map((candidate) => {
        const yPad = Math.max(3, Math.round(rowCount * marker.yPadRatio));
        const xPad = Math.max(waterfallFreqStep * 2, marker.xPadHz);
        return {
          ...marker,
          x0: marker.freqHz - xPad,
          x1: marker.freqHz + xPad,
          y0: Math.max(0, candidate.rowIdx - yPad),
          y1: Math.min(requestedWaterfallRows, candidate.rowIdx + yPad),
          score: candidate.score,
        };
      });
    }).slice(0, 12);
  }, [
    activeClassifiedSignalMarkers,
    renderedWaterfallData,
    baseFreq,
    safeWaterfallBins,
    waterfallFreqStep,
    requestedWaterfallRows,
    waterfallZMin,
    showWaterfall,
    waterfallWarmup,
    renderedWaterfallCellCount,
  ]);
  const classifiedWaterfallTraces = classifiedWaterfallMarks.map((mark, idx) => ({
    x: [mark.x0, mark.x1, mark.x1, mark.x0, mark.x0],
    y: [mark.y0, mark.y0, mark.y1, mark.y1, mark.y0],
    type: 'scatter',
    mode: 'lines',
    line: { color: mark.color, width: 2 },
    fill: 'toself',
    fillcolor: mark.fillColor,
    hovertemplate: `${mark.label}${mark.footprintLabel ? `<br>${mark.footprintLabel}` : ''}<br>%{x:.0f} Hz<extra></extra>`,
    showlegend: idx === 0,
    name: 'Signal mark',
  }));
  const classifiedWaterfallAnnotations = classifiedWaterfallMarks.map((mark, idx) => ({
    x: mark.freqHz,
    y: mark.y1 + 2,
    xref: 'x',
    yref: 'y',
    text: `${mark.label}${mark.footprintLabel ? `<br>${mark.footprintLabel}` : ''}`,
    showarrow: false,
    bgcolor: mark.bgColor,
    bordercolor: mark.color,
    borderwidth: 1,
    borderpad: 2,
    font: { size: 10, color: '#f5fffb' },
    align: 'center',
    ay: idx,
  }));

  useEffect(() => {
    if (!Array.isArray(fftData) || fftData.length === 0) {
      return;
    }
    if (typeof setMinY !== 'function' || typeof setMaxY !== 'function') {
      return;
    }
    const range = rangeFromFftValues(fftData);
    if (!range) {
      if (!startupAutoscaleDoneRef.current) {
        startupAutoscaleAttemptsRef.current += 1;
      }
      return;
    }
    if (autoscaleMode === 'manual') {
      if (startupAutoscaleDoneRef.current) {
        return;
      }
      startupAutoscaleDoneRef.current = true;
      setMinY(range.min);
      setMaxY(range.max);
      return;
    }
    if (autoscaleMode === 'hold') {
      return;
    }
    const sorted = usableFftValues(fftData).sort((a, b) => a - b);
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
      const response = await axios.post('/api/update_settings', nextSettings, settingsPostConfig);
      if (response?.data?.success === false) {
        throw new Error(response.data.error || 'Settings update failed');
      }
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
    applyQuickTune(toFinite(quickCenterMHz, safeFrequencyMHz) + deltaMHz, quickSpanMHz);
  };

  const handleQuickTuneKeyDown = (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      applyQuickTune(quickCenterMHz, quickSpanMHz);
      event.currentTarget.blur();
    } else if (event.key === 'Escape') {
      event.preventDefault();
      setQuickCenterMHz(safeFrequencyMHz);
      setQuickSpanMHz(safeSampleRateMHz);
      event.currentTarget.blur();
    }
  };

  const applyYLimits = (nextMin, nextMax) => {
    const yMin = toFinite(nextMin, minY);
    const yMax = toFinite(nextMax, maxY);
    if (!Number.isFinite(yMin) || !Number.isFinite(yMax) || yMax <= yMin) return;
    setAutoscaleMode('manual');
    setMinY(yMin);
    setMaxY(yMax);
  };

  const shiftYLimits = (deltaDb) => {
    applyYLimits(Number(minY) + deltaDb, Number(maxY) + deltaDb);
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

  const handleGpuSpectrumPick = ({ x, y, shiftKey }) => {
    const marker = {
      x: Number(x),
      y: Number(y),
    };
    if (!Number.isFinite(marker.x) || !Number.isFinite(marker.y)) return;
    if (shiftKey && markerPrimary) {
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
          onChange={(e) => setQuickCenterMHz(e.target.value)}
          onKeyDown={handleQuickTuneKeyDown}
          onBlur={() => applyQuickTune(quickCenterMHz, quickSpanMHz)}
          style={quickTuneInputStyle}
        />
        <label style={quickTuneLabelStyle}>Span (MHz)</label>
        <input
          type="number"
          step="0.1"
          value={quickSpanMHz}
          onChange={(e) => setQuickSpanMHz(e.target.value)}
          onKeyDown={handleQuickTuneKeyDown}
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
        <label style={{ ...quickTuneLabelStyle, marginLeft: 8 }}>Y Min</label>
        <input
          type="number"
          step="5"
          value={Number.isFinite(Number(minY)) ? Number(minY).toFixed(0) : minY}
          onChange={(e) => applyYLimits(e.target.value, maxY)}
          style={{ ...quickTuneInputStyle, width: 66 }}
        />
        <label style={quickTuneLabelStyle}>Y Max</label>
        <input
          type="number"
          step="5"
          value={Number.isFinite(Number(maxY)) ? Number(maxY).toFixed(0) : maxY}
          onChange={(e) => applyYLimits(minY, e.target.value)}
          style={{ ...quickTuneInputStyle, width: 66 }}
        />
        <button type="button" style={quickTuneButtonStyle} onClick={() => shiftYLimits(-5)}>-5 dB</button>
        <button type="button" style={quickTuneButtonStyle} onClick={() => shiftYLimits(5)}>+5 dB</button>
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
                waterfallEnabledAtRef.current = Date.now();
                setWaterfallData([]);
                setWaterfallNoSignal(false);
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
        {useGpuCharts ? (
          <GpuSpectrum
            data={traceStyles.live.visible ? fftData : []}
            minDb={minY}
            maxDb={maxY}
            palette={waterfallColorScale}
            freqStartHz={baseFreq}
            freqStopHz={baseFreq + safeBandwidthHz}
            margin={plotMargin}
            width={`${plotWidth}vw`}
            height={showWaterfall ? '42vh' : '78vh'}
            opacity={traceStyles.live.opacity}
            widthScale={traceStyles.live.width}
            markers={[markerPrimary, markerSecondary]}
            verticalLines={verticalLines}
            horizontalLines={horizontalLines}
            noSignal={spectrumNoSignal}
            onPick={handleGpuSpectrumPick}
            onRendererChange={setGpuRenderer}
          />
        ) : (
          <Plot
            data={[
            traceStyles.live.visible && {
              x: Array.isArray(fftData) ? fftX : [],
              y: Array.isArray(fftData) ? fftData : [],
              type: 'scattergl',
              mode: 'lines',
              marker: { color: 'orange' },
              opacity: traceStyles.live.opacity,
              line: { shape: 'linear', width: traceStyles.live.width },
              showlegend: false,
            },
            traceStyles.max.visible && settings.showMaxTrace && {  // Conditionally add the Max FFT trace
            x: Array.isArray(fftMaxData) ? maxFftX : [],
            y: Array.isArray(fftMaxData) ? fftMaxData : [],
            type: 'scattergl',
            mode: 'lines',
            marker: { color: 'green' },
            opacity: traceStyles.max.opacity,
            line: { shape: 'linear', width: traceStyles.max.width },
            showlegend: false, // Show this trace in the legend
            name: 'Max FFT Data', // Label for the legend
          },
          traceStyles.persist.visible && settings.showPersistanceTrace && {  // Conditionally add the Persistence Trace
            x: Array.isArray(persistanceData) ? persistenceX : [],
            y: Array.isArray(persistanceData) ? persistanceData : [],
            type: 'scattergl',
            mode: 'lines',
            opacity: traceStyles.persist.opacity,
            line: {
              color: 'rgba(0, 0, 255, 0.1)', // Semi-transparent blue line
              shape: 'linear',
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
            domain: [0, 1],
            range: xAxisRangeHz,
            automargin: false,
          },
          yaxis: {
            title: 'Amplitude (dB)',
            range: [minY, maxY],
            color: 'white',
            gridcolor: '#444',
            zeroline: false, // Remove the white line across the 0 mark
            automargin: false,
          },
          margin: plotMargin,
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
          config={{ responsive: true }}
          style={{ ...sharedPlotStyle, height: showWaterfall ? '42vh' : '78vh' }}
          onClick={handlePlotClick}
          onInitialized={(figure, graphDiv) => {
            spectrumPlotRef.current = graphDiv;
          }}
          onUpdate={(figure, graphDiv) => {
            spectrumPlotRef.current = graphDiv;
          }}
          />
        )}
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
                value={Math.max(1, Math.min(maxWaterfallSamples, toFinite(settings.waterfallSamples, 200)))}
                onChange={(e) => {
                  const samples = Math.max(1, Math.min(maxWaterfallSamples, toFinite(e.target.value, 100)));
                  setSettings({ ...settings, waterfallSamples: samples });
                  setWaterfallData((prev) => prev.slice(-samples));
                  pushSettings({ waterfallSamples: samples });
                }}
                style={quickTuneSelectStyle}
              >
                {[100, 200, 375].map((samples) => (
                  <option key={samples} value={samples}>{samples}</option>
                ))}
              </select>
              <button type="button" style={quickTuneButtonStyle} onClick={() => setWaterfallData([])}>Clear</button>
            </div>
          </div>
          {useGpuCharts ? (
            <GpuWaterfall
              data={renderedWaterfallData}
              minDb={waterfallZMin}
              maxDb={waterfallZMax}
              palette={waterfallColorScale}
              freqStartHz={baseFreq}
              freqStopHz={baseFreq + safeBandwidthHz}
              noSignal={waterfallNoSignal}
              width={`${plotWidth}vw`}
              height="36vh"
              margin={plotMargin}
            />
          ) : (
            <Plot
              data={[
              {
                x: waterfallX,
                z: renderedWaterfallData,
                type: 'heatmap',
                colorscale: waterfallColorScale,
                zsmooth: waterfallSmooth,
                zmin: waterfallZMin,
                zmax: waterfallZMax,
                showscale: false, // Remove the color scale
              },
              ...classifiedWaterfallTraces,
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
                domain: [0, 1],
                range: xAxisRangeHz,
                automargin: false,
              },
              yaxis: {
                title: 'Samples',
                color: 'white',
                gridcolor: '#444',
                range: [0, requestedWaterfallRows],
                automargin: false,
              },
              margin: waterfallMargin,
              autosize: true,  // Let Plotly auto size
              uirevision: `waterfall-${requestedWaterfallRows}-${safeWaterfallBins}`,
              showlegend: false,
              paper_bgcolor: '#000',
              plot_bgcolor: '#000',
              font: {
                color: 'white',
              },
              annotations: [
                ...classifiedWaterfallAnnotations,
                ...(waterfallNoSignal ? [{
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
                }] : []),
              ],
            }}
            config={{
              displayModeBar: false, // Hide the mode bar
              responsive: true,
            }}
            style={{ ...sharedPlotStyle, height: '36vh' }}
            onRelayout={handleRelayout} // Attach the relayout event handler
            />
          )}
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
