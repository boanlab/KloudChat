"""The starting points: a sentence somebody begins from, before a shape.

A **design template** (`services.design_templates`) decides what the answer
comes out looking like. A starting point decides how the request opens — what
is being made, and what the person still has to supply. It is the shorter half
of the same idea, and until now it lived only in the frontend bundle.

It moved here because a starting point is no longer typed into the composer.
It is carried by the turn, the way an activated skill is: the message stays
the words the person wrote, and the template arrives beside it as its own
context block. That resolution happens on the server, so the catalogue it
resolves against has to be here too — an id only the client knows is an id the
server can only take on trust.

Written as literals rather than kept in a table, for the same reason the
rendering catalogue is read out of the image: these are versioned workflows
that ship with a release and change when the product changes, not rows anybody
edits at runtime. Somebody who wants a starting point of their own writes a
`templates` row, which is what that table is for, and both lists reach the
gallery in one shape.

The English half is declared and empty. The wire and the client's
fallback-to-Korean rule are worth having before the translations exist —
`design_templates` established exactly that pattern, and a card with a Korean
title in an English UI is readable, while a card waiting on a schema change is
not.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.chat import SessionKind


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One built-in starting point, named after the document it produces."""

    id: str
    #: The surface it starts. A starting point is a request, so its kind *is*
    #: the session kind — unlike a rendering template, whose `deck` has to be
    #: mapped onto the slides surface.
    kind: SessionKind
    #: The gallery's filter chip: 학업(대학생), 연구(대학원생), 업무(직장인).
    group: str
    title: str
    #: What you get. One line, no feature list.
    description: str
    #: What you have to bring, shown as chips before anybody commits, and what
    #: the composer asks for in its placeholder once the card is attached.
    fills: tuple[str, ...]
    #: Ends mid-sentence, where the person takes over. That is the whole shape
    #: of a starting point: it says what is being made and hands the turn back.
    prompt: str
    #: The 서식 this job comes out wearing, when one of them is right for it.
    #:
    #: 결과 서식 used to be the other half of a two-tab dialogue: pick what you
    #: are doing, then pick what it looks like. Two decisions for one job, and
    #: the second one is a question about typography asked of somebody who came
    #: to write an incident report. A 실험 노트 has a shape — that shape *is*
    #: `doc-lab`, and nobody starting one wants a different answer.
    #:
    #: Empty is a real answer and the common one: a 동향 조사 has no house
    #: style, so the writing surfaces choose the colour and the impression from
    #: the subject instead (`deck._theme_style`, `report._outline_style`).
    render_template_id: str = ""
    #: The English half. Empty until somebody writes it; the client falls back
    #: to the Korean, which leaves a card readable rather than blank.
    title_en: str = ""
    description_en: str = ""
    fills_en: tuple[str, ...] = ()
    prompt_en: str = ""
    #: 빈칸마다 하나씩, 어떻게 적으면 되는지 보여 주는 예.
    #:
    #: 「기간·언어」 alone is a noun; 「예: 2020~2024, 영어·한국어」 is an
    #: instruction. The card used to hand five nouns to a placeholder and hope,
    #: and the placeholder vanished at the first keystroke — so the person was
    #: left inventing a format for a thing they had never been shown. One
    #: example per blank, in the order of `fills`; missing ones stay blank.
    examples: tuple[str, ...] = ()
    examples_en: tuple[str, ...] = ()
    #: What the job cannot be done without. `web` — the answer has to come from
    #: sources found now, not remembered; `file` — the person has to bring the
    #: document the job is about. Shown on the card before anybody commits,
    #: and enforced by the composer once they do: 문헌 동향 조사 with web
    #: search switched off is a survey of the model's memory, and nothing on
    #: screen used to say so.
    needs: tuple[str, ...] = ()
    #: The catalogue skills this job runs with, by catalogue key
    #: (`starter._SKILLS`). Resolved on the server when the turn carries the
    #: starting point, so a person who never copied 인용 형식 맞추기 into
    #: their own list still gets it — the card promised it. The wire carries
    #: the names, which is what the composer shows and matches.
    skills: tuple[str, ...] = ()


def _t(
    id: str,
    kind: SessionKind,
    group: str,
    title: str,
    description: str,
    fills: tuple[str, ...],
    prompt: str,
    examples: tuple[str, ...] = (),
    *,
    needs: tuple[str, ...] = (),
    skills: tuple[str, ...] = (),
    render: str = "",
) -> PromptTemplate:
    """One card, positionally — the catalogue below is long enough that the
    keyword form doubled its length without making a row easier to read."""
    return PromptTemplate(
        id=id,
        kind=kind,
        group=group,
        title=title,
        description=description,
        fills=fills,
        prompt=prompt,
        examples=examples,
        needs=needs,
        skills=skills,
        render_template_id=render,
    )


C, R, S = SessionKind.chat, SessionKind.report, SessionKind.slides

# 세 갈래 — 학업(대학생), 연구(대학원생), 업무(직장인) — 에 표면마다 열 안팎.
# 기준은 `docs/llm_service_prompt_set_120.md` 의 시나리오: 한 카드가 한 과업이고,
# 같은 능력을 묻는 프롬프트는 한 카드로 합쳤다. 각 프롬프트는 무엇을 보고, 근거를
# 어떻게 다루고, 답에 무엇이 있어야 하고, 언제 끝나는지를 말하는 작은 절차다.
_ALL: tuple[PromptTemplate, ...] = (
    # ── 학업 × 챗: 학습 도우미 ─────────────────────────────────────────
    _t(
        "t_concept",
        C,
        "학업",
        "개념 쉽게 배우기",
        "낯선 개념을 내 수준에 맞는 비유와 예시로 풀고, 오해하기 쉬운 지점을 짚습니다",
        ("개념", "내 수준", "예시 형태"),
        "개념을 정확한 정의로 한 번, 내 수준에 맞는 비유로 한 번 설명한다. 그 개념이 왜 "
        "필요한지(없으면 무엇이 안 되는지)를 먼저 말하고, 손에 잡히는 예시 하나를 끝까지 "
        "따라간다. 비슷한 개념과 헷갈리는 지점을 한 문단으로 갈라 주고, 정확한 용어를 쓰되 "
        "처음 나오는 용어는 괄호에 원어를 한 번 병기한다. 마지막에 이해를 확인할 질문 둘을 "
        "남긴다. 모르는 부분은 모른다고 말한다.",
        (
            "예: 컨테이너와 가상머신의 차이",
            "예: 컴퓨터공학과 2학년, 네트워크 처음",
            "예: 코드 예제 / 그림 같은 비유",
        ),
        skills=("plain-explain",),
    ),
    _t(
        "t_compare",
        C,
        "학업",
        "두 개념 비교표",
        "두세 개념을 같은 기준으로 견주는 표와, 무엇을 언제 쓰는지 정리합니다",
        ("비교 대상", "비교 기준", "쓰임새"),
        "비교 대상을 같은 기준으로 표 하나에 놓는다. 기준은 요청이 준 것을 쓰고, 없으면 "
        "개념·구조·성능·사용 사례로 잡는다. 표의 칸은 짧은 사실로 채우고, 표 아래에서 "
        "「그래서 언제 무엇을 고르는가」를 상황 두셋으로 말한다. 흔한 오해(같다고 생각되는 "
        "점, 실제로 다른 점)를 한 문단으로 짚는다. 표에 없는 미세한 차이를 성능 차이라고 "
        "부르지 않는다.",
        ("예: 프로세스와 스레드", "예: 개념, 메모리 구조, 성능, 사용 사례", "예: 웹 서버 설계"),
        skills=("comparison-table",),
    ),
    _t(
        "t_worked_problem",
        C,
        "학업",
        "예제로 푸는 문제",
        "예제 문제를 단계별로 풀고, 같은 유형을 혼자 풀 수 있는 절차를 남깁니다",
        ("주제", "예제 문제 또는 조건", "원하는 깊이"),
        "예제 문제를 단계별로 푼다. 단계마다 무엇을 계산하는지와 왜 그 순서인지를 적고, "
        "숫자는 식과 함께 보인다(계산은 도구로 검산한다). 여러 방법이 있으면 같은 예제로 "
        "각각 풀어 결과를 표로 견준다. 마지막에 같은 유형을 혼자 풀 때 따를 절차를 번호 "
        "목록으로 정리하고, 흔히 틀리는 지점을 하나 짚는다.",
        (
            "예: CPU 스케줄링 평균 대기시간",
            "예: 프로세스 P1~P4 도착·실행 시간…",
            "예: 시험 대비 / 개념만",
        ),
        skills=("calculation-unit-check",),
    ),
    _t(
        "t_exam_prep",
        C,
        "학업",
        "시험 범위 정리",
        "시험에 자주 나오는 핵심과 헷갈리는 부분을 갈라 정리합니다",
        ("과목", "범위", "시험 형태"),
        "시험 범위를 핵심 개념·자주 나오는 문제 유형·헷갈리기 쉬운 쌍으로 갈라 정리한다. "
        "개념마다 정의 한 줄과 예시 하나, 헷갈리는 쌍은 무엇이 다른지를 표로 보인다. "
        "출제 가능성이 높은 순서로 우선순위를 매기되 근거(교재의 비중, 개념의 연결)를 "
        "적는다. 범위 밖의 내용을 보태지 않고, 확인할 수 없는 「기출」을 지어내지 않는다.",
        ("예: 데이터베이스", "예: 정규화(1NF~BCNF), 함수 종속", "예: 중간고사 서술형"),
        skills=("plain-explain",),
    ),
    _t(
        "t_code_reading",
        C,
        "학업",
        "코드 원리 이해",
        "언어 기능이나 코드가 어떤 원리로 동작하는지 짧은 예제로 설명합니다",
        ("주제", "언어", "알고 있는 것"),
        "동작 원리를 가장 짧은 예제 코드 하나로 보인다. 코드 다음에 실행 순서를 줄 단위로 "
        "따라가며 무엇이 언제 일어나는지 말하고, 필요하면 실제로 실행해 출력을 붙인다. "
        "자주 나오는 실수 하나와 실무에서 쓰는 형태 하나를 덧붙인다. 코드는 실행되는 "
        "것만 싣고, 설명에 없는 기능을 예제에 슬쩍 넣지 않는다.",
        ("예: Python decorator", "예: Python 3.12", "예: 함수는 일급 객체라는 것까지"),
    ),
    _t(
        "t_debug",
        C,
        "학업",
        "오류 디버깅",
        "오류 메시지의 대표 원인과 찾는 절차를 코드 기준으로 좁힙니다",
        ("오류 메시지", "언어·환경", "코드"),
        "오류 메시지가 뜻하는 것을 먼저 한 줄로 말한다. 대표 원인을 자주 나오는 순서로 "
        "들고, 원인마다 재현 코드 한 토막과 고친 코드를 보인다. 그다음 내 코드에서 원인을 "
        "좁히는 절차(무엇을 출력해 볼지, 어디에 중단점을 둘지)를 번호 목록으로 적는다. "
        "코드가 붙어 있으면 그 코드의 어느 줄이 원인인지 짚고 고친 버전을 실행해 확인한다.",
        ("예: IndexError: list index out of range", "예: Python 3.12", "붙여 넣기 또는 파일 첨부"),
        skills=("debug-procedure",),
    ),
    _t(
        "t_study_plan",
        C,
        "학업",
        "학습 계획 짜기",
        "기간과 주제를 주 단위 계획으로 나누고 확인 방법을 붙입니다",
        ("과목", "기간", "주제 목록", "주당 시간"),
        "기간을 주 단위로 나누고 주마다 주제·학습 목표·확인 방법(풀 문제, 만들어 볼 것)을 "
        "표로 짠다. 주제 순서는 의존 관계를 따르고 왜 그 순서인지 한 줄로 적는다. 주당 "
        "시간에 맞춰 분량을 조정하고, 밀렸을 때 줄일 수 있는 항목을 표시한다. 교재나 "
        "자료는 확인된 것만 추천하고 모르면 종류만 말한다.",
        ("예: 운영체제", "예: 4주", "예: 프로세스, 메모리, 파일 시스템, 동시성", "예: 주 6시간"),
        skills=("study-plan",),
    ),
    _t(
        "t_quiz",
        C,
        "학업",
        "지식 점검 문제",
        "이해를 확인하는 문제를 내고, 마지막에 정답과 해설을 모아 줍니다",
        ("주제", "문제 수", "난이도·형태"),
        "주제의 핵심을 고루 덮는 문제를 낸다. 문제는 번호를 붙여 먼저 전부 내고, 정답과 "
        "해설은 마지막에 따로 모은다 — 풀기 전에 답이 보이지 않게. 형태는 요청을 따르고 "
        "없으면 객관식과 짧은 서술을 섞는다. 해설은 정답인 이유와 오답이 틀린 이유를 함께 "
        "적고, 틀렸다면 다시 볼 개념을 짚는다.",
        ("예: TCP/IP", "예: 10개", "예: 중급, 객관식 7 + 서술 3"),
        skills=("quiz-writer",),
    ),
    # ── 학업 × 보고서: 과제·보고서 ─────────────────────────────────────
    _t(
        "t_report_assignment",
        R,
        "학업",
        "과제 보고서",
        "주제를 서론·본론·결론으로 갖추고, 근거와 출처를 단 과제 보고서를 씁니다",
        ("주제", "분량", "구성", "비교·강조할 것"),
        "과제 보고서를 서론·본론·결론으로 쓴다. 서론은 왜 이 주제인지와 무엇을 다룰지, "
        "본론은 요청이 말한 축(비교 대상, 장점과 한계)을 절로 삼아 근거를 붙여 쓰고, "
        "결론은 본론에서 보인 것만으로 맺는다. 사실·수치는 검색으로 확인한 것만 쓰고 "
        "출처를 단다. 분량은 요청을 따르고, 채우기 위한 되풀이를 하지 않는다.",
        (
            "예: 클라우드 컴퓨팅의 개념과 특징",
            "예: 5쪽",
            "예: 서론·본론·결론",
            "예: AWS·Azure·GCP 비교",
        ),
        needs=("web",),
        skills=("citation", "evidence"),
        render="doc-term-paper",
    ),
    _t(
        "t_report_from_material",
        R,
        "학업",
        "자료 기반 보고서·요약",
        "첨부한 강의자료나 논문의 내용만으로 보고서나 요약문을 씁니다",
        ("첨부 자료", "분량", "초점"),
        "첨부한 자료에 있는 내용만으로 쓴다. 자료에 없는 사실을 보태지 않고, 자료가 "
        "말하지 않는 것은 말하지 않는다고 적는다. 논문이면 연구 목적·제안 방법·실험 "
        "결과·한계점을 절로 삼고, 강의자료면 자료의 순서를 따라 핵심을 절로 삼는다. "
        "수치와 용어는 자료의 표기 그대로 쓰고, 어느 부분에서 왔는지 밝힌다.",
        ("강의자료·논문 PDF 첨부", "예: 2쪽", "예: 연구 목적·방법·결과·한계"),
        needs=("file",),
        skills=("source-faithful",),
    ),
    _t(
        "t_report_polish",
        R,
        "학업",
        "글 다듬기",
        "내가 쓴 글의 내용은 두고 논리 흐름과 문체를 대학 수준으로 고칩니다",
        ("원문", "유지할 것", "목표 문체"),
        "내용을 바꾸지 않고 문장과 흐름만 고친다. 먼저 원문의 구조에서 논리가 끊기는 곳을 "
        "짚고, 고친 글 전체를 보인 뒤, 무엇을 왜 바꿨는지 표로 정리한다(원문 → 고친 문장 "
        "→ 이유). 주장·수치·인용을 더하거나 빼지 않고, 저자의 관점을 유지한다. 문체는 "
        "요청을 따르고 없으면 대학 보고서의 합니다체다.",
        ("보고서 본문 첨부·붙여넣기", "예: 결론의 주장, 인용", "예: 학술적이되 읽기 쉽게"),
        needs=("file",),
        skills=("prose-polish",),
    ),
    _t(
        "t_report_lab",
        R,
        "학업",
        "실험 보고서",
        "목적·환경·방법·결과·분석 구성으로, 측정값의 단위와 자릿수를 맞춥니다",
        ("실험 주제", "환경·장비", "측정 데이터", "비교 대상"),
        "실험 보고서를 목적·환경·방법·결과·분석으로 쓴다. 측정 데이터가 있으면 결과 절에 "
        "표로 싣고 계산한 열(평균, 차이, 오차)을 더한다. 데이터가 없으면 값 자리를 "
        "(측정값)으로 비운 틀을 쓰고 무엇을 어떻게 측정할지를 적는다. 분석은 이론값과 "
        "측정값을 나란히 놓고 차이를 계산해 말하며, 계산하지 않은 편차를 말하지 않는다.",
        (
            "예: Docker와 VM의 웹 서버 성능 비교",
            "예: Ubuntu 24.04, 4코어, nginx",
            "표를 붙여 넣기",
            "예: 처리량·지연 시간",
        ),
        skills=("lab-notes", "calculation-unit-check"),
        render="doc-lab",
    ),
    _t(
        "t_report_essay",
        R,
        "학업",
        "주장형 에세이",
        "찬성과 반대를 검토한 뒤 내 주장을 근거로 세우는 글을 씁니다",
        ("논제", "내 입장", "분량"),
        "논제에 대한 찬성과 반대의 가장 강한 근거를 각각 공정하게 정리한 뒤, 내 주장을 "
        "세우고 반대 근거에 답한다. 구조는 문제 제기 → 양쪽 검토 → 주장과 근거 → 반론 "
        "대응 → 맺음이다. 사실 주장은 확인한 것만 쓰고 출처를 달며, 의견과 사실을 문장에서 "
        "구분한다. 입장이 정해지지 않았으면 검토 뒤에 더 설득력 있는 쪽을 고르고 이유를 "
        "밝힌다.",
        ("예: 대학교에서 생성형 AI 사용을 허용해야 하는가", "예: 조건부 찬성", "예: 3쪽"),
        needs=("web",),
        skills=("citation",),
        render="doc-term-paper",
    ),
    _t(
        "t_report_cover_letter",
        R,
        "학업",
        "자기소개서",
        "지원 분야에 맞춰 경험을 근거로 세우는 자기소개서 초안을 씁니다",
        ("지원 분야·기관", "경험", "강조점", "분량"),
        "자기소개서를 지원 분야가 요구하는 것에서 시작해, 내 경험 가운데 그것을 보여 주는 "
        "일화를 근거로 세운다. 경험은 요청에 적힌 것만 쓰고 없는 프로젝트·수치·수상을 "
        "지어내지 않으며, 부족한 자리는 「(여기에: 경험)」으로 비운다. 문단마다 하나의 "
        "주장(무엇을 할 수 있는가)과 그 근거(무엇을 했는가)를 두고, 상투적 표현 대신 "
        "구체적인 행동으로 쓴다.",
        (
            "예: 클라우드 엔지니어 인턴십",
            "예: 캡스톤에서 쿠버네티스 배포 담당",
            "예: 학습 의지, 협업",
            "예: 1,000자",
        ),
        skills=("cover-letter-tone",),
    ),
    _t(
        "t_report_from_memo",
        R,
        "학업",
        "메모를 결과보고서로",
        "흩어진 메모를 목표·수행 내용·결과·문제점·개선사항의 정식 보고서로 만듭니다",
        ("메모", "구성", "독자"),
        "메모의 내용을 목표·수행 내용·결과·문제점·향후 개선사항으로 다시 세운다. 메모에 "
        "있는 사실만 쓰고, 빠진 항목은 「(미정)」으로 표시해 무엇을 더 적어야 하는지 보인다. "
        "메모의 구어체와 약어는 독자가 알 수 있는 말로 바꾸되 뜻을 바꾸지 않는다. 결과는 "
        "수치가 있으면 표로 세운다.",
        ("메모 붙여 넣기·첨부", "예: 목표·수행·결과·문제·개선", "예: 지도교수"),
        needs=("file",),
        skills=("source-faithful",),
        render="doc-project-brief",
    ),
    # ── 학업 × 슬라이드: 수업·팀 프로젝트 발표 ────────────────────────
    _t(
        "t_slides_concept",
        S,
        "학업",
        "개념·기술 발표",
        "한 주제를 수업에서 발표할 장수·시간에 맞춰 구성하고 발표 노트를 붙입니다",
        ("주제", "장수·시간", "청중", "강조할 것"),
        "주제를 「왜 필요한가 → 무엇인가 → 어떻게 동작하는가 → 어디에 쓰는가 → 정리」의 "
        "줄기로 구성한다. 한 장에는 메시지 하나, 비교는 표로, 구조는 이름표 있는 그림이나 "
        "띠로 보인다. 장수와 시간은 요청을 따르고, 장마다 실제로 말할 발표 노트를 붙인다. "
        "사실과 수치는 확인된 것만 쓰고 출처 장을 마지막에 둔다.",
        (
            "예: Docker의 개념과 동작 원리",
            "예: 10장 / 10분",
            "예: 컴퓨터공학과 2학년",
            "예: 아키텍처 그림, VM과의 표",
        ),
        skills=("deck-story", "speaker-notes"),
        render="deck-lecture",
    ),
    _t(
        "t_slides_from_doc",
        S,
        "학업",
        "문서를 발표로",
        "첨부한 보고서나 논문을 핵심 메시지 중심의 발표자료로 바꿉니다",
        ("첨부 문서", "장수·시간", "구성"),
        "첨부한 문서의 내용만으로 발표를 만든다. 긴 문장을 옮기지 말고 장마다 핵심 "
        "메시지 한 줄과 그것을 받치는 사실 서넛으로 줄인다. 구성은 요청을 따르고(논문이면 "
        "Problem·Approach·Evaluation·Conclusion), 문서의 표와 수치는 그대로 옮긴다. "
        "문서에 없는 내용을 보태지 않으며, 발표 노트에 문서의 어느 부분인지 적는다.",
        ("보고서·논문 첨부", "예: 10장 이내", "예: Problem → Approach → Evaluation → Conclusion"),
        needs=("file",),
        skills=("source-faithful", "speaker-notes"),
    ),
    _t(
        "t_slides_team_project",
        S,
        "학업",
        "팀 프로젝트 최종 발표",
        "개발한 것을 문제·설계·구현·시연·회고 순서로 소개합니다",
        ("프로젝트", "만든 것·기능", "역할·일정", "결과·수치"),
        "팀 프로젝트를 문제 정의 → 목표 → 설계 → 구현 결과 → 시연 → 한계와 회고 → 역할의 "
        "줄기로 구성한다. 기능·수치·일정은 요청에 적힌 것만 쓰고, 없는 성과를 만들지 "
        "않는다. 설계 장은 구성 요소를 이름표 있는 띠로, 결과는 표나 지표로 보인다. "
        "장마다 발표 노트를 붙이고 시연 장에는 무엇을 보여 줄지 순서를 적는다.",
        (
            "예: 스마트 출석관리 시스템",
            "예: QR 출석, 출석 현황, 강의 목록",
            "예: 4명, 8주",
            "예: 응답 0.3초, 사용자 시험 20명",
        ),
        skills=("deck-story", "speaker-notes"),
    ),
    _t(
        "t_slides_experiment",
        S,
        "학업",
        "실험 결과 발표",
        "실험 결과를 표와 그래프로 보이고 핵심 차이를 강조합니다",
        ("실험", "결과 데이터", "핵심 차이", "장수"),
        "결과를 표와 그래프로 보이는 발표를 만든다. 결과 데이터는 요청에 있는 값만 쓰고 "
        "차트는 그 값으로만 그린다. 그래프마다 청중이 기억할 결과 하나를 제목으로 쓰고, "
        "실험 환경과 방법은 앞에 한 장씩, 한계는 뒤에 한 장 둔다. 데이터가 없으면 값 "
        "자리를 비운 틀을 만든다.",
        (
            "예: Docker와 VM 웹 서버 성능 비교",
            "표를 붙여 넣기",
            "예: 컨테이너 처리량 1.4배",
            "예: 8장",
        ),
        skills=("evidence", "speaker-notes"),
    ),
    _t(
        "t_slides_pitch",
        S,
        "학업",
        "제안 발표",
        "문제·해결책·기대효과가 드러나는 짧은 제안 발표를 만듭니다",
        ("제안", "문제", "해결책", "기대효과", "시간"),
        "제안 발표를 문제 → 왜 지금 → 해결책 → 기대효과 → 필요한 것의 줄기로 만든다. "
        "문제 장은 청중이 겪는 상황 하나로 시작하고, 기대효과는 요청이 준 수치만 쓴다. "
        "시간에 맞춰 장수를 정하고(7분이면 6~7장) 장마다 발표 노트를 붙인다. 마지막 "
        "장은 무엇을 요청하는지 한 줄이다.",
        (
            "예: 교내 생성형 AI 학습 지원 서비스",
            "예: 과제 질문이 밤에 몰린다",
            "예: 강의자료 기반 답변 봇",
            "예: 조교 응답 부담 40% 감소",
            "예: 7분",
        ),
        skills=("deck-story",),
        render="deck-proposal",
    ),
    _t(
        "t_slides_shorten",
        S,
        "학업",
        "발표 축약",
        "첨부한 긴 발표자료를 핵심을 지키며 정한 장수로 줄입니다",
        ("첨부 발표자료", "목표 장수·시간", "꼭 남길 것"),
        "첨부한 발표자료를 목표 장수로 줄인다. 먼저 원본의 줄기(무엇을 말하려 하는가)를 "
        "한 줄로 잡고, 그 줄기에 필요한 장만 남긴다. 합칠 수 있는 장은 합치고, 뺀 장의 "
        "내용은 남은 장의 발표 노트로 옮긴다. 새 내용을 보태지 않으며, 마지막에 무엇을 "
        "뺐는지 표로 알린다.",
        ("발표자료 첨부", "예: 7장 / 5분", "예: 결과 그래프, 결론"),
        needs=("file",),
        skills=("source-faithful", "deck-story"),
    ),
    # ── 연구 × 챗: 연구 도우미 ───────────────────────────────────────────
    _t(
        "t_paper_read",
        C,
        "연구",
        "논문 읽기",
        "첨부한 논문의 연구 문제·핵심 아이디어·기여를 논문의 말로 정리합니다",
        ("논문", "알고 싶은 것", "내 배경"),
        "첨부한 논문을 연구 문제 → 핵심 아이디어 → 기여(contribution) → 결과의 순서로 "
        "정리한다. 각 항목은 논문의 어느 절에서 왔는지 밝히고, 저자의 주장과 내 해석을 "
        "문장에서 구분한다. 논문에 없는 것을 보태지 않고, 논문이 답하지 않는 질문은 "
        "그렇다고 적는다. 마지막에 이 논문을 읽은 사람이 다음에 물을 만한 질문 셋을 "
        "남긴다.",
        (
            "논문 PDF 첨부",
            "예: 연구 문제, 핵심 아이디어, contribution",
            "예: 시스템 보안 석사 1년차",
        ),
        needs=("file",),
        skills=("source-faithful",),
    ),
    _t(
        "t_method_analysis",
        C,
        "연구",
        "방법론·평가 분석",
        "제안 방법이 기존 접근과 어떻게 다른지, 평가가 주장을 받치는지 따집니다",
        ("논문", "분석 축", "기존 접근"),
        "제안 방법을 기존 접근과 견준다: 무엇을 다르게 하는지, 그 차이가 어떤 가정 위에 "
        "서는지, 어디서 유리하고 어디서 불리한지. 평가는 주장마다 어떤 실험이 그것을 "
        "받치는지 표로 잇고, 받치지 못하는 주장(측정하지 않은 것, 베이스라인이 빠진 것, "
        "설정이 유리한 것)을 짚는다. 논문에 적힌 수치만 인용하고 절 번호를 단다.",
        ("논문 PDF 첨부", "예: methodology / evaluation", "예: Cilium, Falco"),
        needs=("file",),
        skills=("reviewer-lens", "source-faithful"),
    ),
    _t(
        "t_reviewer_critique",
        C,
        "연구",
        "리뷰어 관점 비판",
        "리뷰어 입장에서 방법과 평가의 잠재적 문제를 찾고 무엇을 요구할지 적습니다",
        ("논문·연구 내용", "학회·분야", "관심 지점"),
        "리뷰어의 자리에서 읽는다. 방법의 가정, 위협 모델, 평가 설정, 베이스라인, 통계, "
        "재현 가능성 순으로 잠재적 문제를 들고, 문제마다 근거(어느 절의 어떤 문장)와 "
        "저자에게 요구할 것(추가 실험, 설명, 조건)을 적는다. 심각한 것과 사소한 것을 "
        "가르고, 확인하지 못한 의심은 의심이라고 표시한다. 문장 다듬기는 마지막에 모은다.",
        (
            "논문 PDF 첨부 또는 요약 붙여넣기",
            "예: USENIX Security",
            "예: evaluation의 workload 선택",
        ),
        skills=("reviewer-lens",),
    ),
    _t(
        "t_research_ideas",
        C,
        "연구",
        "연구 주제 제안",
        "주어진 분야에서 새로운 연구 주제를 문제·접근·평가·위험과 함께 제안합니다",
        ("분야·기술", "개수", "제약"),
        "연구 주제를 요청한 개수만큼 제안한다. 주제마다 풀려는 문제 한 줄, 왜 지금 "
        "열려 있는지, 접근의 핵심 아이디어, 평가 방법, 가장 큰 위험을 적는다. 기존 연구와 "
        "겹칠 가능성이 있는 주제는 무엇과 겹치는지 밝히고(확인한 것만), 이름·연도를 "
        "지어내지 않는다. 마지막에 난이도와 기대 기여로 주제를 표로 견준다.",
        ("예: eBPF와 Kubernetes runtime security", "예: 5개", "예: 실험실에 클러스터 3노드"),
        needs=("web",),
        skills=("citation",),
    ),
    _t(
        "t_eval_design",
        C,
        "연구",
        "평가 설계",
        "제안 시스템의 효과를 입증할 평가 방법론을 설계합니다",
        ("제안 시스템", "주장", "비교 대상", "환경"),
        "입증하려는 주장마다 그것을 보이는 실험을 하나씩 설계한다. 실험마다 측정 지표, "
        "워크로드, 베이스라인, 환경, 반복 횟수와 통계 처리, 예상되는 결과의 모양을 적는다. "
        "타당성 위협(내적·외적)과 그것을 줄이는 장치를 표로 정리하고, 결과가 주장과 다르게 "
        "나올 때 무엇을 뜻하는지도 미리 적는다. 리뷰어가 요구할 만한 실험을 빠뜨리지 "
        "않는다.",
        (
            "예: Kubernetes 네트워크 보안 시스템",
            "예: 오버헤드가 낮고 정책이 세밀하다",
            "예: NetworkPolicy, Cilium",
            "예: 3노드 클러스터",
        ),
        skills=("eval-design", "result-restraint"),
    ),
    _t(
        "t_result_interpret",
        C,
        "연구",
        "결과 해석",
        "측정 결과를 시스템 논문의 관례에 맞게 과장 없이 해석합니다",
        ("결과 수치", "비교 기준", "논문 맥락"),
        "주어진 수치가 무엇을 뜻하는지 분야의 관례에 비추어 해석한다. 좋은지 나쁜지를 "
        "말할 때는 무엇과 견주어서인지(같은 계열 시스템의 보고 범위, 베이스라인)를 밝히고, "
        "확인한 수치만 인용한다. 결과가 뒷받침하는 주장과 뒷받침하지 않는 주장을 갈라 "
        "적고, 논문에 쓸 문장을 두셋 제안하되 데이터가 허락하는 만큼만 말한다.",
        (
            "예: throughput 오버헤드 3.2%, latency 4.7%",
            "예: 기존 eBPF 보안 도구",
            "예: 시스템 논문 evaluation 절",
        ),
        skills=("result-restraint",),
    ),
    _t(
        "t_related_compare",
        C,
        "연구",
        "관련 연구 비교",
        "여러 논문을 위협 모델·접근·적용 지점·평가 기준으로 견주는 표를 만듭니다",
        ("논문들", "비교 기준", "우리 연구"),
        "첨부한 논문들을 같은 기준(위협 모델, 접근, 적용 지점, 평가)으로 표 하나에 놓는다. "
        "칸은 각 논문의 말로 채우고 어느 절에서 왔는지 밝힌다. 표 아래에서 차이가 뜻하는 "
        "것을 정리하고, 우리 연구가 있으면 어느 칸에서 갈리는지 적는다. 읽지 못한 논문은 "
        "빈 칸으로 두고 그렇다고 표시한다.",
        (
            "논문 PDF 여러 개 첨부",
            "예: threat model, approach, enforcement point, evaluation",
            "예: per-process 네트워크 접근 제어",
        ),
        needs=("file",),
        skills=("comparison-table", "source-faithful"),
    ),
    _t(
        "t_reviewer_response",
        C,
        "연구",
        "리뷰어 대응",
        "리뷰어 지적에 답하기 위해 필요한 추가 실험과 설명을 제안합니다",
        ("리뷰어 지적", "현재 실험", "가능한 자원"),
        "지적을 그대로 옮긴 뒤, 리뷰어가 실제로 우려하는 것이 무엇인지 한 줄로 푼다. "
        "답하는 데 필요한 것을 추가 실험·기존 결과의 재해석·설명 보강으로 갈라 표로 "
        "정리하고, 실험마다 무엇을 어떤 조건에서 재고 어떤 결과면 지적이 해소되는지 적는다. "
        "받아들일 지적과 반박할 지적을 구분하고, 반박은 근거를 붙인다.",
        ("예: baseline이 충분하지 않다", "예: Cilium과만 비교", "예: 2주, 클러스터 1개"),
        skills=("rebuttal-manner", "eval-design"),
    ),
    _t(
        "t_claim_challenge",
        C,
        "연구",
        "주장 반론 찾기",
        "내 연구의 핵심 주장에 리뷰어가 제기할 반론과 답을 미리 정리합니다",
        ("핵심 주장", "근거", "분야"),
        "핵심 주장에 대해 리뷰어가 제기할 반론을 강한 순서로 든다. 반론마다 어떤 가정을 "
        "공격하는지, 어떤 실험이나 사례가 반론을 뒷받침할 수 있는지, 그리고 내가 답할 수 "
        "있는지(답의 근거, 또는 인정해야 할 한계)를 적는다. 주장 자체가 흔들리는 반론과 "
        "표현만 고치면 되는 반론을 갈라 표로 정리한다.",
        (
            "예: per-process 접근 제어가 pod-level 정책보다 세밀하다",
            "예: 프로세스별 정책 실험",
            "예: 클라우드 보안",
        ),
        skills=("reviewer-lens",),
    ),
    # ── 연구 × 보고서: 논문·연구문서 ───────────────────────────────────
    _t(
        "t_report_literature",
        R,
        "연구",
        "문헌 동향 조사",
        "주제의 최근 연구를 검색으로 확인해 쟁점·대표 연구·보고된 수치를 표로 정리합니다",
        ("주제", "기간", "정리 축", "미확인 처리"),
        "주제의 최근 연구를 검색으로 확인해 정리한다. 쟁점별로 절을 나누고, 대표 연구는 "
        "저자·연도·핵심 주장·보고된 수치를 표로 세우며, 검색에서 확인한 것만 「확인됨」으로 "
        "적고 나머지는 「확인 필요」로 표시한다. 상반된 결과가 있으면 나란히 놓고 왜 갈리는지 "
        "(표본, 조건, 측정) 말한다. 서지는 검색 결과의 표기 그대로, 지어내지 않는다. "
        "마지막에 아직 열려 있는 질문을 적는다.",
        (
            "예: 고체 전해질 배터리의 상용화 장벽",
            "예: 최근 2년",
            "예: 쟁점, 대표 연구, 보고된 수치",
            "예: 확인 못 한 서지는 「확인 필요」",
        ),
        needs=("web",),
        skills=("citation", "evidence", "result-restraint"),
    ),
    _t(
        "t_paper_abstract",
        R,
        "연구",
        "논문 초록",
        "연구 내용을 분야 관례에 맞는 초록으로 정해진 단어 수 안에 씁니다",
        ("연구 내용", "분야", "단어 수", "언어"),
        "초록을 문제 → 한계 → 제안 → 방법의 핵심 → 결과(수치) → 의의의 순서로 한 문단에 "
        "쓴다. 연구 내용에 있는 수치와 이름만 쓰고 없는 결과를 만들지 않는다. 단어 수 "
        "한도를 지키고, 분야의 관례(시스템 논문이면 구현과 평가를 명시)를 따른다. 언어는 "
        "요청을 따르며 영어면 academic English로 쓴다.",
        ("연구 내용 붙여 넣기", "예: 컴퓨터 시스템", "예: 200단어 이내", "예: 영어"),
        skills=("paper-structure", "result-restraint"),
    ),
    _t(
        "t_paper_intro",
        R,
        "연구",
        "논문 서론",
        "문제 정의와 기여를 Problem → Gap → Approach → Contribution 구조로 씁니다",
        ("문제 정의", "기존 연구의 한계", "제안", "기여"),
        "서론을 Problem(왜 중요한가) → Gap(기존 연구가 무엇을 못 하는가) → Approach(무엇을 "
        "어떻게 하는가) → Contribution(무엇을 내놓는가)의 순서로 쓴다. 문단마다 하나의 "
        "역할을 두고 앞 문단에서 다음 문단으로 넘어가는 문장을 명시한다. 기존 연구는 "
        "제공된 것만 인용하고, 기여는 번호 목록으로 세되 결과 절에서 보일 수 있는 것만 "
        "적는다. 이미 있는 서론이면 흐름을 분석한 뒤 다시 쓴다.",
        (
            "예: pod 단위 정책은 프로세스를 구분 못 함",
            "예: 기존 eBPF 도구는 관측만",
            "예: 프로세스별 네트워크 정책 집행",
            "예: 설계, 구현, 3.2% 오버헤드 평가",
        ),
        skills=("paper-structure",),
    ),
    _t(
        "t_paper_related",
        R,
        "연구",
        "관련 연구 절",
        "제공한 논문들을 주제별로 묶어 우리 연구와의 차이가 드러나게 씁니다",
        ("논문들", "주제 분류", "우리 연구"),
        "제공한 논문을 주제별로 묶어 소절로 쓴다. 소절마다 그 갈래가 무엇을 했고 무엇을 "
        "못 했는지를 논문 단위로 적고, 마지막 문장에서 우리 연구가 어디서 갈리는지 말한다. "
        "인용은 제공된 논문만, 확인하지 못한 서지는 「(확인 필요)」로 표시한다. 논문을 "
        "나열하지 말고 비교로 쓴다.",
        ("논문 PDF·서지 첨부", "예: 정책 집행, 관측, 정책 생성", "예: LLM 기반 정책 자동 생성"),
        needs=("file",),
        skills=("citation", "source-faithful"),
    ),
    _t(
        "t_paper_design",
        R,
        "연구",
        "시스템 설계 절",
        "시스템 아키텍처를 System Design 절에 맞는 학술 문체로 설명합니다",
        ("아키텍처 설명", "구성 요소", "설계 목표"),
        "설계 절을 설계 목표 → 개요(구성 요소와 흐름) → 구성 요소별 상세 → 설계 결정의 "
        "근거 순으로 쓴다. 구성 요소 이름은 제공된 것을 그대로 쓰고, 흐름은 요청·처리·"
        "응답의 순서로 따라간다. 왜 그렇게 설계했는지(대안과 버린 이유)를 결정마다 한 "
        "문장으로 적고, 구현 세부와 설계 원리를 문단에서 구분한다. 없는 구성 요소를 "
        "보태지 않는다.",
        (
            "아키텍처 설명·그림 붙여 넣기",
            "예: 수집기, 정책 엔진, 집행기",
            "예: 낮은 오버헤드, 프로세스 단위 정책",
        ),
        skills=("paper-structure", "terms"),
    ),
    _t(
        "t_paper_eval",
        R,
        "연구",
        "평가 절",
        "실험 환경과 측정 결과로 Evaluation 절을 데이터가 허락하는 만큼만 씁니다",
        ("실험 환경", "측정 결과", "연구 질문"),
        "평가 절을 연구 질문 → 환경과 방법 → 질문별 결과 → 논의(한계)로 쓴다. 결과는 "
        "제공된 수치만 표로 싣고 본문은 표를 읽어 주되, 데이터가 뒷받침하는 범위에서만 "
        "말한다 — 「크게 개선」 대신 「4.7% 낮다」. 결과가 주장을 받치지 못하는 지점은 "
        "숨기지 않고 논의에 적는다. 계산한 값은 식과 함께 쓴다.",
        (
            "예: 3노드 클러스터, 각 16코어",
            "표를 붙여 넣기",
            "예: 오버헤드는 얼마인가, 정책이 더 세밀한가",
        ),
        skills=("result-restraint", "evidence", "calculation-unit-check"),
    ),
    _t(
        "t_paper_english",
        R,
        "연구",
        "학술 영어 교정",
        "논문 문단을 뜻과 기술 내용을 지키며 자연스러운 academic English로 고칩니다",
        ("원문", "학회·문체", "유지할 용어"),
        "뜻과 기술적 내용을 바꾸지 않고 문장만 고친다. 고친 문단 전체를 먼저 보이고, "
        "바꾼 자리를 원문 → 고친 문장 → 이유의 표로 정리한다. 이유는 문법·명확성·간결성·"
        "학술 관례 가운데 하나로 적는다. 용어는 요청이 지킨 것을 그대로 두고, 주장을 "
        "강하게 하거나 약하게 하는 낱말 변경은 하지 않는다.",
        ("문단 붙여 넣기·첨부", "예: USENIX, 간결한 문체", "예: enforcement point, pod"),
        skills=("academic-english",),
    ),
    _t(
        "t_paper_review",
        R,
        "연구",
        "논문 리뷰 작성",
        "첨부한 논문을 Summary·Strengths·Weaknesses·Questions 구조로 평가합니다",
        ("논문", "학회", "평가 기준"),
        "리뷰를 Summary(저자의 말로 한 문단) → Strengths → Weaknesses → Questions for "
        "authors → 점수와 확신도의 순서로 쓴다. 약점마다 근거(절·문장)와 그것이 결론에 "
        "미치는 영향을 적고, 고칠 수 있는 것과 없는 것을 가른다. 논문에 없는 것을 "
        "요구할 때는 왜 필요한지 설명한다. 문장 지적은 마지막에 모은다.",
        ("논문 PDF 첨부", "예: IEEE S&P", "예: novelty, soundness, evaluation"),
        needs=("file",),
        skills=("reviewer-lens", "source-faithful"),
        render="doc-report",
    ),
    _t(
        "t_paper_rebuttal",
        R,
        "연구",
        "반박문",
        "리뷰어 지적에 실험 결과로 직접 답하는 정중한 rebuttal을 씁니다",
        ("리뷰어 코멘트", "실험 결과·근거", "언어"),
        "지적마다 리뷰어의 말을 짧게 인용한 뒤 바로 답한다. 답은 근거(제공된 실험 결과, "
        "논문의 절)를 붙이고, 받아들이는 지적은 무엇을 어떻게 고칠지, 반박하는 지적은 왜 "
        "그렇지 않은지를 적는다. 정중하되 에두르지 않고, 없는 실험을 했다고 하지 않는다. "
        "마지막에 수정 사항 목록을 붙인다.",
        ("코멘트 붙여 넣기", "추가 실험 결과 붙여 넣기·첨부", "예: 영어"),
        skills=("rebuttal-manner", "evidence"),
    ),
    _t(
        "t_research_proposal",
        R,
        "연구",
        "연구계획서",
        "연구 배경·문제 정의·목표·방법론·평가 계획·기대효과를 갖춘 계획서를 씁니다",
        ("주제", "문제 정의", "방법론", "평가 계획", "기간"),
        "연구계획서를 배경 → 문제 정의 → 연구 목표(연구 질문) → 방법론 → 평가 계획 → "
        "일정 → 기대효과의 순서로 쓴다. 연구 질문은 답할 수 있는 형태로 번호를 붙이고, "
        "방법론은 질문마다 어떻게 답하는지, 평가 계획은 무엇을 재어 성공을 판단하는지 "
        "적는다. 기존 연구는 확인된 것만 인용하고, 없는 예비 결과를 만들지 않는다.",
        (
            "예: LLM을 활용한 Kubernetes 보안 정책 자동 생성",
            "예: 수작업 정책은 늦고 틀린다",
            "예: 로그 → 의도 → 정책 → 검증",
            "예: 정확도·오버헤드·전문가 평가",
            "예: 2년",
        ),
        needs=("web",),
        skills=("paper-structure", "citation"),
        render="doc-report",
    ),
    # ── 연구 × 슬라이드: 연구·학회 발표 ──────────────────────────────
    _t(
        "t_slides_paper_review",
        S,
        "연구",
        "논문 리뷰 세미나",
        "첨부한 논문을 연구실 세미나에서 발표할 리뷰 슬라이드로 만듭니다",
        ("논문", "장수", "강조할 것"),
        "첨부한 논문을 배경 → 문제 → 핵심 아이디어 → 설계 → 평가 → 강점과 약점 → 우리에게 "
        "주는 시사점의 줄기로 만든다. 논문의 그림과 수치는 그대로 옮기고 어느 절인지 "
        "노트에 적는다. 논문에 없는 것을 보태지 않되, 강점·약점·시사점 장은 발표자의 "
        "판단으로 쓰고 그렇다고 표시한다. 장마다 발표 노트를 붙인다.",
        ("논문 PDF 첨부", "예: 15장", "예: 평가 방법"),
        needs=("file",),
        skills=("source-faithful", "speaker-notes"),
    ),
    _t(
        "t_slides_conference",
        S,
        "연구",
        "학회 발표",
        "내 논문을 정해진 시간의 학회 발표로 만듭니다. 텍스트를 줄이고 그림·결과를 앞세웁니다",
        ("논문", "발표 시간", "청중", "강조할 결과"),
        "논문을 발표 시간에 맞는 장수로 만든다(12분이면 12장 안팎). 줄기는 문제 → 핵심 "
        "아이디어 한 장 → 설계 → 평가(결과마다 기억할 것 하나) → 결론. 글자를 줄이고 "
        "아키텍처는 띠나 그림으로, 결과는 표·차트로 보인다. 수치는 논문의 것만 쓰고, "
        "장마다 말할 발표 노트와 예상 질문 장을 마지막에 둔다.",
        ("논문 PDF 첨부", "예: 12분", "예: 시스템 보안 연구자", "예: 오버헤드 3.2%"),
        needs=("file",),
        skills=("deck-story", "speaker-notes"),
        render="deck-defense",
    ),
    _t(
        "t_slides_problem",
        S,
        "연구",
        "문제 중심 발표",
        "motivation과 research gap이 또렷하게 전달되는 발표를 구성합니다",
        ("연구", "문제 상황", "기존 연구의 한계", "장수"),
        "발표의 앞 절반을 문제에 쓴다: 청중이 아는 상황 → 그 상황에서 생기는 문제 → 기존 "
        "연구가 그것을 못 푸는 이유(gap) → 그래서 우리가 묻는 질문. 각 장은 한 문장 "
        "메시지와 근거 하나로 만들고, gap 장은 기존 연구를 표로 견준다. 제안은 뒤 절반에 "
        "간단히 두고, 없는 결과를 만들지 않는다.",
        (
            "예: 프로세스 단위 네트워크 접근 제어",
            "예: 한 pod 안의 사이드카가 같은 정책",
            "예: pod-level 정책만 가능",
            "예: 10장",
        ),
        skills=("deck-story",),
    ),
    _t(
        "t_slides_architecture",
        S,
        "연구",
        "아키텍처 발표",
        "제안 시스템의 구성 요소와 워크플로를 그림과 흐름으로 보입니다",
        ("시스템", "구성 요소", "워크플로", "장수"),
        "아키텍처를 전체 그림 한 장 → 구성 요소별 한 장(역할, 입력·출력) → 워크플로(요청이 "
        "지나는 순서) → 설계 결정과 대안의 줄기로 만든다. 구성 요소 이름은 제공된 것 "
        "그대로, 관계는 이름표 있는 띠와 순서로 보인다. 전체 그림은 도식(개념도·구조도)으로 "
        "따로 만들 수 있다고 노트에 적는다.",
        (
            "예: eBPF 기반 런타임 보안 시스템",
            "예: 수집기, 정책 엔진, 집행기, 대시보드",
            "예: 이벤트 수집 → 분석 → 정책 → 집행",
            "예: 8장",
        ),
        skills=("terms", "speaker-notes"),
    ),
    _t(
        "t_slides_eval",
        S,
        "연구",
        "평가 결과 발표",
        "실험 결과를 그래프마다 기억할 결과 하나로 강조하는 발표를 만듭니다",
        ("실험 결과", "연구 질문", "비교 대상"),
        "결과를 연구 질문 순서로 한 장씩 만든다. 장 제목은 그 그래프에서 청중이 기억할 "
        "결과 한 문장이고(「오버헤드 3.2%로 기존의 절반」), 차트는 제공된 값으로만 그린다. "
        "환경·방법 장을 앞에 하나, 한계 장을 뒤에 하나 둔다. 결과가 주장을 받치지 못하는 "
        "지점은 숨기지 않는다.",
        ("표 붙여 넣기·첨부", "예: 오버헤드, 정밀도, 확장성", "예: NetworkPolicy, Cilium"),
        skills=("evidence", "result-restraint"),
    ),
    _t(
        "t_slides_research_plan",
        S,
        "연구",
        "연구계획 발표",
        "Motivation·Gap·Research Questions·Approach·Evaluation Plan·Contributions 순의 발표",
        ("연구", "연구 질문", "접근", "평가 계획", "장수"),
        "연구계획 발표를 Motivation → Gap → Research Questions → Approach → Evaluation "
        "Plan → Expected Contributions → 일정의 줄기로 만든다. 연구 질문은 번호를 붙여 한 "
        "장에 모으고, 접근과 평가 계획은 질문 번호와 짝지어 보인다. 없는 예비 결과를 "
        "만들지 않고, 마지막에 심사위원의 예상 질문과 답 장을 둔다.",
        (
            "예: LLM 기반 보안 정책 자동 생성",
            "예: RQ1 정확도, RQ2 오버헤드, RQ3 검증",
            "예: 로그 → 의도 → 정책",
            "예: 정책 정확도·전문가 평가",
            "예: 15장",
        ),
        skills=("deck-story", "speaker-notes"),
        render="deck-defense",
    ),
    _t(
        "t_slides_poster_talk",
        S,
        "연구",
        "포스터 발표",
        "연구를 3분 포스터 발표용 다섯 장 안팎으로 요약합니다",
        ("연구 내용", "시간", "핵심 결과"),
        "포스터 발표를 문제 → 아이디어 → 설계 한 장 → 핵심 결과 한 장 → 요청·연락의 다섯 "
        "장으로 줄인다. 한 장에 한 문장 메시지, 결과는 수치 하나를 크게. 3분에 맞춘 "
        "발표 노트를 장마다 붙이고, 질문이 나올 자리를 노트에 적는다. 제공된 수치만 쓰고, "
        "포스터에 있는 그림을 어느 장에서 가리킬지 노트에 적는다.",
        ("연구 요약 붙여 넣기", "예: 3분", "예: 오버헤드 3.2%"),
        skills=("deck-story", "speaker-notes"),
    ),
    _t(
        "t_slides_related",
        S,
        "연구",
        "기존 연구 비교 장",
        "우리 연구와 기존 연구들을 비교표로 견줘 차별점을 보이는 장을 만듭니다",
        ("우리 연구", "기존 연구들", "비교 기준"),
        "비교표 장 하나를 중심으로 만든다: 열은 우리 연구와 기존 연구, 행은 기준(위협 "
        "모델, 적용 지점, 세밀함, 오버헤드 등). 칸은 짧은 사실로, 확인하지 못한 칸은 "
        "「?」로 둔다. 표 앞에 기준을 설명하는 장 하나, 뒤에 차별점을 한 문장씩 정리하는 "
        "장 하나를 둔다. 기존 연구의 수치는 제공된 것만 쓴다.",
        (
            "예: 프로세스 단위 정책 집행",
            "예: Cilium, Falco, Tetragon, KubeArmor",
            "예: 위협 모델, 집행 지점, 세밀함, 오버헤드",
        ),
        skills=("comparison-table",),
    ),
    _t(
        "t_slides_defense",
        S,
        "연구",
        "학위 디펜스",
        "개별 연구를 하나의 연구 질문으로 엮는 디펜스 발표의 전체 줄기를 구성합니다",
        ("학위 주제", "개별 연구들", "통합 연구 질문", "장수"),
        "디펜스를 하나의 연구 질문으로 엮는다: 큰 질문 → 왜 어려운가 → 개별 연구가 각각 "
        "어느 부분에 답하는가(지도 한 장) → 연구별 문제·방법·결과 → 종합(질문에 얼마나 "
        "답했는가) → 한계와 향후 연구 → 기여 목록. 연구마다 같은 틀로 장을 만들고, 결과는 "
        "제공된 수치만 쓴다. 장수는 요청을 따르고 마지막에 예상 질문 장을 둔다.",
        (
            "예: 클라우드 네이티브 런타임 보안",
            "예: 연구 1 관측, 연구 2 집행, 연구 3 정책 생성",
            "예: 런타임 보안을 프로세스 단위로 할 수 있는가",
            "예: 30장",
        ),
        skills=("deck-story", "speaker-notes"),
        render="deck-defense",
    ),
    # ── 업무 × 챗: 업무 도우미 ───────────────────────────────────────────
    _t(
        "t_problem_analysis",
        C,
        "업무",
        "문제 원인 분석",
        "증상을 여러 관점으로 나눠 가능한 원인과 확인 순서를 정리합니다",
        ("증상", "환경", "최근 변경", "관점"),
        "증상을 관점별(요청이 준 것, 없으면 애플리케이션·인프라·네트워크)로 나눠 가능한 "
        "원인을 든다. 원인마다 그것이 맞다면 보일 징후, 확인하는 방법(어떤 지표·로그·"
        "명령), 소요 시간을 표로 정리하고, 확인 순서는 가능성이 높고 확인이 싼 것부터 "
        "잡는다. 최근 변경과 겹치는 원인을 먼저 본다. 환경에 없는 구성 요소를 가정하지 "
        "않는다.",
        (
            "예: 응답시간이 30% 증가",
            "예: Kubernetes, Java, RDS",
            "예: 지난주 배포, 트래픽 증가",
            "예: 애플리케이션·인프라·네트워크",
        ),
        skills=("risk-lens",),
    ),
    _t(
        "t_solution_compare",
        C,
        "업무",
        "솔루션 비교",
        "여러 제품·서비스를 도입 관점의 같은 기준으로 견주고 상황별 추천을 합니다",
        ("비교 대상", "관점·기준", "우리 상황"),
        "비교 대상을 같은 기준으로 표 하나에 놓는다. 기준은 요청이 준 것(비용, 운영, "
        "확장성, 생태계, 지원)을 쓰고, 칸은 확인된 사실만 채우며 확인하지 못한 것은 "
        "「확인 필요」로 둔다. 표 아래에서 우리 상황에 무엇이 맞는지를 조건문(「~라면 "
        "A」)으로 말하고, 검색으로 확인한 최신 사항(가격, 지원 종료)은 출처를 단다.",
        (
            "예: AWS EKS, Azure AKS, Google GKE",
            "예: 비용, 운영 부담, 확장성, 보안",
            "예: 직원 300명, AWS 이미 사용",
        ),
        needs=("web",),
        skills=("comparison-table", "evidence"),
    ),
    _t(
        "t_decision_framework",
        C,
        "업무",
        "의사결정 기준",
        "선택지를 고르기 위한 판단 기준과 결정 절차를 프레임워크로 만듭니다",
        ("결정할 것", "선택지", "관점", "제약"),
        "결정을 판단 기준의 표로 만든다: 기준(비용, 운영, 확장성, 보안, 시간), 각 선택지가 "
        "그 기준에서 어떤지, 기준의 가중치를 정할 질문. 그다음 결정 절차를 단계로 적는다 "
        "— 무엇을 먼저 확인하고, 어떤 조건이면 어느 쪽으로 기우는지. 결론은 조건부로 "
        "말하고, 확인되지 않은 수치를 가정하지 않는다. 마지막에 결정 전에 답해야 할 "
        "질문 목록을 둔다.",
        (
            "예: 온프레미스 GPU 서버 구축 vs 클라우드 GPU",
            "예: 구축 / 클라우드",
            "예: 비용, 운영, 확장성",
            "예: 예산 5억, 6개월",
        ),
        skills=("decision-frame",),
    ),
    _t(
        "t_ideas",
        C,
        "업무",
        "아이디어 목록",
        "목표에 맞는 아이디어를 효과·난이도·필요한 것과 함께 제안합니다",
        ("목표", "대상", "개수", "제약"),
        "아이디어를 요청한 개수만큼 낸다. 아이디어마다 한 줄 설명, 누구의 어떤 일이 어떻게 "
        "달라지는지, 기대 효과의 크기, 구현 난이도, 필요한 것(데이터·권한·도구)을 적고, "
        "표로 효과와 난이도를 견준다. 비슷한 것을 개수 채우려 되풀이하지 않고, 먼저 해 "
        "볼 것 셋을 골라 이유를 말한다.",
        (
            "예: 개발팀 생산성 향상",
            "예: 사내 생성형 AI 서비스",
            "예: 10개",
            "예: 사내망, 외부 API 불가",
        ),
    ),
    _t(
        "t_risk_analysis",
        C,
        "업무",
        "위험 분석",
        "프로젝트나 시스템의 위험을 관점별로 찾고 완화책과 확인 방법을 붙입니다",
        ("대상", "단계·상황", "관점"),
        "위험을 관점별(요청이 준 것, 없으면 기술·운영·보안·조직)로 찾는다. 위험마다 "
        "일어나는 조건, 영향, 가능성, 완화책, 조기에 알아챌 신호를 표로 정리하고, 영향과 "
        "가능성으로 우선순위를 매긴다. 이 단계에서 결정해야 막을 수 있는 위험을 따로 "
        "표시한다. 일반론보다 이 대상에서 실제로 생기는 위험을 쓴다.",
        (
            "예: Kubernetes 기반 서비스 신규 구축",
            "예: 프로젝트 초기",
            "예: 기술적 위험 / 데이터·모델·인프라·사용자",
        ),
        skills=("risk-lens",),
    ),
    _t(
        "t_incident_procedure",
        C,
        "업무",
        "장애 원인 분석 절차",
        "간헐적 장애의 원인을 좁혀 가는 확인 절차를 환경에 맞게 만듭니다",
        ("증상", "환경", "발생 패턴", "이미 확인한 것"),
        "장애를 좁히는 절차를 단계로 만든다. 단계마다 무엇을 확인하는지, 어떤 명령·"
        "대시보드·로그를 보는지, 결과가 A면 다음에 무엇을, B면 무엇을 보는지(분기)를 "
        "적는다. 간헐적이면 발생 시각과 상관 있는 것(배포, 스케일링, 특정 노드)부터 "
        "본다. 이미 확인한 것은 다시 시키지 않고, 환경에 없는 구성 요소를 가정하지 않는다.",
        (
            "예: 간헐적 502",
            "예: Kubernetes, ingress-nginx, Java",
            "예: 하루 3~4회, 저녁",
            "예: 애플리케이션 로그에 오류 없음",
        ),
        skills=("debug-procedure", "incident-timeline"),
    ),
    _t(
        "t_meeting_questions",
        C,
        "업무",
        "회의 전 질문 정리",
        "의사결정 회의 전에 확인해야 할 기술·비용 질문을 우선순위로 정리합니다",
        ("회의 주제", "결정할 것", "참석자", "아는 것"),
        "결정 전에 답이 있어야 하는 질문을 기술·비용·운영·일정으로 나눠 든다. 질문마다 "
        "왜 결정에 필요한지, 누가 답할 수 있는지, 답이 없으면 어떤 가정을 두고 갈지 표로 "
        "정리하고, 회의에서 반드시 답을 받을 셋을 고른다. 이미 아는 것은 질문으로 다시 "
        "만들지 않고, 답이 나오면 어떤 결정이 따라오는지 한 줄씩 붙인다.",
        (
            "예: GPU 인프라 투자",
            "예: 구축 여부와 규모",
            "예: CTO, 인프라팀, 재무",
            "예: 후보 모델 3개, 월 예산",
        ),
        skills=("decision-frame",),
    ),
    _t(
        "t_data_insight",
        C,
        "업무",
        "데이터 해석",
        "표의 핵심 변화와 이상 징후를 계산으로 확인해 경영진이 알아야 할 것으로 정리합니다",
        ("데이터", "독자", "알고 싶은 것"),
        "데이터를 도구로 계산한다: 합계·추세·전기 대비·이상치. 경영진이 알아야 할 것을 "
        "결론부터 셋 안팎으로 말하고, 각각에 근거 수치와 계산을 붙인다. 이상 징후는 "
        "무엇이 정상 범위에서 얼마나 벗어났는지로 말하고, 원인은 데이터가 보이는 만큼만 "
        "추정하며 추정이라고 표시한다. 필요하면 차트를 하나 만든다.",
        ("표 붙여 넣기·CSV 첨부", "예: 경영진", "예: 핵심 변화, 이상 징후"),
        skills=("calculation-unit-check", "exec-language"),
    ),
    _t(
        "t_security_review",
        C,
        "업무",
        "보안 검토",
        "시스템의 보안 위험을 데이터·모델·인프라·사용자 관점으로 분석하고 대책을 붙입니다",
        ("대상 시스템", "관점", "환경"),
        "보안 위험을 관점별(데이터, 모델, 인프라, 사용자)로 찾는다. 위험마다 공격 경로, "
        "영향, 대책, 대책의 확인 방법을 표로 정리하고, 지금 당장 막아야 할 것과 설계에 "
        "반영할 것을 가른다. 표준·규정(개인정보 보호, ISMS)은 확인한 것만 이름을 대고, "
        "환경에 없는 구성 요소를 가정하지 않는다.",
        ("예: 사내 LLM 서비스", "예: 데이터·모델·인프라·사용자", "예: 온프레미스, 사내 문서 연동"),
        skills=("security-lens", "risk-lens"),
    ),
    # ── 업무 × 보고서: 보고서·기획서 ───────────────────────────────────
    _t(
        "t_weekly_report",
        R,
        "업무",
        "주간 업무보고",
        "진행 상황을 진행사항·성과·이슈·다음 주 계획으로 정리한 주간 보고서를 씁니다",
        ("프로젝트", "이번 주 진행", "이슈", "다음 주 계획"),
        "주간 보고를 진행사항 → 성과 → 이슈(영향과 필요한 결정) → 다음 주 계획으로 쓴다. "
        "제공된 내용만 쓰고 진척률·수치는 적힌 것만, 없는 항목은 「(미정)」으로 둔다. "
        "이슈는 무엇이 막혀 있고 누가 무엇을 결정해야 풀리는지로 적는다. 항목은 짧은 "
        "문장으로, 읽는 사람이 1분 안에 상황을 알 수 있게.",
        (
            "예: 사내 문서검색 시스템 구축",
            "예: 검색 API 완료, UI 60%",
            "예: p95 2.8초, 권한 협의 지연",
            "예: 성능 튜닝, 인사팀 협의",
        ),
        skills=("source-faithful",),
        render="doc-brief",
    ),
    _t(
        "t_minutes",
        R,
        "업무",
        "회의록",
        "녹취·메모를 논의사항·결정사항·Action Item·담당자로 갈라 정식 회의록으로 만듭니다",
        ("녹취·메모", "회의 정보", "구분"),
        "녹취나 메모를 회의 개요 → 논의사항 → 결정사항 → Action Item(담당자·기한) → "
        "미결 사항으로 정리한다. 녹취에 있는 것만 쓰고, 담당자나 기한이 나오지 않았으면 "
        "「미정」이라고 적는다. Action Item은 표로 세우고, 발언은 요약하되 결정의 근거가 "
        "된 반론은 남긴다.",
        (
            "녹취·메모 첨부·붙여 넣기",
            "예: 9/1 교육과정위원회, 참석 4명",
            "예: 논의·결정·Action Item·담당",
        ),
        needs=("file",),
        skills=("minutes",),
        render="doc-minutes",
    ),
    _t(
        "t_plan_doc",
        R,
        "업무",
        "기획서",
        "배경·목표·대상·기능·구축 방안·기대효과를 갖춘 도입 기획서를 씁니다",
        ("기획 대상", "배경", "대상 사용자", "주요 기능", "제약"),
        "기획서를 배경 → 목표 → 대상 사용자 → 주요 기능 → 구축 방안(단계·일정) → "
        "기대효과 → 위험과 대응의 순서로 쓴다. 기능은 사용자가 겪는 일이 어떻게 달라지는지로 "
        "적고, 기대효과의 수치는 제공된 것만 쓰며 없으면 측정 방법을 대신 적는다. 일정과 "
        "비용은 요청에 없으면 「(미정)」으로 둔다.",
        (
            "예: 사내 생성형 AI 서비스 도입",
            "예: 문서 질의가 헬프데스크로 몰림",
            "예: 전 직원 800명",
            "예: 규정 질의응답, 문서 요약",
            "예: 온프레미스, 6개월",
        ),
        skills=("decision-memo",),
        render="doc-proposal",
    ),
    _t(
        "t_feasibility",
        R,
        "업무",
        "기술 검토 보고서",
        "기술 도입의 타당성을 현황·요구·대안·비용·위험으로 검토합니다",
        ("검토 대상", "현황", "요구사항", "제약"),
        "검토 보고서를 요약 → 현황과 요구사항 → 검토 대상의 개요 → 대안 비교 → 비용과 "
        "일정 → 위험 → 권고의 순서로 쓴다. 대안 비교는 표 하나로, 기준은 요구사항에서 "
        "나온다. 비용·수치는 제공된 것과 검색으로 확인한 것만 쓰고 출처를 달며, 없으면 "
        "「(미정)」. 권고는 조건부로 말하고 다음 단계를 담당·기한과 함께 적는다.",
        (
            "예: Kubernetes 플랫폼 도입",
            "예: VM 40대, 배포 수동",
            "예: 무중단 배포, 자동 확장",
            "예: 운영 인력 3명",
        ),
        needs=("web",),
        skills=("decision-memo", "evidence"),
        render="doc-report",
    ),
    _t(
        "t_decision_report",
        R,
        "업무",
        "의사결정 비교 보고서",
        "두세 선택지를 비용·보안·성능·운영 기준으로 견주고 권고하는 보고서를 씁니다",
        ("선택지", "비교 기준", "근거 수치", "독자"),
        "요약 → 현황과 결정할 사안 → 비교 기준과 전제 → 대안 비교(표 하나) → 위험 → "
        "권고와 다음 단계의 순서로 쓴다. 수치는 요청과 확인된 자료의 것만 쓰고 계산은 식과 "
        "함께 보이며, 없는 값은 「(미정)」. 표 아래에서 표가 말하는 결론을 두 문장으로, "
        "권고는 근거를 앞세워 한 가지를 고른다.",
        (
            "예: 자체 구축 LLM vs 상용 LLM API",
            "예: 비용, 보안, 성능, 운영",
            "예: 월 토큰 5천만, 구축 3억",
            "예: CTO",
        ),
        skills=("decision-memo", "calculation-unit-check"),
        render="doc-report",
    ),
    _t(
        "t_exec_summary",
        R,
        "업무",
        "임원 요약",
        "긴 보고서를 임원이 3분 안에 읽는 한 장 Executive Summary로 줄입니다",
        ("첨부 보고서", "독자", "결정할 것"),
        "첨부한 보고서를 한 장으로 줄인다: 결론(권고) → 근거 셋 → 수치 셋 → 위험 → 요청 "
        "사항. 보고서에 있는 내용만 쓰고 수치는 그대로 옮기며 어느 절에서 왔는지 표시한다. "
        "기술 세부는 빼고 사업 영향(비용, 일정, 위험)으로 바꿔 말한다. 임원이 답해야 할 "
        "질문을 마지막에 둔다.",
        ("보고서 첨부", "예: 경영진", "예: 예산 승인"),
        needs=("file",),
        skills=("brief-one-page", "exec-language"),
        render="doc-brief",
    ),
    _t(
        "t_tech_proposal",
        R,
        "업무",
        "기술 제안서",
        "고객 과제에서 시작해 제안 아키텍처·전환 계획·기대효과·견적으로 맺는 제안서를 씁니다",
        ("고객·과제", "제안", "전환 계획", "기대효과", "비용"),
        "제안서를 고객 현황과 과제 → 제안 개요 → 제안 아키텍처 → 전환 계획(단계·일정) → "
        "기대효과 → 비용 → 위험과 대응 → 다음 단계의 순서로 쓴다. 고객의 수치와 비용은 "
        "제공된 것만 쓰고, 기대효과는 근거를 붙이거나 「(측정 예정)」으로 둔다. 기술 "
        "용어는 고객이 아는 말로 풀고, 아키텍처는 구성 요소와 흐름으로 설명한다.",
        (
            "예: 중견 제조사, VM 기반 서비스 30개",
            "예: Kubernetes 전환",
            "예: 진단 → 설계 → 이전 → 검증 → 운영",
            "예: 배포 시간 2일 → 1시간",
            "예: 구축 2억, 연 유지 4천만 원",
        ),
        skills=("decision-memo", "evidence"),
        render="doc-proposal",
    ),
    _t(
        "t_report_incident",
        R,
        "업무",
        "장애 보고서",
        "타임라인을 바탕으로 영향·시각열·근본 원인·조치·재발 방지를 갖춘 장애 보고서를 씁니다",
        ("장애 타임라인", "영향 범위", "근본 원인", "조치"),
        "장애 보고서를 요약 → 영향(누가, 얼마나, 얼마 동안) → 시각열 → 근본 원인 → 대응과 "
        "복구 → 재발 방지 조치(담당·기한)로 쓴다. 타임라인에 있는 사실만 쓰고 시각은 "
        "그대로 옮기며, 원인이 확인되지 않았으면 「추정」이라고 적는다. 재발 방지는 원인과 "
        "짝지어 표로 세운다. 책임 추궁의 말투를 쓰지 않는다.",
        (
            "타임라인 붙여 넣기·첨부",
            "예: 결제 API, 47분, 약 1,200건 실패",
            "예: 배포 후 커넥션 풀 고갈",
            "예: 롤백, 풀 크기 조정",
        ),
        skills=("incident-timeline",),
        render="doc-incident",
    ),
    _t(
        "t_policy_doc",
        R,
        "업무",
        "정책·가이드라인",
        "사내 서비스 사용 가이드라인을 항목별 원칙·허용·금지·확인 절차로 씁니다",
        ("대상 서비스", "다뤄야 할 항목", "적용 대상", "기존 규정"),
        "가이드라인을 목적과 적용 범위 → 항목별 원칙(허용되는 것, 금지되는 것, 예외와 승인 "
        "절차) → 위반 시 처리 → 문의처의 순서로 쓴다. 항목은 요청이 준 것(기밀정보, "
        "개인정보, 결과 검증, 저작권)을 절로 삼고, 각 항목에 구체적인 예시 하나씩을 든다. "
        "법령·표준은 확인한 것만 이름을 대고, 기존 규정과의 관계를 밝힌다.",
        (
            "예: 사내 생성형 AI 서비스",
            "예: 기밀정보, 개인정보, 생성 결과 검증, 저작권",
            "예: 전 임직원",
            "예: 정보보호 규정 제12조",
        ),
        needs=("web",),
        skills=("policy-frame",),
        render="doc-notice",
    ),
    _t(
        "t_decision_record",
        R,
        "업무",
        "의사결정 기록",
        "회의 메모와 이메일을 배경·대안·결정·근거·후속조치의 결정 문서로 통합합니다",
        ("메모·이메일", "결정 사안", "관련자"),
        "흩어진 메모와 이메일을 배경 → 논의된 대안 → 결정사항 → 근거 → 후속조치(담당·기한)로 "
        "통합한다. 자료에 있는 것만 쓰고 출처(어느 메일, 어느 회의)를 항목마다 표시하며, "
        "자료끼리 어긋나는 곳은 어긋난다고 적는다. 결정되지 않은 것은 미결로 따로 두고, 후속조치는 "
        "표로 세운다.",
        ("메모·메일 붙여 넣기·첨부", "예: 검색 시스템 오픈 일정 조정", "예: 개발팀장, 인사팀, PM"),
        needs=("file",),
        skills=("source-faithful", "decision-memo"),
        render="doc-brief",
    ),
    # ── 업무 × 슬라이드: 보고·제안 발표 ─────────────────────────────
    _t(
        "t_slides_status",
        S,
        "업무",
        "경영진 현황 보고",
        "프로젝트 진행 상황을 성과·이슈·의사결정 필요사항 중심의 10장 이내로 만듭니다",
        ("프로젝트", "진행 상황", "이슈", "결정 필요사항"),
        "현황 보고를 한 줄 요약 → 일정 대비 진척 → 성과 → 이슈(영향·필요한 결정) → "
        "요청 사항 → 다음 단계로 만든다. 수치는 제공된 것만, 계획 대비는 표로, 결정이 "
        "필요한 것은 마지막 장에 질문 형태로. 장수는 10장 이내, 장마다 발표 노트를 붙이고 "
        "이슈 장은 영향과 필요한 결정을 표로 짝짓는다.",
        (
            "예: 사내 문서검색 시스템",
            "예: 진척 72%(계획 80%)",
            "예: p95 2.8초, 권한 협의 지연",
            "예: 오픈 2주 연기 승인",
        ),
        skills=("exec-language", "speaker-notes"),
        render="deck-briefing",
    ),
    _t(
        "t_slides_exec",
        S,
        "업무",
        "임원 보고 요약",
        "첨부한 기술 보고서를 사업 영향과 결정 사항 중심의 짧은 임원 보고로 바꿉니다",
        ("첨부 보고서", "장수", "결정할 것"),
        "첨부한 보고서를 결론 → 사업 영향(비용·일정·위험) → 근거 셋 → 선택지와 권고 → "
        "요청 사항의 짧은 발표로 줄인다. 기술 세부는 빼고 임원의 결정에 필요한 것만 남기며, "
        "수치는 보고서의 것을 그대로 옮기고 어느 절에서 왔는지 노트에 적는다. 장마다 "
        "메시지 한 줄, 장수는 요청을 따른다.",
        ("보고서 첨부", "예: 7장", "예: 플랫폼 전환 승인"),
        needs=("file",),
        skills=("exec-language", "source-faithful"),
        render="deck-briefing",
    ),
    _t(
        "t_slides_customer",
        S,
        "업무",
        "고객 제안 발표",
        "현황·문제·제안 아키텍처·전환 계획·기대효과를 고객의 말로 보이는 제안 발표",
        ("고객·과제", "제안", "일정·비용", "레퍼런스"),
        "제안 발표를 고객 현황과 문제 → 제안 개요 → 아키텍처 → 전환 계획 → 기대효과 → "
        "일정·비용 → 레퍼런스 → 다음 단계(PoC)로 만든다. 고객의 수치와 비용은 제공된 "
        "것만 쓰고, 기대효과에 근거가 없으면 「측정 예정」으로. 기술 용어는 고객이 아는 "
        "말로, 아키텍처는 띠와 흐름으로. 장마다 발표 노트를 붙인다.",
        (
            "예: 제조사, VM 기반 서비스",
            "예: Kubernetes 전환",
            "예: 4개월, 구축 1.2억",
            "예: 동종 업계 2개사",
        ),
        skills=("deck-story", "speaker-notes"),
        render="deck-proposal",
    ),
    _t(
        "t_slides_market",
        S,
        "업무",
        "시장 분석 발표",
        "시장 규모·주요 기업·기술 동향·기회와 위험을 검색으로 확인해 보입니다",
        ("시장", "분석 축", "우리 관점"),
        "시장 분석을 시장 규모(출처 포함) → 주요 기업(표) → 기술 동향 → 기회 → 위험 → "
        "시사점으로 만든다. 수치와 기업 정보는 검색으로 확인한 것만 쓰고 장마다 출처를 "
        "노트에 적으며, 확인하지 못한 것은 「확인 필요」. 차트는 확인된 값으로만 그리고, "
        "시사점 장은 우리 관점에서 무엇을 할지로 맺는다.",
        ("예: 생성형 AI 플랫폼", "예: 규모, 기업, 기술, 기회·위험", "예: 사내 플랫폼 도입 검토"),
        needs=("web",),
        skills=("evidence", "citation"),
    ),
    _t(
        "t_slides_options",
        S,
        "업무",
        "선택지 비교 발표",
        "선택지를 비용·보안·성능·운영 기준의 비교표로 견주고 권고하는 결정 발표",
        ("선택지", "기준", "근거 수치", "권고"),
        "결정 발표를 결정할 것 → 기준 → 비교표 한 장 → 기준별 상세 → 위험 → 권고와 "
        "요청으로 만든다. 표의 칸은 제공된 수치와 확인된 사실만, 없는 칸은 「?」. 권고는 "
        "조건과 근거를 붙여 한 장에, 마지막에 결정에 필요한 질문을 둔다. 장마다 발표 "
        "노트를 붙이고, 기준별 상세 장은 표의 한 행을 풀어 쓴다.",
        (
            "예: 자체 구축 LLM vs 상용 API",
            "예: 비용, 보안, 성능, 운영",
            "예: 구축 3억, API 월 2천만 원",
            "예: 조건부 API",
        ),
        skills=("comparison-table", "exec-language"),
        render="deck-case",
    ),
    _t(
        "t_slides_kickoff",
        S,
        "업무",
        "프로젝트 킥오프",
        "목표·범위·아키텍처·일정·역할·위험을 갖춘 킥오프 발표를 만듭니다",
        ("프로젝트", "목표·범위", "일정·역할", "위험"),
        "킥오프를 배경 → 목표 → 범위(하는 것·안 하는 것) → 아키텍처 개요 → 일정 → 역할 → "
        "위험과 대응 → 첫 2주 할 일로 만든다. 일정·역할·수치는 제공된 것만 쓰고 없으면 "
        "「(미정)」으로 둔다. 범위 장은 두 열로, 역할은 표로, 위험은 완화책과 짝지어, "
        "장마다 발표 노트를 붙인다.",
        (
            "예: 사내 LLM 서비스 구축",
            "예: 규정 질의응답, 1차 인사·총무",
            "예: 4개월, PM 1·개발 3",
            "예: 데이터 접근 권한",
        ),
        skills=("deck-story", "speaker-notes"),
        render="deck-briefing",
    ),
    _t(
        "t_slides_results",
        S,
        "업무",
        "성과 보고",
        "분기 성과를 절감액·주요 조치·남은 과제·다음 목표로 경영진에게 보고합니다",
        ("성과 주제", "수치", "주요 조치", "다음 목표"),
        "성과 보고를 한 줄 결과 → 수치(전후 비교) → 무엇을 해서 그렇게 됐는지 → 남은 과제 → "
        "다음 분기 목표 → 필요한 지원으로 만든다. 수치는 제공된 것만 쓰고 계산은 식과 "
        "함께, 차트는 그 값으로만 그린다. 조치와 결과를 짝지어 표로 보이고, 장마다 발표 "
        "노트를 붙인다.",
        (
            "예: 클라우드 비용 최적화",
            "예: 월 4,200만 → 3,100만 원",
            "예: 예약 인스턴스, 미사용 볼륨 정리",
            "예: 추가 15% 절감",
        ),
        skills=("evidence", "exec-language"),
        render="deck-briefing",
    ),
    _t(
        "t_slides_incident",
        S,
        "업무",
        "장애 리뷰 발표",
        "장애의 영향·원인·대응·재발 방지를 명확하게 보이는 리뷰 발표를 만듭니다",
        ("장애 타임라인", "영향", "원인", "재발 방지"),
        "장애 리뷰를 요약 → 영향 → 시각열 → 근본 원인 → 대응 → 재발 방지(담당·기한) → "
        "배운 것으로 만든다. 타임라인의 사실과 시각만 쓰고, 원인이 확인되지 않았으면 "
        "「추정」. 시각열은 연혁 장으로, 재발 방지는 원인과 짝지은 표로. 책임 추궁의 "
        "말투를 쓰지 않는다.",
        ("타임라인 붙여 넣기", "예: 결제 47분 중단", "예: 커넥션 풀 고갈", "예: 배포 전 부하 시험"),
        skills=("incident-timeline",),
    ),
    _t(
        "t_slides_training",
        S,
        "업무",
        "교육 자료",
        "임직원 교육용 슬라이드를 위험 사례와 준수사항 중심으로 정한 시간에 맞춰 만듭니다",
        ("교육 주제", "대상", "시간", "꼭 다룰 것"),
        "교육 자료를 왜 중요한가(사례 하나) → 위험이 생기는 방식 → 준수사항(해야 할 것·"
        "하지 말 것) → 사례별 판단 연습 → 정리와 문의처로 만든다. 사례는 확인된 것만 "
        "쓰거나 가상 사례라고 표시한다. 시간에 맞춰 장수를 정하고, 퀴즈 장은 문제를 "
        "항목으로 적으며, 장마다 강의 노트를 붙인다.",
        ("예: 생성형 AI 보안 수칙", "예: 전 임직원", "예: 15분", "예: 기밀 입력 금지, 결과 검증"),
        skills=("speaker-notes", "quiz-writer"),
        render="deck-lecture",
    ),
    _t(
        "t_slides_redesign",
        S,
        "업무",
        "발표 재구성",
        "첨부한 발표의 내용을 지키며 장마다 핵심 메시지가 보이도록 다시 구성합니다",
        ("첨부 발표자료", "스타일", "독자"),
        "첨부한 발표의 내용을 바꾸지 않고 구조만 다시 짠다. 장마다 핵심 메시지 한 문장을 "
        "제목으로 세우고, 근거는 표·띠·항목 가운데 내용에 맞는 모양으로 바꾼다. 같은 말을 "
        "하는 장은 합치고, 빠진 논리 연결은 발표 노트에 적는다. 새 사실을 보태지 않으며 "
        "마지막에 무엇을 바꿨는지 표로 알린다.",
        ("발표자료 첨부", "예: 컨설팅 보고 스타일", "예: 경영진"),
        needs=("file",),
        skills=("source-faithful", "deck-story"),
        render="deck-editorial",
    ),
)


#: Indexed once, by id — the process reads this at import and nothing edits it
#: cannot have changed since the process started.
_TEMPLATES: dict[str, PromptTemplate] = {t.id: t for t in _ALL}


def all_templates() -> list[PromptTemplate]:
    return list(_TEMPLATES.values())


def get(template_id: str | None) -> PromptTemplate | None:
    return _TEMPLATES.get(template_id or "")


def skill_names(keys: tuple[str, ...]) -> list[str]:
    """The catalogue names behind a card's skill keys, in order; unknown keys dropped."""
    from app.services.starter import skill_name

    return [name for key in keys if (name := skill_name(key))]


__all__ = ["PromptTemplate", "all_templates", "get", "skill_names"]
