"""Korean public documents: 개조식, 간이기안문, 추진 계획(안), 결과 보고서 — what is detected,
which 서식 fixes it, and how every exporter sets the lists, the 결재란 and 「끝.」."""

from __future__ import annotations

import io
import json
import re
import zipfile

import pytest
from pypdf import PdfReader

from app.services import design_templates, doc_formats, report, report_export

# ── detection ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("request_text", "expected"),
    [
        # A word inside (결과 보고서, 장애인, 계획서) does not pick lab, incident or plan.
        ("신입생 환영 행사 결과 보고서를 써 줘", "result_report"),
        ("부산 출장 결과 보고서 정리해 줘", "result_report"),
        ("만족도 설문 결과 보고서를 만들어 줘", "result_report"),
        ("장애인 지원 사업 현황 보고서 써 줘", "status"),
        ("신입생 OT 계획서 써 줘", "event_plan"),
        ("전산 장비 구매 건 기안문 작성해 줘", "internal_draft"),
        ("세종시 출장 보고서 써 줘", "result_report"),
        ("개조식 보고서로 정리해 줘", "gaejo"),
        # Lab reports and research plans stay theirs.
        ("아래 내용으로 회로실험 결과 보고서를 써 줘.", "lab"),
        ("아래 내용으로 사회조사방법론 조사 계획서를 써 줘.", "plan"),
        ("졸업 작품 계획서 써 줘", "plan"),
        ("서버 장애 보고서 써 줘", "incident"),
        # The rest of the new words.
        ("교직원 연수 추진 계획(안) 써 줘", "event_plan"),
        ("축제 운영 계획 만들어 줘", "event_plan"),
        ("내부결재 올릴 문서", "internal_draft"),
        ("간이기안으로 올려 줘", "internal_draft"),
    ],
)
def test_the_request_names_its_public_document(request_text: str, expected: str):
    assert doc_formats.detect(request_text).id == expected


def test_a_business_plan_and_a_notice_are_not_event_plans():
    assert doc_formats.detect("창업 사업 계획안 써 줘") is None or (
        doc_formats.detect("창업 사업 계획안 써 줘").id != "event_plan"
    )
    assert doc_formats.detect("수강 신청 계획 안내문 써 줘").id == "official"


def test_the_chosen_template_wins_over_the_words():
    for template_id, format_id in (
        ("doc-gaejo-report", "gaejo"),
        ("doc-internal-draft", "internal_draft"),
        ("doc-event-plan", "event_plan"),
        ("doc-result-report", "result_report"),
    ):
        assert design_templates.get(template_id) is not None, template_id
        # The words say lab; the 서식 says otherwise.
        assert doc_formats.detect("회로실험 결과 보고서", template_id).id == format_id
    assert doc_formats.detect("회로실험 결과 보고서", "_document").id == "lab"
    assert doc_formats.detect("아무 문서", None) is None


def test_every_mapped_template_exists_and_maps_to_a_format():
    for template_id, format_id in doc_formats.TEMPLATE_FORMATS.items():
        assert design_templates.get(template_id) is not None, template_id
        assert format_id in doc_formats.BY_ID


def test_a_public_document_head_carries_its_list_style_and_closing():
    for format_id in ("gaejo", "internal_draft", "event_plan", "result_report"):
        fmt = doc_formats.BY_ID[format_id]
        block = doc_formats.title_block(fmt, {}, "")
        assert block["lists"] == "gaejo" and block["closing"] is True
        assert all(value == "" for _, value in block["fields"])
    draft = doc_formats.BY_ID["internal_draft"]
    assert draft.head == "draft"
    assert {"생산등록번호", "등록일", "결재일", "공개 구분"} <= set(draft.fields)
    assert set(doc_formats.APPROVERS) <= set(draft.fields)
    # Other formats are unchanged: no list style, no closing.
    assert "lists" not in doc_formats.title_block(doc_formats.BY_ID["official"], {}, "")


# ── genre rules follow the format ──────────────────────────────────────


def test_the_template_format_names_the_genre_and_drops_the_prose_rules():
    fmt = doc_formats.detect("신입생 행사 문서", "doc-event-plan")
    genre = report._genre_rule("신입생 행사 문서", fmt)
    assert genre.startswith("Genre: 추진 계획(안)") and "개조식" in genre
    assert "금113,560원(금일십일만삼천오백육십원)" in genre and "2026. 10. 7." in genre
    # Without a template the words decide.
    assert report._genre_rule("신입생 행사 문서") == ""
    assert report._genre_rule("행사 결과 보고서 써 줘").startswith("Genre: 결과 보고서")
    assert report._genre_rule("장애인 지원 현황 보고") .startswith("Genre: 현황")
    shaped = report._shaped(report._SECTION_PROMPT, genre)
    assert "Prose is the default" not in shaped and "One register" not in shaped
    assert "Write only what belongs under" in shaped
    assert report._shaped(report._SECTION_PROMPT, "") == report._SECTION_PROMPT


async def test_the_report_pipeline_uses_the_sessions_template(monkeypatch):
    calls: list[str] = []
    draft = (
        "## 추진 배경 및 목적\n□ 신입생 적응 지원\nㅇ 2026년 3월 2일 오후 2시 개최 예정\n"
        "※ 작년 만족도 3.2점\n## 기대 효과\n□ 학교 적응 기간 단축 기대\n"
        "붙임\n1. 세부 일정표 1부.\n2. 예산 내역 1부.  끝."
    )

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        calls.append(json.dumps(messages, ensure_ascii=False))
        return draft, {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [
        event
        async for event in report.write(
            request="신입생 행사 문서 만들어 줘",
            model="synthetic",
            api_key="synthetic",
            web_search=False,
            approved_plan={"title": "신입생 OT", "sections": ["추진 배경 및 목적", "기대 효과"]},
            template_id="doc-event-plan",
        )
    ]
    block = next(event["titleBlock"] for event in events if event["type"] == "titleBlock")
    assert block["format"] == "event_plan" and block["lists"] == "gaejo"
    assert "Genre: 추진 계획(안)" in calls[0] and "Prose is the default" not in calls[0]
    sections = next(event["sections"] for event in events if event["type"] == "report")
    first = sections[0]["content"]
    assert "- 신입생 적응 지원\n  - 2026. 3. 2. 14:00 개최 예정" in first
    # A ※ under an item stands at that item's depth (drawn with ※ as its marker).
    assert "\n  - ※ 작년 만족도 3.2점" in first
    assert "붙임  1. 세부 일정표 1부.  \n2. 예산 내역 1부." in sections[-1]["content"]


# ── notation ───────────────────────────────────────────────────────────


def test_official_notation():
    assert doc_formats.official_amount(113560) == "금113,560원(금일십일만삼천오백육십원)"
    assert doc_formats.hangul_amount(115_200_000) == "일억일천오백이십만"
    assert doc_formats.hangul_amount(10_000) == "일만"
    assert doc_formats.official_date(2026, 10, 7) == "2026. 10. 7."
    text = "2026년 10월 7일 오후 2시 30분, 2026-03-02 오전 9시부터 3시간\n```\n2026-03-02\n```"
    assert doc_formats.official_notation(text) == (
        "2026. 10. 7. 14:30, 2026. 3. 2. 09:00부터 3시간\n```\n2026-03-02\n```"
    )
    # An hour that could be morning or afternoon is left as written.
    assert doc_formats.official_notation("3시에 회의, 18시 종료") == "3시에 회의, 18:00 종료"


def test_typed_markers_become_one_nested_list():
    typed = "□ 현황\n ○ 노후 PC 168대\n   - 고장 월 23건\n     ㆍ 수리비 월 115만 원\n※ 9월 기준"
    assert doc_formats.gaejo_markdown(typed) == (
        "- 현황\n  - 노후 PC 168대\n    - 고장 월 23건\n      - 수리비 월 115만 원\n"
        "      - ※ 9월 기준"
    )
    nested = "- 현황\n    - 세부\n        - 더 세부\n- 다음"
    assert doc_formats.gaejo_markdown(nested) == "- 현황\n  - 세부\n    - 더 세부\n- 다음"
    assert doc_formats.gaejo_markdown("| a | b |\n|---|---|\n| □ x | y |") == (
        "| a | b |\n|---|---|\n| □ x | y |"
    )
    assert doc_formats.without_closing("붙임  1. 계획서 1부.  끝.") == "붙임  1. 계획서 1부."
    assert doc_formats.without_closing("시작과 끝.") == "시작과 끝."


# ── every exporter ─────────────────────────────────────────────────────

SECTIONS = [
    {
        "id": "s1",
        "heading": "추진 배경",
        "level": 1,
        "content": (
            "- 신입생 학교 적응 지원 필요\n  - 작년 만족도 3.2점으로 낮음\n"
            "    - 일정 안내 부족 응답 41%\n      - 문자 안내 미수신 12%\n"
            "※ 만족도는 5점 척도"
        ),
    },
    {
        "id": "s2",
        "heading": "기대 효과",
        "level": 1,
        "content": "- 학교 적응 기간 단축\n\n붙임\n1. 세부 일정표 1부.\n2. 예산 내역 1부.",
    },
    {
        "id": "references",
        "heading": "참고문헌",
        "level": 1,
        "content": "1. 학생처 (2025). 만족도 조사.",
    },
]
MARKERS = ["□", "○", "-", "ㆍ"]


def _block(format_id: str = "internal_draft") -> dict:
    answer = {"fields": {"기안자": "주무관 김민지", "검토자": "팀장 이준호", "공개 구분": "공개"}}
    request = "기안자 주무관 김민지, 검토자 팀장 이준호, 공개"
    return doc_formats.title_block(doc_formats.BY_ID[format_id], answer, request)


def _paragraphs(kind: str, data: bytes) -> list[tuple[str, int]]:
    """`(text, left indent)` per paragraph of a .docx or .hwpx, indent in the file's unit."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    if kind == "docx":
        xml = archive.read("word/document.xml").decode()
        out = []
        for paragraph in re.findall(r"<w:p[ >].*?</w:p>", xml, re.S):
            runs = re.findall(r"<w:t(?: [^>]*)?>([^<]*)</w:t>|<w:(tab)/>", paragraph)
            text = "".join(t or ("\t" if tab else "") for t, tab in runs)
            left = re.search(r'<w:ind [^>]*w:left="(\d+)"', paragraph)
            out.append((text, int(left.group(1)) if left else 0))
        return out
    header = archive.read("Contents/header.xml").decode()
    lefts = {
        int(pid): int(left)
        for pid, left in re.findall(
            r'<hh:paraPr id="(\d+)".*?<hc:left value="(-?\d+)"', header, re.S
        )
    }
    xml = archive.read("Contents/section0.xml").decode()
    out = []
    for pid, body in re.findall(r'<hp:p paraPrIDRef="(\d+)"[^>]*>(.*?)</hp:p>', xml, re.S):
        text = "".join(re.findall(r"<hp:t>([^<]*)</hp:t>", body))
        out.append((text, lefts.get(int(pid), 0)))
    return out


@pytest.mark.parametrize("kind", ["docx", "hwpx"])
def test_gaejo_markers_hang_deeper_each_level_in_the_office_files(kind: str):
    writer = report_export.to_docx if kind == "docx" else report_export.to_hwpx
    lines = _paragraphs(kind, writer("신입생 OT", SECTIONS, title_block=_block("gaejo")))
    items = [(text, left) for text, left in lines if text[:1] in MARKERS and text[1:2] in "\t "]
    # The first section runs one level deeper each line; the second opens at the top again.
    items = items[:4]
    assert [text[0] for text, _ in items] == MARKERS
    assert [left for _, left in items] == sorted({left for _, left in items})
    note = next((text, left) for text, left in lines if text.startswith("※"))
    assert note[1] > 0 and "만족도는 5점 척도" in note[0]
    # No Word bullet glyph and no plain list marker on a 개조식 item.
    assert not any(text.startswith(("•", "–")) for text, _ in lines)


def test_gaejo_markers_hang_deeper_each_level_in_the_pdf():
    data = report_export.to_pdf("신입생 OT", SECTIONS, title_block=_block("gaejo"))
    seen: list[tuple[str, float]] = []

    def visit(text, cm, tm, font, size):
        if text.strip()[:1] in MARKERS and len(text.strip()) == 1:
            seen.append((text.strip(), tm[4] * cm[0] + cm[4]))

    PdfReader(io.BytesIO(data)).pages[0].extract_text(visitor_text=visit)
    seen = seen[:4]
    assert [mark for mark, _ in seen] == MARKERS
    xs = [x for _, x in seen]
    assert xs == sorted(xs) and len(set(round(x) for x in xs)) == 4


def _text(kind: str, data: bytes) -> str:
    if kind == "pdf":
        return "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    return "\n".join(text for text, _ in _paragraphs(kind, data))


WRITERS = {
    "docx": report_export.to_docx,
    "pdf": report_export.to_pdf,
    "hwpx": report_export.to_hwpx,
}


@pytest.mark.parametrize("kind", list(WRITERS))
def test_the_draft_head_carries_its_register_and_approval_box(kind: str):
    text = _text(kind, WRITERS[kind]("실습실 PC 교체", SECTIONS, title_block=_block()))
    for label in ("생산등록번호", "등록일", "결재일", "공개 구분", *doc_formats.APPROVERS):
        assert label in text, (kind, label)
    assert "주무관 김민지" in text and "팀장 이준호" in text and "공개" in text
    assert text.index("결재권자") < text.index("실습실 PC 교체")
    assert doc_formats.blank("결재권자") in text or doc_formats.blank("등록일") in text


@pytest.mark.parametrize("kind", list(WRITERS))
def test_the_end_mark_follows_the_last_attachment_once(kind: str):
    text = _text(kind, WRITERS[kind]("실습실 PC 교체", SECTIONS, title_block=_block()))
    assert text.count("끝.") == 1
    assert re.search(r"2\. 예산 내역 1부\.[  ]{2}끝\.", text), kind
    # The reference list comes after the body it closes.
    assert text.index("끝.") < text.index("참고문헌")
    # 붙임 and its first item share a line.
    assert re.search(r"붙임[\s ]{1,4}1\. 세부 일정표 1부\.", text), kind


@pytest.mark.parametrize("kind", list(WRITERS))
def test_a_body_ending_in_a_table_is_closed_under_it(kind: str):
    sections = [{"id": "s1", "heading": "예산", "level": 1,
                 "content": "| 항목 | 금액 |\n|---|---|\n| 자료집 | 7,400,000원 |\n\n끝."}]
    text = _text(kind, WRITERS[kind]("예산", sections, title_block=_block("event_plan")))
    assert text.count("끝.") == 1
    last = [line for line in text.splitlines() if line.strip()][-1 if kind != "pdf" else -2]
    assert last.strip("  ") == "끝.", (kind, last)


@pytest.mark.parametrize("kind", list(WRITERS))
def test_documents_of_other_formats_keep_their_lists(kind: str):
    block = doc_formats.title_block(doc_formats.BY_ID["status"], {}, "")
    text = _text(kind, WRITERS[kind]("현황", SECTIONS[:1], title_block=block))
    assert "□" not in text and "○" not in text and "끝." not in text


def test_a_grant_application_has_its_own_format_not_the_research_plan():
    # 「사업계획서 … 공모사업 신청용」 is a grant application, not the 연구자·연구 기간 cover.
    ask = ("아래 메모로 「2027 지역 상생 디지털 배움터」 사업계획서 써 줘. 한빛시 공모사업 "
           "신청용이고, 심사위원이 읽는 문서야.")
    fmt = doc_formats.detect(ask)
    assert fmt.id == "grant" and "연구자" not in fmt.fields and "신청 기관" in fmt.fields
    assert doc_formats.detect("한국연구재단 연구계획서 초안을 써 줘.").id == "plan"
    assert doc_formats.detect("창업 사업계획서 써 줘") is None


@pytest.mark.parametrize("ask", [
    "창업 사업계획서 써 줘", "밀키트 구독 사업 계획서를 써 줘.", "내년 사업계획서 초안 작성해 줘",
])
def test_a_business_plan_gets_no_default_format(ask: str):
    assert doc_formats.detect(ask) is None


def test_a_business_plan_keeps_a_named_format():
    assert doc_formats.detect("사업계획서 써 줘", fixed="biz").id == "biz"
    assert doc_formats.detect("한빛시 공모사업 신청용 사업계획서 써 줘").id == "grant"
    assert doc_formats.detect("조사 계획서와 사업계획서를 써 줘").id == "plan"
    assert doc_formats.detect("현장 점검 계획서 써 줘").id == "plan"


def test_a_grant_application_s_cover_reads_its_institution_and_period():
    memo = ("- 신청 기관: 한빛대학교 평생교육원 / 사업 기간: 2027. 3. ~ 2027. 12. (10개월)\n"
            "- 목적: 한빛시 60세 이상 주민과 소상공인의 디지털 역량 격차 해소\n")
    got = doc_formats.head_values(doc_formats.BY_ID["grant"], memo)
    assert got["신청 기관"] == "한빛대학교 평생교육원"
    assert got["사업 기간"].startswith("2027. 3. ~ 2027. 12.")


def test_table_rows_written_as_list_items_come_back_as_a_table():
    # A 개조식 결과 보고서's table rows as list items, not 「○ | 항목 | 계획 |」 lines.
    body = ("- 만족도(응답 141명)\n  - 표: 만족도 조사 결과\n  - | 항목 | 만족도(5점 만점) |\n"
            "  - |---|---|\n  - | 전반 만족 | 4.5 |\n  - | 멘토링 | 4.6 |\n- 예산 집행\n"
            "  - 편성 4,500만원 / 집행 4,215만원\n")
    out = doc_formats.gaejo_markdown(body)
    assert ("표: 만족도 조사 결과\n\n| 항목 | 만족도(5점 만점) |\n|---|---|\n| 전반 만족 | 4.5 |\n"
            "| 멘토링 | 4.6 |\n\n") in out
    assert "- | " not in out and "  - 편성 4,500만원" in out


def test_a_listed_table_is_numbered_like_any_other_once_normalized():
    from app.services import report

    body = ("- 만족도\n  - 표: 만족도 조사 결과\n  - | 항목 | 만족도 |\n  - |---|---|\n"
            "  - | 전반 만족 | 4.5 |\n  - | 멘토링 | 4.6 |\n")
    out = report.normalize_document([{"heading": "성과", "content": body}])[0]["content"]
    assert "**표 1." in out and "\n| 항목 | 만족도 |\n|---|---|\n" in out and "- |" not in out


S03 = ("각 학과에 보낼 협조 요청 공문을 써 줘.\n\n"
       "- 제목 취지: 2027학년도 실험실습 기자재 수요조사 협조 요청\n"
       "- 발신: 한빛대학교 기획처장 / 수신: 각 학과(부)장\n"
       "- 제출 기한: 2026. 11. 6.(금) 18:00까지\n")


def test_a_default_official_letter_is_one_body_without_chapters():
    # The default 공문: one body, no chapter headings.
    from app.services import process_settings, report

    fmt = doc_formats.detect(S03)
    assert fmt.id == "official" and fmt.closing
    process = process_settings.resolve(template_id=None, format_id="official")
    assert process.outline == ("본문",) and process.draws == "when-asked"
    rule = report._genre_rule(S03, fmt)
    assert rule.startswith("Genre: 공문(시행문)") and "no headings" in rule
    sections = report.untitled_body([{"heading": "본문", "content": "x"}], process.outline)
    assert sections[0]["heading"] == ""


def test_an_official_letter_s_items_stand_on_lines_of_their_own():
    from app.services import report

    body = ("1. 2027학년도 수요조사를 하오니 협조하여 주시기 바랍니다.\n"
            "  가. 제출 기한: 2026년 11월 6일(금) 18:00\n"
            "    나. 제출 방법: 메일 제출\n"
            "     ※ 학과장 확인 필수\n"
            "2. 문의: 기획예산팀 윤서영(내선 2214)\n"
            "붙임  실험실습 기자재 수요조사서 1부.")
    out = report.gaejo_body([{"heading": "", "content": body}],
                            doc_formats.BY_ID["official"])[0]["content"]
    assert out.split("\n\n") == [
        "1. 2027학년도 수요조사를 하오니 협조하여 주시기 바랍니다.",
        "가. 제출 기한: 2026. 11. 6.(금) 18:00",
        "나. 제출 방법: 메일 제출",
        "※ 학과장 확인 필수",
        "2. 문의: 기획예산팀 윤서영(내선 2214)",
        "붙임  실험실습 기자재 수요조사서 1부.",
    ]
