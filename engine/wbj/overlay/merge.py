"""Task 20: judgment overlay — lets a Claude sub-agent fill in the
`JudgmentRequest`s the 6 Cerebro specialists (Tasks 15-19) leave
`NOT_SCORABLE` (moat classification, catalyst probability, TAM tier,
thesis killers, ...), then folds the answers back into a rescored
`SpecialistOutput`.

Two entry points:

- `collect_requests(outputs)` gathers every `JudgmentRequest` across the 6
  specialist outputs, for writing `judgment_requests.json` (the file a
  Claude sub-agent reads and answers).
- `merge_overlay(outputs, judgments)` takes those answers back
  (`wbj.schemas.overlay.Judgment`), validates each against its request's
  `schema_hint`, and returns a new list of `SpecialistOutput` with the
  judged metrics scored and `category`/`coverage`/`status` recomputed via
  `wbj.specialists.common.rescore` (Task 14) — never by re-deriving the
  point math here.

Design decisions (documented once here rather than re-litigated per call
site):

1. **Locating the Dimension slot a `JudgmentRequest` feeds.**
   `wbj.core.scoring.Dimension.metric_scores` is a bare `list[(weight,
   Value)]` — it carries no `metric_id`, so there is no way to recover
   "which slot did metric X occupy" from a `Dimension` alone. Rather than
   add that field to `Dimension`/`SpecialistOutput` (a `common.py` /
   `scoring.py` change this task was told to avoid — "Do NOT modify
   existing modules"), this module defines a **marker convention**: a
   specialist that wants a `NOT_SCORABLE` dimension slot to become
   judgment-fillable tags that slot's `Value.warnings` with
   `judgment_marker(request.request_id)` (`"JUDGMENT_REQUIRED:<request_id>"`).
   `merge_overlay` scans every dimension's `metric_scores` for that marker
   and replaces the matching tuple's `Value` (same weight) once a judgment
   resolves it.

   None of the Task 15-19 specialists emit this marker yet (they predate
   this task and were told not to be modified for it) — `merge_overlay`
   degrades gracefully when it's absent: the flat `output.metrics` row is
   still updated (see point 2) and `rescore()` is still called, but a
   judgment with no marked dimension slot leaves `category`/`coverage`
   unchanged for that judgment (there's nothing in the dimension math to
   move). Wiring the marker into the specialists themselves is future
   work, not part of this task's scope.

2. **The flat `metrics` list.** `MetricRow.metric_id` *is* a stable key
   (financial.py's judgment-only rows already follow "request.metric_id ==
   row.metric_id" exactly, e.g. `FIN-GR-004`), so `merge_overlay` always
   replaces the `MetricRow` whose `metric_id` matches the judgment
   request's `metric_id` (creating one if the specialist never registered
   a placeholder row for it), independent of whether a marked dimension
   slot was found.

3. **Answer -> 0-10 score.** `Judgment.answer` is `float | str | dict`
   (`wbj.schemas.overlay.Answer`). A numeric answer is used directly
   (clamped to [0, 10]) as the metric's score. A string answer that
   matches its request's `schema_hint` enum (`"one of A|B|C"`) is mapped
   to an evenly spaced ordinal score, first-listed option = 10 (best) down
   to last = 0 (worst) — a mechanical convention *of this module*, not a
   Cerebro-defined rubric (no Cerebro doc pins e.g. "Wide moat = 10.0"),
   used only so a judged qualitative label can move a metric out of
   NOT_SCORABLE with an auditable, reproducible number; the exact mapping
   is recorded in the resulting `MetricRow.warnings`. A `dict` answer (or
   a string that isn't one of the declared enum options) cannot be reduced
   to a single score generically and is *never* scored — per this
   project's own rule ("Una afirmación cualitativa solo puede incluirse
   como contexto; jamas se convierte en score salvo que una regla del
   Cerebro lo defina explicitamente"): the answer is still recorded as
   context (rationale/source in the row's warnings and in
   `output.assumptions`), but the metric stays `NOT_SCORABLE` and no
   dimension slot is touched.

4. **Rejection vs. hard error.** An unknown `request_id` (one that
   doesn't correspond to any `JudgmentRequest` across `outputs`) is a
   caller/programming error — `merge_overlay` raises
   `UnknownJudgmentRequestError` for it. A judgment that resolves to a
   real request but is missing `evidence_class`/`source`, or whose answer
   fails its request's `schema_hint` check, is a plausible/expected bad
   sub-agent answer — `merge_overlay` *silently skips* it (that output is
   returned unchanged for that judgment) rather than raising, so one
   malformed answer in a batch doesn't sink the rest. Both are
   deliberately named "rejected" in the task brief; this module documents
   choosing hard-fail for the first and soft-skip for the second.

5. **Output hash.** The brief says "assigns new output hash if the
   envelope carries one." `SpecialistOutput` (Task 14, `common.py`) has no
   hash field today, so this is a no-op — nothing to reassign.

6. **`category.confidence`.** `rescore()` (Task 14) deliberately leaves
   `category.confidence` untouched unless the caller updates it
   separately, since a judgment answer changes *what* was scored, not how
   much the underlying evidence should be trusted, and no Cerebro doc
   defines a formula for "how a judgment shifts category confidence."
   This module follows that lead and does not touch `category.confidence`.
   The per-metric `MetricRow.confidence` for a judged row *is* set: to the
   specialist's original placeholder confidence if a `NOT_SCORABLE` row
   already existed for that `metric_id`, else 100.0 for a brand-new row (a
   directly-supplied, sourced answer carries its own full provenance).
"""

from __future__ import annotations

import re
from collections import defaultdict

from wbj.core.nullstates import NullState, Value
from wbj.core.scoring import Dimension
from wbj.schemas.overlay import Answer, Judgment
from wbj.specialists.common import JudgmentRequest, MetricRow, SpecialistOutput, rescore

JUDGMENT_MARKER_PREFIX = "JUDGMENT_REQUIRED:"


def judgment_marker(request_id: str) -> str:
    """The `Value.warnings` marker a specialist tags a `NOT_SCORABLE`
    dimension slot with to make it judgment-fillable (see module
    docstring, point 1)."""
    return f"{JUDGMENT_MARKER_PREFIX}{request_id}"


class UnknownJudgmentRequestError(ValueError):
    """Raised by `merge_overlay` when a `Judgment.request_id` doesn't match
    any `JudgmentRequest` across the given outputs."""

    def __init__(self, request_id: str) -> None:
        super().__init__(f"no JudgmentRequest with request_id={request_id!r} in the given outputs")
        self.request_id = request_id


def collect_requests(outputs: list[SpecialistOutput]) -> list[JudgmentRequest]:
    """Gather every `JudgmentRequest` across the 6 specialist outputs, in
    output order, for writing `judgment_requests.json`."""
    return [req for output in outputs for req in output.judgment_requests]


# --- schema_hint validation & answer -> score -------------------------------

_ENUM_RE = re.compile(r"one of ([A-Za-z0-9_]+(?:\s*\|\s*[A-Za-z0-9_]+)+)", re.IGNORECASE)
_NUMERIC_HINT_RE = re.compile(r"\bfloat\b|\bnumber\b|0-10|probability", re.IGNORECASE)
_ARRAY_HINT_RE = re.compile(r"\barray of\b", re.IGNORECASE)


def _enum_options(schema_hint: str) -> list[str] | None:
    m = _ENUM_RE.search(schema_hint)
    if not m:
        return None
    return [opt.strip() for opt in m.group(1).split("|")]


def schema_hint_ok(schema_hint: str, answer: Answer) -> bool:
    """Best-effort structural check that `answer` is shaped the way
    `schema_hint` describes. Deliberately loose (this is a sanity check on
    an LLM's free-text answer, not a formal schema language): an
    enum hint requires a matching string, a numeric hint requires a
    number, an array hint requires `{"items": [...]}`; anything else
    (dict/object-shaped hints) accepts a `dict` and otherwise falls back
    to permissive (True) rather than guessing at an undeclared shape.
    """
    options = _enum_options(schema_hint)
    if options is not None:
        return isinstance(answer, str) and answer.strip() in options
    if _ARRAY_HINT_RE.search(schema_hint):
        return isinstance(answer, dict) and isinstance(answer.get("items"), list)
    if _NUMERIC_HINT_RE.search(schema_hint):
        return isinstance(answer, (int, float)) and not isinstance(answer, bool)
    return True


def _score_from_answer(schema_hint: str, answer: Answer) -> tuple[float | None, str | None]:
    """Convert a validated answer into a `(score, note)` pair. `score` is
    `None` when the answer can't be reduced to a single 0-10 number (see
    module docstring, point 3); `note` documents how the score was derived
    (or why it wasn't), to be recorded on the resulting `MetricRow`.
    """
    if isinstance(answer, bool):  # bool is an int subclass; not a real numeric answer
        return None, None
    if isinstance(answer, (int, float)):
        score = max(0.0, min(10.0, float(answer)))
        return score, f"JUDGMENT_SCORE_FROM_NUMERIC_ANSWER: {answer!r} -> {score:.4f}"
    options = _enum_options(schema_hint)
    if options and isinstance(answer, str) and answer.strip() in options:
        n = len(options)
        idx = options.index(answer.strip())
        score = 10.0 if n == 1 else 10.0 * (n - 1 - idx) / (n - 1)
        return score, (
            f"JUDGMENT_SCORE_FROM_ENUM_ORDINAL: {answer!r} is option {idx + 1}/{n} of "
            f"{options!r} (first=best) -> {score:.4f}"
        )
    return None, "JUDGMENT_ANSWER_NOT_REDUCIBLE_TO_SCORE: recorded as context only"


# --- merge --------------------------------------------------------------


def merge_overlay(outputs: list[SpecialistOutput], judgments: list[Judgment]) -> list[SpecialistOutput]:
    """Apply `judgments` (Claude sub-agent answers) to `outputs`,
    rescoring each affected specialist via `wbj.specialists.common.rescore`.

    Returns a new list, same order/length as `outputs`; outputs with no
    accepted judgment are returned unchanged (same object). See the module
    docstring for the unknown-request-id / rejection rules.
    """
    requests_by_id: dict[str, tuple[int, JudgmentRequest]] = {}
    for output_index, output in enumerate(outputs):
        for req in output.judgment_requests:
            requests_by_id[req.request_id] = (output_index, req)

    accepted: dict[int, list[tuple[JudgmentRequest, Judgment]]] = defaultdict(list)
    for judgment in judgments:
        if judgment.request_id not in requests_by_id:
            raise UnknownJudgmentRequestError(judgment.request_id)
        if judgment.evidence_class is None or not judgment.source:
            continue  # soft-reject: missing evidence_class/source (see module docstring, point 4)
        output_index, req = requests_by_id[judgment.request_id]
        if not schema_hint_ok(req.schema_hint, judgment.answer):
            continue  # soft-reject: answer doesn't match schema_hint
        accepted[output_index].append((req, judgment))

    new_outputs = list(outputs)
    for output_index, updates in accepted.items():
        new_outputs[output_index] = _apply_updates(outputs[output_index], updates)
    return new_outputs


def _apply_updates(
    output: SpecialistOutput, updates: list[tuple[JudgmentRequest, Judgment]]
) -> SpecialistOutput:
    metrics_by_id: dict[str, MetricRow] = {row.metric_id: row for row in output.metrics}
    dimensions = list(output.dimensions)
    new_assumptions = list(output.assumptions)

    for req, judgment in updates:
        score, note = _score_from_answer(req.schema_hint, judgment.answer)
        warnings = [w for w in (note,) if w]
        if judgment.rationale:
            warnings.append(f"JUDGMENT_RATIONALE: {judgment.rationale}")

        value = (
            Value.of(
                score,
                unit="score",
                evidence_class=judgment.evidence_class,
                source_name=judgment.source,
                warnings=warnings,
            )
            if score is not None
            else Value.null(
                NullState.NOT_SCORABLE,
                unit="score",
                evidence_class=judgment.evidence_class,
                source_name=judgment.source,
                warnings=warnings,
            )
        )

        existing = metrics_by_id.get(req.metric_id)
        metrics_by_id[req.metric_id] = MetricRow.from_value(
            req.metric_id,
            value,
            formula_id=existing.formula_id if existing else req.metric_id,
            formula_version=existing.formula_version if existing else "judgment-overlay",
            score=score if score is not None else "NOT_SCORABLE",
            confidence=existing.confidence if existing else 100.0,
            source=judgment.source,
        )

        new_assumptions.append(
            f"judgment {req.request_id!r} (metric={req.metric_id!r}) answered by sub-agent: "
            f"{judgment.answer!r} (evidence_class={judgment.evidence_class}, source={judgment.source!r})"
        )

        if score is not None:
            marker = judgment_marker(req.request_id)
            dimensions = [_apply_marker(dim, marker, score, judgment) for dim in dimensions]

    merged = rescore(output, dimensions=dimensions, metrics=list(metrics_by_id.values()))
    return merged.model_copy(update={"assumptions": new_assumptions})


def _apply_marker(dimension: Dimension, marker: str, score: float, judgment: Judgment) -> Dimension:
    """Replace the `metric_scores` tuple whose `Value.warnings` carries
    `marker` (see module docstring, point 1) with a scored `Value`,
    keeping the same weight. No-op if no slot in `dimension` carries the
    marker.
    """
    new_scores: list[tuple[float, Value]] = []
    changed = False
    for weight, value in dimension.metric_scores:
        if marker in value.warnings:
            new_scores.append(
                (
                    weight,
                    Value.of(
                        score,
                        unit="score",
                        evidence_class=judgment.evidence_class,
                        source_name=judgment.source,
                    ),
                )
            )
            changed = True
        else:
            new_scores.append((weight, value))
    if not changed:
        return dimension
    return dimension.model_copy(update={"metric_scores": new_scores})
