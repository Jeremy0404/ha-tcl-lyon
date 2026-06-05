"""Pure parsers for the SIRI Lite payloads used by TCL Lyon.

Deliberately free of Home Assistant and aiohttp imports: everything here is a
pure function over already-decoded JSON, so it can be unit-tested offline against
the snapshots in tests/fixtures/ without a running HA or network access.

See docs/02-data-sources.md for the payload contracts and the SIRI/GTFS mapping.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime

from .const import SIRI_LINE_REF_TEMPLATE, SIRI_NAMESPACE_SUFFIXES, UNRECORDED_TIME_SENTINEL

# Trailing namespace tokens (SYTRAL/LOC) stripped off SIRI refs to get the GTFS id.
_NAMESPACE_TAILS = frozenset(suffix.lstrip(":") for suffix in SIRI_NAMESPACE_SUFFIXES)

# SIRI emits sub-second precision up to 9 digits; datetime accepts at most 6.
_OVERLONG_FRACTION_RE = re.compile(r"(\.\d{6})\d+")

# "0001-01-01" — date part of the "not recorded yet" sentinel, matched leniently.
_SENTINEL_PREFIX = UNRECORDED_TIME_SENTINEL[:10]


def parse_ref(ref: str | None) -> str | None:
    """Extract the GTFS id from a SIRI reference.

    ``ActIV:Line::T2:SYTRAL``        -> ``T2``
    ``ActIV:StopArea:SP:32166:SYTRAL`` -> ``32166``

    The last colon-segment is a cosmetic namespace (SYTRAL/LOC); the id is the
    segment just before it. Shapes we don't recognise are returned unchanged so
    matching stays deterministic rather than silently dropping data.
    """
    if not ref:
        return ref
    segments = ref.split(":")
    if len(segments) >= 2 and segments[-1] in _NAMESPACE_TAILS:
        return segments[-2]
    return ref


def build_line_ref(route_id: str) -> str:
    """Inverse of :func:`parse_ref` for lines: ``T2`` -> ``ActIV:Line::T2:SYTRAL``.

    The config flow stores this full SIRI ref because the server only honours the
    namespaced form on ``?LineRef=`` (see docs/03-poc-findings.md).
    """
    return SIRI_LINE_REF_TEMPLATE.format(route_id=route_id)


def parse_time(value: str | None) -> datetime | None:
    """Parse a SIRI ISO-8601 timestamp into an aware UTC datetime.

    Returns ``None`` for missing values and for the ``0001-01-01T00:00:00Z``
    sentinel SIRI uses to mean "not recorded yet" (it is NOT a real time). Tolerates
    the trailing ``Z`` and the over-long sub-second precision the feed emits. On any
    unparseable value, returns ``None`` rather than raising — the feed is flaky and a
    single bad timestamp must not crash a poll.
    """
    if not value:
        return None
    text = value.strip()
    if text.startswith(_SENTINEL_PREFIX):
        return None
    text = _OVERLONG_FRACTION_RE.sub(r"\1", text)
    if text.endswith(("Z", "z")):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Departure:
    """One upcoming passage of a line at a stop, distilled from an estimated call."""

    line_ref: str | None
    line_id: str | None
    stop_id: str | None
    direction: str | None
    destination_id: str | None
    order: int | None
    aimed: datetime | None
    expected: datetime | None
    cancelled: bool

    @property
    def time(self) -> datetime | None:
        """Best available passage time: realtime estimate if known, else scheduled."""
        return self.expected or self.aimed

    @property
    def is_realtime(self) -> bool:
        """True when a realtime estimate is present (not just the timetable)."""
        return self.expected is not None


def parse_departures(payload: dict, *, stop_ids: Iterable[str] | None = None) -> list[Departure]:
    """Flatten an estimated-timetables payload into Departure rows, soonest first.

    When ``stop_ids`` is given, only calls at those GTFS stops are kept — the server
    ignores ``?MonitoringRef=`` so stop filtering has to happen client-side (see the
    POC findings). Cancelled journeys/calls are still returned, flagged via
    ``Departure.cancelled``, so a caller can surface "cancelled" instead of a silent
    gap. Sorted by best-available time with unknown times last.
    """
    wanted = set(stop_ids) if stop_ids is not None else None
    departures: list[Departure] = []
    for journey in _iter_journeys(payload):
        line_ref = _value(journey.get("LineRef"))
        direction = _value(journey.get("DirectionRef"))
        destination_id = parse_ref(_value(journey.get("DestinationRef")))
        journey_cancelled = bool(journey.get("Cancellation"))
        calls = (journey.get("EstimatedCalls") or {}).get("EstimatedCall") or []
        for call in calls:
            stop_id = parse_ref(_value(call.get("StopPointRef")))
            if wanted is not None and stop_id not in wanted:
                continue
            departures.append(
                Departure(
                    line_ref=line_ref,
                    line_id=parse_ref(line_ref),
                    stop_id=stop_id,
                    direction=direction,
                    destination_id=destination_id,
                    order=_int(call.get("Order")),
                    aimed=parse_time(
                        call.get("AimedArrivalTime") or call.get("AimedDepartureTime")
                    ),
                    expected=parse_time(
                        call.get("ExpectedArrivalTime") or call.get("ExpectedDepartureTime")
                    ),
                    cancelled=journey_cancelled or bool(call.get("Cancellation")),
                )
            )
    departures.sort(key=_departure_sort_key)
    return departures


@dataclass(frozen=True, slots=True)
class Disruption:
    """One service disruption from situation-exchange, with its affected lines."""

    situation_number: str | None
    description: str | None
    report_type: str | None
    keywords: tuple[str, ...]
    affected_line_refs: tuple[str, ...]
    affected_line_ids: tuple[str, ...]
    validity_periods: tuple[tuple[datetime | None, datetime | None], ...]
    creation_time: datetime | None


def parse_situations(payload: dict, *, line_refs: Iterable[str] | None = None) -> list[Disruption]:
    """Flatten a situation-exchange payload into Disruption rows.

    When ``line_refs`` (raw SIRI LineRefs) is given, only situations touching at
    least one of those lines are returned. The French ``Description`` is free text
    with no machine-usable typing, so it is passed through verbatim for display.
    """
    wanted = set(line_refs) if line_refs is not None else None
    disruptions: list[Disruption] = []
    for situation in _iter_situations(payload):
        affected_refs = tuple(_affected_line_refs(situation))
        if wanted is not None and wanted.isdisjoint(affected_refs):
            continue
        disruptions.append(
            Disruption(
                situation_number=_value(situation.get("SituationNumber")),
                description=_first_value(situation.get("Description")),
                report_type=situation.get("ReportType"),
                keywords=tuple(situation.get("Keywords") or ()),
                affected_line_refs=affected_refs,
                affected_line_ids=tuple(parse_ref(ref) for ref in affected_refs),
                validity_periods=tuple(
                    (parse_time(period.get("StartTime")), parse_time(period.get("EndTime")))
                    for period in situation.get("ValidityPeriod") or ()
                ),
                creation_time=parse_time(situation.get("CreationTime")),
            )
        )
    return disruptions


# --- internal helpers -------------------------------------------------------


def _value(node: object) -> str | None:
    """Unwrap SIRI's ``{"value": x}`` scalar wrapper, tolerating bare values."""
    if isinstance(node, dict):
        return node.get("value")
    if isinstance(node, str):
        return node
    return None


def _first_value(node: object) -> str | None:
    """First non-empty value from a SIRI list-of-``{"value": ...}`` (e.g. Description)."""
    if isinstance(node, list):
        for item in node:
            value = _value(item)
            if value:
                return value
        return None
    return _value(node)


def _int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _departure_sort_key(departure: Departure) -> tuple[bool, datetime]:
    when = departure.time
    return (when is None, when or datetime.max.replace(tzinfo=UTC))


def _iter_journeys(payload: dict) -> Iterator[dict]:
    deliveries = _service_delivery(payload).get("EstimatedTimetableDelivery") or []
    for delivery in deliveries:
        for frame in delivery.get("EstimatedJourneyVersionFrame") or []:
            yield from frame.get("EstimatedVehicleJourney") or []


def _iter_situations(payload: dict) -> Iterator[dict]:
    deliveries = _service_delivery(payload).get("SituationExchangeDelivery") or []
    for delivery in deliveries:
        situations = (delivery.get("Situations") or {}).get("PtSituationElement") or []
        yield from situations


def _affected_line_refs(situation: dict) -> Iterator[str]:
    for consequence in (situation.get("Consequences") or {}).get("Consequence") or []:
        networks = (consequence.get("Affects") or {}).get("Networks") or {}
        for network in networks.get("AffectedNetwork") or []:
            for line in network.get("AffectedLine") or []:
                ref = _value(line.get("LineRef"))
                if ref:
                    yield ref


def _service_delivery(payload: dict) -> dict:
    return (payload or {}).get("Siri", {}).get("ServiceDelivery", {}) or {}
