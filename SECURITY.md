# Security

## What this server can reach

With valid credentials it can read your entire Action1 tenant: every managed
endpoint's inventory, installed software, missing patches, detected CVEs,
policies, automations and reports. That is a detailed map of an estate's
unpatched attack surface — useful to you, and equally useful to anyone who
obtains it.

## What it deliberately cannot do

Action1's write surface deploys software and runs scripts on managed endpoints,
which is remote code execution across the fleet. v1 exposes no write tools, and
the guarantee is enforced twice:

1. No write tool is registered — a test asserts every tool name starts with a
   read verb.
2. `Action1Client.request` refuses any non-GET method unless
   `ACTION1_ALLOW_WRITE=1`, so a write cannot slip in through a helper.

Setting `ACTION1_ALLOW_WRITE=1` today unblocks nothing useful — there are no
write tools to enable. Do not set it in the belief that it hardens anything.

## Credentials

- The Action1 client ID and secret are read from the process environment and
  never written to disk, logged, or included in tool output.
- Scope the API credential in the Action1 console to the organizations the
  server should see. This server cannot enforce per-tool authorization; whatever
  the credential can read, every client of this server can read.
- Rotating the credential in Action1 takes effect on the next token refresh
  (within `expires_in`, one hour by default) — or immediately, on restart.

## Transport

**stdio** carries no authentication and needs none: the client spawns the
process and the credentials are that process's environment.

**HTTP** requires a decision:

- Set `ACTION1_MCP_BEARER`. Without it the server has no authentication of its
  own, and any client that can reach the port holds the whole tenant.
- Bind loopback (`127.0.0.1`) and terminate TLS in a reverse proxy. The server
  refuses to bind a non-loopback address with no bearer set, but treat that as a
  backstop against mistakes, not as a deployment plan.
- The bearer is a single shared service identity. Action1's audit log shows the
  API credential, never which human asked. Revocation means rotating the token
  and restarting.

## Rate limiting is a shared resource

Action1 counts requests per enterprise across all integrations. A client that
loops over tools can rate-limit your PowerShell automation and any other
integration against the same tenant. The default local throttle (25/min, under
Action1's documented 30/min) exists to make that harder to do by accident.

## Reporting a vulnerability

Open a GitHub issue for anything non-sensitive. For a finding that would expose
tenants if published, use GitHub's private vulnerability reporting on this
repository instead of the public tracker.
