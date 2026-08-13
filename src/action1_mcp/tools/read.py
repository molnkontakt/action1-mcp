"""Read-only tools — GET requests only, no confirmation, nothing mutated.

Every list tool returns the same envelope::

    {"items": [...], "returned": int, "total_items": int | None, "truncated": bool}

`truncated: true` means the result cap stopped the walk before the data ran
out — narrow the query instead of treating the list as complete.

Organization-scoped tools accept `org_id`; when omitted they fall back to
`ACTION1_DEFAULT_ORG_ID` and fail with a pointer to `action1_list_organizations`
if that is unset too.
"""

from __future__ import annotations

from typing import Any

from action1_mcp.app import mcp
from action1_mcp.client import Action1Error, get_client, resolve_org_id

# `fields=*` asks Action1 for extended data (e.g. missing_critical_updates on
# endpoints). Only some endpoints support it, and the docs warn those queries are
# slower — so it is opt-in per call rather than always on.
_EXTENDED = "*"


def _paged(
    path: str,
    limit: int,
    extra: dict[str, Any] | None = None,
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Walk a ResultPage with caller filters merged in."""
    params: dict[str, Any] = dict(extra or {})
    params.update(filters or {})
    return get_client().get_paged(path, params=params, max_items=limit)


def _flatten_report_rows(result: dict[str, Any]) -> dict[str, Any]:
    """Lift `ReportRow.fields` to the top level of each item.

    Report-backed endpoints (`/apps/{org}/data`, `/reportdata/.../data`) return rows
    shaped like::

        {"id": "%255B%2522...", "type": "ReportRow",
         "self": "https://app.eu.action1.com/api/3.0/reportdata/.../data/%255B...",
         "fields": {"Name": "7-Zip (x64 edition)", "Version": "22.01.00.0", ...}}

    The payload is entirely in `fields`; `self` is a ~250-character URL repeating the
    already-present id, and `type` is the same string on every row. Flattening makes
    the data legible and cuts the response size several-fold on a real tenant.

    `id` is kept verbatim — it is a double-URL-encoded composite key and is the only
    way to address a single row, so decoding it would break round-tripping.
    Rows that are not `ReportRow` are passed through untouched.
    """
    items = result.get("items")
    if not isinstance(items, list):
        return result

    flattened: list[Any] = []
    for row in items:
        if not isinstance(row, dict) or not isinstance(row.get("fields"), dict):
            flattened.append(row)
            continue
        merged: dict[str, Any] = {"id": row.get("id")} if row.get("id") is not None else {}
        # Field names win over the row's own metadata only where they do not collide;
        # a report column literally called "id" would otherwise silently replace the key
        # needed to address the row.
        for key, value in row["fields"].items():
            merged["field_id" if key == "id" else key] = value
        flattened.append(merged)

    return {**result, "items": flattened}


# --- identity & tenant ----------------------------------------------------


@mcp.tool()
def action1_whoami() -> dict[str, Any]:
    """Show which Action1 identity these API credentials map to, and their permissions.

    Use this first when a call fails with 403 — it tells you whether the API
    credential simply lacks access to the organization you asked about.

    Returns:
        The `/Me` object: account identity and granted API permissions.
    """
    return get_client().get_object("/Me")


@mcp.tool()
def action1_list_organizations(limit: int = 100) -> dict[str, Any]:
    """List the organizations (tenants) these credentials can see.

    Most other tools need an `org_id` from here.

    Args:
        limit: max organizations to return.
    """
    return _paged("/organizations", limit)


@mcp.tool()
def action1_get_organization(org_id: str | None = None) -> dict[str, Any]:
    """Get one organization's details.

    Args:
        org_id: organization ID; defaults to the server's configured organization.
    """
    return get_client().get_object(f"/organizations/{resolve_org_id(org_id)}")


# --- endpoints ------------------------------------------------------------


@mcp.tool()
def action1_list_endpoints(
    org_id: str | None = None,
    include_update_counts: bool = False,
    limit: int = 200,
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    """List managed endpoints (machines with the Action1 agent installed).

    Args:
        org_id: organization ID; defaults to the server's configured organization.
        include_update_counts: also return `missing_critical_updates` and
            `missing_other_updates` per endpoint. Slower — Action1 documents
            extended queries as longer to process.
        limit: max endpoints to return.
        filters: extra Action1 query parameters passed through verbatim, for
            filters this tool does not name explicitly.

    Returns:
        Standard list envelope. Each item includes at least the endpoint id,
        name, and connection/agent status.
    """
    extra = {"fields": _EXTENDED} if include_update_counts else None
    return _paged(f"/endpoints/managed/{resolve_org_id(org_id)}", limit, extra, filters)


@mcp.tool()
def action1_get_endpoint(
    endpoint_id: str,
    org_id: str | None = None,
    include_update_counts: bool = True,
) -> dict[str, Any]:
    """Get full detail for one managed endpoint.

    Args:
        endpoint_id: the endpoint's Action1 ID (from action1_list_endpoints).
        org_id: organization ID; defaults to the server's configured organization.
        include_update_counts: include missing-update counts. Defaults on here
            because a single-endpoint query is cheap.
    """
    params = {"fields": _EXTENDED} if include_update_counts else {}
    return get_client().get_object(
        f"/endpoints/managed/{resolve_org_id(org_id)}/{endpoint_id}", **params
    )


@mcp.tool()
def action1_list_discovered_endpoints(
    org_id: str | None = None, limit: int = 200
) -> dict[str, Any]:
    """List discovered but *unmanaged* devices — seen on the network, no agent installed.

    Useful for finding coverage gaps: machines that exist but are not being patched.

    Args:
        org_id: organization ID; defaults to the server's configured organization.
        limit: max devices to return.
    """
    return _paged(f"/endpoints/discovery/{resolve_org_id(org_id)}", limit)


@mcp.tool()
def action1_list_endpoint_groups(org_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    """List endpoint groups, which are what policies and automations target.

    Args:
        org_id: organization ID; defaults to the server's configured organization.
        limit: max groups to return.
    """
    return _paged(f"/endpoints/groups/{resolve_org_id(org_id)}", limit)


@mcp.tool()
def action1_get_endpoint_group_members(
    group_id: str, org_id: str | None = None, limit: int = 200
) -> dict[str, Any]:
    """List the endpoints in one group.

    Args:
        group_id: group ID from action1_list_endpoint_groups.
        org_id: organization ID; defaults to the server's configured organization.
        limit: max members to return.
    """
    return _paged(
        f"/endpoints/groups/{resolve_org_id(org_id)}/{group_id}/contents", limit
    )


# --- patching -------------------------------------------------------------


@mcp.tool()
def action1_list_missing_updates(
    org_id: str | None = None,
    security_severity: str | None = None,
    approval_status: str | None = None,
    limit: int = 200,
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    """List updates missing across the organization's endpoints.

    Args:
        org_id: organization ID; defaults to the server's configured organization.
        security_severity: e.g. "critical", "important" — filters by severity.
        approval_status: e.g. "new" for unapproved updates.
        limit: max updates to return.
        filters: extra Action1 query parameters passed through verbatim.

    Returns:
        Standard list envelope; items describe the update and how many endpoints
        are missing it.
    """
    extra = {
        "security_severity": security_severity,
        "approval_status": approval_status,
        "fields": _EXTENDED,
    }
    extra = {k: v for k, v in extra.items() if v is not None}
    return _paged(f"/updates/{resolve_org_id(org_id)}", limit, extra, filters)


@mcp.tool()
def action1_list_installed_apps(
    org_id: str | None = None,
    endpoint_id: str | None = None,
    limit: int = 200,
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    """List installed software, tenant-wide or for one endpoint.

    Args:
        org_id: organization ID; defaults to the server's configured organization.
        endpoint_id: restrict to one endpoint's installed software.
        limit: max entries to return.
        filters: extra Action1 query parameters passed through verbatim.

    Returns:
        Standard list envelope. This endpoint is report-backed, so each item is
        flattened from a `ReportRow` — expect columns like `Name`, `Version`,
        `Vendor` and `Install Type` at the top level, plus the row `id`.
    """
    path = f"/apps/{resolve_org_id(org_id)}/data"
    if endpoint_id:
        path = f"{path}/{endpoint_id}"
    return _flatten_report_rows(_paged(path, limit, None, filters))


@mcp.tool()
def action1_list_packages(limit: int = 200, filters: dict[str, str] | None = None) -> dict[str, Any]:
    """List app packages available in the Action1 software repository.

    These are what a deployment *could* install — this tool does not deploy anything.

    Args:
        limit: max packages to return.
        filters: extra Action1 query parameters passed through verbatim.
    """
    return _paged("/packages/all", limit, None, filters)


# --- vulnerabilities ------------------------------------------------------


@mcp.tool()
def action1_list_vulnerabilities(
    org_id: str | None = None,
    limit: int = 200,
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    """List CVEs detected across the organization's endpoints.

    Args:
        org_id: organization ID; defaults to the server's configured organization.
        limit: max CVEs to return.
        filters: extra Action1 query parameters passed through verbatim (e.g.
            severity filters — see Action1's API specification).
    """
    return _paged(f"/vulnerabilities/{resolve_org_id(org_id)}", limit, None, filters)


@mcp.tool()
def action1_get_vulnerability(cve_id: str, org_id: str | None = None) -> dict[str, Any]:
    """Get details for one CVE as Action1 sees it in this organization.

    Args:
        cve_id: e.g. "CVE-2026-1234".
        org_id: organization ID; defaults to the server's configured organization.
    """
    return get_client().get_object(f"/vulnerabilities/{resolve_org_id(org_id)}/{cve_id}")


@mcp.tool()
def action1_list_vulnerability_endpoints(
    cve_id: str, org_id: str | None = None, limit: int = 200
) -> dict[str, Any]:
    """List the endpoints affected by one CVE.

    Args:
        cve_id: e.g. "CVE-2026-1234".
        org_id: organization ID; defaults to the server's configured organization.
        limit: max endpoints to return.
    """
    return _paged(f"/vulnerabilities/{resolve_org_id(org_id)}/{cve_id}/endpoints", limit)


@mcp.tool()
def action1_list_vulnerability_remediations(
    cve_id: str, org_id: str | None = None, limit: int = 100
) -> dict[str, Any]:
    """List remediations already configured for one CVE.

    Read-only: this shows what remediation exists, it does not create or run one.

    Args:
        cve_id: e.g. "CVE-2026-1234".
        org_id: organization ID; defaults to the server's configured organization.
        limit: max remediations to return.
    """
    return _paged(f"/vulnerabilities/{resolve_org_id(org_id)}/{cve_id}/remediations", limit)


# --- policies, automations, scripts --------------------------------------


@mcp.tool()
def action1_list_policies(org_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    """List policy instances (deployments and remediations configured in the tenant).

    Args:
        org_id: organization ID; defaults to the server's configured organization.
        limit: max policies to return.
    """
    return _paged(f"/policies/instances/{resolve_org_id(org_id)}", limit)


@mcp.tool()
def action1_get_policy_results(
    policy_id: str, org_id: str | None = None, limit: int = 200
) -> dict[str, Any]:
    """Show per-endpoint results for one policy — what succeeded, failed or is pending.

    Args:
        policy_id: policy instance ID from action1_list_policies.
        org_id: organization ID; defaults to the server's configured organization.
        limit: max endpoint results to return.
    """
    return _paged(
        f"/policies/instances/{resolve_org_id(org_id)}/{policy_id}/endpoint_results", limit
    )


@mcp.tool()
def action1_list_automations(org_id: str | None = None, limit: int = 100) -> dict[str, Any]:
    """List automations (scheduled policies) configured in the organization.

    Args:
        org_id: organization ID; defaults to the server's configured organization.
        limit: max automations to return.
    """
    return _paged(f"/policies/schedules/{resolve_org_id(org_id)}", limit)


@mcp.tool()
def action1_list_scripts(limit: int = 200) -> dict[str, Any]:
    """List scripts available in the tenant.

    Useful for auditing what automation *could* run. This tool cannot execute them.

    Args:
        limit: max scripts to return.
    """
    return _paged("/scripts/all", limit)


# --- reports --------------------------------------------------------------


# `/reports/all` returns only the top-level *categories*, not report definitions —
# those live one level down under `/reports/{category_id}/children`. Bounded so a wide
# category tree cannot fan out into the per-tenant rate limit.
_MAX_REPORT_CATEGORIES = 8


@mcp.tool()
def action1_list_reports(limit: int = 200) -> dict[str, Any]:
    """List report definitions, expanding the report category tree one level.

    `/reports/all` returns categories rather than reports, so this walks each
    category's children to reach the actual definitions.

    Args:
        limit: max reports to return.

    Returns:
        Standard list envelope plus `categories`. Each report's `id` is what
        `action1_get_report_data` needs. If the children could not be read — the
        endpoint requires a role permission an API-only user may lack — `items` is
        empty and `note` explains what to do instead.
    """
    client = get_client()
    top = client.get_paged("/reports/all", max_items=limit)
    entries = [i for i in top.get("items", []) if isinstance(i, dict)]
    categories = [i for i in entries if i.get("type") == "ReportCategory"]
    reports = [i for i in entries if i.get("type") != "ReportCategory"]

    denied: list[str] = []
    for category in categories[:_MAX_REPORT_CATEGORIES]:
        category_id = category.get("id")
        if not category_id or len(reports) >= limit:
            continue
        try:
            children = client.get_paged(
                f"/reports/{category_id}/children", max_items=limit - len(reports)
            )
        except Action1Error as exc:
            denied.append(f"{category_id}: {exc.status} {exc.developer_message}")
            continue
        reports.extend(
            row
            for row in children.get("items", [])
            if isinstance(row, dict) and row.get("type") != "ReportCategory"
        )

    result: dict[str, Any] = {
        "items": reports,
        "returned": len(reports),
        "total_items": None,
        "truncated": len(categories) > _MAX_REPORT_CATEGORIES,
        "categories": [{"id": c.get("id"), "name": c.get("name")} for c in categories],
    }
    if denied and not reports:
        result["note"] = (
            "No report definitions could be listed — reading the categories' children was "
            f"refused ({'; '.join(denied)}). Report IDs are stable slugs rather than UUIDs, "
            "so action1_get_report_data can still be called directly with one: 'all_apps' is "
            "known to work, and any report row's `self` URL contains the slug that produced it. "
            "Do not pass a category id such as 'cat_builtin' — that returns 400."
        )
    return result


@mcp.tool()
def action1_get_report_data(
    report_id: str,
    org_id: str | None = None,
    limit: int = 200,
    filters: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Read the rows of one report.

    Returns Action1's last computed data. This tool does not trigger a requery,
    so figures can lag the console.

    Args:
        report_id: a report **slug**, not a category id — e.g. `all_apps`. Passing a
            category id like `cat_builtin` returns 400 "Report ... not found".
        org_id: organization ID; defaults to the server's configured organization.
        limit: max rows to return.
        filters: extra Action1 query parameters passed through verbatim.

    Returns:
        Standard list envelope with each `ReportRow` flattened, so the report's own
        columns appear at the top level alongside the row `id`.
    """
    return _flatten_report_rows(
        _paged(f"/reportdata/{resolve_org_id(org_id)}/{report_id}/data", limit, None, filters)
    )
