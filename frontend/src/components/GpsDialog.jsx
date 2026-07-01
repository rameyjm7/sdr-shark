import React, { useEffect, useState } from 'react';
import axios from 'axios';
import { Box, Chip, Dialog, DialogContent, DialogTitle, IconButton, Paper, Stack, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import GpsFixedIcon from '@mui/icons-material/GpsFixed';
import GpsNotFixedIcon from '@mui/icons-material/GpsNotFixed';
import SatelliteAltIcon from '@mui/icons-material/SatelliteAlt';

const fmt = (value, suffix = '') => {
  if (value === null || value === undefined || value === '') return '-';
  if (typeof value === 'number' && Number.isFinite(value)) return `${value.toFixed(6).replace(/\.?0+$/, '')}${suffix}`;
  return `${value}${suffix}`;
};

const lockColor = (lock) => {
  if (lock === '3D') return '#64f0d2';
  if (lock === '2D') return '#90caf9';
  if (lock === '1D') return '#ffd166';
  return '#888';
};

const lockLabel = (gps) => {
  if (!gps?.connected) return 'GPS disconnected';
  if (gps?.lock === '3D') return '3D Lock';
  if (gps?.lock === '2D') return '2D Lock';
  if (gps?.lock === '1D') return '1D Lock';
  return 'No Lock';
};

const GpsDialog = ({ open, onClose }) => {
  const [gps, setGps] = useState(null);

  useEffect(() => {
    if (!open) return undefined;
    let cancelled = false;
    const fetchGps = () => {
      axios.get('/api/gps/status')
        .then((response) => {
          if (!cancelled) setGps(response?.data?.gps || null);
        })
        .catch((error) => {
          if (!cancelled) setGps((prev) => ({ ...(prev || {}), connected: false, error: error.message }));
        });
    };
    fetchGps();
    const interval = setInterval(fetchGps, 1000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
  }, [open]);

  const lock = gps?.lock || 'NO';
  const LockIcon = gps?.connected && lock !== 'NO' ? GpsFixedIcon : GpsNotFixedIcon;
  const accent = lockColor(lock);

  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      PaperProps={{
        sx: {
          bgcolor: '#101418',
          backgroundImage: 'linear-gradient(145deg, rgba(20, 40, 50, 0.96), rgba(6, 8, 10, 0.98))',
          border: '1px solid rgba(144,202,249,0.18)',
        },
      }}
    >
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', pb: 1 }}>
        <Box>
          <Typography variant="overline" color="text.secondary">SDR Shark</Typography>
          <Stack direction="row" spacing={1} alignItems="center">
            <SatelliteAltIcon sx={{ color: accent }} />
            <Typography variant="h6">GPS</Typography>
          </Stack>
        </Box>
        <IconButton onClick={onClose} aria-label="Close GPS">
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent sx={{ pb: 2 }}>
        <Paper elevation={0} sx={{ p: 2, borderRadius: 2, bgcolor: 'rgba(0,0,0,0.22)', border: '1px solid rgba(255,255,255,0.10)' }}>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 2, flexWrap: 'wrap', gap: 1 }}>
            <Chip
              icon={<LockIcon />}
              label={lockLabel(gps)}
              sx={{ bgcolor: `${accent}22`, color: '#fff', border: `1px solid ${accent}` }}
            />
            <Chip size="small" label={`Mode ${gps?.mode ?? 0}`} />
            <Chip size="small" label={`${gps?.sats_used ?? '-'} used / ${gps?.sats_seen ?? '-'} seen`} />
          </Stack>

          <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(160px, 1fr))', gap: 1.25 }}>
            <Info label="Latitude" value={fmt(gps?.lat)} />
            <Info label="Longitude" value={fmt(gps?.lon)} />
            <Info label="Altitude" value={fmt(gps?.alt, ' m')} />
            <Info label="Speed" value={fmt(gps?.speed, ' m/s')} />
            <Info label="GPS Time" value={fmt(gps?.time)} />
            <Info label="Status" value={gps?.status || '-'} />
          </Box>

          {gps?.device_paths?.length ? (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>
              Devices: {gps.device_paths.join(', ')}
            </Typography>
          ) : null}
          {gps?.error ? (
            <Typography variant="caption" color="error" sx={{ display: 'block', mt: 1 }}>
              {gps.error}
            </Typography>
          ) : null}
        </Paper>
      </DialogContent>
    </Dialog>
  );
};

const Info = ({ label, value }) => (
  <Box sx={{ p: 1, borderRadius: 1.5, bgcolor: 'rgba(255,255,255,0.04)' }}>
    <Typography variant="caption" color="text.secondary">{label}</Typography>
    <Typography variant="body2" sx={{ fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace' }}>
      {value}
    </Typography>
  </Box>
);

export default GpsDialog;
