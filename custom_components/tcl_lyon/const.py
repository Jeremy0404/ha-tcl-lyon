"""Constants for the TCL Lyon integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "tcl_lyon"

PLATFORMS: Final = ["sensor", "binary_sensor"]

SIRI_BASE_URL: Final = "https://data.grandlyon.com/siri-lite/2.0"
SIRI_ESTIMATED_TIMETABLES_URL: Final = f"{SIRI_BASE_URL}/estimated-timetables.json"
SIRI_SITUATION_EXCHANGE_URL: Final = f"{SIRI_BASE_URL}/situation-exchange.json"
SIRI_VEHICLE_MONITORING_URL: Final = f"{SIRI_BASE_URL}/vehicle-monitoring.json"

GTFS_DOWNLOAD_URL: Final = (
    "https://download.data.grandlyon.com/files/rdata/tcl_sytral.tcltheorique/GTFS_TCL.ZIP"
)
GTFS_ENCODING: Final = "cp1252"

DEFAULT_DEPARTURES_INTERVAL: Final = timedelta(seconds=45)
DEFAULT_DISRUPTIONS_INTERVAL: Final = timedelta(minutes=5)
GTFS_REFRESH_INTERVAL: Final = timedelta(days=7)

CONF_STOPS: Final = "stops"
CONF_LINES: Final = "lines"
CONF_STOP_ID: Final = "stop_id"
CONF_LINE_ID: Final = "line_id"
CONF_DIRECTION: Final = "direction"

ATTR_NEXT_DEPARTURES: Final = "next_departures"
ATTR_LINE_REF: Final = "line_ref"
ATTR_DIRECTION: Final = "direction"
ATTR_DESTINATION: Final = "destination"
ATTR_AIMED_TIME: Final = "aimed_time"
ATTR_EXPECTED_TIME: Final = "expected_time"
ATTR_IS_REALTIME: Final = "is_realtime"
ATTR_DISRUPTIONS: Final = "disruptions"
ATTR_VALIDITY_PERIOD: Final = "validity_period"

SIRI_NAMESPACE_PREFIX: Final = "ActIV:"
SIRI_NAMESPACE_SUFFIXES: Final = (":SYTRAL", ":LOC")

UNRECORDED_TIME_SENTINEL: Final = "0001-01-01T00:00:00Z"
