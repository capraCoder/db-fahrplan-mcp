# db-fahrplan — Deutsche Bahn timetable as an MCP server

A small [Model Context Protocol](https://modelcontextprotocol.io) server that gives
Claude (or any MCP client) live Deutsche Bahn timetable data: connections,
departure/arrival boards, platforms, delays, Flexpreis fares and disruption notices.

It talks **directly to the JSON endpoints behind bahn.de** — the same ones the
website uses. No API key, no third-party proxy, no rate-limited community mirror.
You get exactly what bahn.de shows.

## Tools

| Tool | What it does |
|---|---|
| `db_search_station(query)` | Find stations by name → id, name, EVA number, products |
| `db_journeys(from_station, to_station, when, arrive_by, regional_only, deutschlandticket, first_class, max_results)` | Plan connections. `arrive_by=true` makes `when` the latest arrival. Returns dep/arr with real-time delay (`+n`), duration, changes, price in EUR, legs with line + platform, disruption notes |
| `db_departures(station, when, minutes, rail_only)` | Departure board for a time window (trains + S-Bahn by default) |
| `db_arrivals(station, when, minutes, rail_only)` | Arrival board, same shape |

Times are `YYYY-MM-DD HH:MM` local German time; omit for "now".
Station arguments accept plain names (`"Krefeld Hbf"`, `"Köln Messe/Deutz"`).
Nonsense names are rejected instead of silently matched to the nearest guess —
bahn.de's own fuzzy search would happily route you from `Herby Nowe` (Poland).

## Install

```bash
pip install "mcp>=1.0" httpx
git clone https://github.com/capraCoder/db-fahrplan-mcp
```

### Claude Code

```bash
claude mcp add db-fahrplan -s user -- python /abs/path/db_fahrplan_mcp/db_fahrplan_mcp.py
```

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "db-fahrplan": {
      "command": "python",
      "args": ["/abs/path/db_fahrplan_mcp/db_fahrplan_mcp.py"]
    }
  }
}
```

## Example

> Krefeld Hbf to Köln Messe/Deutz, arrive by 08:00 on Sunday 30 Aug 2026

```
db_journeys("Krefeld Hbf", "Köln Messe/Deutz", "2026-08-30 08:00", arrive_by=true)
→ 06:35 Gl.5 RE7 → 07:23 Gl.1, 0 changes, 48 min, 25.80 EUR
```

## Test

```bash
python test_smoke.py      # station search, journey, boards, two negative controls
python test_complex.py    # Krefeld -> Garmisch: ICE + regional, 3+ changes, walk legs,
                          # Deutschlandticket filter (must contain NO ICE/IC), arrive_by bound
```

Both need network access. `test_complex.py` picks the next Wednesday so it does not rot.

## Caveats

- The bahn.de endpoints are **undocumented** and may change without notice.
  If a tool suddenly returns `KeyError`, that is why — open an issue with the
  raw response.
- Prices are the Flexpreis for one adult without BahnCard, as bahn.de quotes
  them for that query. They are informational, not a booking.
- Be reasonable with request volume. This is a personal/research tool, not a
  Deutsche Bahn product, and DB is not affiliated with it.

## Licence

MIT — see `LICENSE`.
