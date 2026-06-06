# Prebuilt data

`stop_lines.json.gz` is the prebuilt GTFS index: the stop/route search data plus
the stop→lines serving map that lets the config flow filter the line picker to
lines that actually serve the chosen stop.

It is **generated**, not hand-edited. Regenerate (and commit) it with:

```bash
python scripts/build_index.py
```

If the file is absent, the integration still works: setup falls back to a live
GTFS download (stops + routes only, no pre-filtering), and each running instance
rebuilds the full index from the live feed within a week. Shipping the file just
makes the very first setup instant and correctly filtered.
