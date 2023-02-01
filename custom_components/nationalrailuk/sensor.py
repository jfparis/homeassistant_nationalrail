"""Platform for sensor integration."""
from __future__ import annotations

from datetime import timedelta
import logging

import async_timeout

from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .client import NationalRailClient
from .const import CONF_DESTINATIONS, CONF_STATION, CONF_TOKEN, DOMAIN, REFRESH

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(hass, entry, async_add_entities):
    """Config entry example."""

    token = entry.data.get(CONF_TOKEN)
    station = entry.data.get(CONF_STATION)
    destinations = entry.data.get(CONF_DESTINATIONS)

    _LOGGER.debug(f"Setting up sensor for {station} to {destinations}")

    coordinator = NationalRailScheduleCoordinator(hass, token, station, destinations)

    await coordinator.async_config_entry_first_refresh()

    async_add_entities([NationalRailSchedule(coordinator)])


class NationalRailScheduleCoordinator(DataUpdateCoordinator):

    def __init__(self, hass, token, station, destinations):
        """Initialize my coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            # Name of the data. For logging purposes.
            name=DOMAIN,
            # Polling interval. Will only be polled if there are subscribers.
            update_interval=timedelta(minutes=REFRESH),
        )
        destinations = destinations.split(",")
        self.my_api = NationalRailClient(token, station, destinations)

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        # try:
        # Note: asyncio.TimeoutError and aiohttp.ClientError are already
        # handled by the data update coordinator.
        async with async_timeout.timeout(30):
            return await self.my_api.async_get_data()
        # except aiohttp.ClientError as err:
        #    raise UpdateFailed(f"Error communicating with API: {err}") from err


class NationalRailSchedule(CoordinatorEntity):
    """An entity using CoordinatorEntity.

    The CoordinatorEntity class provides:
      should_poll
      async_update
      async_added_to_hass
      available

    """

    attribution = "This uses National Rail Darwin Data Feeds"

    def __init__(self, coordinator):
        """Pass coordinator to CoordinatorEntity."""
        super().__init__(coordinator)
        self.entity_id = (
            f"sensor.{coordinator.data['name'].lower()}"
        )
        # self.friendly_name = f"{self.coordinator.data[self.idx]['friendly_name']}"

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self.coordinator.data

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return f"{self.coordinator.data['name']}"

    @property
    def state(self):
        """Return the state of the sensor."""
        _LOGGER.debug("updating state state of %s ", self.coordinator.data['name'])
        # _LOGGER.debug(self.coordinator.data[self.idx])
        return self.coordinator.data["next_train"]