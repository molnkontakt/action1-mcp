# Deploying the shared HTTP server

stdio is fine for a single user on a single machine. Once several clients — or
several concurrent agent sessions — use the same tenant, each one spawning its
own copy of the server wastes memory and multiplies the request rate against a
per-tenant rate limit. One shared HTTP process avoids both.

## Shape

```
  MCP clients                    host running action1-mcp
  ───────────                    ────────────────────────────────
                                 ┌──────────────────────────────┐
  Authorization: Bearer <token>  │ reverse proxy :443 (TLS)      │
  POST /action1/mcp ───────────► │   401 unless bearer matches   │
                                 │        │ strip prefix         │
                                 │        ▼                      │
                                 │ action1-mcp 127.0.0.1:3002    │──► app.<region>.action1.com
                                 │   systemd, own unprivileged   │    /api/3.0
                                 │   user, creds via env         │
                                 └──────────────────────────────┘
```

Two invariants, neither optional:

- **the bind stays loopback.** Nothing but the proxy should be able to reach the
  port.
- **a bearer is enforced.** Set `ACTION1_MCP_BEARER` so the server checks it
  itself. Enforcing it in the proxy as well is belt and braces, not a substitute
  — a proxy rule can be edited away in a config sweep that never touches this
  service.

## systemd

```ini
[Unit]
Description=action1-mcp (MCP server for Action1 RMM)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=action1mcp
EnvironmentFile=/etc/action1-mcp/action1-mcp.env
ExecStart=/opt/action1-mcp/venv/bin/action1-mcp --transport http --host 127.0.0.1 --port 3002
Restart=on-failure
RestartSec=5

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6
MemoryMax=384M

[Install]
WantedBy=multi-user.target
```

`EnvironmentFile` is read by systemd as PID 1 before privileges are dropped, so
it can stay mode 600 root-owned while the service runs unprivileged.

Prefer a secrets manager over a file on disk? Wrap `ExecStart` so credentials are
fetched at start and only ever exist in the process environment — never in
`argv`, which every user with `ps` can read.

## Caddy

```caddyfile
mcp.example.com {
	handle_path /action1/* {
		reverse_proxy 127.0.0.1:3002
	}
	tls {
		dns cloudflare {env.CLOUDFLARE_API_TOKEN}
	}
}
```

`handle_path` strips the prefix, so `/action1/mcp` reaches the server as `/mcp`.

> **Caddy reads `EnvironmentFile` only on start.** `systemctl reload caddy` keeps
> the old process environment; if a token referenced as `{env.X}` was changed,
> the placeholder expands to empty and *every* request 401s — including correct
> ones. After changing the environment file, `restart`, do not `reload`.

## Client configuration

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

Write this into the client config file directly rather than using a CLI flag that
puts the token in `argv`.

## Verifying

```bash
# 401 without a bearer
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://mcp.example.com/action1/mcp

# initialize with one
curl -s https://mcp.example.com/action1/mcp \
  -H "Authorization: Bearer $ACTION1_MCP_BEARER" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"1"}}}'
```

Then confirm the port is not reachable from anywhere but the proxy:

```bash
ss -tlnp | grep 3002   # must show 127.0.0.1:3002, not 0.0.0.0:3002
```
