from __future__ import annotations

from flask import Flask

from sdr_plot_backend import api


def test_get_sdr_devices_includes_antsdre200():
    app = Flask(__name__)

    class _FakeSdr:
        device_id = "antsdre200:0"

        @staticmethod
        def list_devices():
            return [
                {"id": "antsdre200:0", "driver": "antsdre200"},
                {"id": "hackrf:0", "driver": "hackrf"},
            ]

    original_sdr = api.vars.sdr0
    api.vars.sdr0 = _FakeSdr()
    try:
        with app.app_context():
            response = api.get_sdr_devices()
            payload = response.get_json()
        assert any(device["driver"] == "antsdre200" for device in payload["devices"])
        assert payload["selected"] == "antsdre200:0"
    finally:
        api.vars.sdr0 = original_sdr


def test_select_sdr_accepts_antsdre200():
    app = Flask(__name__)
    captured = {}

    original_reselect = api.vars.reselect_radio
    def _fake_reselect(name: str) -> int:
        captured["name"] = name
        return 1

    api.vars.reselect_radio = _fake_reselect
    try:
        with app.test_request_context(json={"sdr_name": "antsdre200:0"}):
            response = api.select_sdr()
            payload = response.get_json()
        assert payload["status"] == "success"
        assert captured["name"] == "antsdre200:0"
    finally:
        api.vars.reselect_radio = original_reselect
