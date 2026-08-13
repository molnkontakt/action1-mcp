"""Tool registration and the org-id fallback."""

from __future__ import annotations

import asyncio

import pytest

from action1_mcp.app import mcp
from action1_mcp.client import Action1Error


def _tool_names() -> set[str]:
    from action1_mcp.tools import read  # noqa: F401

    return {tool.name for tool in asyncio.run(mcp.list_tools())}


def test_every_tool_is_namespaced() -> None:
    names = _tool_names()
    assert names, "no tools registered"
    assert all(n.startswith("action1_") for n in names)


def test_expected_read_tools_are_present() -> None:
    expected = {
        "action1_whoami",
        "action1_list_organizations",
        "action1_list_endpoints",
        "action1_get_endpoint",
        "action1_list_discovered_endpoints",
        "action1_list_missing_updates",
        "action1_list_vulnerabilities",
        "action1_list_policies",
        "action1_list_reports",
    }
    assert expected <= _tool_names()


def test_no_write_tools_are_registered_in_v1() -> None:
    """v1 is read-only. If this fails, docs/SECURITY.md and the README are now lying.

    Matching on leading verbs rather than substrings: `list_installed_apps` reads,
    it does not install.
    """
    read_verbs = ("action1_list_", "action1_get_", "action1_whoami")
    offenders = [n for n in _tool_names() if not n.startswith(read_verbs)]
    assert offenders == []


def test_resolve_org_id_prefers_the_explicit_argument(monkeypatch: pytest.MonkeyPatch) -> None:
    import action1_mcp.client as client_module
    from tests.conftest import make_settings

    class _Stub:
        settings = make_settings(default_org_id="from-env")

    monkeypatch.setattr(client_module, "get_client", lambda: _Stub())
    assert client_module.resolve_org_id("explicit") == "explicit"
    assert client_module.resolve_org_id(None) == "from-env"


def test_resolve_org_id_without_any_default_points_at_the_listing_tool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import action1_mcp.client as client_module
    from tests.conftest import make_settings

    class _Stub:
        settings = make_settings(default_org_id=None)

    monkeypatch.setattr(client_module, "get_client", lambda: _Stub())
    with pytest.raises(Action1Error) as excinfo:
        client_module.resolve_org_id(None)

    assert "action1_list_organizations" in str(excinfo.value)
