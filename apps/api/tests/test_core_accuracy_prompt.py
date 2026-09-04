from app.models.chat import SessionKind
from app.services.context import build_messages, requests_web_search, system_prompt


def test_core_accuracy_contract_is_shared_by_every_surface():
    for kind in (SessionKind.chat, SessionKind.report, SessionKind.slides):
        prompt = system_prompt(kind)
        assert "Keep actors and actions separate" in prompt
        assert "Missing source facts are not blanks" in prompt
        assert "Never cite a source that was not present" in prompt
        assert "Do not invent internal approvals" in prompt
        assert "Do not describe it as a way for the buyer to delay" in prompt
        assert "remove repeated paragraphs" in prompt


def test_latest_request_language_rule_remains_last_and_explicit():
    messages = build_messages(
        SessionKind.chat,
        [
            {
                "role": "user",
                "content": (
                    "Explain reverse-issued tax invoices in plain English for a new "
                    "accountant."
                ),
            }
        ],
    )
    prompt = messages[0]["content"]
    assert "write the entire answer in English" in prompt
    assert prompt.index("Core accuracy contract") < prompt.index(
        "write the entire answer in English"
    )


def test_only_explicit_research_language_implies_web_search():
    assert requests_web_search('40%라는 보도를 검증해 주세요')
    assert requests_web_search('출처를 찾아 비교해 주세요')
    assert requests_web_search('Please fact-check this claim')
    assert not requests_web_search('이 문장을 확인해 주세요')
    assert not requests_web_search('이 개념을 알려 주세요')


def test_search_contract_prefers_the_issuing_agencys_original_material():
    prompt = system_prompt(SessionKind.chat, web_search=True, web_search_available=True)
    assert "정부·공공기관의 보도자료, 원문 보고서, 통계표를 1차 근거" in prompt
    assert "언론·대학 소개 글은 원문을 찾지 못했을 때의 보조 근거" in prompt
    assert "기관 홈페이지 첫 화면은 특정 주장의 근거가 아닙니다" in prompt
    assert "사용자가 제시한 주장을 따옴표로 그대로 검색" in prompt
    assert "나오지 않은 비율, 연도, 조사명, 척도 문항은 기억으로 보태지 마세요" in prompt
