"""Report-backed endpoints: row flattening and the report-category traversal.

Both behaviours exist because of what the live API actually returns, not what the
documentation implies — see the docstrings in tools/read.py.
"""

from __future__ import annotations

import pytest

from action1_mcp.client import Action1Error
from action1_mcp.tools.read import _flatten_report_rows


def _row(**fields: object) -> dict[str, object]:
    """A ReportRow shaped like the ones /apps/{org}/data actually returns."""
    return {
        "id": "%255B%25227-Zip%2522%255D",
        "type": "ReportRow",
        "self": "https://app.eu.action1.com/api/3.0/reportdata/org/all_apps/data/%255B%25227-Zip%2522%255D",
        "fields": dict(fields),
    }


def test_fields_are_lifted_to_the_top_level() -> None:
    result = _flatten_report_rows(
        {"items": [_row(Name="7-Zip (x64 edition)", Version="22.01.00.0")], "returned": 1}
    )
    item = result["items"][0]

    assert item["Name"] == "7-Zip (x64 edition)"
    assert item["Version"] == "22.01.00.0"
    assert item["id"] == "%255B%25227-Zip%2522%255D"


def test_self_and_type_are_dropped() -> None:
    """`self` is a ~250-char URL repeating the id, and `type` is constant per row."""
    item = _flatten_report_rows({"items": [_row(Name="x")]})["items"][0]

    assert "self" not in item
    assert "type" not in item


def test_row_id_is_preserved_verbatim() -> None:
    """It is double-URL-encoded and the only way to address the row; decoding breaks it."""
    encoded = "%255B%2522a%2520b%2522%255D"
    item = _flatten_report_rows({"items": [{"id": encoded, "fields": {"Name": "a b"}}]})["items"][0]

    assert item["id"] == encoded


def test_a_column_named_id_cannot_clobber_the_row_id() -> None:
    item = _flatten_report_rows({"items": [_row(id="column-value", Name="x")]})["items"][0]

    assert item["id"] == "%255B%25227-Zip%2522%255D"
    assert item["field_id"] == "column-value"


def test_envelope_metadata_survives_flattening() -> None:
    result = _flatten_report_rows(
        {"items": [_row(Name="x")], "returned": 1, "total_items": 9, "truncated": True}
    )

    assert result["returned"] == 1
    assert result["total_items"] == 9
    assert result["truncated"] is True


def test_non_report_rows_pass_through_untouched() -> None:
    plain = {"id": "1", "name": "not a report row"}
    assert _flatten_report_rows({"items": [plain]})["items"] == [plain]


# --- report category traversal -------------------------------------------


class _FakeClient:
    """Stands in for Action1Client, scripted per path."""

    def __init__(self, responses: dict[str, object]) -> None:
        self.responses = responses
        self.paths: list[str] = []

    def get_paged(self, path: str, **_: object) -> dict[str, object]:
        self.paths.append(path)
        value = self.responses.get(path)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise AssertionError(f"unexpected path {path}")
        return value  # type: ignore[return-value]


@pytest.fixture
def patch_client(monkeypatch: pytest.MonkeyPatch):
    def apply(responses: dict[str, object]) -> _FakeClient:
        import action1_mcp.tools.read as read_module

        fake = _FakeClient(responses)
        monkeypatch.setattr(read_module, "get_client", lambda: fake)
        return fake

    return apply


CATEGORY = {"id": "cat_builtin", "type": "ReportCategory", "name": "Built-in Reports"}


def test_categories_are_expanded_into_reports(patch_client) -> None:
    from action1_mcp.tools.read import action1_list_reports

    fake = patch_client(
        {
            "/reports/all": {"items": [CATEGORY]},
            "/reports/cat_builtin/children": {
                "items": [{"id": "all_apps", "type": "Report", "name": "Installed Software"}]
            },
        }
    )

    result = action1_list_reports()

    assert [r["id"] for r in result["items"]] == ["all_apps"]
    assert result["categories"] == [{"id": "cat_builtin", "name": "Built-in Reports"}]
    assert "/reports/cat_builtin/children" in fake.paths
    assert "note" not in result


def test_denied_children_degrade_to_a_usable_note(patch_client) -> None:
    """The live tenant's API user gets 403 here; the tool must still be useful."""
    from action1_mcp.tools.read import action1_list_reports

    patch_client(
        {
            "/reports/all": {"items": [CATEGORY]},
            "/reports/cat_builtin/children": Action1Error(
                403, "Access to this organization is denied."
            ),
        }
    )

    result = action1_list_reports()

    assert result["items"] == []
    assert result["categories"] == [{"id": "cat_builtin", "name": "Built-in Reports"}]
    assert "all_apps" in result["note"]
    assert "cat_builtin" in result["note"]


def test_nested_categories_are_not_returned_as_reports(patch_client) -> None:
    from action1_mcp.tools.read import action1_list_reports

    patch_client(
        {
            "/reports/all": {"items": [CATEGORY]},
            "/reports/cat_builtin/children": {
                "items": [
                    {"id": "sub", "type": "ReportCategory", "name": "Nested"},
                    {"id": "all_apps", "type": "Report", "name": "Installed Software"},
                ]
            },
        }
    )

    result = action1_list_reports()

    assert [r["id"] for r in result["items"]] == ["all_apps"]


def test_category_traversal_is_bounded(patch_client) -> None:
    """A wide tree must not fan out into the per-tenant rate limit."""
    from action1_mcp.tools.read import _MAX_REPORT_CATEGORIES, action1_list_reports

    many = [
        {"id": f"cat{i}", "type": "ReportCategory", "name": f"C{i}"}
        for i in range(_MAX_REPORT_CATEGORIES + 4)
    ]
    responses: dict[str, object] = {"/reports/all": {"items": many}}
    for category in many:
        responses[f"/reports/{category['id']}/children"] = {"items": []}

    fake = patch_client(responses)
    result = action1_list_reports()

    children_calls = [p for p in fake.paths if p.endswith("/children")]
    assert len(children_calls) == _MAX_REPORT_CATEGORIES
    assert result["truncated"] is True
