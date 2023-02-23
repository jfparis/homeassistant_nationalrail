"""Platform for sensor integration."""
from __future__ import annotations

from datetime import datetime, timedelta
import logging
import time

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

    description: str = None
    friendly_name: str = None
    sensor_name: str = None

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
        self.station = station
        self.destinations = destinations
        self.my_api = NationalRailClient(token, station, destinations)

        self.last_data_refresh = None

    async def _async_update_data(self):
        """Fetch data from API endpoint.

        This is the place to pre-process the data to lookup tables
        so entities can quickly look up their data.
        """
        # chek whether we should refresh the data of not
        if (
            self.last_data_refresh is None
            or (
                self.last_data_refresh is not None
                and (time.time() - self.last_data_refresh) > 14.5 * 60
            )
            or (
                self.data["next_train_scheduled"] is not None
                and datetime.now(self.data["next_train_scheduled"].tzinfo)
                >= self.data["next_train_scheduled"] - timedelta(minutes=1)
                and not self.data["next_train_expected"] == "Cancelled"
            )
        ):
            # try:
            # Note: asyncio.TimeoutError and aiohttp.ClientError are already
            # handled by the data update coordinator.
            async with async_timeout.timeout(30):
                data = await self.my_api.async_get_data()
                self.last_data_refresh = time.time()
            # except aiohttp.ClientError as err:
            #    raise UpdateFailed(f"Error communicating with API: {err}") from err

            if self.sensor_name is None:
                self.sensor_name = f"train_schedule_{self.station}{'_' + '_'.join(self.destinations) if len(self.destinations) >0 else ''}"

            if self.description is None:
                self.description = (
                    f"Departing trains schedule at {data['station']} station"
                )

            if self.friendly_name is None:
                self.friendly_name = f"Train schedule at {data['station']} station"
                if len(self.destinations) == 1:
                    self.friendly_name += f" for {self.destinations[0]}"
                elif len(self.destinations) > 1:
                    self.friendly_name += f" for {'&'.join(self.destinations)}"

            data["name"] = self.sensor_name
            data["description"] = self.description
            data["friendly_name"] = self.friendly_name

            data["next_train_scheduled"] = None
            data["next_train_expected"] = None
            data["arrival_time"] = None
            data["terminus"] = None
            data["platform"] = None
            data["perturbations"] = False

            for each in data["trains"]:
                if data["next_train_scheduled"] is None and not (
                    (
                        isinstance(each["expected"], str)
                        and each["expected"] == "Cancelled"
                    )
                    or (
                        isinstance(each["time_at_destination"], str)
                        and each["time_at_destination"] == "Cancelled"
                    )
                ):

                    data["next_train_scheduled"] = each["scheduled"]
                    data["next_train_expected"] = each["expected"]
                    data["arrival_time"] = each["time_at_destination"]
                    data["terminus"] = each["terminus"]
                    data["platform"] = each["platform"]

                data["perturbations"] = data["perturbations"] or each["perturbation"]

        else:
            data = self.data

        return data


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
        self.entity_id = f"sensor.{coordinator.data['name'].lower()}"

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return self.coordinator.data

    @property
    def unique_id(self) -> str | None:
        """Return a unique ID."""
        return self.coordinator.data["name"]

    @property
    def state(self):
        """Return the state of the sensor."""
        return self.coordinator.data["next_train_expected"]
