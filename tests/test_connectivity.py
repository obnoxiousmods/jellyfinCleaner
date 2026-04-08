"""Tests for JellyfinClient.check_connectivity error handling.

Covers the scenarios described in the original issue: non-JSON responses,
wrong ports, non-Jellyfin servers, various HTTP error codes, and
connection / timeout failures.
"""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock

import httpx
import pytest

from jellyfin_cleanup.client import JellyfinClient


def _make_cfg(**overrides) -> argparse.Namespace:
    defaults = {
        "url": "http://localhost:8096",
        "api_key": "test-key",
        "timeout_connect": 5.0,
        "timeout_read": 60.0,
        "timeout_write": 10.0,
        "timeout_pool": 10.0,
        "fetch_concurrency": 3,
        "delete_concurrency": 5,
        "delete_batch_size": 2,
        "max_retries": 0,
        "retry_backoff_base": 0.0,
        "retry_backoff_max": 0.0,
        "page_size": 500,
        "db": ":memory:",
    }
    defaults.update(overrides)
    return argparse.Namespace(**defaults)


def _json_response(data: dict, status_code: int = 200, url: str = "") -> httpx.Response:
    """Build a httpx.Response that returns *data* as JSON."""
    import json

    content = json.dumps(data).encode()
    return httpx.Response(
        status_code,
        content=content,
        headers={"content-type": "application/json"},
        request=httpx.Request("GET", url or "http://localhost:8096/test"),
    )


def _text_response(text: str, status_code: int = 200, url: str = "") -> httpx.Response:
    """Build a httpx.Response with a plain-text body."""
    return httpx.Response(
        status_code,
        content=text.encode(),
        headers={"content-type": "text/html"},
        request=httpx.Request("GET", url or "http://localhost:8096/test"),
    )


def _empty_response(status_code: int = 200, url: str = "") -> httpx.Response:
    """Build a httpx.Response with an empty body."""
    return httpx.Response(
        status_code,
        content=b"",
        request=httpx.Request("GET", url or "http://localhost:8096/test"),
    )


# ---------------------------------------------------------------------------
# Successful connectivity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_connectivity_success():
    """Happy path: both endpoints return valid JSON and 200."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    async def mock_get(url, **kwargs):
        if "/System/Info/Public" in url:
            return _json_response({"ServerName": "TestServer", "Version": "10.9.0"})
        if "/System/Info" in url:
            return _json_response({"Id": "abc123"})
        return _text_response("not found", 404)

    jf._client = AsyncMock()
    jf._client.get = mock_get

    # Should NOT raise or call sys.exit
    await jf.check_connectivity()
    await jf.close()


# ---------------------------------------------------------------------------
# /System/Info/Public returns empty body (the original reported bug)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_connectivity_empty_json_body():
    """Empty response body on /System/Info/Public → JSONDecodeError handled."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    async def mock_get(url, **kwargs):
        return _empty_response(200)

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()


# ---------------------------------------------------------------------------
# /System/Info/Public returns non-JSON (e.g. HTML from wrong service)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_connectivity_html_response():
    """HTML response on /System/Info/Public → handled gracefully."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    async def mock_get(url, **kwargs):
        return _text_response("<html><body>Welcome</body></html>", 200)

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()


# ---------------------------------------------------------------------------
# /System/Info/Public returns non-200 status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_connectivity_public_endpoint_404():
    """404 from /System/Info/Public → exit with helpful message."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    async def mock_get(url, **kwargs):
        return _text_response("Not Found", 404)

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()


@pytest.mark.asyncio
async def test_check_connectivity_public_endpoint_500():
    """500 from /System/Info/Public → exit."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    async def mock_get(url, **kwargs):
        return _text_response("Internal Server Error", 500)

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()


# ---------------------------------------------------------------------------
# Connection and timeout errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_connectivity_connect_error():
    """Connection refused → exit."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    async def mock_get(url, **kwargs):
        raise httpx.ConnectError("Connection refused")

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()


@pytest.mark.asyncio
async def test_check_connectivity_timeout():
    """Timeout → exit."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    async def mock_get(url, **kwargs):
        raise httpx.ReadTimeout("Timed out")

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()


# ---------------------------------------------------------------------------
# /System/Info auth failures (API key validation)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_connectivity_auth_401():
    """401 on /System/Info → API key rejected."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    async def mock_get(url, **kwargs):
        if "/System/Info/Public" in url:
            return _json_response({"ServerName": "Test", "Version": "10.9.0"})
        return _text_response("Unauthorized", 401)

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()


@pytest.mark.asyncio
async def test_check_connectivity_auth_403():
    """403 on /System/Info → API key lacks permission."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    async def mock_get(url, **kwargs):
        if "/System/Info/Public" in url:
            return _json_response({"ServerName": "Test", "Version": "10.9.0"})
        return _text_response("Forbidden", 403)

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()


@pytest.mark.asyncio
async def test_check_connectivity_auth_404():
    """404 on /System/Info → endpoint missing."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    async def mock_get(url, **kwargs):
        if "/System/Info/Public" in url:
            return _json_response({"ServerName": "Test", "Version": "10.9.0"})
        return _text_response("Not Found", 404)

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()


@pytest.mark.asyncio
async def test_check_connectivity_auth_unexpected_status():
    """Unexpected status on /System/Info → exit with details."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    async def mock_get(url, **kwargs):
        if "/System/Info/Public" in url:
            return _json_response({"ServerName": "Test", "Version": "10.9.0"})
        return _text_response("Bad Gateway", 502)

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()


# ---------------------------------------------------------------------------
# Connection/timeout error on the authenticated /System/Info endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_connectivity_auth_connect_error():
    """ConnectError on /System/Info (after /Public succeeds) → exit."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _json_response({"ServerName": "Test", "Version": "10.9.0"})
        raise httpx.ConnectError("Connection refused")

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()


@pytest.mark.asyncio
async def test_check_connectivity_auth_timeout():
    """Timeout on /System/Info (after /Public succeeds) → exit."""
    cfg = _make_cfg()
    jf = JellyfinClient(cfg)

    call_count = 0

    async def mock_get(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _json_response({"ServerName": "Test", "Version": "10.9.0"})
        raise httpx.ReadTimeout("Timed out")

    jf._client = AsyncMock()
    jf._client.get = mock_get

    with pytest.raises(SystemExit) as exc_info:
        await jf.check_connectivity()
    assert exc_info.value.code == 1

    await jf.close()
