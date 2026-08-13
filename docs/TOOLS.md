# Tools

21 tools, all read-only (GET). Organization-scoped tools take `org_id`; when
omitted they fall back to `ACTION1_DEFAULT_ORG_ID`, and error with a pointer to
`action1_list_organizations` if that is unset too.

## The list envelope

Every `list_*` tool and `action1_get_*` tool that returns rows uses one shape:

```json
{
  "items": [ ... ],
  "returned": 200,
  "total_items": 1432,
  "truncated": true
}
```

| Field | Meaning |
|---|---|
| `items` | the rows Action1 returned |
| `returned` | `len(items)` |
| `total_items` | server-side total, when Action1 reports one; else `null` |
| `truncated` | `true` when the `limit` cap stopped the walk before the data ran out |

**`truncated: true` means you are holding a prefix, not the set.** Narrow the
query — by organization, endpoint, severity, or a `filters` parameter — rather
than drawing a conclusion from a partial list.

## Identity

### `action1_whoami()`
The `/Me` object: which identity the API credential maps to and what it is
permitted to do. Check this first when something returns 403 — usually the
credential simply has no access to the organization you asked about.

## Tenant

### `action1_list_organizations(limit=100)`
Organizations these credentials can see. Most other tools need an `id` from here.

### `action1_get_organization(org_id=None)`
One organization's details.

## Endpoints

### `action1_list_endpoints(org_id=None, include_update_counts=False, limit=200, filters=None)`
Managed endpoints — machines running the Action1 agent. `include_update_counts`
adds `missing_critical_updates` and `missing_other_updates` per endpoint
(`fields=*`); Action1 documents extended queries as slower, so it is off by
default.

### `action1_get_endpoint(endpoint_id, org_id=None, include_update_counts=True)`
Full detail for one endpoint. Extended fields default **on** here because a
single-object query is cheap.

### `action1_list_discovered_endpoints(org_id=None, limit=200)`
Devices seen on the network with **no agent installed** — i.e. machines nobody is
patching. The coverage-gap query.

### `action1_list_endpoint_groups(org_id=None, limit=100)`
Groups, which are what policies and automations target.

### `action1_get_endpoint_group_members(group_id, org_id=None, limit=200)`
The endpoints in one group.

## Patching

### `action1_list_missing_updates(org_id=None, security_severity=None, approval_status=None, limit=200, filters=None)`
Updates missing across the organization. `security_severity` takes values like
`critical` / `important`; `approval_status=new` returns unapproved updates.

### `action1_list_installed_apps(org_id=None, endpoint_id=None, limit=200, filters=None)`
Installed software, tenant-wide or for one endpoint.

### `action1_list_packages(limit=200, filters=None)`
App packages available in the Action1 software repository — what a deployment
*could* install. This tool does not deploy.

## Vulnerabilities

### `action1_list_vulnerabilities(org_id=None, limit=200, filters=None)`
CVEs detected across the organization's endpoints.

### `action1_get_vulnerability(cve_id, org_id=None)`
One CVE as Action1 sees it in this organization.

### `action1_list_vulnerability_endpoints(cve_id, org_id=None, limit=200)`
Which endpoints are affected by one CVE.

### `action1_list_vulnerability_remediations(cve_id, org_id=None, limit=100)`
Remediations already configured for a CVE. Read-only: shows what exists, does not
create or run anything.

## Policies, automations, scripts

### `action1_list_policies(org_id=None, limit=100)`
Policy instances — the deployments and remediations configured in the tenant.

### `action1_get_policy_results(policy_id, org_id=None, limit=200)`
Per-endpoint outcome for one policy: succeeded, failed, pending.

### `action1_list_automations(org_id=None, limit=100)`
Scheduled policies.

### `action1_list_scripts(limit=200)`
Scripts available in the tenant — useful for auditing what automation *could*
run. This server cannot execute them.

## Reports

### `action1_list_reports(limit=200)`
Report definitions available in the tenant.

### `action1_get_report_data(report_id, org_id=None, limit=200, filters=None)`
Rows of one report, as Action1 last computed them. This does **not** trigger a
requery, so figures can lag the console.

## The `filters` parameter

Several tools accept `filters: dict[str, str]`, passed through verbatim as query
parameters. Action1's filter names are per-endpoint and documented in its
[API specification](https://app.action1.com/apidocs); the named arguments above
cover the common ones and `filters` covers the rest without needing a new tool
per parameter.

```python
action1_list_missing_updates(filters={"approval_status": "new"})
```
