"""Platform for sensor integration with built-in notification support."""

from __future__ import annotations

from datetime import datetime, timedelta, time
import logging
import time as time_module

import async_timeout

from homeassistant.core import HomeAssistant, callback, Context, Event
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
    UpdateFailed
)
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval, async_call_later
from homeassistant.helpers.script import Script
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, STATE_OFF # Add STATE_OFF potentially
from homeassistant.util import dt as dt_util
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.util import dt as dt_util
from .client import NationalRailClient
from .const import (
    CONF_DESTINATIONS,
    CONF_STATION,
    CONF_TOKEN,
    DOMAIN,
    HIGH_FREQUENCY_REFRESH,
    POLLING_INTERVALE,
    REFRESH,
    CONF_MONITOR_TRAIN,
    CONF_TARGET_TIME,
    CONF_TARGET_DESTINATION,
    CONF_TIME_WINDOW_MINUTES,
    CONF_MONITORED_TRAIN_NAME,
    DEFAULT_TIME_WINDOW
)
from .stations import STATION_MAP
from .config_flow import get_global_api_token

_LOGGER = logging.getLogger(__name__)

ERROR_STATES = [
    "Data Error",
    "Source Error",
    "Not Found",
    "No Trains Found",
    STATE_UNKNOWN,
    STATE_UNAVAILABLE,
    "Waiting for Data",
    "error", # Add generic error states just in case
    "Error"
]

async def async_setup_entry(hass, entry, async_add_entities):
    """Config entry example."""
    # Get the API token - either from global entry or this entry
    token = None
    
    _LOGGER.debug("Setting up National Rail UK sensor for station %s", entry.data.get(CONF_STATION))
    
    # Check if token exists in this entry
    if CONF_TOKEN in entry.data:
        token = entry.data.get(CONF_TOKEN)
        _LOGGER.info("Using token from current entry for station %s", entry.data.get(CONF_STATION))
    else:
        # Look for a token-only entry
        token_found = False
        for config_entry in hass.config_entries.async_entries(DOMAIN):
            if (CONF_TOKEN in config_entry.data and 
                CONF_STATION not in config_entry.data):
                token = config_entry.data.get(CONF_TOKEN)
                _LOGGER.info("Found token in token-only entry for station %s", entry.data.get(CONF_STATION))
                token_found = True
                break
        
        # If no token found in entries, check temporary storage
        if not token_found:
            temp_key = f"{DOMAIN}_token_to_save"
            if temp_key in hass.data and hass.data.get(temp_key):
                token = hass.data.get(temp_key)
                _LOGGER.info("Using token from temporary storage for station %s", entry.data.get(CONF_STATION))
                
                # Create a new token-only entry if we have a token
                if token:
                    _LOGGER.info("Creating token-only entry with token from temporary storage")
                    try:
                        result = await hass.config_entries.flow.async_init(
                            DOMAIN,
                            context={"source": "system"},
                            data={CONF_TOKEN: token}
                        )
                        if result and result.get("type") == "create_entry":
                            _LOGGER.info("Successfully created token-only entry")
                            # Clear temporary storage
                            hass.data[temp_key] = None
                        else:
                            _LOGGER.warning("Failed to create token-only entry: %s", result)
                    except Exception as err:
                        _LOGGER.error("Error creating token-only entry: %s", err)
    
    # If still no token, try the helper function as a backup
    if not token:
        token = await get_global_api_token(hass)
        if token:
            _LOGGER.info("Found token using helper function for station %s", entry.data.get(CONF_STATION))
    
    if not token:
        _LOGGER.error("No API token available for station %s", entry.data.get(CONF_STATION))
        return False

    station = entry.data.get(CONF_STATION)
    
    # Handle destinations - may be a string or a list
    destinations = entry.data.get(CONF_DESTINATIONS, "")
    if isinstance(destinations, list):
        # Convert list to string for backward compatibility
        destinations = ",".join(destinations)

    _LOGGER.info("Setting up sensor for %s to %s", station, destinations)

    coordinator = NationalRailScheduleCoordinator(hass, token, station, destinations)
    
    try:
        await coordinator.my_api.setup_client()  # Make sure this is here
        await coordinator.async_config_entry_first_refresh()
        entities = [NationalRailSchedule(coordinator)]
        
        # Check if we should also create a monitored train sensor
        if entry.data.get(CONF_MONITOR_TRAIN, False):
            _LOGGER.info("Setting up monitored train sensor for station %s", station)
            
            # Parse target time string back to time object
            target_time_str = entry.data.get(CONF_TARGET_TIME)
            # Parse the HH:MM:SS format
            time_parts = target_time_str.split(":")
            target_time = time(
                hour=int(time_parts[0]),
                minute=int(time_parts[1]),
                second=int(time_parts[2]) if len(time_parts) > 2 else 0
            )
            
            target_destination = entry.data.get(CONF_TARGET_DESTINATION)
            time_window = entry.data.get(CONF_TIME_WINDOW_MINUTES, DEFAULT_TIME_WINDOW)
            name = entry.data.get(CONF_MONITORED_TRAIN_NAME, 
                                 f"Train to {STATION_MAP.get(target_destination, target_destination)} @ {target_time_str}")
            
            # Create the source sensor entity ID - need to match how it's created in NationalRailScheduleCoordinator
            if destinations:  # Use the destinations already available in this scope
                dest_suffix = '_' + '_'.join(destinations) if isinstance(destinations, list) else f"_{destinations}"
                source_sensor_id = f"sensor.train_schedule_{station}{dest_suffix}"
            else:
                source_sensor_id = f"sensor.train_schedule_{station}"

            _LOGGER.debug("Creating monitored train sensor with source: %s", source_sensor_id)

            
            
            # Create the monitored train sensor
            monitored_train = MonitoredTrainSensor(
                hass,
                name,
                source_sensor_id,
                target_time,
                target_destination,
                timedelta(minutes=time_window)
                
            )
            
            entities.append(monitored_train)
        
        async_add_entities(entities)
        return True
    except Exception as err:
        _LOGGER.exception("Error setting up sensor: %s", err)
        return False



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
        # Parse destinations if needed
        if isinstance(destinations, str):
            destinations = destinations.split(",") if destinations else []
            # Clean up any whitespace
            destinations = [d.strip() for d in destinations if d.strip()]
            
        self.station = station
        self.destinations = destinations
        self.my_api = NationalRailClient(token, station, destinations)

        self.last_data_refresh = None
        
        # Get friendly names from station map
        self.station_name = STATION_MAP.get(station, station)
        self.destination_names = [STATION_MAP.get(d, d) for d in destinations]

    # sensor.py inside the NationalRailScheduleCoordinator class

    async def _async_update_data(self):
        """Fetch data from API endpoint if conditions are met."""
        _LOGGER.debug("Coordinator checking for update for %s", self.station)
        now = time_module.time() # Get current time timestamp
        now_dt = dt_util.now() # Get current timezone-aware datetime

        should_fetch = False
        reason = ""

        # Condition 1: Initial fetch
        if self.last_data_refresh is None:
            should_fetch = True
            reason = "Initial data fetch"
            _LOGGER.debug("Condition met: Initial fetch for %s", self.station)

        # Condition 2: Normal polling interval elapsed
        elif (now - self.last_data_refresh) > POLLING_INTERVALE * 60:
            should_fetch = True
            reason = f"Normal polling interval ({POLLING_INTERVALE} min) elapsed"
            _LOGGER.debug("Condition met: Normal interval elapsed for %s", self.station)

        # Condition 3: High-frequency refresh condition
        # Check only if we have previous data and haven't already decided to fetch
        elif self.data and "next_train_scheduled" in self.data:
            next_sched_str = self.data.get("next_train_scheduled")
            next_expected_str = self.data.get("next_train_expected", "") # Handle missing key

            # Safely parse the scheduled time string
            next_scheduled_dt = None
            if isinstance(next_sched_str, str): # Check if it's a string before parsing
                 try:
                      # Attempt parsing. Assumes the string is compatible with dt_util
                      # If your API returns a different format, adjust parsing here.
                      # Crucially, dt_util.parse_datetime handles ISO formats well.
                      parsed_dt = dt_util.parse_datetime(next_sched_str)
                      if parsed_dt:
                           # Make sure it's timezone-aware for comparison with dt_util.now()
                           next_scheduled_dt = dt_util.as_local(parsed_dt) if dt_util.is_naive(parsed_dt) else parsed_dt
                 except (ValueError, TypeError) as e:
                      _LOGGER.warning("Could not parse next_train_scheduled time '%s': %s", next_sched_str, e)

            if (next_scheduled_dt is not None # Successfully parsed
                and now_dt >= (next_scheduled_dt - timedelta(minutes=HIGH_FREQUENCY_REFRESH)) # Current time is within window
                and isinstance(next_expected_str, str) # Ensure expected is a string for comparison
                and next_expected_str.lower() != "cancelled" # Not cancelled
               ):
                should_fetch = True
                reason = f"High-frequency condition met (train departs within {HIGH_FREQUENCY_REFRESH} min)"
                _LOGGER.debug("Condition met: High-frequency for %s (Next train: %s)", self.station, next_scheduled_dt)
            else:
                 _LOGGER.debug("High-frequency condition not met for %s (Next sched: %s, Next exp: %s, Now: %s)",
                              self.station, next_scheduled_dt, next_expected_str, now_dt)
        else:
            # Case where self.data exists but doesn't have next_train_scheduled yet
             _LOGGER.debug("Skipping high-frequency check for %s: 'next_train_scheduled' not available in previous data.", self.station)


        # --- Perform Fetch OR Return Cached Data ---
        fetched_data = None
        if should_fetch:
            _LOGGER.info("Fetching new data for %s. Reason: %s", self.station, reason)
            try:
                async with async_timeout.timeout(30):
                    fetched_data = await self.my_api.async_get_data()
                    self.last_data_refresh = now # Update timestamp only on successful fetch
                    _LOGGER.debug("Successfully fetched new data for %s", self.station)

            except Exception as err:
                 # Handle fetch errors (similar to your previous code)
                 _LOGGER.error("Error during API data fetch for %s: %s", self.station, err)
                 token = await get_global_api_token(self.hass)
                 if token and token != self.my_api.api_token:
                    _LOGGER.info("Refreshing API token and retrying request for %s", self.station)
                    self.my_api.api_token = token
                    try:
                        async with async_timeout.timeout(30):
                            fetched_data = await self.my_api.async_get_data()
                            self.last_data_refresh = now
                            _LOGGER.debug("Successfully fetched data during retry for %s", self.station)
                    except Exception as err2:
                        _LOGGER.error("Second attempt failed for %s: %s", self.station, err2)
                        raise UpdateFailed(f"Data fetch failed for {self.station} after token refresh") from err2
                 else:
                    raise UpdateFailed(f"Data fetch failed for {self.station}") from err

        else:
            # If no fetch condition met, return the existing data held by the coordinator
            _LOGGER.debug("No fetch condition met for %s. Returning cached data.", self.station)
            if not self.data:
                 # This can happen if the very first check decides not to fetch (unlikely but possible)
                 # Or if a previous fetch failed and self.data is None. Force fetch.
                 _LOGGER.warning("No cached data available for %s, forcing fetch.", self.station)
                 # Re-run the fetch logic, effectively bypassing the 'should_fetch' check this time
                 try:
                      async with async_timeout.timeout(30):
                           fetched_data = await self.my_api.async_get_data()
                           self.last_data_refresh = now
                           _LOGGER.debug("Successfully fetched data during forced update for %s", self.station)
                 except Exception as err:
                      _LOGGER.error("Error during forced API data fetch for %s: %s", self.station, err)
                      # Simplified error handling for this edge case, raise UpdateFailed
                      raise UpdateFailed(f"Forced data fetch failed for {self.station}") from err
            else:
                # Return existing data - NO API CALL
                return self.data


        # --- Process Data (Only if fetched_data is not None) ---
        if fetched_data is None:
             _LOGGER.error("No data available for %s after fetch attempt, cannot process", self.station)
             # If we got here after a failed fetch, UpdateFailed should have been raised.
             # If we got here because self.data was None and the forced fetch failed, raise again.
             if not self.data:
                  raise UpdateFailed(f"Failed to get initial data for {self.station}")
             # Otherwise, maybe return previous data if fetch failed but we had old data?
             # For simplicity, let's treat None fetched_data as an error state here.
             raise UpdateFailed(f"No data returned from fetch for {self.station}")


        # Process the fetched_data (using logic from your previous code)
        # Set sensor_name, description, friendly_name if they haven't been set yet
        if self.sensor_name is None:
            dest_suffix = '_' + '_'.join(self.destinations) if self.destinations else ''
            self.sensor_name = f"train_schedule_{self.station}{dest_suffix}"
        if self.description is None:
            station_name_from_data = fetched_data.get("station", self.station_name)
            self.description = f"Departing trains schedule at {station_name_from_data} station"
        if self.friendly_name is None:
            station_name_from_data = fetched_data.get("station", self.station_name)
            self.friendly_name = f"Train schedule at {station_name_from_data} station"
            if len(self.destinations) == 1:
                self.friendly_name += f" for {self.destination_names[0]}"
            elif len(self.destinations) > 1:
                self.friendly_name += f" for {'&'.join(self.destination_names)}"

        processed_data = {
            "name": self.sensor_name,
            "description": self.description,
            "friendly_name": self.friendly_name,
            "station": fetched_data.get("station", self.station_name),
            "trains": fetched_data.get("trains", []),
            "next_train_scheduled": None, # Will be populated below
            "next_train_expected": None,
            "destinations": None,
            "terminus": None,
            "platform": None,
            "perturbations": False
        }
        if processed_data is None:
            _LOGGER.error("Processed data is None, possible logic error!")
            processed_data = {} 
       

        for each in processed_data["trains"]:
            is_cancelled_at_dest = False 
            if (len(each.get("destinations", [])) > 0 and
                isinstance(each["destinations"][0].get("time_at_destination"), str) and
                each["destinations"][0]["time_at_destination"] == "Cancelled"):
                is_cancelled_at_dest = True

            if (processed_data["next_train_scheduled"] is None and not (
                    (isinstance(each.get("expected"), str) and each["expected"].lower() == "cancelled") or # Use lower()
                    is_cancelled_at_dest
                )):
                 
                processed_data["next_train_scheduled"] = each.get("scheduled")
                processed_data["next_train_expected"] = each.get("expected")
                processed_data["destinations"] = each.get("destinations")
                processed_data["terminus"] = each.get("terminus")
                processed_data["platform"] = each.get("platform")
                 

            processed_data["perturbations"] = processed_data["perturbations"] or each.get("perturbation", False)

        _LOGGER.debug("Finished processing update for %s", self.station)
        _LOGGER.debug("Returning data: %s", processed_data)
        return processed_data

    # Add a method for forced updates when needed
    async def _async_update_data_force(self):
        """Force fetch data from API endpoint regardless of timing."""
        _LOGGER.debug("Forcing data fetch for %s", self.station)
        
        try:
            async with async_timeout.timeout(30):
                # Attempt the API call
                fetched_data = await self.my_api.async_get_data()
                self.last_data_refresh = time_module.time()
                _LOGGER.debug("Successfully fetched data during forced update for %s", self.station)
                data = fetched_data
        except Exception as err:
            _LOGGER.error("Error during forced API data fetch for %s: %s", self.station, err)
            # Try token refresh same as in normal method
            token = await get_global_api_token(self.hass)
            if token and token != self.my_api.api_token:
                _LOGGER.info("Refreshing API token and retrying forced request for %s", self.station)
                self.my_api.api_token = token
                try:
                    async with async_timeout.timeout(30):
                        fetched_data_retry = await self.my_api.async_get_data()
                        self.last_data_refresh = time_module.time()
                        _LOGGER.debug("Successfully fetched data during forced retry for %s", self.station)
                        data = fetched_data_retry
                except Exception as err2:
                    _LOGGER.error("Second attempt failed during forced update for %s: %s", self.station, err2)
                    raise UpdateFailed(f"Forced fetch failed for {self.station} after token refresh") from err2
            else:
                raise UpdateFailed(f"Forced fetch failed for {self.station}") from err
                
        
        self.data = data  # Temporarily set the data so it can be used by _async_update_data
        return await self._async_update_data()  # Let the regular method handle processing


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


class MonitoredTrainSensor(SensorEntity):
    """Representation of the monitored specific train."""

    def __init__(
        self,
        hass: HomeAssistant,
        name: str,
        source_sensor_id: str,
        target_time: time,
        target_destination: str,
        time_window: timedelta
    ):
        """Initialize the sensor."""
        self.hass = hass
        self._source_sensor_id = source_sensor_id
        self._target_time = target_time
        self._target_destination = target_destination
        self._time_window = time_window
        self._seen_train_ids = set()  # Track seen trains
        self._initialized = False # <--- Add this flag
        self._tracked_train_id = None # The ID of the specific train service we are following
        self._tracking_stopped = False # Set to True when the train is Departed/Cancelled/Lost
        self._attr_name = name
        self._attr_unique_id = f"monitored_train_{source_sensor_id.split('.')[-1]}_{target_time.strftime('%H%M%S')}"
        self._attr_icon = "mdi:train"

        # Initialize state and attributes explicitly
        self._attr_native_value = STATE_UNKNOWN
        self._attr_extra_state_attributes = {
            "source_sensor": source_sensor_id,
            "target_destination": target_destination,
            "target_time": target_time.strftime("%H:%M:%S")
        }

        # --- Store previous state details for change detection ---
        self._previous_native_value = STATE_UNKNOWN
        self._previous_attributes = self._attr_extra_state_attributes.copy() # Store a copy!
        self._initialization_done = False # Keep this flag
        
        _LOGGER.info("Initialized MonitoredTrainSensor: %s", self._attr_unique_id)




    def _log_available_entities(self):
        """Log all available train sensor entities for debugging."""
        train_entities = []
        for entity_id in self.hass.states.async_entity_ids():
            if entity_id.startswith("sensor.train_schedule_"):
                state = self.hass.states.get(entity_id)
                train_entities.append({
                    "entity_id": entity_id,
                    "state": state.state if state else "None"
                })
        _LOGGER.debug("Available train entities: %s", train_entities)



    def _calculate_journey_duration(self, train_entry):
        """Calculate and return the journey duration for sorting purposes.
        
        This method takes a matching train entry (which contains train_data and scheduled_dt)
        and calculates the expected journey duration to use for sorting.
        
        Returns:
            int: Journey duration in minutes, or a high number (999) if duration can't be calculated
        """
        train = train_entry.get("train_data", {})
        scheduled_dt = train_entry.get("scheduled_dt")
        
        # Initialize with a high default value (will sort to the end if duration can't be calculated)
        duration_minutes = 999
        
        # Check if arrival info is available
        if "destinations" in train and isinstance(train["destinations"], list):
            for dest in train["destinations"]:
                # Try to get arrival time
                scheduled_arrival_str = dest.get("scheduled_time_at_destination")
                if not scheduled_arrival_str:
                    continue
                    
                # Parse the arrival time string into a datetime
                arrival_dt = self._parse_train_time(scheduled_arrival_str)
                if not arrival_dt or not scheduled_dt:
                    continue
                    
                # Calculate duration in minutes
                duration = arrival_dt - scheduled_dt
                duration_minutes = int(duration.total_seconds() / 60)
                
                # Use this duration and stop looking (we found our target destination)
                break
        
        return duration_minutes        

    async def async_added_to_hass(self) -> None:
        """Register callbacks."""
        _LOGGER.debug("Setting up callbacks for %s", self.entity_id)
        
        # First callback for when source sensor changes state
        @callback
        def state_changed_listener(event):
            """Handle source sensor state changes."""
            _LOGGER.debug("Source sensor %s changed state, scheduling update for %s", 
                         self._source_sensor_id, self.entity_id)
            # Schedule update to run from state change
            self.async_schedule_update_ha_state(True)  # Force update
        # Second callback specifically for when source sensor becomes available or trains data changes
        @callback
        def sensor_available_listener(event):
            """Handle source sensor becoming available or trains data changing."""
            old_state = event.data.get("old_state")
            new_state = event.data.get("new_state")
            
            # Log details for debugging
            _LOGGER.debug("%s: Source sensor transition - old state: %s, new state: %s", 
                         self.entity_id,
                         old_state.state if old_state else "None", 
                         new_state.state if new_state else "None")
            

            # Process the new state if it's valid
            if new_state is not None and new_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                # Check if transitioning from unavailable to available
                if old_state is None or old_state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    _LOGGER.info("Source sensor %s became available, updating %s", 
                                self._source_sensor_id, self.entity_id)
                    self.async_schedule_update_ha_state(True)  # Force update
                
                # Check if trains attribute has changed
                elif "trains" in new_state.attributes:
                    if (old_state is None or 
                        "trains" not in old_state.attributes or 
                        new_state.attributes.get("trains") != old_state.attributes.get("trains")):
                        _LOGGER.info("Source sensor %s trains data changed, updating %s", 
                                    self._source_sensor_id, self.entity_id)
                        self.async_schedule_update_ha_state(True)  # Force update

        # Listen for any state changes in the source sensor
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_sensor_id], state_changed_listener
            )
        )
        
        # Second listener specifically looking for unavailable -> available transitions and data changes
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self._source_sensor_id], sensor_available_listener
            )
        )
        
        @callback
        def periodic_refresh(_):
            """Force a refresh every 5 minutes."""
            _LOGGER.debug("Periodic refresh for %s", self.entity_id)
            self.async_schedule_update_ha_state(True)
        
        # Schedule regular updates every 5 minutes
        self.async_on_remove(
            async_track_time_interval(self.hass, periodic_refresh, timedelta(minutes=5))
        )
        
        # Immediate check for existing data right at startup
        source_state = self.hass.states.get(self._source_sensor_id)
        if source_state and source_state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            if "trains" in source_state.attributes and isinstance(source_state.attributes["trains"], list):
                _LOGGER.info(
                    "Source sensor %s already has train data available at startup (%d trains). Processing immediately.", 
                    self._source_sensor_id, 
                    len(source_state.attributes["trains"])
                )
                # This flag ensures we always process the initial state
                self._initialization_done = True
                # Force immediate update to process the existing data
                await self.async_update()
            else:
                _LOGGER.info(
                    "Source sensor %s is available but does not have valid train data yet.", 
                    self._source_sensor_id
                )
        
        # Backup delayed update in case we miss something
        async def delayed_first_update(_now): # Coroutine needs an argument (current time)
            """Run a delayed first update."""
            if not self._initialization_done:
                _LOGGER.info("Running delayed first update for %s", self.entity_id)
                self._initialization_done = True
                # Call the update method for this entity
                await self.async_update()
                # Manually trigger state write after update if needed (often handled by async_update)
                self.async_write_ha_state()

        # Schedule a single update 1 minute after startup
        self.async_on_remove( # Store the cancel callback for removal
            async_call_later(self.hass, 60, delayed_first_update)
        )
        
        # Call debug logging function to help troubleshoot
        self._log_available_entities()
    

    def _parse_train_time(self, time_input: str | datetime | None) -> datetime | None:
        """Parse ISO time string or datetime into a datetime object."""
        # Handle None or status strings
        if not time_input or (isinstance(time_input, str) and time_input in ["Cancelled", "Delayed", "On time"]):
            return None
            
        # If we already have a datetime object, just return it
        if isinstance(time_input, datetime):
            _LOGGER.debug("Input is already a datetime object: %s", time_input)
            return time_input
            
        # Handle string inputs
        if isinstance(time_input, str):
            try:
                # Attempt to parse standard ISO format
                dt = dt_util.parse_datetime(time_input)
                if dt:
                    # Return timezone-aware datetime directly if parsing worked
                    return dt

                # Add fallback for simple HH:MM or HH:MM:SS if needed
                try:
                    parsed_time = dt_util.parse_time(time_input)  # Might work for "HH:MM:SS"
                    if parsed_time:
                        today = dt_util.now().date()
                        # Combine with today's date and make it timezone-aware using local timezone
                        return dt_util.as_local(datetime.combine(today, parsed_time))
                except (ValueError, TypeError) as e2:
                    _LOGGER.warning("Could not parse train time string: '%s'. Error: %s", time_input, e2)
                    return None
            except (ValueError, TypeError) as e:
                _LOGGER.debug("Could not parse '%s' as standard ISO datetime: %s", time_input, e)
                return None
        
        # If we get here, time_input is an unexpected type
        _LOGGER.warning("Unexpected type for time input: %s (%s)", type(time_input).__name__, time_input)
        return None

    def _is_destination_match(self, train: dict) -> bool:
        """Check if the train goes to the target destination."""
        if not train:
            _LOGGER.debug("Train data is None or empty in _is_destination_match")
            return False
        
        # Add logging to see input values
        _LOGGER.debug("Matching destination %s against train data: %s", 
                     self._target_destination, train)
        
        # 1. Direct matching (existing checks)
        if "terminus" in train and train["terminus"] == self._target_destination:
            return True
            
        if "destinations" in train and isinstance(train["destinations"], list):
            if any(dest.get("name") == self._target_destination for dest in train["destinations"]):
                return True
        
        # 2. Case-insensitive matching
        target_upper = self._target_destination.upper()
        
        if "terminus" in train and isinstance(train["terminus"], str):
            if train["terminus"].upper() == target_upper:
                return True
        
        if "destinations" in train and isinstance(train["destinations"], list):
            if any(dest.get("name", "").upper() == target_upper for dest in train["destinations"]):
                return True
        
        # 3. Station code to name mapping (forward lookup)
        from .stations import STATION_MAP
        target_name = STATION_MAP.get(self._target_destination)
        
        if target_name and "terminus" in train:
            if train["terminus"] == target_name:
                return True
                
        if target_name and "destinations" in train and isinstance(train["destinations"], list):
            if any(dest.get("name") == target_name for dest in train["destinations"]):
                return True
        
        # 4. Station name to code mapping (reverse lookup)
        # Create reverse mapping for name to code lookup
        reverse_map = {name.upper(): code for code, name in STATION_MAP.items()}
        
        # Check for name in terminus or destinations
        if "terminus" in train and isinstance(train["terminus"], str):
            terminus_code = reverse_map.get(train["terminus"].upper())
            if terminus_code and terminus_code == self._target_destination:
                return True
        
        if "destinations" in train and isinstance(train["destinations"], list):
            for dest in train["destinations"]:
                dest_name = dest.get("name", "")
                if dest_name:
                    dest_code = reverse_map.get(dest_name.upper())
                    if dest_code and dest_code == self._target_destination:
                        return True
        
        _LOGGER.debug("No destination match found for '%s' in train data", self._target_destination)
        return False

    def _find_target_train(self, all_trains: list) -> dict | None:
        """Find the train within the time window going to the destination,
           prioritizing the one closest to the target_time.""" # <-- Docstring updated
        if not all_trains:
            _LOGGER.debug("No trains list provided to _find_target_train")
            return None

        _LOGGER.debug("Searching for train to %s closest to %s (+/-%s min window)", # <-- Logging clarified
                      self._target_destination, self._target_time, self._time_window.seconds // 60)

        _LOGGER.debug("Searching through %d trains", len(all_trains))
        for i, train in enumerate(all_trains[:5]): # Log first 5 for slightly more context
            _LOGGER.debug("Train %d: %s to %s", i,
                         train.get("scheduled", "Unknown"),
                         train.get("terminus", "Unknown"))

        best_match_train = None # <-- Initialize variable to hold the best match
        min_time_diff = float('inf') # <-- Initialize minimum difference to infinity

        target_time_total_seconds = self._target_time.hour * 3600 + self._target_time.minute * 60 + self._target_time.second
        window_seconds = self._time_window.total_seconds()
        seconds_in_day = 24 * 3600

        now = dt_util.now()
        _LOGGER.debug("Current time: %s, Target time: %s, Window: %s seconds",
                     now, self._target_time, window_seconds)

        for i, train in enumerate(all_trains):
            scheduled_str = train.get("scheduled")
            _LOGGER.debug("Train %d: Scheduled string: %s", i, scheduled_str)

            scheduled_dt = self._parse_train_time(scheduled_str)
            if not scheduled_dt:
                _LOGGER.debug("Train %d: Skipping, could not parse scheduled time '%s'", i, scheduled_str)
                continue

            _LOGGER.debug("Train %d: Parsed scheduled time: %s", i, scheduled_dt)

            # --- Time Comparison Logic (Focus on Time of Day) ---
            scheduled_time = scheduled_dt.time()
            scheduled_time_total_seconds = scheduled_time.hour * 3600 + scheduled_time.minute * 60 + scheduled_time.second

            # Calculate absolute difference in seconds, handling wrap-around midnight
            time_diff_window_check = abs(scheduled_time_total_seconds - target_time_total_seconds)
            time_diff_window_check = min(time_diff_window_check, seconds_in_day - time_diff_window_check)

            _LOGGER.debug("Train %d: Time difference from target for window check: %s seconds (window: %s seconds)",
                         i, time_diff_window_check, window_seconds)

            if time_diff_window_check <= window_seconds:
                destination_match = self._is_destination_match(train)
                _LOGGER.debug("Train %d: Destination match result: %s", i, destination_match)

                if destination_match:
                    # Calculate the actual time difference for priority sorting (can be negative/positive before abs)
                    # We use the same calculation as window check here since we want absolute difference
                    current_abs_time_diff = time_diff_window_check

                    _LOGGER.debug("Train %d: Match found (Dest: %s, Time Window: Yes). Absolute time diff: %s seconds. Min diff so far: %s seconds.",
                              i, self._target_destination, current_abs_time_diff, min_time_diff)

                    # --- Prioritization Logic ---
                    if current_abs_time_diff < min_time_diff:
                         _LOGGER.debug("Train %d: This train (%s) is closer to target time %s than previous best. Selecting it.",
                                       i, scheduled_str, self._target_time)
                         min_time_diff = current_abs_time_diff
                         best_match_train = train # Update the best match
                    else:
                         _LOGGER.debug("Train %d: This train (%s) is not closer (diff %s) than current best (diff %s). Skipping priority.",
                                       i, scheduled_str, current_abs_time_diff, min_time_diff)

                else:
                    _LOGGER.debug("Train %d: Within time window but destination does not match '%s'. Details: %s",
                              i, self._target_destination,
                              {'terminus': train.get('terminus'), 'destinations': train.get('destinations')})
            else:
                _LOGGER.debug("Train %d: Outside time window (%s seconds vs %s seconds)",
                          i, time_diff_window_check, window_seconds)

        # --- Return the selected train ---
        if best_match_train:
             # Log the specifically chosen train's scheduled time
             chosen_schedule_str = best_match_train.get("scheduled", "Unknown")
             _LOGGER.info("Selected train closest to target time %s: Scheduled %s (Time difference: %s seconds)",
                         self._target_time, chosen_schedule_str, min_time_diff)
             return best_match_train # Return the single best match found
        else:
            _LOGGER.debug("No matching train found within window and for destination %s.", self._target_destination)
            return None

    def _find_next_train(self, all_trains: list, after_time: datetime) -> dict | None:
        """Find the next available (not cancelled) train to the destination after a given time."""
        _LOGGER.debug("Searching for next available train to %s after %s", self._target_destination, after_time)
        next_available = None
        earliest_next_scheduled_dt = None

        for train in all_trains:
            # Skip cancelled trains
            expected_str = train.get("expected", "").lower()
            if expected_str == "cancelled":
                continue

            scheduled_str = train.get("scheduled")
            scheduled_dt = self._parse_train_time(scheduled_str)

            # Skip past/invalid trains or trains scheduled before the 'after_time'
            if not scheduled_dt or scheduled_dt <= after_time:
                continue

            # Check destination using the consistent helper function
            if not self._is_destination_match(train):
                 continue

            # Found a potential candidate
            if next_available is None or scheduled_dt < earliest_next_scheduled_dt:
                 _LOGGER.debug("Found potential next train: %s", scheduled_str)
                 next_available = train
                 earliest_next_scheduled_dt = scheduled_dt

        if next_available:
             _LOGGER.debug("Selected next available train: %s", earliest_next_scheduled_dt)
        else:
             _LOGGER.debug("No subsequent train found to %s after %s", self._target_destination, after_time)

        return next_available
        
    async def async_update(self) -> None:
        """Fetch new state data for the sensor, latching onto the first found train."""
        _LOGGER.debug("Starting async_update for %s. Initialized: %s, Tracking Stopped: %s, Tracked ID: %s",
                     self.entity_id, self._initialized, self._tracking_stopped, self._tracked_train_id)

        # --- Stop processing if tracking has ended ---
        if self._tracking_stopped:
            _LOGGER.debug("%s: Tracking stopped, no further updates.", self.entity_id)
            return

        # --- Store previous state BEFORE this update runs ---
        previous_native_value = self._attr_native_value
        previous_attributes = self._attr_extra_state_attributes.copy() if self._attr_extra_state_attributes else {}

        _LOGGER.debug("Previous state for %s: Value='%s', Attrs=%s",
                     self.entity_id, previous_native_value, previous_attributes)

        # --- Initialize variables for this run ---
        new_native_value = previous_native_value # Default to previous state unless changed
        # Start with base attributes, copy previous potentially relevant ones if needed
        new_attrs = {
            "source_sensor": self._source_sensor_id,
            "target_destination": self._target_destination,
            "target_time": self._target_time.strftime("%H:%M:%S")
        }
        # Reset change flags for this run (these primarily live in attributes now)
        change_flags = {
            "just_cancelled": False,
            "platform_changed": False,
            "time_changed": False,
            "status_changed": False,
            "first_valid_update": False
        }
        current_train_data = None # Holds the data for the train relevant to *this* update cycle

        # --- Get Source Data & Handle Errors ---
        source_state = self.hass.states.get(self._source_sensor_id)
        if not source_state or source_state.state in ERROR_STATES:
             _LOGGER.warning("%s: Source sensor %s unavailable/error (%s). Keeping previous state.",
                           self.entity_id, self._source_sensor_id, source_state.state if source_state else "None")
             # Keep previous state and don't proceed
             return
        all_trains = source_state.attributes.get("trains")
        if not isinstance(all_trains, list):
            _LOGGER.warning("%s: Source sensor %s 'trains' invalid. Keeping previous state.",
                           self.entity_id, self._source_sensor_id)
             # Keep previous state and don't proceed
            return

        # ==============================================================
        # --- Core Logic: Initialization or Tracking ---
        # ==============================================================

        if not self._initialized:
            # --- PHASE 1: Find Initial Target Train ---
            _LOGGER.debug("%s: Not initialized. Searching for initial train near %s +/- %s min.",
                         self.entity_id, self._target_time, self._time_window.seconds // 60)
            initial_train = self._find_target_train(all_trains)

            if initial_train:
                _LOGGER.info("%s: Found initial target train: %s", self.entity_id, initial_train.get("scheduled"))
                current_train_data = initial_train # Use this data for the update
                # --- Extract ID and Latch Onto It ---
                sched_str = initial_train.get("scheduled") # Needed for ID fallback
                terminus = initial_train.get("terminus", "Unknown") # Needed for ID fallback
                self._tracked_train_id = initial_train.get("serviceID") or f"{sched_str}_{terminus}"
                self._seen_train_ids.add(self._tracked_train_id) # Add to seen set

                _LOGGER.info("%s: Latching onto train ID: %s", self.entity_id, self._tracked_train_id)

                # --- Mark as First Valid Update and Fire Found Event ---
                change_flags["first_valid_update"] = True
                # Fire ONLY the found event (event data population happens later)
                # ... event firing moved to end ...

                # --- Mark initialization complete AFTER processing this update ---
                # self._initialized = True # Set later, after state update

            else:
                # Initial search failed
                _LOGGER.debug("%s: Initial search did not find a matching train.", self.entity_id)
                new_native_value = "Not Found"
                new_attrs["status"] = "Not Found"
                new_attrs["info"] = f"No train found matching criteria (Destination: {self._target_destination}, Time: ~{self._target_time})"
                # Do not initialize yet, will retry on next update.
                # Ensure previous train details are cleared
                attributes_to_clear = ["scheduled_departure_iso", "expected_departure_iso", "scheduled_departure_time", "expected_departure_time", "platform", "terminus", "status", "train_id", "calling_points", "delay_minutes", "next_available_train"]
                change_flags_to_clear = ["just_cancelled", "platform_changed", "time_changed", "status_changed", "first_valid_update", "previous_platform", "previous_expected_departure_iso", "previous_expected_departure_time", "previous_status"]
                for attr in attributes_to_clear + change_flags_to_clear:
                    new_attrs.pop(attr, None)

        else:
            # --- PHASE 2: Track the Latched Train ---
            if not self._tracked_train_id:
                 # Should not happen if initialized is True, but handle defensively
                 _LOGGER.error("%s: Initialized but tracked_train_id is None. Resetting.", self.entity_id)
                 self._initialized = False
                 # Keep previous state and try again next time
                 return

            _LOGGER.debug("%s: Initialized. Searching for tracked train ID: %s", self.entity_id, self._tracked_train_id)
            found_tracked_train = None
            for train in all_trains:
                 # --- Calculate current train's ID ---
                 sched_str = train.get("scheduled")
                 terminus = train.get("terminus", "Unknown")
                 current_id = train.get("serviceID") or f"{sched_str}_{terminus}"
                 if current_id == self._tracked_train_id:
                     found_tracked_train = train
                     _LOGGER.debug("%s: Found tracked train in current data.", self.entity_id)
                     break # Found it, stop searching

            if found_tracked_train:
                 # Tracked train still exists in the feed
                 current_train_data = found_tracked_train # Use this data for the update
                 _LOGGER.debug("%s: Processing update for tracked train.", self.entity_id)

            else:
                 # --- Tracked Train Vanished ---
                 _LOGGER.info("%s: Tracked train ID %s not found in current data feed.", self.entity_id, self._tracked_train_id)
                 self._tracking_stopped = True # Stop future updates

                 # Determine final state based on previous state
                 last_status = previous_attributes.get("status")
                 last_expected_iso = previous_attributes.get("expected_departure_iso")
                 last_expected_time_str = previous_attributes.get("expected_departure_time", "?")

                 if last_status == "Cancelled":
                     new_native_value = "Cancelled (Final)"
                     new_attrs["status"] = "Cancelled (Final)"
                     new_attrs["info"] = "Tracked train was cancelled."
                 else:
                     # Check if departure time has passed
                     departed = False
                     last_expected_dt = self._parse_train_time(last_expected_iso)
                     if last_expected_dt:
                         # Consider departed if current time is > expected time + small buffer (e.g., 2 mins)
                         if dt_util.now() > (last_expected_dt + timedelta(minutes=2)):
                             departed = True
                             new_native_value = f"Departed ~{last_expected_time_str}"
                             new_attrs["status"] = "Departed"
                             new_attrs["info"] = "Tracked train has likely departed."
                         else:
                            # Disappeared before expected departure + buffer -> Lost Track
                             new_native_value = "Lost Track"
                             new_attrs["status"] = "Lost Track"
                             new_attrs["info"] = "Tracked train disappeared before expected departure."
                     else:
                        # Couldn't parse last time, assume lost track
                        new_native_value = "Lost Track"
                        new_attrs["status"] = "Lost Track"
                        new_attrs["info"] = "Tracked train disappeared from feed (time unknown)."

                 # Clear specific train details for final states
                 attributes_to_clear = ["scheduled_departure_iso", "expected_departure_iso", "scheduled_departure_time", "expected_departure_time", "platform", "terminus", "train_id", "calling_points", "delay_minutes", "next_available_train"]
                 change_flags_to_clear = ["just_cancelled", "platform_changed", "time_changed", "status_changed", "previous_platform", "previous_expected_departure_iso", "previous_expected_departure_time", "previous_status"]
                 for attr in attributes_to_clear + change_flags_to_clear:
                    new_attrs.pop(attr, None)

        # ==============================================================
        # --- Process Update Data (if current_train_data exists) ---
        # ==============================================================

        event_to_fire = None # Holds {'name': event_name, 'data': event_data}
        train_id_for_event = None # Store the ID for event payload if applicable

        if current_train_data:
            # --- Extract Details & Populate Attributes ---
            # (Same logic as before to get scheduled_str, expected_str, platform, terminus etc.)
            # (Same logic to parse scheduled_dt, expected_dt)
            # (Same logic to determine current_status, current_native_value)
            # (Populate new_attrs with current details: platform, terminus, times, status, train_id etc.)

            scheduled_str = current_train_data.get("scheduled")
            expected_str = current_train_data.get("expected")
            platform = current_train_data.get("platform")
            terminus = current_train_data.get("terminus", "Unknown")
            train_id = current_train_data.get("serviceID") or f"{scheduled_str}_{terminus}"
            train_id_for_event = train_id # Use this ID for events

            scheduled_dt = self._parse_train_time(scheduled_str)
            expected_dt = self._parse_train_time(expected_str) if expected_str and expected_str not in ["Cancelled", "Delayed", "On time"] else None
            if not expected_dt and expected_str == "On time" and scheduled_dt: expected_dt = scheduled_dt # Handle "On time"

            status = "Scheduled"; current_native_value = STATE_UNKNOWN # Defaults
            if isinstance(expected_str, str) and expected_str.lower() == "cancelled": status = "Cancelled"; current_native_value = "Cancelled"
            elif expected_dt and scheduled_dt and expected_dt > scheduled_dt: status = "Delayed"; current_native_value = expected_dt.strftime("%H:%M")
            elif expected_str == "On time" or (expected_dt and scheduled_dt and expected_dt == scheduled_dt): status = "On Time"; current_native_value = scheduled_dt.strftime("%H:%M") if scheduled_dt else "On Time"
            elif expected_dt: status = "Scheduled"; current_native_value = expected_dt.strftime("%H:%M") # Maybe "Updated"?
            elif scheduled_dt: status = "Scheduled"; current_native_value = scheduled_dt.strftime("%H:%M")
            else: status = "Unknown"

            new_native_value = current_native_value # Update the native value

            # Populate attributes
            current_platform = platform if platform else "TBC"
            current_expected_iso = expected_dt.isoformat() if expected_dt else expected_str
            current_status = status

            new_attrs["scheduled_departure_iso"] = scheduled_dt.isoformat() if scheduled_dt else scheduled_str
            new_attrs["expected_departure_iso"] = current_expected_iso
            new_attrs["scheduled_departure_time"] = scheduled_dt.strftime("%H:%M") if scheduled_dt else "?"
            new_attrs["expected_departure_time"] = expected_dt.strftime("%H:%M") if expected_dt else (expected_str if isinstance(expected_str, str) else "?")
            new_attrs["platform"] = current_platform
            new_attrs["terminus"] = terminus
            new_attrs["status"] = current_status
            new_attrs["train_id"] = train_id # Store the ID we are tracking
            if "destinations" in current_train_data: new_attrs["calling_points"] = [dest.get("name") for dest in current_train_data.get("destinations", []) if dest.get("name")]
            if status == "Delayed" and expected_dt and scheduled_dt: new_attrs["delay_minutes"] = int((expected_dt - scheduled_dt).total_seconds() / 60)
            elif "delay_minutes" in previous_attributes and status != "Delayed": new_attrs.pop("delay_minutes", None) # Clear delay if not delayed
            # ... (Add terminus_name lookup if implemented) ...


            # --- Perform Change Detection & Prep Event (if initialized) ---
            if self._initialized:
                 # Compare current_ calculated values with previous_attributes
                 prev_status = previous_attributes.get("status")
                 prev_platform = previous_attributes.get("platform", "TBC")
                 prev_expected_iso = previous_attributes.get("expected_departure_iso")

                 # 1. Cancellation Change
                 if current_status == "Cancelled" and prev_status != "Cancelled":
                     change_flags["just_cancelled"] = True
                     next_available_train_info = None # Placeholder
                     if scheduled_dt: # Find next only if we have a time reference
                         next_train = self._find_next_train(all_trains, scheduled_dt)
                         if next_train: # Format next train info if found
                              next_sched = next_train.get("scheduled"); next_exp = next_train.get("expected")
                              next_plat = next_train.get("platform", "TBC"); next_term = next_train.get("terminus")
                              next_time = self._parse_train_time(next_exp) or self._parse_train_time(next_sched)
                              next_available_train_info = f"{next_time.strftime('%H:%M') if next_time else (next_exp or next_sched)} to {next_term} (Plat: {next_plat})"
                     new_attrs["next_available_train"] = next_available_train_info # Add to attributes
                     event_to_fire = {'name': f"{DOMAIN}_train_cancelled", 'data': { # Base data below
                          "next_available_train": next_available_train_info }}
                     self._tracking_stopped = True # Cancellation is a final state

                 # Only check other changes if NOT just cancelled
                 elif current_status != "Cancelled": # Use elif to ensure only one primary event fires per cycle
                    # 2. Platform Change
                    if current_platform != prev_platform and current_platform != "TBC":
                         change_flags["platform_changed"] = True
                         new_attrs["previous_platform"] = prev_platform
                         event_to_fire = {'name': f"{DOMAIN}_platform_changed", 'data': {
                              "new_platform": current_platform,
                              "previous_platform": prev_platform }}

                    # 3. Time Change
                    elif current_expected_iso != prev_expected_iso and current_expected_iso: # Use elif
                         time_actually_changed = False
                         current_dt_comp = self._parse_train_time(current_expected_iso)
                         prev_dt_comp = self._parse_train_time(prev_expected_iso)
                         if current_dt_comp and prev_dt_comp and current_dt_comp != prev_dt_comp: time_actually_changed = True
                         elif current_dt_comp and not prev_dt_comp: time_actually_changed = True # Changed from TBC/Status to time

                         if time_actually_changed:
                             change_flags["time_changed"] = True
                             new_attrs["previous_expected_departure_iso"] = prev_expected_iso
                             new_attrs["previous_expected_departure_time"] = previous_attributes.get("expected_departure_time", "?")
                             event_to_fire = {'name': f"{DOMAIN}_time_changed", 'data': {
                                  "previous_expected_departure_time": new_attrs["previous_expected_departure_time"] }}

                    # 4. Status Change (Meaningful changes)
                    elif current_status != prev_status and current_status is not None and prev_status is not None: # Use elif
                        is_significant = True
                        if {current_status, prev_status} == {"On Time", "Scheduled"}: # Ignore minor if time same
                             # Check if time actually changed (using parsed datetimes for comparison)
                             current_dt_comp = self._parse_train_time(current_expected_iso)
                             prev_dt_comp = self._parse_train_time(prev_expected_iso)
                             if current_dt_comp and prev_dt_comp and current_dt_comp == prev_dt_comp:
                                 is_significant = False
                             elif current_expected_iso == prev_expected_iso: # If ISO string same, not significant
                                 is_significant = False


                        if is_significant:
                             change_flags["status_changed"] = True
                             new_attrs["previous_status"] = prev_status
                             event_to_fire = {'name': f"{DOMAIN}_status_changed", 'data': {
                                  "new_status": current_status,
                                  "previous_status": prev_status }}

            # --- ELSE (Not Initialized Yet) ---
            else:
                # This is the initial find
                event_to_fire = {'name': f"{DOMAIN}_train_found", 'data': {}} # Base data added below
                # Set initialized flag TRUE *after* this update cycle is processed
                # Need to defer this slightly, see below

        # ==============================================================
        # --- Final State Update & Event Firing ---
        # ==============================================================

        # Add change flags to attributes
        new_attrs.update(change_flags)

        # Check if state or attributes changed
        state_changed = new_native_value != previous_native_value
        attrs_changed = new_attrs != previous_attributes

        if state_changed or attrs_changed:
            self._attr_native_value = new_native_value
            self._attr_extra_state_attributes = new_attrs
            _LOGGER.debug("Updating state for %s: Value='%s', Attrs=%s",
                         self.entity_id, self._attr_native_value, self._attr_extra_state_attributes)
            self.async_write_ha_state() # Ensure HA knows about the update

            # --- Fire Event AFTER state is updated ---
            if event_to_fire:
                # Add common data to event payload
                common_event_data = {
                    "entity_id": self.entity_id,
                    "target_destination": self._target_destination,
                    "terminus": new_attrs.get("terminus"),
                    # "terminus_name": new_attrs.get("terminus_name"), # If implemented
                    "scheduled_departure_time": new_attrs.get("scheduled_departure_time"),
                    "expected_departure_time": new_attrs.get("expected_departure_time"),
                    "platform": new_attrs.get("platform"),
                    "status": new_attrs.get("status"),
                    "delay_minutes": new_attrs.get("delay_minutes"),
                    "calling_points": new_attrs.get("calling_points"),
                    "train_id": train_id_for_event # Include the ID
                }
                # Merge specific event data
                event_data = {**common_event_data, **event_to_fire['data']}

                _LOGGER.info("%s: Firing event '%s' with data: %s",
                             self.entity_id, event_to_fire['name'], event_data)
                self.hass.bus.async_fire(event_to_fire['name'], event_data)

            # --- Set initialized flag only AFTER the first successful update ---
            if not self._initialized and current_train_data:
                 _LOGGER.debug("%s: Marking sensor as initialized.", self.entity_id)
                 self._initialized = True

        else:
             _LOGGER.debug("No state or attribute change detected for %s", self.entity_id)