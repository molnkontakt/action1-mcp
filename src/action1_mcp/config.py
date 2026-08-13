"""Configuration — regions, credentials and runtime limits, all read from the environment.

Nothing here reaches the network. `get_settings()` is cached so the whole
process shares one immutable view of the configuration; tests call
`load_settings()` directly to bypass the cache.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

# Regional API roots. The keys match PSAction1's `Action1.Hosts.ps1` so the same
# region string works in both tools. Lookup is case-insensitive.
REGIONS: dict[str, str] = {
    "northamerica": "https://app.action1.com/api/3.0",
    "northamerica-2": "https://app.na-2.action1.com/api/3.0",
    "na-2": "https://app.na-2.action1.com/api/3.0",
    "europe": "https://app.eu.action1.com/api/3.0",
    "australia": "https://app.au.action1.com/api/3.0",
}

DEFAULT_REGION = "NorthAmerica"

# Action1 documents a soft ceiling of 30 requests/minute counted per *enterprise*
# across every endpoint — so other integrations against the same tenant share the
# budget. We throttle below it locally rather than discovering it through 429s,
# because each 429 costs a full `retry_after` (30 s in Action1's own example).
DEFAULT_RATE_LIMIT_PER_MINUTE = 25

DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_MAX_429_RETRIES = 3

# Action1's own `ResultPage` envelope defaults to 50; 200 is what PSAction1 uses
# for bulk reads and keeps the request count down on large tenants.
DEFAULT_PAGE_SIZE = 200

# Hard ceiling on how many items one tool call will walk. A tenant with 5 000
# endpoints would otherwise blow both the rate limit and the model's context in a
# single `list_endpoints`. Callers are told when this truncates — see
# `Action1Client.get_paged`.
DEFAULT_MAX_ITEMS = 1000


class ConfigError(RuntimeError):
    """Required configuration is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    client_id: str
    client_secret: str
    region: str
    base_url: str
    default_org_id: str | None
    rate_limit_per_minute: int
    timeout_seconds: float
    max_429_retries: int
    page_size: int
    max_items: int
    allow_write: bool


def resolve_region(region: str) -> str:
    """Map a region name to its API root, case-insensitively.

    Raises:
        ConfigError: if the name is not one of `REGIONS`.
    """
    try:
        return REGIONS[region.strip().lower()]
    except KeyError:
        known = ", ".join(sorted({k for k in REGIONS}))
        raise ConfigError(f"Unknown ACTION1_REGION {region!r}. Known regions: {known}") from None


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from None
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {raw!r}") from None
    if value <= 0:
        raise ConfigError(f"{name} must be > 0, got {value}")
    return value


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_settings() -> Settings:
    """Build `Settings` from the environment, uncached.

    Raises:
        ConfigError: if credentials are absent or a numeric setting is malformed.
    """
    client_id = os.environ.get("ACTION1_CLIENT_ID", "").strip()
    client_secret = os.environ.get("ACTION1_CLIENT_SECRET", "").strip()
    missing = [
        name
        for name, value in (
            ("ACTION1_CLIENT_ID", client_id),
            ("ACTION1_CLIENT_SECRET", client_secret),
        )
        if not value
    ]
    if missing:
        raise ConfigError(
            f"Missing required environment variable(s): {', '.join(missing)}. "
            "Generate API credentials in the Action1 console under Configuration → API Credentials."
        )

    region = os.environ.get("ACTION1_REGION", DEFAULT_REGION).strip() or DEFAULT_REGION
    default_org_id = os.environ.get("ACTION1_DEFAULT_ORG_ID", "").strip() or None

    return Settings(
        client_id=client_id,
        client_secret=client_secret,
        region=region,
        base_url=resolve_region(region),
        default_org_id=default_org_id,
        rate_limit_per_minute=_env_int(
            "ACTION1_RATE_LIMIT_PER_MINUTE", DEFAULT_RATE_LIMIT_PER_MINUTE
        ),
        timeout_seconds=_env_float("ACTION1_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS),
        max_429_retries=_env_int("ACTION1_MAX_429_RETRIES", DEFAULT_MAX_429_RETRIES, minimum=0),
        page_size=_env_int("ACTION1_PAGE_SIZE", DEFAULT_PAGE_SIZE),
        max_items=_env_int("ACTION1_MAX_ITEMS", DEFAULT_MAX_ITEMS),
        # v1 ships read-only tools. The flag exists so the client itself — not just
        # the absence of write tools — enforces that promise, and so v2 has a
        # single switch to flip. See docs/ARCHITECTURE.md.
        allow_write=_env_bool("ACTION1_ALLOW_WRITE", False),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide cached settings."""
    return load_settings()
