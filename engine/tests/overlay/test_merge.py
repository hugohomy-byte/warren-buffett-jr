"""Tests for `wbj.overlay.merge` (Task 20): `collect_requests` and
`merge_overlay` against small, self-contained `SpecialistOutput` fixtures
(compute-like, not real specialist output -- see `wbj/overlay/merge.py`'s
module docstring for why the Dimension-slot marker convention used here is
this task's own, not something the Task 15-19 specialists emit yet).
"""

from __future__ import annotations

import pytest

from wbj.core.nullstates import EvidenceClass, NullState, Value
from wbj.core.scoring import Category, Dimension
from wbj.overlay.merge import (
    UnknownJudgmentRequestError,
    collect_requests,
    judgment_marker,
    merge_overlay,
    schema_hint_ok,
)
from wbj.schemas.overlay import Judgment
from wbj.specialists.common import (
    CategoryStats,
    JudgmentRequest,
    MetricRow,
    SecurityRef,
    SpecialistOutput,
    status_from_coverage,
)

MOAT_REQUEST_ID = "business_analysis:moat_classification"
REG_REQUEST_ID = "risk_analysis:regulatory_exposure_score"
THESIS_REQUEST_ID = "risk_analysis:thesis_killers"


def _security() -> SecurityRef:
    return SecurityRef(ticker="NVDA", exchange="NASDAQ", currency="USD")


def _business_output() -> SpecialistOutput:
    """One MOAT dimension: a mechanically-scored slot (weight 0.7, score
    6.0) plus a judgment-fillable NOT_SCORABLE slot (weight 0.3, marked for
    MOAT_REQUEST_ID) -- valid weight 0.7/1.0 = 0.70 exactly meets
    COVERAGE_USABLE, so the dimension *is* scored pre-merge (weighted mean
    6.0), letting the test show points/coverage both *increase* rather than
    jumping from a NOT_SCORABLE dimension.
    """
    moat_dim = Dimension(
        name="MOAT",
        max_points=5.0,
        metric_scores=[
            (0.7, Value.of(6.0, unit="score")),
            (0.3, Value.null(NullState.NOT_SCORABLE, unit="score", warnings=[judgment_marker(MOAT_REQUEST_ID)])),
        ],
    )
    cat = Category(name="business_analysis", max_points=5.0, dimensions=[moat_dim])
    metrics = [
        MetricRow(
            metric_id="moat_classification",
            state=NullState.NOT_SCORABLE,
            unit="score",
            formula_id="moat_classification",
            formula_version="1.0",
            score="NOT_SCORABLE",
            confidence=50.0,
        )
    ]
    return SpecialistOutput(
        agent_id="business_analysis",
        status=status_from_coverage(cat.coverage()),
        security=_security(),
        knowledge_timestamp="2026-07-16T21:00:00+00:00",
        category=CategoryStats(
            max_points=5.0, awarded_points=cat.points(), score_10=cat.score10(), confidence=80.0
        ),
        coverage=cat.coverage(),
        dimensions=[moat_dim],
        metrics=metrics,
        judgment_requests=[
            JudgmentRequest(
                request_id=MOAT_REQUEST_ID,
                agent_id="business_analysis",
                metric_id="moat_classification",
                question="Classify the moat.",
                schema_hint="one of Wide|Narrow|None",
            )
        ],
    )


def _risk_output() -> SpecialistOutput:
    """Two judgment requests: REG_REQUEST_ID feeds a marked NOT_SCORABLE
    dimension slot (numeric schema_hint); THESIS_REQUEST_ID has no
    dimension slot at all (array-shaped, context-only per DECISION_RULES'
    "qualitative claim never becomes a score" rule)."""
    reg_dim = Dimension(
        name="REGULATORY",
        max_points=2.0,
        metric_scores=[
            (1.0, Value.null(NullState.NOT_SCORABLE, unit="score", warnings=[judgment_marker(REG_REQUEST_ID)])),
        ],
    )
    cat = Category(name="risk_analysis", max_points=2.0, dimensions=[reg_dim])
    metrics = [
        MetricRow(
            metric_id="regulatory_exposure_score",
            state=NullState.NOT_SCORABLE,
            unit="score",
            formula_id="regulatory_exposure_score",
            formula_version="1.0",
            score="NOT_SCORABLE",
            confidence=0.0,
        ),
        MetricRow(
            metric_id="thesis_killers",
            state=NullState.NOT_SCORABLE,
            unit="",
            formula_id="thesis_killers",
            formula_version="1.0",
            score="NOT_SCORABLE",
            confidence=0.0,
        ),
    ]
    return SpecialistOutput(
        agent_id="risk_analysis",
        status=status_from_coverage(cat.coverage()),
        security=_security(),
        knowledge_timestamp="2026-07-16T21:00:00+00:00",
        category=CategoryStats(
            max_points=2.0, awarded_points=cat.points(), score_10=cat.score10(), confidence=70.0
        ),
        coverage=cat.coverage(),
        dimensions=[reg_dim],
        metrics=metrics,
        judgment_requests=[
            JudgmentRequest(
                request_id=REG_REQUEST_ID,
                agent_id="risk_analysis",
                metric_id="regulatory_exposure_score",
                question="Score regulatory/legal exposure 0-10 (10=clean).",
                schema_hint="float 0-10",
            ),
            JudgmentRequest(
                request_id=THESIS_REQUEST_ID,
                agent_id="risk_analysis",
                metric_id="thesis_killers",
                question="List >=3 thesis-killer risks.",
                schema_hint="array of >=3 {risk, probability_assumption, impact}",
            ),
        ],
    )


# --- collect_requests ------------------------------------------------------


def test_collect_requests_gathers_across_all_outputs_in_order():
    outputs = [_business_output(), _risk_output()]
    reqs = collect_requests(outputs)
    assert [r.request_id for r in reqs] == [MOAT_REQUEST_ID, REG_REQUEST_ID, THESIS_REQUEST_ID]


def test_collect_requests_empty_when_no_judgment_requests():
    out = _business_output().model_copy(update={"judgment_requests": []})
    assert collect_requests([out]) == []


# --- schema_hint_ok / judgment_marker --------------------------------------


def test_schema_hint_ok_enum():
    assert schema_hint_ok("one of Wide|Narrow|None", "Wide")
    assert not schema_hint_ok("one of Wide|Narrow|None", "Medium")
    assert not schema_hint_ok("one of Wide|Narrow|None", 5.0)


def test_schema_hint_ok_numeric():
    assert schema_hint_ok("float 0-10", 7.5)
    assert not schema_hint_ok("float 0-10", "seven")
    assert not schema_hint_ok("float 0-10", True)  # bool is not a real numeric answer


def test_schema_hint_ok_array():
    assert schema_hint_ok("array of >=3 {risk, impact}", {"items": [1, 2, 3]})
    assert not schema_hint_ok("array of >=3 {risk, impact}", "not an array")


def test_schema_hint_ok_permissive_fallback():
    assert schema_hint_ok("any free-form object", {"note": "x"})


def test_judgment_marker_format():
    assert judgment_marker("foo:bar") == "JUDGMENT_REQUIRED:foo:bar"


# --- merge_overlay: happy path (enum answer -> ordinal score) --------------


def test_merge_overlay_scores_enum_answer_and_increases_points_and_coverage():
    before = _business_output()
    assert before.coverage == pytest.approx(0.7)
    assert before.category.awarded_points == pytest.approx(3.0)  # 5.0 * (0.7*6/0.7) / 10

    judgment = Judgment(
        request_id=MOAT_REQUEST_ID,
        answer="Wide",
        evidence_class=EvidenceClass.E,
        source="claude-sub-agent:business-analysis",
        rationale="Spread persistence 92%, margin range stable, no concentration flag.",
    )
    after = merge_overlay([before], [judgment])[0]

    assert after is not before
    assert after.coverage == pytest.approx(1.0)
    # weighted mean now 0.7*6 + 0.3*10 = 7.2 -> points = 5.0 * 7.2/10 = 3.6
    assert after.category.awarded_points == pytest.approx(3.6)
    assert after.category.awarded_points > before.category.awarded_points
    assert after.coverage > before.coverage
    assert after.status == "COMPLETE"  # coverage 1.0 crosses COVERAGE_COMPLETE

    row = next(r for r in after.metrics if r.metric_id == "moat_classification")
    assert row.state is None
    assert row.value == pytest.approx(10.0)  # "Wide" is first (best) of 3 enum options
    assert row.score == pytest.approx(10.0)
    assert row.evidence_class == EvidenceClass.E
    assert row.source == "claude-sub-agent:business-analysis"
    assert any("JUDGMENT_SCORE_FROM_ENUM_ORDINAL" in w for w in row.warnings)

    # original input is untouched (frozen models, new instances returned)
    assert before.coverage == pytest.approx(0.7)
    assert before.metrics[0].state == NullState.NOT_SCORABLE

    assert any(MOAT_REQUEST_ID in a for a in after.assumptions)


def test_merge_overlay_round_trip_collect_then_answer_then_merge():
    outputs = [_business_output()]
    requests = collect_requests(outputs)
    assert len(requests) == 1
    req = requests[0]

    judgment = Judgment(
        request_id=req.request_id,
        answer="Wide",
        evidence_class=EvidenceClass.E,
        source="analyst",
        rationale="round trip",
    )
    merged = merge_overlay(outputs, [judgment])
    assert merged[0].coverage == pytest.approx(1.0)
    assert merged[0].category.awarded_points > outputs[0].category.awarded_points


# --- merge_overlay: numeric answer ------------------------------------------


def test_merge_overlay_numeric_answer_used_directly_as_score():
    before = _risk_output()
    judgment = Judgment(
        request_id=REG_REQUEST_ID,
        answer=8.5,
        evidence_class=EvidenceClass.E,
        source="analyst",
        rationale="one open inquiry, immaterial",
    )
    after = merge_overlay([before], [judgment])[0]

    assert after.category.awarded_points == pytest.approx(2.0 * 8.5 / 10.0)
    assert after.coverage == pytest.approx(1.0)
    row = next(r for r in after.metrics if r.metric_id == "regulatory_exposure_score")
    assert row.value == pytest.approx(8.5)
    assert row.score == pytest.approx(8.5)


def test_merge_overlay_numeric_answer_clamped_to_0_10():
    before = _risk_output()
    judgment = Judgment(
        request_id=REG_REQUEST_ID, answer=15.0, evidence_class=EvidenceClass.E, source="analyst"
    )
    after = merge_overlay([before], [judgment])[0]
    row = next(r for r in after.metrics if r.metric_id == "regulatory_exposure_score")
    assert row.value == pytest.approx(10.0)


# --- merge_overlay: answer that cannot be reduced to a score ----------------


def test_merge_overlay_dict_answer_recorded_as_context_only_not_scored():
    before = _risk_output()
    judgment = Judgment(
        request_id=THESIS_REQUEST_ID,
        answer={"items": [{"risk": "customer concentration"}, {"risk": "regulatory"}, {"risk": "fx"}]},
        evidence_class=EvidenceClass.Q,
        source="analyst",
        rationale="three plausible thesis killers",
    )
    after = merge_overlay([before], [judgment])[0]

    # no dimension slot is marked for THESIS_REQUEST_ID -> dimension/coverage/points untouched
    assert after.coverage == pytest.approx(before.coverage)
    assert after.category.awarded_points == pytest.approx(before.category.awarded_points)

    row = next(r for r in after.metrics if r.metric_id == "thesis_killers")
    assert row.state == NullState.NOT_SCORABLE  # still not scorable
    assert row.score == "NOT_SCORABLE"
    assert row.evidence_class == EvidenceClass.Q  # but evidence/source now recorded
    assert row.source == "analyst"
    assert any("NOT_REDUCIBLE_TO_SCORE" in w for w in row.warnings)


# --- merge_overlay: rejections ----------------------------------------------


def test_merge_overlay_unknown_request_id_raises():
    before = _business_output()
    judgment = Judgment(
        request_id="business_analysis:does_not_exist",
        answer="Wide",
        evidence_class=EvidenceClass.E,
        source="analyst",
    )
    with pytest.raises(UnknownJudgmentRequestError):
        merge_overlay([before], [judgment])


def test_merge_overlay_missing_evidence_class_rejected_silently():
    before = _business_output()
    judgment = Judgment(request_id=MOAT_REQUEST_ID, answer="Wide", evidence_class=None, source="analyst")
    after = merge_overlay([before], [judgment])[0]
    assert after is before  # untouched: nothing accepted for this output
    assert after.coverage == pytest.approx(before.coverage)


def test_merge_overlay_missing_source_rejected_silently():
    before = _business_output()
    judgment = Judgment(
        request_id=MOAT_REQUEST_ID, answer="Wide", evidence_class=EvidenceClass.E, source=""
    )
    after = merge_overlay([before], [judgment])[0]
    assert after is before
    assert after.coverage == pytest.approx(before.coverage)


def test_merge_overlay_schema_hint_mismatch_rejected_silently():
    before = _business_output()
    judgment = Judgment(
        request_id=MOAT_REQUEST_ID,
        answer="Medium",  # not one of Wide|Narrow|None
        evidence_class=EvidenceClass.E,
        source="analyst",
    )
    after = merge_overlay([before], [judgment])[0]
    assert after is before
    row = after.metrics[0]
    assert row.state == NullState.NOT_SCORABLE  # never touched


# --- merge_overlay: multi-output routing -------------------------------------


def test_merge_overlay_only_touches_the_matching_output():
    biz, risk = _business_output(), _risk_output()
    judgment = Judgment(
        request_id=REG_REQUEST_ID, answer=9.0, evidence_class=EvidenceClass.E, source="analyst"
    )
    after = merge_overlay([biz, risk], [judgment])
    assert after[0] is biz  # business untouched
    assert after[1] is not risk  # risk updated
    assert after[1].coverage == pytest.approx(1.0)


def test_merge_overlay_multiple_judgments_same_output_applied_together():
    before = _risk_output()
    judgments = [
        Judgment(request_id=REG_REQUEST_ID, answer=6.0, evidence_class=EvidenceClass.E, source="a"),
        Judgment(
            request_id=THESIS_REQUEST_ID,
            answer={"items": ["x", "y", "z"]},
            evidence_class=EvidenceClass.Q,
            source="a",
        ),
    ]
    after = merge_overlay([before], judgments)[0]
    reg_row = next(r for r in after.metrics if r.metric_id == "regulatory_exposure_score")
    thesis_row = next(r for r in after.metrics if r.metric_id == "thesis_killers")
    assert reg_row.value == pytest.approx(6.0)
    assert thesis_row.state == NullState.NOT_SCORABLE
    assert after.coverage == pytest.approx(1.0)  # only reg slot feeds a dimension


def test_merge_overlay_empty_judgments_returns_outputs_unchanged():
    before = [_business_output(), _risk_output()]
    after = merge_overlay(before, [])
    assert after[0] is before[0]
    assert after[1] is before[1]
