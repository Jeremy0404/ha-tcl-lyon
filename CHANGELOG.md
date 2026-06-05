# Changelog

All notable changes to this project will be documented in this file.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Initial scaffold: `custom_components/tcl_lyon/` skeleton, `manifest.json`, `const.py`, minimal `config_flow.py`, `sensor.py` / `binary_sensor.py` placeholders, EN+FR translations, `hacs.json`, `pyproject.toml` with ruff + pytest config, README.
- Discovery and planning docs under `docs/`.
- **v0.2 — API client.** `api.py` `TclLyonClient` (async SIRI Lite client with Basic Auth, typed `TclLyonAuthError`/`TclLyonConnectionError`, 401 detection, GTFS download). Pure parsers split into `siri.py` (estimated-timetables → `Departure`, situation-exchange → `Disruption`; `Expected`/`Aimed` fallback, `0001-01-01` sentinel handling, client-side stop filtering) and `gtfs.py` (`GtfsIndex` over stops + routes only, accent-insensitive search). Offline test suite (`tests/test_siri.py`, `test_gtfs.py`, `test_client.py`) with fixtures.
- `scripts/deploy.ps1` to mirror the integration onto a live HA config share for testing.

### Changed
- GTFS decoding now tries UTF-8 first with a Windows-1252 fallback (`GTFS_ENCODINGS`) instead of assuming cp1252 — the current feed is UTF-8.

## [0.1.0] - 2026-06-04

- Project scaffold.
