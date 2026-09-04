from app.models.chat import SessionKind
from app.services.context import build_messages, requests_web_search, system_prompt


def test_core_accuracy_contract_is_shared_by_every_surface():
    for kind in (SessionKind.chat, SessionKind.report, SessionKind.slides):
        prompt = system_prompt(kind)
        assert "Keep actors and actions separate" in prompt
        assert "Missing source facts are not blanks" in prompt
        assert "Never cite a source that was not present" in prompt
        assert "Do not invent internal approvals" in prompt


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
