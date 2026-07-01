import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Box, Button, Chip, Divider, IconButton, Paper, Stack, Tooltip, Typography } from '@mui/material';
import BluetoothIcon from '@mui/icons-material/Bluetooth';
import EventRepeatIcon from '@mui/icons-material/EventRepeat';
import FlightTakeoffIcon from '@mui/icons-material/FlightTakeoff';
import PlayArrowIcon from '@mui/icons-material/PlayArrow';
import RadioIcon from '@mui/icons-material/Radio';
import SensorsIcon from '@mui/icons-material/Sensors';
import StopIcon from '@mui/icons-material/Stop';
import WifiIcon from '@mui/icons-material/Wifi';

const PATTERN_OF_LIFE_STORAGE_KEY = 'sdrshark_pattern_of_life_v1';
const PATTERN_OF_LIFE_MAX_DAYS = 90;
const PATTERN_OF_LIFE_WINDOW_DAYS = 30;
const MS_PER_DAY = 24 * 60 * 60 * 1000;

const formatAge = (seenAt) => {
  const ts = Number(seenAt);
  if (!Number.isFinite(ts)) return 'now';
  const ageSec = Math.max(0, Math.round((Date.now() / 1000) - ts));
  if (ageSec < 60) return `${ageSec}s ago`;
  const ageMin = Math.round(ageSec / 60);
  if (ageMin < 60) return `${ageMin}m ago`;
  return `${Math.round(ageMin / 60)}h ago`;
};

const hasValue = (value) => value !== undefined && value !== null && value !== '';

const dayKeyFromSeenAt = (seenAt) => {
  const ts = Number(seenAt);
  const date = new Date(Number.isFinite(ts) ? ts * 1000 : Date.now());
  return date.toISOString().slice(0, 10);
};

const dayIndexFromKey = (dayKey) => {
  const value = Date.parse(`${dayKey}T00:00:00.000Z`);
  return Number.isFinite(value) ? Math.floor(value / MS_PER_DAY) : 0;
};

const todayDayIndex = () => Math.floor(Date.now() / MS_PER_DAY);

const loadPatternOfLifeStore = () => {
  if (typeof window === 'undefined') return {};
  try {
    const parsed = JSON.parse(window.localStorage.getItem(PATTERN_OF_LIFE_STORAGE_KEY) || '{}');
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch (error) {
    return {};
  }
};

const savePatternOfLifeStore = (store) => {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(PATTERN_OF_LIFE_STORAGE_KEY, JSON.stringify(store));
  } catch (error) {
    // Non-fatal; the activity panel should keep working if browser storage is full.
  }
};

const prunePatternOfLifeStore = (store) => {
  const cutoffDay = todayDayIndex() - PATTERN_OF_LIFE_MAX_DAYS;
  return Object.fromEntries(Object.entries(store || {}).flatMap(([key, entry]) => {
    const days = Object.fromEntries(Object.entries(entry?.days || {}).filter(([day]) => dayIndexFromKey(day) >= cutoffDay));
    if (Object.keys(days).length === 0) return [];
    const sortedDays = Object.keys(days).sort();
    return [[key, {
      ...entry,
      days,
      first_seen_date: sortedDays[0],
      last_seen_date: sortedDays[sortedDays.length - 1],
      seen_day_count: sortedDays.length,
    }]];
  }));
};

const eventKey = (event, idx) => (
  event?.address ||
  event?.packet ||
  (event?.kind === 'fm_station' && event?.frequency_mhz ? `fm-${event.frequency_mhz}` : '') ||
  (event?.kind === 'wifi_frame' ? `wifi-frame-${event.bssid || event.transmitter || event.source_mac || 'mac'}-${event.sequence || event.seen_at || idx}` : '') ||
  (event?.kind === 'wifi_activity' ? `wifi-${event.channel || event.likely_center_freq_hz || 'wide'}-${event.sample_index || event.seen_at || idx}` : '') ||
  (event?.kind === 'zigbee_frame' && event?.psdu_hex ? `zigbee-${event.channel || 'ch'}-${event.psdu_hex}` : '') ||
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
const isWifiEvent = (event) => String(event?.protocol || '').toLowerCase() === 'wifi' || ['wifi_activity', 'wifi_frame'].includes(event?.kind);
const isWifiFrame = (event) => isWifiEvent(event) && event?.kind === 'wifi_frame';
const isZigbeeEvent = (event) => String(event?.protocol || '').toLowerCase() === 'zigbee' || event?.kind === 'zigbee_frame';
const isAdsbEvent = (event) => String(event?.protocol || '').toLowerCase() === 'adsb' || String(event?.kind || '').startsWith('adsb_');

const protocolKey = (event) => {
  const protocol = String(event?.protocol || '').toLowerCase();
  if (protocol === 'btc') return 'BTC';
  if (protocol === 'ble') return 'BTLE';
  if (protocol === 'fm' || event?.kind === 'fm_station') return 'FM';
  if (protocol === 'wifi' || event?.kind === 'wifi_activity') return 'WIFI';
  if (protocol === 'zigbee' || event?.kind === 'zigbee_frame') return 'ZIGBEE';
  if (protocol === 'adsb' || String(event?.kind || '').startsWith('adsb_')) return 'ADSB';
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

const wifiIdentity = (event) => (
  event?.ssid ||
  event?.bssid ||
  event?.source_mac ||
  event?.transmitter ||
  event?.destination ||
  event?.receiver ||
  ''
);

const mergeWifiRows = (rows) => {
  const groups = new Map();
  rows.filter(isWifiFrame).forEach((row, idx) => {
    const key = wifiIdentity(row) || eventKey(row, idx);
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(row);
  });

  return Array.from(groups.values()).map((groupRows) => {
    const latest = groupRows.reduce(
      (winner, row) => (eventSeenAt(row) > eventSeenAt(winner) ? row : winner),
      groupRows[0],
    );
    const named = groupRows.find((row) => row?.ssid) || latest;
    const channels = Array.from(new Set(groupRows.map((row) => row?.channel).filter(hasValue))).sort((a, b) => Number(a) - Number(b));
    const sourceMacs = Array.from(new Set(groupRows.map((row) => row?.source_mac || row?.transmitter).filter(Boolean))).slice(0, 4);
    return {
      ...latest,
      ...named,
      protocol: 'wifi',
      kind: 'wifi_frame',
      detections: groupRows.length,
      group_count: groupRows.length,
      channels,
      source_macs: sourceMacs,
      seen_at: eventSeenAt(latest),
      rssi_dbfs: latest?.rssi_dbfs ?? named?.rssi_dbfs,
      rssi_dbm: latest?.rssi_dbm ?? named?.rssi_dbm,
      channel: latest?.channel ?? named?.channel,
    };
  });
};

const mergeDisplayEvents = (events) => {
  const btcRows = mergeBtcRows(events);
  const wifiRows = mergeWifiRows(events);
  const hasWifiFrames = wifiRows.length > 0;
  const nonMergedRows = events.filter((event) => (
    !isBtcEvent(event) &&
    !isWifiFrame(event) &&
    !(hasWifiFrames && event?.kind === 'wifi_activity')
  ));
  return [...nonMergedRows, ...wifiRows, ...btcRows].sort((a, b) => {
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
  if (isAdsbEvent(event)) return event?.icao || event?.flight || '';
  if (isWifiEvent(event)) return wifiIdentity(event) || `wifi-${event?.channel || event?.likely_center_freq_hz || ''}`;
  if (isZigbeeEvent(event)) return event?.mac?.source_address || event?.mac?.destination_address || event?.psdu_hex || '';
  if (event?.identity) return event.identity;
  if (event?.name) return event.name;
  if (event?.lap) return `BTC LAP ${event.lap}`;
  if (event?.manufacturer?.company_name) return event.manufacturer.company_name;
  return '';
};

const patternOfLifeKey = (event) => {
  const identity = String(eventIdentity(event) || '').trim();
  if (!identity) return '';
  return `${protocolKey(event)}:${identity.toUpperCase()}`;
};

const summarizePatternOfLife = (entry) => {
  const days = Object.keys(entry?.days || {}).sort();
  if (!days.length) return null;
  const dayIndexes = new Set(days.map(dayIndexFromKey));
  let streak = 0;
  let cursor = dayIndexFromKey(days[days.length - 1]);
  while (dayIndexes.has(cursor)) {
    streak += 1;
    cursor -= 1;
  }
  const recentCutoff = todayDayIndex() - (PATTERN_OF_LIFE_WINDOW_DAYS - 1);
  const recentDays = days.filter((day) => dayIndexFromKey(day) >= recentCutoff).length;
  return {
    ...entry,
    seen_days: streak,
    seen_day_count: days.length,
    recent_days: recentDays,
    first_seen_date: days[0],
    last_seen_date: days[days.length - 1],
  };
};

const patternChipLabel = (pattern) => {
  const streak = Number(pattern?.seen_days || 0);
  if (streak > 1) return `${streak}-day streak`;
  if (streak === 1) return '1 day';
  return `seen ${Number(pattern?.recent_days || 0)}/${PATTERN_OF_LIFE_WINDOW_DAYS}d`;
};

const patternChipTitle = (pattern) => {
  const streak = Number(pattern?.seen_days || 0);
  const total = Math.max(streak, Number(pattern?.seen_day_count || 0));
  const range = pattern?.first_seen_date && pattern?.last_seen_date
    ? `${pattern.first_seen_date} to ${pattern.last_seen_date}`
    : '';
  return [
    streak ? `${streak} consecutive UTC day${streak === 1 ? '' : 's'}` : '',
    total ? `${total} total UTC day${total === 1 ? '' : 's'}` : '',
    Number(pattern?.recent_days || 0) ? `${Number(pattern.recent_days)} seen in the last ${PATTERN_OF_LIFE_WINDOW_DAYS} UTC days` : '',
    range,
  ].filter(Boolean).join(' · ');
};

const patternChipSx = {
  color: '#142f24',
  bgcolor: 'transparent',
  background: 'linear-gradient(135deg, #b8f3ce, #f5d36b)',
  border: '1px solid rgba(255, 247, 211, 0.45)',
  fontWeight: 850,
  '& .MuiChip-icon': { color: '#1b6b4f' },
};

const isDeviceEvent = (event) => Boolean(
  eventIdentity(event) ||
  event?.device_type ||
  event?.device_type_detail ||
  isFmEvent(event) ||
  isWifiEvent(event) ||
  isZigbeeEvent(event) ||
  isAdsbEvent(event) ||
  String(event?.protocol || '').toLowerCase() === 'btc',
);

const eventTitle = (event) => {
  if (event?.identity) return event.identity;
  if (event?.name) return event.name;
  if (event?.address) return event.address;
  if (isAdsbEvent(event)) {
    const flight = String(event?.flight || '').trim();
    return flight || (event?.icao ? `Aircraft ${event.icao}` : 'ADS-B aircraft');
  }
  if (isBtcEvent(event)) return event?.full_mac || candidateMac(event);
  if (isFmEvent(event)) return event?.identity || `FM ${Number(event?.frequency_mhz || 0).toFixed(1)} MHz`;
  if (isWifiEvent(event)) {
    if (event?.kind === 'wifi_frame') return event?.ssid ? `WiFi ${event.ssid}` : (wifiIdentity(event) || 'WiFi frame');
    return `WiFi activity${event?.channel ? ` CH ${event.channel}` : ''}`;
  }
  if (isZigbeeEvent(event)) {
    const frameType = event?.mac?.frame_type ? `${event.mac.frame_type} ` : '';
    return `Zigbee ${frameType}CH ${event?.channel ?? '?'}`;
  }
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
    WIFI: 'WiFi / 802.11',
    ZIGBEE: 'Zigbee / 802.15.4',
    ADSB: 'ADS-B Aircraft',
    RF: 'Radio Events',
  };
  return labels[String(protocol || 'RF').toUpperCase()] || String(protocol || 'RF');
};

const protocolSortRank = (protocol) => {
  const rank = ['BTC', 'BTLE', 'WIFI', 'ZIGBEE', 'FM', 'ADSB', 'RF'].indexOf(String(protocol || '').toUpperCase());
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
  if (isWifiEvent(event)) {
    if (event?.kind === 'wifi_frame') return event?.ssid ? `SSID ${event.ssid}` : (event?.bssid ? `BSSID ${event.bssid}` : 'WiFi frames');
    return event?.channel ? `WiFi CH ${event.channel}` : 'WiFi / 802.11';
  }
  if (isZigbeeEvent(event)) return event?.mac?.source_pan_id ? `PAN ${event.mac.source_pan_id}` : 'Zigbee / 802.15.4';
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
      .map((row) => Number(row?.rssi_dbfs ?? row?.rssi_dbm ?? row?.power_dbfs ?? row?.last_rssi_dbfs))
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
  if (protocol === 'wifi') return '~20 MHz ch';
  if (protocol === 'fm') return '~200 kHz ch';
  if (protocol === 'zigbee') return '~2 MHz ch';
  if (protocol === 'adsb') return '1090 MHz';
  return '';
};

const eventDetail = (event) => {
  if (isFmEvent(event)) {
    const excess = Number(event?.excess_db);
    if (event?.detail) return event.detail;
    if (Number.isFinite(excess)) return `FM broadcast carrier detected ${excess.toFixed(1)} dB above local noise.`;
    return 'FM broadcast carrier detected from the live spectrum.';
  }
  if (isWifiEvent(event)) {
    if (event?.kind === 'wifi_frame') {
      const parts = [];
      if (event?.subtype) parts.push(`${event.subtype} frame`);
      if (event?.ssid) parts.push(`SSID ${event.ssid}`);
      if (event?.source_mac) parts.push(`SA ${event.source_mac}`);
      if (event?.destination) parts.push(`DA ${event.destination}`);
      if (event?.bssid) parts.push(`BSSID ${event.bssid}`);
      if (event?.transmitter && event.transmitter !== event.source_mac) parts.push(`TA ${event.transmitter}`);
      if (event?.receiver && event.receiver !== event.destination) parts.push(`RA ${event.receiver}`);
      return parts.length ? parts.join(' · ') : 'Decoded 802.11 MAC frame from pyshark/tshark.';
    }
    const score = Number(event?.score ?? event?.confidence);
    const center = hasValue(event?.likely_center_freq_hz) ? Number(event.likely_center_freq_hz) : NaN;
    const parts = ['802.11 OFDM short-training activity detected'];
    if (Number.isFinite(center)) parts.push(`${(center / 1e6).toFixed(1)} MHz`);
    if (Number.isFinite(score)) parts.push(`score ${score.toFixed(2)}`);
    return parts.join(' · ');
  }
  if (isAdsbEvent(event)) {
    const parts = [];
    if (event?.icao) parts.push(`ICAO ${event.icao}`);
    if (event?.flight) parts.push(`flight ${String(event.flight).trim()}`);
    if (Number(event?.altitude_ft)) parts.push(`${Number(event.altitude_ft).toLocaleString()} ft`);
    if (Number(event?.speed_kt)) parts.push(`${Number(event.speed_kt)} kt`);
    if (Number(event?.track_deg)) parts.push(`track ${Number(event.track_deg)}°`);
    return parts.length ? parts.join(' · ') : 'Decoded Mode S / ADS-B message from 1090 MHz.';
  }
  if (event?.device_type_detail) return event.device_type_detail;
  if (isZigbeeEvent(event)) {
    const mac = event?.mac || {};
    const parts = [];
    if (mac.frame_type) parts.push(`${mac.frame_type} frame`);
    if (event?.decoded_text) parts.push(`text "${event.decoded_text}"`);
    if (mac.source_address) parts.push(`src ${mac.source_address}`);
    if (mac.destination_address) parts.push(`dst ${mac.destination_address}`);
    if (event?.fcs_ok) parts.push('FCS OK');
    return parts.length ? parts.join(' · ') : 'Decoded IEEE 802.15.4/Zigbee frame from the shared 2.4 GHz stream.';
  }
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
      const wifiEvents = Array.isArray(telemetry?.wifi?.events) ? telemetry.wifi.events : [];
      const zigbeeEvents = Array.isArray(telemetry?.zigbee?.events) ? telemetry.zigbee.events : [];
      const adsbEvents = Array.isArray(telemetry?.adsb?.events) ? telemetry.adsb.events : [];
      return [...bluetoothEvents, ...fmEvents, ...wifiEvents, ...zigbeeEvents, ...adsbEvents];
    },
    [telemetry?.bluetooth?.events, telemetry?.fm?.events, telemetry?.wifi?.events, telemetry?.zigbee?.events, telemetry?.adsb?.events],
  );
  const retentionSec = Math.max(60, Math.min(3600, Number(settings?.activityLogRetentionSec) || 600));
  const maxHistoryEvents = Math.max(500, Math.min(5000, Math.round((retentionSec / 60) * 240)));
  const [historyEvents, setHistoryEvents] = useState([]);
  const [clearedAt, setClearedAt] = useState(0);
  const [filterMode, setFilterMode] = useState('all');
  const [playingFrequency, setPlayingFrequency] = useState(null);
  const [playbackError, setPlaybackError] = useState('');
  const [patternOfLifeStore, setPatternOfLifeStore] = useState(loadPatternOfLifeStore);
  const audioCtxRef = useRef(null);
  const gainNodeRef = useRef(null);
  const playCursorRef = useRef(0);
  const audioLoopActiveRef = useRef(false);
  const playingRef = useRef(null);
  const patternProcessedKeysRef = useRef(new Set());

  useEffect(() => {
    setHistoryEvents((prev) => {
      const byKey = new Map();
      prev.forEach((event, idx) => {
        if (eventSeenAt(event) > clearedAt) {
          byKey.set(eventKey(event, idx), event);
        }
      });
      events.forEach((event, idx) => {
        if (eventSeenAt(event) > clearedAt) {
          byKey.set(eventKey(event, idx), event);
        }
      });

      const cutoff = Math.max((Date.now() / 1000) - retentionSec, clearedAt);
      return Array.from(byKey.values())
        .filter((event) => eventSeenAt(event) >= cutoff)
        .sort((a, b) => eventSeenAt(b) - eventSeenAt(a))
        .slice(0, maxHistoryEvents);
    });
  }, [events, retentionSec, maxHistoryEvents, clearedAt]);

  useEffect(() => {
    if (!events.length) return;
    setPatternOfLifeStore((prev) => {
      const next = prunePatternOfLifeStore(prev);
      let changed = false;
      events.forEach((event, idx) => {
        if (!isDeviceEvent(event)) return;
        const patternKey = patternOfLifeKey(event);
        if (!patternKey) return;
        const seenAt = eventSeenAt(event);
        const dayKey = dayKeyFromSeenAt(seenAt);
        const processedKey = `${dayKey}:${eventKey(event, idx)}`;
        if (patternProcessedKeysRef.current.has(processedKey)) return;
        patternProcessedKeysRef.current.add(processedKey);
        if (patternProcessedKeysRef.current.size > 10000) {
          patternProcessedKeysRef.current = new Set(Array.from(patternProcessedKeysRef.current).slice(-5000));
        }
        const previous = next[patternKey] || {};
        const days = {
          ...(previous.days || {}),
          [dayKey]: Number(previous.days?.[dayKey] || 0) + 1,
        };
        const sortedDays = Object.keys(days).sort();
        const rssi = Number(event?.rssi_dbfs ?? event?.rssi_dbm ?? event?.power_dbfs ?? event?.last_rssi_dbfs);
        const previousBestRssi = Number(previous.last_rssi_dbfs);
        const entry = {
          ...previous,
          protocol: protocolKey(event),
          identity: eventIdentity(event),
          title: eventTitle(event),
          days,
          first_seen_at: Math.min(Number(previous.first_seen_at || seenAt), seenAt),
          last_seen_at: Math.max(Number(previous.last_seen_at || 0), seenAt),
          first_seen_date: sortedDays[0],
          last_seen_date: sortedDays[sortedDays.length - 1],
          seen_day_count: sortedDays.length,
          seen_count: Number(previous.seen_count || 0) + 1,
          detections: Number(previous.detections || 0) + Math.max(1, Number(event?.detections || event?.group_count || 1)),
          last_rssi_dbfs: Number.isFinite(rssi)
            ? (Number.isFinite(previousBestRssi) ? Math.max(previousBestRssi, rssi) : rssi)
            : previous.last_rssi_dbfs,
        };
        next[patternKey] = {
          ...entry,
          ...summarizePatternOfLife(entry),
        };
        changed = true;
      });
      return changed ? prunePatternOfLifeStore(next) : prev;
    });
  }, [events]);

  useEffect(() => {
    savePatternOfLifeStore(patternOfLifeStore);
  }, [patternOfLifeStore]);

  const clearActivity = () => {
    setClearedAt(Date.now() / 1000);
    setHistoryEvents([]);
  };

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
  const wifiCount = sortedEvents.filter(isWifiEvent).length;
  const wifiFrameCount = sortedEvents.filter((event) => isWifiEvent(event) && event?.kind === 'wifi_frame').length;
  const zigbeeCount = sortedEvents.filter(isZigbeeEvent).length;
  const adsbCount = sortedEvents.filter(isAdsbEvent).length;
  const deviceEvents = displayEvents.filter(isDeviceEvent);
  const uniqueDevices = new Set(deviceEvents.map(eventIdentity).filter(Boolean)).size;
  const decoderActive = Boolean(telemetry?.bluetooth?.active || telemetry?.fm?.active || telemetry?.wifi?.active || telemetry?.zigbee?.active || telemetry?.adsb?.active);
  const scannerMode = telemetry?.scannerMode || null;
  const scannerStep = scannerMode?.step || {};
  const scannerCenterMHz = Number(scannerStep?.applied_center_hz || scannerStep?.center_hz || 0) / 1e6;
  const scannerLabel = scannerStep?.label || 'selected protocols';
  const scannerFrequencyLabel = Number.isFinite(scannerCenterMHz) && scannerCenterMHz > 0
    ? ` @ ${scannerCenterMHz.toFixed(1)} MHz`
    : '';
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
  const patternForEvent = (event) => summarizePatternOfLife(patternOfLifeStore[patternOfLifeKey(event)]);
  const bestPatternForRows = (rows) => rows
    .map(patternForEvent)
    .filter(Boolean)
    .sort((a, b) => (
      Number(b.seen_days || 0) - Number(a.seen_days || 0) ||
      Number(b.seen_day_count || 0) - Number(a.seen_day_count || 0) ||
      Number(b.last_seen_at || 0) - Number(a.last_seen_at || 0)
    ))[0] || null;
  const renderPatternChip = (pattern) => (pattern ? (
    <Tooltip title={patternChipTitle(pattern)}>
      <Chip
        size="small"
        icon={<EventRepeatIcon />}
        label={patternChipLabel(pattern)}
        sx={patternChipSx}
      />
    </Tooltip>
  ) : null);

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
    const isWifi = isWifiEvent(event);
    const isZigbee = isZigbeeEvent(event);
    const isAdsb = isAdsbEvent(event);
    const accent = isFm ? '#ffb347' : (isWifi ? '#6ecbff' : (isZigbee ? '#b084ff' : (isAdsb ? '#ff6b6b' : (isBtc ? '#ffd166' : '#64f0d2'))));
    const EventIcon = isFm ? RadioIcon : (isWifi ? WifiIcon : (isZigbee ? SensorsIcon : (isAdsb ? FlightTakeoffIcon : BluetoothIcon)));
    const uaps = Array.isArray(event?.uap_options) ? event.uap_options : [];
    const footprintLabel = eventFootprintLabel(event);
    const pattern = patternForEvent(event);
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
          {renderPatternChip(pattern)}
          {pattern?.recent_days > 1 ? (
            <Tooltip title={patternChipTitle(pattern)}>
              <Chip size="small" label={`seen ${pattern.recent_days}/${PATTERN_OF_LIFE_WINDOW_DAYS}d`} />
            </Tooltip>
          ) : null}
          {footprintLabel ? <Chip size="small" label={footprintLabel} /> : null}
          {isFm && event?.frequency_mhz !== undefined ? <Chip size="small" label={`${Number(event.frequency_mhz).toFixed(1)} MHz`} /> : null}
          {isFm ? <Chip size="small" color={event?.decode_status === 'station' ? 'success' : 'warning'} label={event?.decode_status === 'station' ? 'station' : 'potential'} /> : null}
          {isWifi && hasValue(event?.likely_center_freq_hz) ? <Chip size="small" label={`${(Number(event.likely_center_freq_hz) / 1e6).toFixed(1)} MHz`} /> : null}
          {isWifi && event?.kind === 'wifi_frame' ? <Chip size="small" color="success" label="MAC" /> : null}
          {isWifi && event?.ssid ? <Chip size="small" label={`SSID ${event.ssid}`} /> : null}
          {isWifi && event?.subtype ? <Chip size="small" label={String(event.subtype)} /> : null}
          {isWifi && event?.source_mac ? <Chip size="small" label={`SA ${event.source_mac}`} /> : null}
          {isWifi && event?.destination ? <Chip size="small" label={`DA ${event.destination}`} /> : null}
          {isWifi && event?.bssid ? <Chip size="small" label={`BSSID ${event.bssid}`} /> : null}
          {isWifi && Array.isArray(event?.channels) && event.channels.length > 1 ? <Chip size="small" label={`CH ${event.channels.join(', ')}`} /> : null}
          {isWifi && event?.score !== undefined ? <Chip size="small" label={`score ${Number(event.score).toFixed(2)}`} /> : null}
          {isAdsb && event?.icao ? <Chip size="small" label={`ICAO ${event.icao}`} /> : null}
          {isAdsb && event?.flight ? <Chip size="small" label={`Flight ${String(event.flight).trim()}`} /> : null}
          {isAdsb && Number(event?.altitude_ft) ? <Chip size="small" label={`${Number(event.altitude_ft).toLocaleString()} ft`} /> : null}
          {isAdsb && Number(event?.speed_kt) ? <Chip size="small" label={`${Number(event.speed_kt)} kt`} /> : null}
          {isAdsb && Number(event?.track_deg) ? <Chip size="small" label={`TRK ${Number(event.track_deg)}°`} /> : null}
          {isAdsb && event?.squawk ? <Chip size="small" color={[7500, 7600, 7700].includes(Number(event.squawk)) ? 'error' : 'default'} label={`Squawk ${event.squawk}`} /> : null}
          {isZigbee && event?.fcs_ok !== undefined ? <Chip size="small" color={event.fcs_ok ? 'success' : 'warning'} label={event.fcs_ok ? 'FCS OK' : 'FCS bad'} /> : null}
          {isZigbee && event?.mac?.frame_type ? <Chip size="small" label={String(event.mac.frame_type)} /> : null}
          {isZigbee && event?.mac?.source_pan_id !== undefined && event?.mac?.source_pan_id !== null ? <Chip size="small" label={`PAN ${event.mac.source_pan_id}`} /> : null}
          {isZigbee && event?.decoded_text ? <Chip size="small" label={`Text ${event.decoded_text}`} /> : null}
          {event?.kind === 'ble_adv' ? <Chip size="small" label="ADV" /> : null}
          {isBtc && normalizeUap(event?.uap || event?.uap_hex) ? <Chip size="small" label={`UAP ${normalizeUap(event?.uap || event?.uap_hex)}`} /> : null}
          {isBtc && normalizedLap(event) ? <Chip size="small" label={`LAP ${normalizedLap(event)}`} /> : null}
          {hasValue(event?.channel) ? <Chip size="small" label={`CH ${event.channel}`} /> : null}
          {isBtc && Number(event?.candidate_count || 0) > 1 ? <Chip size="small" label={`${Number(event.candidate_count)} UAP candidates`} /> : null}
          {(isBtc || isWifi) && Number(event?.detections || 0) > 1 ? <Chip size="small" label={`${Number(event.detections)} sightings`} /> : null}
          {isBtc && uaps.length > 1 ? <Chip size="small" label={`UAPs ${uaps.slice(0, 4).join(' ')}`} /> : null}
          {event?.rssi_dbm !== undefined ? <Chip size="small" label={`RSSI ${Number(event.rssi_dbm).toFixed(0)} dBm`} /> : null}
          {event?.rssi_dbm === undefined && event?.rssi_dbfs !== undefined ? <Chip size="small" label={`RSSI ${Number(event.rssi_dbfs).toFixed(1)} dBFS`} /> : null}
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
        {isZigbee && (event?.mac?.source_address || event?.mac?.destination_address || event?.psdu_hex) ? (
          <Typography variant="caption" sx={{ mt: 0.75, display: 'block', fontFamily: 'monospace' }}>
            {[
              event?.decoded_text ? `text "${event.decoded_text}"` : '',
              event?.mac?.source_address ? `src ${event.mac.source_address}` : '',
              event?.mac?.destination_address ? `dst ${event.mac.destination_address}` : '',
              event?.psdu_hex ? `psdu ${String(event.psdu_hex).slice(0, 48)}${String(event.psdu_hex).length > 48 ? '...' : ''}` : '',
            ].filter(Boolean).join('  ')}
          </Typography>
        ) : null}
        {isWifi && event?.kind === 'wifi_frame' ? (
          <Typography variant="caption" sx={{ mt: 0.75, display: 'block', fontFamily: 'monospace' }}>
            {[
              event?.source_mac ? `SA ${event.source_mac}` : '',
              event?.destination ? `DA ${event.destination}` : '',
              event?.ssid ? `SSID ${event.ssid}` : '',
              Array.isArray(event?.source_macs) && event.source_macs.length > 1 ? `SA ${event.source_macs.join(', ')}` : '',
              hasValue(event?.channel) ? `CH ${event.channel}` : '',
              event?.rssi_dbm !== undefined ? `RSSI ${Number(event.rssi_dbm).toFixed(0)} dBm` : '',
              event?.rssi_dbm === undefined && event?.rssi_dbfs !== undefined ? `RSSI ${Number(event.rssi_dbfs).toFixed(1)} dBFS` : '',
              event?.transmitter && event.transmitter !== event.source_mac ? `TA ${event.transmitter}` : '',
              event?.receiver && event.receiver !== event.destination ? `RA ${event.receiver}` : '',
            ].filter(Boolean).join('  ')}
          </Typography>
        ) : null}
        {isAdsb && (event?.icao || event?.lat || event?.lon || event?.raw) ? (
          <Typography variant="caption" sx={{ mt: 0.75, display: 'block', fontFamily: 'monospace' }}>
            {[
              event?.icao ? `ICAO ${event.icao}` : '',
              event?.lat && event?.lon ? `pos ${Number(event.lat).toFixed(5)}, ${Number(event.lon).toFixed(5)}` : '',
              event?.raw ? String(event.raw) : '',
            ].filter(Boolean).join('  ')}
          </Typography>
        ) : null}
      </Paper>
    );
  };

  const renderGroupedEvents = () => protocolGroups.map(({ protocol, rows, stats }, groupIndex) => {
    const accent = protocol === 'FM' ? '#ffb347' : (protocol === 'WIFI' ? '#6ecbff' : (protocol === 'ZIGBEE' ? '#b084ff' : (protocol === 'ADSB' ? '#ff6b6b' : (protocol === 'BTC' ? '#ffd166' : '#64f0d2'))));
    const GroupIcon = protocol === 'FM' ? RadioIcon : (protocol === 'WIFI' ? WifiIcon : (protocol === 'ZIGBEE' ? SensorsIcon : (protocol === 'ADSB' ? FlightTakeoffIcon : BluetoothIcon)));
    const defaultOpen = !foldProtocolGroups || groupIndex < 1;
    const groupPattern = bestPatternForRows(rows);
    const body = protocol === 'BTLE'
      ? Array.from(groupRowsBy(rows, manufacturerGroupLabel).entries())
        .sort(([a], [b]) => String(a).localeCompare(String(b)))
        .map(([label, manufacturerRows], idx) => {
          const subgroupStats = summaryStats(manufacturerRows);
          const subgroupPattern = bestPatternForRows(manufacturerRows);
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
                  {renderPatternChip(subgroupPattern)}
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
            <GroupIcon fontSize="small" sx={{ color: accent }} />
            <Typography variant="subtitle1" sx={{ fontWeight: 900, overflow: 'hidden', textOverflow: 'ellipsis' }}>
              {protocolGroupLabel(protocol)}
            </Typography>
          </Stack>
          <Stack direction="row" spacing={0.5} sx={{ flexWrap: 'wrap', justifyContent: 'flex-end' }}>
            <Chip size="small" sx={{ bgcolor: `${accent}22`, color: '#fff' }} label={protocol} />
            <Chip size="small" label={`${rows.length} card${rows.length === 1 ? '' : 's'}`} />
            <Chip size="small" label={`${stats.detections} detections`} />
            {renderPatternChip(groupPattern)}
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
        <Stack direction="row" spacing={1} alignItems="center" justifyContent="space-between">
          <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
            <RadioIcon fontSize="small" sx={{ color: '#64f0d2' }} />
            <Typography variant="h6" sx={{ lineHeight: 1.15 }}>Signal Activity</Typography>
          </Stack>
          <Button
            size="small"
            variant="outlined"
            disabled={historyEvents.length === 0}
            onClick={clearActivity}
            sx={{ minWidth: 0, px: 1.25, py: 0.25 }}
          >
            Clear
          </Button>
        </Stack>
        <Stack direction="row" spacing={0.75} sx={{ mt: 1, flexWrap: 'wrap', gap: 0.75 }}>
          {scannerMode?.active ? (
            <Chip
              size="small"
              color="primary"
              label={`Scanning ${scannerLabel}${scannerFrequencyLabel}`}
            />
          ) : null}
          <Chip size="small" color={decoderActive ? 'success' : 'default'} label={decoderActive ? 'decoder on' : 'decoder idle'} />
          <Chip size="small" label={`${bleAdvCount} BLE adv`} />
          <Chip size="small" label={`${btcCount} BTC`} />
          <Chip size="small" label={`${wifiCount} WiFi`} />
          <Chip size="small" label={`${wifiFrameCount} WiFi frames`} />
          <Chip size="small" label={`${zigbeeCount} Zigbee`} />
          <Chip size="small" label={`${adsbCount} ADS-B`} />
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
