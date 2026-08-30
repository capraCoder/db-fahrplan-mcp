#!/usr/bin/env python
"""
db_fahrplan_mcp — Deutsche Bahn timetable as an MCP server.

Talks directly to the public JSON endpoints behind bahn.de (no API key,
no third-party proxy), so it returns exactly what the bahn.de website shows:
scheduled and real-time departure/arrival times, platforms, transfers,
Flexpreis fares and disruption notices (HIM).

Tools
    db_search_station(query)                       -> stations matching a name
    db_journeys(from, to, when, arrive_by, ...)    -> connections between two stops
    db_departures(station, when, minutes)          -> departure board
    db_arrivals(station, when, minutes)            -> arrival board

Run
    python db_fahrplan_mcp.py            # stdio transport (Claude Code / Desktop)

Claude Code registration
    claude mcp add db-fahrplan -s user -- python /abs/path/db_fahrplan_mcp.py

Requires: Python >= 3.10, mcp >= 1.0, httpx.
Licence: MIT. The bahn.de endpoints are undocumented and may change without
notice — this is a personal/research tool, not a DB product.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

logging.getLogger("httpx").setLevel(logging.WARNING)

BASE = "https://www.bahn.de/web/api"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": "Mozilla/5.0 (db_fahrplan_mcp; +https://github.com/capraCoder)",
    "Accept-Language": "de",
}
ALL_PRODUCTS = ["ICE", "EC_IC", "IR", "REGIONAL", "SBAHN", "BUS", "SCHIFF",
                "UBAHN", "TRAM", "ANRUFPFLICHTIG"]
REGIONAL_ONLY = ["REGIONAL", "SBAHN", "BUS", "SCHIFF", "UBAHN", "TRAM",
                 "ANRUFPFLICHTIG"]
RAIL_ONLY = ["ICE", "EC_IC", "IR", "REGIONAL", "SBAHN"]

mcp = FastMCP("db-fahrplan")


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def _client() -> httpx.Client:
    return httpx.Client(headers=HEADERS, timeout=30.0, follow_redirects=True)


def _when(when: str | None) -> str:
    """Accept ISO 'YYYY-MM-DDTHH:MM', 'YYYY-MM-DD HH:MM', or None (=now)."""
    if not when:
        return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    s = when.strip().replace(" ", "T")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", s):
        s += ":00"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", s):
        raise ValueError(f"when must be 'YYYY-MM-DD HH:MM', got {when!r}")
    return s


def _hhmm(node: dict | None) -> str:
    """'HH:MM' from {'sollzeit':..., 'zeit'?:...} — real time if present, else scheduled."""
    if not node:
        return "?"
    t = node.get("zeit") or node.get("sollzeit") or ""
    return t[11:16] if len(t) >= 16 else "?"


def _delay(node: dict | None) -> str:
    if not node or not node.get("zeit") or not node.get("sollzeit"):
        return ""
    try:
        a = datetime.fromisoformat(node["zeit"])
        b = datetime.fromisoformat(node["sollzeit"])
        d = int((a - b).total_seconds() // 60)
        return f" (+{d})" if d > 0 else ""
    except ValueError:
        return ""


def _resolve(query: str) -> dict[str, Any]:
    """First station hit for a free-text name, or the id if it already looks like one."""
    if query.startswith("A=1@"):
        return {"id": query, "name": query.split("@O=")[1].split("@")[0]}
    hits = _locations(query)
    if not hits:
        raise ValueError(f"no station found for {query!r}")
    hit = hits[0]
    want = {w for w in re.findall(r"[\w]+", query.lower()) if len(w) >= 3}
    got = set(re.findall(r"[\w]+", hit["name"].lower()))
    if want and not (want & got):
        raise ValueError(
            f"no station matches {query!r}; nearest bahn.de guess was "
            f"{hit['name']!r} — use db_search_station to pick the right one")
    return hit


def _locations(query: str) -> list[dict[str, Any]]:
    with _client() as c:
        r = c.get(f"{BASE}/reiseloesung/orte",
                  params={"suchbegriff": query, "typ": "ALL", "limit": 10})
        r.raise_for_status()
    out = []
    for o in r.json():
        if o.get("type") != "ST":
            continue
        out.append({"id": o["id"], "name": o["name"], "extId": o.get("extId"),
                    "products": o.get("products", [])})
    return out


def _notes(obj: dict) -> list[str]:
    notes = []
    for m in obj.get("himMeldungen", []) or []:
        notes.append(m.get("ueberschrift") or m.get("text") or "")
    for m in obj.get("priorisierteMeldungen", []) or []:
        notes.append(m.get("text") or "")
    for m in obj.get("risNotizen", []) or []:
        notes.append(m.get("value") or m.get("text") or "")
    return [n for n in notes if n]


def _format_journey(v: dict) -> dict[str, Any]:
    legs = []
    for a in v["verbindungsAbschnitte"]:
        vm = a.get("verkehrsmittel", {}) or {}
        halte = a.get("halte") or []
        if vm.get("typ") == "WALK":
            legs.append({"type": "walk", "from": a.get("abfahrtsOrt"),
                         "to": a.get("ankunftsOrt"),
                         "minutes": (a.get("abschnittsDauer") or 0) // 60})
            continue
        dep, arr = a.get("abfahrt"), a.get("ankunft")
        legs.append({
            "line": vm.get("mittelText") or vm.get("name"),
            "direction": vm.get("richtung"),
            "from": a.get("abfahrtsOrt"),
            "dep": _hhmm(dep) + _delay(dep),
            "dep_platform": (halte[0].get("gleis") if halte else None),
            "to": a.get("ankunftsOrt"),
            "arr": _hhmm(arr) + _delay(arr),
            "arr_platform": (halte[-1].get("gleis") if halte else None),
            "cancelled": bool(a.get("originCancelled") or a.get("destinationCancelled")),
            "notes": _notes(a),
        })
    first = v["verbindungsAbschnitte"][0]
    last = v["verbindungsAbschnitte"][-1]
    price = (v.get("angebotsPreis") or {}).get("betrag")
    return {
        "date": (first.get("abfahrt") or {}).get("sollzeit", "")[:10],
        "dep": _hhmm(first.get("abfahrt")) + _delay(first.get("abfahrt")),
        "arr": _hhmm(last.get("ankunft")) + _delay(last.get("ankunft")),
        "duration_min": (v.get("verbindungsDauerInSeconds") or 0) // 60,
        "changes": v.get("umstiegsAnzahl", 0),
        "price_eur": price,
        "legs": legs,
        "notes": _notes(v),
    }


# ----------------------------------------------------------------------------
# tools
# ----------------------------------------------------------------------------
@mcp.tool()
def db_search_station(query: str) -> str:
    """Find Deutsche Bahn stations by name. Returns id, name, extId (EVA number), products."""
    return json.dumps(_locations(query), ensure_ascii=False, indent=1)


@mcp.tool()
def db_journeys(
    from_station: str,
    to_station: str,
    when: str | None = None,
    arrive_by: bool = False,
    regional_only: bool = False,
    deutschlandticket: bool = False,
    first_class: bool = False,
    max_results: int = 8,
) -> str:
    """Plan connections between two stations (bahn.de live data).

    from_station / to_station: station names ("Krefeld Hbf") or ids from db_search_station.
    when: 'YYYY-MM-DD HH:MM' local time; default now.
    arrive_by: if true, `when` is the latest arrival time instead of earliest departure.
    regional_only: exclude ICE/IC/EC (Deutschlandticket-compatible trains).
    deutschlandticket: only show connections valid with the Deutschlandticket.
    Returns JSON: per connection dep/arr (real-time, '+n' = delay), duration, changes,
    Flexpreis in EUR, legs with line/platform, and disruption notes.
    """
    src, dst = _resolve(from_station), _resolve(to_station)
    body = {
        "abfahrtsHalt": src["id"],
        "ankunftsHalt": dst["id"],
        "anfrageZeitpunkt": _when(when),
        "ankunftSuche": "ANKUNFT" if arrive_by else "ABFAHRT",
        "klasse": "KLASSE_1" if first_class else "KLASSE_2",
        "produktgattungen": REGIONAL_ONLY if (regional_only or deutschlandticket) else ALL_PRODUCTS,
        "reisende": [{"typ": "ERWACHSENER", "alter": [], "anzahl": 1,
                      "ermaessigungen": [{"art": "KEINE_ERMAESSIGUNG", "klasse": "KLASSENLOS"}]}],
        "schnelleVerbindungen": True,
        "sitzplatzOnly": False,
        "bikeCarriage": False,
        "reservierungsKontingenteVorhanden": False,
        "nurDeutschlandTicketVerbindungen": deutschlandticket,
        "deutschlandTicketVorhanden": deutschlandticket,
    }
    with _client() as c:
        r = c.post(f"{BASE}/angebote/fahrplan", json=body)
        r.raise_for_status()
    data = r.json()
    journeys = [_format_journey(v) for v in data.get("verbindungen", [])][:max_results]
    return json.dumps({"from": src["name"], "to": dst["name"],
                       "query_time": body["anfrageZeitpunkt"],
                       "mode": "arrive_by" if arrive_by else "depart_at",
                       "journeys": journeys}, ensure_ascii=False, indent=1)


def _board(kind: str, station: str, when: str | None, minutes: int, rail_only: bool) -> str:
    st = _resolve(station)
    ts = _when(when)
    with _client() as c:
        r = c.get(f"{BASE}/reiseloesung/{kind}",
                  params={"ortExtId": st["extId"], "ortId": st["id"],
                          "datum": ts[:10], "zeit": ts[11:],
                          "verkehrsmittel[]": RAIL_ONLY if rail_only else ALL_PRODUCTS})
        r.raise_for_status()
    end = datetime.fromisoformat(ts) + timedelta(minutes=minutes)
    rows = []
    for e in r.json().get("entries", []):
        t = e.get("zeit") or e.get("ezZeit") or ""
        try:
            if datetime.fromisoformat(t) > end:
                continue
        except ValueError:
            pass
        rows.append({
            "time": t[11:16],
            "realtime": (e.get("ezZeit") or "")[11:16] or None,
            "line": e.get("verkehrmittel", {}).get("mittelText") or e.get("verkehrmittel", {}).get("name"),
            "direction": e.get("terminus") if kind == "abfahrten" else e.get("origin") or e.get("terminus"),
            "platform": e.get("gleis"),
            "realtime_platform": e.get("ezGleis"),
            "cancelled": bool(e.get("canceled")),
            "notes": [m.get("text") or m.get("ueberschrift") for m in
                      (e.get("himMeldungen") or []) + (e.get("meldungen") or [])],
        })
    return json.dumps({"station": st["name"], "from": ts, "minutes": minutes,
                       kind: rows}, ensure_ascii=False, indent=1)


@mcp.tool()
def db_departures(station: str, when: str | None = None, minutes: int = 60,
                  rail_only: bool = True) -> str:
    """Departure board. `when`: 'YYYY-MM-DD HH:MM' (default now); `minutes`: window;
    `rail_only`: trains and S-Bahn only (default) — set false to include bus/tram/U-Bahn."""
    return _board("abfahrten", station, when, minutes, rail_only)


@mcp.tool()
def db_arrivals(station: str, when: str | None = None, minutes: int = 60,
                rail_only: bool = True) -> str:
    """Arrival board. `when`: 'YYYY-MM-DD HH:MM' (default now); `minutes`: window;
    `rail_only`: trains and S-Bahn only (default) — set false to include bus/tram/U-Bahn."""
    return _board("ankuenfte", station, when, minutes, rail_only)


if __name__ == "__main__":
    mcp.run()
