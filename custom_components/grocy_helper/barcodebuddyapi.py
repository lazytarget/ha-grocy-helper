"""API integration with BarcodeBuddy."""

from aiohttp import ClientSession, FormData

from .const import API, SCAN_MODE
from .grocytypes import (
    BarcodeBuddyScanRequest,
    BarcodeBuddyScanResponse,
)
from .http_requests import async_get, async_post


class BarcodeBuddyAPI:
    """Class to integrate with a BarcodeBuddy API."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        websession: ClientSession,
    ) -> None:
        def wrap_websession():
            s: ClientSession = None
            if isinstance(websession, ClientSession):
                # Passed a ClientSession instance
                s = websession
            else:
                # Might have passed func, invoke factory method
                s = websession()
            # append with default headers
            # create_headers(auth_key=api_key, headers=s.headers)
            return s

        self._session = wrap_websession
        self._base_url = base_url
        self._api_key = api_key

    def get_rest_url(self, endpoint: str):
        return "/".join([self._base_url, endpoint])

    def convert_scan_mode_to_bbuddy_mode(self, mode: SCAN_MODE) -> int:
        if mode == SCAN_MODE.CONSUME:
            return 0
        elif mode == SCAN_MODE.CONSUME_SPOILED:
            return 1
        elif mode == SCAN_MODE.PURCHASE:
            return 2
        elif mode == SCAN_MODE.OPEN:
            return 3
        elif mode == SCAN_MODE.INVENTORY:
            return 4
        elif mode == SCAN_MODE.ADD_TO_SHOPPING_LIST:
            return 5
        elif mode == SCAN_MODE.CONSUME_ALL:
            return 6
        return -1

    def convert_bbuddy_mode_to_scan_mode(self, bb_mode: int) -> SCAN_MODE:
        if bb_mode == 0:
            return SCAN_MODE.CONSUME
        elif bb_mode == 1:
            return SCAN_MODE.CONSUME_SPOILED
        elif bb_mode == 2:
            return SCAN_MODE.PURCHASE
        elif bb_mode == 3:
            return SCAN_MODE.OPEN
        elif bb_mode == 4:
            return SCAN_MODE.INVENTORY
        elif bb_mode == 5:
            return SCAN_MODE.ADD_TO_SHOPPING_LIST
        elif bb_mode == 6:
            return SCAN_MODE.CONSUME_ALL
        return None

    async def get_mode(self):
        url = self.get_rest_url(API.URLs.BBUDDY_GET_MODE)
        j = await async_get(self._session, url, self._api_key)
        return j["data"]["mode"]

    async def set_mode(self, mode: int):
        # STATE_CONSUME = 0; STATE_CONSUME_SPOILED = 1; STATE_PURCHASE = 2; STATE_OPEN = 3; STATE_GETSTOCK = 4; STATE_ADD_SL = 5; STATE_CONSUME_ALL = 6;
        url = self.get_rest_url(API.URLs.BBUDDY_SET_MODE)
        fd = FormData(
            {
                "state": mode,
            }
        )
        return await async_post(
            self._session, url, self._api_key, data=fd, content_type=False
        )

    async def post_scan(
        self, request: BarcodeBuddyScanRequest
    ) -> BarcodeBuddyScanResponse:
        url = self.get_rest_url(API.URLs.BBUDDY_SCAN)
        fd = FormData(request)
        return await async_post(
            self._session, url, self._api_key, data=fd, content_type=False
        )
