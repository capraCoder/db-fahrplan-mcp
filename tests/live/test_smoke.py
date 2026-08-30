"""Live smoke test — hits bahn.de. Run: pytest -m live tests/live/test_smoke.py"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import db_fahrplan_mcp.server as s
from db_fahrplan_mcp._compat import ToolError

pytestmark = pytest.mark.live
WHEN = (datetime.now(s.TZ) + timedelta(days=1)).strftime("%Y-%m-%d 09:00")


def test_search_station() -> None:
    hits = json.loads(s.db_search_station("Köln Hbf"))
    assert hits and hits[0]["name"] == "Köln Hbf" and hits[0]["eva"] == "8000207"


def test_journeys_basic() -> None:
    out = json.loads(s.db_journeys("Köln Hbf", "Düsseldorf Hbf", WHEN))
    assert out["journeys"]
    assert all(any("line" in leg for leg in j["legs"]) for j in out["journeys"])
    assert all(j.get("recon_token") for j in out["journeys"])


def test_boards_rail_only() -> None:
    dep = json.loads(s.db_departures("Köln Hbf", WHEN, 30))
    assert dep["abfahrten"] and not any(str(r["line"]).startswith(("Bus", "STR")) for r in dep["abfahrten"])
    arr = json.loads(s.db_arrivals("Köln Hbf", WHEN, 30))
    assert arr["ankuenfte"]


def test_bogus_station_rejected() -> None:
    with pytest.raises(ToolError):
        s.db_journeys("Xyzzyqq Nowhere", "Köln Hbf")


def test_bad_time_rejected() -> None:
    with pytest.raises(ToolError):
        s.db_journeys("Köln Hbf", "Düsseldorf Hbf", "tomorrow 9am")


def test_realtime_present_on_busy_corridor_now() -> None:
    """At least one leg or board row on Köln–Frankfurt right now should carry a delay or a
    real-time platform; if bahn.de ever renames `echtzeit`, this is the test that turns red."""
    dep = json.loads(s.db_departures("Köln Hbf", None, 90))
    rows = dep["abfahrten"]
    assert rows
    # not every minute has a delay, but a 90-minute window at Köln Hbf virtually always does
    assert any("(+" in r["time"] or r.get("platform_changed") or r.get("cancelled") for r in rows)


def test_trip_details_and_nearby() -> None:
    dep = json.loads(s.db_departures("Köln Hbf", None, 60))
    jid = dep["abfahrten"][0]["journey_id"]
    td = json.loads(s.db_trip_details(jid))
    assert td["train"] and len(td["stops"]) >= 2
    near = json.loads(s.db_nearby(50.9432, 6.9589))
    assert any(o["name"] == "Köln Hbf" for o in near)
