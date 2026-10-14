"""r7 A04-T2 (GLM-5.3): a section call thought through its 1,200 tokens, the one re-ask (3,100)
thought through too, every section failed 「empty section」, and the empty document went on
through every check to be saved as 「1개 절로 비교·분석 보고서 작성을 마쳤습니다」."""

from __future__ import annotations

from test_a_thinking_model_still_answers import payload
from test_documents_are_researched import _Client as _Canned

from app.pipeline import report_stages
from app.services import report, thinking


class _Client:
    """Answers each post with the next canned payload and records max_tokens."""

    def __init__(self, replies: list[dict], asked: list[int], **_):
        self._replies, self._asked = replies, asked

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, _url, json):
        self._asked.append(json["max_tokens"])
        body = self._replies.pop(0)

        class R:
            status_code = 200
            text = ""
            headers: dict = {}

            def raise_for_status(self):
                return None

            @staticmethod
            def json():
                return body

        return R()


async def test_a_second_starved_answer_is_asked_with_more_room(monkeypatch):
    async def litellm_config():
        return "http://mock-litellm", "unused"

    monkeypatch.setattr(report.settings_store, "litellm_config", litellm_config)
    asked: list[int] = []
    replies = [payload(finish="length", content="", reasoning=1200),
               payload(finish="length", content="", reasoning=3100),
               payload(finish="stop", content="비용 계산의 전제는 다음과 같습니다.",
                       completion=900)]
    monkeypatch.setattr(report.httpx, "AsyncClient", lambda **kw: _Client(replies, asked, **kw))
    text, _ = await report._complete("z-ai/glm-5.3", [{"role": "user", "content": "q"}], "k",
                                     1200)
    assert text == "비용 계산의 전제는 다음과 같습니다."
    assert asked[0] == 1200 and asked[1] > 1200 + 1200 and asked[2] > asked[1]


def test_a_model_that_must_think_gets_room_for_its_answer(monkeypatch):
    class Refusal:
        status_code = 400
        text = '{"error": {"message": "Reasoning is mandatory for this endpoint"}}'

    model = "z-ai/glm-5.3-test"
    assert thinking.room(model, 1200) == 1200
    assert thinking.refused(model, Refusal())
    assert thinking.mandatory(model) and thinking.room(model, 1200) >= 1200 + 4000
    # a starved re-ask carries the answer's whole budget on top of what was thought
    assert thinking.starved(payload(finish="length", content="", reasoning=1200), 1200) >= 3600


async def test_a_section_no_call_could_write_says_so(monkeypatch):
    async def litellm_config():
        return "http://mock-litellm", "unused"

    async def unavailable():
        return False

    asked: list[int] = []

    async def complete(_model, messages, _key, max_tokens):
        asked.append(max_tokens)
        prompt = "\n".join(str(m.get("content") or "") for m in messages)
        if 'writing only the "비용 계산의 전제" section' in prompt:
            return "", {"inputTokens": 1, "outputTokens": 1}
        if "writing only the" not in prompt:
            # the one-call draft: nothing, so every section is written on its own
            return "", {"inputTokens": 1, "outputTokens": 1}
        return "세 업체 모두 예산 4,800만원 안에 듭니다.", {"inputTokens": 1, "outputTokens": 1}

    monkeypatch.setattr(report.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(report.research, "available", unavailable)
    monkeypatch.setattr(report, "_complete", complete)
    approved = {"title": "노트북 구매 비교", "sections": ["요약", "비용 계산의 전제", "권고안"]}
    events = [e async for e in report.write(request="노트북 세 업체 비교 보고서", model="m",
                                            api_key="k", approved_plan=approved,
                                            web_search=False)]
    shown = {e["heading"]: e["content"] for e in events if e["type"] == "section" and e["done"]}
    assert shown["비용 계산의 전제"] == report_stages.FAILED_SECTION
    assert 3000 in asked, "the failed section was asked once more with more room"
    sections = next(e["sections"] for e in events if e["type"] == "report")
    assert all(str(s.get("content") or "").strip() for s in sections)
    assert any("4,800만원" in str(s.get("content")) for s in sections)


async def test_a_calc_timeout_leaves_the_writer_computing(monkeypatch):
    # r7 A10-T2: 「코드 계산 시간 초과 — 모델이 계산」 and no TAM·SAM·SOM figure at all
    from app.pipeline.bundle import Part
    from app.pipeline.stage import Context

    async def timed_out(*_args, **_kwargs):
        return "", {"inputTokens": 0, "outputTokens": 0}, "timeout"

    monkeypatch.setattr(report_stages.calc, "computed_values", timed_out)
    bundle = report_stages.bundle_for(
        request="월 9,900원 구독, 초중고 학생 수로 TAM·SAM·SOM을 계산해 줘", model="m", api_key="k")
    events = [e async for e in report_stages.Calc().run(bundle, Context())]
    assert events[-1]["label"] == "코드 계산 시간 초과 — 모델이 계산"
    notes = [p for p in bundle.material.trusted if isinstance(p, Part)]
    assert any(p.text == report_stages.CALC_MISSING_NOTE for p in notes)


async def test_a_document_with_no_section_written_is_not_saved(monkeypatch):
    async def litellm_config():
        return "http://mock-litellm", "unused"

    async def unavailable():
        return False

    monkeypatch.setattr(report.settings_store, "litellm_config", litellm_config)
    monkeypatch.setattr(report.research, "available", unavailable)
    posts: list[dict] = []
    monkeypatch.setattr(report.httpx, "AsyncClient",
                        lambda **kw: _Canned([""] * 40, posts, **kw))
    approved = {"title": "노트북 구매 비교", "sections": ["요약", "비용 계산의 전제", "권고안"]}
    events = [e async for e in report.write(request="노트북 세 업체 비교 보고서", model="m",
                                            api_key="k", approved_plan=approved,
                                            web_search=False)]
    assert not any(e["type"] == "report" for e in events)
    error = next(e for e in events if e["type"] == "error")
    assert "어느 절도 쓰지 못했습니다" in error["message"]
