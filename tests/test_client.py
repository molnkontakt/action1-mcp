"""Client behaviour: auth, retries, pagination, and the read-only guard."""

from __future__ import annotations

import httpx
import pytest

from action1_mcp.client import Action1Error, WriteNotAllowedError
from tests.conftest import make_client, token_response


def test_token_is_fetched_once_and_reused() -> None:
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path.endswith("/oauth2/token"):
            token_calls += 1
            return token_response()
        assert request.headers["Authorization"] == "Bearer jwt-1"
        return httpx.Response(200, json={"ok": True})

    client = make_client(handler)
    client.get("/Me")
    client.get("/Me")

    assert token_calls == 1


def test_token_request_is_form_encoded_with_credentials() -> None:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            seen["content_type"] = request.headers["Content-Type"]
            seen["body"] = request.content.decode()
            return token_response()
        return httpx.Response(200, json={})

    make_client(handler).get("/Me")

    assert seen["content_type"] == "application/x-www-form-urlencoded"
    assert "client_id=cid" in seen["body"]
    assert "client_secret=secret" in seen["body"]


def test_401_triggers_exactly_one_refresh_then_surfaces() -> None:
    token_calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal token_calls
        if request.url.path.endswith("/oauth2/token"):
            token_calls += 1
            return token_response()
        return httpx.Response(401, json={"developer_message": "expired"})

    with pytest.raises(Action1Error) as excinfo:
        make_client(handler).get("/Me")

    assert excinfo.value.status == 401
    assert token_calls == 2  # initial + one forced refresh, not a loop


def test_429_is_retried_using_retry_after() -> None:
    attempts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        attempts += 1
        if attempts == 1:
            return httpx.Response(
                429,
                json={
                    "type": "Error",
                    "status": 429,
                    "developer_message": "Request rate limit exceeded.",
                    "details": {"retry_after": 0},
                },
            )
        return httpx.Response(200, json={"ok": True})

    assert make_client(handler).get("/Me") == {"ok": True}
    assert attempts == 2


def test_429_gives_up_after_max_retries() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        return httpx.Response(429, json={"details": {"retry_after": 0}})

    with pytest.raises(Action1Error) as excinfo:
        make_client(handler, max_429_retries=1).get("/Me")

    assert excinfo.value.status == 429


def test_error_body_developer_message_is_surfaced() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        return httpx.Response(403, json={"developer_message": "no access to organization"})

    with pytest.raises(Action1Error) as excinfo:
        make_client(handler).get("/organizations/x")

    assert "no access to organization" in str(excinfo.value)
    assert excinfo.value.status == 403


def test_non_get_is_blocked_in_read_only_mode() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no request should reach the API")

    with pytest.raises(WriteNotAllowedError):
        make_client(handler).request("POST", "/policies/instances/org-1", json={})


def test_non_get_is_allowed_when_writes_are_enabled() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        assert request.method == "POST"
        return httpx.Response(200, json={"created": True})

    client = make_client(handler, allow_write=True)
    assert client.request("POST", "/anything", json={}) == {"created": True}


def test_none_valued_params_are_dropped() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        seen.append(str(request.url.query.decode()))
        return httpx.Response(200, json={})

    make_client(handler).get("/updates/org-1", security_severity=None, approval_status="new")

    assert seen == ["approval_status=new"]


def test_empty_body_returns_empty_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        return httpx.Response(204)

    assert make_client(handler).get("/Me") == {}


# --- pagination -----------------------------------------------------------


def test_paged_walks_every_page(page_handler) -> None:
    handler = page_handler([[{"id": 1}, {"id": 2}], [{"id": 3}]])
    result = make_client(handler).get_paged("/endpoints/managed/org-1")

    assert [i["id"] for i in result["items"]] == [1, 2, 3]
    assert result["returned"] == 3
    assert result["truncated"] is False


def test_paged_stops_at_max_items_and_flags_truncation(page_handler) -> None:
    handler = page_handler([[{"id": 1}, {"id": 2}], [{"id": 3}, {"id": 4}]], total_items=4)
    result = make_client(handler).get_paged("/endpoints/managed/org-1", max_items=3)

    assert result["returned"] == 3
    assert result["truncated"] is True
    assert result["total_items"] == 4


def test_exact_cap_is_not_reported_as_truncated_when_nothing_remains(page_handler) -> None:
    handler = page_handler([[{"id": 1}, {"id": 2}]], total_items=2)
    result = make_client(handler).get_paged("/endpoints/managed/org-1", max_items=2)

    assert result["returned"] == 2
    assert result["truncated"] is False


def test_paged_follows_next_page_on_the_same_host() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        seen.append(str(request.url))
        if "page2" in str(request.url):
            return httpx.Response(200, json={"items": [{"id": 3}]})
        return httpx.Response(
            200,
            json={
                "items": [{"id": 1}, {"id": 2}],
                "next_page": "https://app.action1.com/api/3.0/endpoints/managed/org-1?page2=1&limit=2",
            },
        )

    result = make_client(handler).get_paged("/endpoints/managed/org-1")

    assert [i["id"] for i in result["items"]] == [1, 2, 3]
    assert any("page2" in url for url in seen)


def test_paged_ignores_next_page_pointing_at_another_host() -> None:
    """A next_page off our own API root must never be followed — we send the bearer with it."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        seen.append(str(request.url.host))
        offset = int(request.url.params.get("from", 0))
        if offset:
            return httpx.Response(200, json={"items": []})
        return httpx.Response(
            200,
            json={
                "items": [{"id": 1}, {"id": 2}],
                "next_page": "https://evil.example.com/api/3.0/endpoints",
            },
        )

    make_client(handler).get_paged("/endpoints/managed/org-1")

    assert set(seen) == {"app.action1.com"}


def test_paged_handles_a_bare_list_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth2/token"):
            return token_response()
        return httpx.Response(200, json=[{"id": 1}])

    result = make_client(handler).get_paged("/scripts/all")

    assert result["items"] == [{"id": 1}]
    assert result["truncated"] is False


def test_total_items_string_is_coerced_to_int(page_handler) -> None:
    handler = page_handler([[{"id": 1}]], total_items=1)
    result = make_client(handler).get_paged("/organizations")

    assert result["total_items"] == 1
