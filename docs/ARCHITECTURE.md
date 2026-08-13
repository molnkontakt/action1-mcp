# Architecture

## Layout

```
src/action1_mcp/
  config.py      # env → Settings; regions; limits. Touches no network.
  client.py      # OAuth2, rate limiting, 429 retry, pagination, errors
  app.py         # the FastMCP instance (+ optional bearer auth)
  server.py      # CLI: stdio | http
  tools/
    read.py      # 21 read tools — the only tier that exists today
```

`app.py` is separate from `server.py` so tool modules can import the FastMCP
instance without a cycle: `server` imports `tools`, `tools` import `app`.

## The client carries the hard parts

Everything awkward about the Action1 API is solved once, in `client.py`, so tool
functions stay four lines long.

**Tokens.** `POST /oauth2/token` with form-encoded client credentials returns a
JWT with `expires_in: 3600`. It is refreshed 120 s before nominal expiry, and once
more — exactly once — if a call returns 401 anyway, which happens when a
credential is rotated server-side mid-token.

**Rate limiting.** Action1 documents a soft ceiling of 30 requests/minute counted
*per enterprise* across every endpoint. That budget is shared with every other
integration hitting the same tenant, so the client throttles locally at 25/min
through a sliding window rather than discovering the limit through 429s. When one
arrives regardless, `details.retry_after` from the response body is honoured
first, `Retry-After` second, and exponential backoff (2 s, 4 s, 8 s) only when
neither is usable.

**Pagination.** List responses come wrapped in Action1's `ResultPage` envelope:

```json
{"type": "ResultPage", "items": [...], "total_items": "1432",
 "limit": "50", "next_page": "...", "prev_page": "..."}
```

`get_paged` walks it via `from`/`limit`, preferring the server's `next_page` URL
when present — but only after checking that URL points back at our own configured
API root. We attach the bearer token to every hop, so blindly following an
absolute URL from a response body would turn pagination into an SSRF primitive
that leaks the token. Off-host `next_page` values fall back to offset arithmetic.

Counters arrive as strings (`"total_items": "1432"`) and are coerced defensively.

**Caps are visible.** Every walk stops at `ACTION1_MAX_ITEMS` (default 1000) and
sets `truncated: true`. A silently truncated list reads to a model as a complete
one, and "no endpoint is missing that patch" is a materially different claim from
"none of the first 1000 were".

## Write tiers (v2, not implemented)

v1 is read-only on purpose: Action1 writes deploy software and run scripts on
endpoints. The structure is in place so adding writes is additive rather than a
rewrite:

- `tools/read.py` — free, no confirmation. **Exists.**
- `tools/write_safe.py` — creates drafts and inert objects only; audit-logged.
- `tools/write_critical.py` — requires `confirm=True`. The first call without it
  returns a preview of exactly what would change, so a human sees the blast
  radius before authorizing it. Optional idempotency key for replay safety.

`ACTION1_ALLOW_WRITE` already gates non-GET at the client layer, so a write tier
cannot reach the API until it is deliberately switched on. Tier modules register
themselves by being imported in `server.main()`.

The same three-tier split is used by [odoo-mcp](https://github.com/molnkontakt/odoo-mcp).

## Testing

No test touches the network. `tests/conftest.py` builds an `Action1Client` over an
`httpx.MockTransport`, so token exchange, 401 refresh, 429 retry, pagination,
truncation and the off-host `next_page` guard are all asserted against a scripted
API. The rate limiter is set to 1000/min in tests so nothing sleeps.
