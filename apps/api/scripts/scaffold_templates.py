"""Generates the derived 서식 folders (toml, instructions, checklist) from `_SHEETS`.

Run from the API image:

    docker compose run --rm --no-deps -v "$PWD/apps/api:/repo" -w /repo api \
        python scripts/scaffold_templates.py

Each derived 서식 names a base 서식 via `seed_from` and shares its `seed.html`.
`build_docx_templates.py` and `build_pptx_templates.py` read the folders this writes.
"""

from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent / "app" / "design_templates"


class Sheet:
    """One derived 서식 as `template.toml` states it."""

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
    """The `layouts` line of the base 서식's `template.toml`, verbatim."""
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


def _wrap_of(base: str) -> str:
    """The base 서식's `[wrap]` section, copied verbatim."""
    text = (ROOT / base / "template.toml").read_text(encoding="utf-8")
    marker = "[wrap]"
    return text[text.index(marker) :].rstrip() if marker in text else ""


def write(sheet: Sheet) -> pathlib.Path:
    folder = ROOT / sheet.folder
    folder.mkdir(exist_ok=True)
    base = ROOT / sheet.base

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
        description_en=(
            "From the question through method and findings to limits, "
            "opening the floor early"
        ),
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
        write(sheet)
        print(f"{sheet.folder:16} {sheet.name:10} ← {sheet.base:12} {sheet.who}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
