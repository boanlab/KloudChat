"""The contact emails in a pasted paper's front matter are the paper's, not the person's data:
they no longer move a Super Agent document turn to the strict column (the 2026-11-17 benchmark
sent the paper tasks there on `personal_data`). Genuine personal data still does."""

from __future__ import annotations

from app.services import governance
from app.services import super_agent as sa

PAPER = (
    "[3] arXiv:2306.14048\n H2O: Heavy-Hitter Oracle for Efficient Generative Inference of "
    "Large Language Models Zhenyu Zhang1 , Ying Sheng2 , Tianyi Zhou3 , Lianmin Zheng4 , Zhao "
    "Song5 , Yuandong Tian6 1 University of Texas at Austin, 2 Stanford University, 3 "
    "University of California, San Diego, arXiv:2306.14048v3 [cs.LG] 18 Dec 2023 4 University "
    "of California, Berkeley, 5 Adobe Research, 6 Meta AI (FAIR) "
    "{zhenyu.zhang,tianlong.chen}@utexas.edu, ying1123@stanford.edu, t8zhou@ucsd.edu, "
    "lianminzheng@gmail.com, zsong@adobe.com, yuandong@meta.com Abstract Large Language Models")
ASK = "아래 논문 앞부분만 근거로 관련 연구 절을 학술 문체로 써 줘.\n\n"


def _personal(text: str, history: tuple[str, ...] = ()) -> list:
    found = governance.findings({"current_input": text, "conversation_history": list(history)})
    return sa.personal_for_routing(
        found, {"current_input": [text], "conversation_history": list(history)})


def test_the_detector_still_sees_the_emails_but_the_router_does_not_act_on_them() -> None:
    found = governance.findings({"current_input": ASK + PAPER, "conversation_history": []})
    assert [f.category for f in found] == ["email"]
    assert _personal(ASK + PAPER) == []


def test_a_persons_own_email_or_data_still_goes_strict() -> None:
    # the person's own address, even beside a paper
    own = ASK + PAPER + "\n\n결과는 kim.minsu@example.com 으로 보내 줘."
    assert [f.category for f in _personal(own)] == ["email"]
    # an email with no paper near it
    assert [f.category for f in _personal("연락처는 kim.minsu@example.com 입니다.")] == ["email"]
    # an address far from the paper's front matter
    far = "arXiv:2306.14048 " + "x " * 1000 + "kim@example.com"
    assert [f.category for f in _personal(far)] == ["email"]
    # other categories are never touched
    for text in (ASK + PAPER + "\n내 번호 +442071838750",):
        assert {f.category for f in _personal(text)} - {"email"}


def test_a_history_message_is_read_the_same_way() -> None:
    assert _personal("이어서 써 줘", (ASK + PAPER,)) == []
    assert [f.category for f in _personal("이어서 써 줘", ("내 메일 a.b@corp.co.kr",))] == [
        "email"]
