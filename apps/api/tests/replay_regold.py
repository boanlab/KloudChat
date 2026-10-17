"""Replay fixture rebuild from their own recorded calls when only the code's outcome changes.

    python tests/replay_regold.py [<cases>]
"""

from __future__ import annotations

import asyncio
import collections
import dataclasses
import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))
# the test environment (stage records off, routing pinned), as pytest loads it
import conftest  # noqa: E402,F401
import replay_build as build  # noqa: E402
import replay_harness as harness  # noqa: E402

#: Kinds the build answers from the prompt alone.
SCRIPTED = ("repair", "amend", "condense", "fill", "rewrite", "key_figures")


def _scripted(kind: str, last: str) -> str:
    after = build._after
    if kind == "repair":
        items = json.loads(after(last, "Items:") or "[]")
        return json.dumps([i["sentence"] for i in items], ensure_ascii=False)
    if kind == "amend":
        return after(last, "절:")
    if kind == "condense":
        return after(last, "Section:")
    if kind == "fill":
        return last.rsplit("\n\n", 1)[-1]
    if kind == "rewrite":
        text = last.rsplit("\n글:\n", 1)[-1].strip()
        return text + " 맡은 일을 끝까지 확인하며 성실히 일하겠습니다."
    return "[]"


class Regold(harness.Gateway):
    """Replays recorded calls; a prompt the code now writes differently is answered as built."""

    def __init__(self, calls: list[dict]) -> None:
        super().__init__(calls=calls)
        self.recorded = calls
        self.used: set[int] = set()
        self.by_key: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        for n, call in enumerate(calls):
            self.by_key[call["key"]].append(n)
        self.rebuilt: list[str] = []

    async def answer(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if not path.endswith("/chat/completions"):
            self.made.append({"key": f"offline {request.method} {path}", "kind": "offline"})
            raise httpx.ConnectError("replay: offline", request=request)
        from app.pipeline.runner import CURRENT_STAGE

        body = json.loads(request.content or b"{}")
        key, prompt, stage = harness.call_key(body), harness.prompt_of(body), CURRENT_STAGE.get()
        queue = self.by_key.get(key)
        if queue:
            n = queue.popleft()
            self.used.add(n)
            call = self.recorded[n]
            kind, reply, usage = call.get("kind", ""), call["reply"], call["usage"]
        else:
            kind = build.kind_of(harness.whole_prompt(body))
            same = [n for n, c in enumerate(self.recorded)
                    if n not in self.used and c.get("stage", "") == stage
                    and c.get("head", "")[:60] == prompt[:60]]
            # all used: the same stage's reply to the same kind of prompt answers again
            alike = same or [n for n, c in enumerate(self.recorded)
                             if c.get("stage", "") == stage and "reply" in c
                             and c.get("head", "")[:60] == prompt[:60]]
            if kind in SCRIPTED:
                reply = _scripted(kind, prompt)
            elif alike:
                reply = self.recorded[alike[0]]["reply"]
            else:
                raise RuntimeError(f"no recorded call to stand for: {stage} {prompt[:200]}")
            if same:
                self.used.add(same[0])
                kind = self.recorded[same[0]].get("kind", kind)
                for q in self.by_key.values():
                    if same[0] in q:
                        q.remove(same[0])
            usage = {"prompt_tokens": len(harness.whole_prompt(body)) // 4 + 1,
                     "completion_tokens": len(reply) // 4 + 1}
            self.rebuilt.append(f"{stage} {kind}: {prompt[:80]!r} … {prompt[-120:]!r}")
        self.made.append({"key": key, "stage": stage, "kind": kind, "head": prompt[:160],
                          "reply": reply, "usage": usage})
        return httpx.Response(200, json=harness._completion(reply, usage), request=request)


async def regold(name: str) -> None:
    import pytest

    from app.services import settings_store

    case, calls, _golden = harness.load(name)
    patch = pytest.MonkeyPatch()
    try:
        async def litellm_config():
            return "http://replay-litellm", "unused"

        patch.setattr(settings_store, "litellm_config", litellm_config)
        real = settings_store.tools_config

        async def without_search():
            return dataclasses.replace(await real(), search="")

        patch.setattr(settings_store, "tools_config", without_search)
        harness.pin_recorded_defaults(patch.setattr)
        gateway = Regold(calls)
        gateway.install(patch.setattr)
        harness.freeze(patch.setattr, case["now"])
        if case["kind"] == "report":
            got = await harness.run_report(case)
        elif case["kind"] == "revise":
            got = await harness.run_revise(case, patch.setattr)
        else:
            got = await harness.run_chat(case, patch.setattr)
    finally:
        patch.undo()
    got = json.loads(json.dumps(got, ensure_ascii=False))
    root = harness.FIXTURES / name
    (root / "calls.json").write_text(json.dumps(gateway.made, ensure_ascii=False, indent=1))
    (root / "golden.json").write_text(json.dumps(got, ensure_ascii=False, indent=1))
    unused = len(calls) - len(gateway.used)
    print(name, f"rebuilt {len(gateway.rebuilt)} calls, {unused} recorded calls unused")
    for line in gateway.rebuilt:
        print("  ", line)


def main() -> None:
    wanted = sys.argv[1].split(",") if len(sys.argv) > 1 else harness.cases()
    for name in wanted:
        asyncio.run(regold(name))


if __name__ == "__main__":
    main()
