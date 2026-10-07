"""Slide patterns: one table every renderer reads.

A pattern is a data shape (what the writer fills) drawn by a general arrangement
(how the screen, the PPTX and the PDF lay it out). Fifty-odd patterns come from four
shapes and ten arrangements, so a renderer implements the arrangements once and every
pattern of that arrangement follows.

Shapes:
- ``pairs``   — ``items: [[left, right], ...]``: a label and what it says.
- ``columns`` — ``columns: [{"title": str, "items": [str, ...]}, ...]``: named lists side by side.
- ``metrics`` — ``metrics: [[value, label], ...]``, as the ``metrics`` layout.
- ``chart``   — ``chart: {kind, unit, categories, series}``, as the ``chart`` layout.
- ``text``    — ``body``: one line under the title.

Arrangements (``render``) and their parameters:
- ``grid``    — boxes in ``cols`` columns; ``style``: card | person | feature.
- ``list``    — one row per item; ``marker``: number | check | qa | term | star | target | ref.
- ``flow``    — boxes left to right with connectors; ``style``: arrow | chevron | phase.
- ``vflow``   — a vertical line with points (milestones).
- ``stack``   — stacked bars that widen or narrow; ``shape``: pyramid | funnel.
- ``cycle``   — items around a ring.
- ``quad``    — a 2×2 grid; ``mode``: swot | matrix.
- ``columns`` — named panels side by side; ``tone``: neutral | vs | posneg | beforeafter.
- ``kpi``     — metrics; ``mode``: grid | bars | compare.
- ``chart``   — the chart drawer with a fixed ``kind``.
- ``text``    — a single large line; ``mode``: question | definition | hypothesis.

The web mirror is ``apps/web/src/components/slides/patterns.ts``; a test keeps the two
equal (`test_slide_patterns_are_one_table.py`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Pattern:
    name: str
    #: What the picker shows.
    label: str
    shape: str
    render: str
    params: dict[str, Any] = field(default_factory=dict)
    #: (fewest, most) items, or columns for ``columns``.
    count: tuple[int, int] = (3, 5)
    #: Character limits: left/right of a pair, or a column title and its items.
    left_max: int = 14
    right_max: int = 80
    #: When the outline should pick it — one line, read by the planner.
    use: str = ""
    #: The writer's rules for this slide.
    rules: str = ""
    #: An example answer (JSON, braces doubled for ``str.format``).
    example: str = ""
    #: Only offered when the request carries numbers.
    numeric: bool = False


def _p(name, label, shape, render, params=None, **kw) -> Pattern:
    return Pattern(name=name, label=label, shape=shape, render=render, params=params or {}, **kw)


PATTERNS: tuple[Pattern, ...] = (
    # ---- grid ----
    _p("cards-2", "카드 2개", "pairs", "grid", {"cols": 2, "style": "card"}, count=(2, 2),
       left_max=16, right_max=110,
       use="두 가지를 나란히 크게 — 두 방안, 두 축, 두 대상.",
       rules="- items 는 `[이름, 설명]` 두 개. 설명은 한두 문장.",
       example=(
           '{{"items": [["교육", "전교생 AI 기초 과목을 필수로 연다"], ["연구", "학과별 융합 과제'
           '를 지원한다"]]}}'
       )),
    _p("cards-3", "카드 3개", "pairs", "grid", {"cols": 3, "style": "card"}, count=(3, 3),
       left_max=14, right_max=80,
       use="같은 급의 세 항목 — 세 전략, 세 원칙, 세 기능.",
       rules="- items 는 `[이름, 설명]` 세 개. 서로 같은 급이어야 한다.",
       example=(
           '{{"items": [["정확", "출처를 붙인다"], ["간결", "한 장에 한 메시지"], ["일관", "용어'
           '를 통일한다"]]}}'
       )),
    _p("feature-grid", "기능 격자", "pairs", "grid", {"cols": 3, "style": "feature"}, count=(4, 6),
       left_max=14, right_max=50,
       use="기능·특징 4~6개를 격자로 — 제품 기능, 서비스 구성.",
       rules="- items 는 `[기능 이름, 한 줄 설명]` 4~6개. 설명은 50자 이내.",
       example=(
           '{{"items": [["사진 등록", "습득물 사진으로 등록"], ["자동 매칭", "분실 신고와 비교"],'
           ' ["알림", "매칭 시 푸시"], ["보관함", "관리실 위치 안내"]]}}'
       )),
    _p("team", "팀·역할", "pairs", "grid", {"cols": 4, "style": "person"}, count=(2, 6),
       left_max=10, right_max=40,
       use="사람과 역할 — 팀원 소개, 역할 분담, 담당자.",
       rules=(
           "- items 는 `[이름, 역할]`. 자료에 없는 사람을 만들지 마라; "
           "이름이 없으면 「팀원 A」처럼."
       ),
       example=(
           '{{"items": [["김서연", "기획·PM"], ["박지훈", "백엔드"], ["이민아", "프론트엔드"]]}}'
       )),
    # ---- list ----
    _p("checklist", "체크리스트", "pairs", "list", {"marker": "check"}, count=(3, 6),
       left_max=24, right_max=60,
       use="확인할 것·준비물·요건 — 점검 목록.",
       rules="- items 는 `[항목, 확인 기준]`. 기준이 없으면 빈 문자열.",
       example='{{"items": [["신청서", "학과 사무실 제출"], ["추천서", "지도교수 서명"]]}}'),
    _p("numbered", "번호 목록", "pairs", "list", {"marker": "number"}, count=(3, 6),
       left_max=24, right_max=70,
       use="순위·순서가 있는 항목 — 우선순위, 상위 N개, 요점 번호.",
       rules="- items 는 `[요점, 부연]`. 순서가 뜻을 가져야 한다. 번호는 자리가 매긴다.",
       example='{{"items": [["문제 정의", "누가 무엇을 잃는지"], ["근거", "조사 수치"]]}}'),
    _p("faq", "질문·답", "pairs", "list", {"marker": "qa"}, count=(2, 4),
       left_max=40, right_max=90,
       use="예상 질문과 답 — Q&A, 자주 묻는 질문.",
       rules="- items 는 `[질문, 답]`. 질문은 물음표로 끝난다. 답은 한두 문장.",
       example=(
           '{{"items": [["비용은 얼마인가요?", "학기당 3만 원입니다."], ["누가 신청하나요?", "재'
           '학생 누구나."]]}}'
       )),
    _p("glossary", "용어 정리", "pairs", "list", {"marker": "term"}, count=(3, 6),
       left_max=16, right_max=70,
       use="용어와 정의 — 개념 정리, 약어 풀이.",
       rules="- items 는 `[용어, 정의]`. 정의는 한 문장.",
       example=(
           '{{"items": [["차단 주파수", "출력이 -3 dB 가 되는 주파수"], ["이득", "출력 대 입력의 '
           '비"]]}}'
       )),
    _p("takeaways", "핵심 요약", "pairs", "list", {"marker": "star"}, count=(2, 4),
       left_max=30, right_max=70,
       use="기억할 것 두셋 — 결론 직전의 요약, 시사점.",
       rules="- items 는 `[요점, 근거 한 줄]`. 앞 장에서 말한 것을 줄인다.",
       example=(
           '{{"items": [["오차 6% 이내", "고주파에서 커짐"], ["fc ≈ 1.59 kHz", "측정과 이론 일치"'
           ']]}}'
       )),
    _p("objectives", "목표", "pairs", "list", {"marker": "target"}, count=(2, 5),
       left_max=24, right_max=70,
       use="목표·성과 지표 — 이번 과제가 이루려는 것.",
       rules="- items 는 `[목표, 측정 기준]`. 기준이 자료에 없으면 빈 문자열.",
       example=(
           '{{"items": [["분실물 회수율 향상", "학기 말 설문"], ["등록 3분 이내", "사용성 시험"]]'
           '}}'
       )),
    _p("references", "참고문헌", "pairs", "list", {"marker": "ref"}, count=(2, 6),
       left_max=24, right_max=120,
       use="출처 목록 — 발표의 참고문헌·자료 출처. 마지막 장 근처.",
       rules="- items 는 `[저자 (연도), 제목·출처]`. 자료에 있는 출처만.",
       example=(
           '{{"items": [["한국노동연구원 (2023)", "플랫폼 노동 실태조사 보고서"], '
           '["고용노동부 (2024)", "플랫폼 종사자 규모와 근무 실태"]]}}'
       )),
    # ---- flow ----
    _p("process", "프로세스", "pairs", "flow", {"style": "arrow"}, count=(3, 5),
       left_max=12, right_max=50,
       use="입력에서 출력으로 가는 처리 흐름 — 데이터 흐름, 업무 절차.",
       rules="- items 는 `[단계 이름, 무엇을 하는가]`. 순서대로. 번호를 붙이지 마라.",
       example=(
           '{{"items": [["수집", "설문 응답 모으기"], ["정제", "결측 제거"], ["분석", "교차 분석"'
           ']]}}'
       )),
    _p("chevron", "쉐브론 단계", "pairs", "flow", {"style": "chevron"}, count=(3, 5),
       left_max=10, right_max=40,
       use="단계가 짧은 이름으로 이어지는 진행 — 추진 단계, 성숙도 단계.",
       rules="- items 는 `[단계, 짧은 설명]`. 단계 이름은 10자 이내.",
       example=(
           '{{"items": [["기획", "요구사항 확정"], ["설계", "아키텍처"], ["구현", "MVP"], ["검증"'
           ', "사용자 시험"]]}}'
       )),
    _p("roadmap", "로드맵", "pairs", "flow", {"style": "phase"}, count=(3, 5),
       left_max=12, right_max=60,
       use="기간별 계획을 가로로 — 1~4주, 분기별, 학기 일정.",
       rules="- items 는 `[기간, 할 일]`. 자료에 날짜가 없으면 1단계·1~4주 같은 상대 시점.",
       example=(
           '{{"items": [["1~4주", "요구사항·설계"], ["5~10주", "구현"], ["11~15주", "시험·발표"]]'
           '}}'
       )),
    _p("milestones", "마일스톤", "pairs", "vflow", {}, count=(3, 6),
       left_max=12, right_max=60,
       use="세로로 이어지는 주요 시점 — 연혁, 진행 이정표.",
       rules="- items 는 `[시점, 일]`. 시간 순서. 날짜를 지어내지 마라.",
       example='{{"items": [["3월", "팀 구성"], ["4월", "제안서 제출"], ["6월", "중간 발표"]]}}'),
    # ---- stack / cycle ----
    _p("pyramid", "피라미드", "pairs", "stack", {"shape": "pyramid"}, count=(3, 5),
       left_max=14, right_max=50,
       use="위로 갈수록 좁아지는 위계 — 욕구 단계, 우선순위 층.",
       rules="- items 는 `[층 이름, 설명]`. 첫 항목이 꼭대기.",
       example=(
           '{{"items": [["비전", "모두가 쓰는 AI"], ["전략", "교육·연구·산학"], ["기반", "인프라'
           '와 데이터"]]}}'
       )),
    _p("funnel", "깔때기", "pairs", "stack", {"shape": "funnel"}, count=(3, 5),
       left_max=14, right_max=50,
       use="단계마다 줄어드는 양 — 전환 깔때기, 선별 과정.",
       rules="- items 는 `[단계, 설명 또는 수치]`. 수치는 자료에 있는 것만.",
       example='{{"items": [["지원", "120명"], ["서류 통과", "40명"], ["최종 선발", "12명"]]}}'),
    _p("cycle", "순환", "pairs", "cycle", {}, count=(3, 6),
       left_max=12, right_max=40,
       use="끝이 처음으로 돌아가는 반복 — PDCA, 개선 주기.",
       rules="- items 는 `[단계, 짧은 설명]`. 마지막 단계가 첫 단계로 이어져야 한다.",
       example=(
           '{{"items": [["계획", "목표 설정"], ["실행", "시범 운영"], ["점검", "지표 확인"], ["개'
           '선", "반영"]]}}'
       )),
    # ---- quad ----
    _p("swot", "SWOT", "pairs", "quad", {"mode": "swot"}, count=(4, 4),
       left_max=8, right_max=90,
       use="강점·약점·기회·위협 분석.",
       rules=(
           "- items 는 정확히 네 개, 차례로 `[\"강점\", 내용]`, `[\"약점\", 내용]`, "
           "`[\"기회\", 내용]`, `[\"위협\", 내용]`."
       ),
       example=(
           '{{"items": [["강점", "학내 데이터 접근"], ["약점", "개발 인력 4명"], ["기회", "분실물'
           ' 민원 증가"], ["위협", "기존 커뮤니티 게시판"]]}}'
       )),
    _p("matrix", "2×2 매트릭스", "pairs", "quad", {"mode": "matrix"}, count=(4, 4),
       left_max=14, right_max=70,
       use="두 축으로 나눈 네 칸 — 중요도×긴급도, 비용×효과.",
       rules=(
           "- items 는 네 개 `[칸 이름, 내용]` — "
           "왼쪽 위, 오른쪽 위, 왼쪽 아래, 오른쪽 아래 순서."
       ),
       example=(
           '{{"items": [["즉시", "보안 패치"], ["계획", "리팩터링"], ["위임", "문서 정리"], ["보'
           '류", "새 기능"]]}}'
       )),
    # ---- columns ----
    _p("compare-2", "두 안 비교", "columns", "columns", {"tone": "vs"}, count=(2, 2),
       left_max=16, right_max=40,
       use="두 대안을 항목별로 맞대어 — A안 대 B안, 기존 대 제안.",
       rules=(
           "- columns 는 두 개 `{\"title\": 안 이름, \"items\": [특징, ...]}`. "
           "항목 3~5개씩, 같은 관점 순서로."
       ),
       example=(
           '{{"columns": [{{"title": "React Native", "items": ["한 코드로 양 플랫폼", "학습 자료 '
           '많음"]}}, {{"title": "Flutter", "items": ["성능 우수", "Dart 학습 필요"]}}]}}'
       )),
    _p("compare-3", "세 안 비교", "columns", "columns", {"tone": "neutral"}, count=(3, 3),
       left_max=14, right_max=34,
       use="세 대안 비교 — 세 기술, 세 정책안.",
       rules="- columns 는 세 개. 항목 2~4개씩.",
       example=(
           '{{"columns": [{{"title": "MySQL", "items": ["관계형"]}}, {{"title": "MongoDB", "items'
           '": ["문서형"]}}, {{"title": "Firebase", "items": ["관리형"]}}]}}'
       )),
    _p("pros-cons", "장단점", "columns", "columns", {"tone": "posneg"}, count=(2, 2),
       left_max=10, right_max=50,
       use="한 대상의 장점과 단점.",
       rules="- columns 는 두 개, 차례로 제목 「장점」, 「단점」. 항목 2~4개씩.",
       example=(
           '{{"columns": [{{"title": "장점", "items": ["빠른 개발"]}}, {{"title": "단점", "items"'
           ': ["비용"]}}]}}'
       )),
    _p("before-after", "전후 비교", "columns", "columns", {"tone": "beforeafter"}, count=(2, 2),
       left_max=10, right_max=50,
       use="바뀌기 전과 후 — 개선 전후, 도입 전후.",
       rules=(
           "- columns 는 두 개, 차례로 「현재」(또는 「도입 전」)와 「개선 후」. "
           "항목은 같은 관점 순서로."
       ),
       example=(
           '{{"columns": [{{"title": "현재", "items": ["게시판에 흩어진 글"]}}, {{"title": "개선 '
           '후", "items": ["한 앱에서 검색"]}}]}}'
       )),
    _p("problem-solution", "문제·해결", "columns", "columns", {"tone": "beforeafter"}, count=(2, 2),
       left_max=10, right_max=60,
       use="문제와 그 해결책을 짝지어.",
       rules="- columns 는 두 개, 차례로 「문제」, 「해결」. 항목이 짝을 이루게 같은 순서로.",
       example=(
           '{{"columns": [{{"title": "문제", "items": ["습득물 정보가 흩어짐"]}}, {{"title": "해'
           '결", "items": ["단일 등록·검색"]}}]}}'
       )),
    _p("myth-fact", "오해와 사실", "columns", "columns", {"tone": "posneg"}, count=(2, 2),
       left_max=10, right_max=60,
       use="흔한 오해와 실제 — 통념 바로잡기.",
       rules="- columns 는 두 개, 「오해」, 「사실」. 사실은 자료의 근거가 있는 것만.",
       example=(
           '{{"columns": [{{"title": "오해", "items": ["라이더는 모두 전업"]}}, {{"title": "사실"'
           ', "items": ["겸업 비중이 크다"]}}]}}'
       )),
    _p("do-dont", "할 것·하지 말 것", "columns", "columns", {"tone": "posneg"}, count=(2, 2),
       left_max=10, right_max=50,
       use="지침 — 해야 할 것과 하지 말 것.",
       rules="- columns 는 두 개, 「할 것」, 「하지 말 것」.",
       example=(
           '{{"columns": [{{"title": "할 것", "items": ["출처 표기"]}}, {{"title": "하지 말 것", '
           '"items": ["수치 추정"]}}]}}'
       )),
    _p("three-column", "3단 구성", "columns", "columns", {"tone": "neutral"}, count=(3, 3),
       left_max=14, right_max=40,
       use="세 갈래로 나뉜 내용 — 세 영역, 세 이해관계자.",
       rules="- columns 는 세 개. 제목은 갈래 이름, 항목 2~4개씩.",
       example=(
           '{{"columns": [{{"title": "학생", "items": ["편의"]}}, {{"title": "관리실", "items": ['
           '"업무 감소"]}}, {{"title": "학교", "items": ["만족도"]}}]}}'
       )),
    # ---- kpi ----
    _p("kpi-grid", "지표 격자", "metrics", "kpi", {"mode": "grid"}, count=(3, 6),
       use="숫자 서너 개를 한눈에 — 현황 지표.", numeric=True,
       rules="- metrics 는 `[값, 이름]` 3~6개. 값은 자료에 있는 수치만.",
       example='{{"metrics": [["742명", "파일럿 인원"], ["6.1만", "월 검색"], ["2.8초", "p95"]]}}'),
    _p("stat-bars", "막대 지표", "metrics", "kpi", {"mode": "bars"}, count=(2, 5),
       use="비율·달성률을 막대로 — 퍼센트 지표.", numeric=True,
       rules="- metrics 는 `[백분율 값, 이름]`. 값은 「72%」처럼 퍼센트.",
       example='{{"metrics": [["72%", "응답률"], ["45%", "겸업 비율"]]}}'),
    _p("number-compare", "전후 수치", "metrics", "kpi", {"mode": "compare"}, count=(2, 2),
       use="한 지표의 전과 후 두 수 — 14분 → 3분.", numeric=True,
       rules="- metrics 는 정확히 두 개 `[값, 이름]` — 먼저 전, 다음 후.",
       example='{{"metrics": [["14분", "도입 전 검색 시간"], ["3분", "도입 후"]]}}'),
    # ---- chart ----
    _p("chart-pie", "원형 차트", "chart", "chart", {"kind": "pie"}, count=(2, 6),
       use="전체에 대한 구성비 — 합이 100%인 비중.", numeric=True,
       rules="- chart 는 kind pie, 계열 하나, 범주 2~6개. 값은 자료의 수치만.",
       example=(
           '{{"chart": {{"kind": "pie", "unit": "%", "categories": ["전업", "겸업"], "series": [{'
           '{"name": "비중", "values": [40, 60]}}]}}}}'
       )),
    _p("chart-donut", "도넛 차트", "chart", "chart", {"kind": "donut"}, count=(2, 6),
       use="구성비를 가운데 합계와 함께.", numeric=True,
       rules="- chart 는 kind donut, 계열 하나, 범주 2~6개.",
       example=(
           '{{"chart": {{"kind": "donut", "unit": "명", "categories": ["A", "B"], "series": [{{"n'
           'ame": "인원", "values": [12, 18]}}]}}}}'
       )),
    _p("chart-hbar", "가로 막대 차트", "chart", "chart", {"kind": "hbar"}, count=(2, 8),
       use="이름이 긴 범주의 크기 비교 — 순위.", numeric=True,
       rules="- chart 는 kind hbar. 범주 이름이 길어도 된다.",
       example=(
           '{{"chart": {{"kind": "hbar", "unit": "건", "categories": ["분실 신고", "습득 등록"], '
           '"series": [{{"name": "건수", "values": [42, 31]}}]}}}}'
       )),
    _p("chart-stacked", "누적 막대 차트", "chart", "chart", {"kind": "stacked"}, count=(2, 6),
       use="범주마다 여러 구성이 쌓인 비교.", numeric=True,
       rules="- chart 는 kind stacked, 계열 2~4개.",
       example=(
           '{{"chart": {{"kind": "stacked", "unit": "명", "categories": ["1학년", "2학년"], "seri'
           'es": [{{"name": "남", "values": [10, 12]}}, {{"name": "여", "values": [8, 9]}}]}}}}'
       )),
    # ---- text ----
    _p("question", "질문", "text", "text", {"mode": "question"}, count=(1, 1), right_max=90,
       use="청중에게 던지는 물음 — 문제 제기, 토론 질문.",
       rules="- body 는 물음표로 끝나는 질문 하나. 90자 이내.",
       example='{{"body": "문화통치기의 신문은 저항의 공간이었는가, 협력의 장치였는가?"}}'),
    _p("definition", "정의", "text", "text", {"mode": "definition"}, count=(1, 1), right_max=120,
       use="핵심 개념 하나의 정의 — 제목이 용어.",
       rules="- body 는 제목이 가리키는 용어의 정의 한두 문장. 120자 이내.",
       example='{{"body": "공론장은 사적 개인들이 모여 공적 문제를 토론하는 사회적 공간이다."}}'),
    _p("hypothesis", "가설", "text", "text", {"mode": "hypothesis"}, count=(1, 1), right_max=120,
       use="연구 가설·주장 한 문장.",
       rules="- body 는 검증할 가설 한 문장. 120자 이내.",
       example='{{"body": "주당 노동시간이 길수록 산재 경험률이 높을 것이다."}}'),
)

BY_NAME: dict[str, Pattern] = {p.name: p for p in PATTERNS}
NAMES: tuple[str, ...] = tuple(p.name for p in PATTERNS)
NUMERIC: frozenset[str] = frozenset(p.name for p in PATTERNS if p.numeric)
SHAPE_FIELD = {
    "pairs": "items", "columns": "columns", "metrics": "metrics", "chart": "chart", "text": "body",
}


def field_of(name: str) -> str | None:
    """The slide field a pattern fills."""
    pattern = BY_NAME.get(name)
    return SHAPE_FIELD[pattern.shape] if pattern else None


def catalogue() -> str:
    """One line per pattern for the outline planner: 「name: 용도」."""
    return "\n".join(f"- {p.name}: {p.use}" for p in PATTERNS)


def clean_pairs(name: str, value: Any) -> list[list[str]]:
    """`items` within the pattern's count and character limits; half-empty pairs dropped
    (a checklist or objective may leave the right side empty)."""
    pattern = BY_NAME[name]
    lo, hi = pattern.count
    optional_right = pattern.render == "list" and pattern.params.get("marker") in (
        "check", "target", "number", "star",
    )
    out: list[list[str]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            item = list(item.values())
        if not isinstance(item, list) or not item:
            continue
        left = " ".join(str(item[0]).split())[: pattern.left_max]
        right = " ".join(str(item[1]).split())[: pattern.right_max] if len(item) > 1 else ""
        # A copied table row is not an item.
        if "|" in left or right.count("|") >= 2:
            continue
        if left and (right or optional_right):
            out.append([left, right])
    out = out[:hi]
    # A fixed count (2×2, SWOT, two cards) is the pattern; elsewhere two items make a list.
    fewest = lo if lo == hi else min(lo, 2)
    return out if len(out) >= fewest else []


def clean_columns(name: str, value: Any) -> list[dict[str, Any]]:
    """`columns` within the pattern's count: each a title and 1–5 short items."""
    pattern = BY_NAME[name]
    lo, hi = pattern.count
    out: list[dict[str, Any]] = []
    for col in value if isinstance(value, list) else []:
        if not isinstance(col, dict):
            continue
        title = " ".join(str(col.get("title") or "").split())[: pattern.left_max]
        items = [
            " ".join(str(i).split())[: pattern.right_max]
            for i in (col.get("items") or [])
            if str(i).strip()
        ][:5]
        if title and items:
            out.append({"title": title, "items": items})
    out = out[:hi]
    return out if len(out) >= lo else []


def wire() -> list[dict[str, Any]]:
    """The table the web mirror must equal: name, label, shape, render, params, count."""
    return [
        {"name": p.name, "label": p.label, "shape": p.shape, "render": p.render,
         "params": dict(p.params), "count": list(p.count)}
        for p in PATTERNS
    ]
