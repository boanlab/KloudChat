"""Labelled method/flow/concept figures as mermaid source, written by a model and drawn by the
client.

Style rules follow PaperBanana's NeurIPS diagram guide (Zhu et al., 2026):
zones as subgraphs, rounded processes / square data / cylinder stores, solid
forward and dashed auxiliary edges, one `hot` highlight. The mermaid source is
what is stored; the picture is derived from it.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from app.services.report import _complete

log = logging.getLogger(__name__)

#: Figure kinds, as the prompt describes them.
FIGURES: dict[str, str] = {
    "method": "제안 방법의 구조도 — 구성 요소와 그 사이의 데이터 흐름",
    "flow": "처리 흐름도 — 입력이 단계를 거쳐 결과가 되기까지",
    "concept": "개념도 — 개념들의 관계와 층위",
}

_RULES = """
너는 학술 논문의 방법 그림(method figure)을 mermaid 로 그리는 도식가다.
사람이 적어 준 설명을 읽고 **그 사람의 방법**을 그려라. 설명에 없는 구성
요소를 지어내지 마라. 이름은 설명에 쓰인 용어를 그대로 써라.

출력은 ```mermaid 블록 하나와, 그 아래 한 줄의 그림 설명(캡션)뿐이다.

## 형태
- 방법 구조도·처리 흐름도는 `flowchart LR`. 왼쪽이 입력, 오른쪽이 결과.
  층(세로 깊이)은 3개 이하.
- 개념도는 `flowchart TB`. 바탕이 되는 개념이 위, 갈래가 가운데, 목표가
  아래. 위계는 위아래로 읽힌다.
- **자기 자신으로 가는 화살표는 없다.** 「A 는 x·y 를 포함한다」는 x·y 를
  A 아래의 노드로 두거나, 이름에 넣어라(`a[정보 리터러시: 검색·평가]`).
  고리는 되먹임에만 쓰고, 되먹임은 다른 노드로 돌아가는 점선이다.
- 단계나 영역은 `subgraph` 로 묶어라. 제목은 짧게. 2~4개가 알맞다.
  `subgraph enc["인코더"]` 처럼 id 와 표시 이름을 나눠 써라.
- 노드 모양은 뜻을 담는다. **아래 넷만** 쓰고, 괄호를 섞지 마라
  (`{(…)}` 같은 것은 문법 오류다):
  - 처리·모듈·연산은 둥근 모양 `id(이름)`
  - 데이터·텐서·문서는 각진 모양 `id[이름]`
  - 저장소·버퍼·데이터베이스는 원통 `id[(이름)]`
  - 판단·조건은 마름모 `id{이름}`
- 노드 id 는 영문 소문자, 이름은 한 번만 정의한다. 화살표 양끝은 반드시
  **이미 정의한 id** 다 — 이름 없는 id 하나만 덜렁 두지 마라.
- 노드는 12개 이하, 이름은 12자 안쪽. `/` 로 둘을 한 칸에 넣지 마라.

## 선
- 본류(순전파, 주 데이터 흐름)는 실선 `-->`.
- 보조 흐름(기울기, 되먹임, 건너뛰기, 손실 계산)은 점선 `-.->`.
- 선 위 글자는 `A -->|글자| B` 하나뿐이고 6자 안쪽. 길어지면 붙이지 마라.
- 연산 기호는 노드로 두지 말고 선 위 글자로: `-->|⊕|`, `-->|⊗|`.

## 강조
- 학습되는 부분이나 최종 출력 **하나**에만 `:::hot` 을 붙여라.
  예: `out[결과]:::hot`. 둘 이상에 붙이면 강조가 아니다.
- `style`, `classDef`, `linkStyle` 을 쓰지 마라. 색은 서식이 정한다.

## 캡션
- mermaid 블록 아래 한 줄. 「그림: 」으로 시작. 무엇을 보여 주는 그림인지
  한 문장. 수식 기호가 필요하면 캡션에 쓰고 노드 이름에는 쓰지 마라.
"""


def _messages(description: str, figure: str, language: str) -> list[dict[str, str]]:
    what = FIGURES.get(figure, FIGURES["method"])
    lang = "영어로" if language == "en" else "한국어로"
    return [
        {"role": "system", "content": _RULES},
        {
            "role": "user",
            "content": (
                f"그릴 것: {what}\n노드 이름과 캡션은 {lang} 쓴다.\n\n설명:\n{description.strip()}"
            ),
        },
    ]


_BLOCK = re.compile(r"```mermaid\s*\n(.*?)```", re.S)
_CAPTION = re.compile(r"^\s*(?:그림|Figure)\s*[:：]\s*(.+)$", re.M)


def _parse(text: str) -> tuple[str, str]:
    """Pulls the mermaid source and the caption out of the answer."""
    block = _BLOCK.search(text)
    source = (block.group(1) if block else text).strip()
    # A `style` line would override the house palette.
    source = "\n".join(
        line
        for line in source.splitlines()
        if not re.match(r"^\s*(style|classDef|linkStyle)\b", line)
    )
    source = _mend(source)
    after = text[block.end() :] if block else ""
    caption = _CAPTION.search(after)
    return source, (caption.group(1).strip() if caption else "")


#: Mixed bracket shapes (`{(x)}`) that mermaid refuses, mapped to one shape.
_MIXED = (
    (re.compile(r"\{\(([^)}]*)\)\}"), r"(\1)"),
    (re.compile(r"\(\{([^)}]*)\}\)"), r"{\1}"),
    (re.compile(r"\[\{([^\]}]*)\}\]"), r"{\1}"),
    (re.compile(r"\{\[([^\]}]*)\]\}"), r"[\1]"),
)


def _mend(source: str) -> str:
    """Rewrites mixed bracket shapes to a single valid one."""
    for pattern, replacement in _MIXED:
        source = pattern.sub(replacement, source)
    return source


async def draw(
    *,
    description: str,
    figure: str,
    model: str,
    api_key: str,
    language: str = "ko",
    broken: str = "",
    error: str = "",
) -> tuple[str, str, dict[str, Any]]:
    """Writes one figure as mermaid. Returns `(source, caption, usage)`.

    `broken`/`error` carry a previous answer mermaid refused, for one retry.
    """
    messages = _messages(description, figure, language)
    if broken:
        messages.append({"role": "assistant", "content": f"```mermaid\n{broken}\n```"})
        messages.append(
            {
                "role": "user",
                "content": (
                    "위 mermaid 는 그리지 못했다. 오류: "
                    f"{error[:300] or '문법 오류'}\n"
                    "같은 그림을 문법만 고쳐 다시 써라. 규칙의 네 가지 노드 모양만 쓰고, "
                    "괄호를 섞지 말고, 화살표 양끝은 정의된 id 만 써라."
                ),
            }
        )
    text, usage = await _complete(model, messages, api_key, max_tokens=1600)
    source, caption = _parse(text)
    if "flowchart" not in source and "graph" not in source:
        raise ValueError("no_diagram")
    return source, caption, usage


__all__ = ["FIGURES", "draw"]
