"""Regenerate the prebuilt GTFS stop→lines index shipped with the integration.

Downloads the live GTFS, runs the full build (streaming stop_times.txt) and writes
custom_components/tcl_lyon/data/stop_lines.json.gz. Commit the result: it lets a
first-ever setup filter the line picker to lines that serve the chosen stop with no
download or scan. Each running instance later refreshes it from the live feed, so
this only needs regenerating now and then (the topology barely changes).

Needs the same GrandLyon credentials as the integration (email + *data* password).
Provide them via env vars or flags:

    GRANDLYON_USER=you@example.com GRANDLYON_PASS=... python scripts/build_index.py
    python scripts/build_index.py --email you@example.com --password ...

A local .env (KEY=VALUE lines) is read automatically if present.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import importlib.util
import json
import os
import sys
import types
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG_DIR = ROOT / "custom_components" / "tcl_lyon"
OUT = PKG_DIR / "data" / "stop_lines.json.gz"
DOWNLOAD_TIMEOUT = 180


def _load_pkg_module(package: str, module: str) -> types.ModuleType:
    """Load one HA-free module from the integration without running its __init__.

    Importing custom_components.tcl_lyon would execute __init__.py and require Home
    Assistant; const.py and gtfs.py only need each other, so load them under a
    synthetic package so this script runs on a plain Python.
    """
    name = f"{package}.{module}"
    spec = importlib.util.spec_from_file_location(name, PKG_DIR / f"{module}.py")
    loaded = importlib.util.module_from_spec(spec)
    sys.modules[name] = loaded
    spec.loader.exec_module(loaded)
    return loaded


_PKG = "_tcl_lyon_index_build"
sys.modules[_PKG] = types.ModuleType(_PKG)
sys.modules[_PKG].__path__ = [str(PKG_DIR)]  # type: ignore[attr-defined]
GTFS_DOWNLOAD_URL = _load_pkg_module(_PKG, "const").GTFS_DOWNLOAD_URL  # const before gtfs
GtfsIndex = _load_pkg_module(_PKG, "gtfs").GtfsIndex


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _credentials() -> tuple[str, str]:
    parser = argparse.ArgumentParser(
        description="Rebuild the prebuilt GTFS stop/lines index shipped in data/."
    )
    parser.add_argument("--email", default=os.environ.get("GRANDLYON_USER"))
    parser.add_argument("--password", default=os.environ.get("GRANDLYON_PASS"))
    args = parser.parse_args()
    if not args.email or not args.password:
        parser.error(
            "credentials required: set GRANDLYON_USER / GRANDLYON_PASS "
            "(or pass --email / --password)"
        )
    return args.email, args.password


def _download(email: str, password: str) -> bytes:
    token = base64.b64encode(f"{email}:{password}".encode()).decode()
    request = urllib.request.Request(GTFS_DOWNLOAD_URL)
    request.add_header("Authorization", f"Basic {token}")
    print(f"downloading {GTFS_DOWNLOAD_URL} ...")
    with urllib.request.urlopen(request, timeout=DOWNLOAD_TIMEOUT) as response:
        return response.read()


def main() -> None:
    _load_dotenv()
    email, password = _credentials()
    data = _download(email, password)
    print(f"downloaded {len(data) / 1e6:.1f} MB; building index (this scans stop_times.txt)...")

    index = GtfsIndex.from_bytes_full(data)
    payload = json.dumps(index.to_dict(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(gzip.compress(payload, mtime=0))  # mtime=0 → reproducible output

    print(
        f"wrote {OUT.relative_to(ROOT)} "
        f"({OUT.stat().st_size / 1e3:.0f} KB): "
        f"{len(index.stops)} stops, {len(index.routes)} routes, "
        f"{len(index.stop_routes)} stops with lines, feed_version={index.feed_version}"
    )


if __name__ == "__main__":
    main()
