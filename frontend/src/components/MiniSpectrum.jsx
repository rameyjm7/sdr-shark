import React, { useEffect, useMemo, useState } from 'react';
import axios from 'axios';
import Plot from 'react-plotly.js';
import { Box, Typography } from '@mui/material';

const MiniSpectrum = ({ settings, minY, maxY, verticalLines = [], horizontalLines = [] }) => {
  const [fft, setFft] = useState([]);
  const [fftMax, setFftMax] = useState([]);
  const [persist, setPersist] = useState([]);
  const [centerMHz, setCenterMHz] = useState(Number(settings?.frequency) || 0);
  const [spanMHz, setSpanMHz] = useState(Math.max(0.2, Number(settings?.sampleRate) || 1));

  const toFinite = (value, fallback) => {
    const n = Number(value);
    return Number.isFinite(n) ? n : fallback;
  };

  const normalizeFreqMhz = (value, fallback) => {
    const n = toFinite(value, fallback);
    // Accept either MHz (typical UI path) or Hz (backend fallback).
    return n > 1e5 ? (n / 1e6) : n;
  };

  useEffect(() => {
    let active = true;

    const fetchFrame = async () => {
      try {
        const [mainResp, extResp] = await Promise.all([
          axios.get('/api/data', { params: { source: 'main', _ts: Date.now() } }),
          axios.get('/api/data_ext', { params: { _ts: Date.now() } }),
        ]);
        if (!active) return;

        const main = mainResp.data || {};
        const ext = extResp.data || {};
        const nextFft = Array.isArray(main.fft) ? main.fft.map((v) => (Number.isFinite(v) ? v : -255)) : [];
        const nextMax = Array.isArray(ext.max) ? ext.max.map((v) => (Number.isFinite(v) ? v : -255)) : [];
        const nextPersist = Array.isArray(ext.persistance) ? ext.persistance.map((v) => (Number.isFinite(v) ? v : -255)) : [];

        setFft(nextFft);
        setFftMax(nextMax);
        setPersist(nextPersist);

        const backendSettings = main.settings || {};
        // Prefer UI settings first so tune actions reflect immediately in this mini view.
        const center = normalizeFreqMhz(
          settings?.frequency,
          normalizeFreqMhz(backendSettings.frequency, 0),
        );
        const span = normalizeFreqMhz(
          settings?.sampleRate,
          normalizeFreqMhz(backendSettings.sample_rate, 1),
        );
        setCenterMHz(center);
        setSpanMHz(Math.max(0.2, span));
      } catch (error) {
        // Keep the mini view best-effort and silent.
      }
    };

    fetchFrame();
    const intervalMs = Math.max(80, toFinite(settings?.updateInterval, 500));
    const timer = setInterval(fetchFrame, intervalMs);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [settings?.frequency, settings?.sampleRate, settings?.updateInterval]);

  const xAxis = useMemo(() => {
    const bins = Math.max(1, fft.length);
    const start = centerMHz - spanMHz / 2;
    const step = spanMHz / bins;
    return Array.from({ length: bins }, (_, i) => start + i * step);
  }, [fft.length, centerMHz, spanMHz]);

  const overlayVerticalTraces = useMemo(() => (
    (Array.isArray(verticalLines) ? verticalLines : [])
      .filter((line) => Number.isFinite(Number(line?.frequency)))
      .map((line) => {
        const freqMHz = Number(line.frequency);
        return {
          x: [freqMHz, freqMHz],
          y: [minY, maxY],
          type: 'scatter',
          mode: 'lines',
          line: { color: 'rgba(135,206,250,0.85)', width: 1, dash: 'dot' },
          hoverinfo: 'skip',
          showlegend: false,
        };
      })
  ), [verticalLines, minY, maxY]);

  const overlayHorizontalTraces = useMemo(() => (
    (Array.isArray(horizontalLines) ? horizontalLines : [])
      .filter((line) => Number.isFinite(Number(line?.power)))
      .map((line) => {
        const pwr = Number(line.power);
        const start = centerMHz - spanMHz / 2;
        const stop = centerMHz + spanMHz / 2;
        return {
          x: [start, stop],
          y: [pwr, pwr],
          type: 'scatter',
          mode: 'lines',
          line: { color: 'rgba(255,140,140,0.85)', width: 1, dash: 'dot' },
          hoverinfo: 'skip',
          showlegend: false,
        };
      })
  ), [horizontalLines, centerMHz, spanMHz]);

  return (
    <Box
      sx={{
        border: '1px solid #2e2e2e',
        borderRadius: 1,
        backgroundColor: '#0b0b0b',
        p: 0.75,
        mb: 1,
      }}
    >
      <Typography variant="caption" sx={{ color: '#9ddcff', px: 0.5 }}>
        Live Trace
      </Typography>
      <Plot
        data={[
          {
            x: xAxis,
            y: fft,
            type: 'scatter',
            mode: 'lines',
            line: { color: '#ffb300', width: 1 },
            hoverinfo: 'skip',
            showlegend: false,
          },
          {
            x: xAxis,
            y: fftMax.length === fft.length ? fftMax : [],
            type: 'scatter',
            mode: 'lines',
            line: { color: '#59d96a', width: 1 },
            opacity: 0.9,
            hoverinfo: 'skip',
            showlegend: false,
          },
          {
            x: xAxis,
            y: persist.length === fft.length ? persist : [],
            type: 'scatter',
            mode: 'lines',
            line: { color: '#5fb0ff', width: 1 },
            opacity: 0.25,
            hoverinfo: 'skip',
            showlegend: false,
          },
          ...overlayVerticalTraces,
          ...overlayHorizontalTraces,
        ]}
        layout={{
          template: 'none',
          autosize: true,
          height: 150,
          margin: { l: 30, r: 10, t: 6, b: 22, pad: 0 },
          paper_bgcolor: '#000',
          plot_bgcolor: '#000',
          xaxis: {
            showgrid: true,
            gridcolor: '#2f2f2f',
            zeroline: false,
            color: '#c9d1d9',
            tickfont: { size: 9 },
            title: { text: '', standoff: 0 },
            ticks: 'outside',
            tickcolor: '#4a4a4a',
          },
          yaxis: {
            showgrid: true,
            gridcolor: '#2f2f2f',
            color: '#c9d1d9',
            tickfont: { size: 9 },
            zeroline: false,
            range: [minY, maxY],
            fixedrange: true,
            ticks: 'outside',
            tickcolor: '#4a4a4a',
          },
          uirevision: 'mini-spectrum-static',
          font: { color: '#c9d1d9' },
        }}
        config={{
          displayModeBar: false,
          responsive: true,
          staticPlot: true,
        }}
        style={{ width: '100%' }}
      />
    </Box>
  );
};

export default MiniSpectrum;
