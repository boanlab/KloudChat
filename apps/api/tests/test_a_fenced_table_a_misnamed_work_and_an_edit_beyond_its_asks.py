"""Round 11 re-check: a table the writer fenced with a broken picture prints as a table once the
picture goes (T3); a sentence naming a work cites that work and numbers follow the body (T3,
DOCS-05); an edit rewrites only the parts its asks name, and an asked summary is that many lines
without the title block again (T1)."""

from __future__ import annotations

import asyncio

from app.pipeline import revise_stages
from app.pipeline.stage import Context
from app.services import citations, pictures, report, revise

# ── a fenced table keeps printing as a table ──

FENCED = ("이러한 제한은 선행 연구에서 지적된 사항이다(표 1, 그림 2).\n\n"
          "```\n**표 1. 연구 대상 및 표본 설계**\n\n| 항목 | 내용 |\n|------|------|\n"
          "| 표본 총량 | 1,200건 |\n| 수집 기한 | 2027년 4월 말 |\n\n"
          "![그림 2. 구조](data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAA)\n```\n")


def test_a_table_fenced_with_a_broken_picture_is_a_table_after_the_picture_goes():
    for body in (FENCED, pictures.drop_broken(FENCED)):
        out = report.final_tidy([{"heading": "연구 대상", "content": body}])[0]["content"]
        assert "```" not in out
        assert "| 표본 총량 | 1,200건 |" in out and "**표 1. 연구 대상 및 표본 설계**" in out


def test_a_bare_fence_holding_only_a_table_is_unwrapped_and_code_stays():
    table = "```\n| 변수 | 단위 |\n|---|---|\n| 정확도 | % |\n```"
    assert report.unfence_pictures(table) == "| 변수 | 단위 |\n|---|---|\n| 정확도 | % |"
    code = "```\nprint('| a | b |')\n|---|---|\n```"
    assert report.unfence_pictures(code) == code
    shell = "```bash\n| a | b |\n|---|---|\n```"
    assert report.unfence_pictures(shell) == shell


# ── a named work cites that work ──

KAMATA = {"ordinal": 1, "title": "Security Design of a Citizen-Oriented RAG Chatbot",
          "author": "M KAMATA", "year": "2026", "origin": "web"}
KAWASHIMA = {"ordinal": 2, "title": "Development of rag system for local government",
             "author": "S Kawashima, S Shiramatsu", "year": "2024", "origin": "web"}
RYU = {"ordinal": 3, "title": "Implementation of RAG-LLM Based AI Agents for Public Service",
       "author": "J Ryu, Y Woo, K Lee", "year": "2025", "origin": "web"}
PAPER = {"ordinal": 4, "title": "t3_paper.pdf", "origin": "file", "attached": True,
         "originLabel": "첨부 파일"}
PAPER_TEXT = ("## t3_paper.pdf\nRetrieval-Augmented Generation for Korean Public-Sector QA\n"
              "Kim Minseo, Park Jiwon. Journal of Applied Language Technology, 2025.\n"
              "Cohen kappa 0.81 on a 300-question subset.")


def _section(heading: str, content: str) -> dict:
    return {"id": heading, "heading": heading, "level": 1, "status": "done", "content": content}


def test_a_sentence_naming_a_work_cites_that_work_and_numbers_follow_the_body():
    sections = [
        _section("배경", "선행 실증 결과(Kim & Park, 2025)에 근거하여 가설을 세운다[1]."),
        _section("선행연구", "Kamata(2026)는 지자체 챗봇의 보안을 평가하였다[2]. "
                 "Ryu et al.(2025)는 해외 사례를 비교하였다[1]. "
                 "Kawashima(2024)는 지자체 RAG를 구축하였다[2]."),
        _section("방법", "코퍼스 구성 기준은 Kim과 Park(2025)의 Method 절을 따른다[3]. "
                 "합의도는 kappa 0.81이었다[1]."),
    ]
    sources = [KAMATA, KAWASHIMA, RYU, PAPER]
    texts = {1: "[1] Security Design\n출처: x\n보안 설계", 2: "[2] Development\n출처: y\n구축",
             3: "[3] Implementation\n출처: z\n사례", 4: PAPER_TEXT}
    credited = citations.credit_attachments(sections, sources, texts)
    assert [s["content"] for s in credited] == [
        "선행 실증 결과(Kim & Park, 2025)에 근거하여 가설을 세운다[4].",
        "Kamata(2026)는 지자체 챗봇의 보안을 평가하였다[1]. "
        "Ryu et al.(2025)는 해외 사례를 비교하였다[3]. "
        "Kawashima(2024)는 지자체 RAG를 구축하였다[2].",
        "코퍼스 구성 기준은 Kim과 Park(2025)의 Method 절을 따른다[4]. "
        "합의도는 kappa 0.81이었다[4].",
    ]
    out, kept = citations.tidy(credited, sources)
    # the reference list in the order the body first cites, body and list renumbered together
    assert [s["title"] for s in kept] == [PAPER["title"], KAMATA["title"], RYU["title"],
                                         KAWASHIMA["title"]]
    assert out[1]["content"].startswith("Kamata(2026)는 지자체 챗봇의 보안을 평가하였다[2].")


def test_a_named_work_the_list_lacks_or_a_cited_match_keeps_its_number():
    sections = [_section("선행연구", "Mala et al.(2025)는 이탈리아 사례를 보고하였다[2]. "
                         "Kamata(2026)는 정확도 0.81을 보고하였다[1].")]
    texts = {1: "[1] Security Design\n출처: x\n", 2: "[2] Development\n출처: y\n",
             4: PAPER_TEXT}
    assert citations.credit_attachments(sections, [KAMATA, KAWASHIMA, PAPER], texts) == sections


# ── an edit rewrites what it asks, and an asked summary is its lines ──

INSTRUCTION = ("2절의 표 2와 그림 1은 빼고, 맨 앞에 3줄 요약을 넣어줘. 내부 허용 한도가 4%라는 "
               "표현은 메모에 없으니 결재 요청으로 고쳐줘.")
PARTS = [
    {"id": "s0", "heading": "이번 주 주요 성과",
     "content": "3분기 매출 41.2억원은 목표 40억원을 103% 달성했습니다."},
    {"id": "s1", "heading": "주요 업무 현황",
     "content": "**표 1. 업무 현황**\n\n| 사업 | 상태 |\n| --- | --- |\n| 대성유통 | 협상 |\n\n"
                "경쟁사 A사가 4% 낮은 단가를 제시했습니다."},
    {"id": "s2", "heading": "이슈 및 요청 사항",
     "content": "대성유통 단가는 내부 허용 한도 4% 안에서 정합니다. 물류팀 SLA 협의가 필요합니다."},
    {"id": "s3", "heading": "다음 주 계획",
     "content": "목요일에 대성유통 2차 미팅에서 단가를 최종 협상합니다."},
]
SUMMARY = ("2026년 10월 3주차(10/12~10/16) 주간 업무 보고\n영업2팀 대리 박서연\n"
           "- 3분기 팀 매출은 41.2억원으로 목표 대비 103%를 달성했습니다.\n"
           "- 대성유통과 단가 협상이 남아 있습니다.\n"
           "- 단가 인하 폭 결재와 물류팀 SLA 협의가 필요합니다.")


def test_each_ask_of_an_edit_goes_to_the_part_it_names():
    asks = revise.section_asks(INSTRUCTION, [p["heading"] for p in PARTS],
                               [p["content"] for p in PARTS])
    assert asks == ({0: ["맨 앞에 3줄 요약을 넣어줘."], 1: ["2절의 표 2와 그림 1은 빼고"],
                     2: ["내부 허용 한도가 4%라는 표현은 메모에 없으니 결재 요청으로 고쳐줘."]}, 3)
    # an ask naming no part is the planner's
    assert revise.section_asks("전체적으로 더 간결하게 써 줘", ["가", "나"], ["a", "b"]) is None


def test_an_asked_summary_is_its_lines_without_the_title_block():
    block = revise.summary_block(SUMMARY, 3, "영업2팀 10월 3주차 업무 보고서",
                                 ["", "박서연 대리", "10/12~10/16", ""])
    assert block == ("```callout\n핵심\n"
                     "3분기 팀 매출은 41.2억원으로 목표 대비 103%를 달성했습니다.\n"
                     "대성유통과 단가 협상이 남아 있습니다.\n"
                     "단가 인하 폭 결재와 물류팀 SLA 협의가 필요합니다.\n```")
    assert revise.summary_block("영업2팀 대리 박서연\n- 매출 목표를 달성했습니다.", 3, "보고서",
                                ["박서연 대리"]) is None


def test_the_edit_rewrites_only_the_asked_parts_and_puts_the_summary_first(monkeypatch):
    async def plan(**_kwargs):
        return revise.Plan(scope="whole", targets=[0, 1, 2, 3], note="요약 넣고 결재 요청으로")

    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens, **_kwargs):
        prompt = messages[-1]["content"]
        asked.append(prompt)
        if prompt.startswith("Write the summary"):
            return SUMMARY, {"inputTokens": 1, "outputTokens": 1}
        if "이슈 및 요청 사항" in prompt.split("\n# ")[0] or "결재 요청으로 고쳐줘" in prompt:
            return ("대성유통 단가 인하 폭(최대 4%)은 결재를 요청합니다. 물류팀 SLA 협의가 "
                    "필요합니다.", {"inputTokens": 1, "outputTokens": 1})
        return ("경쟁사 A사가 4% 낮은 단가를 제시했습니다. 대성유통과 단가 협상이 남아 "
                "있습니다.", {"inputTokens": 1, "outputTokens": 1})

    monkeypatch.setattr(revise, "plan", plan)
    monkeypatch.setattr(report, "_complete", complete)
    bundle = revise_stages.bundle_for(
        request="이번 주 메모로 주간 업무 보고서를 만들어줘.", instruction=INSTRUCTION,
        model={"id": "m"}, planner=None, api_key="k", is_deck=False,
        title="영업2팀 10월 3주차 업무 보고서",
        data={"titleBlock": {"fields": [["보고자", "박서연 대리"], ["대상 기간", "10/12~10/16"]]}},
        parts=[dict(p) for p in PARTS], material=[], plan=None, format_id="",
        usage={"inputTokens": 0, "outputTokens": 0})

    async def run():
        for stage in (revise_stages.Route(), revise_stages.Widen(), revise_stages.Rewrite()):
            async for _event in stage.run(bundle, Context()):
                pass

    asyncio.run(run())
    sections = bundle.document.sections
    # two parts rewritten, the summary put in by code; the untouched part as it was
    rewrites = [p for p in asked if not p.startswith("Write the summary")]
    assert len(rewrites) == 2
    # each part is told its own ask only
    own = [p.split("사용자가 이 절에 요청한 것(그대로 반영): ")[1].split("\n")[0] for p in rewrites]
    assert own == ["2절의 표 2와 그림 1은 빼고",
                   "내부 허용 한도가 4%라는 표현은 메모에 없으니 결재 요청으로 고쳐줘."]
    assert sections[3]["content"] == PARTS[3]["content"]
    first = sections[0]["content"]
    assert first.startswith("```callout\n핵심\n3분기 팀 매출은")
    assert "박서연" not in first and "주간 업무 보고\n" not in first
    assert first.endswith(PARTS[0]["content"])
    assert bundle.document.changed == 3 and bundle.document.missed == []
