import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Box, Typography } from '@mui/material';

// Same card fields/layout as rf-sentinel's own detection cards
// (ui/frontend/index.html: detectionCardsHtml()) - both apps' backends
// feed the same always-on shared BTC/BLE detector now, so this fetches
// rf-sentinel's own resolved discovery_table (UAP/piconet tracking,
// manufacturer lookup, etc. already done there) rather than re-deriving
// it. Colors are adapted to this app's dark theme; rf-sentinel's own page
// is light-themed.
const PROTOCOL_ACCENT = {
  BTLE: '#125fd8',
  BTC: '#64b5f6',
};

const PROTOCOL_BG = {
  BTLE: 'linear-gradient(90deg, rgba(18,95,216,0.16), rgba(255,255,255,0.03) 34%)',
  BTC: 'linear-gradient(90deg, rgba(100,181,246,0.16), rgba(255,255,255,0.03) 34%)',
};

function age(ts) {
  const seconds = Math.max(0, Math.round(Date.now() / 1000 - Number(ts || 0)));
  return `${seconds}s`;
}

function fmtMaybeDb(value) {
  return Number.isFinite(Number(value)) ? `${Number(value).toFixed(1)} dBFS` : 'RSSI unknown';
}

function fmtUuid16(row) {
  if (!Array.isArray(row.uuid16)) return '';
  return row.uuid16
    .map((uuid, idx) => {
      const name = Array.isArray(row.uuid16_names) ? row.uuid16_names[idx] : '';
      return name ? `${uuid} ${name}` : uuid;
    })
    .join(', ');
}

function fmtManufacturer(row) {
  if (!row.manufacturer) return '';
  const name = row.manufacturer.company_name || row.manufacturer.company_id || '';
  const data = row.manufacturer.data || '';
  return data ? `${name} ${data.slice(0, 10)}${data.length > 10 ? '...' : ''}` : name;
}

function Chip({ children }) {
  return (
    <span
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        padding: '3px 6px',
        borderRadius: 999,
        color: '#d9f0ff',
        background: 'rgba(144,202,249,0.14)',
        fontSize: '0.7rem',
        fontVariantNumeric: 'tabular-nums',
        whiteSpace: 'nowrap',
      }}
    >
      {children}
    </span>
  );
}

function DeviceCard({ row }) {
  const protocol = String(row.protocol || '').toUpperCase();
  const accent = PROTOCOL_ACCENT[protocol] || '#3d556d';
  const uuid = fmtUuid16(row);
  const manufacturer = fmtManufacturer(row);
  const deviceType = String(row.device_type || '').trim();
  const deviceTypeDetail = String(row.device_type_detail || '').trim();
  return (
    <Box
      sx={{
        p: 1,
        border: '1px solid rgba(144,202,249,0.18)',
        borderLeft: `4px solid ${accent}`,
        borderRadius: '7px',
        background: PROTOCOL_BG[protocol] || 'rgba(255,255,255,0.03)',
      }}
    >
      <Typography variant="subtitle2" sx={{ color: '#fff', mb: 0.5 }}>
        {row.identity || row.name || row.mac || 'Unknown device'}
      </Typography>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.5, mb: row.detail || row.decoded_text ? 0.5 : 0 }}>
        <Chip>{protocol}</Chip>
        {protocol === 'BTC' && row.active_piconet ? <Chip>active piconet</Chip> : null}
        {deviceType ? <Chip>Type {deviceType}</Chip> : null}
        {deviceTypeDetail ? <Chip>{deviceTypeDetail}</Chip> : null}
        <Chip>{Number(row.detections || 0).toLocaleString()} hits</Chip>
        <Chip>{age(row.last_seen_at)}</Chip>
        <Chip>{fmtMaybeDb(row.last_rssi_dbfs)}</Chip>
        <Chip>CH {row.channel ?? ''}</Chip>
        {row.mac ? <Chip>{row.mac}</Chip> : null}
        {uuid ? <Chip>UUID {uuid}</Chip> : null}
        {manufacturer ? <Chip>{manufacturer}</Chip> : null}
      </Box>
      {row.decoded_text ? (
        <Typography variant="caption" sx={{ color: '#9fd6ff', display: 'block' }}>
          Decoded text: &ldquo;{row.decoded_text}&rdquo;
        </Typography>
      ) : null}
      <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.55)' }}>
        {row.identity_source || row.detail || 'Observed from SDR packet evidence.'}
      </Typography>
    </Box>
  );
}

export default function BluetoothDeviceCards() {
  const [rows, setRows] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      try {
        const res = await axios.get('/api/bluetooth/devices');
        if (cancelled) return;
        setRows(Array.isArray(res.data?.rows) ? res.data.rows : []);
        setError(res.data?.error || null);
      } catch (err) {
        if (!cancelled) setError(String(err));
      }
    };
    poll();
    const interval = setInterval(poll, 3000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, []);

  const sorted = [...rows].sort((a, b) =>
    String(a.identity || a.name || a.mac || '').localeCompare(String(b.identity || b.name || b.mac || '')),
  );

  return (
    <Box>
      <Typography variant="overline" sx={{ color: 'rgba(255,255,255,0.6)' }}>
        Bluetooth devices (BTC/BLE - shared detector){error ? ' - unavailable' : ''}
      </Typography>
      {sorted.length === 0 ? (
        <Typography variant="body2" sx={{ color: 'rgba(255,255,255,0.5)', py: 1 }}>
          No detections yet
        </Typography>
      ) : (
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fill, minmax(240px, 1fr))',
            gap: 1,
            py: 1,
          }}
        >
          {sorted.map((row) => (
            <DeviceCard key={row.key || row.mac || row.identity} row={row} />
          ))}
        </Box>
      )}
    </Box>
  );
}
