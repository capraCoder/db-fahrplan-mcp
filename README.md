# db-fahrplan-mcp

[![PyPI](https://img.shields.io/pypi/v/db-fahrplan-mcp)](https://pypi.org/project/db-fahrplan-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/db-fahrplan-mcp)](https://pypi.org/project/db-fahrplan-mcp/)
[![CI](https://github.com/capraCoder/db-fahrplan-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/capraCoder/db-fahrplan-mcp/actions/workflows/ci.yml)
[![bahn.de canary](https://github.com/capraCoder/db-fahrplan-mcp/actions/workflows/canary.yml/badge.svg)](https://github.com/capraCoder/db-fahrplan-mcp/actions/workflows/canary.yml)
[![License: MIT](https://img.shields.io/pypi/l/db-fahrplan-mcp)](LICENSE)

Live Deutsche Bahn timetables for Claude, Cursor, and any [MCP](https://modelcontextprotocol.io) client:
connections, every fare option, cheapest time of day, departure boards, delays, platform changes,
all stops of a train, coach sequence, stations near you.

It talks **directly to the JSON endpoints behind bahn.de** — the same ones the website uses.
No API key. No proxy. No third-party mirror to go down. You get what bahn.de shows, including
real-time data.

```
uvx db-fahrplan-mcp
```

## What it can answer

| Question | Tool |
|---|---|
| "Krefeld to Köln Messe, there by 8 on Sunday" | `db_journeys` (`arrive_by`) |
| "…with my BahnCard 50, two kids, bike, max one change, via Düsseldorf" | `db_journeys` (`bahncard`, `children_ages`, `bike`, `max_transfers`, `via`) |
| "Only Deutschlandticket trains" | `db_journeys` (`deutschlandticket`) |
| "What tickets exist for that 09:35, and what do they cost?" | `db_journey_offers` — Super Sparpreis / Sparpreis / Flexpreis / regional day tickets |
| "When is it cheapest to go to Berlin on the 12th?" | `db_best_price` — per time band of the day |
| "What leaves Köln Hbf in the next 30 min?" | `db_departures` / `db_arrivals` — live, with delay, platform change, cancellation |
| "Where is ICE 555 right now? Does it still stop at Hagen?" | `db_trip_details` — every stop, live |
| "Which sector do I stand in for 1st class on ICE 109?" | `db_train_formation` — coach sequence and platform sectors |
| "Stations near 50.94, 6.96" | `db_nearby` |
| "Is 'Frankfurt Flughafen' the Fernbahnhof or Regionalbahnhof?" | `db_search_station` |

Every journey carries a `recon_token` (→ fares), every leg and board row a `journey_id` (→ stops).
Times are **Europe/Berlin** wherever the server runs; `(+7)` means 7 minutes late; `(+1d)` means
after midnight. Occupancy (`load`), amenities (WiFi, bike, step-free, quiet zone…), real-time
platforms and disruption notes are included.

## Install

**Claude Code**

```bash
claude mcp add db-fahrplan -s user -- uvx db-fahrplan-mcp
```

**Claude Desktop / Cursor / any MCP client** — `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "db-fahrplan": {
      "command": "uvx",
      "args": ["db-fahrplan-mcp"]
    }
  }
}
```

No `uv`? `pipx install db-fahrplan-mcp` or `pip install db-fahrplan-mcp`, then use
`"command": "db-fahrplan-mcp"`.

**Remote / HTTP**

```bash
db-fahrplan-mcp --transport streamable-http --host 127.0.0.1 --port 8000
```

Binds to localhost by default. Put it behind a reverse proxy with auth before exposing it.

## Example

> Krefeld Hbf → Köln Messe/Deutz, arrive by 08:00 Sunday

```
db_journeys("Krefeld Hbf", "Köln Messe/Deutz", "2026-08-30 08:00", arrive_by=true, format="text")

Krefeld Hbf → Köln Messe/Deutz, arrive by 2026-08-30 08:00 (Europe/Berlin)

06:35 → 07:23 · 48 min · 0× change · 25.80 €
   RE7       Krefeld Hbf 06:35 Gl.5 → Köln Messe/Deutz 07:23 Gl.1
```

> …and on a bad day (real output, signal-box failure at Montabaur):

```
15:53 → 17:09 · 76 min · 0× change
   ICE 125   Köln Hbf 15:53 Gl.2 A-C → Frankfurt(Main)Hbf 17:09 Gl.1 CANCELLED
      ! Ein defektes Stellwerk im Raum Montabaur beeinträchtigt den Bahnverkehr …
   ! Verbindung fällt aus
```

`format="text"` is compact and cheap in tokens; `format="json"` (default) is structured.

## Configuration

| Env var | Default | |
|---|---|---|
| `DB_FAHRPLAN_RATE_PER_MIN` | `30` | Token-bucket limit on requests to bahn.de |
| `DB_FAHRPLAN_TIMEOUT` | `30` | HTTP timeout, seconds |
| `DB_FAHRPLAN_LOG` | `WARNING` | Log level (stderr) |
| `DB_FAHRPLAN_BASE_URL` | `https://www.bahn.de/web/api` | For testing against a mock |

## How it compares

| | db-fahrplan-mcp | DB API Marketplace servers¹ | transport.rest wrappers² |
|---|---|---|---|
| API key | none | client id + secret | none (shared 100 req/min) |
| Journey planning | ✅ | ❌ (timetables only) | ✅ |
| Prices, fare breakdown, best-price day | ✅ | ❌ | ❌ |
| BahnCard / children / via / bike / max changes | ✅ | ❌ | partial |
| Occupancy, amenities, coach sequence | ✅ | ❌ | ❌ |
| Real-time delays, platform changes, cancellations | ✅ | ✅ | ✅ |
| Runs offline tests, weekly live canary | ✅ | – | – |

¹ e.g. PaulvonBerg/db-mcp-server, jorekai/db-timetable-mcp. ² e.g. AnythingMCP's DB connector.

## Development

```bash
git clone https://github.com/capraCoder/db-fahrplan-mcp && cd db-fahrplan-mcp
python -m pip install -e ".[dev]"
pytest              # offline, replays recorded bahn.de responses (tests/fixtures)
pytest -m live      # hits bahn.de — Krefeld→Garmisch multi-modal trip, fares, best price, boards
ruff check . && mypy
```

CI runs on Linux + Windows, Python 3.10–3.13, on both MCP SDK 1.x and 2.x. A weekly canary
runs the live tests and opens an issue if bahn.de changes something.

## Caveats, honestly

- **Unofficial.** Not affiliated with, endorsed by, or supported by Deutsche Bahn AG. "Deutsche
  Bahn" and "DB" are trademarks of Deutsche Bahn AG.
- **Undocumented endpoints.** bahn.de can change or block them at any time. The canary will
  notice within a week; please open an issue with the raw response if you hit it first.
- **bahn.de's terms of use** restrict automated access. This is a personal, non-commercial tool
  with an honest `User-Agent` and a rate limiter (30 req/min by default). Use it for yourself;
  do not build a scraping farm on it. Cloud/VPN IPs are sometimes blocked (HTTP 403).
- **Prices are informational**, as bahn.de quotes them for the given travellers at that moment.
  Booking happens on bahn.de.
- **Timetable changes** (mid-December, mid-June) can make far-future queries temporarily odd.

## Licence

MIT.
