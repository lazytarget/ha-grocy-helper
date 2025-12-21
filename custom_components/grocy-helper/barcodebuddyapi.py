"""API integration with BarcodeBuddy."""

from aiohttp import ClientSession, MultipartWriter, FormData

from .const import API, ApiException
from .grocytypes import (
    BarcodeBuddyScanRequest,
    BarcodeBuddyScanResponse,
)
from .http_requests import async_get, async_post, create_headers


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

    async def set_mode(
        self, mode: int
    ):
        # STATE_CONSUME = 0; STATE_CONSUME_SPOILED = 1; STATE_PURCHASE = 2; STATE_OPEN = 3; STATE_GETSTOCK = 4; STATE_ADD_SL = 5; STATE_CONSUME_ALL = 6;
        url = self.get_rest_url(API.URLs.BBUDDY_SET_MODE)
        fd = FormData({
            "state": mode,
        })
        return await async_post(self._session, url, self._api_key, data=fd, content_type=False)

    async def post_scan(
        self, request: BarcodeBuddyScanRequest
    ) -> BarcodeBuddyScanResponse:
        url = self.get_rest_url(API.URLs.BBUDDY_SCAN)
        fd = FormData(request)
        return await async_post(self._session, url, self._api_key, data=fd, content_type=False)
