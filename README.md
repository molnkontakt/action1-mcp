# action1-mcp

MCP server for the [Action1](https://www.action1.com/) RMM REST API. Gives Claude
(or any other MCP client) **read-only** access to endpoints, missing updates,
vulnerabilities, installed software, policies, automations and reports.

Unofficial — not affiliated with or endorsed by Action1 Corporation.

## Why read-only

Action1's write surface deploys software and runs scripts on managed endpoints.
That is remote code execution across a whole fleet, and it is not something to put
behind a bearer token and a language model on day one. v1 exposes GETs only, and
the guarantee is enforced in the HTTP client itself — not merely by the absence of
write tools. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the write tiers
planned for v2 and [SECURITY.md](SECURITY.md) for the threat model.

## Quick start

```bash
git clone https://github.com/molnkontakt/action1-mcp.git
cd action1-mcp
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

# Credentials from the Action1 console: Configuration → API Credentials
export ACTION1_CLIENT_ID=...
export ACTION1_CLIENT_SECRET=...
export ACTION1_REGION=NorthAmerica          # or Europe / Australia / NA-2
export ACTION1_DEFAULT_ORG_ID=...           # optional, saves passing org_id everywhere

action1-mcp                                  # stdio transport
```

Wire it into Claude Code:

```bash
claude mcp add action1 --transport stdio --command action1-mcp
```

### Shared HTTP server

One long-lived process instead of one per client session:

```bash
export ACTION1_MCP_BEARER="$(openssl rand -base64 36 | tr -d '=+/' | cut -c1-48)"
action1-mcp --transport http --host 127.0.0.1 --port 3002
```

```json
{
  "mcpServers": {
    "action1": {
      "type": "http",
      "url": "https://mcp.example.com/action1/mcp",
      "headers": { "Authorization": "Bearer <ACTION1_MCP_BEARER>" }
    }
  }
}
```

Bind loopback and put a TLS-terminating reverse proxy in front. The server
refuses to bind a non-loopback address when `ACTION1_MCP_BEARER` is unset, but
that check is a backstop, not a deployment plan — see
[docs/DEPLOY.md](docs/DEPLOY.md).

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `ACTION1_CLIENT_ID` | — | **Required.** API credential from the Action1 console |
| `ACTION1_CLIENT_SECRET` | — | **Required.** |
| `ACTION1_REGION` | `NorthAmerica` | `NorthAmerica`, `NA-2`, `Europe`, `Australia` |
| `ACTION1_DEFAULT_ORG_ID` | unset | Org used when a tool is called without `org_id` |
| `ACTION1_MCP_BEARER` | unset | Bearer token required by the HTTP transport |
| `ACTION1_RATE_LIMIT_PER_MINUTE` | `25` | Local throttle; Action1's ceiling is 30/min **per tenant** |
| `ACTION1_MAX_ITEMS` | `1000` | Cap on items one tool call will page through |
| `ACTION1_PAGE_SIZE` | `200` | Items per API request |
| `ACTION1_TIMEOUT_SECONDS` | `60` | Per-request timeout |
| `ACTION1_MAX_429_RETRIES` | `3` | Retries after a rate-limit response |
| `ACTION1_ALLOW_WRITE` | `0` | Unblocks non-GET requests. No write tools exist in v1 |

## Tools

21 read tools — see [docs/TOOLS.md](docs/TOOLS.md) for the full reference.

| Area | Tools |
|---|---|
| Identity | `action1_whoami` |
| Tenant | `action1_list_organizations`, `action1_get_organization` |
| Endpoints | `action1_list_endpoints`, `action1_get_endpoint`, `action1_list_discovered_endpoints`, `action1_list_endpoint_groups`, `action1_get_endpoint_group_members` |
| Patching | `action1_list_missing_updates`, `action1_list_installed_apps`, `action1_list_packages` |
| Vulnerabilities | `action1_list_vulnerabilities`, `action1_get_vulnerability`, `action1_list_vulnerability_endpoints`, `action1_list_vulnerability_remediations` |
| Automation | `action1_list_policies`, `action1_get_policy_results`, `action1_list_automations`, `action1_list_scripts` |
| Reports | `action1_list_reports`, `action1_get_report_data` |

Every list tool returns the same envelope:

```json
{"items": [...], "returned": 200, "total_items": 1432, "truncated": true}
```

`truncated: true` means the cap stopped the walk before the data ran out. The
list is a prefix, not the whole set — narrow the query rather than concluding
from a partial answer.

## Rate limiting

Action1 counts requests **per enterprise**, across every endpoint and every
integration hitting the tenant — so this server shares its budget with your
PowerShell scripts and any other tooling. It throttles locally below the
documented ceiling rather than discovering it through 429s, and when one arrives
anyway it honours the `retry_after` the API returns before falling back to
exponential backoff.

## Development

```bash
pytest          # no network: every request is served by an httpx MockTransport
ruff check .
mypy src
```

## License

Apache-2.0. Provided as is; review before pointing it at a production tenant.
