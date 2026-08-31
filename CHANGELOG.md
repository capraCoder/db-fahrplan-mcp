# Changelog

All notable changes to this project are documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versioning: [SemVer](https://semver.org/).

## [Unreleased]

## [1.0.0] - 2026-08-31

### Changed
- Declared stable: the 9-tool API surface and output shapes of 0.2.x are now the supported contract (SemVer applies from here). Development Status classifier raised to Production/Stable.

No functional changes since 0.2.2.

## [0.2.2] - 2026-08-31

### Added
- `DISCLAIMER.md`: independence, bahn.de terms responsibility, no-data/no-extraction statement, intended personal use, accuracy and liability limits, privacy, official channels.

### Changed
- Canary workflow is manual-only (no scheduled automated queries against bahn.de).
- Attribution consolidated to `capraCoder`.

No functional code changes.

## [0.2.1] - 2026-08-30

### Added
- `CITATION.cff` (software citation metadata, ORCID); OpenTimestamps proofs of release artifacts.

No code changes.

## [0.2.0] - 2026-08-30

### Added
- PyPI package with `db-fahrplan-mcp` entry point: `uvx db-fahrplan-mcp` just works.
- `db_journey_offers` — every fare bahn.de offers for one connection (Super Sparpreis, Sparpreis, Flexpreis, regional day tickets), re-checked live via the reconstruction token.
- `db_best_price` — Bestpreissuche: cheapest fare per time band of a day.
- `db_trip_details` — all stops of one train run with live times, platforms and load.
- `db_nearby` — stations around a GPS position.
- `db_train_formation` — coach sequence / platform sectors (Wagenreihung) for ICE/IC/EC.
- `db_journeys`: `via`, `bahncard`, `adults`, `children_ages`, `bike`, `seat_only`, `max_transfers`, `min_transfer_min`; occupancy (`load`) per journey and leg; amenities per leg (WiFi, bike, step-free…); real-time platforms with `*_platform_changed`; `recon_token` and `journey_id`; `alternative` flag for DB re-routes; `(+1d)` marker for arrivals past midnight.
- `format="text"` on journeys, boards and trip details — compact, token-cheap output for chat.
- Tool titles, `readOnlyHint`/`idempotentHint` annotations, server `instructions`, and a `plan_trip` prompt.
- `--transport streamable-http --host --port` for remote clients; `--version`; `DB_FAHRPLAN_*` env vars.
- Token-bucket rate limiting (30 req/min default), shared HTTP client with retries and clear errors on 403/429/5xx.
- Offline tests from recorded fixtures; live tests behind `-m live`; CI on Linux+Windows × Python 3.10–3.13 and mcp 2.x; weekly bahn.de canary that opens an issue when the API drifts; Trusted-Publishing release workflow.

### Fixed
- **Delays were never shown in journeys**: bahn.de sends real time as `echtzeit`, not `zeit`. Journeys now carry `(+n)` delays like the boards always did.
- **Cancellations on boards were invisible**: boards have no `canceled` field; cancellation is now detected from the message text.
- Times were computed in the host's local timezone; they are now always Europe/Berlin, so the server is correct when run outside Germany.
- Import crashed on `mcp>=2` (FastMCP renamed to MCPServer); a compatibility shim supports 1.10–2.x.
- Station guard rejected `Koeln` for `Köln`; names are now umlaut-folded before comparison, and the error lists bahn.de's top candidates.

## [0.1.0] - 2026-08-30

### Added
- First release: `db_journeys`, `db_departures`, `db_arrivals`, `db_search_station` over bahn.de's JSON endpoints.
