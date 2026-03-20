"""Tests for SQLite helper functions in jellyfin_cleanup."""


import pytest

from jellyfin_cleanup import (
    db_connect,
    db_stats,
    get_pending_targets,
    mark_deleted,
    mark_failed,
    mark_not_found,
    upsert_items,
)

SCRAPED_AT = "2024-01-01T00:00:00+00:00"


@pytest.fixture()
def conn(tmp_path):
    """In-memory SQLite connection for each test."""
    db_path = str(tmp_path / "test.db")
    connection = db_connect(db_path)
    yield connection
    connection.close()


def _make_item(
    item_id: str,
    name: str = "Test",
    type_: str = "Movie",
    path: str = "/data/movies/Test",
) -> dict:
    return {"Id": item_id, "Name": name, "Type": type_, "Path": path}


# ---------------------------------------------------------------------------
# db_connect
# ---------------------------------------------------------------------------


def test_db_connect_creates_table(tmp_path):
    conn = db_connect(str(tmp_path / "fresh.db"))
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = [r[0] for r in tables]
    assert "items" in table_names
    conn.close()


# ---------------------------------------------------------------------------
# upsert_items
# ---------------------------------------------------------------------------


def test_upsert_inserts_new_item(conn):
    upsert_items(conn, [_make_item("abc")], SCRAPED_AT)
    row = conn.execute("SELECT * FROM items WHERE id='abc'").fetchone()
    assert row is not None
    assert row["name"] == "Test"
    assert row["path"] == "/data/movies/Test"
    assert row["delete_status"] == "pending"


def test_upsert_updates_existing_item(conn):
    upsert_items(conn, [_make_item("abc", name="Old Name")], SCRAPED_AT)
    upsert_items(conn, [_make_item("abc", name="New Name")], SCRAPED_AT)
    row = conn.execute("SELECT name FROM items WHERE id='abc'").fetchone()
    assert row["name"] == "New Name"


def test_upsert_multiple_items(conn):
    items = [_make_item(f"id{i}") for i in range(5)]
    upsert_items(conn, items, SCRAPED_AT)
    count = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
    assert count == 5


def test_upsert_missing_optional_fields(conn):
    """Items without Name/Type/Path should not raise."""
    upsert_items(conn, [{"Id": "x1"}], SCRAPED_AT)
    row = conn.execute("SELECT * FROM items WHERE id='x1'").fetchone()
    assert row["name"] == ""
    assert row["type"] == ""
    assert row["path"] == ""


# ---------------------------------------------------------------------------
# get_pending_targets
# ---------------------------------------------------------------------------


def test_get_pending_targets_empty_paths(conn):
    upsert_items(conn, [_make_item("abc")], SCRAPED_AT)
    result = get_pending_targets(conn, [])
    assert result == []


def test_get_pending_targets_matches_prefix(conn):
    upsert_items(
        conn,
        [
            _make_item("a1", path="/mnt/drive1/movies/Film A"),
            _make_item("a2", path="/mnt/drive1/shows/Show B"),
            _make_item("b1", path="/mnt/drive2/movies/Film C"),
        ],
        SCRAPED_AT,
    )
    result = get_pending_targets(conn, ["/mnt/drive1"])
    ids = {r["id"] for r in result}
    assert ids == {"a1", "a2"}


def test_get_pending_targets_multiple_paths(conn):
    upsert_items(
        conn,
        [
            _make_item("a1", path="/drive1/movie"),
            _make_item("b1", path="/drive2/show"),
            _make_item("c1", path="/drive3/other"),
        ],
        SCRAPED_AT,
    )
    result = get_pending_targets(conn, ["/drive1", "/drive2"])
    ids = {r["id"] for r in result}
    assert ids == {"a1", "b1"}


def test_get_pending_targets_excludes_deleted(conn):
    upsert_items(conn, [_make_item("a1", path="/mnt/movies/x")], SCRAPED_AT)
    mark_deleted(conn, ["a1"])
    result = get_pending_targets(conn, ["/mnt/movies"])
    assert result == []


def test_get_pending_targets_includes_failed(conn):
    upsert_items(conn, [_make_item("a1", path="/mnt/movies/x")], SCRAPED_AT)
    mark_failed(conn, ["a1"], "timeout")
    result = get_pending_targets(conn, ["/mnt/movies"])
    assert len(result) == 1


def test_get_pending_targets_excludes_not_found(conn):
    upsert_items(conn, [_make_item("a1", path="/mnt/movies/x")], SCRAPED_AT)
    mark_not_found(conn, ["a1"])
    result = get_pending_targets(conn, ["/mnt/movies"])
    assert result == []


# ---------------------------------------------------------------------------
# mark_deleted / mark_not_found / mark_failed
# ---------------------------------------------------------------------------


def test_mark_deleted(conn):
    upsert_items(conn, [_make_item("d1")], SCRAPED_AT)
    mark_deleted(conn, ["d1"])
    row = conn.execute("SELECT delete_status FROM items WHERE id='d1'").fetchone()
    assert row["delete_status"] == "deleted"


def test_mark_not_found(conn):
    upsert_items(conn, [_make_item("n1")], SCRAPED_AT)
    mark_not_found(conn, ["n1"])
    row = conn.execute("SELECT delete_status FROM items WHERE id='n1'").fetchone()
    assert row["delete_status"] == "not_found"


def test_mark_failed(conn):
    upsert_items(conn, [_make_item("f1")], SCRAPED_AT)
    mark_failed(conn, ["f1"], "some error")
    row = conn.execute(
        "SELECT delete_status, delete_error FROM items WHERE id='f1'"
    ).fetchone()
    assert row["delete_status"] == "failed"
    assert row["delete_error"] == "some error"


def test_mark_deleted_sets_timestamp(conn):
    upsert_items(conn, [_make_item("t1")], SCRAPED_AT)
    mark_deleted(conn, ["t1"])
    row = conn.execute(
        "SELECT delete_attempted_at FROM items WHERE id='t1'"
    ).fetchone()
    assert row["delete_attempted_at"] is not None


# ---------------------------------------------------------------------------
# db_stats
# ---------------------------------------------------------------------------


def test_db_stats_empty(conn):
    assert db_stats(conn) == {}


def test_db_stats_counts(conn):
    items = [_make_item(f"id{i}") for i in range(4)]
    upsert_items(conn, items, SCRAPED_AT)
    mark_deleted(conn, ["id0", "id1"])
    mark_not_found(conn, ["id2"])
    stats = db_stats(conn)
    assert stats["pending"] == 1
    assert stats["deleted"] == 2
    assert stats["not_found"] == 1


def test_db_stats_failed(conn):
    upsert_items(conn, [_make_item("e1")], SCRAPED_AT)
    mark_failed(conn, ["e1"], "err")
    stats = db_stats(conn)
    assert stats.get("failed") == 1
