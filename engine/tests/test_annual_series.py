"""Tests for _annual_series: us-gaap/10-K and IFRS/20-F extraction."""

from wbj.cli import _annual_series, _REVENUE_TAGS, _IFRS_REVENUE_TAGS


def _facts(taxonomy: str, tag: str, rows: list[dict]) -> dict:
    return {"facts": {taxonomy: {tag: {"units": {"USD": rows}}}}}


def _row(end: str, val: float, form: str, filed: str = "") -> dict:
    return {"end": end, "val": val, "form": form, "fp": "FY",
            "filed": filed or end}


def test_us_gaap_10k_extracted():
    facts = _facts("us-gaap", "Revenues", [
        _row("2023-12-31", 100.0, "10-K"),
        _row("2024-12-31", 120.0, "10-K"),
    ])
    series = _annual_series(facts, _REVENUE_TAGS, _IFRS_REVENUE_TAGS)
    assert [r["val"] for r in series] == [100.0, 120.0]


def test_ifrs_20f_extracted_for_foreign_filer():
    """Foreign private issuers (TSM) file IFRS on a 20-F, no us-gaap."""
    facts = _facts("ifrs-full", "Revenue", [
        _row("2023-12-31", 70.6e9, "20-F"),
        _row("2024-12-31", 88.3e9, "20-F"),
    ])
    series = _annual_series(facts, _REVENUE_TAGS, _IFRS_REVENUE_TAGS)
    assert [r["end"] for r in series] == ["2023-12-31", "2024-12-31"]
    assert series[-1]["val"] == 88.3e9


def test_us_gaap_preferred_when_both_present():
    facts = {"facts": {
        "us-gaap": {"Revenues": {"units": {"USD": [_row("2024-12-31", 120.0, "10-K")]}}},
        "ifrs-full": {"Revenue": {"units": {"USD": [_row("2024-12-31", 999.0, "20-F")]}}},
    }}
    series = _annual_series(facts, _REVENUE_TAGS, _IFRS_REVENUE_TAGS)
    assert series[-1]["val"] == 120.0  # us-gaap wins the tie on tag order


def test_non_annual_forms_ignored():
    facts = _facts("ifrs-full", "Revenue", [
        _row("2024-12-31", 88.3e9, "20-F"),
        {"end": "2025-03-31", "val": 25e9, "form": "6-K", "fp": "Q1", "filed": "2025-04"},
    ])
    series = _annual_series(facts, _REVENUE_TAGS, _IFRS_REVENUE_TAGS)
    assert [r["form"] for r in series] == ["20-F"]
