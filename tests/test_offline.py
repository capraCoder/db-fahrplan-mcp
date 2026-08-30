"""Offline tests against recorded bahn.de responses (tests/fixtures, recorded 2026-08-30
during a real disruption at Montabaur — so cancellations, delays and platform changes are
genuine data, not synthetic)."""

from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

import db_fahrplan_mcp.server as s
from db_fahrplan_mcp._compat import ToolError

# ------------------------------------------------------------------ pure helpers


def test_parse_when_accepts_both_separators() -> None:
    a = s.parse_when("2026-09-01 08:05")
    b = s.parse_when("2026-09-01T08:05")
    assert a == b and a.tzinfo is s.TZ and a.hour == 8


@pytest.mark.parametrize("bad", ["tomorrow 9am", "2026-13-01 08:00", "08:00", "2026-09-01 25:00"])
def test_parse_when_rejects_garbage(bad: str) -> None:
    with pytest.raises(ToolError):
        s.parse_when(bad)


def test_parse_when_none_is_berlin_now() -> None:
    t = s.parse_when(None)
    assert t.tzinfo is s.TZ and t.second == 0


def test_times_reads_echtzeit_not_zeit() -> None:
    node = {"sollzeit": "2026-08-30T15:32:00", "echtzeit": "2026-08-30T15:45:00"}
    assert s._stamp(node) == "15:45 (+13)"
    assert s._stamp({"sollzeit": "2026-08-30T15:32:00"}) == "15:32"


def test_stamp_marks_next_day() -> None:
    node = {"sollzeit": "2026-08-31T00:12:00"}
    assert s._stamp(node, ref_date="2026-08-30") == "00:12 (+1d)"


def test_stamp_dst_transition_delay_is_wall_clock() -> None:
    # 2026-10-25: clocks go back 03:00 -> 02:00 in Berlin. A 10-minute delay stays 10 minutes.
    node = {"sollzeit": "2026-10-25T02:55:00", "echtzeit": "2026-10-25T03:05:00"}
    assert "(+10)" in s._stamp(node)


@pytest.mark.parametrize(
    "q,name,ok",
    [
        ("Koeln Hbf", "Köln Hbf", True),
        ("Köln", "Köln Hbf", True),
        ("Frankfurt Main", "Frankfurt(Main)Hbf", True),
        ("Xyzzyqq Nowhere", "Herby Nowe", False),
    ],
)
def test_word_overlap_guard(q: str, name: str, ok: bool) -> None:
    assert bool(s._words(q) & s._words(name)) is ok


def test_travellers_bahncard_and_children() -> None:
    t = s._travellers("50", False, 2, [4, 9, 16])
    assert t[0]["anzahl"] == 2 and t[0]["ermaessigungen"][0]["art"] == "BAHNCARD50"
    assert [x["typ"] for x in t[1:]] == ["KLEINKIND", "FAMILIENKIND", "JUGENDLICHER"]
    with pytest.raises(ToolError):
        s._travellers("75", False, 1, None)


def test_clean_drops_empty() -> None:
    assert s._clean({"a": None, "b": [], "c": {"d": "", "e": 1}, "f": [None]}) == {"c": {"e": 1}, "f": [None]}


def test_resolve_accepts_eva_and_full_id() -> None:
    assert s.resolve("8000207") == {"id": "A=1@L=8000207@", "name": "8000207", "eva": "8000207"}
    r = s.resolve("A=1@O=Köln Hbf@X=6958730@Y=50943029@U=80@L=8000207@p=1@")
    assert r["eva"] == "8000207" and r["name"] == "Köln Hbf"


# ------------------------------------------------------------------ tools on fixtures


def test_journeys_parse_disruption_day(offline) -> None:
    out = json.loads(s.db_journeys("Köln Hbf", "Frankfurt(Main)Hbf", "2026-08-30 15:44"))
    assert out["timezone"] == "Europe/Berlin"
    j = out["journeys"]
    assert j, "fixture has journeys"
    legs = [leg for x in j for leg in x["legs"] if "line" in leg]
    assert any(leg.get("origin_cancelled") for leg in legs), "recorded cancellations survive parsing"
    assert any(re.search(r"\([+-]\d+\)", leg["dep"] + leg["arr"]) for leg in legs), "echtzeit delays survive"
    assert all(leg.get("journey_id") for leg in legs)
    assert all(x.get("recon_token") for x in j)
    assert any(x.get("load") for x in j)
    assert any(leg.get("amenities") for leg in legs)
    body = offline["angebote/fahrplan"][0]["json"]
    assert body["ankunftSuche"] == "ABFAHRT" and body["anfrageZeitpunkt"] == "2026-08-30T15:44:00"


def test_journeys_text_format_is_compact(offline) -> None:
    txt = s.db_journeys("Köln Hbf", "Frankfurt(Main)Hbf", "2026-08-30 15:44", format="text", max_results=2)
    assert "→" in txt and "CANCELLED" in txt and "Gl." in txt
    assert len(txt) < 2500


def test_journeys_request_body_options(offline) -> None:
    s.db_journeys("Köln Hbf", "Berlin Hbf", "2026-09-01 08:00", arrive_by=True, via="Hannover Hbf",
                  bahncard="25", children_ages=[8], bike=True, seat_only=True, max_transfers=2,
                  min_transfer_min=15, first_class=True, deutschlandticket=False)
    body = offline["angebote/fahrplan"][0]["json"]
    assert body["ankunftSuche"] == "ANKUNFT"
    assert body["klasse"] == "KLASSE_1"
    assert body["zwischenhalte"] and body["maxUmstiege"] == 2 and body["minUmstiegszeit"] == 15
    assert body["bikeCarriage"] is True and body["sitzplatzOnly"] is True
    assert body["reisende"][0]["ermaessigungen"][0]["art"] == "BAHNCARD25"
    assert body["reisende"][1]["typ"] == "FAMILIENKIND"


def test_deutschlandticket_forces_regional(offline) -> None:
    s.db_journeys("Köln Hbf", "Berlin Hbf", "2026-09-01 08:00", deutschlandticket=True)
    body = offline["angebote/fahrplan"][0]["json"]
    assert "ICE" not in body["produktgattungen"] and body["nurDeutschlandTicketVerbindungen"] is True


def test_bogus_station_rejected_with_candidates(offline, monkeypatch) -> None:
    hit = ({"id": "x", "name": "Herby Nowe", "eva": "1"},)
    monkeypatch.setattr(s, "_locations", lambda q, kind="ST": hit)
    with pytest.raises(ToolError, match="Herby Nowe"):
        s.db_journeys("Xyzzyqq Nowhere", "Köln Hbf")


def test_offers_sorted_cheapest_first(offline) -> None:
    out = json.loads(s.db_journey_offers("token", bahncard="25"))
    prices = [o["price_eur"] for o in out["offers"] if o.get("price_eur") is not None]
    assert prices == sorted(prices) and len(prices) >= 3
    assert {"Super Sparpreis", "Sparpreis", "Flexpreis"} <= {o["name"] for o in out["offers"]}


def test_best_price_bands(offline) -> None:
    out = json.loads(s.db_best_price("Köln Hbf", "Berlin Hbf", "2026-09-01"))
    assert len(out["bands"]) == 6
    assert sum(1 for b in out["bands"] if b.get("cheapest_of_day")) == 1
    assert offline["angebote/tagesbestpreis"][0]["json"]["anfrageZeitpunkt"] == "2026-09-01T00:00:00"


def test_departures_board(offline) -> None:
    out = json.loads(s.db_departures("Köln Hbf", "2026-08-30 15:44", minutes=120))
    rows = out["abfahrten"]
    assert rows and all(r.get("journey_id") for r in rows)
    assert any("(+" in r["time"] for r in rows), "delays from ezZeit"
    assert any(r.get("platform_changed") for r in rows), "platform changes from ezGleis"
    assert any(r.get("cancelled") for r in rows), "cancellation from typed meldungen"
    params = offline["reiseloesung/abfahrten"][0]["params"]
    assert params["ortExtId"] == "8000207" and "BUS" not in params["verkehrsmittel[]"]


def test_departures_text_format(offline) -> None:
    txt = s.db_departures("Köln Hbf", "2026-08-30 15:44", minutes=30, format="text")
    assert txt.startswith("Departures Köln Hbf") and "CANCELLED" in txt or "Gl." in txt


def test_trip_details(offline) -> None:
    out = json.loads(s.db_trip_details("any"))
    assert out["train"] and len(out["stops"]) >= 5
    assert out["stops"][0].get("dep") and out["stops"][-1].get("arr")
    assert all(st.get("platform") for st in out["stops"][:-1])


def test_nearby_filters_rail(offline) -> None:
    out = json.loads(s.db_nearby(50.9432, 6.9589))
    assert out and all(set(o["products"]) & set(s.RAIL_PRODUCTS) for o in out)
    assert out[0]["name"] == "Köln Hbf"


def test_train_formation_uses_utc(offline) -> None:
    out = json.loads(s.db_train_formation("ICE 109", "Köln Hbf", "2026-08-30 14:54"))
    params = offline["reisebegleitung/wagenreihung/vehicle-sequence"][0]["params"]
    assert params["time"].endswith("Z") and params["time"].startswith("2026-08-30T12:54")
    assert params["category"] == "ICE" and params["number"] == "109"
    coaches = out["groups"][0]["coaches"]
    assert coaches and all(c.get("sector") for c in coaches)
    with pytest.raises(ToolError):
        s.db_train_formation("RE 7", "Köln Hbf", "2026-08-30 14:54")


# ------------------------------------------------------------------ through the MCP layer


def _is_error(res) -> bool:  # mcp 1.x: isError, mcp 2.x: is_error
    return bool(getattr(res, "isError", getattr(res, "is_error", False)))


@pytest.mark.anyio
async def test_mcp_layer_tools_annotations_and_errors(offline, mcp_session) -> None:
    async with mcp_session(s.mcp) as client:
        tools = (await client.list_tools()).tools
        names = {t.name for t in tools}
        assert {"db_journeys", "db_departures", "db_arrivals", "db_search_station", "db_journey_offers",
                "db_best_price", "db_trip_details", "db_nearby", "db_train_formation"} <= names
        for t in tools:
            a = t.annotations
            assert a and (getattr(a, "readOnlyHint", None) or getattr(a, "read_only_hint", None)) is True, t.name
            assert t.title, t.name
        res = await client.call_tool("db_journeys", {"from_station": "Köln Hbf", "to_station": "Berlin Hbf",
                                                     "when": "tomorrow 9am"})
        assert _is_error(res) is True and "YYYY-MM-DD" in res.content[0].text
        res = await client.call_tool("db_departures", {"station": "Köln Hbf", "when": "2026-08-30 15:44",
                                                       "format": "text"})
        assert _is_error(res) is False and "Departures Köln Hbf" in res.content[0].text
        prompts = (await client.list_prompts()).prompts
        assert any(p.name == "plan_trip" for p in prompts)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_version_is_semver() -> None:
    from db_fahrplan_mcp import __version__

    major, minor, patch = __version__.split(".")
    assert all(x.isdigit() for x in (major, minor, patch))
    assert datetime.now().year >= 2026
