"""CLI argument parsing for jellyfin-cleanup."""

from __future__ import annotations

import argparse
import os


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
        "--badData",
        "--bad-data",
        dest="bad_data",
        action="store_true",
        default=False,
        help="Target entries with bad metadata (missing season/episode data or no versions).",
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
