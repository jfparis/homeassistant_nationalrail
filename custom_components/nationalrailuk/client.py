"""Client for the National Rail API"""

from datetime import datetime, timedelta
import logging

import httpx
from zeep import AsyncClient, Settings, xsd
from zeep.exceptions import Fault
from zeep.plugins import HistoryPlugin
from zeep.transports import AsyncTransport

from .const import WSDL

_LOGGER = logging.getLogger(__name__)


class NationalRailClientException(Exception):
    """Base exception class."""


class NationalRailClientInvalidToken(NationalRailClientException):
    """Token is Invalid"""


class NationalRailClientInvalidInput(NationalRailClientException):
    """Invalid input parameters such as station codes"""


def rebuild_date(base, time):
    """Rebuild a date time object from the simplified representation returned by the api"""
    time = time.split(":")
    hour = int(time[0])
    minute = int(time[1])

    date_object = datetime(
        base.year, base.month, base.day, hour, minute, tzinfo=base.tzinfo
    )

    if (date_object - datetime.now(tz=base.tzinfo)).total_seconds() < -4 * 60 * 60:
        new_base = base + timedelta(days=1)
        date_object = datetime(
            new_base.year,
            new_base.month,
            new_base.day,
            hour,
            minute,
            tzinfo=new_base.tzinfo,
        )
    return date_object


class NationalRailClient:
    """Client for the National Rail API"""

    def __init__(self, api_token, station, destinations) -> None:
        """Initialize the client."""
        self.station = station
        self.api_token = api_token
        self.destinations = destinations if destinations is not None else []
        self.client = None
        self.header_value = None
        
        # Prepackage the authorization token structure (but don't set the value yet)
        self.header = xsd.Element(
            "{http://thalesgroup.com/RTTI/2013-11-28/Token/types}AccessToken",
            xsd.ComplexType(
                [
                    xsd.Element(
                        "{http://thalesgroup.com/RTTI/2013-11-28/Token/types}TokenValue",
                        xsd.String(),
                    ),
                ]
            ),
        )

    async def setup_client(self):
        """Set up the SOAP client asynchronously."""
        if self.client is not None:
            return
            
        import asyncio
        
        settings = Settings(strict=False)
        history = HistoryPlugin()
        
        # Create the transport in the executor to avoid blocking
        def create_transport():
            wsdl_client = httpx.Client(verify=True, timeout=300)
            httpx_client = httpx.AsyncClient(verify=True, timeout=300)
            return AsyncTransport(client=httpx_client, wsdl_client=wsdl_client)
        
        # Run the blocking operation in a thread
        transport = await asyncio.to_thread(create_transport)
        
        # Create the client
        self.client = AsyncClient(
            wsdl=WSDL, 
            transport=transport, 
            settings=settings, 
            plugins=[history]
        )
        
        # Set the header value
        self.header_value = self.header(TokenValue=self.api_token)        

    async def get_raw_departures(self):
        """Get the raw data from the api."""
        # Ensure client is set up
        if self.client is None:
            await self.setup_client()
            
        # Rest of the method remains the same
        if len(self.destinations) == 0:
            res = await self.client.service.GetDepBoardWithDetails(
                numRows=10, crs=self.station, _soapheaders=[self.header_value]
            )
        else:
            res = {}
            for each in self.destinations:
                batch = await self.client.service.GetDepBoardWithDetails(
                    numRows=10,
                    crs=self.station,
                    filterCrs=each,
                    filterType="to",
                    _soapheaders=[self.header_value],
                )
                if not res:
                    res = batch
                else:
                    if res["trainServices"] is None:
                        res["trainServices"] = batch["trainServices"]
                    elif (
                        batch["trainServices"] is not None
                        and "service" in batch["trainServices"]
                        and "service" in res["trainServices"]
                    ):
                        res["trainServices"]["service"] = (
                            res["trainServices"]["service"]
                            + batch["trainServices"]["service"]
                        )

        return res

    def process_data(self, json_message):
        """Unpack the data return by the api in a usable format for hass"""

        status = {}
        status["trains"] = []
        status["station"] = json_message["locationName"]

        time_base = json_message["generatedAt"]
        if json_message["trainServices"] is None:
            return status

        services_list = json_message["trainServices"]["service"]
        serviceIDs = [x["serviceID"] for x in services_list]

        for i, service in enumerate(services_list):
            # Skip trains that we have seen already
            if serviceIDs.index(service["serviceID"]) != i:
                continue

            train = {}
            perturbation = False

            time = rebuild_date(time_base, service["std"])

            if service["etd"] == "On time":
                expected = time
            elif service["etd"] == "Delayed" or service["etd"] == "Cancelled":
                expected = service["etd"]
                perturbation = True
            else:
                expected = rebuild_date(time_base, service["etd"])
                delay = (expected - time).total_seconds() / 60
                if delay > 9:
                    perturbation = True

            terminus = service["destination"]["location"][0]["locationName"]

            destinations_list = service["subsequentCallingPoints"]["callingPointList"][
                0
            ]["callingPoint"]

            # arrival_time = None
            # arrival_dest = None

            destination = []
            if len(self.destinations) == 0:
                destination = destinations_list[-1]
            else:
                for each in destinations_list:
                    if each["crs"] in self.destinations:
                        destination.append(each)

            # if national rail returned us a train not heading
            # to our destination
            if not destination:
                continue

            arrival_dest = []
            dest_list = [] # Initialize dest_list

            if isinstance(destination, list):
                # Case 1: Filters were applied, destination is a list (possibly empty)
                dest_list = destination
            elif hasattr(destination, 'crs'): # Check if it's a single CallingPoint object
                # Case 2: No filters applied (len(self.destinations) == 0),
                # destination is the single last CallingPoint object. Wrap it in a list.
                dest_list = [destination]
            # Optional: Add specific check for dict if that's a known possibility, though less likely now
            # elif isinstance(destination, dict):
            #     _LOGGER.warning("Destination is a dictionary, which is unexpected. Trying to process: %s", destination)
            #     # Decide how to handle dict - maybe try accessing keys? Or just skip?
            elif destination is not None: # Only warn if it's truly unexpected (not None, not list, not CallingPoint)
                _LOGGER.warning("Unexpected type or structure for destination object after filtering: %s - %s", type(destination), destination)
                # Cannot process this, dest_list remains empty

            # The rest of the loop processing dest_list remains the same
            for dest in dest_list: # Iterate over the guaranteed list
                # Use getattr for safe attribute access
                st_value = getattr(dest, 'st', None) # Get scheduled time attribute 'st'
            
            
                if st_value is None:
                    _LOGGER.warning("Skipping destination - missing 'st' attribute: %s", getattr(dest, 'locationName', 'Unknown Location'))
                    continue

                try:
                    expected_arrival = rebuild_date(time_base, st_value)

                    arrival_time = None # Initialize arrival_time
                    et_value = getattr(dest, 'et', None) # Get estimated time attribute 'et'

                    if et_value == "On time":
                       arrival_time = expected_arrival
                    elif et_value == "Delayed" or et_value == "Cancelled":
                       arrival_time = et_value
                       perturbation = True
                    elif et_value: # Check if et_value has a value (and is not 'On Time', 'Delayed', etc.)
                        try:
                            arrival_time = rebuild_date(time_base, et_value)
                            delay = (arrival_time - expected_arrival).total_seconds() / 60
                            if delay > 9 and not perturbation:
                                perturbation = True
                        except ValueError:
                             _LOGGER.warning("Could not parse estimated arrival time '%s' for %s", et_value, getattr(dest, 'locationName', 'Unknown Location'))
                             arrival_time = "Invalid Time" # Or keep as None?
                             perturbation = True
                    else:
                       # If et is missing or None, assume 'On time' or handle as needed
                       arrival_time = expected_arrival
                       _LOGGER.debug("'et' attribute missing for destination %s, assuming on time.", getattr(dest, 'locationName', 'Unknown Location'))


                    arrival_dest.append(
                        {
                            # Use getattr for locationName as well
                            "name": getattr(dest, 'locationName', "Unknown"),
                            "time_at_destination": arrival_time,
                            "scheduled_time_at_destination": expected_arrival,
                        }
                    )
                except Exception as e:
                     # Log the specific destination causing trouble
                     location_name = getattr(dest, 'locationName', 'Unknown Location')
                     _LOGGER.error("Error processing destination %s: %s", location_name, e, exc_info=True)
                     continue # Skip this destination on error

            train["scheduled"] = time
            train["expected"] = expected
            train["terminus"] = terminus
            train["destinations"] = arrival_dest
            train["platform"] = service["platform"]
            train["perturbation"] = perturbation

            status["trains"].append(train)

        status["trains"] = sorted(
            status["trains"],
            key=lambda d: d["expected"]
            if isinstance(d["expected"], datetime)
            else d["scheduled"],
        )

        return status

    async def async_get_data(self):
        """Data refresh function called by the coordinator"""
        # Ensure client is set up
        if self.client is None:
            await self.setup_client()
            
        try:
            _LOGGER.info("Requesting depearture data for %s", self.station)
            raw_data = await self.get_raw_departures()
        except Fault as err:
            _LOGGER.exception("Exception whilst fetching data: ")
            if err.message == "Unknown fault occured":
                # likely invalid token
                raise NationalRailClientInvalidToken("Invalid API token") from err
            if err.message == "Unexpected server error":
                # likely invalid input
                raise NationalRailClientInvalidInput("Invalid station input") from err

            raise NationalRailClientException("Unknown Error") from err

        _LOGGER.debug("Raw data:\n%s", raw_data)

        try:
            _LOGGER.info("Procession station schedule for %s", self.station)
            data = self.process_data(raw_data)
        except Exception as err:
            _LOGGER.exception("Exception whilst processing data: ")
            raise NationalRailClientException("unexpected data from api") from err
        return data
