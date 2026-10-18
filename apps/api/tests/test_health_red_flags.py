"""Health red flags: the emergency action first, before any diagnosis."""

from __future__ import annotations

import pytest
from test_exact_facts_are_searched_and_traced import (
    COUNSELLOR_TURNS,
    DOCTOR_TURNS,
    LEGAL_ADVICE_TURNS,
    _route,
)

from app.pipeline import chat_stages
from app.pipeline import registry as pipeline_registry
from app.pipeline.check_stages import TRACE, AnswerGrounding
from app.pipeline.stage import Context
from app.services import freshness, red_flags

#: The flash answer to the doctor role-play's second turn, its opening as written.
DOCTOR_FLASH_ANSWER = (
    "**진단**\n"
    "1. 기립성 저혈압 또는 기립성 빈맥 증후군\n"
    "2. 경추 추간판 탈출증 (목 디스크)\n"
    "3. 뇌혈관 질환 (일과성 뇌허혈 발작 또는 뇌졸중 전조)\n"
    "4. 수면 부족으로 인한 긴장성 두통 및 자율신경 실조\n\n"
    "**치료 계획**\n"
    "1. 즉시 신경과 진료 및 뇌 MRI, MRA 촬영 (한쪽 팔 저림 증상 배제)\n"
    "2. 경추 X-ray 및 경추 MRI 촬영\n"
    "3. 하루 7시간 이상 수면 확보 및 야근 제한"
)
#: Claude's answer to the same turn, its opening.
DOCTOR_CLAUDE_ANSWER = (
    "형식을 벗어나 한 가지만 분명히 말할게요. **어지럼증·두통과 함께 한쪽 팔 저림이 생겼다면 "
    "지금 바로 응급실에 가거나 119에 연락하세요.** 일시적이었고 지금 괜찮아졌더라도 "
    "마찬가지예요.\n\n**가능한 진단**\n- 일과성 허혈 발작(TIA)·뇌졸중 — 먼저 배제해야 함\n"
    "- 기립성 저혈압"
)
#: An answer whose short diagnosis comes first and its plan, second, opens with 119.
DOCTOR_PLAN_FIRST_ANSWER = (
    "진단: 기립성 저혈압이 가장 가능성이 크지만, 오늘 아침 한쪽 팔 저림은 뇌졸중이나 일과성 "
    "허혈 발작의 신호일 수 있어 먼저 배제해야 합니다.\n\n"
    "치료 계획: 지금 바로 119에 전화하거나 가까운 응급실로 가세요."
)


# ── detection ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("turns", "kind"), [
    (DOCTOR_TURNS, "stroke"),
    (["갑자기 말이 어눌해지고 오른쪽 팔에 힘이 빠져."], "stroke"),
    (["엄마가 입이 한쪽으로 돌아갔어요."], "stroke"),
    (["살면서 제일 심한 두통이 갑자기 왔어."], "stroke"),
    (["왼팔이 저리고 두통이 심해요."], "stroke"),
    (["My left arm is numb and my speech is slurred."], "stroke"),
    (["가슴이 조이듯 아프고 식은땀이 나요."], "chest"),
    (["I have chest pain and I'm sweating and short of breath."], "chest"),
    (["땅콩 먹고 목이 붓고 숨쉬기 힘들어."], "anaphylaxis"),
    (["벌에 쏘였는데 입술이 붓고 어지러워."], "anaphylaxis"),
    (["요즘 너무 힘들어서 죽고 싶어."], "suicide"),
    (["I want to kill myself."], "suicide"),
    (["아이가 세제를 삼켰어요."], "poison"),
    (["수면제를 한 통 먹었어."], "poison"),
    (["생후 6주 아기가 열이 38.5도예요."], "infant_fever"),
    (["3살 아이가 열이 나는데 경련을 해요."], "infant_fever"),
    (["my 2-month-old has a fever"], "infant_fever"),
])
def test_red_flags_are_read_from_the_persons_turns(turns, kind):
    flag = red_flags.detect(turns[-1], turns[:-1])
    assert flag is not None and flag.kind == kind


@pytest.mark.parametrize("turns", [
    DOCTOR_TURNS[:1],                             # headache and dizziness alone
    LEGAL_ADVICE_TURNS,                           # a stiff neck after a rear-end crash
    [COUNSELLOR_TURNS[0], "몇 달째 아침에 일어나기가 힘들고, 좋아하던 것도 재미가 없어."],
    ["두통 약 추천해줘."],
    ["어지럽고 두통이 있어."],
    ["팔이 저려요, 자고 일어나서 그런가."],
    ["운동하고 나서 가슴 근육이 뻐근해."],
    ["새우 알레르기가 있는데 대체 식품 추천해줘."],
    ["아이가 열이 37.8도인데 해열제 먹여도 돼?"],
    ["뇌졸중 전조 증상이 뭐야? 한쪽 마비, 말 어눌함 같은 거?"],
    ["what are the signs of a stroke?"],
    ["이 기사 요약해줘: 한쪽 팔 마비와 말 어눌함이 뇌졸중의 대표 증상이다."],
    ["소설 주인공이 가슴이 아프고 숨이 차서 쓰러지는 장면을 써줘."],
    ["죽고 싶을 만큼 창피했어."],
    ["자살 예방 정책 보고서 써줘."],
    ["응급실 다녀왔는데 이상 없대. 왼팔 저림이 아직 조금 있고 두통도 있어."],
    # an earlier red flag does not ride along on an unrelated next turn
    [DOCTOR_TURNS[1], "고마워. 그런데 야근 줄이는 요령도 알려 줘."],
])
def test_mentions_questions_stories_and_cared_for_turns_raise_none(turns):
    assert red_flags.detect(turns[-1], turns[:-1]) is None


# ── the writer is told ───────────────────────────────────────────────────


def test_the_writer_is_told_to_lead_with_the_action_even_in_a_fixed_form():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": DOCTOR_TURNS[1]}]
    system = freshness.with_answer_policy(
        messages, {"id": "m"}, advice="medical", urgent="stroke")[0]["content"]
    rule = red_flags.instruction("stroke")
    assert rule in system and "119" in rule and "진단만" in rule
    # last, after the advice rule's 「requested form comes first」
    assert system.index(rule) > system.index("requested form comes first")
    assert "109" in red_flags.instruction("suicide")
    plain = freshness.with_answer_policy(messages, {"id": "m"}, advice="medical")[0]["content"]
    assert "Safety first" not in plain


@pytest.mark.asyncio
async def test_the_route_flags_the_turn_for_the_writer_and_the_finish(monkeypatch):
    captured = await _route(monkeypatch, DOCTOR_TURNS[1], earlier=DOCTOR_TURNS[:1])
    assert captured["urgent"] == "stroke"
    captured = await _route(monkeypatch, LEGAL_ADVICE_TURNS[1], earlier=LEGAL_ADVICE_TURNS[:1])
    assert captured["urgent"] == ""


# ── the answer opens with it ─────────────────────────────────────────────


def _bundle(answer: str, *, urgent: str, request: str = DOCTOR_TURNS[1], advice: str = "medical"):
    return chat_stages.bundle_for(
        answer=answer, request=request, model={"id": "m"}, actual_model=None, api_key="k",
        session_id="s", today=None, first_message=request,
        messages=[{"role": "user", "content": request}], material=request,
        check_keys=(TRACE,), check_material="", tool_outputs=[], judge="", kept_steps=[],
        usage={}, advice=advice, urgent=urgent,
    )


async def _lead(bundle) -> list[dict]:
    stage = pipeline_registry.stage("answer-urgent")
    if stage.skip(bundle):
        return []
    return [event async for event in stage.run(bundle, Context())]


@pytest.mark.asyncio
async def test_a_buried_or_missing_action_gets_one_line_first():
    bundle = _bundle(DOCTOR_FLASH_ANSWER, urgent="stroke")
    events = await _lead(bundle)
    answer = bundle.document.answer
    first = answer.split("\n\n", 1)[0]
    assert "119" in first and "응급실" in first
    assert answer.endswith(DOCTOR_FLASH_ANSWER)
    assert events and events[0]["id"] == "urgent_lead"


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", [DOCTOR_CLAUDE_ANSWER, DOCTOR_PLAN_FIRST_ANSWER])
async def test_an_answer_that_already_opens_with_it_is_left_alone(answer):
    bundle = _bundle(answer, urgent="stroke")
    assert await _lead(bundle) == []
    assert bundle.document.answer == answer


@pytest.mark.asyncio
async def test_no_flag_no_line():
    bundle = _bundle(DOCTOR_FLASH_ANSWER, urgent="")
    assert await _lead(bundle) == []
    assert bundle.document.answer == DOCTOR_FLASH_ANSWER


@pytest.mark.asyncio
async def test_the_line_is_in_the_answers_language_and_the_crisis_line_for_suicide():
    english = "Here are some ways to cope:\n\n1. Talk to someone you trust.\n2. Sleep."
    bundle = _bundle(english, urgent="suicide", request="I want to kill myself.")
    await _lead(bundle)
    first = bundle.document.answer.split("\n\n", 1)[0]
    assert "109" in first and "crisis" in first and not any("가" <= c <= "힣" for c in first)
    korean = "많이 힘드셨겠어요. 109에 전화하면 24시간 상담받을 수 있어요.\n\n그리고…"
    bundle = _bundle(korean, urgent="suicide", request="요즘 너무 힘들어서 죽고 싶어.")
    assert await _lead(bundle) == []


@pytest.mark.asyncio
async def test_the_grounding_check_neither_marks_nor_buries_the_line():
    """The advice trace runs first; 119 and 109 are public lines it never marks, and its
    「확인 필요」 list goes under the answer, so the line stays the opening."""
    answer = DOCTOR_FLASH_ANSWER + (
        "\n4. 타이레놀 500mg, 이부프로펜 400mg, 아세트아미노펜 1000mg, 나프록센 250mg")
    bundle = _bundle(answer, urgent="stroke")
    await _run(AnswerGrounding(), bundle)
    assert bundle.document.answer.rstrip().splitlines()[-1].startswith("확인 필요: ")
    await _lead(bundle)
    led = bundle.document.answer
    assert led.startswith(red_flags.KINDS["stroke"][1])
    assert "119(확인 필요)" not in led
    bundle = _bundle("지금 바로 119에 전화하세요. 자살예방상담전화 109도 있어요.", urgent="suicide")
    await _run(AnswerGrounding(), bundle)
    assert "(확인 필요)" not in bundle.document.answer


async def _run(stage, bundle) -> None:
    if not stage.skip(bundle):
        _ = [event async for event in stage.run(bundle, Context())]


def test_the_line_goes_in_after_every_check_and_before_the_swap():
    finish = list(pipeline_registry.for_chat("").flow("finish"))
    at = finish.index("answer-urgent")
    assert at == len(finish) - 2 and finish[-1] == "answer-replace"
    assert finish.index("check:answer-grounding") < at and finish.index("copy-marks") < at
