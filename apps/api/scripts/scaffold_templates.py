"""Writes the 서식 folders that carry a persona's document, and nothing else.

Run from the API image:

    docker compose run --rm --no-deps -v "$PWD/apps/api:/repo" -w /repo api \
        python scripts/scaffold_templates.py

The catalogue grew out of shapes — 보고 문서, 한 장 요약, 회의록 — and shapes
are what a designer sees. What a person coming to this has is a job: a 기말
리포트 with Chicago citations, a 설문 분석 with cross-tabs, a 장애 보고 with a
timeline. Those are the same three or four typesettings wearing different
structures and different rules, and the structure and the rules are the whole
of the difference.

So the folders here are generated from one table. Each 서식 declares its own
`template.toml`, its own instructions and its own checklist — the parts that
differ — and shares a `seed.html` with the shape it is typeset in. The seeds
read every colour and face from the design tokens (51 `var(--…)` and one
`#ffffff`), so sharing one is sharing a typesetting rather than a palette.

The Word and PowerPoint halves are `build_docx_templates.py` and
`build_pptx_templates.py`, which read the folders this leaves behind.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "app" / "design_templates"


class Sheet:
    """One 서식, in the terms `template.toml` states one."""

    def __init__(
        self,
        folder: str,
        *,
        base: str,
        name: str,
        name_en: str,
        description: str,
        description_en: str,
        category: str,
        category_en: str,
        fills: tuple[str, ...],
        fills_en: tuple[str, ...],
        example_prompt: str,
        example_prompt_en: str,
        who: str,
        instructions: str,
        checks: tuple[str, ...],
    ) -> None:
        self.folder = folder
        self.base = base
        self.name = name
        self.name_en = name_en
        self.description = description
        self.description_en = description_en
        self.category = category
        self.category_en = category_en
        self.fills = fills
        self.fills_en = fills_en
        self.example_prompt = example_prompt
        self.example_prompt_en = example_prompt_en
        self.who = who
        self.instructions = instructions
        self.checks = checks


def _layouts_of(base: str) -> str:
    """The layouts the base's seed actually draws.

    Guessed once, and wrongly: a deck scaffolded with `["title", "section"]`
    offers the outline call two shapes its seed has no typesetting for, so the
    writer asks for slides that come out blank. The seed decides what layouts
    exist, so the seed's own declaration is what travels with it.
    """
    for line in (ROOT / base / "template.toml").read_text(encoding="utf-8").splitlines():
        if line.startswith("layouts"):
            return line.split("=", 1)[1].strip()
    raise KeyError(f"{base} 에 layouts 가 없습니다")


def _toml(sheet: Sheet, kind: str, layouts: str, wrap: str) -> str:
    def quote(text: str) -> str:
        return '"' + text.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'

    def array(items: tuple[str, ...]) -> str:
        return "[" + ", ".join(quote(item) for item in items) + "]"

    return f"""kind = {quote(kind)}
name = {quote(sheet.name)}
description = {quote(sheet.description)}
category = {quote(sheet.category)}
fills = {array(sheet.fills)}
example_prompt = {quote(sheet.example_prompt)}
seed_from = {quote(sheet.base)}
layouts = {layouts}

# English half of the catalogue. The UI picks a side by language;
# the instructions the model follows stay Korean, because the surfaces
# they belong to answer in Korean by default.
name_en = {quote(sheet.name_en)}
description_en = {quote(sheet.description_en)}
category_en = {quote(sheet.category_en)}
fills_en = {array(sheet.fills_en)}
example_prompt_en = {quote(sheet.example_prompt_en)}

{wrap}
"""


#: Copied from the shape each one is typeset in. `[wrap]` is the seed's own
#: contract with the writer, so it travels with the seed rather than being
#: restated here.
def _wrap_of(base: str) -> str:
    text = (ROOT / base / "template.toml").read_text(encoding="utf-8")
    marker = "[wrap]"
    return text[text.index(marker) :].rstrip() if marker in text else ""


def write(sheet: Sheet) -> pathlib.Path:
    folder = ROOT / sheet.folder
    folder.mkdir(exist_ok=True)
    base = ROOT / sheet.base

    # The typesetting is named, not copied. Copies drift — six of the ten
    # document 서식 once carried byte-identical seeds — and nothing in a copy
    # says it was ever meant to match. `seed_from` in the toml below says it.

    kind = (base / "template.toml").read_text(encoding="utf-8").split("\n", 1)[0]
    kind = kind.split("=", 1)[1].strip().strip('"')

    (folder / "template.toml").write_text(
        _toml(sheet, kind, _layouts_of(sheet.base), _wrap_of(sheet.base)), encoding="utf-8"
    )
    (folder / "instructions.md").write_text(sheet.instructions.strip() + "\n", encoding="utf-8")
    (folder / "checklist.md").write_text(
        "\n".join(f"- {line}" for line in sheet.checks) + "\n", encoding="utf-8"
    )
    return folder


_SHEETS = (
    Sheet(
        "doc-term-paper",
        base="doc-report",
        name="기말 리포트",
        name_en="Term paper",
        description="주장을 근거로 받치고, 인용한 자리마다 출처를 각주로 다는 학기말 글",
        description_en="An argument held up by evidence, with every citation footnoted where it stands",
        category="학업",
        category_en="Study",
        fills=("주제", "분량", "인용 양식"),
        fills_en=("Topic", "Length", "Citation style"),
        example_prompt=(
            "기말 리포트를 써 줘. 읽은 자료에서 인용한 자리마다 각주로 출처를 달고, "
            "확인되지 않은 것은 쓰지 마.\n\n주제와 분량: "
        ),
        example_prompt_en=(
            "Write a term paper. Footnote the source at every place you cite, and leave "
            "out anything you cannot verify.\n\nTopic and length: "
        ),
        who="인문·사회 학부생. 표절 검사를 통과해야 하므로 출처가 분명해야 한다.",
        instructions="""
이 문서는 **기말 리포트** 템플릿으로 만든다. 조판은 정해져 있으니 내용만 쓴다.
읽는 사람은 채점자다. 주장이 무엇인지, 그 주장을 무엇이 받치는지, 받치는 것이
어디서 왔는지 — 세 가지를 찾는다.

각 절은 `layout` 하나와 그 절의 본문 HTML 조각으로 이루어진다.

- `cover` — 표지. 제목과 부제, 그리고 과목·학번·제출일이 들어갈 자리.
- `section` — 장 하나. `<h2>` 로 장 제목을 세우고 본문을 잇는다.

## 규칙

- **서론은 질문을 세운다.** 무엇을 묻는 글인지 첫 문단에서 밝힌다. 배경만
  늘어놓고 질문이 없으면 채점자는 이 글이 무엇을 하려는지 모른다.
- **한 장은 한 주장.** 장 제목이 곧 그 장의 주장이 되게 쓴다. "배경" 같은
  제목은 그 장이 무엇을 말하는지 감추므로 피한다.
- **인용한 자리마다 각주.** 남의 말이나 숫자를 쓴 문장에는 그 자리에 각주를
  단다. 문단 끝에 몰아 달면 어느 문장이 어디서 왔는지 알 수 없다. 각주는
  `[^1]` 로 달고 절 끝에 `[^1]: 저자, 『책』, 쪽수.` 로 받는다.
- **확인하지 못한 것은 쓰지 않는다.** 기억으로 쓴 연도·인명·쪽수는 틀린다.
  자료에서 확인한 것만 쓰고, 확인하지 못했으면 그 문장을 뺀다.
- **결론은 요약이 아니다.** 서론에서 세운 질문에 답하고, 이 글이 다루지 못한
  것을 한 줄로 밝힌다.
- 참고문헌은 마지막 절에 모은다. 각주에 단 것과 목록이 어긋나면 안 된다.
- 명령어·예외 이름·파일 경로처럼 **글자 그대로 읽어야 하는 것**은 `<code>` 로
  감싼다. 조판이 그 자리를 고정폭으로 세워 주므로, 따옴표로 대신하면 무엇이
  코드이고 무엇이 인용인지 구별되지 않는다.
"""
,
        checks=(
            "서론에 이 글이 답하려는 질문이 문장으로 서 있는가.",
            "장 제목이 그 장의 주장을 말하는가. '배경'·'본론' 같은 제목은 아니다.",
            "인용한 문장마다 그 자리에 각주가 붙어 있는가. 문단 끝에 몰려 있으면 아니다.",
            "각주에 단 출처가 참고문헌 목록과 어긋나지 않는가.",
            "연도·인명·쪽수가 자료에서 확인된 것인가. 확인 못 한 것이 남아 있으면 아니다.",
            "결론이 서론의 질문에 답하는가. 앞을 요약만 하면 아니다.",
            "다루지 못한 것을 밝혔는가.",
        ),
    ),
    Sheet(
        "doc-case",
        base="doc-report",
        name="케이스 분석",
        name_en="Case study",
        description="기업 사례를 현황·대안·권고로 갈라, 숫자로 견주고 하나를 고르는 문서",
        description_en="A company case split into situation, options and recommendation, compared on numbers",
        category="학업",
        category_en="Study",
        fills=("대상 기업", "분석 기간", "판단 기준"),
        fills_en=("Company", "Period", "Criteria"),
        example_prompt=(
            "케이스 분석 보고서를 써 줘. 대안을 같은 기준으로 견주고, 어느 것을 "
            "왜 고르는지 마지막에 밝혀 줘.\n\n대상 기업과 기간: "
        ),
        example_prompt_en=(
            "Write a case study. Compare the options on the same criteria and say which "
            "one you pick and why.\n\nCompany and period: "
        ),
        who="경영대 학부생. 팀 프로젝트로 케이스를 분석하고 15분 발표로 잇는다.",
        instructions="""
이 문서는 **케이스 분석** 템플릿으로 만든다. 조판은 정해져 있으니 내용만 쓴다.
읽는 사람은 "그래서 무엇을 하라는 것인가"를 찾는다. 분석만 있고 권고가 없으면
이 서식은 실패다.

각 절은 `layout` 하나와 그 절의 본문 HTML 조각으로 이루어진다.

- `cover` — 표지. 대상 기업과 분석 기간을 밝힌다.
- `section` — 장 하나.

## 규칙

- **현황은 숫자로 쓴다.** "매출이 부진하다" 가 아니라 "매출이 3년 연속 줄어
  2023년 대비 18% 낮다". 숫자가 없으면 그 문장은 의견이다.
- **대안은 셋 안팎, 같은 기준으로.** 표를 쓰되 열은 대안, 행은 기준으로 놓아
  같은 줄에서 견줄 수 있게 한다. 기준이 대안마다 다르면 비교가 아니다.
- **하나를 고른다.** 권고 절은 어느 대안을, 어떤 기준에서, 무엇을 포기하고
  고르는지 밝힌다. "상황에 따라 다르다" 로 끝나면 아무것도 말하지 않은 것이다.
- **위험을 적는다.** 고른 안이 틀렸을 때 무엇이 일어나는지, 무엇을 보면 틀린
  줄 알 수 있는지 한 줄씩.
- 재무 수치는 출처와 기준 시점을 밝힌다. 단위(원·달러·백만)를 표 머리에 적어
  본문에서 반복하지 않는다.
- 명령어·예외 이름·파일 경로처럼 **글자 그대로 읽어야 하는 것**은 `<code>` 로
  감싼다. 조판이 그 자리를 고정폭으로 세워 주므로, 따옴표로 대신하면 무엇이
  코드이고 무엇이 인용인지 구별되지 않는다.
"""
,
        checks=(
            "현황이 숫자로 쓰여 있는가. 형용사만 있으면 아니다.",
            "대안이 셋 안팎이고 같은 기준으로 견주어져 있는가.",
            "표의 열이 대안, 행이 기준인가. 뒤바뀌면 같은 줄에서 비교되지 않는다.",
            "권고가 하나를 고르는가. '상황에 따라' 로 끝나면 아니다.",
            "고른 안을 위해 무엇을 포기하는지 적혀 있는가.",
            "틀렸을 때 무엇이 일어나는지, 무엇을 보면 아는지 적혀 있는가.",
            "재무 수치에 출처와 기준 시점, 단위가 붙어 있는가.",
        ),
    ),
    Sheet(
        "doc-survey",
        base="doc-report",
        name="설문 분석",
        name_en="Survey analysis",
        description="표본과 문항을 밝히고, 기술통계에서 교차분석까지 표로 잇는 보고서",
        description_en="Sample and instrument stated, then descriptives through cross-tabs, in tables",
        category="연구",
        category_en="Research",
        fills=("조사 대상", "표본 수", "알고 싶은 것"),
        fills_en=("Respondents", "Sample size", "Question"),
        example_prompt=(
            "설문 분석 보고서를 써 줘. 표본과 문항을 먼저 밝히고, 기술통계와 "
            "교차분석을 표로 정리해 줘.\n\n조사 대상과 표본 수: "
        ),
        example_prompt_en=(
            "Write a survey analysis. State the sample and the instrument first, then "
            "give descriptives and cross-tabs as tables.\n\nRespondents and sample size: "
        ),
        who="사회과학 학부생·대학원생. 설문 데이터를 정리해 보고서에 넣는다.",
        instructions="""
이 문서는 **설문 분석** 템플릿으로 만든다. 조판은 정해져 있으니 내용만 쓴다.
읽는 사람은 "이 숫자를 믿어도 되는가" 를 먼저 묻는다. 표본을 밝히지 않은
분석은 읽히지 않는다.

각 절은 `layout` 하나와 그 절의 본문 HTML 조각으로 이루어진다.

- `cover` — 표지. 조사 대상과 기간, 표본 수를 밝힌다.
- `section` — 장 하나.

## 규칙

- **조사 개요를 먼저 쓴다.** 누구에게, 언제, 어떻게 물었는지. 배포 수와 회수
  수, 회수율을 적는다. 회수율이 낮으면 낮다고 쓴다.
- **표본을 표로 보인다.** 성별·연령·소속처럼 나눈 분포를 표로 놓는다. 표본이
  한쪽으로 치우쳤으면 그 사실을 본문에 적는다.
- **기술통계와 해석을 섞지 않는다.** 표는 숫자만, 해석은 표 아래 문단에.
- **교차분석은 무엇과 무엇을 걸었는지 밝힌다.** 표 제목에 두 변수를 적고,
  유의성을 봤다면 검정 이름과 값을 함께 쓴다. 보지 않았으면 "차이가 있다" 대신
  "차이가 보인다" 라고 쓴다.
- **상관을 인과로 쓰지 않는다.** 설문은 관계를 보여 주지 원인을 밝히지 않는다.
- 한계 절을 반드시 둔다. 표본의 치우침, 자기보고의 한계, 묻지 못한 것.
- 명령어·예외 이름·파일 경로처럼 **글자 그대로 읽어야 하는 것**은 `<code>` 로
  감싼다. 조판이 그 자리를 고정폭으로 세워 주므로, 따옴표로 대신하면 무엇이
  코드이고 무엇이 인용인지 구별되지 않는다.
"""
,
        checks=(
            "배포 수·회수 수·회수율이 적혀 있는가.",
            "표본 분포가 표로 있는가. 치우쳤다면 본문이 그 사실을 말하는가.",
            "표는 숫자만 담고 해석은 문단에 있는가. 표 안에 해석이 섞이면 아니다.",
            "교차분석 표 제목이 걸어 본 두 변수를 말하는가.",
            "유의성을 보지 않았는데 '차이가 있다' 고 단정하지 않았는가.",
            "상관을 인과로 바꿔 쓴 문장이 없는가.",
            "논의 절이 표가 말하는 것을 문장으로 옮기는가.",
            "한계 절에 표본의 치우침과 묻지 못한 것이 적혀 있는가.",
        ),
    ),
    Sheet(
        "doc-incident",
        base="doc-report",
        name="장애 보고",
        name_en="Incident report",
        description="무슨 일이 있었고, 왜 일어났고, 다시 안 나게 무엇을 바꾸는가",
        description_en="What happened, why, and what changes so it does not happen again",
        category="업무",
        category_en="Work",
        fills=("발생 시각", "영향 범위", "대응한 사람"),
        fills_en=("Detected at", "Blast radius", "Responders"),
        example_prompt=(
            "장애 보고서를 써 줘. 시간순 기록과 원인, 재발 방지책을 나눠서 적고, "
            "사람을 탓하지 말고 시스템을 봐 줘.\n\n발생 시각과 영향: "
        ),
        example_prompt_en=(
            "Write an incident report. Separate the timeline, the cause and the "
            "follow-ups, and look at the system rather than at people.\n\n"
            "Detected at and impact: "
        ),
        who="개발직. 장애 대응이 끝난 뒤 무엇이 있었는지 남긴다.",
        instructions="""
이 문서는 **장애 보고** 템플릿으로 만든다. 조판은 정해져 있으니 내용만 쓴다.
읽는 사람은 두 부류다 — 무슨 일이 있었는지 알아야 하는 사람과, 다시 안 나게
무엇을 바꿀지 정해야 하는 사람.

각 절은 `layout` 하나와 그 절의 본문 HTML 조각으로 이루어진다.

- `cover` — 표지. 발생·인지·복구 시각과 영향 범위를 밝힌다.
- `section` — 장 하나.

## 규칙

- **요약을 맨 앞에.** 무엇이, 얼마 동안, 누구에게 영향을 줬는지 세 문장 안에.
- **시간순 기록은 표로.** 시각·일어난 일·한 일. 추측과 사실을 섞지 않고, 그때
  몰랐던 것은 몰랐다고 적는다. 시각에는 표준시를 밝힌다.
- **원인은 시스템을 본다.** "누가 잘못 눌렀다" 는 원인이 아니라 사건이다. 왜
  그 조작이 가능했는지, 왜 막히지 않았는지, 왜 늦게 알았는지까지 내려간다.
- **사람 이름을 쓰지 않는다.** 역할로 적는다. 이름이 남는 보고서는 다음 사람이
  솔직하게 쓰지 않게 만든다.
- **재발 방지는 표로, 담당과 기한을 붙인다.** 담당 없는 항목은 하지 않기로 한
  것과 같다.
- 탐지가 늦었으면 그것도 한 항목이다. 고치는 것만큼 빨리 아는 것이 중요하다.
- 명령어·예외 이름·파일 경로처럼 **글자 그대로 읽어야 하는 것**은 `<code>` 로
  감싼다. 조판이 그 자리를 고정폭으로 세워 주므로, 따옴표로 대신하면 무엇이
  코드이고 무엇이 인용인지 구별되지 않는다.
"""
,
        checks=(
            "요약 세 문장 안에 무엇이·얼마 동안·누구에게가 있는가.",
            "시간순 기록이 표로 있고 시각에 표준시가 붙어 있는가.",
            "그때 몰랐던 것을 몰랐다고 적었는가. 나중에 안 것을 그때 안 것처럼 쓰지 않았는가.",
            "원인이 시스템까지 내려갔는가. 사람의 조작에서 멈추면 아니다.",
            "사람 이름 대신 역할로 적었는가.",
            "재발 방지 항목마다 담당과 기한이 있는가.",
            "탐지가 늦었다면 그것도 항목으로 있는가.",
        ),
    ),
    Sheet(
        "doc-proposal",
        base="doc-report",
        name="제안서",
        name_en="Proposal",
        description="고객 과제에서 시작해 도입 효과와 일정, 견적으로 맺는 문서",
        description_en="Starts from the customer's problem and ends in effect, schedule and price",
        category="업무",
        category_en="Work",
        fills=("고객사", "과제", "예산 범위"),
        fills_en=("Customer", "Problem", "Budget"),
        example_prompt=(
            "제안서를 써 줘. 고객의 과제에서 시작해서, 도입하면 무엇이 달라지는지 "
            "숫자로 보이고, 일정과 견적으로 맺어 줘.\n\n고객사와 과제: "
        ),
        example_prompt_en=(
            "Write a proposal. Start from the customer's problem, show what changes in "
            "numbers, and end with a schedule and a price.\n\nCustomer and problem: "
        ),
        who="영업직. 미팅 뒤 당일에 보낸다.",
        instructions="""
이 문서는 **제안서** 템플릿으로 만든다. 조판은 정해져 있으니 내용만 쓴다.
읽는 사람은 결재를 올려야 하는 사람이다. 우리 회사 소개가 아니라 그 사람의
문제에서 시작해야 읽힌다.

각 절은 `layout` 하나와 그 절의 본문 HTML 조각으로 이루어진다.

- `cover` — 표지. 고객사와 제안일, 담당자.
- `section` — 장 하나.

## 규칙

- **고객의 말로 시작한다.** 첫 절은 우리가 아니라 고객의 과제다. 미팅에서 들은
  말을 그대로 쓰면 읽는 사람이 자기 이야기라는 것을 안다.
- **제안은 한 문장으로 먼저.** 무엇을 하겠다는 것인지 한 줄로 말하고, 그 다음에
  나눠 설명한다.
- **효과는 숫자와 근거로.** "효율이 좋아집니다" 는 아무 말도 아니다. 무엇이
  얼마나, 어떤 계산으로 달라지는지 쓰고 그 계산의 전제를 밝힌다.
- **일정에는 고객이 할 일도 적는다.** 자료 제공·계정 발급·검수처럼 고객이
  움직여야 하는 것을 빼면 일정은 지켜지지 않는다.
- **견적은 범위와 제외를 함께.** 무엇이 포함되고 무엇이 아닌지 적지 않은 견적은
  나중에 분쟁이 된다.
- 마지막 절은 요청이다. 이 문서를 읽고 무엇을 결정해 달라는 것인지 한 줄.
- 명령어·예외 이름·파일 경로처럼 **글자 그대로 읽어야 하는 것**은 `<code>` 로
  감싼다. 조판이 그 자리를 고정폭으로 세워 주므로, 따옴표로 대신하면 무엇이
  코드이고 무엇이 인용인지 구별되지 않는다.
"""
,
        checks=(
            "첫 절이 고객의 과제인가. 회사 소개로 시작하면 아니다.",
            "제안이 한 문장으로 먼저 서 있는가.",
            "효과가 숫자와 계산 전제로 쓰여 있는가. 형용사만 있으면 아니다.",
            "일정에 고객이 해야 할 일이 함께 있는가.",
            "견적에 포함 범위와 제외 항목이 적혀 있는가.",
            "마지막 절이 무엇을 결정해 달라는 요청인가.",
            "확인되지 않은 고객사 정보를 지어내지 않았는가.",
        ),
    ),
    Sheet(
        "deck-case",
        base="deck-proposal",
        name="케이스 발표",
        name_en="Case deck",
        description="현황과 대안을 나란히 놓고 15분 안에 권고까지 가는 발표",
        description_en="Situation and options side by side, to a recommendation in fifteen minutes",
        category="발표",
        category_en="Presentation",
        fills=("대상 기업", "발표 시간", "결론"),
        fills_en=("Company", "Length", "Recommendation"),
        example_prompt=(
            "케이스 발표 자료를 만들어 줘. 대안을 나란히 견주는 장을 넣고, 마지막 "
            "장에서 무엇을 권고하는지 밝혀 줘.\n\n대상 기업과 발표 시간: "
        ),
        example_prompt_en=(
            "Make a case deck. Include a slide that compares the options side by side, "
            "and say what you recommend on the last one.\n\nCompany and length: "
        ),
        who="경영대 학부생. 팀 케이스를 15분 발표로 만든다.",
        instructions="""
이 덱은 **케이스 발표** 템플릿으로 만든다. 조판은 정해져 있으니 내용만 쓴다.
15분이면 장은 열두 장 안팎이다. 한 장에 한 가지만 말한다.

## 규칙

- **첫 장 다음은 결론이다.** 발표는 논문이 아니다. 무엇을 권고하는지 앞에서
  밝히고, 나머지 장이 그 근거가 되게 한다.
- **현황 장은 숫자 하나로.** 여러 지표를 한 장에 늘어놓으면 아무것도 남지
  않는다. 한 장에 수치 하나와 그 뜻 한 줄.
- **대안은 나란히 놓는 장에서.** 왼쪽과 오른쪽이 같은 기준으로 읽히게 쓴다.
- **말로 할 것은 적지 않는다.** 슬라이드는 읽히는 것이고 설명은 발표자가 한다.
  글머리는 한 줄에 한 생각, 다섯 줄을 넘기지 않는다.
- 마지막 장은 요청이다. 듣는 사람이 무엇을 판단해 달라는 것인지.
- 명령어·예외 이름·파일 경로처럼 **글자 그대로 읽어야 하는 것**은 `<code>` 로
  감싼다. 조판이 그 자리를 고정폭으로 세워 주므로, 따옴표로 대신하면 무엇이
  코드이고 무엇이 인용인지 구별되지 않는다.
"""
,
        checks=(
            "권고가 앞쪽 장에 나오는가. 마지막까지 미루면 아니다.",
            "현황 장이 한 장에 수치 하나를 말하는가.",
            "대안을 나란히 견주는 장이 있고 두 쪽이 같은 기준인가.",
            "글머리가 다섯 줄을 넘지 않는가.",
            "슬라이드에 발표자가 말로 할 문장이 적혀 있지 않은가.",
            "마지막 장이 무엇을 판단해 달라는 요청인가.",
        ),
    ),
    Sheet(
        "deck-defense",
        base="deck-editorial",
        name="심사 발표",
        name_en="Defense deck",
        description="연구 질문에서 방법·결과·한계로, 질문받을 자리를 미리 여는 발표",
        description_en="From the question through method and findings to limits, opening the floor early",
        category="발표",
        category_en="Presentation",
        fills=("연구 주제", "발표 시간", "심사 단계"),
        fills_en=("Topic", "Length", "Stage"),
        example_prompt=(
            "심사 발표 자료를 만들어 줘. 연구 질문을 먼저 세우고, 방법과 결과를 "
            "나눈 뒤 한계를 스스로 밝혀 줘.\n\n연구 주제와 발표 시간: "
        ),
        example_prompt_en=(
            "Make a defense deck. State the research question first, separate method "
            "from findings, and name the limits yourself.\n\nTopic and length: "
        ),
        who="대학원생. 중간·최종 심사에서 발표한다.",
        instructions="""
이 덱은 **심사 발표** 템플릿으로 만든다. 조판은 정해져 있으니 내용만 쓴다.
듣는 사람은 심사위원이다. 무엇을 물었고, 어떻게 답했고, 어디까지만 답했는지를
본다.

## 규칙

- **연구 질문을 한 장에.** 배경보다 먼저 질문을 세운다. 질문이 없는 발표는
  무엇을 심사할지가 없다.
- **방법과 결과를 섞지 않는다.** 어떻게 봤는지와 무엇이 보였는지는 다른 장이다.
- **결과는 그림이나 표로.** 수치를 문장으로 늘어놓으면 읽히지 않는다. 그림
  아래에 그 그림이 말하는 것 한 줄.
- **한계를 스스로 밝힌다.** 심사에서 지적될 것을 먼저 적는 편이 낫다. 무엇을
  못 봤는지, 왜 못 봤는지.
- **다음 계획을 마지막에.** 중간 심사라면 남은 기간에 무엇을 하는지.
- 인용은 장 아래에 짧게. 발표 자료의 각주는 출처를 밝히는 정도로 족하다.
- 명령어·예외 이름·파일 경로처럼 **글자 그대로 읽어야 하는 것**은 `<code>` 로
  감싼다. 조판이 그 자리를 고정폭으로 세워 주므로, 따옴표로 대신하면 무엇이
  코드이고 무엇이 인용인지 구별되지 않는다.
"""
,
        checks=(
            "연구 질문이 배경보다 앞에 한 장으로 서 있는가.",
            "방법과 결과가 다른 장으로 갈라져 있는가.",
            "결과가 그림이나 표로 있고, 그 아래 한 줄로 무엇을 말하는지 적혀 있는가.",
            "한계를 스스로 밝힌 장이 있는가.",
            "다음 계획이 마지막에 있는가.",
            "인용한 수치에 출처가 붙어 있는가.",
        ),
    ),
    Sheet(
        "deck-briefing",
        base="deck-editorial",
        name="사내 브리핑",
        name_en="Briefing deck",
        description="정해진 것과 해야 할 것을 갈라, 회의 시간을 줄이려고 만드는 발표",
        description_en="Decisions apart from actions, made to shorten the meeting",
        category="발표",
        category_en="Presentation",
        fills=("주제", "듣는 사람", "결정할 것"),
        fills_en=("Topic", "Audience", "Decision"),
        example_prompt=(
            "사내 브리핑 자료를 만들어 줘. 정해진 것과 아직 정할 것을 갈라서 적고, "
            "마지막에 담당과 기한을 넣어 줘.\n\n주제와 듣는 사람: "
        ),
        example_prompt_en=(
            "Make a briefing deck. Keep what is decided apart from what is not, and end "
            "with owners and dates.\n\nTopic and audience: "
        ),
        who="사무직. 주 3회 회의를 짧게 끝내려고 만든다.",
        instructions="""
이 덱은 **사내 브리핑** 템플릿으로 만든다. 조판은 정해져 있으니 내용만 쓴다.
이 자료의 목적은 회의를 짧게 끝내는 것이다. 읽고 나면 물어볼 것이 줄어야 한다.

## 규칙

- **첫 장에 오늘 정할 것.** 무엇을 결정하러 모였는지 한 줄. 없으면 이 회의는
  공유이지 회의가 아니고, 그렇다고 적는다.
- **정해진 것과 정할 것을 가른다.** 섞이면 듣는 사람이 이미 끝난 이야기를 다시
  꺼낸다.
- **진행 상황은 표로.** 항목·상태·담당. 상태는 '진행 중' 대신 무엇까지 됐는지.
- **막힌 것을 숨기지 않는다.** 막혔으면 무엇 때문에 막혔고 누가 풀 수 있는지.
- **마지막 장은 담당과 기한.** 담당 없는 항목은 하지 않기로 한 것과 같다.
- 장은 여덟 장을 넘기지 않는다. 넘으면 회의가 아니라 보고서다.
- 명령어·예외 이름·파일 경로처럼 **글자 그대로 읽어야 하는 것**은 `<code>` 로
  감싼다. 조판이 그 자리를 고정폭으로 세워 주므로, 따옴표로 대신하면 무엇이
  코드이고 무엇이 인용인지 구별되지 않는다.
"""
,
        checks=(
            "첫 장에 오늘 결정할 것이 한 줄로 있는가. 없으면 공유라고 밝혔는가.",
            "정해진 것과 아직 정할 것이 갈라져 있는가.",
            "진행 상황 표의 상태가 '진행 중' 대신 무엇까지 됐는지 말하는가.",
            "막힌 것이 있다면 무엇 때문에, 누가 풀 수 있는지 적혀 있는가.",
            "마지막 장에 담당과 기한이 있는가.",
            "장이 여덟 장을 넘지 않는가.",
        ),
    ),
)


def main() -> int:
    for sheet in _SHEETS:
        if not (ROOT / sheet.base).is_dir():
            print(f"바탕 서식이 없습니다: {sheet.base}", file=sys.stderr)
            return 1
        folder = write(sheet)
        print(f"{sheet.folder:16} {sheet.name:10} ← {sheet.base:12} {sheet.who}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
