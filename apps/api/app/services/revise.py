"""Routes an instruction typed under a finished document to the parts it targets.

Scopes: `parts` (named parts), `whole` (every part), `new` (plan again from
scratch). Nothing here rewrites; the surfaces do that.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

import httpx

from app.services import settings_store

log = logging.getLogger(__name__)

#: Parts one instruction may touch before it becomes a whole-document pass.
MAX_TARGETS = 3

_PROMPT = """사용자가 이미 완성된 문서를 보면서 아래 문장을 입력했다.
무엇을 고쳐 달라는 것인지 판정하라.

문서 제목: {title}

문서의 구성:
{outline}

사용자 입력:
{message}

판정 규칙:
- 특정 부분을 고치라는 것이면 그 부분의 번호를 targets 에 담아라. 최대 {limit}개.
- 어느 부분인지 말하지 않았어도 내용으로 짐작되면 그 부분을 골라라.
- 문서 전체에 걸친 요청(예: "전체적으로 더 간결하게", "말투를 바꿔줘")이면
  scope 를 "whole" 로 하라.
- **다른 주제의 새 문서**를 원하는 것이면 scope 를 "new" 로 하라. 지금 문서를
  고치는 것이 아니라 버리고 다시 쓰는 경우만 해당한다.
- 애매하면 "whole" 이 아니라 가장 가까운 부분 하나를 고르는 쪽이 낫다. 문서
  전체를 다시 쓰는 것은 사용자가 보고 있던 글을 통째로 바꾸는 일이다.

note 는 그 부분을 어떻게 고쳐야 하는지 한두 문장으로. 사용자의 말을 그대로
옮기지 말고, 고칠 내용으로 적어라.

JSON 객체로만 답하라.
예: {{"scope": "parts", "targets": [3], "note": "분량을 절반으로 줄이고 표는 남긴다"}}"""


@dataclass(slots=True)
class Plan:
    """Where an instruction lands, and what it asks for there."""

    #: `parts` · `whole` · `new`. `new` means the caller should plan again.
    scope: str = "new"
    #: Indices into the document's parts, zero-based and already bounded.
    targets: list[int] = field(default_factory=list)
    #: The instruction as something to do, for the rewrite prompt.
    note: str = ""
    usage: dict[str, int] = field(default_factory=lambda: {"inputTokens": 0, "outputTokens": 0})

    @property
    def revises(self) -> bool:
        return self.scope in ("parts", "whole") and bool(self.targets)


def _parse(text: str, count: int, message: str) -> Plan:
    """The model's JSON as a `Plan`, bounded to parts that exist; `new` when unusable."""
    block = text[text.find("{") : text.rfind("}") + 1] if "{" in text and "}" in text else ""
    try:
        parsed = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return Plan()
    if not isinstance(parsed, dict):
        return Plan()

    scope = str(parsed.get("scope") or "").strip().lower()
    note = str(parsed.get("note") or "").strip()[:600] or message.strip()[:600]
    if scope == "whole":
        return Plan(scope="whole", targets=list(range(count)), note=note)
    if scope != "parts":
        return Plan(note=note)

    numbers: list[int] = []
    for value in parsed.get("targets") or []:
        try:
            # One-based in the prompt, as numbered on screen.
            index = int(value) - 1
        except (TypeError, ValueError):
            continue
        if 0 <= index < count and index not in numbers:
            numbers.append(index)
    if not numbers:
        return Plan(note=note)
    if len(numbers) > MAX_TARGETS:
        return Plan(scope="whole", targets=list(range(count)), note=note)
    return Plan(scope="parts", targets=numbers, note=note)


async def plan(
    *,
    message: str,
    title: str,
    parts: list[str],
    model: str,
    api_key: str,
) -> Plan:
    """Read one instruction against one document's outline (`parts`, in order). Never raises."""
    if not parts or not message.strip():
        return Plan()

    outline = "\n".join(f"{i + 1}. {name}" for i, name in enumerate(parts))
    base, _ = await settings_store.litellm_config()
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=httpx.Timeout(60.0, connect=10.0),
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {
                            "role": "user",
                            "content": _PROMPT.format(
                                title=title[:200],
                                outline=outline[:4000],
                                message=message[:1000],
                                limit=MAX_TARGETS,
                            ),
                        }
                    ],
                    "max_tokens": 300,
                },
            )
            response.raise_for_status()
            payload = response.json()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        log.info("revision routing failed: %s", exc)
        return Plan()

    raw = payload.get("usage") or {}
    decided = _parse(
        (payload["choices"][0]["message"]["content"] or "").strip(), len(parts), message
    )
    decided.usage = {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }
    return decided


#: Phrases that plainly mean "start over"; checked before the routing call.
_START_OVER = re.compile(
    r"(새로\s*(써|작성|만들)|처음부터\s*다시|다른\s*주제로|아예\s*다시|버리고\s*다시)"
)


def obviously_new(message: str) -> bool:
    return bool(_START_OVER.search(message))


def label(plan: Plan, parts: list[str]) -> str:
    """What the step on screen says this pass is doing."""
    if plan.scope == "whole":
        return "문서 전체 고치는 중"
    named = " · ".join(parts[i] for i in plan.targets if i < len(parts))
    return f"고치는 중: {named}"[:120]


__all__ = ["MAX_TARGETS", "Plan", "label", "obviously_new", "plan"]
