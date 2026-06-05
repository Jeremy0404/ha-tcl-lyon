"""Config flow for TCL Lyon.

Steps:

    user          — credentials (email + data password); validates by pinging
                    situation-exchange.json, then downloads + parses the GTFS index
                    so the next steps can search it offline.
    stop / line   — free-text search over the GTFS stops then routes.
    pick_stop     — choose one parent station from the matches.
    pick_line     — multi-select the lines to follow there, with an "add another
                    stop" loop.

The flow resolves each picked station to its SIRI quay ids and each route to a full
SIRI LineRef up front, so async_setup_entry never needs the GTFS index again.

A reauth flow handles the ConfigEntryAuthFailed the coordinator raises on HTTP 401
(the classic "data password not set" trap) — only the password is re-asked.
"""

from __future__ import annotations

import logging
from typing import Any

import homeassistant.helpers.config_validation as cv
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import (
    GtfsError,
    GtfsIndex,
    Route,
    Stop,
    TclLyonAuthError,
    TclLyonClient,
    TclLyonConnectionError,
    TclLyonError,
    build_line_ref,
)
from .const import (
    CONF_ADD_ANOTHER,
    CONF_LINE_ID,
    CONF_LINE_NAME,
    CONF_LINE_REF,
    CONF_LINES,
    CONF_QUAY_IDS,
    CONF_QUERY,
    CONF_STOP_ID,
    CONF_STOP_NAME,
    CONF_STOPS,
    DOMAIN,
    FORGOT_PASSWORD_URL,
)

_LOGGER = logging.getLogger(__name__)


class TclLyonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TCL Lyon."""

    VERSION = 1

    def __init__(self) -> None:
        self._username: str = ""
        self._password: str = ""
        self._index: GtfsIndex | None = None
        self._stops: list[dict[str, Any]] = []
        self._current_stop: dict[str, Any] = {}
        self._stop_matches: list[Stop] = []
        self._line_matches: list[Route] = []
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Collect and validate credentials, then load the GTFS index."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._username = user_input[CONF_USERNAME]
            self._password = user_input[CONF_PASSWORD]
            client = self._make_client()
            errors = await self._async_validate(client)
            if not errors:
                await self.async_set_unique_id(self._username)
                self._abort_if_unique_id_configured()
                errors = await self._async_load_index(client)
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
        """Search lines by name (can't be pre-filtered to the stop — see CLAUDE.md)."""
        assert self._index is not None
        errors: dict[str, str] = {}
        if user_input is not None:
            self._line_matches = self._index.search_routes(user_input[CONF_QUERY])
            if self._line_matches:
                return await self.async_step_pick_line()
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
        """Multi-select lines to follow at the chosen stop, then loop or finish."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if user_input[CONF_LINES]:
                self._store_current_stop(user_input[CONF_LINES])
                if user_input[CONF_ADD_ANOTHER]:
                    return await self.async_step_stop()
                return self._create_entry()
            errors["base"] = "no_lines"
        options = {route.route_id: _route_label(route) for route in self._line_matches}
        return self.async_show_form(
            step_id="pick_line",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_LINES): cv.multi_select(options),
                    vol.Required(CONF_ADD_ANOTHER, default=False): bool,
                }
            ),
            errors=errors,
            description_placeholders={"stop_name": self._current_stop[CONF_STOP_NAME]},
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

    # --- helpers ------------------------------------------------------------

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
        """Download and parse the GTFS index off the event loop."""
        try:
            data = await client.async_download_gtfs_bytes()
            self._index = await self.hass.async_add_executor_job(GtfsIndex.from_bytes, data)
        except (TclLyonError, GtfsError):
            return {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("Unexpected error loading the GTFS index")
            return {"base": "unknown"}
        return {}

    def _store_current_stop(self, route_ids: list[str]) -> None:
        """Attach the picked lines to the current stop, merging if it already exists."""
        by_id = {route.route_id: route for route in self._line_matches}
        lines = [
            {
                CONF_LINE_REF: build_line_ref(route_id),
                CONF_LINE_ID: route_id,
                CONF_LINE_NAME: by_id[route_id].short_name or route_id,
            }
            for route_id in route_ids
        ]
        existing = next(
            (s for s in self._stops if s[CONF_STOP_ID] == self._current_stop[CONF_STOP_ID]),
            None,
        )
        if existing is None:
            self._current_stop[CONF_LINES] = lines
            self._stops.append(self._current_stop)
        else:
            seen = {line[CONF_LINE_REF] for line in existing[CONF_LINES]}
            existing[CONF_LINES].extend(line for line in lines if line[CONF_LINE_REF] not in seen)

    def _create_entry(self) -> ConfigFlowResult:
        return self.async_create_entry(
            title=f"TCL Lyon ({self._username})",
            data={
                CONF_USERNAME: self._username,
                CONF_PASSWORD: self._password,
                CONF_STOPS: self._stops,
            },
        )


def _route_label(route: Route) -> str:
    name = route.short_name or route.route_id
    return f"{name} — {route.long_name}" if route.long_name else name
