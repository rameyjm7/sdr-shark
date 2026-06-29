import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Chip, Divider, IconButton, Paper, Stack, Tooltip, Typography } from '@mui/material';
import BluetoothIcon from '@mui/icons-material/Bluetooth';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import RadioIcon from '@mui/icons-material/Radio';
import StopIcon from '@mui/icons-material/Stop';

const formatAge = (seenAt) => {
  const ts = Number(seenAt);
  if (!Number.isFinite(ts)) return 'now';
  const ageSec = Math.max(0, Math.round((Date.now() / 1000) - ts));
  if (ageSec < 60) return `${ageSec}s ago`;
  const ageMin = Math.round(ageSec / 60);
  if (ageMin < 60) return `${ageMin}m ago`;
  return `${Math.round(ageMin / 60)}h ago`;
};

const eventKey = (event, idx) => (
  event?.address ||
  event?.packet ||
  (event?.kind === 'fm_station' && event?.frequency_mhz ? `fm-${event.frequency_mhz}` : '') ||
  (event?.lap ? `btc-${event.lap}-${event?.type || 'event'}-${event?.seen_at || idx}` : '') ||
  `${event?.protocol || 'event'}-${event?.kind || 'unknown'}-${event?.seen_at || idx}`
);

const eventSeenAt = (event) => {
  const ts = Number(event?.seen_at);
  return Number.isFinite(ts) ? ts : Date.now() / 1000;
};

const normalizeUap = (value) => {
  const text = String(value || '').toUpperCase().replace(/[^0-9A-F]/g, '');
  return /^[0-9A-F]{2}$/.test(text) ? text : '';
};

const normalizedLap = (event) => {
  const lap = String(event?.lap || '').toUpperCase().replace(/[^0-9A-F]/g, '');
  return /^[0-9A-F]{6}$/.test(lap) ? lap : '';
};

const isBtcEvent = (event) => String(event?.protocol || '').toLowerCase() === 'btc';
const isFmEvent = (event) => String(event?.protocol || '').toLowerCase() === 'fm' || event?.kind === 'fm_station';

const protocolKey = (event) => {
  const protocol = String(event?.protocol || '').toLowerCase();
  if (protocol === 'btc') return 'BTC';
  if (protocol === 'ble') return 'BTLE';
  if (protocol === 'fm' || event?.kind === 'fm_station') return 'FM';
  return protocol ? protocol.toUpperCase() : 'RF';
};

const candidateMac = (event) => {
  if (event?.full_mac || event?.mac || event?.address) return event.full_mac || event.mac || event.address;
  const lap = normalizedLap(event);
  const uap = normalizeUap(event?.uap || event?.uap_hex);
  if (!lap) return 'XX:XX:XX:XX:XX:XX';
  return `XX:XX:${uap || 'XX'}:${lap.slice(0, 2)}:${lap.slice(2, 4)}:${lap.slice(4, 6)}`;
};

const mergeBtcRows = (rows) => {
  const groups = new Map();
  rows.filter(isBtcEvent).forEach((row, idx) => {
    const lap = normalizedLap(row);
    const key = lap ? `lap:${lap}` : eventKey(row, idx);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });

  return Array.from(groups.values()).map((groupRows) => {
    const latest = groupRows.reduce(
      (winner, row) => (eventSeenAt(row) > eventSeenAt(winner) ? row : winner),
      groupRows[0],
    );
    const resolved = groupRows.find((row) => normalizeUap(row?.uap || row?.uap_hex)) || latest;
    const uaps = Array.from(new Set(groupRows.map((row) => normalizeUap(row?.uap || row?.uap_hex)).filter(Boolean))).sort();
    const candidateCounts = groupRows
      .map((row) => Number(row?.candidate_count))
      .filter((value) => Number.isFinite(value) && value > 0);
    const candidateCount = candidateCounts.length ? Math.min(...candidateCounts) : Number(resolved?.candidate_count || 0);
    return {
      ...resolved,
      ...latest,
      protocol: 'btc',
      full_mac: candidateMac({ ...resolved, ...latest }),
      lap: normalizedLap(resolved) || normalizedLap(latest) || resolved?.lap || latest?.lap,
      uap: uaps.length === 1 ? uaps[0] : normalizeUap(resolved?.uap || latest?.uap),
      uap_options: uaps,
      candidate_count: candidateCount,
      detections: groupRows.length,
      group_count: groupRows.length,
      seen_at: eventSeenAt(latest),
      rssi_dbfs: latest?.rssi_dbfs ?? resolved?.rssi_dbfs,
      channel: latest?.channel ?? resolved?.channel,
      center_freq_hz: latest?.center_freq_hz ?? resolved?.center_freq_hz,
    };
  });
};

const mergeDisplayEvents = (events) => {
  const btcRows = mergeBtcRows(events);
  const nonBtcRows = events.filter((event) => !isBtcEvent(event));
  return [...nonBtcRows, ...btcRows].sort((a, b) => {
    const aFm = isFmEvent(a);
    const bFm = isFmEvent(b);
    if (aFm && bFm) return Number(a?.frequency_mhz || 0) - Number(b?.frequency_mhz || 0);
    if (aFm !== bFm) return aFm ? -1 : 1;
    return eventSeenAt(b) - eventSeenAt(a);
  });
};

const eventIdentity = (event) => {
  if (isBtcEvent(event)) return event?.full_mac || candidateMac(event);
  if (event?.address) return event.address;
  if (event?.identity) return event.identity;
  if (event?.name) return event.name;
  if (event?.lap) return `BTC LAP ${event.lap}`;
  if (event?.manufacturer?.company_name) return event.manufacturer.company_name;
  return '';
};

const isDeviceEvent = (event) => Boolean(
  eventIdentity(event) ||
  event?.device_type ||
  event?.device_type_detail ||
  isFmEvent(event) ||
  String(event?.protocol || '').toLowerCase() === 'btc',
);

const eventTitle = (event) => {
  if (event?.identity) return event.identity;
  if (event?.name) return event.name;
  if (event?.address) return event.address;
  if (isBtcEvent(event)) return event?.full_mac || candidateMac(event);
  if (isFmEvent(event)) return event?.identity || `FM ${Number(event?.frequency_mhz || 0).toFixed(1)} MHz`;
  if (event?.kind === 'ble_adv') return 'BLE advertisement';
  if (event?.kind === 'ble_burst') return 'BLE burst';
  return 'Decoded radio event';
};

const protocolLabel = (event) => {
  return protocolKey(event);
};

const protocolGroupLabel = (protocol) => {
  const labels = {
    BTC: 'Bluetooth Classic',
    BTLE: 'Bluetooth Low Energy',
    FM: 'FM Broadcast',
    RF: 'Radio Events',
  };
  return labels[String(protocol || 'RF').toUpperCase()] || String(protocol || 'RF');
};

const protocolSortRank = (protocol) => {
  const rank = ['BTC', 'BTLE', 'FM', 'RF'].indexOf(String(protocol || '').toUpperCase());
  return rank < 0 ? 999 : rank;
};

const groupRowsBy = (rows, labelFn) => rows.reduce((groups, row) => {
  const label = labelFn(row);
  if (!groups.has(label)) groups.set(label, []);
  groups.get(label).push(row);
  return groups;
}, new Map());

const manufacturerGroupLabel = (event) => {
  if (isFmEvent(event)) return 'FM Broadcast';
  if (isBtcEvent(event)) return 'BT Classic / manufacturer unknown';
  const manufacturer = String(
    event?.manufacturer?.company_name ||
    event?.manufacturer_name ||
    event?.company_name ||
    '',
  ).trim();
  if (manufacturer) return manufacturer;
  if (event?.name || event?.identity) return event.name || event.identity;
  return 'Manufacturer unknown';
};

const summaryStats = (rows) => {
  const detections = rows.reduce(
    (sum, row) => sum + Math.max(1, Number(row?.detections || row?.group_count || row?.sightings || 1)),
    0,
  );
  const bestRssi = Math.max(
    ...rows
      .map((row) => Number(row?.rssi_dbfs ?? row?.power_dbfs ?? row?.last_rssi_dbfs))
      .filter(Number.isFinite),
    -120,
  );
  const lastSeen = Math.max(...rows.map(eventSeenAt));
  return { detections, bestRssi, lastSeen };
};

const eventFootprintLabel = (event) => {
  const protocol = String(event?.protocol || '').toLowerCase();
  if (protocol === 'btc') return '~1 MHz hop';
  if (protocol === 'ble') return '~2 MHz ch';
  if (protocol === 'fm') return '~200 kHz ch';
  return '';
};

const eventDetail = (event) => {
  if (isFmEvent(event)) {
    const excess = Number(event?.excess_db);
    if (event?.detail) return event.detail;
    if (Number.isFinite(excess)) return `FM broadcast carrier detected ${excess.toFixed(1)} dB above local noise.`;
    return 'FM broadcast carrier detected from the live spectrum.';
  }
  if (event?.device_type_detail) return event.device_type_detail;
  if (event?.identity_source) return event.identity_source;
  if (event?.manufacturer?.company_name) return `${event.manufacturer.company_name} manufacturer frame`;
  if (event?.detail) return event.detail;
  if (isBtcEvent(event)) {
    const left = Number(event?.candidate_count || 0);
    if (String(event?.status || '') === 'init_failed') {
      return 'The LAP was detected, but this packet could not initialize a valid UAP set yet.';
    }
    if (left > 0 && left <= 2) return 'Very close. One or two UAP candidates remain.';
    if (left > 0 && left < 32) return 'Converging. Follow-up packets are pruning UAP candidates.';
    if (normalizeUap(event?.uap || event?.uap_hex)) return 'Bluetooth Classic LAP/UAP evidence grouped from SDR packet detections.';
    return 'Initialized cleanly. Waiting for more packets from this LAP to prune UAP candidates.';
  }
  if (event?.kind === 'ble_burst') return 'Energy matched the BLE channel profile; waiting for decodable advertisements.';
  return 'Observed from SDR packet evidence.';
};

const DecodedEventsPanel = ({ telemetry, settings }) => {
  const events = useMemo(
    () => {
      const bluetoothEvents = Array.isArray(telemetry?.bluetooth?.events) ? telemetry.bluetooth.events : [];
      const fmEvents = Array.isArray(telemetry?.fm?.events) ? telemetry.fm.events : [];
      return [...bluetoothEvents, ...fmEvents];
    },
    [telemetry?.bluetooth?.events, telemetry?.fm?.events],
  );
  const retentionSec = Math.max(60, Math.min(3600, Number(settings?.activityLogRetentionSec) || 600));
  const maxHistoryEvents = Math.max(500, Math.min(5000, Math.round((retentionSec / 60) * 240)));
  const [historyEvents, setHistoryEvents] = useState([]);
  const [filterMode, setFilterMode] = useState('all');
  const [playingFrequency, setPlayingFrequency] = useState(null);
  const [playbackError, setPlaybackError] = useState('');
  const audioCtxRef = useRef(null);
  const gainNodeRef = useRef(null);
  const playCursorRef = useRef(0);
  const audioLoopActiveRef = useRef(false);
  const playingRef = useRef(null);

  useEffect(() => {
    setHistoryEvents((prev) => {
      const byKey = new Map();
      prev.forEach((event, idx) => {
        byKey.set(eventKey(event, idx), event);
      });
      events.forEach((event, idx) => {
        byKey.set(eventKey(event, idx), event);
      });

      const cutoff = (Date.now() / 1000) - retentionSec;
      return Array.from(byKey.values())
        .filter((event) => eventSeenAt(event) >= cutoff)
        .sort((a, b) => eventSeenAt(b) - eventSeenAt(a))
        .slice(0, maxHistoryEvents);
    });
  }, [events, retentionSec, maxHistoryEvents]);

  const sortedEvents = historyEvents;
  const displayEvents = useMemo(() => mergeDisplayEvents(sortedEvents), [sortedEvents]);
  const filteredEvents = useMemo(
    () => (filterMode === 'devices' ? displayEvents.filter(isDeviceEvent) : displayEvents),
    [filterMode, displayEvents],
  );
  const bleAdvCount = sortedEvents.filter((event) => event?.kind === 'ble_adv').length;
  const btcCount = sortedEvents.filter(isBtcEvent).length;
  const fmCount = sortedEvents.filter(isFmEvent).length;
  const fmStationCount = sortedEvents.filter((event) => isFmEvent(event) && event?.decode_status === 'station').length;
  const fmPotentialCount = Math.max(0, fmCount - fmStationCount);
  const deviceEvents = displayEvents.filter(isDeviceEvent);
  const uniqueDevices = new Set(deviceEvents.map(eventIdentity).filter(Boolean)).size;
  const decoderActive = Boolean(telemetry?.bluetooth?.active || telemetry?.fm?.active);
  const emptyText = filterMode === 'devices'
    ? 'No identified devices in the retained activity window yet. Burst-only detections are hidden in this view.'
    : 'No decoded signal activity yet. Tune into an active band and decoded packets will appear here.';
  const protocolGroups = useMemo(() => {
    const groups = Array.from(groupRowsBy(filteredEvents, protocolKey).entries())
      .sort(([a], [b]) => protocolSortRank(a) - protocolSortRank(b) || String(a).localeCompare(String(b)));
    return groups.map(([protocol, rows]) => ({
      protocol,
      rows: [...rows].sort((a, b) => {
        if (isFmEvent(a) && isFmEvent(b)) return Number(a?.frequency_mhz || 0) - Number(b?.frequency_mhz || 0);
        return eventSeenAt(b) - eventSeenAt(a);
      }),
      stats: summaryStats(rows),
    }));
  }, [filteredEvents]);
  const foldProtocolGroups = protocolGroups.length > 1 || filteredEvents.length > 3;

  const ensureAudio = async () => {
    if (!audioCtxRef.current) audioCtxRef.current = new AudioContext({ sampleRate: 48000 });
    if (!gainNodeRef.current) {
      gainNodeRef.current = audioCtxRef.current.createGain();
      gainNodeRef.current.gain.value = 0.75;
      gainNodeRef.current.connect(audioCtxRef.current.destination);
    }
    if (audioCtxRef.current.state === 'suspended') await audioCtxRef.current.resume();
    if (playCursorRef.current < audioCtxRef.current.currentTime + 0.25) {
      playCursorRef.current = audioCtxRef.current.currentTime + 0.25;
    }
  };

  const schedulePcm16 = (arrayBuffer) => {
    const audioCtx = audioCtxRef.current;
    const gainNode = gainNodeRef.current;
    if (!audioCtx || !gainNode) return;
    const pcm = new Int16Array(arrayBuffer);
    const frames = Math.floor(pcm.length / 2);
    if (!frames) return;
    const left = new Float32Array(frames);
    const right = new Float32Array(frames);
    for (let i = 0; i < frames; i += 1) {
      left[i] = pcm[i * 2] / 32768.0;
      right[i] = pcm[i * 2 + 1] / 32768.0;
    }
    const buffer = audioCtx.createBuffer(2, frames, 48000);
    buffer.copyToChannel(left, 0, 0);
    buffer.copyToChannel(right, 1, 0);
    const source = audioCtx.createBufferSource();
    source.buffer = buffer;
    source.connect(gainNode);
    const startAt = Math.max(playCursorRef.current, audioCtx.currentTime + 0.02);
    source.start(startAt);
    playCursorRef.current = startAt + buffer.duration;
    if ((playCursorRef.current - audioCtx.currentTime) > 1.0) {
      playCursorRef.current = audioCtx.currentTime + 0.3;
    }
  };

  const audioLoop = async () => {
    if (audioLoopActiveRef.current) return;
    audioLoopActiveRef.current = true;
    while (playingRef.current) {
      try {
        await ensureAudio();
        const response = await fetch('/api/fm/audio/batch?count=6&timeout=0.4', { cache: 'no-store' });
        if (response.status === 204) {
          await new Promise((resolve) => setTimeout(resolve, 120));
          continue;
        }
        if (!response.ok) throw new Error(`audio fetch failed (${response.status})`);
        schedulePcm16(await response.arrayBuffer());
      } catch (error) {
        setPlaybackError(error?.message || 'FM playback error');
        await new Promise((resolve) => setTimeout(resolve, 160));
      }
    }
    audioLoopActiveRef.current = false;
  };

  const stopPlayback = async () => {
    playingRef.current = null;
    setPlayingFrequency(null);
    try {
      await fetch('/api/fm/stop', { method: 'POST' });
    } catch (error) {
      // Non-fatal: playback loop is already stopped locally.
    }
  };

  const startPlayback = async (event) => {
    const frequency = Number(event?.frequency_mhz);
    if (!Number.isFinite(frequency)) return;
    setPlaybackError('');
    await ensureAudio();
    if (playingRef.current === frequency) {
      await stopPlayback();
      return;
    }
    const response = await fetch('/api/fm/play', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ frequency_mhz: frequency }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok || payload?.ok === false) {
      setPlaybackError(payload?.error || 'FM station is not ready to play');
      return;
    }
    playingRef.current = frequency;
    setPlayingFrequency(frequency);
    audioLoop();
  };

  useEffect(() => () => {
    playingRef.current = null;
    fetch('/api/fm/stop', { method: 'POST' }).catch(() => {});
  }, []);

  const renderEventCard = (event, idx) => {
    const isBtc = String(event?.protocol || '').toLowerCase() === 'btc';
    const isFm = isFmEvent(event);
    const accent = isFm ? '#ffb347' : (isBtc ? '#ffd166' : '#64f0d2');
    const EventIcon = isFm ? RadioIcon : BluetoothIcon;
    const uaps = Array.isArray(event?.uap_options) ? event.uap_options : [];
    const footprintLabel = eventFootprintLabel(event);
    return (
      <Paper
        key={eventKey(event, idx)}
        elevation={0}
        sx={{
          mb: 1,
          p: 1.25,
          borderRadius: 2,
          border: '1px solid rgba(255,255,255,0.12)',
          borderLeft: `4px solid ${accent}`,
          bgcolor: 'rgba(8, 10, 12, 0.9)',
        }}
      >
        <Stack direction="row" justifyContent="space-between" spacing={1}>
          <Stack direction="row" spacing={0.75} alignItems="center" sx={{ minWidth: 0 }}>
            <EventIcon fontSize="small" sx={{ color: accent, flexShrink: 0 }} />
            <Typography variant="subtitle2" sx={{ fontWeight: 800, overflow: 'hidden', textOverflow: 'ellipsis' }}>{eventTitle(event)}</Typography>
          </Stack>
          <Stack direction="row" spacing={0.5} alignItems="center" sx={{ flexShrink: 0 }}>
            {isFm && event?.decode_status === 'station' ? (
              <Tooltip title={playingFrequency === Number(event?.frequency_mhz) ? 'Stop FM audio' : 'Play FM audio'}>
                <IconButton
                  size="small"
                  onClick={() => startPlayback(event)}
                  sx={{ color: accent, p: 0.25 }}
                  aria-label={playingFrequency === Number(event?.frequency_mhz) ? 'Stop FM audio' : 'Play FM audio'}
                >
                  {playingFrequency === Number(event?.frequency_mhz) ? <StopIcon fontSize="small" /> : <PlayArrowIcon fontSize="small" />}
                </IconButton>
              </Tooltip>
            ) : null}
            <Typography variant="caption" color="text.secondary">{formatAge(event?.seen_at)}</Typography>
          </Stack>
        </Stack>
        <Stack direction="row" spacing={0.5} sx={{ mt: 0.75, flexWrap: 'wrap', gap: 0.5 }}>
          <Chip
            size="small"
            icon={<EventIcon />}
            label={protocolLabel(event)}
            sx={{ bgcolor: `${accent}22`, color: '#fff', '& .MuiChip-icon': { color: accent } }}
          />
          {footprintLabel ? <Chip size="small" label={footprintLabel} /> : null}
          {isFm && event?.frequency_mhz !== undefined ? <Chip size="small" label={`${Number(event.frequency_mhz).toFixed(1)} MHz`} /> : null}
          {isFm ? <Chip size="small" color={event?.decode_status === 'station' ? 'success' : 'warning'} label={event?.decode_status === 'station' ? 'station' : 'potential'} /> : null}
          {event?.kind === 'ble_adv' ? <Chip size="small" label="ADV" /> : null}
          {isBtc && normalizeUap(event?.uap || event?.uap_hex) ? <Chip size="small" label={`UAP ${normalizeUap(event?.uap || event?.uap_hex)}`} /> : null}
          {isBtc && normalizedLap(event) ? <Chip size="small" label={`LAP ${normalizedLap(event)}`} /> : null}
          {event?.channel !== undefined ? <Chip size="small" label={`CH ${event.channel}`} /> : null}
          {isBtc && Number(event?.candidate_count || 0) > 1 ? <Chip size="small" label={`${Number(event.candidate_count)} UAP candidates`} /> : null}
          {isBtc && Number(event?.detections || 0) > 1 ? <Chip size="small" label={`${Number(event.detections)} sightings`} /> : null}
          {isBtc && uaps.length > 1 ? <Chip size="small" label={`UAPs ${uaps.slice(0, 4).join(' ')}`} /> : null}
          {event?.rssi_dbfs !== undefined ? <Chip size="small" label={`${Number(event.rssi_dbfs).toFixed(1)} dBFS`} /> : null}
          {isFm && event?.excess_db !== undefined ? <Chip size="small" label={`+${Number(event.excess_db).toFixed(1)} dB`} /> : null}
          {isFm && event?.pilot_db !== undefined ? <Chip size="small" label={`pilot ${Number(event.pilot_db).toFixed(1)} dB`} /> : null}
          {isFm && event?.rds_likely ? <Chip size="small" label="RDS likely" /> : null}
          {isFm && Number(event?.sightings || 0) > 1 ? <Chip size="small" label={`${Number(event.sightings)} sightings`} /> : null}
          {event?.confidence !== undefined ? <Chip size="small" label={`${Math.round(Number(event.confidence) * 100)}%`} /> : null}
        </Stack>
        <Divider sx={{ my: 1, borderColor: 'rgba(255,255,255,0.08)' }} />
        <Typography variant="body2" color="text.secondary">{eventDetail(event)}</Typography>
        {isFm && playbackError && playingFrequency === Number(event?.frequency_mhz) ? (
          <Typography variant="caption" color="error" sx={{ mt: 0.75, display: 'block' }}>
            {playbackError}
          </Typography>
        ) : null}
        {event?.address ? (
          <Typography variant="caption" sx={{ mt: 0.75, display: 'block', fontFamily: 'monospace' }}>
            {event.address}
          </Typography>
        ) : null}
        {!event?.address && isBtc ? (
          <Typography variant="caption" sx={{ mt: 0.75, display: 'block', fontFamily: 'monospace' }}>
            {event?.full_mac || candidateMac(event)}
          </Typography>
        ) : null}
      </Paper>
    );
  };

  const renderGroupedEvents = () => protocolGroups.map(({ protocol, rows, stats }, groupIndex) => {
    const accent = protocol === 'FM' ? '#ffb347' : (protocol === 'BTC' ? '#ffd166' : '#64f0d2');
    const defaultOpen = !foldProtocolGroups || groupIndex < 1;
    const body = protocol === 'BTLE'
      ? Array.from(groupRowsBy(rows, manufacturerGroupLabel).entries())
        .sort(([a], [b]) => String(a).localeCompare(String(b)))
        .map(([label, manufacturerRows], idx) => {
          const subgroupStats = summaryStats(manufacturerRows);
          return (
            <Paper
              key={`${protocol}-${label}`}
              component="details"
              defaultOpen={idx < 2}
              elevation={0}
              sx={{
                mb: 1,
                borderRadius: 2,
                border: '1px solid rgba(100,240,210,0.18)',
                bgcolor: 'rgba(255,255,255,0.025)',
                overflow: 'hidden',
                '& summary': { cursor: 'pointer', listStyle: 'none', p: 1, '&::-webkit-details-marker': { display: 'none' } },
              }}
            >
              <Stack component="summary" direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
                <Stack direction="row" spacing={0.75} alignItems="center" sx={{ minWidth: 0 }}>
                  <BluetoothIcon fontSize="small" sx={{ color: accent }} />
                  <Typography variant="subtitle2" sx={{ fontWeight: 800, overflow: 'hidden', textOverflow: 'ellipsis' }}>{label}</Typography>
                </Stack>
                <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', justifyContent: 'flex-end' }}>
                  <Chip size="small" label={`${manufacturerRows.length} device${manufacturerRows.length === 1 ? '' : 's'}`} />
                  <Chip size="small" label={`${subgroupStats.detections} detections`} />
                  <Chip size="small" label={`${subgroupStats.bestRssi.toFixed(1)} dBFS`} />
                  <Chip size="small" label={formatAge(subgroupStats.lastSeen)} />
                </Stack>
              </Stack>
              <Box sx={{ px: 1, pb: 1 }}>
                {manufacturerRows.map(renderEventCard)}
              </Box>
            </Paper>
          );
        })
      : rows.map(renderEventCard);

    return (
      <Paper
        key={`protocol-${protocol}`}
        component="details"
        defaultOpen={defaultOpen}
        elevation={0}
        sx={{
          mb: 1.25,
          borderRadius: 2,
          border: `1px solid ${accent}44`,
          bgcolor: 'rgba(6, 12, 16, 0.82)',
          overflow: 'hidden',
          '& summary': { cursor: 'pointer', listStyle: 'none', p: 1.1, '&::-webkit-details-marker': { display: 'none' } },
        }}
      >
        <Stack component="summary" direction="row" alignItems="center" justifyContent="space-between" spacing={1}>
          <Stack direction="row" spacing={0.75} alignItems="center" sx={{ minWidth: 0 }}>
            {protocol === 'FM' ? <RadioIcon fontSize="small" sx={{ color: accent }} /> : <BluetoothIcon fontSize="small" sx={{ color: accent }} />}
            <Typography variant="subtitle1" sx={{ fontWeight: 900, overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {protocolGroupLabel(protocol)}
            </Typography>
          </Stack>
          <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <Chip size="small" sx={{ bgcolor: `${accent}22`, color: '#fff' }} label={protocol} />
            <Chip size="small" label={`${rows.length} card${rows.length === 1 ? '' : 's'}`} />
            <Chip size="small" label={`${stats.detections} detections`} />
            <Chip size="small" label={`${stats.bestRssi.toFixed(1)} dBFS`} />
            <Chip size="small" label={formatAge(stats.lastSeen)} />
          </Stack>
        </Stack>
        <Box sx={{ px: 1, pb: 1 }}>
          {body}
        </Box>
      </Paper>
    );
  });

  return (
    <Box sx={{ height: '100%', minHeight: 0, display: 'flex', flexDirection: 'column', gap: 1.25 }}>
      <Paper
        elevation={0}
        sx={{
          p: 1.5,
          borderRadius: 2,
          border: '1px solid #253342',
          background: 'linear-gradient(135deg, rgba(8, 24, 30, 0.96), rgba(16, 18, 22, 0.96))',
        }}
      >
        <Typography variant="overline" color="text.secondary">Decoded Intelligence</Typography>
        <Stack direction="row" spacing={1} alignItems="center">
          <RadioIcon fontSize="small" sx={{ color: '#64f0d2' }} />
          <Typography variant="h6" sx={{ lineHeight: 1.15 }}>Signal Activity</Typography>
        </Stack>
        <Stack direction="row" spacing={0.75} sx={{ mt: 1, flexWrap: 'wrap', gap: 0.75 }}>
          <Chip size="small" color={decoderActive ? 'success' : 'default'} label={decoderActive ? 'decoder on' : 'decoder idle'} />
          <Chip size="small" label={`${bleAdvCount} BLE adv`} />
          <Chip size="small" label={`${btcCount} BTC`} />
          <Chip size="small" label={`${fmStationCount} FM stations`} />
          <Chip size="small" label={`${fmPotentialCount} potential`} />
          <Chip size="small" label={`${uniqueDevices} devices`} />
          <Chip size="small" label={`${Math.round(retentionSec / 60)}m retained`} />
          <Chip
            size="small"
            clickable
            color={filterMode === 'all' ? 'primary' : 'default'}
            label="All"
            onClick={() => setFilterMode('all')}
          />
          <Chip
            size="small"
            clickable
            color={filterMode === 'devices' ? 'primary' : 'default'}
            label="Devices"
            onClick={() => setFilterMode('devices')}
          />
        </Stack>
      </Paper>

      <Box sx={{ flex: 1, minHeight: 0, overflowY: 'auto', pr: 0.5 }}>
        {filteredEvents.length === 0 ? (
          <Paper
            elevation={0}
            sx={{
              p: 2,
              borderRadius: 2,
              border: '1px dashed #334',
              bgcolor: 'rgba(255,255,255,0.03)',
            }}
          >
            <Typography variant="body2" color="text.secondary">
              {emptyText}
            </Typography>
          </Paper>
        ) : renderGroupedEvents()}
      </Box>
    </Box>
  );
};

export default DecodedEventsPanel;
