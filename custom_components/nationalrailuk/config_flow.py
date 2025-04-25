"""Config flow for National Rail UK integration."""

import logging
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
import homeassistant.helpers.config_validation as cv  # Add this import
from datetime import time

from .client import NationalRailClient, NationalRailClientException, NationalRailClientInvalidToken, NationalRailClientInvalidInput
from .const import (
    CONF_DESTINATIONS, 
    CONF_STATION, 
    CONF_TOKEN,
    DOMAIN,
    CONF_MONITOR_TRAIN,
    CONF_TARGET_TIME,
    CONF_TARGET_DESTINATION,
    CONF_TIME_WINDOW_MINUTES,
    CONF_MONITORED_TRAIN_NAME,
    DEFAULT_TIME_WINDOW
)
from .stations import STATIONS, STATION_MAP

_LOGGER = logging.getLogger(__name__)

def parse_destinations(destinations_str):
    """Parse comma-separated destination string or list to list."""
    if not destinations_str:
        return []
    
    # If it's already a list, just return it (after stripping)
    if isinstance(destinations_str, list):
        return [d.strip() if isinstance(d, str) else d for d in destinations_str]
    
    # Otherwise treat it as a string and split
    return [d.strip() for d in destinations_str.split(",") if d.strip()]

def parse_time_string(time_str):
    """Parse HH:MM or HH:MM:SS string into time object."""
    if not time_str:
        return None
    
    parts = time_str.split(":")
    if len(parts) == 2:
        return time(int(parts[0]), int(parts[1]))
    elif len(parts) == 3:
        return time(int(parts[0]), int(parts[1]), int(parts[2]))
    else:
        raise ValueError(f"Invalid time format: {time_str}")

async def validate_api_token(hass, token: str) -> bool:
    """Validate the API token by making a test API call."""
    try:
        client = NationalRailClient(token, "PAD", [])
        await client.setup_client()  # Ensure client is set up
        await client.async_get_data()
        return True
    except NationalRailClientInvalidToken:
        return False
    except Exception as err:
        _LOGGER.warning("Error validating API token: %s", err)
        # If there's another error, it might be due to API limits or temporary issues
        # Rather than rejecting the token, we'll assume it's valid
        return True

async def validate_station_input(hass, user_input: dict) -> dict:
    """Validate a station configuration."""
    # Create a token to validate with
    token = user_input.get(CONF_TOKEN)
    if not token:
        # Try to get an existing token
        token = await get_global_api_token(hass)
        if not token:
            raise NationalRailClientException("No API token provided")
    
    station = user_input.get(CONF_STATION)
    if not station:
        raise NationalRailClientInvalidInput("No station provided")
    
    destinations = user_input.get(CONF_DESTINATIONS, [])
    
    # Handle destinations properly whether it's a string or list
    destination_list = destinations if isinstance(destinations, list) else parse_destinations(destinations)
    
    try:
        # Make a test API call
        client = NationalRailClient(token, station, destination_list)
        await client.setup_client()  # Ensure client is set up
        await client.async_get_data()
        
        # Return a clean result without token
        result = user_input.copy()
        if CONF_TOKEN in result and CONF_STATION in result:
            del result[CONF_TOKEN]  # Don't store token in station entries
        
        return result
    except NationalRailClientInvalidToken:
        _LOGGER.error("Invalid National Rail API token")
        raise
    except Exception as err:
        _LOGGER.exception("Unexpected error during station validation: %s", err)
        raise NationalRailClientException("Failed to validate station configuration") from err

async def get_global_api_token(hass):
    """Get the global API token from any available source."""
    # First look for dedicated token entries (those with only CONF_TOKEN)
    for entry in hass.config_entries.async_entries(DOMAIN):
        if CONF_TOKEN in entry.data and CONF_STATION not in entry.data:
            # Found a dedicated token entry
            _LOGGER.debug("Found token in dedicated token entry: %s...", entry.data[CONF_TOKEN][:5])
            return entry.data[CONF_TOKEN]
    
    # Then check all entries in the config registry for any token
    for entry in hass.config_entries.async_entries(DOMAIN):
        if CONF_TOKEN in entry.data:
            _LOGGER.debug("Found token in regular entry: %s...", entry.data[CONF_TOKEN][:5])
            return entry.data[CONF_TOKEN]
    
    # Finally check temporary storage
    if f"{DOMAIN}_token_to_save" in hass.data and hass.data[f"{DOMAIN}_token_to_save"]:
        token = hass.data[f"{DOMAIN}_token_to_save"]
        _LOGGER.debug("Found token in temporary storage: %s...", token[:5])
        return token
    
    _LOGGER.debug("No token found in any location")
    return None


async def ensure_global_token_entry(hass, token, source="migration"):
    """Ensure a global token entry exists."""
    # Check if we already have a global token entry
    for entry in hass.config_entries.async_entries(DOMAIN):
        if CONF_TOKEN in entry.data and CONF_STATION not in entry.data:
            _LOGGER.info("Global token entry already exists")
            
            # Check if we need to update the token
            if entry.data[CONF_TOKEN] != token:
                _LOGGER.info("Updating existing global token entry")
                new_data = dict(entry.data)
                new_data[CONF_TOKEN] = token
                hass.config_entries.async_update_entry(
                    entry,
                    data=new_data
                )
            
            return None
    
    # No dedicated token entry found, create one
    _LOGGER.info("Creating new global token entry")
    
    # Create a flow context with the token
    context = {"source": source}
    flow_id = await hass.config_entries.flow.async_init(
        DOMAIN, 
        context=context,
        data={
            CONF_TOKEN: token,
            # Add a special flag to indicate this is a token-only entry
            "is_token_entry": True
        }
    )
    
    _LOGGER.info("Initiated flow for global token entry: %s", flow_id)
    return None


class NationalRailConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for National Rail UK."""

    VERSION = 2  # Updated from 1 to 2

    def __init__(self):
        """Initialize the flow."""
        self._token = None

    async def async_step_system(self, user_input=None) -> FlowResult:
        """Handle a flow initialized by the system."""
        if user_input and CONF_TOKEN in user_input:
            _LOGGER.info("Creating token-only entry from system")
            
            # Check for existing token entries first
            for entry in self.hass.config_entries.async_entries(DOMAIN):
                if CONF_TOKEN in entry.data and CONF_STATION not in entry.data:
                    _LOGGER.info("Token-only entry already exists, updating it")
                    new_data = dict(entry.data)
                    new_data[CONF_TOKEN] = user_input[CONF_TOKEN]
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data=new_data
                    )
                    return self.async_abort(reason="already_configured")
            
            # Set a unique ID
            await self.async_set_unique_id(f"{DOMAIN}_token")
            self._abort_if_unique_id_configured()  # Removed the update_data parameter
            
            # Create new entry
            return self.async_create_entry(
                title="National Rail API Token",
                data={CONF_TOKEN: user_input[CONF_TOKEN]}
            )
        
        # If no token provided, abort
        return self.async_abort(reason="missing_token")

    async def async_step_user(self, user_input=None) -> FlowResult:
        """Handle the user's initial choices."""
        # Regular user flow
        errors = {}
        
        # Check if we need to set up the token first
        token = await get_global_api_token(self.hass)
        
        if not token:
            # Check temporary storage
            if f"{DOMAIN}_token_to_save" in self.hass.data and self.hass.data[f"{DOMAIN}_token_to_save"]:
                token = self.hass.data[f"{DOMAIN}_token_to_save"]
                _LOGGER.info("Using token from temporary storage: %s...", token[:5])
                
                # Create a token-only entry
                await self.async_set_unique_id(f"{DOMAIN}_api_token")
                self._abort_if_unique_id_configured()
                
                return self.async_create_entry(
                    title="National Rail API Token",
                    data={CONF_TOKEN: token}
                )
        
        if not token:
            # Still no token found, redirect to token setup
            return await self.async_step_api_token()
        
        # We have a token, proceed to station setup
        self._token = token
        return await self.async_step_station()

    async def async_step_api_token(self, user_input=None) -> FlowResult:
        """Handle the API token configuration step."""
        errors = {}
        
        if user_input is not None:
            try:
                # Validate the token
                if await validate_api_token(self.hass, user_input[CONF_TOKEN]):
                    # Token is valid, save it
                    self._token = user_input[CONF_TOKEN]
                    
                    # Check for existing token entries first
                    for entry in self.hass.config_entries.async_entries(DOMAIN):
                        if CONF_TOKEN in entry.data and CONF_STATION not in entry.data:
                            _LOGGER.info("Token-only entry already exists, updating it")
                            new_data = dict(entry.data)
                            new_data[CONF_TOKEN] = user_input[CONF_TOKEN]
                            self.hass.config_entries.async_update_entry(
                                entry,
                                data=new_data
                            )
                            return await self.async_step_station()
                    
                    # Create a global token entry
                    await self.async_set_unique_id(f"{DOMAIN}_api_token")
                    self._abort_if_unique_id_configured()
                    
                    return self.async_create_entry(
                        title="National Rail API Token",
                        data={CONF_TOKEN: user_input[CONF_TOKEN]}
                    )
                else:
                    errors["base"] = "invalid_token"
            except Exception as ex:
                _LOGGER.exception("Unexpected error during token validation")
                errors["base"] = "unknown"
        
        # Show form to enter API token
        return self.async_show_form(
            step_id="api_token",
            data_schema=vol.Schema({
                vol.Required(CONF_TOKEN): str,
            }),
            errors=errors,
            description_placeholders={
                "token_info": "You need to provide a valid National Rail Darwin API token"
            }
        )

    async def async_step_station(self, user_input=None) -> FlowResult:
        """Handle the station configuration step."""
        errors = {}
        
        # Create station options for dropdown
        station_options = {station["code"]: f"{station['name']} ({station['code']})" 
                         for station in STATIONS}
        
        # Create station options for multi-select dropdown
        station_select_options = [
            {"value": station["code"], 
             "label": f"{station['name']} ({station['code']})"} 
            for station in STATIONS
        ]
        
        if user_input is not None:
            # Add this validation
            if not user_input.get(CONF_MONITOR_TRAIN) and (
                user_input.get(CONF_TARGET_TIME) or 
                user_input.get(CONF_TARGET_DESTINATION)
            ):
                errors["base"] = "toggle_required"
                return self.async_show_form(
                    step_id="station",
                    data_schema=self._get_station_schema(station_options, station_select_options),
                    errors=errors
                )
            try:
                # Store token locally but don't include in user_input that will be saved
                local_token = self._token
                
                # Use the token for validation only
                validation_input = user_input.copy()
                validation_input[CONF_TOKEN] = local_token
                
                # Validate the input
                await validate_station_input(self.hass, validation_input)
                
                # Don't save the token in the station entry
                if CONF_TOKEN in user_input:
                    del user_input[CONF_TOKEN]
                
                # Handle and prepare target time if monitoring specific train
                if user_input.get(CONF_MONITOR_TRAIN, False) and CONF_TARGET_TIME in user_input:
                    # Convert time input string to time object and back to string for storage
                    if isinstance(user_input[CONF_TARGET_TIME], str):
                        try:
                            # Parse the time string
                            target_time = parse_time_string(user_input[CONF_TARGET_TIME])
                            # Only store hours and minutes, setting seconds to 00
                            user_input[CONF_TARGET_TIME] = target_time.strftime("%H:%M:00")
                        except ValueError as e:
                            _LOGGER.error("Invalid time format: %s", e)
                            errors["base"] = "invalid_time_format"
                            return self.async_show_form(
                                step_id="station", 
                                data_schema=self._get_station_schema(station_options, station_select_options),
                                errors=errors
                            )
                
                # Set title based on station name and destination
                station_name = STATION_MAP.get(user_input[CONF_STATION], user_input[CONF_STATION])
                destination_names = []
                
                if user_input.get(CONF_DESTINATIONS):
                    # Get list of destinations
                    destinations = user_input[CONF_DESTINATIONS]
                    if isinstance(destinations, str):
                        destinations = parse_destinations(destinations)
                    
                    for dest in destinations:
                        destination_names.append(STATION_MAP.get(dest, dest))
                
                if user_input.get(CONF_MONITOR_TRAIN, False):
                    # Use specified name or create one
                    if not user_input.get(CONF_MONITORED_TRAIN_NAME):
                        target_time = user_input[CONF_TARGET_TIME]
                        target_dest = STATION_MAP.get(user_input[CONF_TARGET_DESTINATION], 
                                                     user_input[CONF_TARGET_DESTINATION])
                        title = f"Train {station_name} -> {target_dest} @ {target_time}"
                        user_input[CONF_MONITORED_TRAIN_NAME] = f"Train to {target_dest} @ {target_time}"
                    else:
                        title = user_input[CONF_MONITORED_TRAIN_NAME]
                else:
                    title = f"Train Schedule {user_input[CONF_STATION]}"
                    if destination_names:
                        title += f" -> {', '.join(destination_names)}"
                
                return self.async_create_entry(title=title, data=user_input)
            except NationalRailClientException as ex:
                _LOGGER.exception("Error during station configuration: %s", ex)
                errors["base"] = "cannot_connect"
            except NationalRailClientInvalidInput as ex:
                _LOGGER.exception("Invalid station input: %s", ex)
                errors["base"] = "invalid_station_input"
        
        # Show form with all required fields
        return self.async_show_form(
            step_id="station", 
            data_schema=self._get_station_schema(station_options, station_select_options),
            errors=errors
        )
    
    def _get_station_schema(self, station_options, station_select_options):
        """Get the schema for the station configuration step."""
        # Include all fields, but make monitoring fields optional
        schema_dict = {
            vol.Required(CONF_STATION): vol.In(station_options),
            vol.Optional(CONF_DESTINATIONS, default=[]): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=station_select_options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            vol.Optional(CONF_MONITOR_TRAIN, default=False): selector.BooleanSelector(),
            # Always include these fields but clearly indicate they're for monitored trains
            vol.Optional(CONF_TARGET_TIME): selector.TimeSelector(),  # Simplified time selector
            vol.Optional(CONF_TARGET_DESTINATION): vol.In(station_options),
            vol.Optional(CONF_TIME_WINDOW_MINUTES, default=DEFAULT_TIME_WINDOW): selector.NumberSelector(
                selector.NumberSelectorConfig(
                    min=5,
                    max=60,
                    step=5,
                    unit_of_measurement="minutes"
                )
            ),
            vol.Optional(CONF_MONITORED_TRAIN_NAME): cv.string
            
        }
        
        return vol.Schema(schema_dict)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options for the National Rail integration."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        super().__init__(config_entry)
        # Store any needed properties from config_entry directly
        self._entry_data = config_entry.data
        self._destinations = parse_destinations(config_entry.data.get(CONF_DESTINATIONS, ""))
        self._monitor_train = config_entry.data.get(CONF_MONITOR_TRAIN, False)

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            # Convert multi-select list to comma-separated string if needed
            if isinstance(user_input.get(CONF_DESTINATIONS), list):
                user_input[CONF_DESTINATIONS] = ",".join(user_input[CONF_DESTINATIONS])
                
            # Always store time with seconds set to 00
            if CONF_TARGET_TIME in user_input:
                # Format time as HH:MM:00
                try:
                    time_parts = user_input[CONF_TARGET_TIME].split(":")
                    if len(time_parts) >= 2:
                        user_input[CONF_TARGET_TIME] = f"{time_parts[0]}:{time_parts[1]}:00"
                except Exception:
                    # Keep the original value if parsing fails
                    pass
                
            return self.async_create_entry(title="", data=user_input)

        # Create station options for multi-select dropdown
        station_select_options = [
            {"value": station["code"], 
             "label": f"{station['name']} ({station['code']})"} 
            for station in STATIONS
        ]
        
        station_options = {station["code"]: f"{station['name']} ({station['code']})" 
                         for station in STATIONS}
        
        # Get current destinations as a list
        current_destinations = parse_destinations(
            self.config_entry.data.get(CONF_DESTINATIONS, "")
        )
        

        
        schema = vol.Schema({
            vol.Optional(CONF_DESTINATIONS, default=current_destinations): 
            selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=station_select_options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.DROPDOWN
                )
            ),
            
        })
        
        # If this is a monitored train entry, add appropriate options
        if self._monitor_train:
            # Parse the current target time back to a time object
            current_target_time = self.config_entry.data.get(CONF_TARGET_TIME)
            current_target_dest = self.config_entry.data.get(CONF_TARGET_DESTINATION)
            current_time_window = self.config_entry.data.get(CONF_TIME_WINDOW_MINUTES, DEFAULT_TIME_WINDOW)
            current_name = self.config_entry.data.get(CONF_MONITORED_TRAIN_NAME, "")
            
            schema = vol.Schema({
                vol.Optional(CONF_TARGET_TIME, default=current_target_time): selector.TimeSelector(),  # Simplified
                vol.Optional(CONF_TARGET_DESTINATION, default=current_target_dest): vol.In(station_options),
                vol.Optional(CONF_TIME_WINDOW_MINUTES, default=current_time_window): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=5,
                        max=60,
                        step=5,
                        unit_of_measurement="minutes"
                    )
                ),
                vol.Optional(CONF_MONITORED_TRAIN_NAME, default=current_name): cv.string
                
            })
        
        return self.async_show_form(step_id="init", data_schema=schema)