"""What the person copies carries no check noise (round 11, CHAT-02·03, DOCS-20): their own values
in another notation are grounded, the checks' own talk and the narration between tool calls go,
and a slide's notes say nothing of how they were written."""

from __future__ import annotations

import datetime as dt

from app.services import check_skills, deck, internal_words, value_guard

PROOFREAD = ("Please proofread the English paragraph below, show me what you changed and why "
             "(briefly), and then translate the corrected version into natural Korean for a "
             "company newsletter.\n\n\"Our team have been working on the new onboarding flow "
             "since last month, and we are happy to announce that it will be launch on November "
             "3rd. The new flow reduce the number of steps from nine to five, and early test show "
             "that users completes it in about half the time.\"")

TRANSLATED = ("**Corrected version**\n\n> Our team has been working on the new onboarding flow "
              "since last month, and it will launch on November 3rd.\n\n"
              "| Original | Corrected | Why |\n|---|---|---|\n"
              "| it will **be launch** | it will **launch** | After \"will\" you need the base "
              "verb. |\n\n"
              "The automated check flagged the \"thanks → thank\" line as a scope change, but "
              "that is just word-level alignment noise — the numbers, dates, and quantities "
              "(nine to five, November 3rd, half the time) are all unchanged.\n\n"
              "**Korean translation for the company newsletter**\n\n"
              "> 오는 11월 3일에 새 절차를 정식 오픈할 예정입니다. 바뀐 절차는 단계를 9단계에서 "
              "5단계로 줄였습니다.")

#: what the code printed for return rates, as fractions
PRINTED = ("지점          매출  판매수량  반품수량      반품률\n"
           "강남 210905000.0  3360   108 0.032143\n광주 117311000.0  1668    58 0.034772\n"
           "대전 104319000.0  1490    51 0.034228\n")


def _ground(text: str, *, material: str = "", computed: tuple[str, ...] = (),
            asked: str = "") -> check_skills.Outcome:
    options = {**check_skills.options_for(dt.date(2026, 10, 19), surface="chat"),
               "person": "\n".join([material, *computed]), "requirements": True}
    return check_skills.ground(("grounding-check",), text, material=material,
                               computed=list(computed), options=options, evidence=[asked])


def test_the_persons_own_values_in_another_notation_are_grounded():
    ev = value_guard.Evidence.of([PROOFREAD, PRINTED, "Revenue reached 1.2 million dollars."])
    # 「November 3rd」 → 11월 3일, 「nine to five」 → 9·5
    assert ev.supports_day(None, 11, 3)
    assert ev.supports_number(9, "", "9") and ev.supports_number(5, "", "5")
    # 「1.2 million」 → 120만, a printed fraction → its percent
    assert ev.supports_number(1_200_000, "", "120만")
    assert ev.supports_number(3.5, "%", "3.5")
    # a range of shares is no pair of days
    assert value_guard._values("반품률은 3.2~3.5%입니다") == [(3.2, ""), (3.5, "%")]


def test_a_translation_of_the_persons_date_is_not_marked():
    traced = _ground(TRANSLATED, asked=PROOFREAD)
    assert value_guard.MARK not in traced.text
    assert traced.marked == 0


def test_a_share_the_code_printed_as_a_fraction_is_not_marked():
    traced = _ground("반품률은 지점 간 3.2~3.5%로 차이가 없습니다.", computed=(PRINTED,),
                     asked="첨부한 매출 데이터를 요약해 줘")
    assert value_guard.MARK not in traced.text


def test_the_checks_own_talk_never_reaches_the_answer_in_any_language():
    cleaned = internal_words.strip_work_notes(TRANSLATED, request=PROOFREAD)
    assert "automated check" not in cleaned and "alignment noise" not in cleaned
    assert "> 오는 11월 3일에" in cleaned and "| it will **launch** |" in cleaned
    # an English sentence beside it stays
    mixed = ("The tone is formal. The automated check flagged two lines, but both are fine. "
             "Use it as is.")
    assert internal_words.strip_work_notes(mixed, request="Proofread this.") == (
        "The tone is formal. Use it as is.")
    korean = ("교정한 문장은 아래와 같습니다. 자동 점검이 「11월 3일」을 확인 필요로 표시했지만 "
              "원문 날짜 그대로입니다.\n\n> 오는 11월 3일에 새 절차를 엽니다.")
    assert "자동 점검" not in internal_words.strip_work_notes(korean, request="교정해 줘")
    # a medical or code check the answer is about stays
    about = "혈액 검사에서 수치가 높게 잡혔다면 재검을 받으세요."
    assert internal_words.strip_work_notes(about, request="검사 결과 봐 줘") == about


BUSAN = ("부모님 모시고 부산 2박 3일이면 **해운대 한 구역에 머물면서 택시로만 움직이는 것**이 "
         "정답입니다. 지하철 환승이나 오르막 산책로를 빼면 걷는 거리를 크게 줄일 수 있습니다. "
         "바다 조망과 맛집을 한 블록 안에서 해결할 수 있습니다.\n\n"
         "## 이동 전략: 해운대 단일 거점 + 택시\n\n"
         + "부산역에서 해운대까지 택시로 이동합니다. " * 20)


def test_narration_glued_across_tool_calls_does_not_open_the_answer():
    glued = ("부모님 모시고 가시면 동선이 핵심이라서, 현재 부산 주요 명소들의 엘리베이터·경사로 "
             "유무와 바다 조망 명소를 먼저 확인해 보겠습니다.바다 조망 명소의 접근 설비와 KTX "
             "요금·숙박 시세를 확인하겠습니다.\n\n")
    assert internal_words.strip_tool_preamble(glued + BUSAN) == BUSAN
    # an opening that answers, or carries a figure, is the answer's
    assert internal_words.strip_tool_preamble(BUSAN) == BUSAN
    found = "KTX 편도 요금은 59,800원으로 확인했습니다.\n\n"
    assert internal_words.strip_tool_preamble(found + BUSAN) == found + BUSAN


def test_slide_notes_say_nothing_of_how_they_were_written():
    slides = [
        {"layout": "title", "title": "2026년 3분기 사업 실적 및 4분기 계획 보고"},
        {"layout": "agenda", "title": "목차", "bullets": ["실적", "이슈", "계획", "요청"]},
        {"layout": "metrics", "title": "3분기 핵심 실적", "metrics": [["매출", "128억"]],
         "notes": "3분기는 매출과 이익률 모두 개선되었습니다."},
        {"layout": "bullets", "title": "4분기 목표", "bullets": ["매출 140억"],
         "notes": "이를 위해 다음 장의 예산이 필요합니다. 사업부장의 보고 톤에 맞춰 간결하고 "
                  "명확하게 두 문장으로 구성했습니다."},
        {"layout": "statement", "title": "예산 1.5억", "body": "승인을 요청드립니다.",
         "notes": "영남 물류 안정화 예산 1.5억 원의 승인을 요청드립니다."},
    ]
    deck.strip_internal_text(slides)
    assert slides[3]["notes"] == "이를 위해 다음 장의 예산이 필요합니다."
    # notes on most slides, none asked: the opening slides are not left silent
    assert deck.fill_missing_notes(slides, "임원회의에 보고할 슬라이드 만들어줘") == [0, 1]
    assert slides[0]["notes"].startswith("안녕하세요.")
    assert "순서로 진행하겠습니다" in slides[1]["notes"]
