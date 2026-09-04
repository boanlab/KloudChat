"""Proposes a design system draft from an existing document with one model call.

The draft is returned to the editor, never stored. Every field passes through
`design.normalise_tokens` / `design.craft_keys`, so invented values become
defaults. Caller owns billing.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.core.config import settings
from app.services import design, settings_store, thinking

log = logging.getLogger(__name__)

#: Characters of the source sent to the model.
_MAX_SOURCE = 12_000

_PROMPT = """다음 문서를 읽고, 이 조직이 쓰는 글과 서식의 규칙을 디자인 시스템으로 정리하라.

규칙:
- name 은 이 시스템을 부를 짧은 이름. 문서 제목을 그대로 옮기지 말고 무엇을 위한
  서식인지 가리켜라. 20자 이내.
- description 은 한 줄. 어떤 문서에 쓰는 것인지 적어라.
- accent/ink/muted 는 `#rrggbb` 여섯 자리 소문자. 문서에 색이 드러나면 그 색을,
  드러나지 않으면 문서의 성격에 맞는 차분한 색을 골라라. accent 는 제목과 강조에
  쓰이므로 흰 글자를 얹어도 읽히는 어두운 색이어야 한다.
- font 는 "gothic" 또는 "serif" 둘 중 하나. 공문과 인쇄물은 serif, 화면과 발표는
  gothic 이 보통이다.
- body 는 이 문서가 지키는 문체 규칙을 200자 이내로. 문서에서 실제로 관찰한 것만
  적어라. 없으면 빈 문자열로 두고 지어내지 마라.
- image_style 은 이 조직의 그림에 붙일 영어 구절. 관찰할 근거가 없으면 빈 문자열.
- craft 는 다음 중에서만 고른 목록이다: {craft}

JSON 객체로만 답하라.
예: {{"name": "학과 공문", "description": "대내외 공문과 협조 요청에 쓰는 서식",
  "tokens": {{"accent": "#1e3a8a", "ink": "#111827", "muted": "#6b7280", "font": "serif"}},
  "body": "제목 다음에 근거를 밝히고, 한 문장에 한 사실만 담는다.",
  "image_style": "muted documentary photography, low saturation",
  "craft": ["restraint", "typography"]}}

문서:
{source}"""


async def _complete(model: str, prompt: str, api_key: str) -> tuple[str, dict[str, int]]:
    base, _ = await settings_store.litellm_config()
    async with httpx.AsyncClient(
        base_url=base.rstrip("/"),
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=httpx.Timeout(settings.chat_timeout_sec, connect=10.0),
    ) as client:
        response = await client.post(
            "/v1/chat/completions",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 700,
                # A reasoning model may spend the whole budget thinking and
                # return empty content.
                "reasoning": thinking.NO_REASONING,
            },
        )
        response.raise_for_status()
        payload = response.json()

    raw = payload.get("usage") or {}
    return (payload["choices"][0]["message"]["content"] or "").strip(), {
        "inputTokens": int(raw.get("prompt_tokens") or 0),
        "outputTokens": int(raw.get("completion_tokens") or 0),
    }


def _object(text: str) -> dict:
    """The first JSON object in a reply that may be fenced or prefaced."""
    body = re.sub(r"^\s*```[A-Za-z]*\s*\n(.*?)\n?\s*```\s*$", r"\1", text.strip(), flags=re.S)
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return {}
    try:
        return json.loads(body[start : end + 1])
    except json.JSONDecodeError:
        return {}


class ExtractError(RuntimeError):
    """Nothing usable came back; the message is user-facing."""


async def extract(*, source: str, model: str, api_key: str) -> tuple[dict, dict[str, int]]:
    """`(draft, usage)`: a normalised design system proposal. Raises `ExtractError` when unreadable.
    """
    text = re.sub(r"\n{3,}", "\n\n", source).strip()
    if len(text) < 40:
        raise ExtractError("읽을 내용이 너무 짧습니다.")

    reply, usage = await _complete(
        model,
        _PROMPT.format(craft=" / ".join(design.CRAFT), source=text[:_MAX_SOURCE]),
        api_key,
    )
    data = _object(reply)
    if not data:
        log.warning("design extraction unparseable: %s", reply[:300])
        raise ExtractError("문서에서 디자인 시스템을 읽어내지 못했습니다.")

    draft = {
        "name": str(data.get("name") or "").strip()[:60],
        "description": str(data.get("description") or "").strip()[:200],
        "tokens": design.normalise_tokens(data.get("tokens")),
        "body": str(data.get("body") or "").strip()[: design.MAX_BODY],
        "image_style": str(data.get("image_style") or "").strip()[: design.MAX_IMAGE_STYLE],
        "craft": design.craft_keys(data.get("craft")),
    }
    if not draft["name"]:
        raise ExtractError("문서에서 디자인 시스템을 읽어내지 못했습니다.")
    return draft, usage


__all__ = ["ExtractError", "extract"]
