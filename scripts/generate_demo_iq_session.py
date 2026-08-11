#!/usr/bin/env python3
"""Generate a deterministic public-safe SDR-Shark IQ replay session."""

from __future__ import annotations

import argparse
import json
import math
import shutil
from pathlib import Path

import numpy as np


DEFAULT_SESSION_ID = "public-demo-2p4ghz"
DEFAULT_CENTER_HZ = 2_437_000_000
DEFAULT_SAMPLE_RATE = 2_000_000
DEFAULT_BANDWIDTH = 2_000_000
DEFAULT_DURATION_SECONDS = 30.0
DEFAULT_CHUNK_SAMPLES = 8192
DEFAULT_GAIN_DB = 18.0
DEFAULT_REPLAY_PREVIEW_SCALE = 0.0005


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".demo/iq-sessions", help="IQ session root directory")
    parser.add_argument("--session-id", default=DEFAULT_SESSION_ID)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION_SECONDS)
    parser.add_argument("--sample-rate", type=float, default=DEFAULT_SAMPLE_RATE)
    parser.add_argument("--center-frequency", type=float, default=DEFAULT_CENTER_HZ)
    parser.add_argument("--bandwidth", type=float, default=DEFAULT_BANDWIDTH)
    parser.add_argument("--chunk-samples", type=int, default=DEFAULT_CHUNK_SAMPLES)
    parser.add_argument("--force", action="store_true", help="Regenerate an existing session")
    return parser


def _burst_envelope(t: np.ndarray, centers: tuple[float, ...], width: float) -> np.ndarray:
    env = np.zeros_like(t, dtype=np.float32)
    for center in centers:
        env += np.exp(-0.5 * ((t - center) / width) ** 2).astype(np.float32)
    return np.clip(env, 0.0, 1.0)


def _frequency_hopper(t: np.ndarray, duration: float) -> np.ndarray:
    """Stationary receiver view of a public-safe frequency-hopping emitter."""
    hop_offsets = np.array(
        [-720_000.0, -360_000.0, 180_000.0, 610_000.0, -120_000.0, 430_000.0, -540_000.0, 760_000.0],
        dtype=np.float64,
    )
    hop_period = 0.18
    hop_active_start = min(7.0, max(0.0, duration * 0.24))
    hop_active_stop = max(hop_active_start + 1.0, duration - 2.0)
    relative = np.maximum(0.0, t - hop_active_start)
    hop_index = np.floor(relative / hop_period).astype(np.int64) % hop_offsets.size
    local = np.mod(relative, hop_period)
    duty = hop_period * 0.62
    edge = hop_period * 0.08
    on = (t >= hop_active_start) & (t <= hop_active_stop) & (local <= duty)
    rise = np.clip(local / max(edge, 1e-6), 0.0, 1.0)
    fall = np.clip((duty - local) / max(edge, 1e-6), 0.0, 1.0)
    envelope = np.where(on, np.minimum(rise, fall), 0.0).astype(np.float32)
    symbol_wobble = 0.14 * np.sin(2.0 * np.pi * t * 37.0)
    phase = hop_offsets[hop_index] * t + symbol_wobble
    return (0.30 * envelope * np.exp(2j * np.pi * phase)).astype(np.complex64)


def _quantize_cs8(iq: np.ndarray) -> bytes:
    real = np.clip(np.real(iq), -0.98, 0.98)
    imag = np.clip(np.imag(iq), -0.98, 0.98)
    interleaved = np.empty(real.size * 2, dtype=np.int8)
    interleaved[0::2] = np.clip(real * 118.0, -127, 127).astype(np.int8)
    interleaved[1::2] = np.clip(imag * 118.0, -127, 127).astype(np.int8)
    return interleaved.tobytes()


def _chunk_iq(
    *,
    start_sample: int,
    chunk_samples: int,
    sample_rate: float,
    duration: float,
    rng: np.random.Generator,
) -> np.ndarray:
    n = np.arange(start_sample, start_sample + chunk_samples, dtype=np.float64)
    t = n / float(sample_rate)
    progress = np.mod(t / max(duration, 1.0), 1.0)

    noise_level = 0.018 + 0.006 * (1.0 + np.sin(2.0 * np.pi * progress * 3.0))
    noise = (
        rng.normal(0.0, noise_level, chunk_samples)
        + 1j * rng.normal(0.0, noise_level, chunk_samples)
    ).astype(np.complex64)

    carriers = (
        (0.095, -640_000.0, 0.09),
        (0.072, -115_000.0, 0.27),
        (0.060, 415_000.0, 0.53),
    )
    iq = noise
    for amp, offset_hz, phase in carriers:
        slow_fade = 0.68 + 0.32 * np.sin(2.0 * np.pi * progress * (1.2 + phase))
        tone = amp * slow_fade * np.exp(2j * np.pi * ((offset_hz * t) + phase))
        iq = iq + tone.astype(np.complex64)

    burst_centers = (3.0, 4.2, 8.5, 12.0, 16.8, 20.4, 25.5, 27.0)
    env = _burst_envelope(np.mod(t, duration), burst_centers, width=0.08)
    chirp_offset = 690_000.0 + 80_000.0 * np.sin(2.0 * np.pi * t * 1.7)
    burst = 0.24 * env * np.exp(2j * np.pi * (chirp_offset * t + 0.18 * np.sin(2.0 * np.pi * t * 9.0)))
    iq = iq + burst.astype(np.complex64)

    lower_burst = _burst_envelope(np.mod(t + 0.35, duration), (6.3, 14.4, 22.2, 28.6), width=0.12)
    iq = iq + (0.17 * lower_burst * np.exp(2j * np.pi * (-360_000.0 * t + 0.11))).astype(np.complex64)
    iq = iq + _frequency_hopper(t, duration)
    return iq.astype(np.complex64, copy=False)


def _existing_session_matches(metadata: dict[str, object], args: argparse.Namespace) -> bool:
    stream = dict(metadata.get("stream") or {})
    return (
        metadata.get("id") == args.session_id
        and metadata.get("status") == "complete"
        and metadata.get("format") == "cs8"
        and float(metadata.get("duration_seconds") or 0.0) == round(max(0.1, float(args.duration)), 6)
        and int(stream.get("center_freq_hz") or 0) == int(round(float(args.center_frequency)))
        and int(stream.get("sample_rate_sps") or 0) == int(round(float(args.sample_rate)))
        and int(stream.get("bandwidth_hz") or 0) == int(round(float(args.bandwidth)))
        and float(metadata.get("replay_preview_scale") or 0.0) == DEFAULT_REPLAY_PREVIEW_SCALE
        and int(dict(metadata.get("generator") or {}).get("version") or 0) == 2
    )


def generate_session(args: argparse.Namespace) -> dict[str, object]:
    root = Path(args.root).expanduser()
    session_dir = root / args.session_id
    metadata_path = session_dir / "metadata.json"
    capture_path = session_dir / "chunks.cs8"
    index_path = session_dir / "chunks.jsonl"

    if session_dir.exists() and not args.force:
        if metadata_path.exists() and capture_path.exists() and index_path.exists():
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            if _existing_session_matches(metadata, args):
                return metadata
            shutil.rmtree(session_dir)
        else:
            raise SystemExit(f"Existing incomplete session at {session_dir}; rerun with --force")

    if session_dir.exists():
        shutil.rmtree(session_dir)
    session_dir.mkdir(parents=True, exist_ok=True)

    sample_rate = float(args.sample_rate)
    duration = max(0.1, float(args.duration))
    chunk_samples = max(256, int(args.chunk_samples))
    total_samples = int(round(duration * sample_rate))
    chunk_count = int(math.ceil(total_samples / chunk_samples))
    rng = np.random.default_rng(20260809)

    byte_count = 0
    with capture_path.open("wb") as capture, index_path.open("w", encoding="utf-8") as index:
        for chunk_idx in range(chunk_count):
            start_sample = chunk_idx * chunk_samples
            remaining = max(0, total_samples - start_sample)
            this_chunk_samples = min(chunk_samples, remaining)
            if this_chunk_samples <= 0:
                break
            iq = _chunk_iq(
                start_sample=start_sample,
                chunk_samples=this_chunk_samples,
                sample_rate=sample_rate,
                duration=duration,
                rng=rng,
            )
            raw = _quantize_cs8(iq)
            offset = byte_count
            capture.write(raw)
            byte_count += len(raw)
            index.write(json.dumps({
                "chunk": chunk_idx + 1,
                "offset": offset,
                "bytes": len(raw),
                "timestamp_offset_seconds": round(start_sample / sample_rate, 6),
            }, separators=(",", ":")) + "\n")

    metadata = {
        "id": args.session_id,
        "label": "Public synthetic 2.4 GHz SDR-Shark demo",
        "created_at": "2026-08-09T00:00:00.000Z",
        "format": "cs8",
        "sample_bytes": 2,
        "capture_file": capture_path.name,
        "index_file": index_path.name,
        "stream": {
            "backend": "synthetic",
            "source": "public_demo",
            "device_id": f"demo:{args.session_id}",
            "stream_id": args.session_id,
            "iq_format": "i8",
            "center_freq_hz": int(round(float(args.center_frequency))),
            "sample_rate_sps": int(round(sample_rate)),
            "bandwidth_hz": int(round(float(args.bandwidth))),
            "gain_db": DEFAULT_GAIN_DB,
        },
        "max_seconds": 0.0,
        "max_mb": 0.0,
        "status": "complete",
        "chunks": chunk_count,
        "bytes": byte_count,
        "duration_seconds": round(duration, 6),
        "replay_preview_scale": DEFAULT_REPLAY_PREVIEW_SCALE,
        "generator": {
            "name": "scripts/generate_demo_iq_session.py",
            "version": 2,
            "public_safe": True,
            "description": "Deterministic synthetic tones, noise-floor motion, burst clusters, and a stationary-view Bluetooth-like frequency-hopping emitter; no recorded RF environment data.",
        },
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def main() -> int:
    args = build_parser().parse_args()
    metadata = generate_session(args)
    print(json.dumps({
        "id": metadata["id"],
        "path": str(Path(args.root).expanduser() / str(metadata["id"])),
        "chunks": metadata["chunks"],
        "bytes": metadata["bytes"],
        "duration_seconds": metadata["duration_seconds"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
