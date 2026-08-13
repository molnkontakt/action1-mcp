"""Regression tests for defects found by review and confirmed against a live tenant.

Each test names the failure it prevents. The pagination cases exist because the
worst outcome for a security-reporting tool is a partial answer that looks complete.
"""

from __future__ import annotations

import httpx
import pytest

from action1_mcp.client import Action1Error
from tests.conftest import make_client, token_response


def _envelope(items: list[dict[str, object]], **extra: object) -> dict[str, object]:
    return {"type": "ResultPage", "items": items, **extra}


# --- ACTION1_MAX_ITEMS is a ceiling, not a default ------------------------


def test_caller_limit_cannot_exceed_the_configured_ceiling() -> None:
    """A model asking for limit=100000 must not walk the tenant's whole rate budget."""
    pages_served = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal pages_served
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        pages_served += 1
        offset = int(request.url.params["from"])
        return httpx.Response(
            200,
            json=_envelope(
                [{"id": offset + i} for i in range(2)], total_items="1000000"
            ),
        )

    client = make_client(handler, max_items=6, page_size=2)
    result = client.get_paged("/endpoints/managed/org-1", max_items=100_000)

    assert result["returned"] == 6
    assert result["truncated"] is True
    assert pages_served == 3


def test_caller_limit_below_the_ceiling_is_respected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        offset = int(request.url.params["from"])
        return httpx.Response(200, json=_envelope([{"id": offset + i} for i in range(2)]))

    result = make_client(handler, max_items=100, page_size=2).get_paged(
        "/endpoints/managed/org-1", max_items=4
    )

    assert result["returned"] == 4


def test_negative_limit_cannot_produce_a_negative_slice() -> None:
    """`batch[:-1]` would silently drop one item and call the rest truncated."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        return httpx.Response(200, json=_envelope([{"id": i} for i in range(50)]))

    result = make_client(handler).get_paged("/vulnerabilities/org-1", max_items=-1)

    assert result["returned"] == 1
    assert result["items"] == [{"id": 0}]


# --- truncated must err towards true --------------------------------------


def test_exact_cap_without_a_server_total_reports_truncated() -> None:
    """Several live endpoints omit total_items; absence is not proof of completeness."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        return httpx.Response(200, json=_envelope([{"id": 1}, {"id": 2}]))

    result = make_client(handler, page_size=2).get_paged("/apps/org-1/data", max_items=2)

    assert result["returned"] == 2
    assert result["total_items"] is None
    assert result["truncated"] is True


def test_exact_cap_with_a_total_that_matches_is_not_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        return httpx.Response(200, json=_envelope([{"id": 1}, {"id": 2}], total_items="2"))

    result = make_client(handler, page_size=2).get_paged("/organizations", max_items=2)

    assert result["truncated"] is False


def test_short_page_with_a_larger_total_is_flagged_truncated() -> None:
    """Action1 honours our limit, so this needs a server-side total to be detectable."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        return httpx.Response(200, json=_envelope([{"id": 1}], total_items="99"))

    result = make_client(handler, page_size=10).get_paged("/updates/org-1", max_items=50)

    assert result["returned"] == 1
    assert result["truncated"] is True


def test_short_page_that_exhausts_the_data_is_not_truncated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        return httpx.Response(200, json=_envelope([{"id": 1}], total_items="1"))

    result = make_client(handler, page_size=10).get_paged("/updates/org-1", max_items=50)

    assert result["truncated"] is False


def test_bare_list_response_respects_the_cap() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        return httpx.Response(200, json=[{"id": i} for i in range(500)])

    result = make_client(handler).get_paged("/scripts/all", max_items=10)

    assert result["returned"] == 10
    assert result["truncated"] is True


# --- redirects and retry delays -------------------------------------------


def test_a_redirect_is_an_error_not_an_empty_result() -> None:
    """A 302 used to fall through to `return {}` — an empty CVE list reads as "all clear"."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        return httpx.Response(302, headers={"Location": "https://elsewhere.example.com/"})

    with pytest.raises(Action1Error) as excinfo:
        make_client(handler).get("/vulnerabilities/org-1")

    assert excinfo.value.status == 302


def test_redirects_are_not_followed() -> None:
    assert make_client(lambda r: httpx.Response(200))._http.follow_redirects is False


@pytest.mark.parametrize("header", ["-1", "inf", "nan"])
def test_malformed_retry_after_header_falls_back_to_backoff(header: str) -> None:
    """`time.sleep(-1)` raises ValueError; `inf` hangs the worker forever."""
    from action1_mcp.client import _retry_after_seconds

    response = httpx.Response(429, headers={"Retry-After": header})

    assert _retry_after_seconds(response) is None


def test_absurd_retry_after_is_capped() -> None:
    from action1_mcp.client import MAX_RETRY_AFTER_SECONDS, _retry_after_seconds

    response = httpx.Response(429, json={"details": {"retry_after": 86400}})

    assert _retry_after_seconds(response) == MAX_RETRY_AFTER_SECONDS


def test_zero_retry_after_is_still_honoured() -> None:
    """Deliberate: 0 means retry now, and the retry count still bounds the loop."""
    from action1_mcp.client import _retry_after_seconds

    assert _retry_after_seconds(httpx.Response(429, json={"details": {"retry_after": 0}})) == 0
