import React, { useEffect, useMemo, useRef } from 'react';
import { colorFor } from './GpuWaterfall';

const clamp01 = (value) => Math.max(0, Math.min(1, value));
const lerp = (a, b, t) => a + (b - a) * t;

const createShader = (gl, type, source) => {
  const shader = gl.createShader(type);
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    const error = gl.getShaderInfoLog(shader);
    gl.deleteShader(shader);
    throw new Error(error || 'Shader compile failed');
  }
  return shader;
};

const createProgram = (gl) => {
  const vertex = createShader(gl, gl.VERTEX_SHADER, `
    attribute vec2 a_position;
    attribute vec4 a_color;
    varying vec4 v_color;
    void main() {
      v_color = a_color;
      gl_Position = vec4(a_position, 0.0, 1.0);
    }
  `);
  const fragment = createShader(gl, gl.FRAGMENT_SHADER, `
    precision mediump float;
    varying vec4 v_color;
    void main() {
      gl_FragColor = v_color;
    }
  `);
  const program = gl.createProgram();
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    const error = gl.getProgramInfoLog(program);
    gl.deleteProgram(program);
    throw new Error(error || 'Program link failed');
  }
  return program;
};

const formatMHz = (hz) => `${(hz / 1e6).toFixed(2)}`;

const buildBars = ({ values, width, height, minDb, maxDb, palette, margin, opacity, widthScale, glow, freqStartHz, freqStopHz, viewStartHz, viewStopHz }) => {
  const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
  const left = Math.max(0, Math.floor((margin?.l || 0) * dpr));
  const right = Math.max(0, Math.floor((margin?.r || 0) * dpr));
  const top = Math.max(0, Math.floor((margin?.t || 0) * dpr));
  const bottom = Math.max(0, Math.floor((margin?.b || 0) * dpr));
  const plotW = Math.max(1, width - left - right);
  const plotH = Math.max(1, height - top - bottom);
  const baseY = top + plotH;
  const zMin = Number.isFinite(minDb) ? minDb : -100;
  const zMax = Number.isFinite(maxDb) && maxDb > zMin ? maxDb : zMin + 80;
  const range = Math.max(1, zMax - zMin);
  const count = values.length;
  const columnW = Math.max(1, plotW / Math.max(1, count - 1));
  const dataStart = Number.isFinite(freqStartHz) ? Number(freqStartHz) : 0;
  const dataStop = Number.isFinite(freqStopHz) && Number(freqStopHz) > dataStart ? Number(freqStopHz) : dataStart + Math.max(1, count - 1);
  const viewStart = Number.isFinite(viewStartHz) ? Number(viewStartHz) : dataStart;
  const viewStop = Number.isFinite(viewStopHz) && Number(viewStopHz) > viewStart ? Number(viewStopHz) : dataStop;
  const viewSpan = Math.max(1, viewStop - viewStart);
  const positions = [];
  const colors = [];
  const pushVertex = (x, y, color) => {
    positions.push((x / width) * 2 - 1, 1 - (y / height) * 2);
    colors.push(color[0], color[1], color[2], color[3]);
  };

  for (let i = 0; i < count; i += 1) {
    const value = Number(values[i]);
    if (!Number.isFinite(value)) continue;
    const normalized = clamp01((value - zMin) / range);
    const shaped = Math.pow(normalized, 0.62);
    const [r, g, b] = colorFor(shaped, palette).map((c) => c / 255);
    const hot = clamp01((normalized - 0.54) / 0.42);
    const alpha = glow
      ? Math.min(0.32, (0.07 + hot * 0.24) * opacity)
      : Math.min(0.98, (0.34 + shaped * 0.52) * opacity);
    const freqHz = dataStart + (i / Math.max(1, count - 1)) * (dataStop - dataStart);
    const xFrac = (freqHz - viewStart) / viewSpan;
    if (xFrac < -0.02 || xFrac > 1.02) continue;
    const x = left + xFrac * plotW;
    const y = top + (1 - normalized) * plotH;
    const widthFactor = Math.max(0.5, Math.min(4, Number(widthScale) || 1));
    const pad = glow
      ? Math.max(columnW * (1.6 + widthFactor), 3 * dpr)
      : Math.max(columnW * (0.34 + widthFactor * 0.24), 1);
    const rise = glow ? 20 * dpr * hot : 0;
    const x0 = Math.max(left, x - pad);
    const x1 = Math.min(left + plotW, x + pad);
    const y0 = Math.max(top, y - rise);
    const y1 = baseY;
    const color = glow
      ? [lerp(r, 1, hot * 0.25), lerp(g, 0.96, hot * 0.20), lerp(b, 0.35, hot * 0.18), alpha]
      : [r, g, b, alpha];
    pushVertex(x0, y0, color);
    pushVertex(x1, y0, color);
    pushVertex(x0, y1, color);
    pushVertex(x0, y1, color);
    pushVertex(x1, y0, color);
    pushVertex(x1, y1, color);
  }

  return {
    positions: new Float32Array(positions),
    colors: new Float32Array(colors),
    vertexCount: positions.length / 2,
  };
};

const GpuSpectrum = ({
  data,
  secondaryData,
  primaryFreqStartHz,
  primaryFreqStopHz,
  secondaryFreqStartHz,
  secondaryFreqStopHz,
  minDb,
  maxDb,
  palette,
  freqStartHz,
  freqStopHz,
  margin,
  width = '100%',
  height = '42vh',
  opacity = 1,
  widthScale = 1,
  markers,
  verticalLines,
  horizontalLines,
  noSignal,
  onPick,
  onRendererChange,
}) => {
  const canvasRef = useRef(null);
  const overlayRef = useRef(null);
  const glStateRef = useRef(null);
  const rendererReportedRef = useRef(false);

  const values = useMemo(() => (Array.isArray(data) ? data : []), [data]);
  const secondaryValues = useMemo(() => (Array.isArray(secondaryData) ? secondaryData : []), [secondaryData]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;
    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    const resize = () => {
      const rect = canvas.getBoundingClientRect();
      canvas.width = Math.max(1, Math.floor(rect.width * dpr));
      canvas.height = Math.max(1, Math.floor(rect.height * dpr));
      const overlay = overlayRef.current;
      if (overlay) {
        overlay.width = canvas.width;
        overlay.height = canvas.height;
      }
    };
    resize();
    const observer = new ResizeObserver(resize);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    let state = glStateRef.current;
    try {
      if (!state) {
        const gl = canvas.getContext('webgl', { antialias: false, alpha: false, preserveDrawingBuffer: false });
        if (!gl) throw new Error('WebGL unavailable');
        const program = createProgram(gl);
        const debugInfo = gl.getExtension('WEBGL_debug_renderer_info');
        const renderer = debugInfo
          ? gl.getParameter(debugInfo.UNMASKED_RENDERER_WEBGL)
          : gl.getParameter(gl.RENDERER);
        state = {
          gl,
          program,
          positionBuffer: gl.createBuffer(),
          colorBuffer: gl.createBuffer(),
          renderer,
        };
        glStateRef.current = state;
        if (!rendererReportedRef.current && typeof onRendererChange === 'function') {
          rendererReportedRef.current = true;
          onRendererChange({ mode: 'GPU', detail: renderer || 'WebGL' });
        }
      }
      const { gl, program, positionBuffer, colorBuffer } = state;
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.disable(gl.DEPTH_TEST);
      gl.enable(gl.BLEND);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      gl.clearColor(0.004, 0.004, 0.008, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.useProgram(program);

      const drawSet = (set) => {
        if (!set.vertexCount) return;
        const posLoc = gl.getAttribLocation(program, 'a_position');
        gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, set.positions, gl.DYNAMIC_DRAW);
        gl.enableVertexAttribArray(posLoc);
        gl.vertexAttribPointer(posLoc, 2, gl.FLOAT, false, 0, 0);

        const colorLoc = gl.getAttribLocation(program, 'a_color');
        gl.bindBuffer(gl.ARRAY_BUFFER, colorBuffer);
        gl.bufferData(gl.ARRAY_BUFFER, set.colors, gl.DYNAMIC_DRAW);
        gl.enableVertexAttribArray(colorLoc);
        gl.vertexAttribPointer(colorLoc, 4, gl.FLOAT, false, 0, 0);
        gl.drawArrays(gl.TRIANGLES, 0, set.vertexCount);
      };

      if (secondaryValues.length) {
        drawSet(buildBars({
          values: secondaryValues,
          width: canvas.width,
          height: canvas.height,
          minDb,
          maxDb,
          palette,
          margin,
          opacity: Math.min(0.42, opacity * 0.5),
          widthScale: Math.max(0.5, widthScale * 0.75),
          glow: true,
          freqStartHz: secondaryFreqStartHz,
          freqStopHz: secondaryFreqStopHz,
          viewStartHz: freqStartHz,
          viewStopHz: freqStopHz,
        }));
        drawSet(buildBars({
          values: secondaryValues,
          width: canvas.width,
          height: canvas.height,
          minDb,
          maxDb,
          palette,
          margin,
          opacity: Math.min(0.52, opacity * 0.62),
          widthScale: Math.max(0.5, widthScale * 0.7),
          glow: false,
          freqStartHz: secondaryFreqStartHz,
          freqStopHz: secondaryFreqStopHz,
          viewStartHz: freqStartHz,
          viewStopHz: freqStopHz,
        }));
      }

      drawSet(buildBars({
        values,
        width: canvas.width,
        height: canvas.height,
        minDb,
        maxDb,
        palette,
        margin,
        opacity,
        widthScale,
        glow: true,
        freqStartHz: primaryFreqStartHz || freqStartHz,
        freqStopHz: primaryFreqStopHz || freqStopHz,
        viewStartHz: freqStartHz,
        viewStopHz: freqStopHz,
      }));
      drawSet(buildBars({
        values,
        width: canvas.width,
        height: canvas.height,
        minDb,
        maxDb,
        palette,
        margin,
        opacity,
        widthScale,
        glow: false,
        freqStartHz: primaryFreqStartHz || freqStartHz,
        freqStopHz: primaryFreqStopHz || freqStopHz,
        viewStartHz: freqStartHz,
        viewStopHz: freqStopHz,
      }));
    } catch (error) {
      if (!rendererReportedRef.current && typeof onRendererChange === 'function') {
        rendererReportedRef.current = true;
        onRendererChange({ mode: 'CPU', detail: 'WebGL unavailable' });
      }
      // Leave a blank dark panel if WebGL is unavailable; the overlay still shows axes.
    }
  }, [values, secondaryValues, minDb, maxDb, palette, margin, opacity, widthScale, onRendererChange, freqStartHz, freqStopHz, primaryFreqStartHz, primaryFreqStopHz, secondaryFreqStartHz, secondaryFreqStopHz]);

  useEffect(() => {
    const overlay = overlayRef.current;
    const ctx = overlay?.getContext('2d');
    if (!overlay || !ctx) return;
    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    const w = overlay.width / dpr;
    const h = overlay.height / dpr;
    const left = margin?.l || 0;
    const right = margin?.r || 0;
    const top = margin?.t || 0;
    const bottom = margin?.b || 0;
    const plotW = Math.max(1, w - left - right);
    const plotH = Math.max(1, h - top - bottom);
    const zMin = Number.isFinite(minDb) ? minDb : -100;
    const zMax = Number.isFinite(maxDb) && maxDb > zMin ? maxDb : zMin + 80;

    ctx.clearRect(0, 0, overlay.width, overlay.height);
    ctx.save();
    ctx.scale(dpr, dpr);
    ctx.strokeStyle = 'rgba(105, 114, 128, 0.34)';
    ctx.lineWidth = 1;
    ctx.font = '11px system-ui, sans-serif';
    ctx.fillStyle = 'rgba(239, 246, 255, 0.86)';
    ctx.textBaseline = 'middle';

    for (let i = 0; i <= 4; i += 1) {
      const y = top + (plotH * i) / 4;
      const db = lerp(zMax, zMin, i / 4);
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(left + plotW, y);
      ctx.stroke();
      ctx.textAlign = 'right';
      ctx.fillText(db.toFixed(0), left - 7, y);
    }
    for (let i = 0; i <= 4; i += 1) {
      const x = left + (plotW * i) / 4;
      const hz = lerp(freqStartHz, freqStopHz, i / 4);
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, top + plotH);
      ctx.stroke();
      ctx.textAlign = i === 0 ? 'left' : (i === 4 ? 'right' : 'center');
      ctx.fillText(formatMHz(hz), x, top + plotH + 18);
    }

    (verticalLines || []).forEach(({ frequency }) => {
      const mhz = Number(frequency);
      if (!Number.isFinite(mhz)) return;
      const hz = mhz * 1e6;
      const x = left + ((hz - freqStartHz) / Math.max(1, freqStopHz - freqStartHz)) * plotW;
      if (x < left || x > left + plotW) return;
      ctx.strokeStyle = 'rgba(255, 64, 64, 0.85)';
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, top + plotH);
      ctx.stroke();
    });

    (horizontalLines || []).forEach(({ power }) => {
      const db = Number(power);
      if (!Number.isFinite(db)) return;
      const y = top + (1 - (db - zMin) / Math.max(1, zMax - zMin)) * plotH;
      if (y < top || y > top + plotH) return;
      ctx.strokeStyle = 'rgba(255, 64, 64, 0.85)';
      ctx.beginPath();
      ctx.moveTo(left, y);
      ctx.lineTo(left + plotW, y);
      ctx.stroke();
    });

    (markers || []).filter(Boolean).forEach((marker, idx) => {
      const x = left + ((marker.x - freqStartHz) / Math.max(1, freqStopHz - freqStartHz)) * plotW;
      const y = top + (1 - (marker.y - zMin) / Math.max(1, zMax - zMin)) * plotH;
      const color = idx === 0 ? '#63b3ff' : '#b38bff';
      ctx.strokeStyle = color;
      ctx.fillStyle = color;
      ctx.setLineDash([3, 4]);
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, top + plotH);
      ctx.stroke();
      ctx.setLineDash([]);
      ctx.beginPath();
      ctx.arc(x, y, 4, 0, Math.PI * 2);
      ctx.fill();
    });

    if (noSignal) {
      ctx.textAlign = 'center';
      ctx.fillStyle = '#ff9b9b';
      ctx.strokeStyle = 'rgba(255,128,128,0.75)';
      ctx.fillText('[NO SIGNAL]', left + plotW / 2, top + plotH / 2);
    }
    ctx.restore();
  }, [freqStartHz, freqStopHz, horizontalLines, margin, markers, maxDb, minDb, noSignal, verticalLines]);

  const handleClick = (event) => {
    if (typeof onPick !== 'function' || values.length < 2) return;
    const canvas = overlayRef.current;
    if (!canvas) return;
    const rect = canvas.getBoundingClientRect();
    const left = margin?.l || 0;
    const right = margin?.r || 0;
    const top = margin?.t || 0;
    const bottom = margin?.b || 0;
    const plotW = Math.max(1, rect.width - left - right);
    const plotH = Math.max(1, rect.height - top - bottom);
    const x = event.clientX - rect.left;
    const y = event.clientY - rect.top;
    if (x < left || x > left + plotW || y < top || y > top + plotH) return;
    const ratio = clamp01((x - left) / plotW);
    const idx = Math.max(0, Math.min(values.length - 1, Math.round(ratio * (values.length - 1))));
    onPick({
      x: lerp(freqStartHz, freqStopHz, ratio),
      y: Number(values[idx]),
      shiftKey: event.shiftKey,
    });
  };

  return (
    <div
      style={{
        position: 'relative',
        width,
        height,
        minHeight: 260,
        background: '#020204',
        overflow: 'hidden',
      }}
      onClick={handleClick}
    >
      <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />
      <canvas ref={overlayRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />
    </div>
  );
};

export default GpuSpectrum;
