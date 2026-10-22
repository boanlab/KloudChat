"""Report checks for verify (find only; verify.repair rewrites)."""

from __future__ import annotations

from app.services import key_figures, quality_gate, stated_days, stated_policy, units
from app.services.verify import Check, Doc, Finding, model_check


def _report():
    from app.services import report  # imported late: circular import

    return report


def _one(sentence: str) -> list[dict]:
    return [{"heading": "", "content": sentence}]


async def numbers(doc: Doc):
    """Weekly rate vs yearly total; CAGR not derivable from its own figures."""
    report = _report()
    found = [
        Finding(i, sentence, "numeric", why, rank=1,
                still=lambda s: bool(report.numeric_issues(_one(s))))
        for i, sentence, why in report.numeric_issues(doc.sections)
    ]
    return found, {}


async def metrics(doc: Doc):
    """One metric with two values in two places."""
    report = _report()
    found = []
    # material's own paired values shown on purpose
    groups = [g for g in report.metric_conflicts(doc.sections)
              if not key_figures.explains([s for _, s in g], doc.conflicts)]
    for group in groups[:4]:
        sentences = [s for _, s in group]
        for index, sentence in group:
            others = " / ".join(s[:120] for s in sentences if s != sentence)
            found.append(Finding(index, sentence, "metric_conflict",
                                 f"같은 지표를 다른 곳에서는 다르게 적었다: {others}. "
                                 "자료로 맞는 값을 확인해 하나로 맞춰라.", rank=2))
    return found, {}


async def innards(doc: Doc):
    """References to source internals the reader lacks (「매뉴얼 제2장」)."""
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
    """Changed figures, unit-slipped formulas, scaled amounts, placeholders."""
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
    """Code-passed figure sentences, sent to the judge model."""

    @model_check
    async def check(doc: Doc):
        report = _report()
        if not doc.settled:
            return [], {}
        flagged, spent = await report.judge_figure_sentences(
            doc.sections, doc.settled, judge=judge, api_key=api_key, material=doc.material
        )
        # shown, not rewritten: judge over-flags, rewrite unverifiable
        return [Finding(i, s, "judged_figure", why, rank=1, auto=False,
                        still=lambda t, s=doc.settled: bool(key_figures.conflicts(t, s)))
                for i, s, why in flagged], spent

    return check


async def scaled_metrics(doc: Doc):
    """Named metric off by a power of ten elsewhere."""
    whole = "\n".join(str(sec.get("content") or "") for sec in doc.sections)
    found = []
    for sentence, name, written, usual in quality_gate.scaled_metric_slips(whole):
        index = next((i for i, sec in enumerate(doc.sections)
                      if sentence in str(sec.get("content") or "")), None)
        if index is None:
            continue
        usual_text = f"{usual:,.0f}"
        found.append(Finding(
            index, sentence, "scaled_metric",
            f"「{name}」을 이 문서의 다른 곳에서는 모두 {usual_text}(으)로 적었는데 이 문장은 "
            f"{written:,.0f}로 적어 자릿수가 다르다. 같은 기준의 값이면 {usual_text}로 고치고, "
            "그 값으로 계산한 수치도 맞춰라.", rank=1,
            still=lambda t, n=name, u=usual_text: bool(quality_gate.scaled_metric_slips(
                f"{n} {u}원. {n} {u}원.\n{t}"))))
    return found, {}


def policy_material(doc: Doc) -> tuple[str, str]:
    """What a stated limit, approval or rule may come from: the person's material, and the
    source quotes for a sentence that cites them."""
    return (doc.given or doc.material,
            "\n".join(str(src.get("quote") or "") for src in doc.sources or []))


async def stated_rules(doc: Doc):
    """Limits, approvals and rules stated as settled that the material only asks about."""
    report, (material, quotes) = _report(), policy_material(doc)
    found = [
        Finding(i, sentence, "unsourced_policy", stated_policy.why(said), rank=1,
                still=lambda s, m=material, q=quotes: bool(stated_policy.untraced(s, m, q)))
        for i, sentence, said in stated_policy.findings(doc.sections, material,
                                                        report._REFERENCE_HEADING, quotes)
    ]
    return found, {}


async def stated_dates(doc: Doc):
    """Items put on a weekday, or in a week, the person's material never gives them."""
    report, material = _report(), doc.given or doc.material

    def context(i: int, piece: str) -> str:
        sec = doc.sections[i]
        return stated_days.context_of(str(sec.get("heading") or ""),
                                      str(sec.get("content") or ""), piece)

    found = [
        Finding(i, piece, "unsourced_day", stated_days.why(items), rank=1,
                still=lambda s, m=material, c=context(i, piece): bool(
                    stated_days.untraced(s, m, c)))
        for i, piece, items in stated_days.findings(doc.sections, material,
                                                    report._REFERENCE_HEADING)
    ]
    return found, {}


CODE_CHECKS: list[Check] = [numbers, metrics, innards, figures, scaled_metrics, stated_rules,
                            stated_dates]

