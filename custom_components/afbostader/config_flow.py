"""Config flow for AF Bostäder."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.util import dt as dt_util

from .api import AfbAuthError, AfbClient, AfbError
from .const import DOMAIN, TIMEZONE

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


class AfbConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for AF Bostäder."""

    VERSION = 1

    async def _validate(self, username: str, password: str) -> str | None:
        """Try to log in. Returns an error key or None."""
        session = async_create_clientsession(self.hass, cookie_jar=aiohttp.DummyCookieJar())
        client = AfbClient(session, username, password, dt_util.get_time_zone(TIMEZONE))
        try:
            await client.login()
        except AfbAuthError:
            return "invalid_auth"
        except (AfbError, aiohttp.ClientError, TimeoutError):
            _LOGGER.exception("Error connecting to AF Bostäder")
            return "cannot_connect"
        finally:
            await session.close()
        return None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            username = user_input[CONF_USERNAME].strip().lower()
            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()
            if error := await self._validate(username, user_input[CONF_PASSWORD]):
                errors["base"] = error
            else:
                return self.async_create_entry(
                    title=username,
                    data={CONF_USERNAME: username, CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(step_id="user", data_schema=USER_SCHEMA, errors=errors)

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            username = entry.data[CONF_USERNAME]
            if error := await self._validate(username, user_input[CONF_PASSWORD]):
                errors["base"] = error
            else:
                return self.async_update_reload_and_abort(
                    entry, data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )
