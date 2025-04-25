"""Constants for the National Rail UK integration."""

DOMAIN = "nationalrailuk"
DOMAIN_DATA = f"{DOMAIN}_data"

# Platforms
SENSOR = "sensor"
PLATFORMS = [SENSOR]

WSDL = "https://lite.realtime.nationalrail.co.uk/OpenLDBWS/wsdl.aspx?ver=2021-11-01"


CONF_TOKEN = "api_token"
CONF_STATION = "station"
CONF_DESTINATIONS = "destinations"

# New configuration keys for specific train monitoring
CONF_MONITOR_TRAIN = "monitor_train"
CONF_TARGET_TIME = "target_time"
CONF_TARGET_DESTINATION = "target_destination"
CONF_TIME_WINDOW_MINUTES = "time_window_minutes"
CONF_MONITORED_TRAIN_NAME = "monitored_train_name"


# Default values
DEFAULT_TIME_WINDOW = 30

# Refresh frequency for the sensor
REFRESH = 1

# Polling interval (in minutes)
POLLING_INTERVALE = 10

# Increase polling frequency if withing X minutes of next departure or if train is late
HIGH_FREQUENCY_REFRESH = 7


ATTR_ENTITY_ID = "entity_id"