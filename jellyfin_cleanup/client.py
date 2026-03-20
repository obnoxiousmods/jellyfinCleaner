"""Async Jellyfin API client — connectivity checks, scraping, and deletion."""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sqlite3
import sys
import time
from datetime import UTC, datetime

import httpx

from .database import Database

log = logging.getLogger("jf_cleanup")


class JellyfinClient:
    """High-level async client for interacting with a Jellyfin server."""

    def __init__(self, cfg: argparse.Namespace) -> None:
        self.cfg = cfg
        timeout = httpx.Timeout(
            connect=cfg.timeout_connect,
            read=cfg.timeout_read,
            write=cfg.timeout_write,
            pool=cfg.timeout_pool,
        )
        max_conn = max(cfg.fetch_concurrency, cfg.delete_concurrency) + 2
        self._client = httpx.AsyncClient(
            base_url=cfg.url,
            headers={
                "X-Emby-Token": cfg.api_key,
                "Content-Type": "application/json",
            },
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=max_conn,
                max_keepalive_connections=max_conn - 2,
            ),
        )

    # -- context-manager support ------------------------------------------

    async def __aenter__(self) -> JellyfinClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    # -- retry logic ------------------------------------------------------

    async def request_with_retry(
        self,
        fn,
        *args,
        skip_retry_on: set[int] | None = None,
        **kwargs,
    ) -> httpx.Response:
        skip_retry_on = skip_retry_on or set()
        last_exc: Exception | None = None
        retryable = {429, 500, 502, 503, 504}
        cfg = self.cfg

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

    # -- connectivity -----------------------------------------------------

    async def check_connectivity(self) -> None:
        cfg = self.cfg
        client = self._client

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

    # -- scraping ---------------------------------------------------------

    async def fetch_page(
        self,
        sem: asyncio.Semaphore,
        start_index: int,
    ) -> tuple[list, int]:
        async with sem:
            r = await self.request_with_retry(
                self._client.get,
                "/Items",
                params={
                    "Recursive": "true",
                    "Fields": "Path",
                    "Limit": self.cfg.page_size,
                    "StartIndex": start_index,
                },
            )
            data = r.json()
            return data.get("Items", []), data.get("TotalRecordCount", 0)

    async def scrape_all_items(self, db: Database) -> int:
        cfg = self.cfg
        scraped_at = datetime.now(UTC).isoformat()

        log.info("Scraping page 0 to get total record count...")
        first_items, total = await self.fetch_page(asyncio.Semaphore(1), 0)
        db.upsert_items(first_items, scraped_at)

        if total <= cfg.page_size:
            log.info("Scraped %d / %d items", len(first_items), total)
            return total

        offsets = list(range(cfg.page_size, total, cfg.page_size))
        sem = asyncio.Semaphore(cfg.fetch_concurrency)
        completed = 0
        t0 = time.monotonic()

        async def fetch_and_store(offset: int) -> None:
            nonlocal completed
            items, _ = await self.fetch_page(sem, offset)
            db.upsert_items(items, scraped_at)
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

    # -- deletion ---------------------------------------------------------

    async def _delete_individually(
        self,
        db: Database,
        batch: list[sqlite3.Row],
    ) -> None:
        for row in batch:
            item_id = row["id"]
            name = row["name"]
            try:
                r = await self.request_with_retry(
                    self._client.delete,
                    f"/Items/{item_id}",
                    skip_retry_on={404},
                )
                if r.status_code == 404:
                    log.info("[NOT FOUND — already gone]  %s (%s)", name, item_id)
                    db.mark_not_found([item_id])
                else:
                    log.info("[DELETED]  %s (%s)", name, item_id)
                    db.mark_deleted([item_id])
            except Exception as exc:
                log.error("[FAILED]  %s (%s) — %s", name, item_id, exc)
                db.mark_failed([item_id], str(exc))

    async def delete_batch(
        self,
        sem: asyncio.Semaphore,
        db: Database,
        batch: list[sqlite3.Row],
    ) -> None:
        ids = [row["id"] for row in batch]

        async with sem:
            try:
                r = await self.request_with_retry(
                    self._client.delete,
                    "/Items",
                    params={"ids": ",".join(ids)},
                    skip_retry_on={404},
                )
                if r.status_code == 404:
                    log.debug(
                        "Bulk 404 on batch of %d — falling back to individual deletes",
                        len(ids),
                    )
                    await self._delete_individually(db, batch)
                else:
                    log.info("[DELETED batch of %d]", len(ids))
                    db.mark_deleted(ids)

            except Exception as exc:
                log.error("[FAILED batch of %d] %s", len(ids), exc)
                db.mark_failed(ids, str(exc))

    async def delete_targets(
        self,
        db: Database,
        targets: list[sqlite3.Row],
    ) -> None:
        cfg = self.cfg
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
        results = await asyncio.gather(
            *[self.delete_batch(sem, db, b) for b in batches],
            return_exceptions=True,
        )
        # Log any truly unexpected errors that slipped through
        for i, result in enumerate(results):
            if isinstance(result, BaseException):
                log.error("Unexpected error in batch %d: %s", i, result)
