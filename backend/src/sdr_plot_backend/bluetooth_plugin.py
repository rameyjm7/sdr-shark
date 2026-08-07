from __future__ import annotations

import json
import os
import sqlite3
import time
from typing import Any

# BTC/BLE decoding used to be its own engine here: this class span up its
# own btcexplorer-sniffer-gateway subprocess and a separate Python BLE
# detector, triggered whenever SDR-Shark's own tuning happened to overlap
# the 2.4 GHz ISM band - meaning detection only existed while a user
# happened to be looking at the right frequency in this app specifically,
# duplicating rf-sentinel's own (better-tested) engine.
#
# station1 now runs a single always-on shared detector (bt_detector_service
# in rf-iq-gateway, using rf-sentinel's bluetooth_scanner/bluetooth-classic
# engine against a dedicated radio) that writes to a shared SQLite database
# both apps read from - detection isn't tied to which app is open or what
# it's tuned to, and switching between sdr-shark and rf-sentinel shows the
# same accumulated detections instead of each app starting from zero.
DB_PATH = os.getenv("BT_DETECTOR_DB", "/tmp/bt-detections.sqlite3")

# An event from the shared detector within this window counts as "the
# detector is alive" for the `active` flag - it emits periodic iq/status
# housekeeping events even with nothing detected, so this isn't just "has
# any BT device been seen recently."
ACTIVE_WINDOW_S = 30.0

_SKIP_KINDS = {"metrics", "config", "debug_bin_energy"}


class BluetoothGatewayPlugin:
    def __init__(self) -> None:
        self.enabled = str(os.getenv("SDR_SHARK_BLUETOOTH_PLUGIN", "1")).strip().lower() not in {"0", "false", "no"}
        self._last_error = ""

    def update(self, sdr: Any) -> None:
        # Detection is independent of this app's own tuning now - nothing
        # to start/stop here. Kept as a no-op so callers elsewhere in this
        # file don't need to change.
        return

    def stop(self) -> None:
        return

    def snapshot(self, max_events: int = 50) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False, "active": False, "event_count": 0, "events": [], "last_error": ""}
        events: list[dict[str, Any]] = []
        active = False
        try:
            conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2.0)
            try:
                cur = conn.execute(
                    "SELECT ts, kind, raw_json FROM events ORDER BY id DESC LIMIT ?",
                    (max(1, int(max_events)) * 2,),  # over-fetch since some rows get filtered below
                )
                rows = cur.fetchall()
            finally:
                conn.close()
            if rows and (time.time() - float(rows[0][0])) <= ACTIVE_WINDOW_S:
                active = True
            for ts, kind, raw_json in rows:
                if str(kind or "") in _SKIP_KINDS:
                    continue
                try:
                    event = json.loads(raw_json)
                except (json.JSONDecodeError, TypeError):
                    continue
                event["seen_at"] = ts
                events.append(event)
                if len(events) >= max_events:
                    break
            events.reverse()  # oldest-first, matching the previous engine's ordering
            self._last_error = ""
        except sqlite3.OperationalError:
            # DB not created yet (detector service not running/hasn't written
            # its first event) - not a real error, just nothing to show yet.
            pass
        except Exception as exc:
            self._last_error = str(exc)
        return {
            "enabled": True,
            "active": active,
            "event_count": len(events),
            "events": events,
            "last_error": self._last_error,
        }
