from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import numpy as np

from sdr_plot_backend.iq_session import IQReplaySDR


def _load_demo_generator():
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "generate_demo_iq_session.py"
    spec = importlib.util.spec_from_file_location("generate_demo_iq_session", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_generate_demo_iq_session_creates_replay_fixture(tmp_path):
    generator = _load_demo_generator()
    session_root = tmp_path / "iq-sessions"
    args = generator.build_parser().parse_args([
        "--root", str(session_root),
        "--session-id", "public-demo-test",
        "--duration", "0.25",
        "--sample-rate", "200000",
        "--chunk-samples", "1024",
    ])

    metadata = generator.generate_session(args)
    session_dir = session_root / "public-demo-test"
    capture_path = session_dir / "chunks.cs8"
    index_path = session_dir / "chunks.jsonl"
    metadata_path = session_dir / "metadata.json"

    assert metadata["id"] == "public-demo-test"
    assert metadata["status"] == "complete"
    assert metadata["format"] == "cs8"
    assert metadata["stream"]["center_freq_hz"] == generator.DEFAULT_CENTER_HZ
    assert metadata["stream"]["sample_rate_sps"] == 200000
    assert metadata["replay_preview_scale"] == generator.DEFAULT_REPLAY_PREVIEW_SCALE
    assert metadata["chunks"] > 0
    assert metadata["bytes"] > 0
    assert capture_path.stat().st_size == metadata["bytes"]
    assert index_path.stat().st_size > 0
    assert json.loads(metadata_path.read_text(encoding="utf-8"))["generator"]["public_safe"] is True


def test_iq_replay_sdr_reads_generated_demo_samples(tmp_path):
    generator = _load_demo_generator()
    session_root = tmp_path / "iq-sessions"
    args = generator.build_parser().parse_args([
        "--root", str(session_root),
        "--session-id", "public-demo-test",
        "--duration", "0.25",
        "--sample-rate", "200000",
        "--chunk-samples", "1024",
    ])
    generator.generate_session(args)

    replay = IQReplaySDR(session_root / "public-demo-test", loop=False, speed=20, size=1024)
    chunks = list(replay._iter_chunks())
    assert chunks

    replay.start()
    try:
        for _ in range(50):
            samples = replay.get_latest_samples()
            if np.any(np.abs(samples) > 0):
                break
        else:
            raise AssertionError("generated replay did not produce non-zero samples")
    finally:
        replay.stop()

    info = replay.iq_tap_info()
    assert info["backend"] == "replay"
    assert info["center_freq_hz"] == generator.DEFAULT_CENTER_HZ
    assert info["sample_rate_sps"] == 200000
    assert replay.current_device_label() == "Public synthetic 2.4 GHz SDR-Shark demo"
    assert replay.mimo_info()["enabled"] is False
