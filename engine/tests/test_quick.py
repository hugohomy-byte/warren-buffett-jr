"""Tests for the quick 6-category scorecard."""

import pytest

from wbj.quick import quick_scorecard


def _series(vals: list[tuple[str, float]]) -> list[dict]:
    return [{"end": end, "val": val, "form": "10-K", "fp": "FY"} for end, val in vals]


def _packet() -> dict:
    years = ["2021-12-31", "2022-12-31", "2023-12-31", "2024-12-31", "2025-12-31"]
    return {
        "ticker": "TEST",
        "annual": {
            "revenue": _series(list(zip(years, [80e9, 90e9, 100e9, 110e9, 125e9]))),
            "net_income": _series(list(zip(years, [20e9, 23e9, 26e9, 28e9, 32e9]))),
            "operating_cash_flow": _series(list(zip(years, [25e9, 28e9, 31e9, 34e9, 38e9]))),
            "capex": _series(list(zip(years, [5e9, 6e9, 6e9, 7e9, 8e9]))),
            "long_term_debt": _series([(years[-1], 40e9)]),
            "equity": _series(list(zip(years[-2:], [70e9, 80e9]))),
            "operating_income": _series([(years[-1], 35e9)]),
            "gross_profit": _series([(years[-1], 60e9)]),
            "interest_expense": _series([(years[-1], 2e9)]),
        },
    }


def test_six_categories_present_in_fixed_order():
    sc = quick_scorecard(_packet())
    assert [r["key"] for r in sc["categories"]] == [
        "business", "financial", "market", "technical", "risk", "valuation",
    ]


def test_unscorable_categories_marked_with_reason():
    sc = quick_scorecard(_packet())
    ns = {r["key"]: r for r in sc["categories"] if r["status"] == "not_scorable"}
    assert set(ns) == {"market", "technical", "valuation"}
    assert all(r["score10"] is None and r["reason"] for r in ns.values())


def test_scored_categories_in_range_and_overall_weighted():
    sc = quick_scorecard(_packet())
    scored = {r["key"]: r for r in sc["categories"] if r["status"] == "scored"}
    assert set(scored) == {"business", "financial", "risk"}
    for r in scored.values():
        assert 0 <= r["score10"] <= 10
    assert sc["evidence_points_covered"] == 50  # 20 + 15 + 15
    expected = sum(r["max_points"] * r["score10"] for r in scored.values()) / 50
    assert abs(sc["overall_10"] - round(expected, 1)) < 0.05


def test_strong_company_scores_high():
    sc = quick_scorecard(_packet())  # 25% margins, 14% growth, low leverage
    assert sc["overall_10"] >= 7.0


def test_missing_everything_gives_no_overall():
    empty = {"ticker": "X", "annual": {k: [] for k in (
        "revenue", "net_income", "operating_cash_flow", "capex",
        "long_term_debt", "equity", "operating_income", "gross_profit",
        "interest_expense")}}
    sc = quick_scorecard(empty)
    assert sc["overall_10"] is None
    assert sc["evidence_points_covered"] == 0


# --- Phase 1: quick FMP scoring (market / technical / valuation) ---


def _ohlcv_rising(n: int = 220) -> list[dict]:
    """Newest-first daily bars with a steadily rising close (FMP order)."""
    rows = [{"date": f"D{i:05d}", "close": 100.0 + i} for i in range(n)]
    return list(reversed(rows))


def _by_key(sc: dict) -> dict:
    return {r["key"]: r for r in sc["categories"]}


def test_market_scores_with_forward_estimates():
    p = _packet()
    p["as_of"] = "2026-01-01"
    p["market_data"] = {
        "estimates": [
            {"date": "2025-01-25", "estimatedRevenueAvg": 120e9,
             "numberAnalystEstimatedRevenue": 30},   # past — ignored
            {"date": "2026-12-31", "estimatedRevenueAvg": 150e9,
             "numberAnalystEstimatedRevenue": 40},   # future — used
        ],
    }
    m = _by_key(quick_scorecard(p))["market"]
    assert m["status"] == "scored"
    assert 0 <= m["score10"] <= 10


def test_market_scores_with_stable_api_field_names():
    # Live FMP /stable/ uses revenueAvg / numAnalystsRevenue (not the legacy
    # estimatedRevenueAvg / numberAnalystEstimatedRevenue of /api/v3/).
    p = _packet()
    p["as_of"] = "2026-01-01"
    p["market_data"] = {
        "estimates": [
            {"date": "2026-12-31", "revenueAvg": 150e9, "numAnalystsRevenue": 40},
        ],
    }
    m = _by_key(quick_scorecard(p))["market"]
    assert m["status"] == "scored"
    assert 0 <= m["score10"] <= 10


def test_market_falls_back_to_finnhub_trailing_growth():
    """FMP refuses estimates for many symbols (402); FinnHub trailing revenue
    growth keeps Market & Growth scorable instead of N/S."""
    p = _packet()
    p["market_data"] = {"metrics": {"revenueGrowthTTMYoy": 11.85}}  # no estimates
    m = _by_key(quick_scorecard(p))["market"]
    assert m["status"] == "scored"
    assert m["evidence"]["source"] == "finnhub"
    assert m["evidence"]["recent_rev_growth"] == pytest.approx(0.1185)


def test_market_prefers_forward_estimates_over_trailing():
    p = _packet()
    p["as_of"] = "2026-01-01"
    p["market_data"] = {
        "estimates": [{"date": "2026-12-31", "revenueAvg": 150e9, "numAnalystsRevenue": 40}],
        "metrics": {"revenueGrowthTTMYoy": 11.85},
    }
    m = _by_key(quick_scorecard(p))["market"]
    assert m["status"] == "scored"
    assert "forward_rev_growth" in m["evidence"]
    assert m["evidence"].get("source") != "finnhub"


def test_market_ns_when_no_growth_source():
    p = _packet()
    p["market_data"] = {"price": 100.0}  # no estimates, no growth metrics
    m = _by_key(quick_scorecard(p))["market"]
    assert m["status"] == "not_scorable"


def test_technical_scores_with_price_history():
    p = _packet()
    p["market_data"] = {"ohlcv": _ohlcv_rising(220)}
    t = _by_key(quick_scorecard(p))["technical"]
    assert t["status"] == "scored"
    assert 0 <= t["score10"] <= 10


def test_valuation_scores_with_price_and_shares():
    p = _packet()
    p["annual"]["diluted_shares"] = _series([("2025-12-31", 10e9)])
    p["market_data"] = {"price": 100.0, "market_cap": 1000e9}
    v = _by_key(quick_scorecard(p))["valuation"]
    assert v["status"] == "scored"
    assert 0 <= v["score10"] <= 10


def test_fmp_categories_ns_without_market_data():
    ns = {r["key"]: r for r in quick_scorecard(_packet())["categories"]
          if r["status"] == "not_scorable"}
    assert set(ns) == {"market", "technical", "valuation"}
    assert ns["market"]["reason"] == "sin cobertura de analistas (FMP)"
    assert ns["technical"]["reason"] == "historial de precio insuficiente (FMP)"
    assert ns["valuation"]["reason"] == "sin precio de mercado (FMP)"


def test_technical_ns_with_too_little_history():
    p = _packet()
    p["market_data"] = {"ohlcv": _ohlcv_rising(30)}  # < 200 sessions
    t = _by_key(quick_scorecard(p))["technical"]
    assert t["status"] == "not_scorable"


# --- FinnHub backfill (used when FMP's daily quota is spent) --------------


def test_technical_scores_from_finnhub_metrics_without_history():
    """No candles, but FinnHub's trailing returns still score momentum."""
    p = _packet()
    p["market_data"] = {
        "price": 200.0,
        "metrics": {"26WeekPriceReturnDaily": 10.74, "52WeekHigh": 236.54},
    }
    t = _by_key(quick_scorecard(p))["technical"]
    assert t["status"] == "scored"
    assert 0 <= t["score10"] <= 10
    ev = t["evidence"]
    assert ev["source"] == "finnhub"
    assert ev["momentum_6m"] == pytest.approx(0.1074)  # percent -> ratio
    assert ev["off_52w_high"] == pytest.approx(200.0 / 236.54 - 1)


def test_technical_prefers_real_history_over_metrics():
    """With enough candles the SMA path wins; metrics are the fallback."""
    p = _packet()
    p["market_data"] = {
        "ohlcv": _ohlcv_rising(220),
        "metrics": {"26WeekPriceReturnDaily": 10.74, "52WeekHigh": 236.54},
    }
    t = _by_key(quick_scorecard(p))["technical"]
    assert t["status"] == "scored"
    assert "price_vs_sma200" in t["evidence"]
    assert t["evidence"].get("source") != "finnhub"


def test_technical_ns_when_metrics_lack_usable_fields():
    p = _packet()
    p["market_data"] = {"price": 200.0, "metrics": {"beta": 2.2}}
    t = _by_key(quick_scorecard(p))["technical"]
    assert t["status"] == "not_scorable"


def test_valuation_falls_back_to_finnhub_pe_without_shares():
    """EDGAR often omits diluted shares, so P/E can't be derived locally."""
    p = _packet()
    p["annual"]["diluted_shares"] = []
    p["market_data"] = {"price": 200.0, "metrics": {"peTTM": 41.4}}
    v = _by_key(quick_scorecard(p))["valuation"]
    assert v["status"] == "scored"
    assert v["evidence"]["pe"] == pytest.approx(41.4)


def test_valuation_ignores_nonpositive_finnhub_pe():
    p = _packet()
    p["annual"]["diluted_shares"] = []
    p["market_data"] = {"price": 200.0, "metrics": {"peTTM": -12.0}}
    v = _by_key(quick_scorecard(p))["valuation"]
    assert v["status"] == "not_scorable"


def test_valuation_ns_without_price():
    p = _packet()
    p["annual"]["diluted_shares"] = _series([("2025-12-31", 10e9)])
    p["market_data"] = {"market_cap": 1000e9}  # no price
    v = _by_key(quick_scorecard(p))["valuation"]
    assert v["status"] == "not_scorable"


def test_business_financial_risk_unchanged_by_market_data():
    def core(sc):
        return {r["key"]: r["score10"] for r in sc["categories"]
                if r["key"] in ("business", "financial", "risk")}

    base = core(quick_scorecard(_packet()))
    p = _packet()
    p["annual"]["diluted_shares"] = _series([("2025-12-31", 10e9)])
    p["market_data"] = {
        "ohlcv": _ohlcv_rising(220), "price": 100.0, "market_cap": 1000e9,
        "estimates": [{"date": "2099-12-31", "estimatedRevenueAvg": 150e9,
                       "numberAnalystEstimatedRevenue": 40}],
    }
    assert core(quick_scorecard(p)) == base
