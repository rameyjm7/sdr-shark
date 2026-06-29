import React, { useEffect, useMemo, useRef } from 'react';

const clamp01 = (value) => Math.max(0, Math.min(1, value));

const lerp = (a, b, t) => a + (b - a) * t;

const paletteStops = {
  'BladeRF Neon': [
    [0.00, 4, 2, 22],
    [0.10, 18, 18, 75],
    [0.22, 0, 88, 190],
    [0.36, 0, 230, 255],
    [0.50, 34, 255, 116],
    [0.64, 246, 255, 54],
    [0.78, 255, 120, 30],
    [0.90, 255, 238, 130],
    [1.00, 255, 255, 245],
  ],
  Jet: [
    [0.00, 0, 0, 88],
    [0.18, 0, 38, 255],
    [0.38, 0, 230, 255],
    [0.58, 60, 255, 65],
    [0.76, 255, 245, 45],
    [0.90, 255, 92, 24],
    [1.00, 210, 0, 0],
  ],
  Turbo: [
    [0.00, 48, 18, 59],
    [0.18, 45, 91, 217],
    [0.36, 29, 207, 194],
    [0.54, 146, 251, 80],
    [0.72, 252, 197, 52],
    [0.88, 238, 88, 33],
    [1.00, 122, 4, 3],
  ],
  Viridis: [
    [0.00, 68, 1, 84],
    [0.25, 59, 82, 139],
    [0.50, 33, 145, 140],
    [0.75, 94, 201, 98],
    [1.00, 253, 231, 37],
  ],
  Cividis: [
    [0.00, 0, 32, 76],
    [0.25, 46, 81, 109],
    [0.50, 124, 124, 120],
    [0.75, 188, 173, 108],
    [1.00, 255, 233, 69],
  ],
  Hot: [
    [0.00, 0, 0, 0],
    [0.35, 180, 0, 0],
    [0.65, 255, 150, 0],
    [0.85, 255, 255, 80],
    [1.00, 255, 255, 255],
  ],
  Portland: [
    [0.00, 12, 51, 131],
    [0.25, 10, 136, 186],
    [0.50, 242, 211, 56],
    [0.75, 242, 143, 56],
    [1.00, 217, 30, 30],
  ],
};

export const colorFor = (value, palette) => {
  const stops = paletteStops[palette] || paletteStops['BladeRF Neon'];
  const t = clamp01(value);
  let upper = stops[stops.length - 1];
  let lower = stops[0];
  for (let i = 1; i < stops.length; i += 1) {
    if (t <= stops[i][0]) {
      upper = stops[i];
      lower = stops[i - 1];
      break;
    }
  }
  const span = Math.max(0.0001, upper[0] - lower[0]);
  const localT = clamp01((t - lower[0]) / span);
  return [
    Math.round(lerp(lower[1], upper[1], localT)),
    Math.round(lerp(lower[2], upper[2], localT)),
    Math.round(lerp(lower[3], upper[3], localT)),
  ];
};

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
    varying vec2 v_uv;
    void main() {
      v_uv = (a_position + 1.0) * 0.5;
      gl_Position = vec4(a_position, 0.0, 1.0);
    }
  `);
  const fragment = createShader(gl, gl.FRAGMENT_SHADER, `
    precision mediump float;
    uniform sampler2D u_texture;
    varying vec2 v_uv;
    void main() {
      vec4 c = texture2D(u_texture, v_uv);
      float bloom = smoothstep(0.62, 1.0, max(max(c.r, c.g), c.b));
      gl_FragColor = vec4(mix(c.rgb, vec3(1.0, 0.96, 0.70), bloom * 0.18), 1.0);
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

const formatMHz = (hz) => `${(hz / 1e6).toFixed(3)} MHz`;

const GpuWaterfall = ({
  data,
  minDb,
  maxDb,
  palette,
  freqStartHz,
  freqStopHz,
  noSignal,
  width = '100%',
  height = '34vh',
  margin,
}) => {
  const canvasRef = useRef(null);
  const overlayRef = useRef(null);
  const glStateRef = useRef(null);

  const dimensions = useMemo(() => {
    const rows = Array.isArray(data) ? data.length : 0;
    const cols = rows > 0 && Array.isArray(data[0]) ? data[0].length : 0;
    return { rows, cols };
  }, [data]);

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
        const positionBuffer = gl.createBuffer();
        gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
        gl.bufferData(
          gl.ARRAY_BUFFER,
          new Float32Array([-1, -1, 1, -1, -1, 1, -1, 1, 1, -1, 1, 1]),
          gl.STATIC_DRAW,
        );
        const texture = gl.createTexture();
        state = { gl, program, positionBuffer, texture };
        glStateRef.current = state;
      }

      const { gl, program, positionBuffer, texture } = state;
      const { rows, cols } = dimensions;
      gl.viewport(0, 0, canvas.width, canvas.height);
      gl.clearColor(0.005, 0.005, 0.012, 1);
      gl.clear(gl.COLOR_BUFFER_BIT);
      if (!rows || !cols) return;

      const zMin = Number.isFinite(minDb) ? minDb : -100;
      const zMax = Number.isFinite(maxDb) && maxDb > zMin ? maxDb : zMin + 80;
      const invRange = 1 / Math.max(1, zMax - zMin);
      const pixels = new Uint8Array(rows * cols * 4);
      for (let y = 0; y < rows; y += 1) {
        const row = data[y] || [];
        for (let x = 0; x < cols; x += 1) {
          const raw = Number(row[x]);
          const db = Number.isFinite(raw) ? raw : zMin;
          const normalized = Math.pow(clamp01((db - zMin) * invRange), 0.68);
          const [r, g, b] = colorFor(normalized, palette);
          const idx = (y * cols + x) * 4;
          pixels[idx] = r;
          pixels[idx + 1] = g;
          pixels[idx + 2] = b;
          pixels[idx + 3] = 255;
        }
      }

      gl.useProgram(program);
      gl.bindBuffer(gl.ARRAY_BUFFER, positionBuffer);
      const pos = gl.getAttribLocation(program, 'a_position');
      gl.enableVertexAttribArray(pos);
      gl.vertexAttribPointer(pos, 2, gl.FLOAT, false, 0, 0);
      gl.activeTexture(gl.TEXTURE0);
      gl.bindTexture(gl.TEXTURE_2D, texture);
      gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.LINEAR);
      gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.LINEAR);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.RGBA, cols, rows, 0, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
      gl.uniform1i(gl.getUniformLocation(program, 'u_texture'), 0);
      gl.drawArrays(gl.TRIANGLES, 0, 6);
    } catch (error) {
      const ctx = canvas.getContext('2d');
      const { rows, cols } = dimensions;
      if (!ctx || !rows || !cols) return;
      const image = ctx.createImageData(cols, rows);
      const zMin = Number.isFinite(minDb) ? minDb : -100;
      const zMax = Number.isFinite(maxDb) && maxDb > zMin ? maxDb : zMin + 80;
      const invRange = 1 / Math.max(1, zMax - zMin);
      for (let y = 0; y < rows; y += 1) {
        const row = data[y] || [];
        for (let x = 0; x < cols; x += 1) {
          const normalized = Math.pow(clamp01((Number(row[x]) - zMin) * invRange), 0.68);
          const [r, g, b] = colorFor(normalized, palette);
          const idx = (y * cols + x) * 4;
          image.data[idx] = r;
          image.data[idx + 1] = g;
          image.data[idx + 2] = b;
          image.data[idx + 3] = 255;
        }
      }
      const scratch = document.createElement('canvas');
      scratch.width = cols;
      scratch.height = rows;
      scratch.getContext('2d').putImageData(image, 0, 0);
      ctx.imageSmoothingEnabled = true;
      ctx.drawImage(scratch, 0, 0, canvas.width, canvas.height);
    }
  }, [data, dimensions, minDb, maxDb, palette]);

  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    const ctx = overlay.getContext('2d');
    if (!ctx) return;
    const w = overlay.width;
    const h = overlay.height;
    const dpr = Math.max(1, Math.min(2, window.devicePixelRatio || 1));
    ctx.clearRect(0, 0, w, h);
    ctx.save();
    ctx.scale(dpr, dpr);
    const cssW = w / dpr;
    const cssH = h / dpr;
    ctx.strokeStyle = 'rgba(0, 0, 0, 0.42)';
    ctx.lineWidth = 1;
    for (let i = 0; i <= 5; i += 1) {
      const x = (cssW * i) / 5;
      ctx.beginPath();
      ctx.moveTo(x, 0);
      ctx.lineTo(x, cssH);
      ctx.stroke();
    }
    for (let i = 1; i < 4; i += 1) {
      const y = (cssH * i) / 4;
      ctx.beginPath();
      ctx.moveTo(0, y);
      ctx.lineTo(cssW, y);
      ctx.stroke();
    }
    ctx.fillStyle = 'rgba(238, 246, 255, 0.78)';
    ctx.font = '11px system-ui, sans-serif';
    ctx.textBaseline = 'bottom';
    for (let i = 0; i <= 5; i += 1) {
      const x = (cssW * i) / 5;
      const hz = lerp(freqStartHz, freqStopHz, i / 5);
      ctx.textAlign = i === 0 ? 'left' : (i === 5 ? 'right' : 'center');
      ctx.fillText(formatMHz(hz), x + (i === 0 ? 5 : i === 5 ? -5 : 0), cssH - 6);
    }
    ctx.restore();
  }, [freqStartHz, freqStopHz, dimensions]);

  return (
    <div
      style={{
        position: 'relative',
        width,
        height,
        minHeight: 180,
        background: '#05050b',
        overflow: 'hidden',
        borderTop: '1px solid rgba(255,255,255,0.08)',
      }}
    >
      <div
        style={{
          position: 'absolute',
          left: margin?.l || 0,
          right: margin?.r || 0,
          top: 0,
          bottom: 0,
          overflow: 'hidden',
        }}
      >
        <canvas ref={canvasRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%' }} />
        <canvas ref={overlayRef} style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', pointerEvents: 'none' }} />
      </div>
      <div
        style={{
          position: 'absolute',
          left: margin?.l || 0,
          right: margin?.r || 0,
          top: 0,
          bottom: 0,
          pointerEvents: 'none',
          boxShadow: 'inset 0 0 28px rgba(0,0,0,0.75), inset 0 0 80px rgba(2,8,30,0.45)',
          mixBlendMode: 'screen',
        }}
      />
      {noSignal && (
        <div
          style={{
            position: 'absolute',
            left: '50%',
            top: '50%',
            transform: 'translate(-50%, -50%)',
            color: '#ff9b9b',
            fontSize: 18,
            border: '1px solid rgba(255,128,128,0.75)',
            background: 'rgba(0,0,0,0.45)',
            padding: '5px 10px',
          }}
        >
          [NO SIGNAL]
        </div>
      )}
    </div>
  );
};

export default GpuWaterfall;
