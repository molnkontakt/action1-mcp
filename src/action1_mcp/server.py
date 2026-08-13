"""CLI entrypoint.

Two transports:

- **stdio** (default) — the client spawns one process per session. Credentials
  come from that process's environment.
- **http** — one shared long-lived process, which is what you want when several
  clients or sessions use the same tenant. Set `ACTION1_MCP_BEARER` before
  exposing it to anything, and bind loopback behind a TLS-terminating proxy.
"""

from __future__ import annotations

import argparse
import os
import sys

from action1_mcp import __version__
from action1_mcp.app import mcp


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="action1-mcp",
        description="MCP server for the Action1 RMM REST API (read-only)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default=os.environ.get("ACTION1_MCP_TRANSPORT", "stdio"),
        help="stdio (default) or http",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("ACTION1_MCP_HOST", "127.0.0.1"),
        help="bind address for --transport http (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("ACTION1_MCP_PORT", "3002")),
        help="bind port for --transport http (default: 3002)",
    )
    parser.add_argument("--version", action="version", version=f"action1-mcp {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    # Tool modules register themselves on import via @mcp.tool()
    from action1_mcp.tools import read  # noqa: F401

    if args.transport == "stdio":
        mcp.run()
        return

    if not os.environ.get("ACTION1_MCP_BEARER", "").strip():
        # Not fatal: a reverse proxy in front may be doing the authenticating, as
        # long as the bind stays loopback. Binding a public interface with no
        # token in the process is not a configuration anyone chooses on purpose.
        if args.host not in {"127.0.0.1", "::1", "localhost"}:
            print(
                f"refusing to bind {args.host} over HTTP with no ACTION1_MCP_BEARER set — "
                "every tool would be reachable unauthenticated",
                file=sys.stderr,
            )
            raise SystemExit(2)
        print(
            "warning: ACTION1_MCP_BEARER is unset; this server has no authentication of "
            "its own and relies entirely on the loopback bind and whatever proxies it",
            file=sys.stderr,
        )

    mcp.run(transport="http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
