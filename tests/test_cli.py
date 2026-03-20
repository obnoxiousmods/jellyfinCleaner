"""Tests for CLI argument parsing in jellyfin_cleanup."""

import os
import sys
from unittest.mock import patch

import pytest

import jellyfin_cleanup


def _parse(args: list[str], env: dict | None = None) -> object:
    """Run parse_args with the given argv and optional env overrides."""
    env_vars = {"JELLYFIN_API_KEY": "test-key"}
    if env:
        env_vars.update(env)
    with patch.object(sys, "argv", ["jellyfin_cleanup"] + args):
        with patch.dict(os.environ, env_vars, clear=False):
            return jellyfin_cleanup.parse_args()


# ---------------------------------------------------------------------------
# API key handling
# ---------------------------------------------------------------------------


def test_api_key_from_flag():
    cfg = _parse(["--api-key", "mykey"], env={"JELLYFIN_API_KEY": ""})
    assert cfg.api_key == "mykey"


def test_api_key_from_env():
    cfg = _parse([], env={"JELLYFIN_API_KEY": "envkey"})
    assert cfg.api_key == "envkey"


def test_api_key_flag_takes_precedence_over_env():
    cfg = _parse(["--api-key", "flagkey"], env={"JELLYFIN_API_KEY": "envkey"})
    assert cfg.api_key == "flagkey"


def test_missing_api_key_exits():
    with patch.object(sys, "argv", ["jellyfin_cleanup"]):
        with patch.dict(os.environ, {"JELLYFIN_API_KEY": ""}, clear=False):
            # Remove key entirely
            env = os.environ.copy()
            env.pop("JELLYFIN_API_KEY", None)
            with patch.dict(os.environ, env, clear=True):
                with pytest.raises(SystemExit):
                    jellyfin_cleanup.parse_args()


# ---------------------------------------------------------------------------
# Path merging
# ---------------------------------------------------------------------------


def test_positional_paths():
    cfg = _parse(["/mnt/drive1", "/mnt/drive2"])
    assert cfg.target_paths == ["/mnt/drive1", "/mnt/drive2"]


def test_target_path_flag():
    cfg = _parse(["--target-path", "/mnt/drive1", "-t", "/mnt/drive2"])
    assert "/mnt/drive1" in cfg.target_paths
    assert "/mnt/drive2" in cfg.target_paths


def test_positional_and_flag_merged():
    cfg = _parse(["/mnt/drive1", "--target-path", "/mnt/drive2"])
    assert set(cfg.target_paths) == {"/mnt/drive1", "/mnt/drive2"}


def test_duplicate_paths_deduplicated():
    cfg = _parse(["/mnt/drive1", "--target-path", "/mnt/drive1"])
    assert cfg.target_paths.count("/mnt/drive1") == 1


def test_no_paths_is_empty():
    cfg = _parse([])
    assert cfg.target_paths == []


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_default_url():
    cfg = _parse([])
    assert cfg.url == "http://127.0.0.1:8096"


def test_custom_url():
    cfg = _parse(["--url", "http://nas:8096"])
    assert cfg.url == "http://nas:8096"


def test_default_page_size():
    cfg = _parse([])
    assert cfg.page_size == 500


def test_default_delete_batch_size():
    cfg = _parse([])
    assert cfg.delete_batch_size == 50


def test_default_flags_false():
    cfg = _parse([])
    assert cfg.dry_run is False
    assert cfg.yes is False
    assert cfg.verbose is False
    assert cfg.force_rescrape is False
    assert cfg.no_rescrape is False


def test_dry_run_flag():
    cfg = _parse(["--dry-run"])
    assert cfg.dry_run is True


def test_yes_flag():
    cfg = _parse(["--yes"])
    assert cfg.yes is True


def test_verbose_short_flag():
    cfg = _parse(["-v"])
    assert cfg.verbose is True


def test_force_rescrape():
    cfg = _parse(["--force-rescrape"])
    assert cfg.force_rescrape is True


def test_no_rescrape():
    cfg = _parse(["--no-rescrape"])
    assert cfg.no_rescrape is True


# ---------------------------------------------------------------------------
# Numeric parameters
# ---------------------------------------------------------------------------


def test_custom_page_size():
    cfg = _parse(["--page-size", "100"])
    assert cfg.page_size == 100


def test_custom_max_retries():
    cfg = _parse(["--max-retries", "3"])
    assert cfg.max_retries == 3


def test_custom_timeouts():
    cfg = _parse(
        [
            "--timeout-connect", "2.5",
            "--timeout-read", "30.0",
            "--timeout-write", "5.0",
            "--timeout-pool", "8.0",
        ]
    )
    assert cfg.timeout_connect == 2.5
    assert cfg.timeout_read == 30.0
    assert cfg.timeout_write == 5.0
    assert cfg.timeout_pool == 8.0
