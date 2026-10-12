"""답이 자료로 다 답하지 못했다고 말하면, 담당 부서·연락처를 적힌 그대로 덧붙입니다."""

from __future__ import annotations

import json
import re
import sys

NO_CONTACT = "담당 부서에 문의해 주세요."

#: Answer says the material does not (fully) cover the question.
_SHORT = re.compile(
    r"(?:자료|규정|문서)(?:에서|에는|에|로는)?\s{0,3}(?:(?:이|그)\s{0,2})?"
    r"(?:질문에\s{0,2}(?:대한|맞는)\s{0,2})?(?:내용을|답을|근거를|내용은|내용이)?\s{0,3}"
    r"(?:찾지\s{0,2}못|나와\s{0,2}있지\s{0,2}않|명시되어\s{0,2}있지\s{0,2}않|없습니다|없어|없으므로|없어서)"
    r"|확인되지\s{0,2}않|확인할\s{0,2}수\s{0,2}없|담당\s{0,2}부서에\s{0,2}(?:문의|확인)"
    # 「자료 (장학금 지급 규정 및 학칙) 에 명시되어 있지 않아」「자료에 포함되어 있지 않습니다」
    r"|자료[^\n.]{0,40}?(?:명시되어|포함되어|나와|언급되어)\s{0,2}있지\s{0,2}않"
)
_SOURCES = re.compile(r"\n#{2,3} ?출처[ \t]{0,4}\n")
#: Answer's own department pointer; pointless after a full answer.
_REFERRAL = re.compile(r"담당\s{0,2}부서에\s{0,2}(?:문의|확인)")
#: 「담당 부서에 문의해 주세요.」 standing alone as a sentence or a line.
_BARE = re.compile(r"(?:^|(?<=[.!?。]))[ \t]{0,4}(?:자세한\s{0,2}(?:사항|내용)은\s{0,2})?"
                   r"담당\s{0,2}부서에\s{0,2}문의(?:해|하여|하시기|하십시오)[^.\n]{0,12}[.!]?[ \t]{0,4}$",
                   re.M)


def line_for(contact: str) -> str:
    contact = (contact or "").strip()
    return f"담당 부서·연락처: {contact}" if contact else NO_CONTACT


def check(text: str, material: str = "", computed=None, options=None) -> list[dict]:
    opts = options or {}
    if opts.get("surface") not in (None, "", "chat"):
        return []
    body = _SOURCES.split(text or "", maxsplit=1)[0]
    short = _SHORT.search(_REFERRAL.sub("", body[:6000]))
    if not short:
        # Full answer still ending with an unnamed 「담당 부서」 referral.
        bare = _BARE.search(body)
        if bare and not str(opts.get("contact") or "").strip():
            return [{"kind": "drop", "text": bare.group(0), "line": 0, "start": bare.start(),
                     "end": bare.end(), "placeholder": ""}]
        return []
    contact = str(opts.get("contact") or "").strip()
    if (contact and contact in body) or (not contact and NO_CONTACT in body):
        return []
    return [{"kind": "contact", "text": "", "line": 0, "start": 0, "end": 0,
             "placeholder": line_for(contact)}]


def fix(text: str, findings: list[dict]) -> str:
    if not findings:
        return text
    if findings[0].get("kind") == "drop":
        found = findings[0]
        kept = (text[:found["start"]] + text[found["end"]:]).rstrip()
        return re.sub(r"\n{3,}", "\n\n", kept)
    line = str(findings[0].get("placeholder") or "")
    parts = _SOURCES.split(text or "", maxsplit=1)
    if len(parts) == 2:
        heading = _SOURCES.search(text).group(0)
        return parts[0].rstrip() + "\n\n" + line + "\n" + heading + parts[1]
    return (text or "").rstrip() + "\n\n" + line


def summary(findings: list[dict]) -> str:
    if findings and findings[0].get("kind") == "drop":
        return "다 답한 뒤 붙은 「담당 부서에 문의」를 뺌"
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
