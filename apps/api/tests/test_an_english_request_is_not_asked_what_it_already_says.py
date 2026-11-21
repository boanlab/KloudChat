"""A pre-writing question with a Korean-word detector goes to the judge for an English request.

The training deck pattern asks 「누가 듣는 교육인가요?」 unless the request has one of
「신입|대상|부서 …」. An English all-hands deck request never has those words, so it was
always asked."""

from __future__ import annotations

from app.services import gap_questions

LEARNERS = {"id": "learners", "label": "대상", "question": "누가 듣는 교육인가요? (부서·경력)",
            "detector": {"type": "regex", "pattern": "(?:신입|대상|부서|팀|직원)"}}
MEETING = {"id": "meeting_date", "label": "회의 날짜", "question": "회의는 언제였나요?",
           "detector": {"type": "builtin", "name": "meeting_date"}}
ENGLISH = ("Create a 6-8 slide deck in English for our internal Security Awareness Month kickoff "
           "(all-hands, 10 minutes). Cover why it matters and what we ask everyone to do.")


def test_an_english_request_has_its_korean_word_detectors_judged():
    asked = gap_questions.in_language([LEARNERS, MEETING], ENGLISH)
    assert asked[0]["detector"] == {"type": "judge"}
    assert asked[1]["detector"] == MEETING["detector"]
    assert gap_questions.needs_judge(asked)


def test_a_korean_request_keeps_its_detectors():
    korean = "신입사원 입문 교육용 개인정보보호 발표 자료를 10장으로 만들어 줘."
    assert gap_questions.in_language([LEARNERS], korean) == [LEARNERS]


async def test_without_a_judge_model_nothing_is_asked():
    asked = gap_questions.in_language([LEARNERS], ENGLISH)

    async def complete(*args, **kwargs):
        raise AssertionError("no model was given")

    assert await gap_questions.judged(asked, request=ENGLISH, material="", complete=complete,
                                      model="", api_key="") == {"learners": True}
