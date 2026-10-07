"""Checks `verify` runs on a written report; they only find, `verify.repair` rewrites.

Code checks run before and after the repair; model checks run once, before it.
"""

from __future__ import annotations

from app.services import key_figures, quality_gate, units
from app.services.verify import Check, Doc, Finding, model_check


def _report():
    from app.services import report  # the report's readers; imported late, report imports us

    return report


def _one(sentence: str) -> list[dict]:
    return [{"heading": "", "content": sentence}]


async def numbers(doc: Doc):
    """A weekly rate against its yearly total, a CAGR its own figures do not give."""
    report = _report()
    found = [
        Finding(i, sentence, "numeric", why, rank=1,
                still=lambda s: bool(report.numeric_issues(_one(s))))
        for i, sentence, why in report.numeric_issues(doc.sections)
    ]
    return found, {}


async def metrics(doc: Doc):
    """One metric stated with two values in two places: each place is told the other."""
    report = _report()
    found = []
    for group in report.metric_conflicts(doc.sections)[:4]:
        sentences = [s for _, s in group]
        for index, sentence in group:
            others = " / ".join(s[:120] for s in sentences if s != sentence)
            found.append(Finding(index, sentence, "metric_conflict",
                                 f"같은 지표를 다른 곳에서는 다르게 적었다: {others}. "
                                 "자료로 맞는 값을 확인해 하나로 맞춰라.", rank=2))
    return found, {}


async def innards(doc: Doc):
    """「매뉴얼 제2장」 and 「해당 자료」: a source's insides the reader does not have."""
    report = _report()
    found = [
        Finding(i, sentence, "source_innards",
                "독자에게 없는 자료의 장·절이나 「해당 자료」를 가리킨다. "
                "자료 이름을 밝히거나 내용을 직접 말하는 문장으로 고쳐라.", rank=4,
                still=lambda s: bool(report._SOURCE_INNARDS.search(s)))
        for i, sentences in report.source_innard_sentences(doc.sections).items()
        for sentence in sentences
    ]
    return found, {}


def _slipped(s: str) -> bool:
    return bool(units.slipped_formulas(s))


def _placeholder(s: str) -> bool:
    return bool(quality_gate._PLACEHOLDER.search(s))


async def figures(doc: Doc):
    """Changed settled figures, unit-slipped formulas, scaled amounts and placeholders."""
    report = _report()

    def changed(s: str) -> bool:
        return bool(key_figures.conflicts(s, doc.settled))

    def scaled(s: str) -> bool:
        return bool(quality_gate.scaled_numbers(_one(s), doc.material))

    found = []
    for i, sentence, why in report.flagged_sentences(doc.sections, doc.material, doc.settled):
        if "식 「" in why:
            code, rank, still = "formula_unit_slip", 0, _slipped
        elif "정해진" in why:
            code, rank, still = "settled_figure_changed", 1, changed
        elif "자릿수" in why:
            code, rank, still = "carried_number_changed", 2, scaled
        else:
            code, rank, still = "placeholder", 3, _placeholder
        found.append(Finding(i, sentence, code, why, rank=rank, still=still))
    return found, {}


def judge_figures(judge: str, api_key: str) -> Check:
    """The figure sentences the code let through, put to the judge model."""

    @model_check
    async def check(doc: Doc):
        report = _report()
        if not doc.settled:
            return [], {}
        flagged, spent = await report.judge_figure_sentences(
            doc.sections, doc.settled, judge=judge, api_key=api_key
        )
        # Shown, not rewritten: the judge also flags passing mentions, and no rule can
        # confirm its rewrite.
        return [Finding(i, s, "judged_figure", why, rank=1, auto=False,
                        still=lambda t, s=doc.settled: bool(key_figures.conflicts(t, s)))
                for i, s, why in flagged], spent

    return check


async def scaled_metrics(doc: Doc):
    """A named metric (CAC, ARPU …) written a power of ten off its value elsewhere."""
    whole = "\n".join(str(sec.get("content") or "") for sec in doc.sections)
    found = []
    for sentence, name, written, usual in quality_gate.scaled_metric_slips(whole):
        index = next((i for i, sec in enumerate(doc.sections)
                      if sentence in str(sec.get("content") or "")), None)
        if index is None:
            continue
        usual_text = f"{usual:,.0f}"
        # Fixed once the sentence, read beside the usual value twice, raises nothing.
        found.append(Finding(
            index, sentence, "scaled_metric",
            f"「{name}」을 이 문서의 다른 곳에서는 모두 {usual_text}(으)로 적었는데 이 문장은 "
            f"{written:,.0f}로 적어 자릿수가 다르다. 같은 기준의 값이면 {usual_text}로 고치고, "
            "그 값으로 계산한 수치도 맞춰라.", rank=1,
            still=lambda t, n=name, u=usual_text: bool(quality_gate.scaled_metric_slips(
                f"{n} {u}원. {n} {u}원.\n{t}"))))
    return found, {}


CODE_CHECKS: list[Check] = [numbers, metrics, innards, figures, scaled_metrics]

