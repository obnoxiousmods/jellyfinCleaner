"""SQLite database layer for caching scraped Jellyfin items."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime


class Database:
    """Thin OOP wrapper around a SQLite database that stores Jellyfin items."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS items (
                id                  TEXT PRIMARY KEY,
                name                TEXT NOT NULL,
                type                TEXT,
                path                TEXT,
                scraped_at          TEXT NOT NULL,
                delete_status       TEXT DEFAULT 'pending',
                -- 'pending' | 'deleted' | 'not_found' | 'failed'
                delete_attempted_at TEXT,
                delete_error        TEXT
            )
        """)
        self._conn.commit()

    # -- context-manager support ------------------------------------------

    def __enter__(self) -> Database:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- low-level helpers ------------------------------------------------

    @property
    def connection(self) -> sqlite3.Connection:
        """Expose the raw connection for advanced use / tests."""
        return self._conn

    @contextmanager
    def _cursor(self):
        cur = self._conn.cursor()
        try:
            yield cur
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            cur.close()

    # -- public API -------------------------------------------------------

    def upsert_items(self, items: list[dict], scraped_at: str) -> None:
        with self._cursor() as cur:
            cur.executemany(
                """
                INSERT INTO items (id, name, type, path, scraped_at)
                VALUES (:id, :name, :type, :path, :scraped_at)
                ON CONFLICT(id) DO UPDATE SET
                    name       = excluded.name,
                    type       = excluded.type,
                    path       = excluded.path,
                    scraped_at = excluded.scraped_at
                """,
                [
                    {
                        "id": item["Id"],
                        "name": item.get("Name", ""),
                        "type": item.get("Type", ""),
                        "path": item.get("Path", ""),
                        "scraped_at": scraped_at,
                    }
                    for item in items
                ],
            )

    def get_pending_targets(
        self,
        target_paths: list[str],
    ) -> list[sqlite3.Row]:
        """Return items under any of the target paths that still need deletion."""
        if not target_paths:
            return []
        placeholders = " OR ".join("path LIKE ? || '%'" for _ in target_paths)
        query = f"""
            SELECT id, name, type, path
            FROM   items
            WHERE  ({placeholders})
              AND  delete_status IN ('pending', 'failed')
            ORDER BY path, type, name
        """
        return self._conn.execute(query, target_paths).fetchall()

    def mark_deleted(self, item_ids: list[str]) -> None:
        now = datetime.now(UTC).isoformat()
        with self._cursor() as cur:
            cur.executemany(
                "UPDATE items SET delete_status='deleted', delete_attempted_at=? WHERE id=?",
                [(now, iid) for iid in item_ids],
            )

    def mark_not_found(self, item_ids: list[str]) -> None:
        now = datetime.now(UTC).isoformat()
        with self._cursor() as cur:
            cur.executemany(
                "UPDATE items SET delete_status='not_found', delete_attempted_at=? WHERE id=?",
                [(now, iid) for iid in item_ids],
            )

    def mark_failed(self, item_ids: list[str], error: str) -> None:
        now = datetime.now(UTC).isoformat()
        with self._cursor() as cur:
            cur.executemany(
                """UPDATE items
                   SET delete_status='failed', delete_attempted_at=?, delete_error=?
                   WHERE id=?""",
                [(now, error, iid) for iid in item_ids],
            )

    def stats(self) -> dict:
        rows = self._conn.execute(
            "SELECT delete_status, COUNT(*) AS n FROM items GROUP BY delete_status"
        ).fetchall()
        return {r["delete_status"]: r["n"] for r in rows}

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]

    def close(self) -> None:
        self._conn.close()


# ---------------------------------------------------------------------------
# Backward-compatible free functions (delegate to a Database instance)
# ---------------------------------------------------------------------------


def db_connect(path: str) -> sqlite3.Connection:
    """Open *and initialise* a SQLite database, returning the raw connection.

    This is kept for backward compatibility with existing code and tests that
    work directly with :class:`sqlite3.Connection` objects.
    """
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            type                TEXT,
            path                TEXT,
            scraped_at          TEXT NOT NULL,
            delete_status       TEXT DEFAULT 'pending',
            -- 'pending' | 'deleted' | 'not_found' | 'failed'
            delete_attempted_at TEXT,
            delete_error        TEXT
        )
    """)
    conn.commit()
    return conn


def upsert_items(conn: sqlite3.Connection, items: list[dict], scraped_at: str) -> None:
    db = Database.__new__(Database)
    db._conn = conn
    db.upsert_items(items, scraped_at)


def get_pending_targets(
    conn: sqlite3.Connection,
    target_paths: list[str],
) -> list[sqlite3.Row]:
    db = Database.__new__(Database)
    db._conn = conn
    return db.get_pending_targets(target_paths)


def mark_deleted(conn: sqlite3.Connection, item_ids: list[str]) -> None:
    db = Database.__new__(Database)
    db._conn = conn
    db.mark_deleted(item_ids)


def mark_not_found(conn: sqlite3.Connection, item_ids: list[str]) -> None:
    db = Database.__new__(Database)
    db._conn = conn
    db.mark_not_found(item_ids)


def mark_failed(conn: sqlite3.Connection, item_ids: list[str], error: str) -> None:
    db = Database.__new__(Database)
    db._conn = conn
    db.mark_failed(item_ids, error)


def db_stats(conn: sqlite3.Connection) -> dict:
    db = Database.__new__(Database)
    db._conn = conn
    return db.stats()
