"""Live complex-trip test — Krefeld Hbf -> Garmisch-Partenkirchen, next Wednesday.

~7 h, always multi-modal: regional feeder, one or two ICE/IC legs, regional tail.
Exercises ICE parsing, 3+ transfers, walk legs, Deutschlandticket filter, arrive_by,
BahnCard pricing, fare breakdown via recon token, and best-price bands.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

import db_fahrplan_mcp.server as s

pytestmark = pytest.mark.live
FROM, TO = "Krefeld Hbf", "Garmisch-Partenkirchen"


def _next_wednesday() -> str:
    d = datetime.now(s.TZ) + timedelta(days=3)
    while d.weekday() != 2:
        d += timedelta(days=1)
    return d.strftime("%Y-%m-%d")


DAY = _next_wednesday()


def is_ld(leg: dict) -> bool:
    line = str(leg.get("line", ""))
    return line.startswith(("ICE", "IC", "EC")) and not line.startswith("ICB")


def rail_legs(j: dict) -> list[dict]:
    return [leg for leg in j["legs"] if "line" in leg]


@pytest.fixture(scope="module")
def unrestricted() -> list[dict]:
    return json.loads(s.db_journeys(FROM, TO, f"{DAY} 09:00", max_results=8))["journeys"]


def test_unrestricted_shape(unrestricted: list[dict]) -> None:
    u = unrestricted
    assert len(u) >= 3
    assert any(is_ld(leg) for j in u for leg in rail_legs(j)), "at least one ICE/IC leg"
    assert any(j["changes"] >= 3 for j in u), "at least one journey with >= 3 changes"
    assert any(leg.get("type") == "walk" for j in u for leg in j["legs"]), "walk leg parsed"
    assert all(isinstance(j["price_eur"], (int, float)) and j["price_eur"] > 0 for j in u)
    assert all(j["changes"] == len(rail_legs(j)) - 1 for j in u)
    rl = [leg for j in u for leg in rail_legs(j)]
    assert sum(1 for leg in rl if leg.get("dep_platform")) >= 0.9 * len(rl), "DB leaves a few unassigned"
    assert all(leg.get("journey_id") for leg in rl)


def test_leg_times_chain(unrestricted: list[dict]) -> None:
    for j in unrestricted:
        legs = rail_legs(j)
        for a, b in zip(legs, legs[1:], strict=False):
            assert b["dep"][:5] >= a["arr"][:5] or "(+1d)" in b["dep"]


def test_deutschlandticket_excludes_long_distance(unrestricted: list[dict]) -> None:
    t = json.loads(s.db_journeys(FROM, TO, f"{DAY} 09:00", deutschlandticket=True))["journeys"]
    assert t
    assert not any(is_ld(leg) for j in t for leg in rail_legs(j)), "NEGATIVE CONTROL: no ICE/IC/EC"
    assert all(j["changes"] >= 3 for j in t)
    assert min(j["duration_min"] for j in t) > min(j["duration_min"] for j in unrestricted)


def test_arrive_by_bound() -> None:
    a = json.loads(s.db_journeys(FROM, TO, f"{DAY} 18:00", arrive_by=True))["journeys"]
    assert a
    assert all(j["arr"][:5] <= "18:00" for j in a)
    assert max(j["arr"][:5] for j in a) >= "16:30", "latest arrival close to the bound"


def test_bahncard_is_cheaper(unrestricted: list[dict]) -> None:
    bc = json.loads(s.db_journeys(FROM, TO, f"{DAY} 09:00", bahncard="50", max_results=8))["journeys"]
    full = {j["dep"]: j["price_eur"] for j in unrestricted}
    pairs = [(full[j["dep"]], j["price_eur"]) for j in bc if j["dep"] in full and j["price_eur"]]
    assert pairs and all(b < f for f, b in pairs), "BahnCard 50 price must be below full price"


def test_offers_breakdown(unrestricted: list[dict]) -> None:
    j = next(x for x in unrestricted if any(is_ld(leg) for leg in rail_legs(x)))
    out = json.loads(s.db_journey_offers(j["recon_token"]))
    names = {o["name"] for o in out["offers"]}
    assert "Flexpreis" in names
    prices = [o["price_eur"] for o in out["offers"] if o.get("price_eur")]
    assert prices == sorted(prices)


def test_best_price_has_bands() -> None:
    out = json.loads(s.db_best_price(FROM, TO, DAY))
    assert out["bands"], out.get("note")
    assert sum(1 for b in out["bands"] if b.get("cheapest_of_day")) == 1
