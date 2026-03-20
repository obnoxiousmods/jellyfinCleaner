"""Allow ``python -m jellyfin_cleanup``."""

from .core import main_sync

if __name__ == "__main__":
    main_sync()
