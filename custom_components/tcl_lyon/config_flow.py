"""Config + options flow for TCL Lyon.

Initial setup (ConfigFlow):

    user          — credentials (email + data password); validates by pinging
                    situation-exchange.json, then downloads + parses the GTFS index
                    so the next steps can search it offline.
    stop / line   — free-text search over the GTFS stops then routes.
    pick_stop     — choose one parent station from the matches.
    pick_line     — multi-select the lines to follow there.
    direction     — pick which direction(s) of each line to follow, labelled by the
                    terminus seen in a one-off live poll, with an "add another stop"
                    loop. Falls back to "all directions" when the feed is down.

The stop/line/direction steps live in `_TargetSelectionFlow` and are shared with the
options flow, which lets the user add or remove targets later without re-entering
credentials. The flow resolves each station to its SIRI quay ids and each route to a
full SIRI LineRef up front, so async_setup_entry never needs the GTFS index again.

A reauth flow handles the ConfigEntryAuthFailed the coordinator raises on HTTP 401.
"""

from __future__ import annotations

import logging
from copy import deepcopy
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    GtfsIndex,
    Route,
    Stop,
    TclLyonAuthError,
    TclLyonClient,
    TclLyonConnectionError,
    TclLyonError,
    build_line_ref,
    parse_departures,
)
from .const import (
    CONF_ADD_ANOTHER,
    CONF_DIRECTION,
    CONF_DIRECTION_NAME,
    CONF_DIRECTIONS,
    CONF_LINE_ID,
    CONF_LINE_NAME,
    CONF_LINE_REF,
    CONF_LINES,
    CONF_QUAY_IDS,
    CONF_QUERY,
    CONF_REMOVE,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
    FORGOT_PASSWORD_URL,
)
from .store import async_get_index

_LOGGER = logging.getLogger(__name__)


class _TargetSelectionFlow:
    """Shared stop → line → direction picker steps for the config and options flows.

    A subclass must set ``_index`` / ``_client`` / ``_username`` / ``_password`` and a
    starting ``_stops`` list before entering ``async_step_stop``, and implement
    ``_async_finish`` (config flow creates the entry; options flow writes options).
    """

    # Provided by the ConfigFlow / OptionsFlow base class.
    hass: Any

    _index: GtfsIndex | None
    _client: TclLyonClient | None
    _username: str
    _password: str
    _stops: list[dict[str, Any]]
    _current_stop: dict[str, Any]
    _stop_matches: list[Stop]
    _line_matches: list[Route]
    _direction_choices: dict[str, dict[str, Any]]

    def _init_target_state(self) -> None:
        self._index = None
        self._client = None
        self._stops = []
        self._current_stop = {}
        self._stop_matches = []
        self._line_matches = []
        self._direction_choices = {}

    async def async_step_stop(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Search stops by name."""
        assert self._index is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            self._stop_matches = self._index.search_stops(user_input[CONF_QUERY])
            if self._stop_matches:
                return await self.async_step_pick_stop()
            errors["base"] = "no_results"
        return self.async_show_form(
            step_id="stop",
            data_schema=vol.Schema({vol.Required(CONF_QUERY): str}),
            errors=errors,
        )

    async def async_step_pick_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose one station from the search matches and resolve its quays."""
        assert self._index is not None
        matches = {stop.stop_id: stop for stop in self._stop_matches}
        if user_input is not None:
            stop = matches[user_input[CONF_STOP_ID]]
            self._current_stop = {
                CONF_STOP_ID: stop.stop_id,
                CONF_STOP_NAME: stop.name,
                CONF_QUAY_IDS: self._index.quay_ids(stop.stop_id),
            }
            return await self.async_step_line()
        options = {stop.stop_id: f"{stop.name} ({stop.stop_id})" for stop in self._stop_matches}
        return self.async_show_form(
            step_id="pick_stop",
            data_schema=vol.Schema({vol.Required(CONF_STOP_ID): vol.In(options)}),
        )

    async def async_step_line(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Search lines, restricted to those that serve the chosen stop.

        The serving map comes from the cached/shipped GTFS index; when it has no
        entry for the stop (cheap fallback build, or a data gap) the filter is
        dropped and every match is shown rather than dead-ending the user.
        """
        assert self._index is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            serving = self._index.routes_serving(self._current_stop[CONF_STOP_ID]) or None
            query = user_input[CONF_QUERY]
            self._line_matches = self._index.search_routes(query, serving=serving)
            if self._line_matches:
                return await self.async_step_pick_line()
            # Distinguish "no such line" from "exists, but not at this stop".
            if serving is not None and self._index.search_routes(query):
                errors["base"] = "line_not_at_stop"
            else:
                errors["base"] = "no_results"
        return self.async_show_form(
            step_id="line",
            data_schema=vol.Schema({vol.Required(CONF_QUERY): str}),
            errors=errors,
            description_placeholders={"stop_name": self._current_stop[CONF_STOP_NAME]},
        )

    async def async_step_pick_line(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Multi-select lines, then discover their directions for the next step."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_LINES]:
                by_id = {route.route_id: route for route in self._line_matches}
                lines = [
                    {
                        CONF_LINE_REF: build_line_ref(route_id),
                        CONF_LINE_ID: route_id,
                        CONF_LINE_NAME: by_id[route_id].short_name or route_id,
                    }
                    for route_id in user_input[CONF_LINES]
                ]
                self._direction_choices = await self._build_direction_choices(lines)
                return await self.async_step_direction()
            errors["base"] = "no_lines"
        options = {route.route_id: _route_label(route) for route in self._line_matches}
        return self.async_show_form(
            step_id="pick_line",
            data_schema=vol.Schema({vol.Required(CONF_LINES): cv.multi_select(options)}),
            errors=errors,
            description_placeholders={"stop_name": self._current_stop[CONF_STOP_NAME]},
        )

    async def async_step_direction(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick the direction(s) to follow, then loop for another stop or finish."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_DIRECTIONS]:
                targets = [self._direction_choices[key] for key in user_input[CONF_DIRECTIONS]]
                self._store_current_stop(targets)
                if user_input[CONF_ADD_ANOTHER]:
                    return await self.async_step_stop()
                return await self._async_finish()
            errors["base"] = "no_directions"
        options = {key: _direction_label(target) for key, target in self._direction_choices.items()}
        return self.async_show_form(
            step_id="direction",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_DIRECTIONS): cv.multi_select(options),
                    vol.Required(CONF_ADD_ANOTHER, default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={"stop_name": self._current_stop[CONF_STOP_NAME]},
        )

    # --- shared helpers -----------------------------------------------------

    def _make_client(self) -> TclLyonClient:
        return TclLyonClient(async_get_clientsession(self.hass), self._username, self._password)

    async def _async_validate(self, client: TclLyonClient) -> dict[str, str]:
        """Probe credentials; return a form-error dict (empty on success)."""
        try:
            await client.async_validate_credentials()
        except TclLyonAuthError:
            return {"base": "invalid_auth"}
        except TclLyonConnectionError:
            return {"base": "cannot_connect"}
        except Exception:  # surface as a generic form error, never crash the flow
            _LOGGER.exception("Unexpected error validating TCL Lyon credentials")
            return {"base": "unknown"}
        return {}

    async def _async_load_index(self, client: TclLyonClient) -> dict[str, str]:
        """Load the GTFS search index: cached → shipped → cheap download."""
        try:
            self._index = await async_get_index(self.hass, client)
        except Exception:
            _LOGGER.exception("Unexpected error loading the GTFS index")
            return {"base": "unknown"}
        return {} if self._index is not None else {"base": "cannot_connect"}

    async def _build_direction_choices(
        self, lines: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """Map a picker key → target dict for every (line, direction) the user can pick."""
        choices: dict[str, dict[str, Any]] = {}
        for line in lines:
            for direction, name in await self._discover_directions(line):
                target = {
                    **line,
                    CONF_DIRECTION: direction,
                    CONF_DIRECTION_NAME: name,
                }
                key = f"{line[CONF_LINE_ID]}|{direction or 'all'}"
                choices[key] = target
        return choices

    async def _discover_directions(
        self, line: dict[str, Any]
    ) -> list[tuple[str | None, str | None]]:
        """Directions of ``line`` seen at the current stop, as (DirectionRef, terminus).

        One live poll, filtered to the stop's quays. Each distinct DirectionRef is
        labelled by the terminus name(s) seen for it. Always appends an "all
        directions" choice (None), which is also the sole option when the feed is
        down or the line isn't running right now — so setup never dead-ends.
        """
        assert self._index is not None and self._client is not None
        destinations: dict[str, set[str]] = {}
        try:
            payload = await self._client.async_fetch_estimated_timetables(line[CONF_LINE_REF])
            departures = parse_departures(payload, stop_ids=self._current_stop[CONF_QUAY_IDS])
        except TclLyonError:
            departures = []
        for departure in departures:
            if departure.direction is None:
                continue
            seen = destinations.setdefault(departure.direction, set())
            if departure.destination_id:
                seen.add(departure.destination_id)

        result: list[tuple[str | None, str | None]] = []
        for direction in sorted(destinations):
            names = sorted(self._dest_name(dest_id) for dest_id in destinations[direction])
            result.append((direction, " / ".join(names) if names else direction))
        result.append((None, None))  # the always-available combined option
        return result

    def _dest_name(self, stop_id: str) -> str:
        assert self._index is not None
        stop = self._index.stops.get(stop_id)
        return stop.name if stop else stop_id

    def _store_current_stop(self, targets: list[dict[str, Any]]) -> None:
        """Attach the picked targets to the current stop, merging if it already exists."""

        def ident(target: dict[str, Any]) -> tuple[str, str | None]:
            return (target[CONF_LINE_REF], target.get(CONF_DIRECTION))

        existing = next(
            (s for s in self._stops if s[CONF_STOP_ID] == self._current_stop[CONF_STOP_ID]),
            None,
        )
        if existing is None:
            self._current_stop[CONF_LINES] = list(targets)
            self._stops.append(self._current_stop)
        else:
            seen = {ident(t) for t in existing[CONF_LINES]}
            existing[CONF_LINES].extend(t for t in targets if ident(t) not in seen)

    async def _async_finish(self) -> ConfigFlowResult:
        raise NotImplementedError


class TclLyonConfigFlow(_TargetSelectionFlow, ConfigFlow, domain=DOMAIN):
    """Handle initial setup for TCL Lyon."""

    VERSION = 1

    def __init__(self) -> None:
        self._username = ""
        self._password = ""
        self._reauth_entry: ConfigEntry | None = None
        self._init_target_state()

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> TclLyonOptionsFlow:
        return TclLyonOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect and validate credentials, then load the GTFS index."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            self._client = self._make_client()
            errors = await self._async_validate(self._client)
            if not errors:
                await self.async_set_unique_id(self._username)
                self._abort_if_unique_id_configured()
                errors = await self._async_load_index(self._client)
                if not errors:
                    return await self.async_step_stop()

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME, default=self._username or vol.UNDEFINED): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
            description_placeholders={"forgot_password_url": FORGOT_PASSWORD_URL},
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauth: only the data password is re-asked."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        self._username = entry_data[CONF_USERNAME]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Validate the new password and reload the entry."""
        assert self._reauth_entry is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            self._password = user_input[CONF_PASSWORD]
            errors = await self._async_validate(self._make_client())
            if not errors:
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data={**self._reauth_entry.data, CONF_PASSWORD: self._password},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            errors=errors,
            description_placeholders={
                "username": self._username,
                "forgot_password_url": FORGOT_PASSWORD_URL,
            },
        )

    async def _async_finish(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=f"TCL Lyon ({self._username})",
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_STOPS: self._stops,
            },
        )


class TclLyonOptionsFlow(_TargetSelectionFlow, OptionsFlow):
    """Add or remove followed targets after setup, without re-entering credentials."""

    def __init__(self) -> None:
        self._username = ""
        self._password = ""
        self._init_target_state()

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # self.config_entry is provided by the options-flow manager.
        self._username = self.config_entry.data[CONF_USERNAME]
        self._password = self.config_entry.data[CONF_PASSWORD]
        return self.async_show_menu(step_id="init", menu_options=["add_stop", "remove_target"])

    async def async_step_add_stop(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Load the GTFS index once, then reuse the shared stop/line/direction steps."""
        if self._index is None:
            self._client = self._make_client()
            if await self._async_load_index(self._client):
                return self.async_abort(reason="cannot_connect")
            self._stops = deepcopy(self._current_targets())
        return await self.async_step_stop()

    async def async_step_remove_target(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Multi-select existing (stop, line, direction) targets to drop."""
        current = self._current_targets()
        options = {
            _target_key(stop, target): _target_label(stop, target)
            for stop in current
            for target in stop[CONF_LINES]
        }
        if not options:
            return self.async_abort(reason="nothing_to_remove")
        if user_input is not None:
            removing = set(user_input[CONF_REMOVE])
            kept_stops: list[dict[str, Any]] = []
            for stop in deepcopy(current):
                kept = [t for t in stop[CONF_LINES] if _target_key(stop, t) not in removing]
                if kept:
                    stop[CONF_LINES] = kept
                    kept_stops.append(stop)
            self._stops = kept_stops
            return await self._async_finish()
        return self.async_show_form(
            step_id="remove_target",
            data_schema=vol.Schema({vol.Required(CONF_REMOVE): cv.multi_select(options)}),
        )

    def _current_targets(self) -> list[dict[str, Any]]:
        entry = self.config_entry
        if CONF_STOPS in entry.options:
            return entry.options[CONF_STOPS]
        return entry.data.get(CONF_STOPS, [])

    async def _async_finish(self) -> ConfigFlowResult:
        # Stored in options; __init__ prefers options over data and an update listener
        # reloads the entry, rebuilding the sensors.
        return self.async_create_entry(title="", data={CONF_STOPS: self._stops})


def _route_label(route: Route) -> str:
    name = route.short_name or route.route_id
    return f"{name} — {route.long_name}" if route.long_name else name


def _direction_label(target: dict[str, Any]) -> str:
    if target.get(CONF_DIRECTION) is None:
        return f"{target[CONF_LINE_NAME]} — all directions"
    return f"{target[CONF_LINE_NAME]} → {target[CONF_DIRECTION_NAME]}"


def _target_label(stop: dict[str, Any], target: dict[str, Any]) -> str:
    if target.get(CONF_DIRECTION) is None:
        return f"{target[CONF_LINE_NAME]} @ {stop[CONF_STOP_NAME]} (all directions)"
    return f"{target[CONF_LINE_NAME]} → {target[CONF_DIRECTION_NAME]} @ {stop[CONF_STOP_NAME]}"


def _target_key(stop: dict[str, Any], target: dict[str, Any]) -> str:
    return f"{stop[CONF_STOP_ID]}|{target[CONF_LINE_ID]}|{target.get(CONF_DIRECTION) or 'all'}"
