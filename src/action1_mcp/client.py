"""HTTP client for the Action1 REST API v3.0.

Handles the four things every caller would otherwise reimplement:

- **OAuth2 client-credentials tokens** — `POST /oauth2/token` returns a JWT with
  `expires_in: 3600`; it is refreshed shortly before expiry and once more on a 401.
- **Rate limiting** — a local sliding-window throttle keeps us under Action1's
  per-enterprise ceiling, plus honest 429 handling using the `retry_after` the
  API returns.
- **Pagination** — Action1 wraps list responses in a `ResultPage` envelope with
  `items` / `total_items` / `next_page`, paged by `from` and `limit`.
- **Errors** — Action1's error body (`status`, `developer_message`, `user_message`)
  is turned into one exception type rather than leaking raw HTTP.

The client is synchronous and thread-safe; FastMCP may invoke tools from a
worker thread.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from typing import Any
from urllib.parse import urlsplit

import httpx

from action1_mcp.config import Settings, get_settings

# Refresh this many seconds before the token actually expires, so a request never
# leaves with a token that dies in flight.
TOKEN_REFRESH_MARGIN_SECONDS = 120

# Used only when a 429 arrives without a usable `retry_after`: 2 s, 4 s, 8 s.
# Mirrors the fallback in Action1's own documented retry examples.
FALLBACK_RETRY_BASE_SECONDS = 2

# Ceiling on a delay we were *told* to wait. The value is upstream-controlled and we
# sleep on it inside a tool call, so an absurd one (or a proxy-generated `inf`) would
# hang a worker with no way to cancel it — `ACTION1_TIMEOUT_SECONDS` bounds the
# request, not the sleep.
MAX_RETRY_AFTER_SECONDS = 120


class Action1Error(RuntimeError):
    """An Action1 API call failed.

    Attributes:
        status: HTTP status code (0 when the request never got a response).
        developer_message: Action1's `developer_message`, or a fallback.
        path: the request path, for context in the message.
    """

    def __init__(self, status: int, developer_message: str, path: str = "") -> None:
        self.status = status
        self.developer_message = developer_message
        self.path = path
        where = f" ({path})" if path else ""
        super().__init__(f"Action1 API error {status}{where}: {developer_message}")


class WriteNotAllowedError(Action1Error):
    """A non-GET request was attempted while the client is in read-only mode."""

    def __init__(self, method: str, path: str) -> None:
        super().__init__(
            0,
            f"{method} is blocked: this server is read-only. "
            "Set ACTION1_ALLOW_WRITE=1 only once write tools exist and you accept "
            "that Action1 write operations execute code on managed endpoints.",
            path,
        )


class _RateLimiter:
    """Sliding-window throttle, shared by every request including token fetches.

    Action1 counts requests per enterprise across all endpoints, so the budget is
    shared with any other integration hitting the same tenant. Blocking locally is
    cheaper than a 429, which costs a full `retry_after`.
    """

    def __init__(self, max_per_minute: int) -> None:
        self._max = max_per_minute
        self._window: deque[float] = deque()
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                while self._window and now - self._window[0] >= 60.0:
                    self._window.popleft()
                if len(self._window) < self._max:
                    self._window.append(now)
                    return
                sleep_for = 60.0 - (now - self._window[0])
            time.sleep(max(sleep_for, 0.01))


def _error_from_response(response: httpx.Response, path: str) -> Action1Error:
    """Turn an error response into `Action1Error`, tolerating non-JSON bodies."""
    developer_message = response.text.strip()[:500] or response.reason_phrase
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        developer_message = str(
            body.get("developer_message") or body.get("user_message") or developer_message
        )
    return Action1Error(response.status_code, developer_message, path)


def _retry_after_seconds(response: httpx.Response) -> float | None:
    """Read `details.retry_after` from a 429 body, falling back to the header.

    Returns None when neither carries a usable value, which is the caller's cue
    to use exponential backoff instead.
    """
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        details = body.get("details")
        if isinstance(details, dict):
            try:
                value = float(details["retry_after"])
            except (KeyError, TypeError, ValueError):
                value = -1.0
            if math.isfinite(value) and value >= 0:
                return min(value, MAX_RETRY_AFTER_SECONDS)
    header = response.headers.get("Retry-After")
    if header:
        try:
            value = float(header)
        except ValueError:
            # An HTTP-date Retry-After lands here; fall through to backoff.
            return None
        if not math.isfinite(value) or value < 0:
            return None
        return min(value, MAX_RETRY_AFTER_SECONDS)
    return None


def _as_int(value: Any) -> int | None:
    """Coerce Action1's stringly-typed counters (`"total_items": "142"`) to int."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Action1Client:
    """Authenticated, rate-limited client for one Action1 region."""

    def __init__(
        self,
        settings: Settings | None = None,
        http: httpx.Client | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        # follow_redirects stays off deliberately: we attach the bearer to every
        # request, and a redirect we follow blindly would carry it to whatever host
        # the response names. Redirects are surfaced as errors instead — see request().
        self._http = http or httpx.Client(
            timeout=self.settings.timeout_seconds, follow_redirects=False
        )
        self._limiter = _RateLimiter(self.settings.rate_limit_per_minute)
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = threading.Lock()

    # -- authentication ----------------------------------------------------

    def _fetch_token(self) -> None:
        """Exchange client credentials for a JWT. Caller must hold `_token_lock`."""
        url = f"{self.settings.base_url}/oauth2/token"
        self._limiter.acquire()
        response = self._http.post(
            url,
            data={
                "client_id": self.settings.client_id,
                "client_secret": self.settings.client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        if response.status_code != 200:
            raise _error_from_response(response, "/oauth2/token")

        payload = response.json()
        token = payload.get("access_token")
        if not token:
            raise Action1Error(
                response.status_code, "token response contained no access_token", "/oauth2/token"
            )

        expires_in = _as_int(payload.get("expires_in")) or 3600
        self._token = str(token)
        self._token_expires_at = time.monotonic() + max(
            expires_in - TOKEN_REFRESH_MARGIN_SECONDS, 30
        )

    def _authorization(self, force_refresh: bool = False) -> str:
        with self._token_lock:
            if force_refresh or self._token is None or time.monotonic() >= self._token_expires_at:
                self._fetch_token()
            return f"Bearer {self._token}"

    # -- requests ----------------------------------------------------------

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """Perform one API call and return the decoded JSON body.

        Args:
            method: HTTP verb. Anything but GET requires `ACTION1_ALLOW_WRITE=1`.
            path: path relative to the regional API root, e.g. `/organizations`.
            params: query parameters; `None` values are dropped.
            json: request body for write calls.

        Raises:
            WriteNotAllowedError: on a non-GET call in read-only mode.
            Action1Error: on any 4xx/5xx that survives the retry policy.
        """
        method = method.upper()
        if method != "GET" and not self.settings.allow_write:
            raise WriteNotAllowedError(method, path)

        url = path if path.startswith("http") else f"{self.settings.base_url}{path}"
        clean_params = {k: v for k, v in (params or {}).items() if v is not None}

        refreshed = False
        retries_429 = 0
        while True:
            self._limiter.acquire()
            response = self._http.request(
                method,
                url,
                params=clean_params or None,
                json=json,
                headers={
                    "Authorization": self._authorization(),
                    "Accept": "application/json",
                },
            )

            if response.status_code == 401 and not refreshed:
                # The token may have been revoked or rotated server-side before
                # its nominal expiry. One forced refresh, then give up.
                refreshed = True
                self._authorization(force_refresh=True)
                continue

            if response.status_code == 429:
                if retries_429 >= self.settings.max_429_retries:
                    raise _error_from_response(response, path)
                delay = _retry_after_seconds(response)
                if delay is None:
                    delay = float(FALLBACK_RETRY_BASE_SECONDS * (2**retries_429))
                retries_429 += 1
                time.sleep(delay)
                continue

            if response.status_code >= 300:
                # 3xx included on purpose. We do not follow redirects, so a 302 would
                # otherwise fall through to the empty-body branch below and return {},
                # which `get_paged` turns into an empty item list. "No CVEs detected"
                # is the worst possible way for a security tool to fail.
                raise _error_from_response(response, path)

            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError:
                raise Action1Error(
                    response.status_code, "response body was not valid JSON", path
                ) from None

    def get(self, path: str, **params: Any) -> Any:
        """GET a single (non-paged) resource."""
        return self.request("GET", path, params=params)

    def get_object(self, path: str, **params: Any) -> dict[str, Any]:
        """GET a single resource that must decode to a JSON object.

        Raises:
            Action1Error: if the response was a list or scalar instead, which
                means the endpoint is paged and the caller wanted `get_paged`.
        """
        result = self.get(path, **params)
        if not isinstance(result, dict):
            raise Action1Error(
                0, f"expected a JSON object, got {type(result).__name__}", path
            )
        return result

    # -- pagination --------------------------------------------------------

    def _next_url(self, page: dict[str, Any]) -> str | None:
        """Return `next_page` only when it points back at our own API root.

        Following an absolute URL handed to us by a response is how a compromised
        or misconfigured upstream turns pagination into an SSRF primitive and a
        credential leak — we send the bearer token with every hop. Anything off
        our configured host is ignored and the caller falls back to `from`/`limit`.
        """
        raw = page.get("next_page")
        if not raw or not isinstance(raw, str):
            return None
        base = urlsplit(self.settings.base_url)
        candidate = urlsplit(raw)
        if (candidate.scheme, candidate.netloc) != (base.scheme, base.netloc):
            return None
        if not candidate.path.startswith(base.path):
            return None
        return raw

    def get_paged(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        max_items: int | None = None,
        page_size: int | None = None,
    ) -> dict[str, Any]:
        """Walk a `ResultPage` and collect its items.

        Stops at `max_items`, bounded by `ACTION1_MAX_ITEMS`, and says so in the
        result rather than silently returning a partial list.

        Returns:
            `{items, returned, total_items, truncated}` — `total_items` is the
            server-side total when Action1 reports one, else None. `truncated` is
            True when more data may exist beyond what was returned. It errs towards
            True: over-reporting tells the caller to narrow the query, while
            under-reporting makes a partial answer look complete.
        """
        # `ACTION1_MAX_ITEMS` is a ceiling, not merely a default. A caller may ask for
        # less; asking for more cannot raise it. Without the clamp one tool call with
        # limit=100000 could sit on the tenant's shared 30 req/min budget for twenty
        # minutes, since the rate limiter blocks rather than erroring. The max(1, ...)
        # also floors a negative limit, which would otherwise reach `batch[:-1]`.
        if max_items is None:
            cap = self.settings.max_items
        else:
            cap = max(1, min(max_items, self.settings.max_items))
        size = page_size or self.settings.page_size
        size = min(size, cap)

        items: list[Any] = []
        total_items: int | None = None
        url: str | None = path
        query: dict[str, Any] | None = {**(params or {}), "from": 0, "limit": size}
        truncated = False

        while url is not None:
            page = self.request("GET", url, params=query)
            if not isinstance(page, dict):
                # A non-envelope response (single object or bare list) is not paged.
                if isinstance(page, list):
                    return {
                        "items": page[:cap],
                        "returned": min(len(page), cap),
                        "total_items": None,
                        "truncated": len(page) > cap,
                    }
                return {"items": [page], "returned": 1, "total_items": None, "truncated": False}

            batch = page.get("items")
            if not isinstance(batch, list):
                batch = []
            if total_items is None:
                total_items = _as_int(page.get("total_items"))

            room = max(cap - len(items), 0)
            if len(batch) > room:
                items.extend(batch[:room])
                truncated = True
                break
            items.extend(batch)

            if len(items) >= cap:
                # We stopped because the cap filled, not because the data ran out.
                # Only a server-side total can prove nothing remains — and several
                # Action1 endpoints (/apps/{org}/data, /policies/instances/{org},
                # /reports/all) return no `total_items` at all, so absence of a
                # counter must not be read as "that was everything".
                truncated = total_items is None or total_items > len(items)
                break
            if len(batch) < size:
                # A short page means the data ran out: Action1 honours the requested
                # limit rather than clamping it (verified against a live tenant —
                # limit=500 returned 500 rows of 1543). A server-side total still
                # overrules that inference where one is available.
                truncated = bool(total_items is not None and total_items > len(items))
                break

            next_url = self._next_url(page)
            if next_url:
                url, query = next_url, None
            else:
                url = path
                query = {**(params or {}), "from": len(items), "limit": size}

        return {
            "items": items,
            "returned": len(items),
            "total_items": total_items,
            "truncated": truncated,
        }

    def close(self) -> None:
        self._http.close()


_client: Action1Client | None = None
_client_lock = threading.Lock()


def get_client() -> Action1Client:
    """Process-wide client, created on first use."""
    global _client
    with _client_lock:
        if _client is None:
            _client = Action1Client()
        return _client


def resolve_org_id(org_id: str | None) -> str:
    """Fall back to `ACTION1_DEFAULT_ORG_ID` when a tool was called without one.

    Raises:
        Action1Error: when neither is available, pointing at the tool that lists
            the valid values.
    """
    resolved = org_id or get_client().settings.default_org_id
    if not resolved:
        raise Action1Error(
            0,
            "No organization specified and ACTION1_DEFAULT_ORG_ID is unset. "
            "Call action1_list_organizations to get one, then pass org_id.",
        )
    return resolved
