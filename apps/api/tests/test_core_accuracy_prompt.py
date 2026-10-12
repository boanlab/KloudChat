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
    assert ("press releases, original reports and statistical tables of government and "
            "public agencies as primary evidence") in prompt
    assert ("media or university write-ups only as secondary evidence when the original "
            "cannot be found") in prompt
    assert "An agency's home page is not evidence for a specific claim" in prompt
    assert "search the user's claim verbatim in quotes" in prompt
    assert ("Do not add from memory any ratio, year, survey name or scale item that does "
            "not actually appear") in prompt
