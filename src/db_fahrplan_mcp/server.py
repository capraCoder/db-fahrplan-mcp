"""
db-fahrplan-mcp — Deutsche Bahn timetables as an MCP server.

Talks directly to the JSON endpoints behind bahn.de (the same ones the website
uses). No API key, no proxy. Unofficial; not affiliated with Deutsche Bahn AG.

All times are Europe/Berlin local time, regardless of where this server runs.
Never print to stdout here: stdout is the MCP wire. Log to stderr only.
"""

from __future__ import annotations

import argparse
import functools
import json
import logging
import os
import re
import sys
import threading
import time
import unicodedata
from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

import httpx
from pydantic import Field

from . import __version__
from ._compat import Context, FastMCP, ToolError, read_only_annotations  # noqa: F401

log = logging.getLogger("db_fahrplan_mcp")

# --------------------------------------------------------------------------- config
BASE = os.environ.get("DB_FAHRPLAN_BASE_URL", "https://www.bahn.de/web/api").rstrip("/")
TIMEOUT = float(os.environ.get("DB_FAHRPLAN_TIMEOUT", "30"))
RATE_PER_MIN = int(os.environ.get("DB_FAHRPLAN_RATE_PER_MIN", "30"))
TZ = ZoneInfo("Europe/Berlin")
UA = f"db-fahrplan-mcp/{__version__} (+https://github.com/capraCoder/db-fahrplan-mcp)"
HEADERS = {
    "Accept": "application/json",
    "Content-Type": "application/json",
    "User-Agent": UA,
    "Accept-Language": "de",
}

ALL_PRODUCTS = ["ICE", "EC_IC", "IR", "REGIONAL", "SBAHN", "BUS", "SCHIFF", "UBAHN", "TRAM",
                "ANRUFPFLICHTIG"]
REGIONAL_PRODUCTS = ["REGIONAL", "SBAHN", "BUS", "SCHIFF", "UBAHN", "TRAM", "ANRUFPFLICHTIG"]
RAIL_PRODUCTS = ["ICE", "EC_IC", "IR", "REGIONAL", "SBAHN"]

BAHNCARD = {
    None: "KEINE_ERMAESSIGUNG", "": "KEINE_ERMAESSIGUNG", "none": "KEINE_ERMAESSIGUNG",
    "25": "BAHNCARD25", "50": "BAHNCARD50", "100": "BAHNCARD100",
    "business25": "BAHNCARDBUSINESS25", "business50": "BAHNCARDBUSINESS50",
    "at-vorteilscard": "A-VORTEILSCARD", "ch-halbtax": "CH-HALBTAXABO_OHNE_RAILPLUS",
    "ch-ga": "CH-GENERAL-ABONNEMENT", "nl-40": "NL-40_OHNE_RAILPLUS", "at-klimaticket": "KLIMATICKET_OE",
}
LOAD = {0: None, 1: "low", 2: "medium", 3: "high", 4: "very high", 99: "very high"}
AMENITY = {  # zugattribute keys -> short English
    "RG": "step-free vehicle", "RO": "wheelchair space", "OC": "accessible WC",
    "FB": "bike carriage (limited)", "FK": "bike carriage", "WV": "WiFi", "LS": "power sockets",
    "KL": "air conditioning", "BR": "bistro", "RS": "restaurant", "KR": "family area",
    "RZ": "quiet zone", "EA": "seat reservation possible", "BT": "bike reservation required",
}
Fmt = Literal["json", "text"]

INSTRUCTIONS = """Live Deutsche Bahn (German railway) timetables from bahn.de. No API key.
Workflow: call db_journeys with station names directly; use db_search_station only if a name
is ambiguous or rejected. All times are Europe/Berlin local time as 'YYYY-MM-DD HH:MM'.
'+n' after a time is a real-time delay in minutes. Every journey carries recon_token (for
db_journey_offers = full fare breakdown) and every leg carries journey_id (for db_trip_details =
all intermediate stops, live). Prefer format='text' for chat answers; 'json' for processing.
Unofficial tool on undocumented bahn.de endpoints; data can be wrong or break without
notice — advise the user to verify anything that matters on bahn.de before relying on it."""

mcp = FastMCP("db-fahrplan", instructions=INSTRUCTIONS)
READ_ONLY = read_only_annotations()


# --------------------------------------------------------------------------- HTTP
class _Bucket:
    """Token bucket: be a polite guest on bahn.de."""

    def __init__(self, per_minute: int) -> None:
        self.cap = max(1, per_minute)
        self.tokens = float(self.cap)
        self.stamp = time.monotonic()
        self.lock = threading.Lock()

    def take(self) -> None:
        with self.lock:
            now = time.monotonic()
            self.tokens = min(self.cap, self.tokens + (now - self.stamp) * self.cap / 60)
            self.stamp = now
            if self.tokens < 1:
                wait = (1 - self.tokens) * 60 / self.cap
                log.info("rate limit: sleeping %.1fs", wait)
                time.sleep(wait)
                self.tokens = 0
            else:
                self.tokens -= 1


_bucket = _Bucket(RATE_PER_MIN)
_client: httpx.Client | None = None


def client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True,
                               transport=httpx.HTTPTransport(retries=2))
    return _client


def _request(method: str, path: str, **kw: Any) -> Any:
    """GET/POST with rate limiting, retry on 429/5xx, and human-readable failures."""
    url = f"{BASE}/{path}"
    last: Exception | None = None
    for attempt in range(3):
        _bucket.take()
        try:
            r = client().request(method, url, **kw)
        except httpx.TimeoutException as e:
            last = e
            log.warning("timeout on %s (attempt %d)", path, attempt + 1)
            continue
        except httpx.HTTPError as e:
            raise ToolError(f"network error reaching bahn.de: {e}") from e
        if r.status_code in (429, 500, 502, 503, 504):
            last = httpx.HTTPStatusError(f"{r.status_code}", request=r.request, response=r)
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code == 403:
            raise ToolError("bahn.de refused the request (HTTP 403). Cloud/VPN IPs are often "
                            "blocked; try from a residential connection or wait a few minutes.")
        if r.status_code >= 400:
            raise ToolError(f"bahn.de returned HTTP {r.status_code} for {path}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as e:
            raise ToolError(f"bahn.de returned non-JSON for {path}") from e
    raise ToolError(f"bahn.de unavailable after 3 attempts ({last}); retry in a minute")


# --------------------------------------------------------------------------- time helpers
def now_berlin() -> datetime:
    return datetime.now(TZ)


def parse_when(when: str | None) -> datetime:
    """'YYYY-MM-DD HH:MM' (Europe/Berlin) -> aware datetime. None -> now."""
    if not when:
        return now_berlin().replace(second=0, microsecond=0)
    s = when.strip().replace("T", " ")
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})[ ](\d{2}:\d{2})(?::\d{2})?", s)
    if not m:
        raise ToolError(f"`when` must be 'YYYY-MM-DD HH:MM' in Europe/Berlin local time, got "
                        f"{when!r}. Relative words like 'tomorrow' are not accepted — compute the "
                        f"date first (today is {now_berlin():%Y-%m-%d}).")
    try:
        return datetime.strptime(f"{m[1]} {m[2]}", "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    except ValueError as e:
        raise ToolError(f"invalid date/time {when!r}: {e}") from e


def _api_ts(d: datetime) -> str:
    return d.astimezone(TZ).strftime("%Y-%m-%dT%H:%M:%S")


def _parse_naive(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s[:19]).replace(tzinfo=TZ)
    except ValueError:
        return None


def _times(node: dict | None, ref_date: str | None = None) -> tuple[str, int | None, str | None]:
    """(HH:MM of best-known time, delay minutes or None, ISO date if != ref_date)."""
    if not node:
        return "?", None, None
    soll = _parse_naive(node.get("sollzeit") or node.get("zeit"))
    echt = _parse_naive(node.get("echtzeit") or node.get("ezZeit"))
    best = echt or soll
    if not best:
        return "?", None, None
    delay = int((echt - soll).total_seconds() // 60) if (echt and soll) else None
    date = best.strftime("%Y-%m-%d")
    return best.strftime("%H:%M"), delay, (date if date != ref_date else None)


def _stamp(node: dict | None, ref_date: str | None = None) -> str:
    """Compact 'HH:MM', 'HH:MM (+7)', 'HH:MM (+1d)' form."""
    hhmm, delay, date = _times(node, ref_date)
    s = hhmm
    if delay:
        s += f" ({delay:+d})"
    if date and ref_date:
        days = (datetime.fromisoformat(date) - datetime.fromisoformat(ref_date)).days
        s += f" (+{days}d)" if days > 0 else f" ({days}d)"
    return s


# --------------------------------------------------------------------------- stations
def _fold(s: str) -> str:
    s = s.lower().replace("ä", "ae").replace("ö", "oe").replace("ü", "ue").replace("ß", "ss")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return s


def _words(s: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", _fold(s)) if len(w) >= 3}


@functools.lru_cache(maxsize=512)
def _locations(query: str, kind: str = "ST") -> tuple[dict[str, Any], ...]:
    data = _request("GET", "reiseloesung/orte",
                    params={"suchbegriff": query, "typ": "ALL", "limit": 10})
    out = []
    for o in data:
        if kind != "ALL" and o.get("type") != kind:
            continue
        out.append({"id": o["id"], "name": o["name"], "eva": o.get("extId"),
                    "type": o.get("type"), "products": o.get("products", []),
                    "lat": o.get("lat"), "lon": o.get("lon")})
    return tuple(out)


def resolve(query: str) -> dict[str, Any]:
    """Station name | EVA number | full bahn.de id -> {id, name, eva}."""
    q = query.strip()
    if q.startswith("A=1@"):
        eva = re.search(r"@L=(\d+)@", q)
        name = re.search(r"@O=([^@]+)@", q)
        return {"id": q, "name": name[1] if name else q, "eva": eva[1] if eva else None}
    if re.fullmatch(r"\d{6,8}", q):
        return {"id": f"A=1@L={q}@", "name": q, "eva": q}
    hits = _locations(q)
    if not hits:
        raise ToolError(f"no station found for {q!r}")
    hit = hits[0]
    if _words(q) and not (_words(q) & _words(hit["name"])):
        alts = ", ".join(h["name"] for h in hits[:3])
        raise ToolError(f"no station matches {q!r}; bahn.de's nearest guesses were: {alts}. "
                        f"Pass one of those names, or call db_search_station.")
    return hit


# --------------------------------------------------------------------------- parsing
def _msg(m: Any) -> str:
    """bahn.de messages come as dicts with varying keys, or as bare strings."""
    if isinstance(m, str):
        return m
    if isinstance(m, dict):
        return str(m.get("ueberschrift") or m.get("text") or m.get("value") or "")
    return ""


def _notes(obj: dict) -> list[str]:
    out: list[str] = []
    for key in ("himMeldungen", "priorisierteMeldungen", "risNotizen", "meldungen"):
        for m in obj.get(key) or []:
            out.append(_msg(m))
    seen: set[str] = set()
    uniq = []
    for n in out:
        if n and n not in seen:
            seen.add(n)
            uniq.append(n)
    return uniq


def _load(obj: dict) -> dict[str, str]:
    out = {}
    for a in obj.get("auslastungsmeldungen") or []:
        lvl = LOAD.get(a.get("stufe", 0))
        if lvl:
            out["1st" if a.get("klasse") == "KLASSE_1" else "2nd"] = lvl
    return out


def _amenities(vm: dict) -> list[str]:
    out = []
    for z in vm.get("zugattribute") or []:
        k = (z.get("key") or "").strip()
        if k in AMENITY:
            out.append(AMENITY[k])
    return out


def _clean(d: Any) -> Any:
    """Drop None / empty containers recursively — saves tokens."""
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items() if v not in (None, "", [], {}, ())}
    if isinstance(d, list):
        return [_clean(x) for x in d]
    return d


def format_journey(v: dict) -> dict[str, Any]:
    secs = v["verbindungsAbschnitte"]
    ref = ((secs[0].get("abfahrt") or {}).get("sollzeit") or "")[:10] or None
    legs: list[dict[str, Any]] = []
    for a in secs:
        vm = a.get("verkehrsmittel") or {}
        if vm.get("typ") in ("WALK", "TRANSFER"):
            legs.append({"type": "walk", "from": a.get("abfahrtsOrt"), "to": a.get("ankunftsOrt"),
                         "minutes": (a.get("abschnittsDauer") or 0) // 60})
            continue
        halte = a.get("halte") or []
        h0, h1 = (halte[0] if halte else {}), (halte[-1] if halte else {})
        legs.append({
            "line": vm.get("mittelText") or vm.get("name"),
            "direction": vm.get("richtung"),
            "from": a.get("abfahrtsOrt"),
            "dep": _stamp(a.get("abfahrt"), ref),
            "dep_platform": h0.get("ezGleis") or h0.get("gleis"),
            "dep_platform_changed": bool(h0.get("ezGleis") and h0.get("ezGleis") != h0.get("gleis")),
            "to": a.get("ankunftsOrt"),
            "arr": _stamp(a.get("ankunft"), ref),
            "arr_platform": h1.get("ezGleis") or h1.get("gleis"),
            "arr_platform_changed": bool(h1.get("ezGleis") and h1.get("ezGleis") != h1.get("gleis")),
            "stops": max(0, len(halte) - 1) or None,
            "load": _load(a) or None,
            "amenities": _amenities(vm) or None,
            "origin_cancelled": a.get("originCancelled") or None,
            "destination_cancelled": a.get("destinationCancelled") or None,
            "journey_id": a.get("journeyId"),
            "notes": _notes(a),
        })
    price = (v.get("angebotsPreis") or {}).get("betrag")
    return {
        "date": ref,
        "dep": _stamp(secs[0].get("abfahrt"), ref),
        "arr": _stamp(secs[-1].get("ankunft"), ref),
        "duration_min": (v.get("ezVerbindungsDauerInSeconds") or v.get("verbindungsDauerInSeconds") or 0) // 60,
        "changes": v.get("umstiegsAnzahl", 0),
        "price_eur": price,
        "price_note": ("price deferred — call db_journey_offers" if v.get("isAngebotseinholungNachgelagert")
                       else "partial fare" if v.get("hasTeilpreis") else None),
        "load": _load(v) or None,
        "alternative": v.get("isAlternativeVerbindung") or None,
        "service_days": (v.get("serviceDays") or {}).get("regular") if isinstance(v.get("serviceDays"), dict) else None,
        "recon_token": v.get("ctxRecon"),
        "legs": legs,
        "notes": _notes(v),
    }


def journey_text(j: dict) -> str:
    head = f"{j['dep']} → {j['arr']} · {j['duration_min']} min · {j['changes']}× change"
    if j.get("price_eur") is not None:
        head += f" · {j['price_eur']:.2f} €"
    if j.get("load"):
        head += " · load " + "/".join(f"{k} {v}" for k, v in j["load"].items())
    if j.get("alternative"):
        head += " · ALTERNATIVE (disruption)"
    lines = [head]
    for leg in j["legs"]:
        if leg.get("type") == "walk":
            lines.append(f"   walk {leg['minutes']} min → {leg['to']}")
            continue
        p1 = f" Gl.{leg['dep_platform']}" if leg.get("dep_platform") else ""
        p2 = f" Gl.{leg['arr_platform']}" if leg.get("arr_platform") else ""
        flag = " CANCELLED" if leg.get("origin_cancelled") or leg.get("destination_cancelled") else ""
        lines.append(f"   {leg['line']:<9} {leg['from']} {leg['dep']}{p1} → {leg['to']} {leg['arr']}{p2}{flag}")
        for n in leg.get("notes") or []:
            lines.append(f"      ! {n[:160]}")
    for n in j.get("notes") or []:
        lines.append(f"   ! {n[:160]}")
    return "\n".join(lines)


def _travellers(bahncard: str | None, first_class: bool, adults: int, children_ages: list[int] | None) -> list[dict]:
    art = BAHNCARD.get((bahncard or "").lower().strip() or None)
    if art is None:
        raise ToolError(f"unknown bahncard {bahncard!r}; use one of {sorted(k for k in BAHNCARD if k)}")
    klasse = "KLASSENLOS" if art == "KEINE_ERMAESSIGUNG" else ("KLASSE_1" if first_class else "KLASSE_2")
    out = [{"typ": "ERWACHSENER", "anzahl": max(1, adults), "alter": [],
            "ermaessigungen": [{"art": art, "klasse": klasse}]}]
    for age in children_ages or []:
        typ = "KLEINKIND" if age <= 5 else "FAMILIENKIND" if age <= 14 else "JUGENDLICHER"
        out.append({"typ": typ, "anzahl": 1, "alter": [str(age)],
                    "ermaessigungen": [{"art": "KEINE_ERMAESSIGUNG", "klasse": "KLASSENLOS"}]})
    return out


def _journey_body(src: dict, dst: dict, when: datetime, *, arrive_by: bool, first_class: bool,
                  products: list[str], travellers: list[dict], deutschlandticket: bool,
                  bike: bool, seat_only: bool, via: list[dict] | None, max_transfers: int | None,
                  min_transfer_min: int | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "abfahrtsHalt": src["id"], "ankunftsHalt": dst["id"],
        "anfrageZeitpunkt": _api_ts(when),
        "ankunftSuche": "ANKUNFT" if arrive_by else "ABFAHRT",
        "klasse": "KLASSE_1" if first_class else "KLASSE_2",
        "produktgattungen": products,
        "reisende": travellers,
        "schnelleVerbindungen": True,
        "sitzplatzOnly": seat_only,
        "bikeCarriage": bike,
        "reservierungsKontingenteVorhanden": False,
        "nurDeutschlandTicketVerbindungen": deutschlandticket,
        "deutschlandTicketVorhanden": deutschlandticket,
    }
    if via:
        body["zwischenhalte"] = via
    if max_transfers is not None:
        body["maxUmstiege"] = max(0, max_transfers)
    if min_transfer_min is not None:
        body["minUmstiegszeit"] = max(0, min_transfer_min)
    return body


def _out(payload: dict, fmt: Fmt, text: str) -> str:
    return text if fmt == "text" else json.dumps(_clean(payload), ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- tools
@mcp.tool(title="Search stations", annotations=READ_ONLY)
def db_search_station(
    query: Annotated[str, Field(description="Station name or fragment, e.g. 'Köln Messe' or 'Frankfurt Flughafen'")],
    include_stops: Annotated[bool, Field(description="Also return bus/tram stops and addresses")] = False,
) -> str:
    """Find Deutsche Bahn stations by name. Call this only when a name is ambiguous or another
    tool rejected it; the other tools accept plain station names directly. Returns name, EVA
    number (`eva`), products served, and the full bahn.de `id` (also accepted by other tools)."""
    hits = _locations(query.strip(), "ALL" if include_stops else "ST")
    return json.dumps(_clean([dict(h) for h in hits]), ensure_ascii=False, indent=1)


@mcp.tool(title="Plan a journey", annotations=READ_ONLY)
def db_journeys(
    from_station: Annotated[str, Field(description="Origin: station name, EVA number, or bahn.de id")],
    to_station: Annotated[str, Field(description="Destination: station name, EVA number, or bahn.de id")],
    when: Annotated[str | None, Field(
        description="'YYYY-MM-DD HH:MM' Europe/Berlin local time. Omit for now. No relative "
        "words.")] = None,
    arrive_by: Annotated[bool, Field(
        description="If true, `when` is the LATEST ARRIVAL time instead of earliest "
        "departure")] = False,
    via: Annotated[str | None, Field(description="Optional intermediate station the route must pass through")] = None,
    bahncard: Annotated[str | None, Field(
        description="'25', '50', '100', 'business25', 'business50', or None. Affects prices "
        "only.")] = None,
    adults: Annotated[int, Field(ge=1, le=9)] = 1,
    children_ages: Annotated[list[int] | None, Field(
        description="Ages of accompanying children, e.g. [4, 9]. Affects prices.")] = None,
    first_class: Annotated[bool, Field(description="Price 1st class instead of 2nd")] = False,
    regional_only: Annotated[bool, Field(
        description="Exclude ICE/IC/EC (Deutschlandticket-compatible trains)")] = False,
    deutschlandticket: Annotated[bool, Field(
        description="Only connections fully valid with the Deutschlandticket (implies "
        "regional_only, no price)")] = False,
    bike: Annotated[bool, Field(description="Require bike carriage")] = False,
    seat_only: Annotated[bool, Field(description="Only trains where a seat reservation is possible")] = False,
    max_transfers: Annotated[int | None, Field(ge=0, le=10)] = None,
    min_transfer_min: Annotated[int | None, Field(ge=0, le=120, description="Minimum minutes for each change")] = None,
    max_results: Annotated[int, Field(ge=1, le=12)] = 6,
    format: Annotated[Fmt, Field(
        description="'text' = compact human table (best for chat); 'json' = structured")] = "json",
) -> str:
    """Plan train connections between two stations with live bahn.de data.

    Each connection: dep/arr (real-time; '(+7)' = 7 min late, '(+1d)' = next day), duration,
    changes, price in EUR for the given travellers/BahnCard (None with Deutschlandticket), load
    (occupancy) per class, `recon_token` (pass to db_journey_offers for Sparpreis/Flexpreis
    breakdown), and per leg: line, platforms (real-time, with `*_platform_changed`), stops count,
    amenities (WiFi, bike, step-free…), cancellations, `journey_id` (pass to db_trip_details),
    and disruption notes. A journey flagged `alternative` is DB's re-route around a disruption.
    """
    src, dst = resolve(from_station), resolve(to_station)
    t = parse_when(when)
    regional = regional_only or deutschlandticket
    body = _journey_body(
        src, dst, t, arrive_by=arrive_by, first_class=first_class,
        products=REGIONAL_PRODUCTS if regional else ALL_PRODUCTS,
        travellers=_travellers(bahncard, first_class, adults, children_ages),
        deutschlandticket=deutschlandticket, bike=bike, seat_only=seat_only,
        via=[{"id": resolve(via)["id"]}] if via else None,
        max_transfers=max_transfers, min_transfer_min=min_transfer_min)
    data = _request("POST", "angebote/fahrplan", json=body)
    journeys = [format_journey(v) for v in data.get("verbindungen", [])][:max_results]
    payload = {"from": src["name"], "to": dst["name"], "timezone": "Europe/Berlin",
               "query": f"{'arrive by' if arrive_by else 'depart'} {t:%Y-%m-%d %H:%M}",
               "journeys": journeys}
    head = f"{src['name']} → {dst['name']}, {payload['query']} (Europe/Berlin)"
    text = head + "\n\n" + ("\n\n".join(journey_text(j) for j in journeys) or "no connections found")
    return _out(payload, format, text)


@mcp.tool(title="Fare options for one journey", annotations=READ_ONLY)
def db_journey_offers(
    recon_token: Annotated[str, Field(description="`recon_token` from a db_journeys result")],
    bahncard: Annotated[str | None, Field(description="'25', '50', '100', 'business25', 'business50', or None")] = None,
    adults: Annotated[int, Field(ge=1, le=9)] = 1,
    children_ages: list[int] | None = None,
    first_class: bool = False,
) -> str:
    """All fares bahn.de offers for ONE specific connection (Super Sparpreis, Sparpreis,
    Flexpreis, regional day tickets…) with price and class, re-checked live. Use after
    db_journeys when the user asks 'how much' or 'cheapest ticket'. Prices are informational,
    not a booking."""
    body = {"ctxRecon": recon_token, "klasse": "KLASSE_1" if first_class else "KLASSE_2",
            "reisende": _travellers(bahncard, first_class, adults, children_ages),
            "deutschlandTicketVorhanden": False, "nurDeutschlandTicketVerbindungen": False,
            "reservierungsKontingenteVorhanden": False}
    data = _request("POST", "angebote/recon", json=body)
    vs = data.get("verbindungen") or []
    if not vs:
        raise ToolError("bahn.de could not reconstruct this journey (token expired?) — re-run db_journeys")
    v = vs[0]
    offers = []
    for o in v.get("reiseAngebote") or v.get("angebote") or []:
        offers.append({"name": o.get("name"), "price_eur": (o.get("preis") or {}).get("betrag"),
                       "class": o.get("klasse"), "conditions": (o.get("konditionsAnzeigen") or o.get("konditionen")),
                       "cancellable": o.get("stornierbar")})
    offers.sort(key=lambda x: (x["price_eur"] is None, x["price_eur"] or 0))
    return json.dumps(_clean({"journey": format_journey(v), "offers": offers}), ensure_ascii=False, indent=1)


@mcp.tool(title="Cheapest fares across a day", annotations=READ_ONLY)
def db_best_price(
    from_station: str,
    to_station: str,
    date: Annotated[str, Field(description="'YYYY-MM-DD' — the travel day")],
    bahncard: str | None = None,
    adults: Annotated[int, Field(ge=1, le=9)] = 1,
    children_ages: list[int] | None = None,
    first_class: bool = False,
    regional_only: bool = False,
) -> str:
    """Bestpreissuche: the cheapest available fare in each time band of a day (00–07, 07–10,
    10–13, 13–16, 16–19, 19–24) with the connection it applies to. Use for 'when is it cheapest
    to go'. Typically only meaningful for long-distance routes booked in advance."""
    src, dst = resolve(from_station), resolve(to_station)
    t = parse_when(f"{date.strip()} 00:00")
    body = _journey_body(src, dst, t, arrive_by=False, first_class=first_class,
                         products=REGIONAL_PRODUCTS if regional_only else ALL_PRODUCTS,
                         travellers=_travellers(bahncard, first_class, adults, children_ages),
                         deutschlandticket=False, bike=False, seat_only=False, via=None,
                         max_transfers=None, min_transfer_min=None)
    data = _request("POST", "angebote/tagesbestpreis", json=body)
    bands = []
    for iv in data.get("intervalle") or []:
        best = None
        for c in iv.get("verbindungen") or []:
            if c.get("verbindung"):
                best = format_journey(c["verbindung"])
                break
        bands.append({"from": (iv.get("ab") or "")[11:16], "to": (iv.get("bis") or "")[11:16],
                      "price_eur": (iv.get("preis") or {}).get("betrag"),
                      "cheapest_of_day": iv.get("bestpreis") or None,
                      "partial_fare": iv.get("teilpreis") or None, "journey": best})
    if not bands:
        return json.dumps({"from": src["name"], "to": dst["name"], "date": date, "bands": [],
                           "note": "bahn.de returned no best-price bands (route too short, day too "
                                   "close, or regional-only). Use db_journeys instead."},
                          ensure_ascii=False)
    return json.dumps(_clean({"from": src["name"], "to": dst["name"], "date": date, "bands": bands}),
                      ensure_ascii=False, indent=1)


def _board(kind: str, station: str, when: str | None, minutes: int, rail_only: bool, fmt: Fmt) -> str:
    st = resolve(station)
    t = parse_when(when)
    if not st.get("eva"):
        raise ToolError(f"need an EVA number for boards; call db_search_station({station!r})")
    data = _request("GET", f"reiseloesung/{kind}",
                    params={"ortExtId": st["eva"], "ortId": st["id"], "datum": f"{t:%Y-%m-%d}",
                            "zeit": f"{t:%H:%M:%S}",
                            "verkehrsmittel[]": RAIL_PRODUCTS if rail_only else ALL_PRODUCTS})
    end = t + timedelta(minutes=minutes)
    rows = []
    for e in data.get("entries") or []:
        best = _parse_naive(e.get("ezZeit") or e.get("zeit"))
        sched = _parse_naive(e.get("zeit"))
        if sched and sched > end:
            continue
        notes = _notes(e)
        # boards have no boolean; cancellation is a typed message (HALT_AUSFALL / FAHRT_AUSFALL) or text
        cancelled = any(str((m or {}).get("type", "")).endswith("AUSFALL") for m in e.get("meldungen") or []
                        if isinstance(m, dict)) or any(re.search(r"f[äa]llt aus|entf[äa]llt", n, re.I) for n in notes)
        vm = e.get("verkehrmittel") or {}  # sic: bahn.de spells it without the second 's' here
        rows.append({
            "time": _stamp({"sollzeit": e.get("zeit"), "echtzeit": e.get("ezZeit")}),
            "line": vm.get("mittelText") or vm.get("name"),
            "direction": e.get("terminus"),
            "platform": e.get("ezGleis") or e.get("gleis"),
            "platform_changed": bool(e.get("ezGleis") and e.get("ezGleis") != e.get("gleis")),
            "cancelled": cancelled or None,
            "journey_id": e.get("journeyId"),
            "notes": notes,
            "_sort": best or sched,
        })
    rows.sort(key=lambda r: r["_sort"] or t)
    truncated = len(rows) > 60
    rows = rows[:60]
    for r in rows:
        r.pop("_sort", None)
    payload = {"station": st["name"], "from": f"{t:%Y-%m-%d %H:%M}", "minutes": minutes,
               "timezone": "Europe/Berlin", "truncated": truncated or None, kind: rows}
    label = "Departures" if kind == "abfahrten" else "Arrivals"
    lines = [f"{label} {st['name']} from {t:%Y-%m-%d %H:%M} (+{minutes} min, Europe/Berlin)"]
    for r in rows:
        p = f" Gl.{r['platform']}" + ("!" if r["platform_changed"] else "") if r.get("platform") else ""
        c = " CANCELLED" if r["cancelled"] else ""
        lines.append(f"{r['time']:<12} {str(r['line']):<9} {r['direction']}{p}{c}")
    if truncated:
        lines.append("… truncated at 60 rows; narrow `minutes` or set rail_only")
    return _out(payload, fmt, "\n".join(lines))


@mcp.tool(title="Departure board", annotations=READ_ONLY)
def db_departures(
    station: Annotated[str, Field(description="Station name, EVA number, or bahn.de id")],
    when: Annotated[str | None, Field(description="'YYYY-MM-DD HH:MM' Europe/Berlin; omit for now")] = None,
    minutes: Annotated[int, Field(ge=5, le=720, description="Window length")] = 60,
    rail_only: Annotated[bool, Field(description="Trains and S-Bahn only; false adds bus/tram/U-Bahn")] = True,
    format: Fmt = "json",
) -> str:
    """Live departure board: time (real-time, with delay), line, destination, platform (real-time,
    `platform_changed` flag), cancellation, disruption notes, and `journey_id` for db_trip_details."""
    return _board("abfahrten", station, when, minutes, rail_only, format)


@mcp.tool(title="Arrival board", annotations=READ_ONLY)
def db_arrivals(
    station: Annotated[str, Field(description="Station name, EVA number, or bahn.de id")],
    when: Annotated[str | None, Field(description="'YYYY-MM-DD HH:MM' Europe/Berlin; omit for now")] = None,
    minutes: Annotated[int, Field(ge=5, le=720)] = 60,
    rail_only: bool = True,
    format: Fmt = "json",
) -> str:
    """Live arrival board, same shape as db_departures (direction = origin)."""
    return _board("ankuenfte", station, when, minutes, rail_only, format)


@mcp.tool(title="Trip details (all stops, live)", annotations=READ_ONLY)
def db_trip_details(
    journey_id: Annotated[str, Field(description="`journey_id` from a db_journeys leg or a board row")],
    format: Fmt = "json",
) -> str:
    """Every stop of one specific train run with scheduled and real-time arrival/departure,
    platform (real-time), load, and notes — i.e. 'where is this train now, is my stop still
    served, which platform at my stop'. Also returns train amenities and running days."""
    data = _request("GET", "reiseloesung/fahrt", params={"journeyId": journey_id, "poly": "false"})
    ref = (data.get("reisetag") or "")[:10] or None
    stops = []
    for h in data.get("halte") or []:
        stops.append({
            "name": h.get("name"), "eva": h.get("extId"),
            "arr": _stamp(h.get("ankunft"), ref) if h.get("ankunft") else None,
            "dep": _stamp(h.get("abfahrt"), ref) if h.get("abfahrt") else None,
            "platform": h.get("ezGleis") or h.get("gleis"),
            "platform_changed": bool(h.get("ezGleis") and h.get("ezGleis") != h.get("gleis")),
            "load": _load(h) or None,
            "notes": _notes(h),
        })
    payload = {"train": data.get("zugName"), "date": ref, "cancelled": data.get("cancelled") or None,
               "runs_on": data.get("regulaereVerkehrstage"),
               "amenities": _amenities({"zugattribute": data.get("zugattribute")}) or None,
               "stops": stops, "notes": _notes(data)}
    lines = [f"{payload['train']} on {ref}" + (" — CANCELLED" if payload["cancelled"] else "")]
    for s in stops:
        p = f" Gl.{s['platform']}" + ("!" if s["platform_changed"] else "") if s.get("platform") else ""
        lines.append(f"  {s.get('arr') or '':<14} {s.get('dep') or '':<14} {s['name']}{p}")
    return _out(payload, format, "\n".join(lines))


@mcp.tool(title="Stations near coordinates", annotations=READ_ONLY)
def db_nearby(
    latitude: Annotated[float, Field(ge=-90, le=90)],
    longitude: Annotated[float, Field(ge=-180, le=180)],
    radius_m: Annotated[int, Field(ge=50, le=20000)] = 1000,
    rail_only: Annotated[bool, Field(description="Only stops served by trains/S-Bahn")] = True,
    max_results: Annotated[int, Field(ge=1, le=50)] = 10,
) -> str:
    """Stations and stops around a GPS position — 'stations near me'. Returns name, EVA number,
    products, and the bahn.de `id` usable in every other tool."""
    data = _request("GET", "reiseloesung/orte/nearby",
                    params={"lat": latitude, "long": longitude, "radius": radius_m, "maxNo": 50})
    out = []
    for o in data:
        prods = set(o.get("products") or [])
        if rail_only and not (prods & set(RAIL_PRODUCTS)):
            continue
        out.append({"name": o.get("name"), "eva": o.get("extId"), "id": o.get("id"),
                    "products": sorted(prods), "lat": o.get("lat"), "lon": o.get("lon")})
        if len(out) >= max_results:
            break
    return json.dumps(_clean(out), ensure_ascii=False, indent=1)


@mcp.tool(title="Coach sequence (Wagenreihung)", annotations=READ_ONLY)
def db_train_formation(
    train: Annotated[str, Field(description="Long-distance train, e.g. 'ICE 610' or 'IC 2013'")],
    station: Annotated[str, Field(description="Station where it departs — name or EVA number")],
    when: Annotated[str, Field(description="Scheduled departure 'YYYY-MM-DD HH:MM' at that station (Europe/Berlin)")],
) -> str:
    """Coach order and platform sectors for a long-distance train at a station: which sector
    (A–G) 1st class, bistro, bike and quiet coaches stop at, and the real-time platform. Answers
    'where do I stand on the platform'. ICE/IC/EC only; not available for regional trains."""
    m = re.fullmatch(r"\s*(ICE|IC|EC|ECE|RJ|NJ|TGV)\s*(\d{1,5})\s*", train, re.I)
    if not m:
        raise ToolError("train must look like 'ICE 610' or 'IC 2013'")
    cat, num = m[1].upper(), m[2]
    st = resolve(station)
    if not st.get("eva"):
        raise ToolError("need an EVA number for the station; call db_search_station")
    t = parse_when(when)
    utc = t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    data = _request("GET", "reisebegleitung/wagenreihung/vehicle-sequence",
                    params={"category": cat, "number": num, "evaNumber": st["eva"],
                            "date": f"{t:%Y-%m-%d}", "time": utc})
    groups = []
    for g in data.get("groups") or []:
        tr = g.get("transport") or {}
        coaches = []
        for v in g.get("vehicles") or []:
            typ = v.get("type") or {}
            pos = v.get("platformPosition") or {}
            am = [a["type"].lower().replace("_", " ") for a in v.get("amenities") or []
                  if a.get("status") not in ("UNDEFINED", "NOT_AVAILABLE")]
            coaches.append({"number": v.get("wagonIdentificationNumber"), "sector": pos.get("sector"),
                            "class": ("1st" if typ.get("hasFirstClass") and not typ.get("hasEconomyClass")
                                      else "2nd" if typ.get("hasEconomyClass") and not typ.get("hasFirstClass")
                                      else "1st/2nd" if typ.get("hasFirstClass") else None),
                            "category": (typ.get("category") or "").lower().replace("_", " ") or None,
                            "status": v.get("status") if v.get("status") != "OPEN" else None,
                            "amenities": am or None})
        groups.append({"train": f"{tr.get('category', cat)} {tr.get('number', num)}",
                       "destination": (tr.get("destination") or {}).get("name"), "coaches": coaches})
    payload = {"train": f"{cat} {num}", "station": st["name"], "when": f"{t:%Y-%m-%d %H:%M}",
               "platform": data.get("departurePlatform") or data.get("platform"),
               "platform_scheduled": data.get("departurePlatformSchedule"),
               "status": data.get("sequenceStatus"), "groups": groups}
    return json.dumps(_clean(payload), ensure_ascii=False, indent=1)


# --------------------------------------------------------------------------- prompt
@mcp.prompt(name="plan_trip", title="Plan a train trip in Germany")
def plan_trip(from_station: str, to_station: str, when: str = "", constraints: str = "") -> str:
    """Guided trip planning: connections, then fares, then platform advice."""
    return (f"Plan a Deutsche Bahn trip from {from_station} to {to_station}"
            + (f" for {when}" if when else " departing now")
            + (f". Constraints: {constraints}" if constraints else "")
            + ". Use db_journeys (format='text'); if the user cares about price call db_journey_offers "
              "on the best recon_token; mention delays, platform changes and disruption notes; "
              "give one clear recommendation and one fallback.")


# --------------------------------------------------------------------------- entry point
def main(argv: Iterable[str] | None = None) -> None:
    p = argparse.ArgumentParser(prog="db-fahrplan-mcp",
                                description="Deutsche Bahn timetables as an MCP server (bahn.de, no API key).")
    p.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--log-level", default=os.environ.get("DB_FAHRPLAN_LOG", "WARNING"))
    p.add_argument("--version", action="version", version=f"db-fahrplan-mcp {__version__}")
    a = p.parse_args(list(argv) if argv is not None else None)
    logging.basicConfig(stream=sys.stderr, level=a.log_level.upper(),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if a.transport == "stdio":
        mcp.run()
    else:
        for attr, val in (("host", a.host), ("port", a.port)):
            try:
                setattr(mcp.settings, attr, val)  # mcp 1.x
            except AttributeError:
                pass
        mcp.run(transport=a.transport)


if __name__ == "__main__":
    main()
