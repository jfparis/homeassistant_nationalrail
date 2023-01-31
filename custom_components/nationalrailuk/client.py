from datetime import datetime, timedelta
import logging

from zeep import AsyncClient, Settings, xsd
from zeep.plugins import HistoryPlugin

from .const import DOMAIN, WSDL

_LOGGER = logging.getLogger(__name__)


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
            res = None
            for each in self.destinations:
                batch = await self.client.service.GetDepBoardWithDetails(
                    numRows=10,
                    crs=self.station,
                    filterCrs=each,
                    filterType="to",
                    _soapheaders=[self.header_value],
                )
                if res is None:
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
        status = {}
        status["trains"] = []
        origin_station = json_message["locationName"]
        status["description"] = f"Departing train schedule at {origin_station}"
        status["name"] = f"train_schedule_{self.station}{'_' + '_'.join(self.destinations) if len(self.destinations) >0 else ''}"
        status["friendly_name"] = f"{self.destinations} schedule at Mortlake"
        time_base = json_message["generatedAt"]
        if json_message["trainServices"] is None:
            status["next_train"] = None
            status["arrival_time"] = None
            status["delay"] = None
            return status
        services_list = json_message["trainServices"]["service"]
        delays = []
        for service in services_list:
            train = {}
            try:
                time = rebuild_date(time_base, service["std"])
            except BaseException as err:
                time = None
                raise err
            try:
                if service["etd"] == "On time":
                    expected = time
                else:
                    expected = rebuild_date(time_base, service["etd"])
            except BaseException as err:
                expected = None
                raise err
            delay = (expected - time).total_seconds() / 60
            delays.append(delay)
            terminus = service["destination"]["location"][0]["locationName"]

            destinations_list = service["subsequentCallingPoints"]["callingPointList"][
                0
            ]["callingPoint"]

            arrival_time = None
            arrival_dest = None

            if len(self.destinations) == 0:
                destination = destinations_list[-1]
                arrival_dest = destination["locationName"]
                try:
                    if destination["et"] == "On time":
                        arrival_time = rebuild_date(time_base, destination["st"])
                    else:
                        arrival_time = rebuild_date(time_base, destination["et"])
                except BaseException as err:
                    _LOGGER.exception("Exception whilst fetching data: ")
                    raise err
            else:
                for destination in destinations_list:
                    if destination["crs"] in self.destinations:
                        arrival_dest = destination["locationName"]
                        try:
                            if destination["et"] == "On time":
                                arrival_time = rebuild_date(
                                    time_base, destination["st"]
                                )
                            else:
                                arrival_time = rebuild_date(
                                    time_base, destination["et"]
                                )
                        except BaseException as err:
                            _LOGGER.exception("Exception whilst fetching data: ")
                            raise err

            if arrival_dest is None:
                continue

            train["scheduled"] = time
            train["expected"] = expected
            train["terminus"] = terminus
            train["destination"] = arrival_dest
            train["time_at_destination"] = arrival_time
            train["delay"] = delay
            train["platform"] = service["platform"]
            status["trains"].append(train)

        status["trains"] = sorted(status["trains"], key=lambda d: d["expected"])
        status["next_train"] = status["trains"][0]["expected"]
        status["arrival_time"] = status["trains"][0]["time_at_destination"]
        status["delay"] = sum(delays) / len(delays)

        return status

    async def async_get_data(self):
        try:
            _LOGGER.debug(f"Requesting depearture data for {self.station}")
            raw_data = await self.get_raw_departures()
        except Exception as err:
            _LOGGER.exception("Exception whilst fetching data: ")
            raise err
        try:
            _LOGGER.debug(f"Procession station schedule for {self.station}")
            data = self.process_data(raw_data)
        except Exception as err:
            _LOGGER.exception("Exception whilst processing data: ")
            raise err
        return data
