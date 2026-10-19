"""Replay fixture builder from recorded agent runs.

    python tests/replay_build.py <accept-runs-dir> <data-dir> [<out-dir> [<cases>]] [--live]

`--live` sends every prompt kind the case does not script to the live models via LiteLLM.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_harness as harness  # noqa: E402

WRITER = "strict-local/qwen3.8-27b"
PLANNER = "strict-local/qwen3.5-122b"
#: The runs' wall clock (KST), so relative dates read as they did.
NOW = "2026-10-08T16:00:00+09:00"

QUOTE_CP1 = {
    "vat_basis": "excl",
    "items": [
        {"name": "PM", "mm": 1.0, "monthly_price": 9000000},
        {"name": "AI 엔지니어", "mm": 3.0, "monthly_price": 8000000},
        {"name": "데이터 엔지니어", "mm": 1.5, "monthly_price": 7000000},
        {"name": "GPU 서버 임차", "qty": 3, "unit": "개월", "unit_price": 1200000},
    ],
    "budget": {"amount": 48000000, "vat_basis": "excl"},
    "per_head": None, "scenarios": [], "quotes": [],
}
#: The known trap (AI engineer read as two people): the vote's third reading settles it.
QUOTE_CP1_SLIP = {**QUOTE_CP1, "items": [
    {**QUOTE_CP1["items"][0]},
    {"name": "AI 엔지니어", "mm": 3.0, "people": 2, "monthly_price": 8000000},
    *QUOTE_CP1["items"][2:]]}

COSTS_CR1 = {
    "period": "1년", "years": 1,
    "options": [
        {"name": "톡플로우", "items": [
            {"name": "이용료", "unit_price": 6000, "count": 80, "per": "month", "months": 12}]},
        {"name": "팀링크 엔터프라이즈", "items": [
            {"name": "이용료", "unit_price": 8000, "count": 80, "per": "month", "months": 12}]},
        {"name": "워크닷", "items": [
            {"name": "이용료", "unit_price": 7500, "count": 80, "per": "month", "months": 12}]},
    ],
    "scenarios": [],
}
SCORES_CR1 = {
    "criteria": [],
    "musts": [{"name": "SSO", "kind": "yesno"},
              {"name": "화상회의 인원", "kind": "min", "limit": 60, "unit": "명"}],
    "options": [
        {"name": "톡플로우", "values": {}, "musts": {"SSO": "있음", "화상회의 인원": 100}},
        {"name": "팀링크", "values": {}, "musts": {"SSO": "있음", "화상회의 인원": 50}},
        {"name": "워크닷", "values": {}, "musts": {"SSO": "있음", "화상회의 인원": 300}},
    ],
}

COSTS_CR4 = {
    "period": "1년", "years": 1,
    "options": [
        {"name": "런큐브", "items": [
            {"name": "연 이용료", "unit_price": 18000000, "count": 1, "per": "year", "years": 1}]},
        {"name": "에듀노트", "items": [
            {"name": "이용료", "unit_price": 4800, "count": 300, "per": "month", "months": 12}]},
    ],
    "scenarios": [],
}
SCORES_CR4 = {
    "criteria": [{"name": "비용", "weight": 50, "kind": "cost"},
                 {"name": "모바일 앱", "weight": 30, "kind": "yesno"},
                 {"name": "HR 연동", "weight": 20, "kind": "yesno"}],
    "musts": [{"name": "SCORM", "kind": "yesno"}],
    "options": [
        {"name": "런큐브", "values": {"비용": 18000000, "모바일 앱": "있음", "HR 연동": "없음"},
         "musts": {"SCORM": "있음"}},
        {"name": "에듀노트", "values": {"비용": 17280000, "모바일 앱": "없음", "HR 연동": "없음"},
         "musts": {"SCORM": "있음"}},
        {"name": "클래스온", "values": {"비용": None, "모바일 앱": "있음", "HR 연동": "있음"},
         "musts": {"SCORM": "없음"}},
    ],
}

#: name → (run, kind, extra replies by prompt kind: a list is answered in turn)
CASES: dict[str, dict] = {
    "minutes": {"run": "minutes-1__r1", "agent": "minutes", "kind": "report",
                "replies": {}},
    "gaejo": {"run": "gaejo-report-1__r1", "agent": "gaejo-report", "kind": "report",
              "replies": {"key_figures": [json.dumps([
                  {"name": "대당 교체 비용", "value": "1억 2천만 원", "aliases": ["교체 비용"]},
                  {"name": "시설 개보수 예산", "value": "9억 원", "aliases": ["개보수 예산"]}],
                  ensure_ascii=False)]}},
    "client_proposal": {"run": "client-proposal-1__r1", "agent": "client-proposal",
                        "kind": "report",
                        "replies": {
                            "quote": [json.dumps(q, ensure_ascii=False)
                                      for q in (QUOTE_CP1, QUOTE_CP1_SLIP, QUOTE_CP1)],
                            "key_figures": [json.dumps([
                                {"name": "예산", "value": "48,000,000원",
                                 "aliases": ["사업 예산"]}],
                                ensure_ascii=False)]}},
    "comparison": {"run": "comparison-report-1__r1", "agent": "comparison-report",
                   "kind": "report",
                   "replies": {
                       "costs": [json.dumps(COSTS_CR1, ensure_ascii=False)] * 2,
                       "scores": [json.dumps(SCORES_CR1, ensure_ascii=False)],
                       "key_figures": [json.dumps([
                           {"name": "직원 수", "value": "80명", "aliases": ["직원", "인원"]}],
                           ensure_ascii=False)]}},
    "career_history": {"run": "career-docs-2__r1", "agent": "career-docs", "kind": "report",
                       "replies": {}},
    # Long form: every section its own call, four at a time behind the gate.
    "long_form": {"run": "client-proposal-2__r1", "agent": "client-proposal", "kind": "report",
                  "request_suffix": "\n\n분량은 10쪽 이상으로 길게 써 줘.", "replies": {}},
    # A follow-up that changes a criterion: the pre-pass again, the dependents, the alignment.
    "revise_comparison": {"run": "comparison-report-4__r1", "agent": "comparison-report",
                          "kind": "revise", "turn": "conditional-not-asked",
                          "replies": {"costs": [json.dumps(COSTS_CR4, ensure_ascii=False)] * 2,
                                      "scores": [json.dumps(SCORES_CR4, ensure_ascii=False)]}},
    # A plain follow-up on one part.
    "revise_proposal": {"run": "client-proposal-4__r1", "agent": "client-proposal",
                        "kind": "revise", "turn": "turn2", "replies": {}},
    "data_analysis_chat": {"run": "data-analysis-1__r1", "agent": "data-analysis",
                           "kind": "chat", "csv": "library_2025.csv", "replies": {}},
    "career_docs_chat": {"run": "career-docs-1__r1", "agent": "career-docs", "kind": "chat",
                         "replies": {}},
}

#: Prompt markers → kind, first match wins.
KINDS = [
    ("outline", "Make the title and table of contents (목차) of a report"),
    ("draft", "Write the whole report in one pass"),
    ("section", 'You are writing only the "'),
    ("fill", "filling the blanks with the material's actual values"),
    ("key_figures", "must state with one and the same value"),
    ("quote", "values for the quote calculation"),
    ("costs", "computing each alternative's total cost"),
    ("scores", "scoring the alternatives and checking the must-have conditions"),
    ("head", "의 머리 정보를 채워라"),
    ("diagram_plan", "도식의 종류"),
    ("figure_shape", "Pick the shape of the figure"),
    ("diagram", "그릴 것:"),
    ("repair", "Some sentences in the report have problems"),
    ("amend", "Only add to the section below."),
    ("condense", "section of a 개조식 report"),
    ("rewrite", "자기소개서 「[제출용 답안"),
    ("revise_plan", "Decide what they are asking to change"),
    ("calc", "Python 으로 구한다"),
    ("judge", "골라라"),
    ("judge", "Pick only the"),
    ("judge", "pick only the"),
    ("judge", "does this sentence wrongly write"),
    ("judge", "판정하라"),
]


def kind_of(prompt: str) -> str:
    return next((kind for kind, marker in KINDS if marker in prompt), "other")


def _events(run: Path) -> list[list[dict]]:
    return [[json.loads(line) for line in path.read_text().splitlines() if line.strip()]
            for path in sorted(run.glob("events_*.jsonl"))]


def _written(events: list[dict]) -> dict[str, str]:
    """Each heading's body as the run first streamed it, before figures were added."""
    out: dict[str, str] = {}
    for e in events:
        if e.get("type") == "section" and e.get("done") and e["heading"] not in out:
            out[e["heading"]] = str(e.get("content") or "")
    return out


def _after(prompt: str, marker: str) -> str:
    return prompt.split(marker, 1)[1].strip() if marker in prompt else ""


class Responder:
    """Answers each prompt kind from the run."""

    def __init__(self, spec: dict, run: Path) -> None:
        self.extra = {k: list(v) for k, v in spec.get("replies", {}).items()}
        self.run = json.loads((run / "run.json").read_text())
        passes = _events(run)
        writing = next((p for p in passes if any(e.get("type") == "report" or
                                                 e.get("type") == "titleBlock" for e in p)),
                       passes[-1] if passes else [])
        self.writing = writing
        self.bodies = _written(writing)
        self.plan = next((t.get("approved_plan") for t in self.run["turns"]
                          if t.get("approved_plan")), None)
        self.head = next((e["titleBlock"] for e in writing if e.get("type") == "titleBlock"), None)
        self.figures = [e for e in writing if e.get("type") == "step"
                        and str(e.get("id", "")).startswith("dia") and e.get("status") == "done"]
        self.unknown: list[str] = []
        self.revision = next((p for p in passes if any(
            e.get("type") == "step" and e.get("id") == "route" for e in p)), [])
        # The revision's rewritten sections answer the rewrite prompts first.
        self.bodies = {**self.bodies, **_written(self.revision)}
        self.route = next((e["label"] for e in self.revision if e.get("type") == "step"
                           and e.get("id") == "route" and e.get("status") == "done"), "")

    def __call__(self, body: dict) -> tuple[str, str]:
        prompt = harness.whole_prompt(body)
        kind = kind_of(prompt)
        if self.extra.get(kind):
            queue = self.extra[kind]
            return kind, queue.pop(0) if len(queue) > 1 else queue[0]
        last = harness.prompt_of(body)
        if kind == "outline" and self.plan:
            plan = self.plan
            return kind, json.dumps({"title": plan["title"], "style": "편집형",
                                     "subject": plan["title"], "sections": plan["sections"],
                                     "alternatives": []}, ensure_ascii=False)
        if kind == "revise_plan":
            names = re.findall(r"(?m)^(\d{1,2})\. (.+)$", _after(last, "문서의 구성:"))
            target = self.route.split(": ", 1)[-1]
            hits = [int(n) for n, name in names if name.strip() == target.strip()] or [1]
            return kind, json.dumps({"scope": "parts", "targets": hits[:1],
                                     "note": "요청한 기준을 반영해 다시 쓴다"}, ensure_ascii=False)
        if kind == "draft":
            outline = _after(last, "each as a `## 제목` line):").split("\n\nSources:", 1)[0]
            headings = re.findall(r"(?m)^## (.+)$", outline)
            return kind, "\n\n".join(f"## {h}\n\n{self.bodies.get(h, '')}" for h in headings
                                     if h in self.bodies)
        if kind == "section":
            heading = last.split('You are writing only the "', 1)[1].split('"', 1)[0]
            return kind, self.bodies.get(heading, f"{heading}의 내용입니다.")
        if kind == "head" and self.head:
            return kind, json.dumps({"fields": dict(self.head.get("fields") or [])},
                                    ensure_ascii=False)
        if kind == "diagram_plan":
            rows = [{"part": int(re.sub(r"\D", "", e["id"]) or 0) + 1, "figure": "flow",
                     "description": e["label"], "caption": e["label"]} for e in self.figures]
            return kind, json.dumps(rows, ensure_ascii=False)
        if kind == "figure_shape":  # runs recorded before the shape choice drew mermaid
            return kind, "other"
        if kind == "diagram":
            what = _after(last, "설명:")[:40] or "단계"
            return kind, f"```mermaid\nflowchart LR\n  a[{what}] --> b[검토] --> c[결정]\n```"
        if kind == "repair":
            items = json.loads(_after(last, "Items:") or "[]")
            return kind, json.dumps([i["sentence"] for i in items], ensure_ascii=False)
        if kind == "amend":
            return kind, _after(last, "절:")
        if kind == "condense":
            return kind, _after(last, "Section:")
        if kind == "fill":
            return kind, last.rsplit("\n\n", 1)[-1]
        if kind == "rewrite":
            text = last.rsplit("\n글:\n", 1)[-1].strip()
            return kind, text + " 맡은 일을 끝까지 확인하며 성실히 일하겠습니다."
        if kind in ("key_figures", "diagram_plan"):
            return kind, "[]"
        if kind == "other":
            self.unknown.append(last[:200])
        return kind, "{}"


class LiveResponder(Responder):
    """Answers from the live models through LiteLLM (`--live`: `REPLAY_LIVE_URL`, the
    OpenAI-style base such as `http://host.docker.internal:8080/litellm/v1`, and
    `REPLAY_LIVE_KEY`). A case's scripted extra replies (the planted traps) still answer
    their kinds. Calls block, so the turn schedules as it does when replayed."""

    def __init__(self, spec: dict, run: Path, url: str, key: str) -> None:
        import httpx

        super().__init__(spec, run)
        self.client = httpx.Client(base_url=url, timeout=900,
                                   headers={"Authorization": f"Bearer {key}"})

    def __call__(self, body: dict) -> tuple[str, str]:
        import time

        kind = kind_of(harness.whole_prompt(body))
        if self.extra.get(kind):
            return super().__call__(body)
        sent = {k: v for k, v in body.items() if k not in ("stream", "stream_options")}
        for attempt in range(6):
            resp = self.client.post("/chat/completions", json=sent)
            if resp.status_code < 500 and resp.status_code != 429:
                break
            time.sleep(10 * (attempt + 1))
        resp.raise_for_status()
        return kind, str(resp.json()["choices"][0]["message"].get("content") or "")


def report_case(name: str, spec: dict, run: Path) -> dict:
    from app.services import check_skills, design_templates

    turns = json.loads((run / "run.json").read_text())["turns"]
    template_id = _agent_toml(spec["agent"]).get("template")
    format_id = _agent_toml(spec["agent"]).get("doc_format")
    template = design_templates.get(template_id)
    keys = check_skills.attached(agent_key=spec["agent"],
                                 template_checks=template.check_skills if template else ())
    return {
        "name": name, "kind": "report", "run": spec["run"], "now": NOW,
        "write": {
            "request": turns[0]["sent"]["content"] + spec.get("request_suffix", ""),
            "model": WRITER, "outline_model": PLANNER,
            "api_key": "k", "web_search": False, "template_id": template_id,
            "format_id": format_id,
            "check_keys": list(check_skills.of_stage(keys, "post")),
        },
    }


def revise_case(name: str, spec: dict, run: Path) -> dict:
    """The document as the writing pass left it, and the follow-up that revises it."""
    record = json.loads((run / "run.json").read_text())
    passes = _events(run)
    writing = next(p for p in passes if any(e.get("type") == "titleBlock" for e in p))
    last: dict[str, dict] = {}
    for e in writing:
        if e.get("type") == "section":
            last[e["sectionId"]] = e
    sections = [{"id": sid, "heading": e["heading"], "level": 1, "status": "done",
                 "content": e["content"]} for sid, e in last.items()]
    block = next(e["titleBlock"] for e in writing if e.get("type") == "titleBlock")
    plan = next(t["approved_plan"] for t in record["turns"] if t.get("approved_plan"))
    instruction = next(t["sent"]["content"] for t in record["turns"]
                       if t["label"] == spec["turn"])
    request = record["turns"][0]["sent"]["content"]
    return {
        "name": name, "kind": "revise", "run": spec["run"], "now": NOW,
        "revise": {
            "model": WRITER, "planner": PLANNER, "title": plan["title"],
            "request": request, "instruction": instruction,
            "data": {"sections": sections, "sources": [], "request": request[:4000],
                     "titleBlock": block},
        },
    }


def chat_case(name: str, spec: dict, run: Path, data: Path) -> dict:
    from app.services import check_skills

    record = json.loads((run / "run.json").read_text())
    first = _events(run)[0]
    session = json.loads((run / "session.json").read_text())
    final = next((m["content"] for m in session["messages"] if m["role"] == "assistant"), "")
    raw = next((e["text"] for e in first if e.get("type") == "retract"), final)
    steps = [{k: v for k, v in e.items() if k != "type"} for e in first
             if e.get("type") == "step" and not str(e.get("id", "")).startswith(
                 ("context-", "answer_plan", "text_fit", "grounding"))]
    content = record["turns"][0]["sent"]["content"]
    material = (data / spec["csv"]).read_text() if spec.get("csv") else ""
    keys = check_skills.attached(agent_key=spec["agent"])
    return {
        "name": name, "kind": "chat", "run": spec["run"], "now": NOW,
        "turn": {
            "model": WRITER, "judge_model": PLANNER, "agent_key": spec["agent"],
            "messages": [{"role": "user", "content": content + (
                f"\n\n[첨부: {spec['csv']}]\n{material}" if material else "")}],
            "first_user_message": content,
            "answer": raw,
            # What the turn's tools printed is not in the run; the data it read stands in.
            "tool_outputs": [material] if material else [],
            "steps": steps,
            "check_keys": list(check_skills.of_stage(keys, "post")),
            "check_options": check_skills.clean_options(
                _agent_toml(spec["agent"]).get("check_options")),
            "check_material": material,
        },
    }


def _agent_toml(key: str) -> dict:
    import tomllib

    path = Path(__file__).resolve().parents[1] / "app" / "agents" / f"{key}.toml"
    return tomllib.loads(path.read_text())


class Patches:
    """`monkeypatch.setattr` outside pytest."""

    def __init__(self) -> None:
        self.undo: list[tuple[object, str, object]] = []

    def __call__(self, target, name, value) -> None:
        self.undo.append((target, name, getattr(target, name)))
        setattr(target, name, value)

    def close(self) -> None:
        for target, name, value in reversed(self.undo):
            setattr(target, name, value)


async def record(case: dict, responder: Responder) -> tuple[list[dict], object]:
    import dataclasses

    from app.services import settings_store

    patch = Patches()
    try:
        gateway = harness.Gateway(responder=responder)
        gateway.install(patch)
        prepare(patch)
        real = settings_store.tools_config

        async def without_search():
            return dataclasses.replace(await real(), search="")

        patch(settings_store, "tools_config", without_search)
        harness.freeze(patch, case["now"])
        if case["kind"] == "report":
            out = await harness.run_report(case)
        elif case["kind"] == "revise":
            out = await harness.run_revise(case, patch)
        else:
            out = await harness.run_chat(case, patch)
        return gateway.made, out
    finally:
        patch.close()


def prepare(patch) -> None:
    from app.services import settings_store

    async def litellm_config():
        return "http://replay-litellm", "unused"

    patch(settings_store, "litellm_config", litellm_config)


def main() -> None:
    import os

    args = [a for a in sys.argv[1:] if a != "--live"]
    live = "--live" in sys.argv[1:]
    runs, data = Path(args[0]), Path(args[1])
    out = Path(args[2]) if len(args) > 2 else harness.FIXTURES
    wanted = set(args[3].split(",")) if len(args) > 3 else set(CASES)
    for name, spec in CASES.items():
        if name not in wanted:
            continue
        run = runs / spec["run"]
        case = (report_case(name, spec, run) if spec["kind"] == "report"
                else revise_case(name, spec, run) if spec["kind"] == "revise"
                else chat_case(name, spec, run, data))
        responder = (LiveResponder(spec, run, os.environ["REPLAY_LIVE_URL"],
                                   os.environ["REPLAY_LIVE_KEY"]) if live
                     else Responder(spec, run))
        calls, golden = asyncio.run(record(case, responder))
        root = out / name
        root.mkdir(parents=True, exist_ok=True)
        (root / "input.json").write_text(json.dumps(case, ensure_ascii=False, indent=1))
        (root / "calls.json").write_text(json.dumps(calls, ensure_ascii=False, indent=1))
        (root / "golden.json").write_text(json.dumps(golden, ensure_ascii=False, indent=1))
        kinds: dict[str, int] = {}
        for c in calls:
            kinds[c.get("kind", "")] = kinds.get(c.get("kind", ""), 0) + 1
        print(name, "calls:", kinds, "unknown:", responder.unknown[:3])


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt):
        main()
