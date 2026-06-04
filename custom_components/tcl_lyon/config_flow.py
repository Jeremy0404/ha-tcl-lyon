"""Config flow for TCL Lyon.

Scaffolded — full flow arrives in v0.4.

Planned steps:

    1. user        — credentials (email + data password)
                     validates by pinging situation-exchange.json
                     401 → form error with link to mot-de-passe-oublie
    2. add_stop    — search "Bellecour", pick parent stop from GTFS index
    3. pick_lines  — multi-select lines serving that stop
    4. another?    — loop back to add_stop or finish

Options flow will let the user re-edit stops/lines without redoing creds.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

from .const import DOMAIN


class TclLyonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for TCL Lyon."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial step.

        Stub — accepts creds and creates an empty entry. v0.4 will add
        validation and the stop/line picker steps.
        """
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"TCL Lyon ({user_input[CONF_USERNAME]})",
                data=user_input,
            )

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)
