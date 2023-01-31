"""Constants for the National Rail UK integration."""

DOMAIN = "nationalrailuk"
DOMAIN_DATA = f"{DOMAIN}_data"

# Platforms
SENSOR = "sensor"
PLATFORMS = [SENSOR]

WSDL = "http://lite.realtime.nationalrail.co.uk/OpenLDBWS/wsdl.aspx?ver=2021-11-01"


CONF_TOKEN = "api_token"
CONF_STATION = "station"
CONF_DESTINATIONS = "destinations"

REFRESH = 10