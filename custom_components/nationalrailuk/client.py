"""Client for the National Rail API"""
from datetime import datetime, timedelta
import logging

from zeep import AsyncClient, Settings, xsd
from zeep.exceptions import Fault
from zeep.plugins import HistoryPlugin

from .const import WSDL

_LOGGER = logging.getLogger(__name__)


class NationalRailClientException(Exception):
    """Base exception class."""


class NationalRailClientInvalidToken(NationalRailClientException):
    """Token is Invalid"""


class NationalRailClientInvalidInput(NationalRailClientException):
    """Token is Invalid"""


def rebuild_date(base, time):
    """Rebuild a date time object from the implified representation returned by the api"""
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
        self.station = station
        self.api_token = api_token
        self.destinations = destinations if destinations is not None else []

        settings = Settings(strict=False)

        history = HistoryPlugin()

        self.client = AsyncClient(wsdl=WSDL, settings=settings, plugins=[history])

        # Prepackage the authorisation token
        header = xsd.Element(
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
        self.header_value = header(TokenValue=self.api_token)

    async def get_raw_departures(self):
        """Get the raw data from the api"""
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
                    else:
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
        for service in services_list:
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

            destination = None
            if len(self.destinations) == 0:
                destination = destinations_list[-1]
            else:
                for each in destinations_list:
                    if each["crs"] in self.destinations:
                        destination = each
                        break

            # if national rail returned us a train not heading
            # to our destination
            if destination is None:
                continue

            arrival_dest = destination["locationName"]
            expected_arrival = rebuild_date(time_base, destination["st"])
            if destination["et"] == "On time":
                arrival_time = expected_arrival
            elif destination["et"] == "Delayed" or destination["et"] == "Cancelled":
                arrival_time = destination["et"]
                perturbation = True
            else:
                arrival_time = rebuild_date(time_base, destination["et"])
                delay = (arrival_time - expected_arrival).total_seconds() / 60
                if delay > 9:
                    perturbation = True

            train["scheduled"] = time
            train["expected"] = expected
            train["terminus"] = terminus
            train["destination"] = arrival_dest
            train["time_at_destination"] = arrival_time
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
        """Data resfresh function called by the coordinator"""
        try:
            _LOGGER.debug("Requesting depearture data for %s", self.station)
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

        try:
            _LOGGER.debug("Procession station schedule for %s", self.station)
            data = self.process_data(raw_data)
        except Exception as err:
            _LOGGER.exception("Exception whilst processing data: ")
            raise NationalRailClientException("unexpected data from api") from err
        return data
