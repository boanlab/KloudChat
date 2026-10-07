"""Documents take derived numbers from code, and cite only sources that exist."""

import json

import pytest

from app.services import calc, credibility, report
from app.services.tools.base import ToolResult

ENGINEERING = (
    "아래 내용으로 회로실험 결과 보고서를 써 줘. 결과(측정 표와 계산한 이득·이론값·오차).\n\n---\n"
    "R=1kΩ, C=0.1μF, 입력 2Vpp. 측정 출력: 100Hz 1.98, 1590Hz 1.40, 10000Hz 0.32 Vpp"
)


def test_calculation_is_needed_only_when_asked_over_data():
    assert calc.needed(ENGINEERING)
    assert not calc.needed("아래 개요로 세미나 리포트를 써 줘.\n\n---\n1920년대 신문 3종")
    # The word in pasted material does not ask for it.
    assert not calc.needed("요약해 줘.\n\n---\n이득 1 2 3 4 5 계산")


@pytest.mark.asyncio
async def test_code_output_becomes_the_writers_fixed_values(monkeypatch):
    class Backends:
        exec = "http://sandbox"

    async def tools_config():
        return Backends()

    ran = {}

    async def execute_code(args):
        ran["code"] = args["code"]
        out = "1590 Hz | 이득 = -3.098 dB | 이론 = -3.015 dB\n차단 주파수 = 1591.549 Hz"
        return ToolResult(content=f"stdout:\n{out}")

    async def complete(model, messages, api_key, max_tokens):
        assert "1590Hz 1.40" in messages[0]["content"]
        return "```python\nimport math\nprint('x')\n```", {"inputTokens": 5, "outputTokens": 7}

    from app.services.tools import builtin

    monkeypatch.setattr(calc.settings_store, "tools_config", tools_config)
    monkeypatch.setattr(builtin, "execute_code", execute_code)
    block, usage, status = await calc.computed_values(
        ENGINEERING, [], complete=complete, model="m", api_key="k"
    )
    assert status == "done" and "import math" in ran["code"]
    assert "차단 주파수 = 1591.549 Hz" in block and "그대로" in block
    # Two independent runs agreed: both are paid for.
    assert usage == {"inputTokens": 10, "outputTokens": 14}


@pytest.mark.asyncio
async def test_no_sandbox_leaves_the_document_alone(monkeypatch):
    class Backends:
        exec = ""

    async def tools_config():
        return Backends()

    monkeypatch.setattr(calc.settings_store, "tools_config", tools_config)
    block, _, status = await calc.computed_values(
        ENGINEERING, [], complete=None, model="m", api_key="k"
    )
    assert (block, status) == ("", "unavailable")


def test_citations_of_pasted_notes_are_dropped_and_real_ones_kept():
    sections = [
        {"heading": "개요", "content": "4명이 15주 안에 완성합니다 [2]. 통계청 자료입니다 [1]."},
        {"heading": "참고문헌", "content": "[1] 통계청. 2024 가계동향."},
    ]
    out = report.drop_unbacked_citations(sections, [])
    assert out[0]["content"] == "4명이 15주 안에 완성합니다. 통계청 자료입니다 [1]."
    shelf = [{"url": "https://kostat.go.kr"}, {"url": "https://doi.org/x"}]
    kept = report.drop_unbacked_citations(sections[:1], shelf)
    assert kept[0]["content"] == sections[0]["content"]


def test_source_tiers():
    assert credibility.tier("https://kostat.go.kr/board") == 3
    assert credibility.tier("https://doi.org/10.1/abc") == 3
    assert credibility.tier("https://s-space.snu.ac.kr/handle/1") == 3
    assert credibility.tier("https://www.yna.co.kr/view/AKR") == 2
    assert credibility.tier("https://en.wikipedia.org/wiki/X") == 1
    assert credibility.tier("https://blog.naver.com/someone/1") == 0
    assert credibility.tier("https://www.educba.com/rc-filter") == 0
    assert credibility.tier("https://someone.tistory.com/12") == 0


def test_a_question_in_the_outline_is_not_a_section():
    text = '{"title": "계획서", "sections": ["연구 배경", {"question": "어떤 출처?", ' \
        '"options": ["A", "B"]}, {"heading": "선행 연구"}, "{\'question\': \'x\'}", "결론"]}'
    assert report._parse_outline(text)[1] == ["연구 배경", "선행 연구", "결론"]


def test_korean_author_year_entries_run_together_are_split():
    c = "김상현 (1990). 『동아일보의 역사』. 동아일보사. 이영호 (1995). 『식민지 조선』. 한길사."
    out = report.reference_list_only([{"heading": "참고문헌", "content": c}])[0]["content"]
    assert out.split("\n\n")[1].startswith("이영호 (1995).")


def test_an_empty_reference_section_is_rebuilt_from_cited_works():
    sections = [
        {"heading": "본론",
         "content": "김상현 (1990)은 동아일보를 보았고, (이영호, 1995)도 같다. "
                    "박재순 (1986)은 다르다."},
        {"heading": "참고문헌", "content": "\"신문은 복합적 공론장을 형성했다.\""},
    ]
    material = (
        "*   **출처:**\n    *   김상현, 『동아일보의 역사』, 동아일보사, 1990년.\n"
        "    *   이영호, 『조선일보의 역사』, 조선일보사, 1995년.\n"
    )
    out = report.fill_reference_list(sections, material)[1]["content"].split("\n\n")
    assert out[0] == "김상현, 『동아일보의 역사』, 동아일보사, 1990년."
    assert out[1] == "이영호, 『조선일보의 역사』, 조선일보사, 1995년."
    assert out[2].strip() == "박재순 (1986). (서지 확인 필요)"
    # A list with entries is left as it is.
    entry = {"heading": "참고문헌", "content": "김상현 (1990). 『동아일보의 역사』."}
    listed = [sections[0], entry]
    assert report.fill_reference_list(listed, material) == listed


@pytest.mark.asyncio
async def test_off_subject_hits_are_dropped_by_the_relevance_check(monkeypatch):
    from app.services import research

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "[1, 3]"}}], "usage": {}}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            return Response()

    async def litellm_config():
        return "http://llm", ""

    monkeypatch.setattr(research.httpx, "AsyncClient", Client)
    monkeypatch.setattr(research.settings_store, "litellm_config", litellm_config)
    hit = lambda u, t: (1.0, {"url": u, "title": t, "snippet": ""})  # noqa: E731
    ranked = [[hit("https://a", "1920년대 동아일보"), hit("https://b", "Eritrea public sphere")],
              [hit("https://c", "조선일보 창간")]]
    kept, _ = await research._on_subject("1920년대 조선 신문 리포트", ranked, "m", "k")
    assert [[h["url"] for _, h in keep] for keep in kept] == [["https://a"], ["https://c"]]
    assert credibility.tier("https://prezi.com/x") == 0



def test_runs_agree_on_shared_items_and_only_those_are_kept():
    first = ["최대 처짐 = 1.042 mm", "최대 굽힘응력 = 50 MPa", "안전율 = 3.300",
             "대안2 I 증가 후 처짐 = 0.6127 mm"]
    second = ["최대 처짐(이론) = 1.0417 mm", "최대 굽힘응력(이론) = 50.0000 MPa",
              "안전율(이론) = 3.3000 (-)", "대안2(I 1.728배 증가) 처짐 = 0.6028 mm"]
    assert calc.agree(first, second) and calc.agree(second, first)
    assert calc.confirmed(first, second) == first[:3]
    slipped = ["최대 처짐 = 1041.667 mm", "최대 굽힘응력 = 50 MPa", "안전율 = 3.3"]
    assert not calc.agree(first, slipped)


def test_english_glosses_on_korean_headings_are_dropped():
    text = '{"title": "설계 검토", "sections": ["검토 목적 (review purpose)", ' \
        '"계산 (calculation: deflection, stress, safety factor table)", "API 설계 (REST)", ' \
        '"결과(측정 표)"]}'
    assert report._parse_outline(text)[1] == ["검토 목적", "계산", "API 설계", "결과(측정 표)"]


def test_an_outline_cut_off_at_the_token_cap_still_gives_its_sections():
    cut = ('{"title": "AI 보안 솔루션 기술 동향", "style": "편집형", "subject": "AI 보안 솔루션", '
           '"sections": ["개요", "대상 환경", "배포 방법", "주요 기능", "적용 기')
    title, headings = report._parse_outline(cut)
    assert title == "AI 보안 솔루션 기술 동향"
    assert headings == ["개요", "대상 환경", "배포 방법", "주요 기능"]
    objects = (
        '{"title": "T", "sections": [{"heading": "배경"}, '
        '{"heading": "대상 환경"}, {"heading": "배포'
    )
    assert report._parse_outline(objects)[1] == ["배경", "대상 환경"]


def test_sentences_pointing_into_a_source_are_found():
    sections = [
        {"heading": "현황", "content": (
            "매뉴얼 제2장 'AI 보안 위협 분류 및 진단'은 위협을 세 갈래로 나눕니다[3]. "
            "랜섬웨어 공격이 고도화되고 있습니다[6].\n\n"
            "해당 매뉴얼은 대응 절차를 제시합니다[3]. "
            "자세한 내용은 p. 12에 있습니다.")},
        {"heading": "참고문헌", "content": "[3] 과기정통부. AI 보안 위협 대응 매뉴얼 제2장."},
    ]
    found = report.source_innard_sentences(sections)
    assert list(found) == [0]
    assert len(found[0]) == 3
    assert not any("랜섬웨어" in s for s in found[0])
    # 「본 보고서는」 is this report, not a source.
    own = [{"heading": "개요", "content": "본 보고서는 동향을 다룹니다."}]
    assert not report.source_innard_sentences(own)


@pytest.mark.asyncio
async def test_only_the_pointing_sentences_are_rewritten_and_citations_kept(monkeypatch):
    content = "매뉴얼 제2장 'AI 보안 위협 분류'는 위협을 세 갈래로 나눕니다[3]. 다른 문장입니다[6]."
    sections = [{"heading": "현황", "content": content}]
    plain = (
        "과기정통부·KISA의 『AI 보안 위협 대응 매뉴얼』은 위협을 데이터·모델, "
        "에이전트·공급망, 고성능 모델 위협의 세 갈래로 나눕니다[3]."
    )

    async def complete(model, messages, api_key, max_tokens):
        return f'["{plain}"]', {"inputTokens": 1, "outputTokens": 1}

    from app.services import checks, verify

    source = {"title": "AI 보안 위협 대응 매뉴얼", "publisher": "과기정통부·KISA"}
    out, left, _ = await verify.verify_and_repair(
        verify.Doc(sections, sources=[source]), [checks.innards],
        complete=complete, model="m", api_key="k")
    assert out[0]["content"] == plain + " 다른 문장입니다[6]." and left == []


def test_an_outline_written_as_notes_keeps_only_the_sections():
    prose = (
        "1. **제목**: 주제를 가리키는 명사구로 작성. "
        "\"AI 기반 사이버 보안 솔루션 기술 동향 분석\"\n"
        "2. **Subject**: 요청에 적힌 말 그대로 \"AI 보안 관련 솔루션\".\n"
        "3. **Style**: 기술 동향 분석 보고서는 '편집형'이 적절합니다.\n"
        "4. **Sections**:\n"
        "   - 요청된 항목: 대상 환경, 배포 방법, 주요 기능, 적용 기술, 종합 분석.\n"
        "   - 구조:\n"
        "     1. 서론 (연구 배경 및 목적)\n"
        "     2. 대상 환경 분석 (어떤 환경에 적용되는지)\n"
        "     3. 주요 기능 및 적용 기술 분석 (무엇을 어떻게 하는지)\n"
        "     4. 배포 모델 및 방식 분석 (어떻게 배포하는지)\n"
        "     5. 시장 및 기술 동향 종합 분석 (종합적인 추세)\n"
        "     6. 결론 및 시사점 (마무리)\n"
    )
    assert not report.outline_is_json(prose)
    assert report._parse_outline(prose)[1] == [
        "서론", "대상 환경 분석", "주요 기능 및 적용 기술 분석", "배포 모델 및 방식 분석",
        "시장 및 기술 동향 종합 분석", "결론 및 시사점",
    ]
    assert report.outline_is_json('{"title": "T", "sections": ["가", "나", "다"]}')


@pytest.mark.asyncio
async def test_one_metric_stated_two_ways_is_reconciled_against_the_sources(monkeypatch):
    sections = [
        {"heading": "요약", "content": "AI 보안 시장은 연평균 41%로 성장합니다 [4]."},
        {"heading": "현황", "content": (
            "글로벌 AI 보안 시장은 연평균 성장률(CAGR) 14.62%로 "
            "2032년까지 706억 달러에 이릅니다 [1].")},
        {"heading": "전망", "content": "성장률은 20%에서 30%로 높아질 것입니다."},  # a comparison
    ]
    groups = report.metric_conflicts(sections)
    assert len(groups) == 1 and [i for i, _ in groups[0]] == [0, 1]

    async def complete(model, messages, api_key, max_tokens):
        prompt = messages[0]["content"]
        items = json.loads(prompt[prompt.index("항목:") + len("항목:"):].strip())
        scoped = [
            i["sentence"].replace("글로벌 AI 보안 시장은", "AI 사이버보안 시장 전체는", 1)
            if i["sentence"].startswith("글로벌")
            else i["sentence"].replace("AI 보안 시장은", "생성형 AI 보안 시장은", 1)
            for i in items
        ]
        return json.dumps(scoped, ensure_ascii=False), {"inputTokens": 1, "outputTokens": 1}

    from app.services import checks, verify

    out, _, _ = await verify.verify_and_repair(
        verify.Doc(sections), [checks.metrics], complete=complete, model="m", api_key="k")
    assert out[0]["content"].startswith("생성형 AI 보안 시장은")
    assert out[1]["content"].startswith("AI 사이버보안 시장 전체는")
    assert out[2] == sections[2]


def test_a_table_rows_working_corrects_a_unit_slip_and_its_repeats():
    body = ("| 구분 | 수치 | 근거 |\n|---|---|---|\n"
            "| SOM(CAC 상한) | 38.4억 원/연 | 400명 × 8만 원 × 12 |\n"
            "| SOM(생산 상한) | 9.6억 원/연 | 8,000회 × 1만 원 × 12 |\n\n"
            "CAC 상한은 연 약 38.4억 원입니다.")
    out = report.fix_table_formulas(body)
    assert "| SOM(CAC 상한) | 3.84억 원/연 | 400명 × 8만 원 × 12 |" in out
    assert "| SOM(생산 상한) | 9.6억 원/연 |" in out  # right already
    assert out.endswith("CAC 상한은 연 약 3.84억 원입니다.")


def test_a_restated_calculation_gets_the_materials_amount_back():
    given = ("월 4,000박스 판매 시 영업이익 = 4,000 × 12,000 − 3,000만 "
             "= 4,800만 − 3,000만 = 1,800만 원")
    slipped = "마진 4억 8,000만 원에서 고정비 3,000만 원을 뺀 영업이익 1,800만 원을 얻습니다."
    assert report.fix_restated_amounts(slipped, given) == (
        "마진 4,800만 원에서 고정비 3,000만 원을 뺀 영업이익 1,800만 원을 얻습니다.")
    # Ten times a material amount, but not a restatement of that line: left alone.
    total = "10개월 누적 매출은 3억 원이고 월 고정비는 3,000만 원입니다."
    assert report.fix_restated_amounts(total, given) == total


def test_a_written_formula_in_korean_units_is_worked_out_again():
    from app.services import units

    assert units.fix_written_arithmetic("1,000만 가구 × 40만 원 × 0.03 = 1.2조 원") == (
        "1,000만 가구 × 40만 원 × 0.03 = 1,200억 원")
    assert units.fix_written_arithmetic("| 400명 × 48,000원 → 3.2억원 | 월 3.2억원 |") == (
        "| 400명 × 48,000원 → 1,920만원 | 월 1,920만원 |")
    for right in ("500세트 × 4회 × 4주 = 8,000회차", "4,000 × 12,000 − 3,000만 = 4,800만",
                  "3 × 1.11 = 3.3", "2,000만원 ÷ 5만원 = 400명"):
        assert units.fix_written_arithmetic(right) == right


def test_a_monthly_pnl_row_is_worked_out_from_the_documents_own_inputs():
    assumptions = {"heading": "가정", "content": (
        "| 항목 | 값 |\n|---|---|\n| 판매가 | 20,000원 |\n| 변동비 | 8,000원 |\n"
        "| 월 고정비 | 3,000만 원 |")}
    pnl = {"heading": "손익", "content": (
        "| 월 | 판매량(박스) | 매출 | 영업이익 |\n|---|---|---|---|\n"
        "| 1개월 | 500 | 1,000만 원 | -2,400만 원 |\n| 3개월 | 1,500 | 3,000만 원 | 600만 원 |")}
    out = report.fix_pnl_tables([assumptions, pnl])[1]["content"]
    assert out.endswith("| 3개월 | 1,500 | 3,000만 원 | -1,200만 원 |")
    # Two prices in the document: the inputs are not settled, nothing is corrected.
    two = {"heading": "제품", "content": "| 항목 | 값 |\n|---|---|\n| 판매가 | 12,000원 |"}
    assert report.fix_pnl_tables([assumptions, two, pnl])[2] == pnl
