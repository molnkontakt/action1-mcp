"""The FastMCP instance.

Kept in its own module so tool modules can import it without an import cycle
(`server.py` imports tools, tools import this).
"""

from __future__ import annotations

import os

from fastmcp import FastMCP
from fastmcp.server.auth.providers.jwt import StaticTokenVerifier

INSTRUCTIONS = """\
Read-only access to an Action1 RMM tenant: endpoints, missing updates,
vulnerabilities, installed software, policies, automations and reports.

Organization-scoped tools take an `org_id`. Call `action1_list_organizations`
first if you do not have one, unless the server has a default configured.

List results are capped and report `truncated: true` when the cap was hit —
narrow the query rather than assuming you saw everything. The Action1 API is
rate-limited per tenant, so prefer one broad call over many narrow ones.\
"""


def _auth_provider() -> StaticTokenVerifier | None:
    """Bearer-token auth for HTTP transport, when `ACTION1_MCP_BEARER` is set.

    Unset means no authentication, which is only safe for stdio or behind a
    proxy that authenticates on this server's behalf. Over HTTP, an unauthenticated
    Action1 MCP server hands its whole tenant to anyone who can reach the port.
    """
    token = os.environ.get("ACTION1_MCP_BEARER", "").strip()
    if not token:
        return None
    return StaticTokenVerifier(
        tokens={token: {"client_id": "action1-mcp", "scopes": ["action1:read"]}}
    )


mcp: FastMCP = FastMCP(
    "action1-mcp",
    instructions=INSTRUCTIONS,
    auth=_auth_provider(),
)
