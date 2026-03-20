import argparse
import asyncio
import logging
import os
import random
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import UTC, datetime

import httpx

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jellyfin_cleanup",
        description="Find and delete Jellyfin library items by path prefix.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "paths",
        nargs="*",
        metavar="PATH",
        help="One or more path prefixes to target (e.g. /10TB2/tvShows /10TB/movies). "
        "Overrides --target-path.",
    )
    parser.add_argument(
        "--target-path",
        "-t",
        action="append",
        dest="target_paths",
        metavar="PATH",
        default=[],
        help="Path prefix to target. Can be supplied multiple times.",
    )
    parser.add_argument(
        "--url",
        "-u",
        default="http://127.0.0.1:8096",
        metavar="URL",
        help="Jellyfin base URL.",
    )
    parser.add_argument(
        "--api-key",
        "-k",
        default=None,
        metavar="KEY",
        help="Jellyfin API key. Falls back to JELLYFIN_API_KEY env var.",
    )
    parser.add_argument(
        "--db",
        default="jellyfin_cleanup.db",
        metavar="FILE",
        help="SQLite database path for caching scraped items.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=500,
        metavar="N",
        help="Items per fetch page.",
    )
    parser.add_argument(
        "--fetch-concurrency",
        type=int,
        default=3,
        metavar="N",
        help="Simultaneous page-fetch requests.",
    )
    parser.add_argument(
        "--delete-concurrency",
        type=int,
        default=5,
        metavar="N",
        help="Simultaneous bulk-delete requests.",
    )
    parser.add_argument(
        "--delete-batch-size",
        type=int,
        default=50,
        metavar="N",
        help="Items per bulk-delete API call.",
    )
    parser.add_argument(
        "--max-retries",
        type=int,
        default=5,
        metavar="N",
        help="Max retries per request before giving up.",
    )
    parser.add_argument(
        "--retry-backoff-base",
        type=float,
        default=1.0,
        metavar="SECS",
        help="Initial retry backoff in seconds (doubles + jitter each attempt).",
    )
    parser.add_argument(
        "--retry-backoff-max",
        type=float,
        default=30.0,
        metavar="SECS",
        help="Maximum retry backoff ceiling in seconds.",
    )
    parser.add_argument(
        "--timeout-connect",
        type=float,
        default=5.0,
        metavar="SECS",
    )
    parser.add_argument(
        "--timeout-read",
        type=float,
        default=60.0,
        metavar="SECS",
    )
    parser.add_argument(
        "--timeout-write",
        type=float,
        default=10.0,
        metavar="SECS",
    )
    parser.add_argument(
        "--timeout-pool",
        type=float,
        default=10.0,
        metavar="SECS",
    )
    parser.add_argument(
        "--force-rescrape",
        action="store_true",
        default=False,
        help="Re-scrape Jellyfin even if cached data exists (skip the prompt).",
    )
    parser.add_argument(
        "--no-rescrape",
        action="store_true",
        default=False,
        help="Never re-scrape; always use cached data (skip the prompt).",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        default=False,
        help="Skip the delete confirmation prompt.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Preview matched items without deleting anything.",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable DEBUG logging.",
    )

    args = parser.parse_args()

    # Merge positional paths + --target-path into one deduplicated list
    all_paths = list(dict.fromkeys(args.paths + args.target_paths))
    args.target_paths = all_paths

    # API key: argparse → env var
    if not args.api_key:
        args.api_key = os.environ.get("JELLYFIN_API_KEY")
    if not args.api_key:
        parser.error(
            "API key required — pass --api-key or set JELLYFIN_API_KEY env var."
        )

    return args


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )


log = logging.getLogger("jf_cleanup")

# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------


def db_connect(path: str) -> sqlite3.Connection:
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


@contextmanager
def db_cursor(conn: sqlite3.Connection):
    cur = conn.cursor()
    try:
        yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()


def upsert_items(conn: sqlite3.Connection, items: list[dict], scraped_at: str) -> None:
    with db_cursor(conn) as cur:
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
    conn: sqlite3.Connection,
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
    return conn.execute(query, target_paths).fetchall()


def mark_deleted(conn: sqlite3.Connection, item_ids: list[str]) -> None:
    now = datetime.now(UTC).isoformat()
    with db_cursor(conn) as cur:
        cur.executemany(
            "UPDATE items SET delete_status='deleted', delete_attempted_at=? WHERE id=?",
            [(now, iid) for iid in item_ids],
        )


def mark_not_found(conn: sqlite3.Connection, item_ids: list[str]) -> None:
    now = datetime.now(UTC).isoformat()
    with db_cursor(conn) as cur:
        cur.executemany(
            "UPDATE items SET delete_status='not_found', delete_attempted_at=? WHERE id=?",
            [(now, iid) for iid in item_ids],
        )


def mark_failed(conn: sqlite3.Connection, item_ids: list[str], error: str) -> None:
    now = datetime.now(UTC).isoformat()
    with db_cursor(conn) as cur:
        cur.executemany(
            """UPDATE items
               SET delete_status='failed', delete_attempted_at=?, delete_error=?
               WHERE id=?""",
            [(now, error, iid) for iid in item_ids],
        )


def db_stats(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT delete_status, COUNT(*) AS n FROM items GROUP BY delete_status"
    ).fetchall()
    return {r["delete_status"]: r["n"] for r in rows}


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


async def request_with_retry(
    fn,
    *args,
    cfg: argparse.Namespace,
    skip_retry_on: set[int] | None = None,
    **kwargs,
) -> httpx.Response:
    skip_retry_on = skip_retry_on or set()
    last_exc: Exception | None = None
    retryable = {429, 500, 502, 503, 504}

    for attempt in range(1, cfg.max_retries + 2):
        try:
            response = await fn(*args, **kwargs)
            if response.status_code in skip_retry_on:
                return response
            if response.status_code in retryable:
                raise httpx.HTTPStatusError(
                    f"Retryable {response.status_code}",
                    request=response.request,
                    response=response,
                )
            response.raise_for_status()
            return response

        except (
            httpx.TransportError,
            httpx.TimeoutException,
            httpx.HTTPStatusError,
        ) as exc:
            last_exc = exc
            if attempt > cfg.max_retries:
                break
            ceiling = min(
                cfg.retry_backoff_base * (2 ** (attempt - 1)),
                cfg.retry_backoff_max,
            )
            delay = random.uniform(0, ceiling)
            log.warning(
                "Attempt %d/%d failed (%s: %s) — retrying in %.1fs",
                attempt,
                cfg.max_retries + 1,
                type(exc).__name__,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    raise RuntimeError(
        f"Request failed after {cfg.max_retries + 1} attempts: {last_exc}"
    ) from last_exc


# ---------------------------------------------------------------------------
# Connectivity check
# ---------------------------------------------------------------------------


async def check_connectivity(
    client: httpx.AsyncClient, cfg: argparse.Namespace
) -> None:
    log.info("Checking connectivity → %s", cfg.url)
    try:
        r = await client.get("/System/Info/Public", timeout=cfg.timeout_connect)
        info = r.json()
        log.info(
            "Server: %s  version %s",
            info.get("ServerName"),
            info.get("Version"),
        )
    except httpx.ConnectError as exc:
        log.error("Connection failed: %s", exc)
        sys.exit(1)
    except httpx.TimeoutException:
        log.error("Timeout reaching server")
        sys.exit(1)

    r = await client.get("/System/Info", timeout=cfg.timeout_connect)
    if r.status_code == 200:
        log.info("API key valid ✓")
    elif r.status_code == 401:
        log.error("AUTH FAILED — API key rejected")
        sys.exit(1)
    elif r.status_code == 404:
        log.error("404 on /System/Info — check base URL / path prefix")
        sys.exit(1)
    else:
        log.error("Unexpected auth response %s: %s", r.status_code, r.text)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Scraping
# ---------------------------------------------------------------------------


async def fetch_page(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    start_index: int,
    cfg: argparse.Namespace,
) -> tuple[list, int]:
    async with sem:
        r = await request_with_retry(
            client.get,
            "/Items",
            cfg=cfg,
            params={
                "Recursive": "true",
                "Fields": "Path",
                "Limit": cfg.page_size,
                "StartIndex": start_index,
            },
        )
        data = r.json()
        return data.get("Items", []), data.get("TotalRecordCount", 0)


async def scrape_all_items(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    cfg: argparse.Namespace,
) -> int:
    scraped_at = datetime.now(UTC).isoformat()

    log.info("Scraping page 0 to get total record count...")
    first_items, total = await fetch_page(client, asyncio.Semaphore(1), 0, cfg)
    upsert_items(conn, first_items, scraped_at)

    if total <= cfg.page_size:
        log.info("Scraped %d / %d items", len(first_items), total)
        return total

    offsets = list(range(cfg.page_size, total, cfg.page_size))
    sem = asyncio.Semaphore(cfg.fetch_concurrency)
    completed = 0
    t0 = time.monotonic()

    async def fetch_and_store(offset: int) -> None:
        nonlocal completed
        items, _ = await fetch_page(client, sem, offset, cfg)
        upsert_items(conn, items, scraped_at)
        completed += 1
        elapsed = time.monotonic() - t0
        done = min(cfg.page_size + completed * cfg.page_size, total)
        rate = done / elapsed if elapsed > 0 else 0
        print(f"  {done:>6}/{total}  ({rate:.0f} items/s)   ", end="\r", flush=True)

    log.info(
        "Total %d items across %d pages — fetching (concurrency=%d)...",
        total,
        1 + len(offsets),
        cfg.fetch_concurrency,
    )
    await asyncio.gather(*[fetch_and_store(off) for off in offsets])
    print()
    log.info("Scrape complete — %d items stored in %s", total, cfg.db)
    return total


# ---------------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------------


async def _delete_individually(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    batch: list[sqlite3.Row],
    cfg: argparse.Namespace,
) -> None:
    for row in batch:
        item_id = row["id"]
        name = row["name"]
        try:
            r = await request_with_retry(
                client.delete,
                f"/Items/{item_id}",
                cfg=cfg,
                skip_retry_on={404},
            )
            if r.status_code == 404:
                log.info("[NOT FOUND — already gone]  %s (%s)", name, item_id)
                mark_not_found(conn, [item_id])
            else:
                log.info("[DELETED]  %s (%s)", name, item_id)
                mark_deleted(conn, [item_id])
        except RuntimeError as exc:
            log.error("[FAILED]  %s (%s) — %s", name, item_id, exc)
            mark_failed(conn, [item_id], str(exc))


async def delete_batch(
    client: httpx.AsyncClient,
    sem: asyncio.Semaphore,
    conn: sqlite3.Connection,
    batch: list[sqlite3.Row],
    cfg: argparse.Namespace,
) -> None:
    ids = [row["id"] for row in batch]

    async with sem:
        try:
            r = await request_with_retry(
                client.delete,
                "/Items",
                cfg=cfg,
                params={"ids": ",".join(ids)},
                skip_retry_on={404},
            )
            if r.status_code == 404:
                # One or more items missing — fall back to per-item so each
                # gets its own status recorded correctly
                log.debug(
                    "Bulk 404 on batch of %d — falling back to individual deletes",
                    len(ids),
                )
                await _delete_individually(client, conn, batch, cfg)
            else:
                log.info("[DELETED batch of %d]", len(ids))
                mark_deleted(conn, ids)

        except RuntimeError as exc:
            log.error("[FAILED batch of %d] %s", len(ids), exc)
            mark_failed(conn, ids, str(exc))


async def delete_targets(
    client: httpx.AsyncClient,
    conn: sqlite3.Connection,
    targets: list[sqlite3.Row],
    cfg: argparse.Namespace,
) -> None:
    batches = [
        targets[i : i + cfg.delete_batch_size]
        for i in range(0, len(targets), cfg.delete_batch_size)
    ]
    sem = asyncio.Semaphore(cfg.delete_concurrency)
    log.info(
        "Deleting %d items in %d batches (batch_size=%d, concurrency=%d)...",
        len(targets),
        len(batches),
        cfg.delete_batch_size,
        cfg.delete_concurrency,
    )
    await asyncio.gather(*[delete_batch(client, sem, conn, b, cfg) for b in batches])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(cfg: argparse.Namespace) -> None:
    conn = db_connect(cfg.db)

    timeout = httpx.Timeout(
        connect=cfg.timeout_connect,
        read=cfg.timeout_read,
        write=cfg.timeout_write,
        pool=cfg.timeout_pool,
    )
    max_conn = max(cfg.fetch_concurrency, cfg.delete_concurrency) + 2

    async with httpx.AsyncClient(
        base_url=cfg.url,
        headers={"X-Emby-Token": cfg.api_key, "Content-Type": "application/json"},
        timeout=timeout,
        limits=httpx.Limits(
            max_connections=max_conn,
            max_keepalive_connections=max_conn - 2,
        ),
    ) as client:
        await check_connectivity(client, cfg)

        # --- Scrape decision ---
        existing = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        do_scrape: bool

        if cfg.force_rescrape:
            do_scrape = True
        elif cfg.no_rescrape:
            do_scrape = False
        elif existing > 0:
            ans = (
                input(
                    f"\n{existing} items cached in {cfg.db}. "
                    "Re-scrape from Jellyfin? [y/N]: "
                )
                .strip()
                .lower()
            )
            do_scrape = ans == "y"
        else:
            do_scrape = True

        if do_scrape:
            await scrape_all_items(client, conn, cfg)
        else:
            log.info("Using cached data from %s", cfg.db)

        # --- Find targets across all requested paths ---
        if not cfg.target_paths:
            log.error(
                "No target paths specified. "
                "Pass paths as positional arguments or use --target-path."
            )
            sys.exit(1)

        log.info("Target paths: %s", cfg.target_paths)
        targets = get_pending_targets(conn, cfg.target_paths)
        log.info("Found %d pending/failed items across all target paths", len(targets))

        if not targets:
            log.info("Nothing to delete.")
            log.info("DB stats: %s", db_stats(conn))
            return

        # --- Preview ---
        print(f"\nItems to delete ({len(targets)}):")
        # Group by path prefix for readability
        for tp in cfg.target_paths:
            group = [r for r in targets if r["path"].startswith(tp)]
            if group:
                print(f"\n  [{tp}]  ({len(group)} items)")
                for row in group[:10]:
                    print(f"    [{row['type']:12}] {row['name']}")
                if len(group) > 10:
                    print(f"    ... and {len(group) - 10} more")

        if cfg.dry_run:
            log.info("Dry run — nothing deleted.")
            return

        # --- Confirm ---
        if not cfg.yes:
            confirm = (
                input(f"\nDelete all {len(targets)} items? (yes/no): ").strip().lower()
            )
            if confirm != "yes":
                log.info("Aborted.")
                return

        # --- Delete ---
        await delete_targets(client, conn, targets, cfg)

        # --- Summary ---
        stats = db_stats(conn)
        log.info("Done. DB stats: %s", stats)
        if stats.get("failed", 0):
            log.warning(
                "%d items still marked 'failed' — re-run to retry "
                "(cached data will be reused, no re-scrape needed).",
                stats["failed"],
            )


def main_sync() -> None:
    """Entry point for the ``jellyfin-cleanup`` console script."""
    cfg = parse_args()
    setup_logging(cfg.verbose)
    asyncio.run(main(cfg))


if __name__ == "__main__":
    main_sync()
