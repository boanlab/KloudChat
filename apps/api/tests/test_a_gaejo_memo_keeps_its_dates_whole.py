"""A 개조식 보고 keeps its 공문서 dates whole (lineup v1 gaejo-report-3__r1: 「- 1안: 2026. 11.」,
「12. 31. 만료)」 split off its item and 「12. 7.(월) 오픈)가 …」 that lost its start): no
sentence splitter ends a sentence inside 「2026. 11. 16.(월)」, typed markers are list lines,
and a date cut at its dots is joined back by the 개조식 normaliser."""

from __future__ import annotations

from app.services import deck, doc_formats, quality_gate, report, units

GAEJO = next(f for f in doc_formats.FORMATS if f.id == "gaejo")

#: The run's material, shortened.
MAIL = (
    "[메일] 비즈웍스: 전자결재 이관은 2026-11-23(월) 오픈 가능. "
    "메일 계정 640개는 2026-11-16 완료.\n"
    "[메일] 총무팀: 2026-11-30(월) 교직원 인사발령 전 1주일은 결재가 몰림. "
    "2026-12-07(월) 오픈 제안.\n"
    "[메일] 정보화팀: 2안이면 기존 그룹웨어 유지보수 계약을 2026-12-31까지 연장, "
    "금3,200,000원 추가.\n"
)
#: The draft as the run's model wrote it: dates already in 공문서 notation, inside items and
#: parentheses.
DRAFT = """## 현황
- 신규 그룹웨어 계약 및 이관 현황
  - 메일 계정 이관 완료 예정일: 2026. 11. 16.
  - 신규 시스템 오픈 가능일: 2026. 11. 23.(월)
## 문제점 및 검토
- 2026. 11. 30.(월) 교직원 인사발령으로 인한 결재 업무 공백 위험
- 총무팀 의견: 2026. 11. 23.(월) 오픈 강행 시 초기 안정화 부담이 큼

| 기준 | 1안 | 2안 |
| :--- | :--- | :--- |
| 오픈일 | 2026. 11. 23.(월) | 2026. 12. 7.(월) |
| 추가 비용 | 없음 | 금3,200,000원 |

검토 의견: 2안(2026. 12. 7.(월) 오픈)가 업무 연속성과 안정성 측면에서 유리함
## 향후 조치 및 결정 요청
- 결정 요청 사항
  - 신규 그룹웨어 오픈 일정 선택
    - 1안: 2026. 11. 23.(월) 오픈
    - 2안: 2026. 12. 7.(월) 오픈
- 2안 선택 시 조치 계획
  - 기존 그룹웨어 유지보수 계약 연장(2026. 12. 31. 만료)
- 승인 요청
  - 2안(2026. 12. 7.(월) 오픈) 선택 승인
## 붙임
붙임  1. 비즈웍스 전자결재 이관 일정 통보(2026. 9. 28.) 1부.
"""

KEPT = (
    "1안: 2026. 11. 23.(월) 오픈",
    "2안: 2026. 12. 7.(월) 오픈",
    "기존 그룹웨어 유지보수 계약 연장(2026. 12. 31. 만료)",
    "2안(2026. 12. 7.(월) 오픈) 선택 승인",
    "2안(2026. 12. 7.(월) 오픈)가 업무 연속성과 안정성 측면에서 유리함",
)


async def test_the_written_memo_keeps_every_date_whole(monkeypatch):
    async def complete(model, messages, api_key, max_tokens, **kwargs):
        return DRAFT, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request="그룹웨어 교체 건 메일 스레드야. 정보화팀장님께 올릴 개조식 보고로 정리해 줘.",
        model="m", api_key="k", web_search=False, untrusted_context=[MAIL],
        template_id="doc-gaejo-report",
        approved_plan={"title": "그룹웨어 교체 일정 결정 요청",
                       "sections": list(doc_formats.GAEJO_OUTLINE)},
    )]
    sections = next(e["sections"] for e in events if e["type"] == "report")
    body = "\n".join(str(s["content"]) for s in sections)
    for line in KEPT:
        assert line in body, line
    # No line opens with a date's tail, and none ends in a cut-off year or month.
    for line in body.split("\n"):
        assert not doc_formats._DATE_TAIL.match(line.strip()), line
        assert not line.rstrip().endswith(("2026.", " 11.", " 12.")), line
    # The plan's nesting survives the restatement trim.
    assert "    - 1안: 2026. 11. 23.(월) 오픈" in body


def test_the_restatement_trim_keeps_dates_items_and_indents():
    earlier = [DRAFT.split("## 향후")[0]]
    part = DRAFT.split("## 향후 조치 및 결정 요청\n")[1].split("## 붙임")[0]
    trimmed, cut = report.trim_restatements(part, earlier)
    assert "  - 기존 그룹웨어 유지보수 계약 연장(2026. 12. 31. 만료)" in trimmed
    # What goes, goes whole: no date is cut at its dots, no fragment is left behind.
    assert "- 1안: 2026. 11. 23.(월) 오픈" in cut
    for line in trimmed.split("\n"):
        assert not line.rstrip().endswith(("2026.", " 11.", " 12.")), line
        assert not doc_formats._DATE_TAIL.match(line.strip()), line
    # A 개조식 body keeps every item: each is a line of its own.
    whole, none = report.trim_restatements(part, earlier, keep_items=True)
    assert none == [] and whole == part


def test_a_cut_first_sentence_leaves_the_item_marker():
    body = "- 이관 대상 640개. 신규 항목 추가 예정.\n"
    trimmed, cut = report.trim_restatements(body, ["이관 대상 640개"])
    assert cut == ["- 이관 대상 640개."]
    assert trimmed == "- 신규 항목 추가 예정.\n"


def test_no_splitter_breaks_inside_a_dotted_date_or_number_run():
    line = ("계약 연장(2026. 12. 31. 만료)으로 정리함. 2안(2026. 12. 7.(월) 오픈) 선택. "
            "버전 1. 2. 적용.")
    want = ["계약 연장(2026. 12. 31. 만료)으로 정리함.", "2안(2026. 12. 7.(월) 오픈) 선택.",
            "버전 1. 2. 적용."]
    assert units.SENTENCE_BREAK.split(line) == want
    assert [s.strip() for s in report._SENTENCE_END.split(line)] == want
    assert [m.group(0).strip() for m in report._SENTENCE.finditer(line + " ")] == want
    tail = "- 메일 계정 이관 완료 예정일: 2026. 11. 16."
    assert [m.group(0) for m in report._SENTENCE.finditer(tail + " ")] == [tail]
    assert report._SPACED_SENTENCE_END.split(line) == want
    # gaejo-report-5__r1 lost 「- A사: 2026.」 and kept 「12. 17. 12. 18.) 1일 여유」.
    run5 = "  - A사: 2026. 12. 17. 완료(8주), 기한(2026. 12. 18.) 1일 여유"
    assert units.SENTENCE_BREAK.split(run5) == [run5]
    assert report.trim_restatements(run5, ["2026. 12. 17. 2026. 12. 18. 완료 기한"])[1] in (
        [], [run5.strip()])
    # A sentence still ends after a number that closes it.
    assert units.SENTENCE_BREAK.split("참석자는 12명. 다음 회의는 금요일.") == [
        "참석자는 12명.", "다음 회의는 금요일."]
    assert deck._sentence_count(line) == 3
    assert deck._join_notes(line, "2안(2026. 12. 7.(월) 오픈) 선택.") == line


def test_long_paragraph_split_and_point_units_keep_dates():
    para = " ".join(["기존 계약은 2026. 12. 31. 만료되며 2안은 2026. 12. 7.(월) 오픈함."] * 12)
    out = report.split_long_paragraphs(para)
    assert "2026.\n" not in out and "\n\n12." not in out and "\n\n31." not in out
    assert report.fix_point_units(para) == para


def test_typed_markers_are_list_lines_for_the_trimming_helpers():
    table = "| 항목 | 값 |\n|---|---|\n| 1안 | 2026 |\n| 2안 | 3200000 |\n"
    for mark in ("□", "○", "ㅇ", "※", "·"):
        line = f"{mark} 1안 2026, 2안 3200000 비교"
        assert line in report.trim_table_echo(f"{table}\n{line}\n설명 문장.")
    sized = report.enforce_sentence_counts(
        [{"heading": "요약", "content": "□ 첫째 항목. 둘째 문장.\n○ 세부 항목. 또 한 문장.\n"}],
        "요약은 한 문장으로")
    assert "○ 세부 항목. 또 한 문장." in sized[0]["content"]
    long_list = "\n".join(
        f"□ 항목 {i}: 2026. 11. {i + 1}.(월) 이관 일정 확정. 세부 사항 별도." for i in range(12))
    assert report.split_long_paragraphs(long_list) == long_list
    one_line = "□ " + "이관 일정 2026. 11. 16.(월) 확정, 세부 사항은 붙임 참조함 " * 20
    found = quality_gate.report_findings([{"heading": "현황", "content": one_line}])
    assert not [f for f in found if f["code"] == "long_paragraph"]


def test_the_gaejo_normaliser_joins_a_date_cut_at_its_dots():
    text = (
        "- 기존 그룹웨어 유지보수 계약 연장(2026.\n12. 31. 만료)\n"
        "- 1안: 2026. 11.\n16.(월) 오픈\n"
        "- 검토 의견: 2안(2026.\n12. 7.(월) 오픈)가 유리함\n"
        "\n1. 첫째 항목\n2. 둘째 항목"
    )
    out = doc_formats.gaejo_markdown(text)
    assert "- 기존 그룹웨어 유지보수 계약 연장(2026. 12. 31. 만료)" in out
    assert "- 1안: 2026. 11. 16.(월) 오픈" in out
    assert "- 검토 의견: 2안(2026. 12. 7.(월) 오픈)가 유리함" in out
    assert "1. 첫째 항목\n2. 둘째 항목" in out
    # A table row is never continued.
    assert doc_formats.gaejo_markdown("| a | b |\n12. 31. 만료)") == "| a | b |\n12. 31. 만료)"
    # 공문서 dates from ISO stay one line through gaejo_body.
    body = report.gaejo_body(
        [{"heading": "현황", "content": "- 계약 연장(2026-12-31 만료)"}], GAEJO)
    assert body[0]["content"] == "- 계약 연장(2026. 12. 31. 만료)"
