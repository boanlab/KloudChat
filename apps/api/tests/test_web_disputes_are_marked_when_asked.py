"""r7 A07-T2 (DSP): the request asked unconfirmed points to be marked 「확인 필요」; three
sentences the web check found contradicted went out unmarked (listed in the step only)."""

from __future__ import annotations

from app.pipeline import report_stages
from app.services import verify

_A07_ASK = (
    "「공공기관의 민간 클라우드(SaaS) 이용 확대 정책 동향」을 조사해서 보고서로 정리해 줘. "
    "사실마다 출처를 달고, 확인 못 한 건 확인 필요로 표시해 줘."
)
_HELD = ("행안부는 2026년 10월 민간 클라우드 전환 예정 기관의 정보시스템 담당자를 대상으로 CSP별 "
         "서비스 설명회를 개최했습니다.")
_N2SF = ("이전 대상 선정 시에는 국가정보원의 국가망보안체계 가이드라인(N2SF)에 따른 정보시스템 "
         "보안등급(C·S·O)과 개편된 공공부문 정보시스템 등급(A1~A4) 등을 종합적으로 고려합니다.")


def _left(section: int, sentence: str) -> verify.Finding:
    return verify.Finding(section, sentence, "fact", "웹 자료와 어긋난다", auto=False)


def test_contradicted_sentences_are_marked_when_the_request_asks():
    sections = [{"heading": "정책 흐름", "content": f"{_HELD} [5] {_N2SF}"}]
    assert report_stages.mark_web_disputes(
        sections, [_left(0, _HELD), _left(0, _N2SF)], _A07_ASK) == 2
    text = sections[0]["content"]
    assert "설명회를 개최했습니다 (확인 필요)." in text
    assert "종합적으로 고려합니다 (확인 필요)." in text
    # once only
    assert report_stages.mark_web_disputes(sections, [_left(0, _HELD)], _A07_ASK) == 0


def test_unmarked_when_the_request_did_not_ask():
    sections = [{"heading": "정책 흐름", "content": _HELD}]
    assert report_stages.mark_web_disputes(
        sections, [_left(0, _HELD)], "공공 클라우드 정책 동향 보고서 써 줘") == 0
    assert sections[0]["content"] == _HELD
