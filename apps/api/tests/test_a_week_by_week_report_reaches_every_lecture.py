"""A report asked about every one of 20 project files plans past the usual 12 sections and
checks its 목차 reaches them all; a material question set is one file's set, not every
numbered question across 20 lecture notes; an assessment item the judge reads is its
question, never the lectures up to the next one."""

from __future__ import annotations

import re
from json import dumps

from app.services import coverage, fact_check, report
from app.services.verify import Doc

TOPICS = [
    "클라우드 컴퓨팅 개요", "서비스 모델", "배포 모델", "가상화의 원리", "컨테이너와 Docker",
    "Kubernetes 기초", "클라우드 스토리지", "중간고사 대비 정리", "네트워킹과 VPC",
    "로드 밸런싱", "서버리스 컴퓨팅", "클라우드 데이터베이스", "보안과 IAM", "DevOps와 CI/CD",
    "모니터링과 관측 가능성", "비용 최적화", "마이크로서비스", "빅데이터 처리", "엣지 컴퓨팅",
    "기말 프로젝트 안내",
]
REQUEST = ("올린 강의 자료 20개 전체를 주차별로 요약한 강의 개요서를 Word로 써 줘. 강마다 핵심 "
           "개념 두세 가지와 생각해 볼 문제를 넣어 줘.")


def _items() -> list[coverage.Item]:
    return [coverage.Item(f"{n:02d}강_{t.replace(' ', '_')}.pdf", f"{n}강", t)
            for n, t in enumerate(TOPICS, start=1)]


def _note(n: int, topic: str) -> str:
    """A lecture note as a project's knowledge block carries it: bold name, then its text,
    ending in one or two 「생각해 볼 문제」."""
    return (f"**{n:02d}강_{topic.replace(' ', '_')}.pdf**\n[페이지 1]\n제{n}강 {topic}\n"
            f" 학습목표\n {topic}을 설명할 수 있다\n본문 {topic} 설명이 이어진다.\n"
            f"  생각해볼문제\n1. {topic}에서 가장 중요한 설계 결정은 무엇인가?\n"
            f"2. {topic}의 한계를 설명하라.\n 다음강의\n   다음 시간에는 다음 주제를 다룬다.")


MATERIAL = "\n\n".join(_note(n, t) for n, t in enumerate(TOPICS, start=1))


# ── the 목차 has room for every file and is checked against them ──


def test_an_each_item_plan_may_run_past_twelve_sections():
    assert report.most_sections(0) == report._MAX_SECTIONS == 12
    assert report.most_sections(5) == 12
    assert report.most_sections(20) == 23
    assert report.most_sections(200) == 30
    assert report.outline_tokens(23) > report.outline_tokens(12) == 400
    twenty = dumps({"title": "강의 개요", "sections": [f"{n}주차: {t}" for n, t in
                                                         enumerate(TOPICS, start=1)]},
                        ensure_ascii=False)
    assert len(report._parse_outline(twenty)[1]) == 12  # the usual ceiling
    assert len(report._parse_outline(twenty, 23)[1]) == 20
    rule = report.each_item_rule(20, 23)
    assert "every one of the 20 files" in rule and "range" in rule
    assert report.each_item_rule(1, 12) == ""


def test_the_outline_gaps_are_the_files_no_heading_reaches():
    items = _items()
    twelve = [f"{n}주차: {t}" for n, t in enumerate(TOPICS[:12], start=1)] + ["참고문헌"]
    assert [i.label for i in coverage.outline_gaps(items, twelve)] == [
        f"{n}강" for n in range(13, 21)]
    # a range heading reaches every unit inside it
    grouped = ["1~10강 기초와 인프라", "11-20강 운영과 응용"]
    assert coverage.outline_gaps(items, grouped) == []
    assert [i.label for i in coverage.outline_gaps(items, ["1~10강 기초"])][:2] == ["11강", "12강"]
    # 주차 ranges do not stand for 강 numbers
    assert len(coverage.outline_gaps(items, ["1~20주차 개요"])) == 20


class _Planner:
    """The gateway: a 12-heading 목차 first, every lecture once the files left out are named."""

    def __init__(self):
        self.asked: list[str] = []
        self.room: list[int] = []

    def __call__(self, **_):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        return False

    async def post(self, _url, json):
        said = "\n".join(str(m.get("content") or "") for m in json.get("messages") or [])
        self.asked.append(said)
        self.room.append(int(json.get("max_tokens") or 0))
        count = 20 if "left out these files" in said else 12
        text = dumps({"title": "클라우드 컴퓨팅 강의 개요", "style": "미니멀",
                            "subject": "클라우드 컴퓨팅",
                            "sections": [f"{n}주차: {t}" for n, t in
                                         enumerate(TOPICS[:count], start=1)],
                            "alternatives": []}, ensure_ascii=False)

        class R:
            status_code = 200
            headers: dict = {}

            def raise_for_status(self):
                return None

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}],
                        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}

        return R()


async def _proposal(monkeypatch, request: str) -> tuple[dict, _Planner]:
    async def litellm_config():
        return "http://litellm.test", "unused"

    monkeypatch.setattr(report.settings_store, "litellm_config", litellm_config)
    planner = _Planner()
    monkeypatch.setattr(report.httpx, "AsyncClient", planner)
    sources = [{"id": f"f{n}", "name": i.name, "label": i.label, "title": i.title,
                "state": "included", "listed": n > 6}
               for n, i in enumerate(_items(), start=1)]
    events = [e async for e in report.write(
        request=request, model="strict-local/qwen3.8-27b", api_key="k", web_search=False,
        trusted_context=[], untrusted_context=["# 프로젝트 지식\n" + MATERIAL],
        project_sources=sources,
    )]
    proposal = next((e for e in events if e["type"] == "proposal"), None)
    assert proposal is not None, [(e.get("type"), e.get("label")) for e in events]
    return proposal["plan"], planner


async def test_a_week_by_week_plan_of_twenty_lectures_reaches_all_twenty(monkeypatch):
    plan, planner = await _proposal(monkeypatch, REQUEST)
    headings = [s if isinstance(s, str) else s.get("heading") for s in plan["sections"]]
    assert sum(1 for h in headings if re.match(r"\d+주차", h or "")) == 20
    assert "20주차: 기말 프로젝트 안내" in headings
    # one more ask, naming the eight files the first 목차 left out
    assert len(planner.asked) == 2
    assert "13강 보안과 IAM" in planner.asked[1] and "every one of the 20 files" in planner.asked[0]
    assert planner.room[0] > 400


async def test_a_request_about_one_lecture_plans_as_before(monkeypatch):
    plan, planner = await _proposal(monkeypatch, "4강 가상화 내용을 보고서로 정리해 줘")
    assert len(plan["sections"]) == 12
    assert len(planner.asked) == 1 and "every one of the" not in planner.asked[0]
    assert planner.room == [400]


# ── one file's question set, not twenty lectures' questions ──


def test_twenty_lectures_with_two_questions_each_are_no_question_set():
    assert report.question_set(MATERIAL) == ""
    sections = [{"heading": "12주차: 클라우드 데이터베이스", "content": "본문입니다."},
                {"heading": "참고문헌", "content": ""}]
    assert report.carry_question_set(sections, MATERIAL, REQUEST) == sections


def test_a_quiz_file_among_lecture_notes_is_carried_alone():
    quiz = ("**형성평가.docx**\n# 형성평가\n\n" + "\n".join(
        f"{n}. {t}의 특징으로 옳은 것은?\n① 가 ② 나 ③ 다 ④ 라" for n, t in
        enumerate(TOPICS[:6], start=1)))
    material = MATERIAL + "\n\n" + quiz
    block = report.question_set(material)
    assert len(report._question_lines(block)) == 6
    assert "**01강_" not in block and "생각해볼문제" not in block
    sections = [{"heading": "평가", "content": "문항으로 확인한다."}]
    carried = report.carry_question_set(sections, material, "형성평가 문항을 넣은 보고서 써 줘")
    assert carried[0]["content"].count("의 특징으로 옳은 것은?") == 6


# ── an assessment item is its question, not the pages after it ──


def test_an_item_stops_short_of_the_next_lecture():
    content = ("**평가 문항**\n\n" + MATERIAL)
    items = fact_check._items(Doc([{"heading": "8강", "content": content}]))
    assert items
    assert all(block.count("\n") < fact_check._ITEM_LINES for _, block in items)
    assert not any("**05강_" in block and "가상화의 원리" in block for _, block in items)


def test_a_file_uploaded_again_is_one_item_to_cover():
    """Walk 11-14 rerun: four copies of lecture10.webm in the project gave four sections."""
    from app.services import coverage

    items = [coverage.Item("01강.pdf", "1강", "개요"), coverage.Item("lecture10.webm", "", ""),
             coverage.Item("lecture10.webm", "", ""), coverage.Item("02강.pdf", "2강", "모델")]
    assert [i.name for i in coverage.distinct(items)] == ["01강.pdf", "lecture10.webm", "02강.pdf"]


def test_a_request_scoped_to_some_lectures_is_about_those_only():
    """Walk 11-14 rerun: 「1~7강을 강별로 … 8강의 문제」 planned all 22 project files."""
    from app.services import coverage

    items = [coverage.Item(f"{n:02d}강.pdf", f"{n}강", "") for n in range(1, 21)]
    items.append(coverage.Item("메모.md", "", ""))
    asked = ("중간고사 대비 요약 보고서를 써 줘. 1~7강 핵심 개념을 강별로 정리하고, "
             "8강의 자주 틀리는 문제도")
    assert [i.label for i in coverage.asked_of(items, asked)] == [f"{n}강" for n in range(1, 9)]
    # no unit named, or units no file carries: every file
    every = "올린 강의 자료 20개 전체를 주차별로 요약해 줘"
    assert coverage.asked_of(items, every) == items
    assert coverage.asked_of(items, "3~4주차를 각각 정리") == items
