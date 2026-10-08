"""Generates `template.docx` (styles and page only) and `form.docx` per document 서식.

Run from the API image:

    docker compose run --rm --no-deps api python scripts/build_docx_templates.py [folder ...]

Named folders only, when given; otherwise every 서식.

`_SPECS` mirrors each 서식's `seed.html` type scale; keep the two in step.
Fonts must be faces every Hangul Windows Word carries (맑은 고딕, 바탕), or Word
substitutes silently; 함초롬 ships with Hancom only. Styles name their faces
directly: a theme font (`asciiTheme`) would win over them in Word.
"""

from __future__ import annotations

import pathlib
import sys

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

ROOT = pathlib.Path(__file__).resolve().parent.parent / "app" / "design_templates"


class Spec:
    """Word-side design of one 서식."""

    def __init__(
        self,
        folder: str,
        *,
        body_font: str,
        heading_font: str,
        accent: str,
        body_pt: float,
        margins_mm: tuple[int, int, int, int],
        title_pt: float,
        h1_pt: float,
        h2_pt: float,
        note: str,
        form: tuple[tuple, ...] = (),
        spacing: tuple[float, float, float] | None = None,
    ) -> None:
        self.folder = folder
        #: (before a heading, after it, after a paragraph) in pt, with 160% of the size as
        #: the least line height; None keeps the shared spacing.
        self.spacing = spacing
        self.form = form
        self.body_font = body_font
        self.heading_font = heading_font
        self.accent = accent
        self.body_pt = body_pt
        self.margins_mm = margins_mm
        self.title_pt = title_pt
        self.h1_pt = h1_pt
        self.h2_pt = h2_pt
        self.note = note


# ── blank form blocks ─────────────────────────────────────────────────────
# `template.docx` must stay empty: the export writer appends to it. The
# downloadable `form.docx` is built from the same styles plus these blocks.


def H(level: int, text: str) -> tuple:
    """A heading. Level 0 is the document's title."""
    return ("h", level, text)


def P(text: str) -> tuple:
    """A greyed guidance line."""
    return ("p", text)


def T(columns: tuple[str, ...], rows: int = 3) -> tuple:
    """A table with its header filled in and `rows` empty rows under it."""
    return ("t", columns, rows)


def B(items: tuple[str, ...]) -> tuple:
    """A bulleted list of guidance prompts."""
    return ("b", items)


_SPECS = (
    Spec(
        "doc-report",
        body_font="바탕",
        heading_font="맑은 고딕",
        accent="2B4C7E",
        body_pt=10.5,
        margins_mm=(20, 20, 20, 20),
        title_pt=20,
        h1_pt=13,
        h2_pt=11,
        note="제출용 보고 문서. 각주로 근거를 단다.",
        form=(
            H(0, "보고서 제목"),
            P("표지만 읽고 무엇을 위한 문서인지, 누가 읽는지, 언제 것인지 알 수 있게."),
            T(("작성", "소속", "작성일", "문서번호"), rows=1),
            H(1, "검토 범위"),
            P("무엇을 보고 무엇을 보지 않았는지. 범위를 밝히지 않으면 빠진 것이 실수로 읽힙니다."),
            H(1, "확인한 것"),
            P(
                "수치와 인용에는 각주 표시를 답니다. "
                "표시 없는 수치가 이어지면 근거를 적지 않은 것입니다."
            ),
            T(("항목", "값", "단위·기준 시점", "출처")),
            H(1, "비교"),
            P("같은 기준으로 견줍니다. 비교 값이라면 무엇 대비인지 밝힙니다."),
            H(1, "권고와 남은 확인"),
            P("확인하지 못한 것은 확인하지 못했다고 적습니다."),
            H(1, "읽는 사람이 할 일"),
            T(("할 일", "담당", "기한")),
        ),
    ),
    Spec(
        "doc-project-brief",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="3B5BDB",
        body_pt=10,
        margins_mm=(18, 18, 18, 18),
        title_pt=19,
        h1_pt=12.5,
        h2_pt=10.5,
        note="프로젝트 브리프. 표지 아래 개요 한 문단, 나머지는 나란히 놓는 격자.",
        form=(
            H(0, "프로젝트 제목"),
            P("이 프로젝트가 무엇을 하는 것인지 한 줄로."),
            T(("책임자", "기간", "작성일"), rows=1),
            H(1, "개요"),
            P("무엇을, 왜, 언제까지. 한 문단으로 씁니다. 세 문단이면 개요가 아닙니다."),
            H(1, "산출물과 목표"),
            P("받는 사람이 손에 쥐는 것과, 확인할 수 있는 기준. 활동은 산출물이 아닙니다."),
            T(("산출물", "목표")),
            H(1, "이해관계자와 성공 기준"),
            P("누가 무엇을 하는지와, 무엇을 보면 됐다고 할 수 있는지."),
            T(("이해관계자", "역할", "성공 기준")),
            H(1, "일정과 예산"),
            T(("단계", "기간", "금액")),
            H(1, "전제와 위험"),
            B(("틀리면 무엇이 무너지는가", "무엇을 보면 아는가")),
        ),
    ),
    Spec(
        "doc-brief",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="1F6F5C",
        body_pt=10,
        margins_mm=(16, 16, 16, 16),
        title_pt=17,
        h1_pt=11.5,
        h2_pt=10.5,
        note="한 장 요약. 여백을 줄여 한 쪽에 담는다.",
        form=(
            H(0, "한 장 요약 제목"),
            P("읽는 사람이 무엇을 결정해야 하는지 한 줄로."),
            T(("작성", "작성일", "결정 기한"), rows=1),
            H(1, "결정할 것"),
            P("첫 줄에서 무엇을 정해야 하는지 알 수 있게."),
            H(1, "지금 상황"),
            P("숫자로. 형용사만 있으면 그 문장은 의견입니다."),
            H(1, "대안"),
            P("같은 기준으로 비교합니다. 하나만 있으면 비교가 아닙니다."),
            T(("안", "장점", "단점", "비용")),
            H(1, "걸려 있는 것"),
            B(("무엇을 잃는가", "무엇을 얻는가")),
            H(1, "다음 행동"),
            P("조판이 마지막 항목을 강조하므로 다른 것을 여기 두지 않습니다."),
            T(("할 일", "담당", "기한")),
        ),
    ),
    Spec(
        "doc-minutes",
        body_font="바탕",
        heading_font="맑은 고딕",
        accent="5B4B8A",
        body_pt=10.5,
        margins_mm=(20, 18, 18, 18),
        title_pt=18,
        h1_pt=12.5,
        h2_pt=11,
        note="회의록. 결정과 논의를 갈라 적는다.",
        form=(
            H(0, "회의 이름"),
            T(("일시", "장소", "작성"), rows=1),
            H(1, "참석"),
            P("오지 못한 사람이 이 문서만 읽고 따라올 수 있게. 불참자도 적습니다."),
            H(1, "논의"),
            P("정해진 것과 논의만 된 것을 섞지 않습니다. 반대나 유보 의견도 남깁니다."),
            H(1, "결정 사항"),
            P("결정마다 왜 그렇게 정했는지 한 줄이라도 남깁니다."),
            T(("결정", "근거", "담당", "기한")),
            H(1, "남은 쟁점"),
            P("결론이 나지 않은 안건은 다음에 누가 언제 정하는지 적습니다."),
            H(1, "실행 항목"),
            P("오른쪽 칸은 문장이 아니라 이름과 날짜만. 미정이면 '미정' 이라고 적습니다."),
            T(("할 일", "담당", "기한")),
        ),
    ),
    Spec(
        "doc-notice",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="A2452C",
        body_pt=11,
        margins_mm=(22, 20, 20, 20),
        title_pt=19,
        h1_pt=13,
        h2_pt=11.5,
        note="안내문·공지. 본문을 크게 잡아 게시용으로 읽힌다.",
        form=(
            T(("수신", "참조"), rows=1),
            H(0, "제목"),
            T(("시행일", "담당", "연락처", "문서번호"), rows=1),
            H(1, "근거"),
            P("이 안내가 무엇에 근거한 것인지. 규정·회의 결정·상위 공문 등."),
            H(1, "내용"),
            P("첫 절을 읽고 무엇이 언제부터 달라지는지 알 수 있게."),
            B(("무엇이", "언제부터", "누구에게 해당하는지")),
            H(1, "협조 요청"),
            P("받는 사람이 할 일에 기한과 제출처를 함께 적습니다. 할 일이 없으면 없다고 말합니다."),
            H(1, "붙임"),
            P("본문에서 실제로 쓰인 자료만. 붙임 1. 파일 이름 1부.  끝."),
        ),
    ),
    Spec(
        "doc-lab",
        body_font="바탕",
        heading_font="맑은 고딕",
        accent="1F5673",
        body_pt=10,
        margins_mm=(18, 18, 18, 18),
        title_pt=17,
        h1_pt=12,
        h2_pt=10.5,
        note="실험 노트. 절차와 관찰을 촘촘히 담는다.",
        form=(
            H(0, "실험 제목"),
            P("무엇을 확인하려 했는지 표지에서 알 수 있게."),
            T(("실험자", "실험일", "장소", "지도"), rows=1),
            H(1, "목적"),
            P("무엇을 확인하려는 실험인지 한 문장으로."),
            H(1, "장비·설정값·반복 횟수"),
            P("이 노트만 보고 같은 실험을 다시 할 수 있게."),
            T(("장비", "설정값", "반복")),
            H(1, "절차"),
            B(("준비", "측정", "정리")),
            H(1, "측정값"),
            P("첫 칸만 항목 이름, 나머지는 수치. 단위는 열 제목에만 답니다."),
            T(("회차", "측정값", "조건")),
            H(1, "계산"),
            P(
                "결과 숫자가 어떤 식에서 나왔는지 전개를 남깁니다. "
                "값만 있고 계산이 없으면 안 됩니다."
            ),
            H(1, "해석"),
            P(
                "관측한 값과 그에 대한 해석을 구분합니다. "
                "예상과 다른 값은 지우지 않고 그대로 둡니다."
            ),
            H(1, "한계"),
            P("실제 한계를 적습니다. '추가 연구가 필요하다' 로 대신하지 않습니다."),
            H(1, "원자료"),
            P("원자료가 어디 있는지."),
        ),
    ),
    Spec(
        "doc-term-paper",
        body_font="바탕",
        heading_font="맑은 고딕",
        accent="4B3B8F",
        body_pt=10.5,
        margins_mm=(25, 25, 25, 25),
        title_pt=19,
        h1_pt=13,
        h2_pt=11,
        note="기말 리포트. 각주가 앉을 여백을 넓게 둔다.",
        form=(
            H(0, "리포트 제목"),
            P("무엇을 묻는 글인지 부제로 한 줄."),
            T(("과목", "학번", "이름", "제출일"), rows=1),
            H(1, "서론"),
            P("이 글이 답하려는 질문을 문장으로 세웁니다. 배경만 늘어놓지 않습니다."),
            H(1, "본론"),
            P("장 제목이 그 장의 주장이 되게 씁니다. 인용한 문장에는 그 자리에 각주를 답니다."),
            H(1, "결론"),
            P("서론에서 세운 질문에 답합니다. 앞을 요약만 하지 않습니다."),
            H(1, "다루지 못한 것"),
            P("이 글이 다루지 못한 것을 한 줄로 밝힙니다."),
            H(1, "참고문헌"),
            P("각주에 단 것과 어긋나지 않게 모읍니다."),
        ),
    ),
    Spec(
        "doc-case",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="1F6FEB",
        body_pt=10.5,
        margins_mm=(20, 18, 18, 18),
        title_pt=19,
        h1_pt=13,
        h2_pt=11,
        note="케이스 분석. 표로 견주고 하나를 고른다.",
        form=(
            H(0, "케이스 제목"),
            T(("대상 기업", "분석 기간", "작성", "작성일"), rows=1),
            H(1, "권고"),
            P("무엇을 하자는 것인지 먼저 적습니다. 뒤의 절이 이것의 근거가 됩니다."),
            H(1, "현황"),
            P("숫자로 씁니다. 형용사만 있으면 그 문장은 의견입니다."),
            T(("지표", "기준 시점", "값", "출처")),
            H(1, "대안"),
            P("열은 대안, 행은 기준. 같은 줄에서 견줄 수 있어야 합니다."),
            T(("기준", "안 1", "안 2", "안 3")),
            H(1, "권고"),
            P("어느 안을, 어떤 기준에서, 무엇을 포기하고 고르는지."),
            H(1, "위험"),
            B(("틀렸을 때 무엇이 일어나는가", "무엇을 보면 틀린 줄 아는가")),
        ),
    ),
    Spec(
        "doc-survey",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="0F766E",
        body_pt=10,
        margins_mm=(20, 18, 18, 18),
        title_pt=18,
        h1_pt=12.5,
        h2_pt=11,
        note="설문 분석. 표가 많으므로 본문을 작게.",
        form=(
            H(0, "조사 제목"),
            T(("조사 기간", "대상", "표본 수", "작성"), rows=1),
            H(1, "조사 개요"),
            P("누구에게, 언제, 어떻게 물었는지."),
            T(("배포", "회수", "회수율"), rows=1),
            H(1, "표본"),
            P("치우쳤다면 치우쳤다고 본문에 적습니다."),
            T(("구분", "빈도", "비율(%)")),
            H(1, "기술통계"),
            P("표는 숫자만. 해석은 표 아래 문단에."),
            T(("문항", "N", "평균", "표준편차")),
            H(1, "교차분석"),
            P("표 제목에 걸어 본 두 변수를 적습니다. 검정을 했다면 이름과 값도."),
            H(1, "한계"),
            B(("표본의 치우침", "자기보고의 한계", "묻지 못한 것")),
        ),
    ),
    Spec(
        "doc-incident",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="B42318",
        body_pt=10.5,
        margins_mm=(18, 18, 18, 18),
        title_pt=18,
        h1_pt=12.5,
        h2_pt=11,
        note="장애 보고. 시간순 기록이 넓게 앉는다.",
        form=(
            H(0, "장애 제목"),
            T(("발생", "인지", "복구", "영향 범위"), rows=1),
            H(1, "요약"),
            P("무엇이, 얼마 동안, 누구에게. 세 문장 안에."),
            H(1, "시간순 기록"),
            P("추측과 사실을 섞지 않습니다. 그때 몰랐던 것은 몰랐다고 적습니다."),
            T(("시각(KST)", "일어난 일", "한 일")),
            H(1, "원인"),
            P("왜 그 조작이 가능했는지, 왜 막히지 않았는지, 왜 늦게 알았는지까지."),
            H(1, "재발 방지"),
            T(("할 일", "담당(역할)", "기한")),
            H(1, "탐지"),
            P("늦게 알았다면 무엇을 보면 빨리 알 수 있었는지."),
        ),
    ),
    Spec(
        "doc-proposal",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="7C3AED",
        body_pt=10.5,
        margins_mm=(20, 20, 20, 20),
        title_pt=20,
        h1_pt=13,
        h2_pt=11,
        note="제안서. 표지를 크게 잡아 보내는 문서로 읽힌다.",
        form=(
            H(0, "제안 제목"),
            P("무엇을 제안하는지 한 줄로."),
            T(("고객사", "제안일", "담당", "연락처"), rows=1),
            H(1, "고객의 과제"),
            P("우리가 아니라 고객의 말로 시작합니다."),
            H(1, "제안"),
            P("무엇을 하겠다는 것인지 한 문장으로 먼저."),
            H(1, "효과"),
            P("무엇이 얼마나, 어떤 계산으로. 계산의 전제를 밝힙니다."),
            T(("항목", "지금", "도입 후", "근거")),
            H(1, "일정"),
            T(("단계", "기간", "우리가 할 일", "고객이 할 일")),
            H(1, "견적"),
            T(("항목", "수량", "금액"), rows=3),
            P("포함 범위와 제외 항목을 아래에 적습니다."),
            H(1, "요청"),
            P("이 문서를 읽고 무엇을 결정해 달라는 것인지 한 줄."),
        ),
    ),
    # 공문서 계열. Faces every Hangul Windows Word carries (맑은 고딕, 바탕), not 함초롬:
    # these files go to offices that open them in Word as often as in Hancom.
    Spec(
        "doc-gaejo-report",
        # 바탕 for the 문서용 명조 the agent wears; the export sets the design's body face
        # (`body_face = "design"`).
        body_font="바탕",
        heading_font="맑은 고딕",
        accent="1A1A1A",
        # The 1쪽 보고 scale and page (`doc_type.FORMAT_TYPE`, `FORMAT_SPACE`, `FORMAT_MARGINS`).
        body_pt=12,
        margins_mm=(20, 20, 15, 20),
        title_pt=18,
        h1_pt=14,
        h2_pt=13,
        spacing=(7, 2, 0),
        note="개조식 보고서. □ ○ - ㆍ 계단, 맨 위 핵심 두 줄.",
        form=(
            H(0, "보고서 제목"),
            T(("보고일", "보고자(부서)", "보고 대상"), rows=1),
            T(("핵심",), rows=2),
            P("맨 위 상자: 결론 한 줄, 필요한 결정(또는 다음 조치) 한 줄."),
            H(1, "현황"),
            P("□ 첫째 단계, ○ 둘째 단계, - 셋째, ㆍ 넷째. 항목은 명사형(…함, …임)으로 맺습니다."),
            # `doc_formats.GAEJO_OUTLINE`: the sections the 개조식 보고 is written in.
            H(1, "문제점 및 검토"),
            H(1, "향후 조치 및 결정 요청"),
            T(("조치", "담당", "기한")),
            P("붙임  1. ○○○ 1부.  끝."),
        ),
    ),
    Spec(
        "doc-internal-draft",
        body_font="바탕",
        heading_font="맑은 고딕",
        accent="1A1A1A",
        body_pt=11,
        margins_mm=(20, 20, 20, 20),
        title_pt=15,
        h1_pt=12,
        h2_pt=11,
        note="간이기안문(별지 제2호서식). 발신하지 않는 내부결재용.",
        form=(
            T(("생산등록번호", "등록일", "결재일", "공개 구분"), rows=1),
            T(("기안자", "검토자", "결재권자"), rows=2),
            H(0, "제목"),
            H(1, "관련 근거"),
            P("어떤 규정·계획·회의에 따른 일인지."),
            H(1, "목적"),
            H(1, "주요 내용"),
            P("무엇을, 언제, 얼마에. 결재받을 금액은 「금○○원(금○○원)」과 산출 근거."),
            H(1, "행정 사항"),
            P("붙임  1. ○○○ 1부.  끝."),
        ),
    ),
    Spec(
        "doc-event-plan",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="1A1A1A",
        body_pt=10.5,
        margins_mm=(20, 20, 20, 20),
        title_pt=17,
        h1_pt=12.5,
        h2_pt=11,
        note="추진 계획(안). 개요·예산·일정 표.",
        form=(
            H(0, "○○ 추진 계획(안)"),
            T(("작성일", "작성 부서", "작성자"), rows=1),
            H(1, "추진 배경 및 목적"),
            H(1, "추진 개요"),
            T(("구분", "내용"), rows=5),
            P("일시·장소·대상·인원·주관을 한 행씩."),
            H(1, "세부 추진 내용"),
            H(1, "소요 예산"),
            T(("항목", "산출 근거", "금액"), rows=4),
            P("산출 근거는 「단가 × 수량」, 마지막 행은 합계."),
            H(1, "추진 일정"),
            T(("일정", "내용", "담당")),
            H(1, "기대 효과"),
            P("붙임  1. ○○○ 1부.  끝."),
        ),
    ),
    Spec(
        "doc-result-report",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="1A1A1A",
        body_pt=10.5,
        margins_mm=(20, 20, 20, 20),
        title_pt=17,
        h1_pt=12.5,
        h2_pt=11,
        note="결과 보고서. 개요 표와 수치로 적는 결과.",
        form=(
            H(0, "○○ 결과 보고"),
            T(("보고일", "보고 부서", "보고자"), rows=1),
            H(1, "개요"),
            T(("구분", "내용"), rows=4),
            P("일시·장소·참석·주관을 한 행씩."),
            H(1, "추진 경과"),
            H(1, "주요 결과·실적"),
            T(("항목", "계획", "실적", "비율")),
            H(1, "성과 및 문제점"),
            P("문제점마다 개선 사항을 짝지어 적습니다."),
            H(1, "향후 계획(개선 사항)"),
            P("붙임  1. ○○○ 1부.  끝."),
        ),
    ),
    # 업무 문서 계열(고객 제안·비교·경력): 맑은 고딕 하나. 받는 쪽이 Word 로 연다.
    Spec(
        "doc-client-proposal",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="1F3A5F",
        body_pt=10.5,
        margins_mm=(20, 20, 20, 20),
        title_pt=20,
        h1_pt=13,
        h2_pt=11,
        note="고객 제안서. RFP 목차, 요구사항 대응표, 부가세까지 맞는 견적.",
        form=(
            H(0, "○○ 사업 제안서"),
            T(("고객사", "사업명", "제안사", "제안일", "담당자", "연락처", "유효기간"), rows=1),
            P("RFP가 목차를 주었으면 아래 절 대신 그 목차와 순서를 따릅니다."),
            H(1, "제안 요약"),
            P("무엇을, 얼마에, 언제까지 하겠다는 것인지 세 줄."),
            H(1, "고객 과제 이해"),
            P("고객의 말(RFP 문구, 미팅 발언)을 따옴표로 옮겨 시작합니다."),
            H(1, "제안 내용"),
            H(1, "요구사항 대응표"),
            T(("요구 ID", "요구 내용", "대응 방안", "제안서 위치")),
            H(1, "추진 일정"),
            T(("단계", "기간", "제안사가 할 일", "고객이 할 일")),
            H(1, "수행 조직·투입 인력"),
            T(("역할", "인원", "투입 기간", "주요 경력")),
            H(1, "견적"),
            P("부가세 포함·별도를 밝힙니다. 금액 = 수량 × 단가."),
            T(("항목", "수량", "단위", "단가", "금액", "비고"), rows=3),
            T(("공급가액", "부가세", "합계"), rows=1),
            H(1, "전제·제외 범위·위험"),
            H(1, "요청 사항"),
            P("고객에게 요청하는 결정 한 줄."),
        ),
    ),
    Spec(
        "doc-comparison",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="1F3A5F",
        body_pt=10.5,
        margins_mm=(20, 18, 18, 18),
        title_pt=18,
        h1_pt=12.5,
        h2_pt=11,
        note="비교·분석 보고서. 가중치·점수식이 보이는 비교표, 조건부 권고.",
        form=(
            H(0, "○○ 비교·분석"),
            T(("검토 대상", "판단 기준", "작성일", "작성자"), rows=1),
            H(1, "검토 목적과 판단 기준"),
            P("첫 줄: 고른 대안과 그 조건. 이어서 필수 조건, 가중치(합 100), 점수식."),
            H(1, "대안 개요"),
            H(1, "비교표"),
            P("열은 대안, 행은 기준. 빈칸 대신 「자료 없음」, 칸마다 출처 번호."),
            T(("기준(가중치)", "안 1", "안 2", "안 3"), rows=4),
            H(1, "출처별 차이"),
            T(("항목", "출처와 날짜", "값", "채택과 이유")),
            H(1, "조건 미확인 사항"),
            B(("확인되지 않은 조건", "확인되면 결론이 어떻게 정해지는가")),
            H(1, "권고와 조건"),
            P("「○○이 확인되면 A, 아니면 B」. 조건이 바뀌면 결론이 바뀌는 지점."),
        ),
    ),
    Spec(
        "doc-career-history",
        body_font="맑은 고딕",
        heading_font="맑은 고딕",
        accent="1A1A1A",
        body_pt=10.5,
        margins_mm=(18, 18, 18, 18),
        title_pt=18,
        h1_pt=12.5,
        h2_pt=11,
        note="경력기술서. 회사별 → 프로젝트별, 표 안에 표 없이.",
        form=(
            H(0, "경력기술서"),
            T(("이름", "연락처", "이메일", "지원 회사", "지원 직무", "작성일"), rows=1),
            H(1, "경력 요약"),
            P("총 경력(겹친 기간은 한 번만)과 핵심 역량 세 줄."),
            H(1, "경력 사항"),
            T(("회사", "기간", "소속·직급", "담당 업무")),
            H(1, "주요 프로젝트"),
            H(2, "회사명 — 프로젝트명"),
            P("기간 YYYY.MM–YYYY.MM · 역할 · 수행 내용 · 성과(자료에 있는 수치만)."),
            H(1, "보유 기술"),
            H(1, "자격·교육"),
            T(("이름", "기관", "취득·수료 연월")),
        ),
    ),
)


def _east_asian(style, font: str) -> None:
    """Sets the face for Latin and East Asian runs; `font.name` alone covers Latin only.

    The theme attributes go: Word reads `asciiTheme` before `ascii`."""
    style.font.name = font
    element = style.element.rPr.rFonts
    element.set(qn("w:eastAsia"), font)
    element.set(qn("w:cs"), font)
    for name in ("w:asciiTheme", "w:hAnsiTheme", "w:eastAsiaTheme", "w:cstheme"):
        element.attrib.pop(qn(name), None)


def _ink(style, colour: RGBColor) -> None:
    """A style's text colour, with Word's theme colour (the default blue) removed."""
    style.font.color.rgb = colour
    found = style.element.rPr.find(qn("w:color"))
    if found is not None:
        for name in ("w:themeColor", "w:themeShade", "w:themeTint"):
            found.attrib.pop(qn(name), None)


#: The table style the export and the form use: hairlines, a bold head row over an
#: accent rule, cell padding. Word and Hancom read `tblStylePr`; the export also
#: writes the head row's look on the cells, for viewers that do not.
_TABLE_STYLE = (
    '<w:style xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    ' w:type="table" w:customStyle="1" w:styleId="ReportTable">'
    '<w:name w:val="보고서 표"/><w:basedOn w:val="TableGrid"/><w:uiPriority w:val="59"/>'
    '<w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr>'
    "<w:tblPr><w:tblBorders>"
    '<w:top w:val="single" w:sz="4" w:space="0" w:color="BBBBBB"/>'
    '<w:left w:val="single" w:sz="4" w:space="0" w:color="BBBBBB"/>'
    '<w:bottom w:val="single" w:sz="4" w:space="0" w:color="BBBBBB"/>'
    '<w:right w:val="single" w:sz="4" w:space="0" w:color="BBBBBB"/>'
    '<w:insideH w:val="single" w:sz="4" w:space="0" w:color="BBBBBB"/>'
    '<w:insideV w:val="single" w:sz="4" w:space="0" w:color="BBBBBB"/>'
    "</w:tblBorders><w:tblCellMar>"
    '<w:top w:w="60" w:type="dxa"/><w:left w:w="120" w:type="dxa"/>'
    '<w:bottom w:w="60" w:type="dxa"/><w:right w:w="120" w:type="dxa"/>'
    "</w:tblCellMar></w:tblPr>"
    '<w:tblStylePr w:type="firstRow"><w:rPr><w:b/></w:rPr>'
    '<w:tcPr><w:tcBorders><w:bottom w:val="single" w:sz="10" w:space="0" w:color="{accent}"/>'
    '</w:tcBorders><w:shd w:val="clear" w:color="auto" w:fill="F2F2F2"/></w:tcPr></w:tblStylePr>'
    "</w:style>"
)


def _table_style(document, accent: str) -> None:
    """Adds `보고서 표` to the styles part; python-docx has no API for a table style."""
    from docx.oxml import parse_xml

    document.styles.element.append(parse_xml(_TABLE_STYLE.replace("{accent}", accent)))


def _field(paragraph, instruction: str, placeholder: str = "") -> None:
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), instruction)
    if placeholder:
        run = OxmlElement("w:r")
        text = OxmlElement("w:t")
        text.text = placeholder
        run.append(text)
        field.append(run)
    paragraph._p.append(field)


def build(spec: Spec) -> pathlib.Path:
    document = Document()
    accent = RGBColor.from_string(spec.accent)

    top, right, bottom, left = spec.margins_mm
    for section in document.sections:
        section.orientation = WD_ORIENT.PORTRAIT
        section.page_width = Mm(210)
        section.page_height = Mm(297)
        section.top_margin = Mm(top)
        section.right_margin = Mm(right)
        section.bottom_margin = Mm(bottom)
        section.left_margin = Mm(left)
        footer = section.footer.paragraphs[0]
        footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
        _field(footer, "PAGE", "1")

    normal = document.styles["Normal"]
    _east_asian(normal, spec.body_font)
    normal.font.size = Pt(spec.body_pt)
    normal.paragraph_format.space_after = Pt(6)
    # 160%: Korean needs more leading than Word's default.
    normal.paragraph_format.line_spacing = 1.6

    # Every heading level the export writes, in the 서식's faces: one left alone keeps
    # Word's theme face and blue.
    ink = RGBColor.from_string("1A1A1A")
    for name, size, colour in (
        ("Title", spec.title_pt, accent),
        ("Heading 1", spec.h1_pt, accent),
        ("Heading 2", spec.h2_pt, ink),
        ("Heading 3", spec.body_pt + 0.5, ink),
        ("Heading 4", spec.body_pt, ink),
    ):
        style = document.styles[name]
        _east_asian(style, spec.heading_font)
        style.font.size = Pt(size)
        style.font.bold = True
        style.font.italic = False
        _ink(style, colour)
        style.paragraph_format.space_before = Pt(12 if name != "Title" else 0)
        style.paragraph_format.space_after = Pt(6)
        style.paragraph_format.keep_with_next = True
    # The Title's rule under it, in the accent rather than the theme blue.
    rule = document.styles["Title"].element.find(f"{qn('w:pPr')}/{qn('w:pBdr')}/{qn('w:bottom')}")
    if rule is not None:
        rule.set(qn("w:color"), spec.accent)
        rule.attrib.pop(qn("w:themeColor"), None)

    # Body Text (본문) carries the writing; Normal is the base headings inherit from.
    body = document.styles["Body Text"]
    _east_asian(body, spec.body_font)
    body.font.size = Pt(spec.body_pt)
    body.paragraph_format.space_after = Pt(6)
    body.paragraph_format.line_spacing = 1.6

    subtitle = document.styles["Subtitle"]
    _east_asian(subtitle, spec.heading_font)
    subtitle.font.size = Pt(spec.body_pt + 1)
    subtitle.font.bold = False
    subtitle.font.color.rgb = RGBColor.from_string("666666")

    # The depths the export writes a nested list in, as Word's own list styles.
    for name in (
        "List Bullet",
        "List Bullet 2",
        "List Bullet 3",
        "List Number",
        "List Number 2",
        "List Number 3",
        "Quote",
        "Caption",
    ):
        if name in [s.name for s in document.styles]:
            _east_asian(document.styles[name], spec.body_font)
            document.styles[name].font.size = Pt(spec.body_pt)

    # Guidance styles for the form. Named styles, never direct formatting, so
    # the reader can restyle or delete guidance in one move; Enter lands in 본문.
    guide = document.styles.add_style("안내", WD_STYLE_TYPE.PARAGRAPH)
    guide.base_style = document.styles["Body Text"]
    _east_asian(guide, spec.body_font)
    guide.font.size = Pt(spec.body_pt - 0.5)
    guide.font.italic = True
    guide.font.color.rgb = RGBColor.from_string("7A7A7A")
    guide.paragraph_format.space_after = Pt(2)
    guide.next_paragraph_style = document.styles["Body Text"]

    guide_list = document.styles.add_style("안내 목록", WD_STYLE_TYPE.PARAGRAPH)
    guide_list.base_style = document.styles["List Bullet"]
    _east_asian(guide_list, spec.body_font)
    guide_list.font.size = Pt(spec.body_pt - 0.5)
    guide_list.font.italic = True
    guide_list.font.color.rgb = RGBColor.from_string("7A7A7A")
    guide_list.paragraph_format.space_after = Pt(2)
    guide_list.next_paragraph_style = document.styles["Body Text"]

    head = document.styles.add_style("표 머리", WD_STYLE_TYPE.PARAGRAPH)
    head.base_style = document.styles["Body Text"]
    _east_asian(head, spec.heading_font)
    head.font.size = Pt(spec.body_pt - 0.5)
    head.font.bold = True
    head.paragraph_format.space_after = Pt(0)
    head.paragraph_format.line_spacing = 1.2

    cell = document.styles.add_style("표 본문", WD_STYLE_TYPE.PARAGRAPH)
    cell.base_style = document.styles["Body Text"]
    _east_asian(cell, spec.body_font)
    cell.font.size = Pt(spec.body_pt - 0.5)
    cell.paragraph_format.space_after = Pt(0)
    cell.paragraph_format.line_spacing = 1.2

    # Figures in a table: flush right so the digits line up (금액·수량·합계).
    figure = document.styles.add_style("표 숫자", WD_STYLE_TYPE.PARAGRAPH)
    figure.base_style = cell
    figure.paragraph_format.alignment = WD_ALIGN_PARAGRAPH.RIGHT

    _table_style(document, spec.accent)

    if spec.spacing is not None:
        before, after, item = spec.spacing
        for name in ("Normal", "Body Text", "Title", "Heading 1", "Heading 2", "Heading 3"):
            style = document.styles[name]
            form = style.paragraph_format
            if name.startswith("Heading"):
                form.space_before, form.space_after = Pt(before), Pt(after)
            elif name == "Title":
                form.space_before = form.space_after = Pt(0)
            else:
                form.space_after = Pt(item)
            # Points, at least: a ratio multiplies the face's own line height in Word.
            size = style.font.size.pt if style.font.size else spec.body_pt
            ratio = 1.6 if name in ("Normal", "Body Text") else 1.3
            form.line_spacing = Pt(round(size * ratio, 1))
            form.line_spacing_rule = WD_LINE_SPACING.AT_LEAST

    # The template ships empty; any paragraph left here would head every export.
    for paragraph in list(document.paragraphs):
        paragraph._element.getparent().remove(paragraph._element)

    out = ROOT / spec.folder / "template.docx"
    document.save(out)
    return out


def build_form(spec: Spec) -> pathlib.Path | None:
    """Builds the downloadable blank form from the `template.docx` `build` just wrote."""
    if not spec.form:
        return None
    document = Document(str(ROOT / spec.folder / "template.docx"))

    # Named styles only, no direct formatting.
    for block in spec.form:
        if block[0] == "h":
            _, level, text = block
            # Built-in heading styles, so the navigation pane and 목차 work.
            document.add_heading(text, level=level)
        elif block[0] == "p":
            document.add_paragraph(block[1], style="안내")
            # Empty 본문 paragraph to write in, so typing does not inherit the guidance style.
            document.add_paragraph("", style="Body Text")
        elif block[0] == "b":
            for item in block[1]:
                document.add_paragraph(item, style="안내 목록")
            document.add_paragraph("", style="Body Text")
        elif block[0] == "t":
            _, columns, rows = block
            # `보고서 표` (on `Table Grid`) keeps empty cells visible; the head row repeats.
            table = document.add_table(rows=rows + 1, cols=len(columns))
            table.style = document.styles["보고서 표"]
            head_row = table.rows[0]._tr.get_or_add_trPr()
            repeat = OxmlElement("w:tblHeader")
            repeat.set(qn("w:val"), "true")
            head_row.append(repeat)
            for index, name in enumerate(columns):
                paragraph = table.rows[0].cells[index].paragraphs[0]
                paragraph.text = name
                paragraph.style = document.styles["표 머리"]
            for row in table.rows[1:]:
                for used in row.cells:
                    used.paragraphs[0].style = document.styles["표 본문"]
            document.add_paragraph("", style="Body Text")

    out = ROOT / spec.folder / "form.docx"
    document.save(out)
    return out


def main() -> int:
    only = set(sys.argv[1:])
    for spec in _SPECS:
        if only and spec.folder not in only:
            continue
        folder = ROOT / spec.folder
        if not folder.is_dir():
            print(f"없는 서식: {spec.folder}", file=sys.stderr)
            return 1
        path = build(spec)
        form = build_form(spec)
        size = f"{path.stat().st_size:>7,}"
        blank = f" + 양식 {form.stat().st_size:,}바이트" if form else ""
        print(f"{spec.folder:14} {size}바이트{blank}  {spec.note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
