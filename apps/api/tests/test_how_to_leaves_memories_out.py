"""A question about using KloudChat is answered from its guide alone: the person's memories
and 개인 맞춤 설정 stay out of that turn.

Persona A/B, 3rd run (P3): a seeded wrong memory 「보고서 언어(항상 영어로)」 came back in the
how-to answer as advice to write 「회의록은 영어로 작성한다」 into the new agent's 지시.
"""

from __future__ import annotations

import pytest
from test_app_help import _PERSONA
from test_context_visibility import _Db, _memory, _session, _user
from test_privacy import _external_model, _NoWriteDb, _patch_guard_dependencies, _request

from app.models.chat import ChatSession
from app.models.user import User
from app.routers import sessions as sessions_router
from app.schemas.chat import SendMessage
from app.services.workspace_context import assemble

_WRONG = _memory("보고서 언어", "보고서와 회의록은 항상 영어로 작성한다")
_ABOUT = {"aboutMe": "회의록은 영어로 쓰는 편이다", "responseStyle": "영어로 답해 줘"}


def _person():
    user = _user()
    user.preferences = dict(_ABOUT)
    return user


async def test_a_how_to_turn_reads_no_memory_and_no_personal_settings():
    context = await assemble(_Db(memories=[_WRONG]), _person(), _session(), how_to=True)

    sources = {b.source for b in context.blocks}
    assert "memory" not in sources and "user.instructions" not in sources
    assert "영어" not in "\n".join(b.text for b in context.blocks)
    # Nor does the timeline say it answered from one.
    assert context.loaded_memories == () and context.total_memories == 0


async def test_any_other_turn_still_reads_them():
    context = await assemble(_Db(memories=[_WRONG]), _person(), _session())

    sources = {b.source for b in context.blocks}
    assert {"memory", "user.instructions"} <= sources


class _Stop(Exception):
    pass


@pytest.mark.parametrize(("text", "how_to"), [
    (_PERSONA, True),
    ("회의록을 워드 파일로 받고 싶은데 여기서 할 수 있어?", True),
    ("에이전트에 넣은 지시는 어디서 바꿔?", True),
    # A task keeps the person's memories.
    ("어제 회의 메모야. 회의록으로 만들어 줘.", False),
])
async def test_the_chat_turn_asks_for_a_context_without_them(monkeypatch, text, how_to):
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    await _patch_guard_dependencies(
        monkeypatch, session=session, models=[_external_model("external/one")], blocks=[]
    )
    asked: list[dict] = []

    async def recorder(*_args, **kwargs):
        asked.append(kwargs)
        raise _Stop

    monkeypatch.setattr(sessions_router, "assemble", recorder)
    with pytest.raises(_Stop):
        await sessions_router.send_message(
            session.id, SendMessage(content=text), _request(), user, _NoWriteDb()
        )
    assert asked and asked[0].get("how_to", False) is how_to
