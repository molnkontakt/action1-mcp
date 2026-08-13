"""Shared fixtures — an Action1Client wired to a scripted fake API.

No test touches the network; every request is served by an `httpx.MockTransport`
handler the test supplies.
"""

from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from action1_mcp.client import Action1Client
from action1_mcp.config import Settings

BASE = "https://app.action1.com/api/3.0"


def make_settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "client_id": "cid",
        "client_secret": "secret",
        "region": "NorthAmerica",
        "base_url": BASE,
        "default_org_id": "org-1",
        "rate_limit_per_minute": 1000,  # effectively off, so tests never sleep
        "timeout_seconds": 5.0,
        "max_429_retries": 3,
        "page_size": 2,
        "max_items": 100,
        "allow_write": False,
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def token_response() -> httpx.Response:
    return httpx.Response(
        200, json={"access_token": "jwt-1", "expires_in": 3600, "token_type": "bearer"}
    )


def make_client(
    handler: Callable[[httpx.Request], httpx.Response], **setting_overrides: object
) -> Action1Client:
    """Build a client whose HTTP layer is `handler`.

    The handler is responsible for `/oauth2/token` too — several tests assert on
    how often it is called.
    """
    transport = httpx.MockTransport(handler)
    return Action1Client(
        settings=make_settings(**setting_overrides),
        http=httpx.Client(transport=transport),
    )


@pytest.fixture
def page_handler() -> Callable[[list[list[dict[str, object]]], int | None], Callable[..., object]]:
    """Factory for a handler that serves `pages` as a ResultPage sequence."""

    def build(pages: list[list[dict[str, object]]], total_items: int | None = None):
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/oauth2/token"):
                return token_response()
            calls.append(request)
            offset = int(request.url.params.get("from", 0))
            limit = int(request.url.params.get("limit", 2))
            index = offset // limit if limit else 0
            items = pages[index] if index < len(pages) else []
            body: dict[str, object] = {"type": "ResultPage", "items": items}
            if total_items is not None:
                body["total_items"] = str(total_items)
            return httpx.Response(200, json=body)

        handler.calls = calls  # type: ignore[attr-defined]
        return handler

    return build
