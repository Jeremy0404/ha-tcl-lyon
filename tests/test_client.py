"""Tests for the TclLyonClient transport layer.

Uses a hand-rolled fake aiohttp session (no network, no test server) to exercise
the status/error mapping and the request shape.
"""

from __future__ import annotations

import aiohttp
import pytest

from custom_components.tcl_lyon import api
from custom_components.tcl_lyon.api import (
    REQUEST_MAX_ATTEMPTS,
    TclLyonAuthError,
    TclLyonClient,
    TclLyonConnectionError,
)
from custom_components.tcl_lyon.const import (
    SIRI_ESTIMATED_TIMETABLES_URL,
    SIRI_SITUATION_EXCHANGE_URL,
)


class FakeResponse:
    def __init__(self, *, status=200, json_data=None, body=b"", json_error=None):
        self.status = status
        self._json_data = json_data
        self._body = body
        self._json_error = json_error

    def raise_for_status(self):
        if self.status >= 400:
            raise aiohttp.ClientResponseError(
                request_info=None, history=(), status=self.status, message="error"
            )

    async def json(self, *, content_type=None):
        if self._json_error is not None:
            raise self._json_error
        return self._json_data

    async def read(self):
        return self._body


class FakeGetCM:
    """Mimics aiohttp's request context manager; IO 'happens' on __aenter__."""

    def __init__(self, response=None, raise_on_enter=None):
        self._response = response
        self._raise_on_enter = raise_on_enter

    async def __aenter__(self):
        if self._raise_on_enter is not None:
            raise self._raise_on_enter
        return self._response

    async def __aexit__(self, *exc):
        return False


class FakeSession:
    def __init__(self, response=None, raise_on_enter=None):
        self._response = response
        self._raise_on_enter = raise_on_enter
        self.calls: list[dict] = []

    def get(self, url, *, params=None, auth=None, timeout=None):
        self.calls.append({"url": url, "params": params, "auth": auth, "timeout": timeout})
        return FakeGetCM(self._response, self._raise_on_enter)


def make_client(session) -> TclLyonClient:
    return TclLyonClient(session, "user@example.com", "secret")


async def test_fetch_estimated_timetables_returns_payload_and_passes_lineref():
    payload = {"Siri": {"ServiceDelivery": {}}}
    session = FakeSession(FakeResponse(json_data=payload))
    client = make_client(session)

    result = await client.async_fetch_estimated_timetables("ActIV:Line::T2:SYTRAL")

    assert result is payload
    call = session.calls[0]
    assert call["url"] == SIRI_ESTIMATED_TIMETABLES_URL
    assert call["params"] == {"LineRef": "ActIV:Line::T2:SYTRAL"}
    assert call["auth"] == aiohttp.BasicAuth("user@example.com", "secret")


async def test_fetch_situation_exchange_hits_bulk_endpoint():
    session = FakeSession(FakeResponse(json_data={"ok": True}))
    client = make_client(session)

    await client.async_fetch_situation_exchange()

    assert session.calls[0]["url"] == SIRI_SITUATION_EXCHANGE_URL
    assert session.calls[0]["params"] is None


async def test_validate_credentials_ok():
    session = FakeSession(FakeResponse(json_data={}))
    client = make_client(session)
    assert await client.async_validate_credentials() is None


async def test_401_raises_auth_error():
    session = FakeSession(FakeResponse(status=401))
    client = make_client(session)
    with pytest.raises(TclLyonAuthError):
        await client.async_validate_credentials()


async def test_server_error_raises_connection_error():
    session = FakeSession(FakeResponse(status=500))
    client = make_client(session)
    with pytest.raises(TclLyonConnectionError):
        await client.async_fetch_situation_exchange()


async def test_timeout_raises_connection_error():
    session = FakeSession(raise_on_enter=TimeoutError())
    client = make_client(session)
    with pytest.raises(TclLyonConnectionError):
        await client.async_fetch_situation_exchange()


async def test_client_error_raises_connection_error():
    session = FakeSession(raise_on_enter=aiohttp.ClientConnectionError("boom"))
    client = make_client(session)
    with pytest.raises(TclLyonConnectionError):
        await client.async_fetch_situation_exchange()


async def test_invalid_json_raises_connection_error():
    session = FakeSession(FakeResponse(json_error=ValueError("not json")))
    client = make_client(session)
    with pytest.raises(TclLyonConnectionError):
        await client.async_fetch_situation_exchange()


async def test_download_gtfs_writes_body(tmp_path):
    session = FakeSession(FakeResponse(body=b"PK\x03\x04zip-bytes"))
    client = make_client(session)
    dest = tmp_path / "gtfs.zip"

    returned = await client.async_download_gtfs(dest)

    assert returned == dest
    assert dest.read_bytes() == b"PK\x03\x04zip-bytes"


class SequenceSession:
    """Hands back a different outcome per .get() call, for retry tests.

    Each outcome is either a FakeResponse (returned) or an Exception (raised on
    enter, like a timeout or connection drop).
    """

    def __init__(self, outcomes):
        self._outcomes = list(outcomes)
        self.calls = 0

    def get(self, url, *, params=None, auth=None, timeout=None):
        self.calls += 1
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            return FakeGetCM(raise_on_enter=outcome)
        return FakeGetCM(outcome)


@pytest.fixture
def no_sleep(monkeypatch):
    """Skip the real backoff sleep and record the requested delays."""
    delays: list[float] = []

    async def fake_sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(api.asyncio, "sleep", fake_sleep)
    return delays


async def test_retries_transient_failure_then_succeeds(no_sleep):
    payload = {"ok": True}
    session = SequenceSession([TimeoutError(), FakeResponse(json_data=payload)])
    client = make_client(session)

    result = await client.async_fetch_situation_exchange()

    assert result == payload
    assert session.calls == 2  # one failure, one success
    assert no_sleep == [1.0]  # backed off once between the two attempts


async def test_retries_5xx_then_succeeds(no_sleep):
    payload = {"ok": True}
    session = SequenceSession([FakeResponse(status=503), FakeResponse(json_data=payload)])
    client = make_client(session)

    assert await client.async_fetch_situation_exchange() == payload
    assert session.calls == 2


async def test_gives_up_after_max_attempts_with_growing_backoff(no_sleep):
    session = SequenceSession([TimeoutError()] * REQUEST_MAX_ATTEMPTS)
    client = make_client(session)

    with pytest.raises(TclLyonConnectionError):
        await client.async_fetch_situation_exchange()

    assert session.calls == REQUEST_MAX_ATTEMPTS
    # One sleep fewer than attempts, doubling each time.
    assert no_sleep == [1.0, 2.0]


async def test_auth_error_is_not_retried(no_sleep):
    session = SequenceSession([FakeResponse(status=401), FakeResponse(json_data={})])
    client = make_client(session)

    with pytest.raises(TclLyonAuthError):
        await client.async_fetch_situation_exchange()

    assert session.calls == 1  # failed fast, no retry, no second outcome consumed
    assert no_sleep == []


async def test_permanent_4xx_is_not_retried(no_sleep):
    session = SequenceSession([FakeResponse(status=404), FakeResponse(json_data={})])
    client = make_client(session)

    with pytest.raises(TclLyonConnectionError):
        await client.async_fetch_situation_exchange()

    assert session.calls == 1
    assert no_sleep == []
