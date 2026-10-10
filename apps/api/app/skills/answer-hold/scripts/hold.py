"""자료에서 찾은 대목이 없거나 답이 찾은 대목과 맞지 않으면, 답 대신 「자료에서 찾지 못했습니다」와 담당 부서·연락처를 보냅니다.

인자(JSON 하나): {"text": "<답>", "computed": ["<이번 턴 도구 출력·앞선 답>"],
  "options": {"surface": "chat", "contact": "<담당 부서·연락처>"}}
대화 답에만 돈다. 결과는 요약 줄과 마지막 줄 JSON 으로 낸다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import json
import re
import sys

NOT_FOUND = "제공된 자료에서 이 질문에 대한 내용을 찾지 못했습니다."
NO_CONTACT = "담당 부서에 문의해 주세요."

#: The answer already says the material does not hold it.
_NOT_COVERED = re.compile(
    r"(?:자료|규정|문서|안내\s{0,2}자료)(?:에서|에는|에|로는)?\s{0,3}"
    r"(?:(?:이|그)\s{0,2})?(?:질문에\s{0,2}(?:대한|맞는)\s{0,2})?(?:내용을|답을|근거를)?\s{0,3}"
    r"(?:찾지\s{0,2}못|확인(?:되지|할\s{0,2}수\s{0,2}없)|나와\s{0,2}있지\s{0,2}않|없습니다|없어)"
)
#: A passage header of the agent's material in a tool result (`knowledge.render`).
_PASSAGE = re.compile(r"^\[\d{1,3}\] 자료: ", re.M)
#: An earlier answer's source list naming the material (no link: `agent._link_citations`).
_CITED = re.compile(r"^### 출처[ \t]{0,4}$", re.M)
_SOURCES = re.compile(r"\n#{2,3} ?출처[ \t]{0,4}\n[\s\S]{0,6000}$")
_SENTENCE = re.compile(r"(?<=[.!?。])[ \t]{1,8}|\n{1,8}")
#: Support: share of a sentence's letter pairs found in what was retrieved.
_SUPPORT = 0.3
_MIN_CLAIM = 12


def message(contact: str) -> str:
    contact = (contact or "").strip()
    return NOT_FOUND + "\n\n" + (f"담당 부서·연락처: {contact}" if contact else NO_CONTACT)


def _pairs(text: str) -> set[str]:
    flat = re.sub(r"[^0-9A-Za-z가-힣]", "", text or "").lower()
    return {flat[i:i + 2] for i in range(len(flat) - 1)}


def corpus(computed) -> str:
    """What the answer may rest on: this turn's passages of the material, and earlier answers
    in the conversation that cited it."""
    out = []
    for item in computed or []:
        text = str(item or "")
        if _PASSAGE.search(text):
            out.append(text)
        elif _CITED.search(text) and "[" in text:
            out.append(text)
    return "\n".join(out)


def claims(text: str) -> list[str]:
    body = _SOURCES.sub("", text or "")
    out = []
    for piece in _SENTENCE.split(body):
        line = piece.strip().lstrip("#-*•·> ").strip()
        letters = re.sub(r"[^0-9A-Za-z가-힣]", "", line)
        if len(letters) < _MIN_CLAIM or line.startswith(("담당 부서", "출처")):
            continue
        out.append(line)
    return out


def check(text: str, material: str = "", computed=None, options=None) -> list[dict]:
    opts = options or {}
    if opts.get("surface") not in (None, "", "chat") or not (text or "").strip():
        return []
    if _NOT_COVERED.search((text or "")[:3000]):
        return []
    said = claims(text)
    if not said:
        return []
    found = corpus(computed)
    if found:
        grams = _pairs(found)
        if any(len(g := _pairs(s)) and len(g & grams) >= _SUPPORT * len(g) for s in said):
            return []
        why = "찾은 자료와 맞는 문장이 없음"
    else:
        why = "자료에서 찾은 대목이 없음"
    return [{"kind": "unsupported_answer", "text": (text or "").strip()[:80], "line": 0,
             "start": 0, "end": 0, "why": why,
             "placeholder": message(str(opts.get("contact") or ""))}]


def fix(text: str, findings: list[dict]) -> str:
    return str(findings[0].get("placeholder") or text) if findings else text


def summary(findings: list[dict]) -> str:
    if not findings:
        return ""
    return f"근거 없는 답 보류: {findings[0].get('why') or ''} → 담당 부서 안내"


def run(options: dict) -> dict:
    text = str(options.get("text") or "")
    found = check(text, "", options.get("computed") or [], options.get("options") or {})
    return {"ok": True, "text": fix(text, found), "findings": found, "summary": summary(found)}


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else json.load(sys.stdin)
        result = run(options if isinstance(options, dict) else {})
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    if result.get("summary"):
        print(result["summary"])
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
