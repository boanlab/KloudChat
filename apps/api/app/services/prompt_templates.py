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
rendering catalogue is read out of the image: these are twenty-four sentences
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
    #: The gallery's filter chip: 학업, 업무, 연구, 영업, 개발.
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
    #: The English half. Empty until somebody writes it; the client falls back
    #: to the Korean, which leaves a card readable rather than blank.
    title_en: str = ""
    description_en: str = ""
    fills_en: tuple[str, ...] = ()
    prompt_en: str = ""


_ALL: tuple[PromptTemplate, ...] = (
    # 보고서와 발표의 시작점은 서식이 대신한다.
    #
    # 둘은 같은 일을 두 번 말하고 있었다 — 설문 분석, 장애 보고, 제안서,
    # 케이스 분석은 이름까지 같아서 한 격자에 두 번 섰다. 서식 쪽에는 지시와
    # 확인 항목과 채워 쓸 양식 파일이 붙어 있고 시작점 쪽에는 문장뿐이므로,
    # 남길 것은 서식이다.
    #
    # 챗의 시작점은 남긴다. 챗에는 서식이 하나도 없어서 이것이 그 화면이
    # 내놓는 전부다. 없애면 대신 서는 것 없이 비는 자리가 된다.
    # ── chat ───────────────────────────────────────────────────────────
    PromptTemplate(
        id="t_translate",
        kind=SessionKind.chat,
        group="학업",
        title="원문 읽기",
        description="번역과 함께 전공 용어를 정리해 준다",
        fills=("원문",),
        prompt="이 원문을 읽어야 한다. 전공 용어는 원어를 함께 적어 줘.\n\n원문: ",
    ),
    PromptTemplate(
        id="t_debug",
        kind=SessionKind.chat,
        group="개발",
        title="장애 원인 좁히기",
        description="스택 트레이스에서 가설과 확인 방법까지",
        fills=("에러 로그", "재현 조건"),
        prompt="이 에러의 원인을 좁혀야 한다. 확신이 없는 것은 확인할 방법을 알려 줘.\n\n에러: ",
    ),
    PromptTemplate(
        id="t_schema",
        kind=SessionKind.chat,
        group="개발",
        title="쿼리 짜기",
        description="스키마를 보고 원하는 집계를 뽑는 SQL",
        fills=("테이블 구조", "뽑고 싶은 것"),
        prompt="이 스키마에서 쿼리를 짜야 한다.\n\n스키마와 뽑고 싶은 것: ",
    ),
    PromptTemplate(
        id="t_email",
        kind=SessionKind.chat,
        group="업무",
        title="메일 초안",
        description="용건이 첫 문장에 오는 짧은 메일",
        fills=("용건", "받는 사람"),
        prompt="메일을 써야 한다. 길게 쓰지 말아 줘.\n\n용건과 받는 사람: ",
    ),
    PromptTemplate(
        id="t_meeting_prep",
        kind=SessionKind.chat,
        group="영업",
        title="미팅 준비",
        description="상대가 물어볼 것과 이쪽이 확인할 것",
        fills=("고객사", "지난 접촉"),
        prompt="이 고객사와 미팅을 준비해야 한다.\n\n고객사와 지금까지의 경위: ",
    ),
    PromptTemplate(
        id="t_compare",
        kind=SessionKind.chat,
        group="업무",
        title="자료 대조",
        description="두 문서의 차이를 항목별로 짚는다",
        fills=("문서 둘",),
        prompt=(
            "두 자료를 대조해야 한다. 달라진 항목만 짚어 주고, 같은 것은 넘어가 줘.\n\n"
            "무엇과 무엇: "
        ),
    ),
    # ── image ──────────────────────────────────────────────────────────
    PromptTemplate(
        id="t_cover",
        kind=SessionKind.image,
        group="학업",
        title="표지 그림",
        description="글자 없이 주제만 암시하는 표지용 이미지",
        fills=("주제",),
        prompt="표지에 쓸 그림. 글자는 넣지 말고, 주제를 암시하는 정도로. 주제: ",
    ),
    PromptTemplate(
        id="t_slidebg",
        kind=SessionKind.image,
        group="업무",
        title="슬라이드 배경",
        description="가운데를 비워 둔 저채도 배경",
        fills=("분위기",),
        prompt="발표 슬라이드 배경. 글자가 얹힐 가운데는 비우고 채도는 낮게. 분위기: ",
    ),
    PromptTemplate(
        id="t_diagram",
        kind=SessionKind.image,
        group="개발",
        title="개념도",
        description="구조를 보여 주는 선 위주의 그림",
        fills=("구조",),
        prompt="구조를 설명하는 개념도. 선 위주로 단순하게, 라벨 자리는 비워서. 구조: ",
    ),
    # ── audio / video ──────────────────────────────────────────────────
    PromptTemplate(
        id="t_narration",
        kind=SessionKind.av,
        group="업무",
        title="발표 내레이션",
        description="슬라이드에 얹을 음성 해설",
        fills=("읽을 내용",),
        prompt="발표에 얹을 내레이션. 또박또박, 문어체 말고 말하듯이.\n\n내용: ",
    ),
    PromptTemplate(
        id="t_intro",
        kind=SessionKind.av,
        group="영업",
        title="오프닝 클립",
        description="발표 시작에 트는 짧은 영상",
        fills=("분위기",),
        prompt="발표 오프닝에 쓸 짧은 영상. 어두운 화면에서 서서히 밝아지는 정도로. 분위기: ",
    ),
    PromptTemplate(
        id="t_product",
        kind=SessionKind.av,
        group="영업",
        title="제품 소개 클립",
        description="미팅에서 트는 짧은 사용 장면",
        fills=("제품", "쓰는 장면"),
        prompt="제품 소개 클립. 자막이 들어갈 아래쪽은 비워서. 제품과 장면: ",
    ),
    PromptTemplate(
        id="t_bgm",
        kind=SessionKind.av,
        group="업무",
        title="배경 음악",
        description="말소리를 가리지 않는 짧은 루프",
        fills=("분위기",),
        prompt="영상 뒤에 깔 배경 음악. 내레이션을 가리지 않게 잔잔하게. 분위기: ",
    ),
)

#: By id, in catalogue order. Built once, because the catalogue is a constant
#: of this image: rebuilding it per request would be work for a dict that
#: cannot have changed since the process started.
_TEMPLATES: dict[str, PromptTemplate] = {t.id: t for t in _ALL}


def all_templates() -> list[PromptTemplate]:
    return list(_TEMPLATES.values())


def get(template_id: str | None) -> PromptTemplate | None:
    return _TEMPLATES.get(template_id or "")


__all__ = ["PromptTemplate", "all_templates", "get"]
