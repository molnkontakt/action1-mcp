"""Configuration loading and region resolution."""

from __future__ import annotations

import pytest

from action1_mcp.config import ConfigError, load_settings, resolve_region


def test_regions_resolve_case_insensitively() -> None:
    assert resolve_region("europe") == "https://app.eu.action1.com/api/3.0"
    assert resolve_region("NorthAmerica") == "https://app.action1.com/api/3.0"
    assert resolve_region("NA-2") == "https://app.na-2.action1.com/api/3.0"


def test_unknown_region_names_the_valid_ones() -> None:
    with pytest.raises(ConfigError) as excinfo:
        resolve_region("mars")
    assert "europe" in str(excinfo.value)


def test_missing_credentials_are_reported_together(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ACTION1_CLIENT_ID", raising=False)
    monkeypatch.delenv("ACTION1_CLIENT_SECRET", raising=False)

    with pytest.raises(ConfigError) as excinfo:
        load_settings()

    message = str(excinfo.value)
    assert "ACTION1_CLIENT_ID" in message
    assert "ACTION1_CLIENT_SECRET" in message


def test_defaults_are_conservative(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACTION1_CLIENT_ID", "cid")
    monkeypatch.setenv("ACTION1_CLIENT_SECRET", "secret")
    monkeypatch.delenv("ACTION1_REGION", raising=False)
    monkeypatch.delenv("ACTION1_ALLOW_WRITE", raising=False)
    monkeypatch.delenv("ACTION1_RATE_LIMIT_PER_MINUTE", raising=False)

    settings = load_settings()

    assert settings.base_url == "https://app.action1.com/api/3.0"
    assert settings.allow_write is False
    # Must stay under Action1's documented 30/min per-enterprise ceiling.
    assert settings.rate_limit_per_minute < 30


def test_write_flag_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACTION1_CLIENT_ID", "cid")
    monkeypatch.setenv("ACTION1_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ACTION1_ALLOW_WRITE", "1")

    assert load_settings().allow_write is True


def test_malformed_numeric_setting_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACTION1_CLIENT_ID", "cid")
    monkeypatch.setenv("ACTION1_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ACTION1_PAGE_SIZE", "lots")

    with pytest.raises(ConfigError):
        load_settings()
