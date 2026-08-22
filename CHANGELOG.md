# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Fixed
- Stop prompting to re-enter credentials on transient `401`s from the SIRI feed. Basic Auth is stateless, so a lone 401 amid working polls is almost always a server blip, not a credential change. Data polls now retry a 401 like any other transient error, and the coordinators only trigger the reauth flow after several consecutive auth-failed polls — a genuinely wrong password still trips it within a couple of cycles.

## [0.7.0] - 2026-06-06

### Changed
- Enabled the HACS brands validation in CI: the integration's bundled brand icons (`custom_components/tcl_lyon/brand/`) satisfy it directly, so no `home-assistant/brands` entry is needed. This release marks readiness for the HACS default store.

## [0.6.1] - 2026-06-06

### Added
- Inline help text under the data-password field during setup (translated EN + FR), spelling out that it is distinct from the SSO password — the most common cause of a 401 at setup.

### Changed
- The SIRI JSON transport now retries transient failures (timeouts, dropped connections, 5xx/429, garbled JSON) with exponential backoff before giving up, so a single blip in the ~58%-uptime feed no longer flaps every entity to "unavailable". Auth (401) and permanent 4xx still fail fast — the former surfacing reauth immediately. Retry is at the client layer, so both coordinators and config-flow credential validation benefit.

### Fixed
- The SIRI parsers no longer raise when a 200 response carries a malformed body (a bare scalar or string where a list/dict is expected); such junk is tolerated and yields no rows instead of crashing the poll with a recurring traceback.

## [0.6.0] - 2026-06-05

### Added
- Initial scaffold: `custom_components/tcl_lyon/` skeleton, `manifest.json`, `const.py`, minimal `config_flow.py`, `sensor.py` / `binary_sensor.py` placeholders, EN+FR translations, `hacs.json`, `pyproject.toml` with ruff + pytest config, README.
- Discovery and planning docs under `docs/`.
- **v0.2 — API client.** `api.py` `TclLyonClient` (async SIRI Lite client with Basic Auth, typed `TclLyonAuthError`/`TclLyonConnectionError`, 401 detection, GTFS download). Pure parsers split into `siri.py` (estimated-timetables → `Departure`, situation-exchange → `Disruption`; `Expected`/`Aimed` fallback, `0001-01-01` sentinel handling, client-side stop filtering) and `gtfs.py` (`GtfsIndex` over stops + routes only, accent-insensitive search). Offline test suite (`tests/test_siri.py`, `test_gtfs.py`, `test_client.py`) with fixtures.
- **v0.3 — Coordinator + first sensor.** `DeparturesCoordinator` polls estimated-timetables one request per followed line (~45s) and filters calls to the wanted stops client-side, keyed by SIRI LineRef; auth failures surface as `ConfigEntryAuthFailed`, connection failures as `UpdateFailed` so entities degrade to "unavailable" together. `TclDepartureSensor` (CoordinatorEntity) exposes whole minutes until the next non-cancelled passage as state, with a `next_departures` attribute (aimed/expected times, realtime + cancellation flags, minutes-to-go). Wired through `__init__.py` (client + first refresh). One hardcoded stop/line for now — replaced by the config flow in v0.4. Added `tests/test_coordinator.py`.
- `scripts/deploy.ps1` to mirror the integration onto a live HA config share for testing.
- **CI/CD.** GitHub Actions running ruff, pytest (3.13), hassfest, and HACS validation on every PR; tag-driven releases that publish a `tcl_lyon.zip` GitHub Release for HACS to install. Dependabot for actions + pip, a Conventional-Commit PR-title check, and a `RELEASING.md` developer guide.

### Changed
- GTFS decoding now tries UTF-8 first with a Windows-1252 fallback (`GTFS_ENCODINGS`) instead of assuming cp1252 — the current feed is UTF-8.
- Raised the minimum supported platform to Home Assistant 2025.2 / Python 3.13.

## [0.1.0] - 2026-06-04

- Project scaffold.
