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
# The feed's encoding has been observed as both UTF-8 (current) and Windows-1252
# (POC, 2026-06-04). Decode UTF-8 first, fall back to cp1252, rather than assuming.
GTFS_ENCODINGS: Final = ("utf-8", "cp1252")

DEFAULT_DEPARTURES_INTERVAL: Final = timedelta(seconds=45)
DEFAULT_DISRUPTIONS_INTERVAL: Final = timedelta(minutes=5)
GTFS_REFRESH_INTERVAL: Final = timedelta(days=7)

# Cached GTFS search index (stop/route data + the stop→lines serving map).
# Shipped prebuilt in data/, then refreshed from the live feed into HA storage.
GTFS_INDEX_STORAGE_VERSION: Final = 1
GTFS_INDEX_STORAGE_KEY: Final = f"{DOMAIN}_gtfs_index"
# Bump when to_dict/from_dict change shape so stale caches are discarded.
GTFS_INDEX_SCHEMA_VERSION: Final = 1

# Most upcoming passages exposed in the sensor's attribute list.
MAX_DEPARTURES: Final = 10

# Help link surfaced in the config flow for the data-password trap (see README).
FORGOT_PASSWORD_URL: Final = "https://data.grandlyon.com/portail/fr/mot-de-passe-oublie"

# Inverse of parse_ref for lines: route_id "T2" -> "ActIV:Line::T2:SYTRAL".
SIRI_LINE_REF_TEMPLATE: Final = "ActIV:Line::{route_id}:SYTRAL"

# Config-flow / options-flow form fields.
CONF_QUERY: Final = "query"
CONF_ADD_ANOTHER: Final = "add_another"
CONF_DIRECTIONS: Final = "directions"
CONF_REMOVE: Final = "remove"

# Config-entry data model: CONF_STOPS is a list of stop dicts, each with a resolved
# quay-id set and a list of followed (line, direction) targets — so setup never needs
# the GTFS index again after the flow has run. The initial config flow writes it to
# entry.data; the options flow rewrites it to entry.options (which then wins).
CONF_STOPS: Final = "stops"
CONF_STOP_ID: Final = "stop_id"
CONF_STOP_NAME: Final = "stop_name"
CONF_QUAY_IDS: Final = "quay_ids"
CONF_LINES: Final = "lines"
CONF_LINE_REF: Final = "line_ref"
CONF_LINE_ID: Final = "line_id"
CONF_LINE_NAME: Final = "line_name"
# direction = SIRI DirectionRef ("inbound"/"outbound"), or None for "all directions".
CONF_DIRECTION: Final = "direction"
CONF_DIRECTION_NAME: Final = "direction_name"

ATTR_NEXT_DEPARTURES: Final = "next_departures"
ATTR_LINE_REF: Final = "line_ref"
ATTR_DIRECTION: Final = "direction"
ATTR_DESTINATION: Final = "destination"
ATTR_AIMED_TIME: Final = "aimed_time"
ATTR_EXPECTED_TIME: Final = "expected_time"
ATTR_IS_REALTIME: Final = "is_realtime"
ATTR_CANCELLED: Final = "cancelled"
ATTR_MINUTES: Final = "minutes"
ATTR_DISRUPTIONS: Final = "disruptions"
ATTR_DISRUPTION_COUNT: Final = "disruption_count"
ATTR_SUMMARY: Final = "summary"
ATTR_VALIDITY_PERIOD: Final = "validity_period"
ATTR_SITUATION_NUMBER: Final = "situation_number"
ATTR_DESCRIPTION: Final = "description"
ATTR_KEYWORDS: Final = "keywords"
ATTR_REPORT_TYPE: Final = "report_type"

SIRI_NAMESPACE_PREFIX: Final = "ActIV:"
SIRI_NAMESPACE_SUFFIXES: Final = (":SYTRAL", ":LOC")

UNRECORDED_TIME_SENTINEL: Final = "0001-01-01T00:00:00Z"
