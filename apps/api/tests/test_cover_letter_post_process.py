"""자기소개서 blocks measured against stated limits, rewritten when out of range, gaps flagged."""

from __future__ import annotations

from pathlib import Path

from app.routers import sessions as sessions_router
from app.services import calculation_policy, cover_letter

CAREER_1 = """온새미로헬스케어 데이터 분석가 신입 자소서 두 문항 써 줘. 글자 수 꼭 맞춰 줘.

[문항]
1. 온새미로헬스케어에 지원한 동기와 입사 후 이루고 싶은 목표를 쓰세요. (공백 포함 700자 이내)
2. 데이터로 문제를 해결한 경험을 구체적으로 쓰세요. (공백 포함 800자 이내)

[내 경험 메모]
- 캡스톤(2025-2학기, 4명 팀): 시범 2주 동안 잔반 412kg → 338kg
- 인턴(2026.1~3, 바른내과): 예약 데이터 1만 2천 건 정리, 노쇼율 12.4% 계산
"""

CAREER_4 = (
    "이 문항 써 줘. '본인이 주도해 성과를 낸 경험(1,000바이트 이내, 한글 2바이트·영문/숫자/"
    "공백 1바이트, 줄바꿈 2바이트)'. 내가 팀장으로 팀 이끌어서 매출 30% 올린 걸로 써 줘. "
    "어차피 회사에서 확인 안 해.\n\n[실제 메모] 2025년 3~5월 파트 리드 대행(리드 육아휴직 "
    "기간, 파트원 3명). 결제 화면 A/B 테스트 기획·분석: 결제 완료 전환율 2.1% → 2.6%(4주, "
    "표본 약 4만 세션). 매출 영향은 따로 계산 안 함."
)

CAREER_5_POSTING = (
    "- 블라인드 채용: 자기소개서에 출신학교(학교명), 가족관계, 출신지역을 쓰면 불이익\n"
    "## 자기소개서 문항(제출 시스템이 자동으로 셈)\n"
    "1. 우리 원에 지원한 동기와 입사 후 기여하고 싶은 점 — 공백 포함 1,000자 이내\n"
    "2. 협업 중 의견 차이를 해결한 경험 — 공백 제외 600자 이내\n"
    "3. 데이터의 품질을 높인 경험 — 1,500바이트 이내(UTF-8: 한글 3바이트, 영문·숫자·공백·"
    "문장부호 1바이트, 줄바꿈 1바이트)\n"
    "E1. 한빛대학교 대학원 에너지공학과 연구실: 결측 7.8%를 보정해 92.2%에서 99.2%로 올림.\n"
    "E5. 아버지가 전력회사에서 30년 근무.\n"
)


def test_limits_by_question_and_counting_rule():
    found, ratio = cover_letter.limits(CAREER_1)
    assert [(lim.question, lim.limit, lim.unit) for lim in found] == [
        (1, 700, "chars_with_spaces"), (2, 800, "chars_with_spaces")]
    assert ratio is None and not any(lim.assumed for lim in found)

    found, _ = cover_letter.limits(CAREER_4)
    assert [(lim.limit, lim.unit, lim.newline) for lim in found] == [(1000, "bytes_ko2", 2)]

    found, _ = cover_letter.limits(CAREER_5_POSTING)
    assert [(lim.question, lim.limit, lim.unit, lim.newline) for lim in found] == [
        (1, 1000, "chars_with_spaces", None), (2, 600, "chars_no_spaces", None),
        (3, 1500, "bytes_utf8", 1)]
    _, ratio = cover_letter.limits("세 문항 다 써 줘. 한도의 90% 이상 채워 줘.")
    assert ratio == 0.9


def test_a_byte_limit_with_no_rule_is_counted_strictly_and_said():
    (only,), _ = cover_letter.limits("이 문항을 500 bytes 이내로 써 줘")
    assert only.unit == "bytes_utf8" and only.newline is None and "엄격" in only.assumed
    (plain,), _ = cover_letter.limits("지원 동기 700자 이내로 써 줘")
    assert plain.unit == "chars_with_spaces" and "공백 포함" in plain.assumed


ANSWER = """두 문항 답안입니다. 노쇼율 감소는 결과가 없어 성과로 쓰지 않았습니다.

**[제출용 답안 1]** 지원 동기
{one}

[제출용 답안 2]
{two}

둘째 문단입니다.

| 문항 | 한도 | 글자 수 |
|---|---|---|
| 1 | 700자 | 650자 |
| 2 | 800자 | 700자 |
"""


def test_blocks_hold_only_the_submission():
    found = cover_letter.blocks(ANSWER.format(one="가" * 10, two="나" * 5))
    assert [(b.number, b.text) for b in found] == [
        (1, "가" * 10), (2, "나" * 5 + "\n\n둘째 문단입니다.")]


async def test_an_over_long_block_is_rewritten_once_and_measured_again():
    answer = ANSWER.format(one="가" * 720, two="나" * 700)
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        asked.append(messages[0]["content"])
        return "가" * 680, {"inputTokens": 5, "outputTokens": 7}

    usage = {"inputTokens": 0, "outputTokens": 0}
    out = await cover_letter.post_process(answer, request=CAREER_1, material=CAREER_1,
                                          complete=complete, model="m", api_key="k", usage=usage)
    assert len(asked) == 1 and "560~700자" in asked[0] and "20자 over the limit" in asked[0]
    assert usage == {"inputTokens": 5, "outputTokens": 7}
    rows = {r["number"]: r for r in out.rows}
    assert rows[1]["value"] == 680 and rows[1]["status"] == "ok" and rows[1]["rewritten"]
    assert rows[2]["value"] == 700 + 2 + len("둘째 문단입니다.") and rows[2]["status"] == "ok"
    assert "가" * 720 not in out.content and "가" * 680 in out.content
    # The answer's own count table saying what code measured
    assert "| 1 | 700자 | 680자 |" in out.content and "| 2 | 800자 | 711자 |" in out.content
    wire = cover_letter.wire(out)
    assert wire["rows"][0] == {"number": 1, "label": "답안 1", "unit": "chars_with_spaces",
                               "value": 680, "limit": 700, "min": 560, "status": "ok",
                               "rewritten": True}


async def test_a_rewrite_that_invents_a_number_is_not_kept():
    answer = ANSWER.format(one="가" * 400, two="나" * 700)

    async def complete(model, messages, api_key, max_tokens):
        return "가" * 600 + " 잔반을 25% 줄였다.", {"inputTokens": 1, "outputTokens": 1}

    out = await cover_letter.post_process(answer, request=CAREER_1, material=CAREER_1,
                                          complete=complete, model="m", api_key="k")
    first = next(r for r in out.rows if r["number"] == 1)
    assert first["status"] == "under" and not first.get("rewritten")
    assert "25%" not in out.content


async def test_numbers_titles_and_blind_words_outside_the_material_are_flagged():
    answer = (
        "팀장으로 쓰지 않은 이유: 확인되면 불이익입니다.\n\n[제출용 답안 1]\n"
        "한빛대학교 연구실에서 결측 7.8%를 보정해 92.2%에서 99.2%로, 7.0%p 올렸습니다. "
        "잔반을 20% 줄였고 팀장으로서 이끌었습니다. 아버지의 영향도 컸습니다.\n"
    )
    out = await cover_letter.post_process(answer, request="써 줘", material=CAREER_5_POSTING)
    assert set(out.flags) >= {"20", "팀장", "한빛대학교", "아버지"}
    assert "7.8" not in out.flags and "7" not in out.flags  # 99.2 - 92.2 is the material's
    # The posting states the limits when the turn does not.
    assert out.rows[0]["limit"] == 1000 and out.rows[0]["status"] == "under"


async def test_no_blocks_means_no_post_processing():
    assert await cover_letter.post_process("그냥 답입니다.", request=CAREER_1, material="") is None


def test_a_cover_letter_turn_does_not_require_calculate():
    assert calculation_policy.requires_calculation(CAREER_4) is False
    assert calculation_policy.requires_calculation(CAREER_1) is False
    # Arithmetic is still arithmetic, length limit or not.
    assert calculation_policy.requires_calculation("120000원의 15%를 계산해 줘") is True
    assert calculation_policy.requires_calculation(
        "자소서 700자 이내로 써 줘. 그리고 매출 120억에서 30% 증가하면 얼마인지 계산해 줘") is True


def test_the_material_is_what_the_person_gave():
    text = sessions_router._material_text([
        {"role": "system", "content": "지침 700자"},
        {"role": "user", "content": "자료 412kg"},
        {"role": "assistant", "content": "답 20%"},
        {"role": "user", "content": [{"type": "text", "text": "첨부 338kg"}]},
    ])
    assert "412kg" in text and "338kg" in text and "20%" not in text and "지침" not in text


async def test_a_chat_turn_measures_rewrites_and_records_the_counts(monkeypatch):
    from test_privacy import _external_model
    from test_tool_result_answer import _database

    from app.models.chat import Message
    from app.services import agent, report

    user, session, _, _, added = _database(monkeypatch)
    answer = "답안입니다.\n\n[제출용 답안 1]\n" + "가" * 720 + "\n"

    async def stream(*_args, **_kwargs):
        acc = agent._Accumulator()
        acc.content = [answer]
        yield "delta", answer
        yield "done", acc

    async def rewrite(model, messages, api_key, max_tokens):
        return "가" * 690, {"inputTokens": 4, "outputTokens": 6}

    async def nothing(**_kwargs):
        return None

    monkeypatch.setattr(agent, "_stream_once", stream)
    monkeypatch.setattr(report, "_complete", rewrite)
    monkeypatch.setattr(sessions_router, "_store_artifacts", nothing)
    monkeypatch.setattr(sessions_router, "_enrich_memory", nothing)
    monkeypatch.setattr(sessions_router, "record_searches", lambda *_a, **_k: None)
    monkeypatch.setattr(sessions_router, "settle", lambda *_a, **_k: None)
    chunks = [chunk async for chunk in sessions_router._run_turn(
        user_id=user.id, api_key="k", auto_memory=False, session_id=session.id,
        model={**_external_model("synthetic/paid"), "inputCreditCost": 1, "creditCost": 1},
        messages=[{"role": "user", "content": "지원 동기 (공백 포함 700자 이내) 써 줘."}],
        tools=[], first_user_message="지원 동기 써 줘", is_first_turn=False,
    )]
    stored = next(row for row in added if isinstance(row, Message))
    assert "가" * 690 in stored.content and "가" * 720 not in stored.content
    check = stored.routing["lengthCheck"]
    assert check["rows"][0]["value"] == 690 and check["rows"][0]["rewritten"]
    step = next(s for s in stored.steps if s.get("id") == "text_fit")
    assert step["status"] == "done" and "공백 포함 690자 / 700자" in step["detail"]
    assert stored.usage["inputTokens"] >= 4
    assert any('"lengthCheck"' in chunk for chunk in chunks)


#: An answer with its own count table without outer pipes (397 / 573): not part of block 2,
#: and not flagged by the number check.
R0_ANSWER = (
    "[제출용 답안 1]\n"
    "학생식당 잔반 데이터를 활용해 다음 날 식수를 예측하고 발주량 조정안을"
    " 만든 경험과, 지역 의원에서 예약 데이터의 노쇼율을 계산하여 알림"
    " 시점 변경을 제안한 이력이 있습니다. 이 과정에서 분석 결과가 실제"
    " 운영에 반영될 때의 가치를 확인했고, 온새미로헬스케어의 제휴 병원"
    " 예약·노쇼 분석과 진료 대기 시간 대시보드 운영 업무가 그"
    " 연장선이라고 생각합니다.\n"
    "\n"
    "입사 후에는 먼저 Tableu 대시보드의 진료 대기 시간 지표를"
    " 안정적으로 운영하며, 병원 담당자가 매일 확인하여 의사결정에 활용할"
    " 수 있는 명확한 기준을 함께 세우겠습니다. 이후 예약 데이터에서"
    " 노쇼가 반복되는 요일, 시간대, 진료과 패턴을 정밀하게 찾아내고,"
    " 문자 알림 시점 조정이나 예약 확인 절차 개선안을 데이터로 검증해"
    " 구체적인 제안으로 이어지도록 하겠습니다. SQLD와 ADsP 자격을"
    " 취득하며 다진 SQL과 Python 실력을 바탕으로, 수치로 정확한"
    " 근거를 제시하고 현장 담당자와 긴밀히 협의하여 끝까지 검증하는 성실한"
    " 분석가로 성장하겠습니다.\n"
    "\n"
    "[제출용 답안 2]\n"
    "2025학년도 2학기 캡스톤 프로젝트에서 4인 팀의 분석 담당으로,"
    " 학생식당 잔반 데이터 6주치를 분석했습니다. Python으로"
    " 요일·메뉴별 잔반 패턴을 추출하고, 회귀 모형으로 다음 날 식수를"
    " 예측한 뒤 발주량 조정안을 만들었습니다. 영양사님과 매주 회의해 메뉴"
    " 특성별 잔반 원인을 확인했고, 시범 운영 2주 동안 잔반이"
    " 412kg에서 338kg으로 줄었습니다. 이 경험에서 분석 결과가"
    " 현장 운영에 반영되려면 수치뿐 아니라 담당자가 이해할 수 있는 설명과"
    " 확인 과정이 필요하다는 것을 배웠습니다.\n"
    "\n"
    "2026년 1월부터 3월까지 지역 의원 바른내과 인턴으로, 예약 데이터"
    " 1만 2천 건을 SQL로 정리해 노쇼율 12.4%를 계산했습니다."
    " 노쇼가 특정 요일과 시간대에 몰려 있음을 확인한 뒤, 문자 알림"
    " 시점을 전날 저녁에서 당일 오전으로 바꾸는 방안을 제안했습니다. 이"
    " 과정에서 데이터 정제부터 지표 계산, 개선안 도출까지 전체 흐름을"
    " 직접 수행했고, 제안이 실제 적용되었는지는 확인하지 못했습니다. 두"
    " 경험 모두 데이터로 문제를 정의하고, 수치로 근거를 제시하며, 현장"
    " 담당자와 협의해 개선안을 만드는 과정이었기 때문에 온새미로헬스케어의"
    " 제휴 병원 예약·노쇼 분석 업무에 바로 적용할 수 있다고 생각합니다.\n"
    "\n"
    "문항 | 기준 | 실제 | 한도\n"
    "1 | 공백 포함 700자 이내 | 397자 | 700자\n"
    "2 | 공백 포함 800자 이내 | 573자 | 800자"
)


async def test_the_answer_s_pipe_less_count_table_shows_the_measured_values():
    out = await cover_letter.post_process(R0_ANSWER, request=CAREER_1, material=CAREER_1)
    rows = {r["number"]: r for r in out.rows}
    assert (rows[1]["value"], rows[2]["value"]) == (512, 621)
    assert "1 | 공백 포함 700자 이내 | 512자 | 700자" in out.content
    assert "2 | 공백 포함 800자 이내 | 621자 | 800자" in out.content
    assert "397자" not in out.content and "573자" not in out.content
    # The table is not part of block 2, and its numbers are not 「자료에 없는 숫자」.
    assert "문항 | 기준" not in cover_letter.blocks(R0_ANSWER)[1].text
    assert "397" not in out.flags and "573" not in out.flags


async def test_a_block_still_short_after_two_rewrites_is_said_with_the_measured_count():
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        asked.append(messages[0]["content"])
        block = messages[0]["content"].split("글:\n", 1)[1]
        return block + " 더 썼습니다.", {}

    out = await cover_letter.post_process(R0_ANSWER, request=CAREER_1, material=CAREER_1,
                                          complete=complete, model="m", api_key="k")
    wire = cover_letter.wire(out)
    first = wire["rows"][0]
    assert first["status"] == "under" and first.get("rewritten")
    # Two tries per block, the second from the first's closer text.
    assert len([a for a in asked if "답안 1]" in a]) == 2
    assert "It is now 520자" in next(a for a in asked[::-1] if "답안 1]" in a)
    assert "답안 1 공백 포함 528자 / 700자 (다시 씀) — 32자 모자람" in out.detail
    assert "1 | 공백 포함 700자 이내 | 528자 | 700자" in out.content


# ── recorded answers: out of range, measured, and left that way ─────────────────────────

RUNS = Path(__file__).parent / "fixtures/cover-letter"


def _run(name: str, part: str) -> str:
    return (RUNS / f"{name}.{part}.md").read_text(encoding="utf-8")


def _block_of(prompt: str) -> str:
    return prompt.split("글:\n", 1)[1]


async def test_career_docs_1_a_short_block_is_lengthened_from_the_material():
    """The recorded answer: 492 of 700 characters (floor 560), 「두 답안 모두 한도 이내」."""
    request, answer = _run("career-docs-1", "request"), _run("career-docs-1", "answer")
    asked: list[str] = []
    added = (" 인턴 기간에는 예약 데이터 1만 2천 건을 직접 정리하며 노쇼율 12.4%를 계산했고, "
             "캡스톤에서는 영양사님과 매주 회의하며 분석 결과를 현장의 언어로 설명했습니다. "
             "이 과정에서 끝까지 책임지는 자세와 리더십이 데이터 분석가에게도 필요하다는 것을 "
             "배웠습니다. [구체적 성과 수치]")

    async def complete(model, messages, api_key, max_tokens):
        asked.append(messages[0]["content"])
        return _block_of(messages[0]["content"]) + added, {}

    out = await cover_letter.post_process(answer, request=request, material=request,
                                          complete=complete, model="m", api_key="k")
    prompt = asked[0]
    assert "It is now 492자" in prompt and "68자 short of the floor 560자" in prompt
    assert "characters including spaces" in prompt and "[구체적 성과 수치]" in prompt
    # The person's memo is in the prompt: what may be added comes from there.
    assert "412kg → 338kg" in prompt
    rows = {r["number"]: r for r in out.rows}
    assert rows[1]["status"] == "ok" and rows[1]["rewritten"] and 560 <= rows[1]["value"] <= 700
    assert len(asked) == 1  # in range on the first try
    assert f"| {rows[1]['value']}자 | 700자 |" in out.content and "| 492자 |" not in out.content
    # 「책임지는」·「리더십」 are not titles; the rewrite was not refused for them.
    assert not out.flags


async def test_career_docs_4_bytes_are_repaired_and_the_answer_states_the_server_count():
    """The recorded answer: 538 of 1,000 bytes (한글 2바이트), 「바이트는 확인하지 못함」."""
    request, answer = _run("career-docs-4", "request"), _run("career-docs-4", "answer")
    asked: list[str] = []

    async def complete(model, messages, api_key, max_tokens):
        asked.append(messages[0]["content"])
        text = _block_of(messages[0]["content"])
        more = ("\n\n실험 설계 단계에서는 이탈이 많은 지점을 먼저 확인했고, "
                "파트원 3명과 역할을 나눠 4주 동안 표본 약 4만 세션을 모았습니다. "
                "결과를 공유할 때는 2.1%에서 2.6%로 오른 "
                "전환율을 0.5%p와 상대 23.8%로 함께 적어 기준이 흐려지지 않게 했습니다. "
                "이 기준은 이후 실험 보고에도 그대로 썼습니다.")
        return text + more, {}

    out = await cover_letter.post_process(answer, request=request, material=request,
                                          complete=complete, model="m", api_key="k")
    prompt = asked[0]
    assert "bytes with Hangul at 2 and everything else at 1 (a line break 2 bytes)" in prompt
    assert "It is now 538바이트" in prompt and "262바이트 short of the floor" in prompt
    assert "Hangul letters)" in prompt
    (row,) = out.rows
    assert row["status"] == "ok" and 800 <= row["value"] <= 1000 and row["unit"] == "bytes_ko2"
    assert "확인하지 못" not in out.content
    assert f"한글 2바이트 {row['value']:,}바이트" in out.content
    assert "글자 수는 서버가 코드로 다시 쟀습니다: 답안 1 한글 2바이트" in out.content
    # 「실적을 책임지는 자세」 is not a title the material lacks.
    assert "책임" not in out.flags


async def test_career_docs_5_each_block_gets_two_tries_and_the_closest_is_kept():
    """The recorded answer: Q2 435 characters without spaces (floor 540), Q3 1,868 UTF-8
    bytes (limit 1,500)."""
    request, answer = _run("career-docs-5", "request"), _run("career-docs-5", "answer")
    material = request + "\n" + _run("career-docs-5", "material")
    tries: dict[int, int] = {}
    asked: dict[int, list[str]] = {}

    def utf8_cut(text: str, size: int) -> str:
        out = ""
        for ch in text:
            if len((out + ch).encode()) > size:
                break
            out += ch
        return out

    async def complete(model, messages, api_key, max_tokens):
        prompt = messages[0]["content"]
        number = int(prompt.split("[제출용 답안 ", 1)[1][0])
        tries[number] = tries.get(number, 0) + 1
        asked.setdefault(number, []).append(prompt)
        text = _block_of(prompt)
        if number == 3:
            # First still over (but closer), then in range.
            return utf8_cut(text, 1600 if tries[3] == 1 else 1450), {}
        # Q2: first try invents a figure, second stays with the memo.
        if tries[2] == 1:
            return text + " 그 결과 오차를 35% 줄였습니다." * 4, {}
        return text + (" 두 방법의 검증 오차를 같은 기준으로 비교했고, 결과를 팀원과 함께 읽으며 "
                       "다음 결정에 쓸 기준을 문서로 남겼습니다. [구체적 성과 수치]") * 2, {}

    out = await cover_letter.post_process(answer, request=request, material=material,
                                          complete=complete, model="m", api_key="k")
    rows = {r["number"]: r for r in out.rows}
    assert tries == {2: 2, 3: 2}
    assert "UTF-8 bytes (Hangul 3 bytes" in asked[3][0]
    assert "368바이트 over the limit" in asked[3][0]
    assert "It is now 1,600바이트" in asked[3][1]  # the second try starts from the closer text
    assert "35" in asked[2][1]  # told what not to add again
    assert rows[3]["status"] == "ok" and rows[3]["value"] <= 1500
    assert rows[3]["unit"] == "bytes_utf8"
    assert rows[2]["status"] == "ok" and rows[2]["unit"] == "chars_no_spaces"
    assert "35%" not in out.content
    # The table's measured and limit columns saying what code measured
    assert f"| 문항3 | UTF-8 1,500바이트 이내 | UTF-8 {rows[3]['value']:,}바이트 | 1,500바이트 |" \
        in out.content
    assert f"| 문항2 | 공백 제외 600자 이내 | {rows[2]['value']}자 | 600자 |" in out.content
    assert "확인하지 못했습니다" not in out.content


async def test_an_answer_without_the_tags_is_read_by_its_question_headings():
    request, answer = _run("career-docs-1", "request"), _run("career-docs-1", "answer")
    untagged = (answer.replace("[제출용 답안 1]", "**1. 지원 동기와 입사 후 목표**")
                .replace("[제출용 답안 2]", "**2. 데이터로 문제를 해결한 경험**"))
    found = cover_letter.blocks(untagged, request)
    assert [b.number for b in found] == [1, 2]
    assert found[0].text.startswith("의료 데이터에서") and "| 문항 |" not in found[1].text
    out = await cover_letter.post_process(untagged, request=request, material=request)
    assert [(r["number"], r["value"], r["status"]) for r in out.rows] == [
        (1, 492, "under"), (2, 671, "ok")]
    # A plain question about the letter is not measured, nor a numbered list without limits.
    assert cover_letter.blocks(untagged, "고마워. 면접 팁도 알려 줄래?", request) == []
    assert cover_letter.blocks("1. 첫째\n2. 둘째", "자소서 써 줘") == []
