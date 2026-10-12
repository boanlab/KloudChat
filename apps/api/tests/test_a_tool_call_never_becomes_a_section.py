"""A skill that tells the reader to call `run_skill_script` reaches a document writer (no tools)
without that step, and a tool call the writer still writes out as JSON never ships: the section is
written once more, or the block is dropped."""

from __future__ import annotations

import json

from app.models.chat import SessionKind
from app.models.workspace import Skill
from app.pipeline import chat_stages
from app.pipeline.stage import Context
from app.services import internal_words, report, skill_packages, starter, workspace_context

REQUEST = "아래 메모로 회의록 작성해 줘. 결정 사항과 할 일(담당·기한)이 한눈에 보이게."
CALL = {
    "action_type": "run_skill_script", "skill": "gap-check", "script": "gaps",
    "args": {"op": "check", "questions": [{"id": "q1", "label": "회의 일자",
                                           "detector": {"type": "judge"}}],
             "request": REQUEST, "material": "10/8(목) 14시 교육혁신팀 주간회의"},
}
FENCED = "```json\n" + json.dumps(CALL, ensure_ascii=False, indent=0) + "\n```"


def _skill(key: str) -> Skill:
    package = skill_packages.packages()[key]
    return Skill(id=f"s-{key}", owner_id="u1", name=package.name, body=package.body,
                 catalog_key=key, kinds=list(package.kinds),
                 required_tools=list(package.required_tools))


def test_a_written_out_tool_call_is_dropped_and_prose_kept():
    body = (f"할 일은 아래와 같습니다.\n\n{FENCED}\n\n"
            "| 할 일 | 담당 |\n|---|---|\n| 체크리스트 | 김도윤 |")
    out = internal_words.strip_tool_calls(body)
    assert "action_type" not in out and "run_skill_script" not in out
    assert out.startswith("할 일은 아래와 같습니다.") and "| 체크리스트 | 김도윤 |" in out
    assert internal_words.has_tool_call(body)
    # bare, not fenced
    bare = "앞 문단.\n" + json.dumps(CALL, ensure_ascii=False) + "\n뒤 문단."
    assert internal_words.strip_tool_calls(bare) == "앞 문단.\n\n뒤 문단."
    named = json.dumps({"name": "run_code", "arguments": {"code": "print(1)"}})
    assert internal_words.strip_tool_calls(named) == ""


def test_json_that_is_not_one_of_our_tool_calls_stays():
    for text in (
        '```json\n{"name": "홍길동", "age": 30}\n```',
        # an API example naming someone else's function
        '```json\n{"tool_calls": [{"function": {"name": "get_weather", "arguments": "{}"}}]}\n```',
        '설정은 {"action": "approve"} 처럼 씁니다.',
    ):
        assert internal_words.strip_tool_calls(text) == text
        assert not internal_words.has_tool_call(text)


def test_final_tidy_drops_a_tool_call_section_body():
    sections = report.final_tidy([{"heading": "할 일", "content": FENCED},
                                  {"heading": "결정 사항", "content": "온라인 송출로 수용한다."}])
    assert sections[0]["content"].strip() == ""
    assert sections[1]["content"] == "온라인 송출로 수용한다."


def test_a_document_writer_gets_no_script_call_step():
    resolved = [(skill, starter.runtime_metadata(skill))
                for skill in (_skill("gap-check"), _skill("grounding-check"), _skill("pii-mask"))]
    for kind in (SessionKind.report, SessionKind.slides):
        blocks = workspace_context._skill_blocks(resolved, kind)
        text = "\n".join(b.text for b in blocks)
        assert "run_skill_script" not in text and "CLI" not in text and "인터페이스" not in text
        assert text.count(workspace_context.NO_TOOLS_NOTE) == 3
        # writing guidance kept
        assert "hold without them" in text and "Do not copy a person's mobile number" in text
    # a chat turn has the tool and keeps the step
    chat = workspace_context._skill_blocks(resolved, SessionKind.chat)[0].text
    assert "run_skill_script" in chat


DRAFT = """## 결정 사항
대기자 2명은 온라인 동시 송출로 수용한다.
## 할 일
{call}
## 미결 및 보류 사항
디자인 업체는 다음 회의에서 정한다.
"""


async def test_a_section_written_as_a_tool_call_is_written_again(monkeypatch):
    prompts: list[str] = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        prompts.append(prompt)
        if len(prompts) == 1 and all(h in prompt for h in ("결정 사항", "할 일", "미결")):
            return DRAFT.format(call=FENCED), {"inputTokens": 1, "outputTokens": 1}
        return "- 모바일 검수 체크리스트 공유 (김도윤, 기한 [기한])", {
            "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    gap = _skill("gap-check")
    block = workspace_context._skill_blocks([(gap, starter.runtime_metadata(gap))],
                                            SessionKind.report)[0].text
    events = [e async for e in report.write(
        request=REQUEST, model="m", api_key="k", web_search=False, trusted_context=[block],
        approved_plan={"title": "교육혁신팀 주간회의 회의록",
                       "sections": ["결정 사항", "할 일", "미결 및 보류 사항"]},
    )]
    sections = next(e["sections"] for e in events if e["type"] == "report")
    todo = next(s for s in sections if s["heading"] == "할 일")
    assert "체크리스트" in todo["content"]
    shipped = "\n".join(str(s["content"]) for s in sections)
    assert "action_type" not in shipped and "run_skill_script" not in shipped
    # the writer was never told to call the script
    assert any(workspace_context.NO_TOOLS_NOTE in p for p in prompts)
    assert not any("run_skill_script" in p for p in prompts)


async def test_a_chat_answer_never_shows_a_written_out_tool_call():
    bundle = chat_stages.bundle_for(
        answer=f"회의록 초안입니다.\n\n{FENCED}\n\n- 결정: 온라인 송출", request=REQUEST,
        model={"id": "m"}, actual_model=None, api_key="k", session_id="s", today=None,
        first_message=REQUEST, messages=[], material="", check_keys=(), check_material="",
        tool_outputs=[], judge="", kept_steps=[], usage={},
    )
    await chat_stages.Normalize().apply(bundle, Context())
    assert "run_skill_script" not in bundle.document.answer
    assert "- 결정: 온라인 송출" in bundle.document.answer
