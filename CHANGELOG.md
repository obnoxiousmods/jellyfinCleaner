# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0] - 2024-01-01

### Added

- Initial release of `jellyfin_cleanup` CLI tool.
- Async scraping of the full Jellyfin library with configurable page size and concurrency.
- SQLite cache so subsequent runs can skip re-scraping.
- Bulk deletion via `DELETE /Items?ids=…` with per-item fallback on 404.
- Exponential backoff with jitter for retries on transient errors.
- `--dry-run` mode to preview matches without deleting.
- `--force-rescrape` and `--no-rescrape` flags to control cache behaviour non-interactively.
- `--yes` flag to skip the delete confirmation prompt.
- Support for multiple target path prefixes (positional or `--target-path`).
- `jellyfin-cleanup` console script entry point.
- GitHub Actions CI workflow (lint with ruff + pytest on Python 3.11 and 3.12).
