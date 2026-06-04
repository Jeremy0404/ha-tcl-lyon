"""Client for the data.grandlyon.com SIRI Lite API and GTFS feed.

Scaffolded — implementation arrives in v0.2.

Planned shape:

    class TclLyonClient:
        def __init__(self, session, username, password): ...
        async def fetch_estimated_timetables(self, line_ref: str) -> dict: ...
        async def fetch_situation_exchange(self) -> dict: ...
        async def download_gtfs(self, dest: Path) -> Path: ...

    class GtfsIndex:
        '''Parses stops.txt + routes.txt into a search-friendly index.

        Loads with encoding='cp1252' (NOT utf-8). Skips stop_times.txt,
        trips.txt, calendar*.txt — we only need stops + routes for the
        config_flow stop/line selectors.
        '''
        @classmethod
        def from_zip(cls, zip_path: Path) -> "GtfsIndex": ...

See docs/02-data-sources.md for the API contracts and SIRI/GTFS mapping.
"""

from __future__ import annotations
