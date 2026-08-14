"""Turning finished conversations into durable facts.

After a turn ends, a cheap model is asked whether the *user* said anything worth
remembering, and at most a couple of short facts are written.

Three rules keep it from becoming noise:

* **Only about the user.** What earns a row is what KloudChat cannot look up —
  their lab, their conventions, their stated preferences.
* **At most two per turn**, and nothing at all is the common answer.
* **Never a near-duplicate.** Existing memories go into the prompt, because
  only the model separates "the same fact, said differently" from "the same
  sentence, different value" — on real pairs, character overlap puts a
  rewording at 0.50 and two different lab codes at 0.43. The string check is a
  backstop for the identical case only.

Failures are swallowed: a missing memory is a small loss, a turn that failed to
return is a large one.
"""

from __future__ import annotations

import json
import logging
import re

import httpx
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.models.user import User
from app.models.workspace import Memory, MemoryType
from app.services import settings_store

log = logging.getLogger(__name__)

#: Two is enough for one exchange. More than that and the extractor is padding.
_MAX_PER_TURN = 2
_MAX_TOTAL = 200

_PROMPT = """다음 대화에서 **사용자에 대해** 앞으로도 계속 참이고,
다음 대화에서 알고 있으면 도움이 될 사실만 뽑아라.

뽑지 말 것:
- 세상에 대한 일반 지식, 모델이 이미 아는 것
- 이번 대화에서만 유효한 일시적인 맥락
- 사용자가 물어본 질문 자체

뽑을 것의 예: 소속·역할, 진행 중인 연구 주제, 사용하는 도구나 규칙, 명시한 선호.

이미 알고 있는 사실은 다시 뽑지 마라. 같은 내용을 다르게 표현한 것도 안 된다.
다만 값이 바뀐 경우(예: 코드가 달라짐)는 새 사실로 뽑아라.
%(known)s
JSON 배열로만 답하라. 없으면 빈 배열 []. 최대 2개.
각 항목: {"name": "짧은 제목", "body": "한 문장 사실"}

사용자: %(user)s
어시스턴트: %(assistant)s"""


def _norm(text: str) -> str:
    """Loose key for duplicate detection: letters and digits only."""
    return re.sub(r"[^0-9a-z가-힣]+", "", text.lower())


def _shingles(text: str) -> set[str]:
    """Character bigrams of the normalised text.

    Catches rewordings without embeddings, which matters for Korean where the
    difference is mostly particles and endings.
    """
    n = _norm(text)
    return {n[i : i + 2] for i in range(len(n) - 1)} or {n}


#: Near-identical strings only. High on purpose: two facts sharing a sentence
#: frame but differing in the part that matters already overlap around 0.60.
_NEAR_IDENTICAL = 0.9


def _is_duplicate(body: str, seen: list[tuple[str, set[str]]]) -> bool:
    key, grams = _norm(body), _shingles(body)
    for other_key, other_grams in seen:
        if key == other_key:
            return True
        if grams and len(grams & other_grams) / len(grams) >= _NEAR_IDENTICAL:
            return True
    return False


async def extract(
    db: AsyncSession,
    user: User,
    *,
    user_message: str,
    assistant_message: str,
    api_key: str,
    model: str,
) -> int:
    """Writes any new facts and returns how many. Caller commits."""
    if not user_message.strip() or not assistant_message.strip():
        return 0

    existing = (await db.exec(select(Memory).where(Memory.user_id == user.id))).all()
    if len(existing) >= _MAX_TOTAL:
        # Past this size the store stops helping and needs pruning by hand.
        log.info("auto-memory skipped for %s: %d memories already", user.email, len(existing))
        return 0
    seen = [(_norm(m.body or m.name), _shingles(m.body or m.name)) for m in existing]

    base, _ = await settings_store.litellm_config()
    # Newest first — the prompt has room for a few dozen.
    recent = sorted(existing, key=lambda m: m.updated_at, reverse=True)[:40]
    known = (
        "\n[이미 알고 있는 사실]\n" + "\n".join(f"- {m.body or m.name}" for m in recent) + "\n"
        if recent
        else ""
    )
    prompt = _PROMPT % {
        "user": user_message[:2000],
        "assistant": assistant_message[:2000],
        "known": known,
    }
    try:
        async with httpx.AsyncClient(
            base_url=base.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=settings.title_timeout_sec,
        ) as client:
            response = await client.post(
                "/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "max_tokens": 300,
                },
            )
            response.raise_for_status()
            content = (response.json()["choices"][0]["message"]["content"] or "").strip()
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        log.info("auto-memory extraction skipped: %s", exc)
        return 0

    # Small models fence their JSON however they like.
    match = re.search(r"\[.*\]", content, re.S)
    if not match:
        return 0
    try:
        items = json.loads(match.group(0))
    except json.JSONDecodeError:
        return 0
    if not isinstance(items, list):
        return 0

    written = 0
    for item in items[:_MAX_PER_TURN]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()[:80]
        body = str(item.get("body") or "").strip()[:500]
        if not name or not body or _is_duplicate(body, seen):
            continue
        seen.append((_norm(body), _shingles(body)))
        db.add(
            Memory(
                user_id=user.id,
                name=name,
                # Provenance, so the memory screen is not a list of facts the
                # user does not remember writing.
                description="대화에서 자동으로 기록됨",
                type=MemoryType.user,
                body=body,
            )
        )
        written += 1
    return written
