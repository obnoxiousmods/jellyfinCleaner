"""Main orchestration logic for jellyfin-cleanup."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .cli import parse_args
from .client import JellyfinClient
from .database import Database

log = logging.getLogger("jf_cleanup")


def setup_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%H:%M:%S",
    )


async def main(cfg: argparse.Namespace) -> None:
    db = Database(cfg.db)

    async with JellyfinClient(cfg) as jf:
        await jf.check_connectivity()

        # --- Scrape decision ---
        existing = db.count()
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
            await jf.scrape_all_items(db)
        else:
            log.info("Using cached data from %s", cfg.db)

        # --- Find targets ---
        if not cfg.bad_data and not cfg.target_paths:
            log.error(
                "No target paths specified. "
                "Pass paths as positional arguments or use --target-path."
            )
            sys.exit(1)

        if cfg.bad_data:
            if cfg.target_paths:
                log.info("--badData/--bad-data set; ignoring provided target paths.")
            targets = db.get_bad_data_targets()
            log.info("Found %d pending/failed items with bad metadata", len(targets))
        else:
            log.info("Target paths: %s", cfg.target_paths)
            targets = db.get_pending_targets(cfg.target_paths)
            log.info("Found %d pending/failed items across all target paths", len(targets))

        if not targets:
            log.info("Nothing to delete.")
            log.info("DB stats: %s", db.stats())
            return

        # --- Preview ---
        print(f"\nItems to delete ({len(targets)}):")
        if cfg.bad_data:
            for row in targets[:30]:
                print(
                    f"    [{row['type']:12}] {row['name']} ({row['bad_reason']})"
                    f" - {row['path'] or '<no path>'}"
                )
            if len(targets) > 30:
                print(f"    ... and {len(targets) - 30} more")
        else:
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
        await jf.delete_targets(db, targets)

        # --- Summary ---
        stats = db.stats()
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
    try:
        asyncio.run(main(cfg))
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(130)
