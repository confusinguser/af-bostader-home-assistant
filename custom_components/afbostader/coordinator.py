"""Data update coordinator for AF Bostäder."""

from __future__ import annotations

import logging

import aiohttp

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import AfbAuthError, AfbClient, AfbError, LaundryData
from .const import DOMAIN, SCAN_INTERVAL, TIMEZONE

_LOGGER = logging.getLogger(__name__)

type AfbConfigEntry = ConfigEntry[AfbCoordinator]


class AfbCoordinator(DataUpdateCoordinator[LaundryData]):
    """Polls AF Bostäder for laundry bookings and availability."""

    config_entry: AfbConfigEntry

    def __init__(self, hass: HomeAssistant, entry: AfbConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=SCAN_INTERVAL,
        )
        # Own session so the login cookies don't leak into HA's shared cookie jar.
        self.client = AfbClient(
            async_create_clientsession(hass, cookie_jar=aiohttp.DummyCookieJar()),
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            dt_util.get_time_zone(TIMEZONE),
        )

    async def _async_update_data(self) -> LaundryData:
        try:
            return await self.client.async_get_data()
        except AfbAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except (AfbError, aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Error communicating with AF Bostäder: {err}") from err
