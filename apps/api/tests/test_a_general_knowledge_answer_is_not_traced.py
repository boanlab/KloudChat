"""A chat answer from the model's own knowledge carries no 「(확인 필요)」 marks; one that
quotes a source's numbers still does."""

from __future__ import annotations

from app.pipeline import chat_stages
from app.pipeline.check_stages import TRACE, AnswerGrounding
from app.pipeline.stage import Context
from app.services import check_skills

SEASONS = (
    "봄은 3월 초에 시작해 5월까지 이어집니다. 여름은 6월 말부터 덥고 습도가 90퍼센트까지 오릅니다.\n"
    "가을은 9월에 접어들며 맑습니다. 겨울은 12월부터 춥습니다."
)


def _bundle(answer: str, *, check_keys=(), material: str = "", tool_outputs=()):
    return chat_stages.bundle_for(
        answer=answer, request="한국의 사계절을 설명해줘", model={"id": "m"},
        actual_model=None, api_key="k", session_id="s", today=None, first_message="",
        messages=[], material="한국의 사계절을 설명해줘", check_keys=check_keys,
        check_material=material, tool_outputs=list(tool_outputs), judge="", kept_steps=[],
        usage={}, traced=False,
    )


async def _run(bundle) -> list[dict]:
    stage = AnswerGrounding()
    if stage.skip(bundle):
        return []
    return [event async for event in stage.run(bundle, Context())]


async def test_no_sources_means_no_marks_even_with_the_check_attached():
    bundle = _bundle(SEASONS, check_keys=(TRACE,))
    assert AnswerGrounding().skip(bundle)
    assert await _run(bundle) == []
    assert bundle.document.answer == SEASONS


async def test_an_attached_source_still_marks_a_number_it_does_not_hold():
    material = "여름 평균 기온은 25도입니다."
    bundle = _bundle("여름 평균 기온은 25도이고 습도는 90퍼센트입니다.", check_keys=(TRACE,),
                     material=material)
    events = await _run(bundle)
    assert events and events[-1]["id"] == "grounding"
    assert "90퍼센트" not in bundle.document.answer or "확인 필요" in bundle.document.answer
    assert "25도" in bundle.document.answer


async def test_a_tool_output_counts_as_a_source():
    bundle = _bundle("습도는 90퍼센트입니다.", check_keys=(TRACE,), tool_outputs=["기온 25도"])
    assert not AnswerGrounding().skip(bundle)


def test_month_words_are_never_found_in_a_chat_answer():
    options = check_skills.options_for(None, None, surface="chat")
    found = check_skills.entry(TRACE).check(SEASONS, "근거 자료의 내용입니다.", [], options)
    assert not [f for f in found if f["kind"] == "month"]


def test_a_dated_day_is_still_found_in_a_chat_answer():
    options = check_skills.options_for(None, None, surface="chat")
    found = check_skills.entry(TRACE).check("행사는 3월 17일에 열립니다.", "자료", [], options)
    assert any(f["kind"] == "date" for f in found)
