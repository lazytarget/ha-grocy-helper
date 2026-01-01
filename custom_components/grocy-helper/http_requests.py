from __future__ import annotations
from typing import Any, Dict
from aiohttp import ClientSession as Session, web_exceptions, FormData, typedefs
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
) -> Dict[str, str]:
    headers: Dict[str, str] = {}
    if isinstance(auth_key, str):
        headers.update([(AUTHORIZATION[0], AUTHORIZATION[1] % auth_key)])
        # headers[AUTHORIZATION[0]] = AUTHORIZATION[1] % auth_key
    else:
        headers.update([(auth_key[0], auth_key[1])])
        # headers[auth_key[0]] = auth_key[1]

    if isinstance(with_content, str):
        headers.update([(CONTENT_TYPE[0], with_content)])
    elif with_content:
        headers.update([CONTENT_TYPE])
        # headers[CONTENT_TYPE[0]] = CONTENT_TYPE[1]

    if request_id:
        headers.update([(X_REQUEST_ID[0], X_REQUEST_ID[1] % request_id)])
        # headers[X_REQUEST_ID[0]] = X_REQUEST_ID[1] % request_id
    _LOGGER.debug("HTTP [XXXX] Headers: %s", json.dumps(headers))
    return headers


async def async_get(
    session: Session,
    url: str,
    auth_key: str | None = None,
    params: Dict[str, Any] | typedefs.Query | None = None,
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
        _LOGGER.debug("HTTP [GET] Resp Error: %s", json.dumps(j))
        msg = j.get("error_message", j.get("result", {}).get("result"))
        if not msg:
            msg = json.dumps(j)
        he = ApiException(response.status, msg)
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
    content_type: str | None = None,
    params: typedefs.Query | None = None,
):
    request_id = (
        data.pop("request_id", None) if data and isinstance(data, Dict) else None
    )

    headers = create_headers(
        auth_key=auth_key,
        with_content=content_type if content_type else bool(data) if isinstance(data, Dict) else content_type,
        request_id=request_id,
    )
    # if content_type:
    #     headers["Content-Type"] = content_type

    _LOGGER.info("HTTP [POST] Req: %s  \t%s", url, session)
    if not isinstance(session, Session):
        session = session()
    if json_data:
        response = await session.post(
            url,
            headers=headers,
            # data=json.dumps(data) if data else None,
            data=data,
            json=json_data,
            params=params,
        )
    else:
        response = await session.post(
            url,
            headers=headers,
            # data=json.dumps(data) if data else None,
            data=data,
            params=params
        )

    if response.status == 200:
        try:
            j = await response.json()
            _LOGGER.debug("HTTP [POST] 200 :> Resp: %s", j)
            return j
        except:
            j = await response.text()
            _LOGGER.debug("HTTP [POST] 200 :> Resp[TEXT]: %s", j)
            raise
    elif response.status == 400:
        j: Dict = {}
        try:
            j = await response.json()
            _LOGGER.debug("HTTP [POST] 400 :> Resp: %s", j)
        except:
            j = await response.text()
            _LOGGER.debug("HTTP [POST] 400 :> Resp[TEXT]: %s", j)
            he = ApiException(response.status, j)
            raise he

        _LOGGER.debug("HTTP [POST] Resp Error: %s", json.dumps(j))
        msg = j.get("error_message", j.get("result", {}).get("result"))
        if not msg:
            msg = json.dumps(j)
        he = ApiException(response.status, msg)
        raise he

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
