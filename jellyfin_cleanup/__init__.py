"""jellyfin-cleanup — find and delete Jellyfin library items by path prefix."""

# Public API re-exports so that ``from jellyfin_cleanup import …`` and
# ``import jellyfin_cleanup; jellyfin_cleanup.parse_args()`` keep working
# exactly as before.

from .cli import parse_args
from .client import JellyfinClient
from .core import main, main_sync, setup_logging
from .database import (
    Database,
    db_connect,
    db_stats,
    get_bad_data_targets,
    get_pending_targets,
    mark_deleted,
    mark_failed,
    mark_not_found,
    upsert_items,
)

__all__ = [
    # CLI
    "parse_args",
    # Core
    "main",
    "main_sync",
    "setup_logging",
    # Database (OOP)
    "Database",
    # Database (backward-compat free functions)
    "db_connect",
    "db_stats",
    "get_bad_data_targets",
    "get_pending_targets",
    "mark_deleted",
    "mark_failed",
    "mark_not_found",
    "upsert_items",
    # Client (OOP)
    "JellyfinClient",
]
