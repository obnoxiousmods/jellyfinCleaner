# jellyfinCleaner

A command-line tool to find and delete Jellyfin library items by path prefix — useful when you have moved, renamed, or removed drives and need to clean up stale entries that Jellyfin still tracks.

## Features

- **Async scraping** — fetches your entire Jellyfin library in parallel pages and caches results in a local SQLite database.
- **SQLite cache** — avoids re-scraping on every run; prompts you to re-use cached data or refresh it.
- **Bulk deletion** — sends batched `DELETE /Items?ids=…` requests with configurable concurrency and batch size; falls back to per-item deletion on 404 responses.
- **Retry with backoff** — exponential backoff with jitter on transient errors (429 / 5xx / timeouts).
- **Dry-run mode** — preview what would be deleted without touching anything.
- **Resumable** — items that failed to delete are marked `failed` in the DB and will be retried automatically on the next run.

## Requirements

- Python ≥ 3.11
- A Jellyfin server with API access

## Installation

### Quick install with pip

```bash
pip install .
```

### Quick install with uv (in a virtual environment)

```bash
uv venv
source .venv/bin/activate   # Linux/macOS
uv pip install .
```

This installs the `jellyfin-cleanup` command.

> **📖 For detailed installation instructions** (multiple methods, troubleshooting, and more) see [INSTALLATION/INSTALLATION.md](INSTALLATION/INSTALLATION.md).

## Quick Start

```bash
# Set your API key once (or pass --api-key on every run)
export JELLYFIN_API_KEY="your_api_key_here"

# Preview items under a path (dry-run, no changes made)
jellyfin-cleanup --dry-run /mnt/old-drive/movies

# Delete items (will prompt for confirmation)
jellyfin-cleanup /mnt/old-drive/movies

# Delete items from multiple paths without confirmation prompts
jellyfin-cleanup --yes /mnt/old-drive/movies /mnt/old-drive/shows

# Run against a remote Jellyfin instance
jellyfin-cleanup --url http://jellyfin.home:8096 --api-key abc123 /mnt/old-drive
```

## Usage

```
usage: jellyfin_cleanup [-h] [--target-path PATH] [--url URL] [--api-key KEY]
                        [--db FILE] [--page-size N] [--fetch-concurrency N]
                        [--delete-concurrency N] [--delete-batch-size N]
                        [--max-retries N] [--retry-backoff-base SECS]
                        [--retry-backoff-max SECS] [--timeout-connect SECS]
                        [--timeout-read SECS] [--timeout-write SECS]
                        [--timeout-pool SECS] [--force-rescrape] [--no-rescrape]
                        [--yes] [--dry-run] [--badData] [--verbose]
                        [PATH ...]
```

### Key options

| Option | Default | Description |
|---|---|---|
| `PATH …` (positional) | — | One or more path prefixes to target |
| `-t`, `--target-path PATH` | — | Path prefix (repeatable, merged with positional) |
| `-u`, `--url URL` | `http://127.0.0.1:8096` | Jellyfin base URL |
| `-k`, `--api-key KEY` | `JELLYFIN_API_KEY` env | Jellyfin API key |
| `--db FILE` | `jellyfin_cleanup.db` | SQLite cache file |
| `--page-size N` | `500` | Items per fetch page |
| `--fetch-concurrency N` | `3` | Parallel page-fetch requests |
| `--delete-concurrency N` | `5` | Parallel bulk-delete requests |
| `--delete-batch-size N` | `50` | Items per bulk-delete API call |
| `--max-retries N` | `5` | Max retries per request |
| `--force-rescrape` | `False` | Re-scrape even if cache exists |
| `--no-rescrape` | `False` | Always use cached data |
| `--yes`, `-y` | `False` | Skip delete confirmation prompt |
| `--dry-run` | `False` | Preview without deleting |
| `--badData` | `False` | Ignore path filters and target entries with bad metadata (missing season/episode values or missing media versions) |
| `--verbose`, `-v` | `False` | Enable DEBUG logging |

## Development

```bash
# Install with dev extras
pip install -e ".[dev]"

# Run tests
pytest -v

# Lint
ruff check .
```

## How It Works

1. **Connectivity check** — verifies the server is reachable and the API key is valid.
2. **Scrape** — pages through `GET /Items` (including path + metadata fields) and stores every item in a local SQLite database with its `delete_status = 'pending'`.
3. **Target matching** — either queries the DB by `path` prefix (default) or, with `--badData`, finds entries with invalid episode/season metadata or no media versions.
4. **Preview** — prints a grouped summary of matching items.
5. **Delete** — sends concurrent batched `DELETE /Items?ids=…` requests; records each outcome (`deleted`, `not_found`, or `failed`) back to the DB.
6. **Summary** — prints final DB statistics and warns if any items remain `failed`.

## License

GPL-3.0 — see [LICENSE](LICENSE).
