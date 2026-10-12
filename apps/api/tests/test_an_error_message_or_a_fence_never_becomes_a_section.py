"""A model's tool error in place of a section (「required_context_missing / Required context is
missing. Provide the required_context argument …」) is written again, never shipped; prose
fenced as ```markdown is set as the body, not a code block."""

from __future__ import annotations

from app.services import report

ERROR = ("required_context_missing / Required context is missing. Provide the "
         "required_context argument (relevant source material or prior conversation) to "
         "continue.")
REQUEST = "「2026 학과 행사 추진 계획(안)」 써 줘. 소요 예산 표도 넣어 줘."


def test_an_error_message_is_not_a_section():
    assert report.error_text(ERROR, REQUEST)
    assert report.error_text(f"## 소요 예산\n\n{ERROR}\n", REQUEST)
    # Korean prose with English terms, a table, an English document: not an error
    assert not report.error_text(
        "소요 예산은 API 서버(tool server)와 GPU 대여비로 구성됩니다. 총 320만 원입니다.", REQUEST)
    assert not report.error_text(
        "| item | cost |\n|---|---|\n| GPU rental | 2,000,000 |\n\n예산 합계는 200만 원입니다.",
        REQUEST)
    assert not report.error_text(ERROR, "Write an incident report about a missing context error.")


def test_prose_fenced_as_markdown_is_the_body():
    body = ("```markdown\n- 추진 배경\n  - 학과 교류 확대 필요\n\n| 구분 | 내용 |\n|---|---|\n"
            "| 일시 | 11월 |\n```\n\n다음 문단.")
    out = report.unfence_pictures(body)
    assert "```" not in out and "- 추진 배경" in out and "| 일시 | 11월 |" in out
    tidied = report.final_tidy([{"heading": "추진 배경 및 목적", "content": body}])[0]["content"]
    assert "```" not in tidied
    code = "```python\nprint('x')\n```"
    assert report.unfence_pictures(code) == code


DRAFT = f"""## 추진 배경
학과 교류를 넓히려고 행사를 엽니다. 대상은 재학생 120명입니다.
## 소요 예산
{ERROR}
## 추진 일정
11월 둘째 주에 엽니다.
"""


async def test_a_section_written_as_an_error_is_written_again(monkeypatch):
    drafted: list[str] = []

    async def complete(model, messages, api_key, max_tokens, **kwargs):
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        if not drafted and all(h in prompt for h in ("추진 배경", "소요 예산", "추진 일정")):
            drafted.append(prompt)
            return DRAFT, {"inputTokens": 1, "outputTokens": 1}
        return "소요 예산은 다과비 30만 원, 대관료 20만 원으로 총 50만 원입니다.", {
            "inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report, "_complete", complete)
    events = [e async for e in report.write(
        request=REQUEST, model="m", api_key="k", web_search=False,
        approved_plan={"title": "2026 학과 행사 추진 계획(안)",
                       "sections": ["추진 배경", "소요 예산", "추진 일정"]},
    )]
    sections = next(e["sections"] for e in events if e["type"] == "report")
    budget = next(s for s in sections if s["heading"] == "소요 예산")
    assert "required_context" not in budget["content"]
    assert "Provide" not in "\n".join(str(s["content"]) for s in sections)
