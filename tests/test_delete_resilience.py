"""Tests for the JellyfinClient deletion resilience.

Ensures that when individual items or batches exhaust all retries, the
deletion process continues with remaining items — every item should be
attempted even if some fail.
"""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from jellyfin_cleanup.client import JellyfinClient
from jellyfin_cleanup.database import Database


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


def _row(item_id: str, name: str = "Item") -> MagicMock:
    """Create a fake sqlite3.Row-like object."""
    row = MagicMock()
    row.__getitem__ = lambda self, key: {"id": item_id, "name": name}[key]
    return row


@pytest.fixture()
def db(tmp_path):
    d = Database(str(tmp_path / "test.db"))
    yield d
    d.close()


# ---------------------------------------------------------------------------
# _delete_individually keeps going when one item fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_individual_delete_continues_after_failure(db):
    """Even if one item raises an exception, subsequent items are still attempted."""
    cfg = _make_cfg(max_retries=0)
    jf = JellyfinClient(cfg)

    call_count = 0

    async def mock_delete(url, **kwargs):
        nonlocal call_count
        call_count += 1
        if "item-fail" in url:
            raise httpx.ConnectError("Connection refused")
        resp = httpx.Response(204, request=httpx.Request("DELETE", url))
        return resp

    jf._client = AsyncMock()
    jf._client.delete = mock_delete

    # Seed the DB with three items — the second will fail
    items = [
        {"Id": "item-ok-1", "Name": "Good1", "Type": "Movie", "Path": "/d/a"},
        {"Id": "item-fail", "Name": "Bad", "Type": "Movie", "Path": "/d/b"},
        {"Id": "item-ok-2", "Name": "Good2", "Type": "Movie", "Path": "/d/c"},
    ]
    db.upsert_items(items, "2024-01-01T00:00:00+00:00")
    rows = db.get_pending_targets(["/d"])

    await jf._delete_individually(db, rows)

    # All three items should have been attempted
    assert call_count == 3

    stats = db.stats()
    assert stats.get("deleted", 0) == 2
    assert stats.get("failed", 0) == 1

    await jf.close()


# ---------------------------------------------------------------------------
# delete_batch catches unexpected errors so other batches keep going
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_delete_continues_after_batch_failure(db):
    """If one batch raises an unexpected exception, other batches still complete."""
    cfg = _make_cfg(max_retries=0, delete_batch_size=1, delete_concurrency=5)
    jf = JellyfinClient(cfg)

    async def mock_delete(url, **kwargs):
        params = kwargs.get("params", {})
        ids = params.get("ids", "")
        if "item-fail" in ids:
            raise httpx.ConnectError("Connection refused")
        resp = httpx.Response(204, request=httpx.Request("DELETE", url))
        return resp

    jf._client = AsyncMock()
    jf._client.delete = mock_delete

    items = [
        {"Id": "item-ok-1", "Name": "Good1", "Type": "Movie", "Path": "/d/a"},
        {"Id": "item-fail", "Name": "Bad", "Type": "Movie", "Path": "/d/b"},
        {"Id": "item-ok-2", "Name": "Good2", "Type": "Movie", "Path": "/d/c"},
    ]
    db.upsert_items(items, "2024-01-01T00:00:00+00:00")
    rows = db.get_pending_targets(["/d"])

    await jf.delete_targets(db, rows)

    stats = db.stats()
    assert stats.get("deleted", 0) == 2
    assert stats.get("failed", 0) == 1

    await jf.close()


# ---------------------------------------------------------------------------
# All items are attempted even if every single one fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_all_items_attempted_even_when_all_fail(db):
    """Every item should be attempted even if they all fail."""
    cfg = _make_cfg(max_retries=0, delete_batch_size=2, delete_concurrency=5)
    jf = JellyfinClient(cfg)

    call_count = 0

    async def mock_delete(url, **kwargs):
        nonlocal call_count
        call_count += 1
        raise httpx.ConnectError("Connection refused")

    jf._client = AsyncMock()
    jf._client.delete = mock_delete

    items = [
        {"Id": f"item-{i}", "Name": f"Item{i}", "Type": "Movie", "Path": f"/d/{i}"}
        for i in range(5)
    ]
    db.upsert_items(items, "2024-01-01T00:00:00+00:00")
    rows = db.get_pending_targets(["/d"])

    await jf.delete_targets(db, rows)

    stats = db.stats()
    # All items should be marked as failed, none should remain pending
    assert stats.get("failed", 0) == 5
    assert stats.get("pending", 0) == 0

    await jf.close()
