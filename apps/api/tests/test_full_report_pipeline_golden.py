"""The whole report pipeline, fed malformed model answers.

Each cleanup step has its own test; this one runs them in their real order on one document
— outline, approval, draft, cleanup, figures, captions, the final passes — and asks the
finished sections to pass the quality gate a reader's eye would apply. A step that undoes
another (a joiner that erases paragraphs, a caption pass that misses a malformed table)
fails here even when every unit test passes.
"""

from __future__ import annotations

import json

import pytest

from app.services import diagrams, quality_gate, report
from tests.conftest import both_passes

LONG = " ".join(
    f"보안 운영은 {n}단계에서 탐지와 대응을 묶어 자동화하는 방향으로 이동하고 있습니다."
    for n in range(1, 16)
)

DRAFT = f"""## 요약

AI 보안 시장은 연평균 41%로 성장합니다. 이 보고서는 대상 환경과 배포 방식을 다룹니다.

## 시장 현황

글로벌 AI 보안 시장은 연평균 성장률(CAGR) 14.62%로 2032년까지 706억 달러에 이릅니다. \
매뉴얼 제2장 'AI 보안 위협 분류'는 위협을 세 갈래로 나눕니다. 프로мп트 인젝션이 대표 위협입니다.

{LONG} 시장 지표는 다음과 같습니다. ```kpi
706억 달러 | 2032년 시장 규모
```

## 배포 방식 비교

배포 방식을 비교하면 〔표〕와 같은 차이가 있습니다.
기준 | 클라우드 | 온프레미스
비용 | 낮음 | 높음
통제 | 보통 | 높음

표: 배포 방식 비교

〔그림〕과 같이 요청은 게이트웨이와 마스킹을 거쳐 서버에 닿습니다.

[[그림: flow | 요청 처리 흐름 | 사용자 요청 → 게이트웨이 → 마스킹 → LLM 서버]]

## 주요 기능

주요 기능은 탐지, 대응, 감사의 세 가지입니다. ```callout
핵심
세 기능은 하나의 흐름으로 이어집니다.
```

## 결론

조직은 배포 방식과 통제 수준을 함께 검토해야 합니다.
"""


class _Gateway:
    """Answers by what is asked, with malformed replies."""

    def __init__(self) -> None:
        self.outline_asks = 0

    def answer(self, prompt: str) -> str:
        if "JSON 객체로만 답하라" in prompt or "앞선 답은 JSON 이 아니었다" in prompt:
            self.outline_asks += 1
            if self.outline_asks == 1:  # notes, not the JSON asked for
                return ('1. **제목**: "AI 보안 동향"\n2. **Sections**:\n   - 구조:\n'
                        "     1. 요약 (개요)\n     2. 시장 현황\n")
            return json.dumps({"title": "AI 보안 솔루션 동향", "sections": [
                "요약", "시장 현황", "배포 방식 비교", "주요 기능", "결론"]}, ensure_ascii=False)
        if "Write the whole report in one pass" in prompt:
            return DRAFT
        if "보고서의 문장들에 문제가 있다" in prompt:
            # The one repair: each flagged sentence mended as the model would have.
            items = json.loads(prompt[prompt.index("항목:") + len("항목:"):].strip())
            mended = []
            for item in items:
                s = item["sentence"]
                if "지표" in item["problem"]:
                    s = (s.replace("글로벌 AI 보안 시장은", "AI 사이버보안 시장 전체는", 1)
                         if s.startswith("글로벌")
                         else s.replace("AI 보안 시장은", "생성형 AI 보안 시장은", 1))
                s = s.replace("매뉴얼 제2장 'AI 보안 위협 분류'는",
                              "과기정통부·KISA의 『AI 보안 위협 대응 매뉴얼』은")
                mended.append(s)
            return json.dumps(mended, ensure_ascii=False)
        if "make them consistent" in prompt.replace("\n", " "):
            start = prompt.index("Sentences:") + len("Sentences:")
            sentences = json.loads(prompt[start:prompt.index("Answer with")].strip())
            scoped = [
                s.replace("글로벌 AI 보안 시장은", "AI 사이버보안 시장 전체는", 1)
                if s.startswith("글로벌")
                else s.replace("AI 보안 시장은", "생성형 AI 보안 시장은", 1)
                for s in sentences
            ]
            return json.dumps(scoped, ensure_ascii=False)
        if "The reader has only the report" in prompt:
            start = prompt.index("Sentences:") + len("Sentences:")
            sentences = json.loads(prompt[start:prompt.index("Answer with")].strip())
            return json.dumps([s.replace("매뉴얼 제2장 'AI 보안 위협 분류'는",
                                         "과기정통부·KISA의 『AI 보안 위협 대응 매뉴얼』은")
                               for s in sentences], ensure_ascii=False)
        if "도식의 종류" in prompt:
            return "[]"
        return "{}"


@pytest.fixture
def model(monkeypatch):
    gateway = _Gateway()

    async def litellm_config():
        return "http://mock-litellm", "unused"

    async def complete(_model, messages, _api_key, max_tokens):
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        return gateway.answer(prompt), {"inputTokens": 1, "outputTokens": 1}

    async def make(planned, *, model, api_key, slide):
        source = "flowchart LR\n  a[사용자 요청] --> b[게이트웨이] --> c[마스킹] --> d[LLM 서버]"
        return ({"figure": planned.figure, "description": planned.description, "source": source,
                 "caption": planned.caption, "key": "k"}, {"inputTokens": 0, "outputTokens": 0})

    monkeypatch.setattr(report.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(report, "_complete", complete)
    monkeypatch.setattr(diagrams, "make", make)
    return gateway


async def test_the_finished_report_passes_the_quality_gate(model):
    request = "AI 보안 솔루션 동향 보고서를 써 줘"
    events = await both_passes(report, request=request, model="m", api_key="k")
    proposal = next(e for e in events if e["type"] == "proposal")
    assert proposal["plan"]["sections"][0] == "요약"  # the notes never became headings
    sections = next(e for e in events if e["type"] == "report")["sections"]
    findings = quality_gate.report_findings(sections)
    assert findings == [], findings
    text = "\n\n".join(s["content"] for s in sections)
    assert "**표 1. 배포 방식 비교**" in text and "표 1과 같은 차이" in text
    assert "그림 1과 같이" in text and "```mermaid" in text
    assert "프롬프트 인젝션" in text and "생성형 AI 보안 시장은" in text
    assert "『AI 보안 위협 대응 매뉴얼』" in text
    # The kpi repeats a figure the text gives, so it goes (drop_redundant_kpi); either way
    # no fence is left on the end of a sentence.
    assert "다음과 같습니다. ```" not in text and "세 가지입니다.\n\n```callout" in text
    step = next(e for e in events if e.get("id") == "quality")
    assert step["status"] == "done"


async def test_the_assumptions_are_written_first_and_given_to_every_other_section(monkeypatch):
    order: list[str] = []
    prompts: dict[str, str] = {}

    async def litellm_config():
        return "http://mock-litellm", "unused"

    async def complete(_model, messages, _api_key, max_tokens):
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        if "JSON 객체로만 답하라" in prompt:
            return json.dumps({"title": "밀키트 기획안", "sections": [
                "사업 개요", "핵심 가정", "수익 모델", "실행 로드맵", "리스크"]},
                ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}
        if 'You are writing only the "' in prompt:
            heading = prompt.split('You are writing only the "', 1)[1].split('"', 1)[0]
            order.append(heading)
            prompts[heading] = prompt
            body = ("| 항목 | 값 |\n|---|---|\n| 판매가 | 20,000원 |\n| 월 고정비 | 3,000만 원 |"
                    if heading == "핵심 가정" else f"{heading}의 내용입니다.")
            return body, {"inputTokens": 1, "outputTokens": 1}
        return "[]" if "도식의 종류" in prompt else "{}", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(report, "_complete", complete)
    monkeypatch.setattr(report, "_long_form", lambda request: True)
    await both_passes(report, request="신사업 기획안을 써 줘", model="m", api_key="k")
    assert order[0] == "핵심 가정"
    for heading in ("사업 개요", "수익 모델", "실행 로드맵", "리스크"):
        assert "확정된 핵심 가정" in prompts[heading] and "3,000만 원" in prompts[heading]
    assert "확정된 핵심 가정" not in prompts["핵심 가정"]


def test_the_rewriting_passes_cannot_leave_what_the_final_tidy_mends():
    from app.services import quality_gate, report

    body = (
        "가구당 연간 식료품비 400만 원을 곱하면 "
        "TAM은 1,200만 × 400만 원 = 4,800,000억 원입니다.\n\n"
        "쓸 수 있는 수치 목록에 따르면, 수익 모델 절이 아직 작성되지 않았습니다. "
        "월 고정비는 3,000만 원입니다.\n\n"
        "```\ncallout\n3월 말에 중단 기준을 확인합니다\n"
        "판매량이 1,600박스를 밑돌면 멈춥니다.\n```\n\n"
        "**표 5. 운영 리스크와 대응**\n\n"
        "| 리스크 | 영향 | 대응 | 확인 시점 |\n| --- | --- | --- | --- |\n| 소분·정량 오차 | 1~\n\n"
        "끝 문단입니다."
    )
    sections = [{"heading": "실행 로드맵", "content": body}]
    found = {f["code"] for f in quality_gate.report_findings(sections)}
    assert {"prompt_talk", "fence_language_apart", "cut_table_row"} <= found

    tidy = report.final_tidy(sections)[0]["content"]
    assert "48조 원" in tidy and "4,800,000억" not in tidy
    assert "수치 목록" not in tidy and "월 고정비는 3,000만 원입니다." in tidy
    assert "```callout\n3월 말에" in tidy
    # The table had no whole row: it goes, and its caption with it.
    assert "소분·정량" not in tidy and "표 5." not in tidy and "끝 문단입니다." in tidy
    tidied = [{"heading": "실행 로드맵", "content": tidy}]
    after = {f["code"] for f in quality_gate.report_findings(tidied)}
    assert not after & {"prompt_talk", "fence_language_apart", "cut_table_row"}


def test_a_cut_last_row_goes_and_the_whole_rows_stay():
    from app.services import report

    head = "| 항목 | 값 | 근거 |\n| --- | --- | --- |\n| 고정비 | 3,000만 원 | 임대 |"
    assert report.drop_cut_rows(head + "\n| 변동비 | 8,0") == head
    # A row the writer simply did not close is still whole.
    whole = "| 항목 | 값 | 근거 |\n| --- | --- | --- |\n| 고정비 | 3,000만 원 | 임대"
    assert report.drop_cut_rows(whole) == whole


def test_a_range_written_as_two_amounts_does_not_fail_the_number_checks():
    """「월 3만 4만 원」 (a range with its 〜 lost) does not raise inside numeric_issues."""
    from app.services import report

    sections = [{"heading": "시장", "content": (
        "시장은 2024년 3만 4만 원 수준에서 2028년 1조 원으로 연평균 25% 성장합니다."
    )}]
    report.numeric_issues(sections)
    assert report._amount_or_none("3만 4만") is None
    assert report._amount_or_none("1,200천만") == 1.2e10


def test_a_check_that_fails_keeps_the_sections_and_the_others_run():
    import asyncio

    from app.services import verify

    async def broken(doc):
        raise ValueError("could not convert string to float: '4만'")

    async def fine(doc):
        return [], {"inputTokens": 1}

    sections = [{"heading": "a", "content": "본문"}]
    found, spent = asyncio.run(verify.find(verify.Doc(sections), [broken, fine]))
    assert found == [] and spent["inputTokens"] == 1


def test_an_open_comparison_cell_is_searched_for_and_filled_with_its_source(monkeypatch):
    import asyncio

    from app.services import report, research

    body = (
        "**표 1. 솔루션 비교**\n\n"
        "| 솔루션 | 대상 환경 | 배포 방법 |\n| --- | --- | --- |\n"
        "| 시큐어브리지 | 생성형 AI 활용 환경 [2] | 온프레미스·SaaS [2] |\n"
        "| AI SOC(지니언스) | 보안운영센터 [4] | (자료에 배포 방식 명시 없음) |"
    )
    sections = [{"heading": "비교", "content": body}]
    assert report.table_gaps(sections) == [(0, 5, 2, "AI SOC(지니언스)", "배포 방법")]

    async def run(query, *, model, api_key, max_sources):
        assert query == "AI SOC(지니언스) 배포 방법"
        return research.Findings(
            sources=[{"ordinal": 1, "title": "AI SOC", "url": "https://genians.co.kr/x"}],
            context="[1] 지니언스 AI SOC\n본문 발췌:\n온프레미스와 클라우드 SaaS로 제공한다.",
            searched=True,
        )

    async def complete(model, messages, api_key, max_tokens):
        return json.dumps({"same_target": True, "same_measure": True,
                           "value": "온프레미스·클라우드 SaaS [1]",
                           "quote": "온프레미스와 클라우드 SaaS로 제공한다"}, ensure_ascii=False), {
            "inputTokens": 10, "outputTokens": 5}

    monkeypatch.setattr(research, "run", run)
    monkeypatch.setattr(report, "_complete", complete)
    sources = [{"ordinal": n, "url": f"https://a/{n}"} for n in range(1, 5)]
    out, spent = asyncio.run(
        report.fill_gaps_from_search(sections, sources, model="m", api_key="k")
    )
    filled = "| AI SOC(지니언스) | 보안운영센터 [4] | 온프레미스·클라우드 SaaS [5] |"
    assert filled in out[0]["content"]
    assert sources[-1]["ordinal"] == 5 and sources[-1]["url"] == "https://genians.co.kr/x"
    assert spent["inputTokens"] == 10


def test_a_cell_the_pages_do_not_settle_stays_open(monkeypatch):
    import asyncio

    from app.services import report, research

    body = "| 솔루션 | 배포 방법 |\n| --- | --- |\n| X | 확인 필요 |"

    async def run(query, *, model, api_key, max_sources):
        return research.Findings(sources=[{"ordinal": 1, "url": "u"}],
                                 context="[1] t\n본문 발췌:\n무관한 내용", searched=True)

    async def complete(model, messages, api_key, max_tokens):
        return "없음", {}

    monkeypatch.setattr(research, "run", run)
    monkeypatch.setattr(report, "_complete", complete)
    sources: list[dict] = []
    out, _ = asyncio.run(report.fill_gaps_from_search(
        [{"heading": "a", "content": body}], sources, model="m", api_key="k"))
    assert out[0]["content"] == body and sources == []


def test_a_product_comparison_reads_each_named_products_own_pages(monkeypatch):
    import asyncio

    from app.services import research

    first = research.Findings(
        sources=[{"ordinal": 1, "url": "https://news/a", "title": "AI 보안 동향"}],
        context="[1] AI 보안 동향\n본문 발췌:\nAkto Argus와 시큐어브리지가 출시됐다.",
        searched=True, usage={"inputTokens": 1, "outputTokens": 1},
    )

    async def ask(prompt, model, api_key, max_tokens):
        # One name the pages carry, one they do not (the model's own).
        return '["Akto Argus (Akto)", "Imaginary Shield (Nobody)"]', {
            "inputTokens": 5, "outputTokens": 5}

    asked = []

    async def run(query, *, model, api_key, max_sources):
        asked.append(query)
        return research.Findings(
            sources=[{"ordinal": 1, "url": "https://docs.akto.io/self-hosted", "title": "Akto"},
                     {"ordinal": 2, "url": "https://news/a", "title": "dup"}],
            context=(
                "[1] Akto\n본문 발췌:\n자체 호스팅·CI/CD 통합\n\n[2] dup\n본문 발췌:\n같은 기사"
            ),
            searched=True, queries=[query],
        )

    monkeypatch.setattr(research, "_ask", ask)
    monkeypatch.setattr(research, "run", run)
    request = "AI 보안 솔루션의 대상 환경, 배포 방법, 주요 기능을 비교 분석해 줘"
    out = asyncio.run(research.deepen_for_products(request, first, model="m", api_key="k"))
    assert asked == ["Akto Argus (Akto) 대상 환경 배포 방법 주요 기능"]
    assert [s["url"] for s in out.sources] == ["https://news/a", "https://docs.akto.io/self-hosted"]
    assert out.sources[1]["ordinal"] == 2
    assert "[2] Akto\n본문 발췌:\n자체 호스팅·CI/CD 통합" in out.context
    assert "같은 기사" not in out.context
    # A request that compares nothing is left alone.
    plain = research.Findings(sources=first.sources, context=first.context, searched=True)
    assert asyncio.run(research.deepen_for_products(
        "밀키트 시장 규모를 알려 줘", plain, model="m", api_key="k")) is plain


_QUESTIONS = "\n\n".join(
    f"### {n}. {stem}\n① 가 ② 나 ③ 다 ④ 라\n\n**정답**: ②\n**해설**: 풀이 {n}"
    for n, stem in enumerate(
        ["다음 중 원소에 해당하는 것은?", "화합물에 대한 설명으로 옳은 것은?",
         "나트륨의 원소 기호로 옳은 것은?", "물의 화학식으로 옳은 것은?",
         "혼합물을 고르시오.", "산소 분자를 이루는 원자의 수는?"], start=1)
)


def test_a_summarised_question_set_is_carried_as_written():
    from app.services import report

    material = "# 수업 흐름\n\n본문\n\n# 형성평가\n\n" + _QUESTIONS + "\n\n# 안내문\n\n학부모님께"
    request = "지도안을 써 줘. 구성은 평가 계획(형성평가 문항 포함).\n\n---\n" + material
    sections = [
        {"heading": "평가 계획", "content": "객관식 6문항으로 개념 이해를 확인한다."},
        {"heading": "참고문헌", "content": ""},
    ]
    out = report.carry_question_set(sections, material, request)
    carried = out[0]["content"]
    assert "**1. 다음 중 원소에 해당하는 것은?**" in carried and "**해설**: 풀이 6" in carried
    assert "학부모님께" not in carried and "---" not in carried
    # Written in already: left alone.
    assert report.carry_question_set(out, material, request) == out
    # Not asked for questions: left alone.
    plain = "요약해 줘\n\n---\n" + material
    assert report.carry_question_set(sections, material, plain) == sections


def test_the_report_gate_reads_an_amount_changed_from_the_material():
    from app.services import quality_gate

    sections = [{"heading": "시장", "content": "구독 의향 가구는 120만 가구입니다."}]
    found = quality_gate.report_findings(sections, "구독 의향 10%인 12만 가구")
    assert any(f["code"] == "carried_number_changed" for f in found)


def test_a_doubled_or_empty_citation_is_tidied_and_a_link_is_not():
    from app.services import report

    body = "시장은 커진다 [[5]]. 근거는 없다 []. 링크 [[6]](https://x.org)는 둔다."
    tidy = report.final_tidy([{"heading": "a", "content": body}])[0]["content"]
    assert tidy == "시장은 커진다 [5]. 근거는 없다. 링크 [[6]](https://x.org)는 둔다."


def test_a_flagged_sentence_is_mended_once_and_kept_only_when_the_finding_is_gone(monkeypatch):
    import asyncio


    material = "구독 의향 10%인 12만 가구가 월 2회 주문합니다."
    sections = [{"heading": "시장", "content": (
        "구독 의향 가구는 120만 가구로 잡습니다 [3]. 단가는 (미정)입니다. 끝 문장입니다."
    )}]
    replies = {
        "120만": "구독 의향 가구는 12만 가구로 잡습니다 [3].",
        "미정": "단가는 정하지 않았습니다 [9].",  # a citation the sentence did not have
    }

    async def complete(model, messages, api_key, max_tokens):
        prompt = messages[0]["content"]
        items = json.loads(prompt[prompt.index("항목:") + len("항목:"):].strip())
        answers = [replies["120만" if "자릿수" in i["problem"] else "미정"] for i in items]
        return json.dumps(answers, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    from app.services import checks, verify

    out, _, spent = asyncio.run(verify.verify_and_repair(
        verify.Doc(sections, material=material), [checks.figures],
        complete=complete, model="m", api_key="k"))
    body = out[0]["content"]
    assert "12만 가구로 잡습니다 [3]." in body and "120만" not in body
    # The second reply brought a citation of its own: refused, the sentence stays.
    assert "단가는 (미정)입니다." in body and spent["inputTokens"] == 1


def test_a_unit_slip_in_an_operand_is_not_pushed_further_and_captions_settle():
    from app.services import quality_gate, report, units

    slip = "월 매출은 500명 × 4,000만원 × 2회 = 4,000만 원으로 산출됩니다."
    assert units.fix_written_arithmetic(slip) == slip  # not 「400억」 on both sides
    assert units.slipped_formulas(slip) == ["500명 × 4,000만원 × 2회 = 4,000만 원"]
    found = quality_gate.report_findings([{"heading": "수익", "content": slip}])
    assert any(f["code"] == "formula_unit_slip" for f in found)

    body = "월별로 쌓아 보면 다음과 같습니다.\n\n| 월 | 판매량 |\n| --- | --- |\n| 1월 | 600 |"
    once = report.caption_figures_and_tables([{"heading": "a", "content": body}])
    assert report.caption_figures_and_tables(once) == once


def test_a_reference_follows_its_table_when_an_earlier_one_is_dropped():
    from app.services import report

    table = "| 항목 | 값 |\n| --- | --- |\n| 가 | 1 |"
    body = (
        "손익은 표 4, 리스크는 표 6에 정리합니다.\n\n"
        f"**표 4. 손익**\n\n{table}\n\n"
        f"**표 6. 리스크**\n\n{table}"
    )
    out = report.caption_figures_and_tables([{"heading": "a", "content": body}])[0]["content"]
    assert "손익은 표 1, 리스크는 표 2에 정리합니다." in out and "**표 2. 리스크**" in out
    assert "표 6" not in out and "(표 2" not in out


def test_the_question_set_ends_where_the_questions_do():
    """Three ways the models wrote it: the number on its own line (「**문항 1**」), the
    answers under their own heading, a notice to parents right after — whose 「착용해
    주십시오」 lines are requests, not questions."""
    from app.services import report

    split = "\n\n".join(
        f"**문항 {n}** [1차시]\n다음 중 {w}의 원소 기호로 옳은 것은?\n① N ② Na ③ S ④ Si"
        for n, w in enumerate(["나트륨", "철", "금", "은", "구리"], start=1)
    )
    answers = "## 정답 및 해설\n\n### 객관식\n\n| 문항 | 정답 |\n| --- | --- |\n| 1 | ② |"
    notice = ("**과학 실험 수업 안내문**\n\n학부모님께\n\n1. 보안경을 꼭 착용해 주십시오.\n"
              "2. 앞치마를 챙겨 주십시오.\n3. 실험실에서는 뛰지 마십시오.")
    tail = "## 출제 포인트 요약\n\n고르게 나눴다."
    block = report.question_set(f"{split}\n\n{answers}\n\n{tail}\n\n{notice}")
    assert len(report._question_lines(block)) == 5
    assert "| 1 | ② |" in block and "출제 포인트" not in block and "학부모" not in block


def test_products_are_searched_along_the_requests_own_axes_and_an_outline_is_not_one():
    """Product searches follow the request's own axes; no fixed phrase is sent with every
    product, and a request that compares no products searches none."""
    from app.services import research

    security = ("최근 AI 보안 관련 솔루션들이 많이 등장하고 있는데, 대상 환경, 배포 방법, "
                "주요 기능, 적용 기술 등을 종합적으로 분석해서 기술 동향 보고서 작성해줘")
    axes = ["대상 환경", "배포 방법", "주요 기능", "적용 기술"]
    assert research.comparison_axes(security) == axes
    plan = ("아래 내용으로 신사업 기획안을 써 줘. 구성은 사업 개요, 시장 분석, 수익 모델과 "
            "손익분기 분석, 경쟁 및 차별화, 실행 로드맵, 리스크와 대응.")
    assert research.comparison_axes(plan) == []
    kits = "밀키트 구독 서비스 3곳의 가격, 배송 주기, 메뉴 수를 비교해 줘"
    assert research.comparison_axes(kits) == ["가격", "배송 주기", "메뉴 수"]


def test_a_picture_inside_a_plain_code_block_is_drawn_not_printed():
    """A figure mark inside a ```text block is drawn, not printed as base64."""
    from app.services import quality_gate, report

    body = ("안전 점검 순서입니다.\n\n```text\n점검 → 착용 → 실험\n"
            "![그림 4. 실험 안전 점검 흐름](data:image/png;base64,iVBORw0KGgo)\n```\n\n"
            "```mermaid\nflowchart LR\n a --> b\n```")
    sections = [{"heading": "안전 지도", "content": body}]
    assert any(f["code"] == "picture_in_code" for f in quality_gate.report_findings(sections))
    tidy = report.final_tidy(sections)[0]["content"]
    assert "```text" not in tidy and "점검 → 착용 → 실험" in tidy
    assert "\n![그림 4. 실험 안전 점검 흐름](data:image/png;base64,iVBORw0KGgo)\n" in tidy
    assert "```mermaid\nflowchart LR" in tidy


def test_the_export_draws_a_picture_left_in_a_code_block():
    """Whatever the pipeline missed or a hand edit put back, the PDF never prints base64."""
    from app.services import report_export

    one_pixel = ("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
                 "AAAABJRU5ErkJggg==")
    picture = f"![그림 1. 흐름](data:image/png;base64,{one_pixel})"
    for text in (f"```text\n점검\n{picture}\n```", f"```\n점검\n{picture}"):
        lines = report_export._markdown_to_lines(text)
        assert ("body", "점검", "", 0) in lines
        assert any(kind == "image" for kind, *_ in lines)
        assert not any(kind == "body" and "base64" in str(body) for kind, body, *_ in lines)


def test_a_drawn_block_before_a_picture_keeps_its_closing_fence():
    """A callout followed by a figure keeps its closing 「```」; it is not taken for the opening
    of a plain block holding the picture."""
    from app.services import quality_gate, report

    body = ("문단입니다.\n\n```callout\n제목\n본문 한 줄\n```\n\n"
            "![그림 4. 흐름](data:image/png;base64,iVBORw0KGgo)\n\n끝 문단입니다.\n\n"
            "```kpi\n2,500박스 | 손익분기\n```")
    assert report.unfence_pictures(body) == body
    tidy = report.final_tidy([{"heading": "a", "content": body}])
    assert not any(f["code"] == "unbalanced_fence" for f in quality_gate.report_findings(tidy))


def test_an_amount_written_with_cheon_is_read_whole_and_never_doubled():
    """「1억 8천만」 is read whole, not as 「1억 8」, and is never replaced twice
    (「1억 8,000만 8,000만 8,00」)."""
    from app.services import units

    assert units._ko_number("1억 8천만") == 180_000_000
    assert units._ko_number("5천") == 5_000
    for right in ("운전자금은 3,000만 원 × 6개월 = 1억 8천만 원입니다.",
                  "5,000명 × 3만 원 = 1억 5천만 원입니다."):
        assert units.fix_written_arithmetic(right) == right
    wrong = "3,000만 원 × 6개월 = 2억 원. 2억 원이 필요합니다."
    fixed = units.fix_written_arithmetic(wrong)
    assert fixed == "3,000만 원 × 6개월 = 1억 8,000만 원. 1억 8,000만 원이 필요합니다."
    assert units.fix_written_arithmetic(fixed) == fixed


def test_a_scraped_page_loses_its_frame_and_keeps_its_text_and_tables():
    """A scraped page's frame (「로그인이 필요한 서비스입니다」, menus) is taken off before the
    writer reads it."""
    from app.services import report, research

    page = ("확인\n로그인이 필요한 서비스입니다.\n로그인 취소\n"
            "[본문 내용 바로가기](https://e.x/a)\n"
            "홈\n강좌정보\n수강대상\n다운로드\n고객센터\n\n"
            "원소는 한 종류의 원자로 이루어진 물질이다.\n\n"
            "| 구분 | 예 |\n| --- | --- |\n| 원소 | 산소 |\n| 원소 | 철 |\n"
            "| 원소 | 금 |\n| 원소 | 은 |")
    text = research.page_text(page)
    assert "로그인" not in text and "강좌정보" not in text and "바로가기" not in text
    assert "원소는 한 종류의 원자로" in text and "| 원소 | 은 |" in text
    # A question set never runs on into a search block.
    material = "\n\n".join(f"{n}. 다음 중 원소는 무엇인가?\n① 물 ② 산소" for n in range(1, 6))
    block = report.question_set(material + "\n\n검색어: 원소\n본문 발췌:\n로그인")
    assert "검색어" not in block and "본문 발췌" not in block


def test_the_format_mends_run_twice_change_nothing():
    """The verify stage normalizes again after its repair: that is only safe because the
    second run of every mend leaves the document as the first left it."""
    from app.services import key_figures, report

    body = (
        "손익분기는 표 6에 정리합니다 [[3]].\n\n```\ncallout\n제목\n본문\n```\n\n"
        "**표 4. 손익**\n\n| 항목 | 값 |\n| --- | --- |\n| 판매가 | 2,000원 |\n\n"
        "**표 6. 리스크**\n\n| 리스크 | 대응 |\n| --- | --- |\n"
        "| 물류 | 계약 |\n| 품질 | 검수 |\n\n"
        "목표 4,000박스면 (4,000박스 × 12,000원) - 3,000만 원 = "
        "영업이익 1억 8,000만 원입니다.\n\n"
        "```text\n점검\n![그림 1. 흐름](data:image/png;base64,iVBORw0KGgo)\n```"
    )
    settled = [key_figures.Figure("박스당 판매가", "20,000원", ["판매가"])]
    sections = [{"heading": "수익", "content": body},
                {"heading": "참고문헌", "content": "[3] 자료"}]
    once = report.normalize_document(sections, settled=settled, sources=[{"ordinal": 3}])
    twice = report.normalize_document(once, settled=settled, sources=[{"ordinal": 3}])
    assert once == twice
    text = once[0]["content"]
    assert "영업이익 1,800만 원" in text and "```callout" in text and "```text" not in text


def test_a_picture_pasted_with_a_report_reaches_the_writer_as_its_caption():
    """A picture in the source report reaches the deck writer as its caption, not as base64
    that would use up the 24,000-character budget."""
    from app.services.context import pasted_material

    picture = "![그림 3. 손익 흐름](data:image/png;base64," + "A" * 50_000 + ")"
    request = ("아래 기획안으로 경영진 보고 자료를 만들어 줘.\n\n---\n"
               f"## 수익 모델\n\n{picture}\n\n" + "로드맵 내용입니다. " * 30)
    pasted = pasted_material(request)
    assert "[그림: 그림 3. 손익 흐름]" in pasted and "base64" not in pasted
    assert pasted.rstrip().endswith("로드맵 내용입니다.")


def test_a_cell_is_filled_only_with_the_same_thing_measured_the_same_way(monkeypatch):
    """An assumption cell is never searched, and a value is taken only for the same target
    and measure, its words and numbers on the page."""
    import asyncio

    from app.services import report, research

    plan = ("| 항목 | 값 | 근거 |\n| --- | --- | --- |\n| 월 해지율 | (미정) | 파일럿 |\n"
            "| 박스 원가 | (미정) | 견적 |")
    assert [g for g in report.table_gaps([{"heading": "a", "content": plan}])
            if not report._OWN_ASSUMPTION.search(f"{g[3]} {g[4]}")] == []

    body = "| 제품 | 배포 방법 |\n| --- | --- |\n| X 보안 | (미정) |"
    asked = []

    async def run(query, *, model, api_key, max_sources):
        return research.Findings(sources=[{"ordinal": 1, "url": "u", "title": "t"}],
                                 context="[1] t\n본문 발췌:\nY 건설은 현장 설치형으로 공급한다.",
                                 searched=True)

    async def complete(model, messages, api_key, max_tokens):
        asked.append(1)
        return json.dumps({"same_target": False, "same_measure": True,
                           "value": "현장 설치형 [1]", "quote": "현장 설치형으로 공급한다"},
                          ensure_ascii=False), {}

    monkeypatch.setattr(research, "run", run)
    monkeypatch.setattr(report, "_complete", complete)
    sources: list[dict] = []
    out, _ = asyncio.run(report.fill_gaps_from_search(
        [{"heading": "a", "content": body}], sources, model="m", api_key="k"))
    assert asked and out[0]["content"] == body and sources == []


def test_a_body_wrapped_as_json_and_cut_off_is_the_body():
    """A section wrapped as JSON and cut off before closing (`{"content": "...`) is unwrapped
    to its body."""
    from app.services.report import final_tidy, unwrap_json_prose

    cut = ('{\n"content": "각 차시는 45분으로 운영합니다.\\n\\n'
           '1차시는 원자의 종류를 구별합니다.\n2차시는')
    assert unwrap_json_prose(cut).startswith("각 차시는 45분으로 운영합니다.\n\n1차시는")
    closed = '{"본문": "수업은 원소의 뜻에서 시작합니다.\n각 차시는 45분입니다."}'
    assert unwrap_json_prose(closed) == "수업은 원소의 뜻에서 시작합니다.\n각 차시는 45분입니다."
    tidy = final_tidy([{"heading": "차시별 지도 계획", "content": cut}])[0]["content"]
    assert not tidy.lstrip().startswith("{") and '"content"' not in tidy and "\\n" not in tidy


def test_an_answer_cut_at_the_ceiling_is_asked_again_with_room(monkeypatch):
    import asyncio

    import httpx

    from app.services import report, settings_store

    asked = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        asked.append(body["max_tokens"])
        cut = len(asked) == 1
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "length" if cut else "stop",
                         "message": {"content": "1차시 | 2차" if cut
                                     else "1차시 | 2차시 | 3차시"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": body["max_tokens"] // 10}})

    async def config():
        return "http://llm", None

    real = httpx.AsyncClient
    monkeypatch.setattr(settings_store, "litellm_config", config)
    monkeypatch.setattr(report.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))
    text, used = asyncio.run(report._complete("local/m", [{"role": "user", "content": "x"}],
                                              "k", 1200))
    assert asked == [1200, 2400] and text.endswith("3차시")
    assert used == {"inputTokens": 20, "outputTokens": 360}
