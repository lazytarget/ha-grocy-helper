from __future__ import annotations
from typing import Any, Dict
from aiohttp import ClientSession as Session, web_exceptions
import json
import logging
from .const import ApiException

_LOGGER = logging.getLogger(__name__)

CONTENT_TYPE = ("Content-Type", "application/json; charset=utf-8")
AUTHORIZATION = ("GROCY-API-KEY", "%s")
X_REQUEST_ID = ("X-Request-Id", "%s")


def create_headers(
    auth_key: str | None = None,
    with_content: bool = False,
    request_id: str | None = None,
    headers: Dict[str, str] = None
) -> Dict[str, str]:
    headers = headers or {}
    if auth_key:
        headers |= [(AUTHORIZATION[0], AUTHORIZATION[1] % auth_key)]
    if with_content:
        headers.update([CONTENT_TYPE])
    if request_id:
        headers.update([(X_REQUEST_ID[0], X_REQUEST_ID[1] % request_id)])
    _LOGGER.debug("HTTP [XXXX] Headers: %s", json.dumps(headers))
    return headers


async def async_get(
    session: Session,
    url: str,
    auth_key: str | None = None,
    params: Dict[str, Any] | None = None,
    return_none_when_404: bool = False,
):
    _LOGGER.info(
        "HTTP [GET] Req: %s%s", url, f" | Params: {str(params)}" if params else ""
    )
    if not isinstance(session, Session):
        session = session()
    response = await session.get(
        url, params=params, headers=create_headers(auth_key=auth_key)
    )

    if response.status == 200:
        j = await response.json()
        _LOGGER.debug("HTTP [GET] Resp: %s", json.dumps(j))
        return j
    elif response.status == 400:
        j = await response.json() or {}
        he = ApiException(response.status, j.get("error_message"))
        raise he
    elif response.status == 404 and return_none_when_404:
        return None

    try:
        _LOGGER.info(
            "Raising for status: %s -> %s", response.status, await response.text()
        )
        response.raise_for_status()
    except BaseException as ex:
        _LOGGER.error(
            "HTTP [GET] Resp: %s -> %s", response.status, await response.text()
        )
        raise ex

    return response.ok


async def async_post(
    session: Session,
    url: str,
    auth_key: str | None = None,
    data: Dict[str, Any] | None = None,
    json_data: Any | None = None,
):
    request_id = data.pop("request_id", None) if data else None

    headers = create_headers(
        auth_key=auth_key, with_content=bool(data), request_id=request_id
    )

    _LOGGER.info("HTTP [POST] Req: %s  \t%s", url, session)
    if not isinstance(session, Session):
        session = session()
    _LOGGER.info("Sesh: %s", session)
    response = await session.post(
        url,
        headers=headers,
        data=json.dumps(data) if data else None,
        json=json_data,
    )

    if response.status == 200:
        j = await response.json()
        _LOGGER.debug("HTTP [POST] Resp: %s", j)
        return j
    elif response.status == 400:
        j = await response.json() or {}
        raise web_exceptions.HTTPBadRequest(text=j.get("error_message"))

    try:
        response.raise_for_status()
    except BaseException as ex:
        _LOGGER.error(
            "HTTP [POST] Resp: %s -> %s", response.status, await response.text()
        )
        raise ex

    return response.ok


def delete(
    session: Session,
    url: str,
    auth_key: str | None = None,
    args: Dict[str, Any] | None = None,
):
    request_id = args.pop("request_id", None) if args else None

    headers = create_headers(auth_key=auth_key, request_id=request_id)

    _LOGGER.info("HTTP [DELETE] Req: %s", url)
    if not isinstance(session, Session):
        session = session()
    response = session.delete(
        url,
        headers=headers,
    )

    try:
        response.raise_for_status()
    except BaseException as ex:
        _LOGGER.error(
            "HTTP [DELETE] Resp: %s -> %s", response.status_code, response.text
        )
        raise ex

    return response.ok
