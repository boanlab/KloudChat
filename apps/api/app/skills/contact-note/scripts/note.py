"""답이 자료로 다 답하지 못했다고 말하면, 담당 부서·연락처를 적힌 그대로 덧붙입니다.

인자(JSON 하나): {"text": "<답>", "options": {"surface": "chat", "contact": "<담당 부서·연락처>"}}
연락처가 없으면 「담당 부서에 문의해 주세요.」를 붙인다. 대화 답에만 돈다.
결과는 요약 줄과 마지막 줄 JSON 으로 낸다. 표준 라이브러리만 쓴다.
"""

from __future__ import annotations

import json
import re
import sys

NO_CONTACT = "담당 부서에 문의해 주세요."

#: The answer says the material does not (fully) cover the question.
_SHORT = re.compile(
    r"(?:자료|규정|문서)(?:에서|에는|에|로는)?\s{0,3}(?:(?:이|그)\s{0,2})?"
    r"(?:질문에\s{0,2}(?:대한|맞는)\s{0,2})?(?:내용을|답을|근거를|내용은|내용이)?\s{0,3}"
    r"(?:찾지\s{0,2}못|나와\s{0,2}있지\s{0,2}않|명시되어\s{0,2}있지\s{0,2}않|없습니다|없어|없으므로|없어서)"
    r"|확인되지\s{0,2}않|확인할\s{0,2}수\s{0,2}없|담당\s{0,2}부서에\s{0,2}(?:문의|확인)"
    # 「자료 (장학금 지급 규정 및 학칙) 에 명시되어 있지 않아」「자료에 포함되어 있지 않습니다」
    r"|자료[^\n.]{0,40}?(?:명시되어|포함되어|나와|언급되어)\s{0,2}있지\s{0,2}않"
)
_SOURCES = re.compile(r"\n#{2,3} ?출처[ \t]{0,4}\n")


def line_for(contact: str) -> str:
    contact = (contact or "").strip()
    return f"담당 부서·연락처: {contact}" if contact else NO_CONTACT


def check(text: str, material: str = "", computed=None, options=None) -> list[dict]:
    opts = options or {}
    if opts.get("surface") not in (None, "", "chat"):
        return []
    body = _SOURCES.split(text or "", maxsplit=1)[0]
    if not _SHORT.search(body[:6000]):
        return []
    contact = str(opts.get("contact") or "").strip()
    if (contact and contact in body) or (not contact and NO_CONTACT in body):
        return []
    return [{"kind": "contact", "text": "", "line": 0, "start": 0, "end": 0,
             "placeholder": line_for(contact)}]


def fix(text: str, findings: list[dict]) -> str:
    if not findings:
        return text
    line = str(findings[0].get("placeholder") or "")
    parts = _SOURCES.split(text or "", maxsplit=1)
    if len(parts) == 2:
        heading = _SOURCES.search(text).group(0)
        return parts[0].rstrip() + "\n\n" + line + "\n" + heading + parts[1]
    return (text or "").rstrip() + "\n\n" + line


def summary(findings: list[dict]) -> str:
    return "담당 부서·연락처를 덧붙임" if findings else ""


def run(options: dict) -> dict:
    text = str(options.get("text") or "")
    found = check(text, "", None, options.get("options") or {})
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
