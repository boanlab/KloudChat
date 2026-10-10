"""답에 남은 개인정보(휴대전화 번호·개인 이메일·주민등록번호·카드 번호)를 [전화번호]·[이메일]·[주민번호]·[카드번호]로 가립니다.

인자(JSON 하나): {"text": "<답>", "options": {"contact": "<에이전트의 담당 부서·연락처>"}}
기관 대표번호·서비스 번호·부서 메일(help@, minwon@ …)과 담당 부서·연락처는 그대로 둔다.
서버에서는 이 인스턴스의 개인정보 탐지(외부 모델로 나가기 전에 가리는 것과 같은 것)를 쓰고,
샌드박스에서는 아래 패턴을 쓴다. 결과는 요약 줄과 마지막 줄 JSON 으로 낸다.
"""

from __future__ import annotations

import json
import re
import sys

try:  # On the server: the instance's own detector, the one egress masking uses.
    from app.services import governance as _governance
except Exception:  # noqa: BLE001 — in the sandbox, the patterns below
    _governance = None

_ROLE = frozenset("""
info contact press pr support help helpdesk service cs customer sales biz business partner
partners marketing admin administrator webmaster postmaster office hello hr recruit jobs
career careers noreply no-reply newsletter news minwon team staff billing account accounts
security abuse legal privacy media ir inquiry inquiries enquiry enquiries
""".split())

_PATTERNS = (
    ("[주민번호]", re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)")),
    ("[카드번호]", re.compile(r"(?<!\d)(?:\d{4}[- ]){3}\d{4}(?!\d)")),
    ("[전화번호]", re.compile(r"(?<!\d)(?:\+?82[- ]?)?0?1[016-9][- ]?\d{3,4}[- ]?\d{4}(?!\d)")),
    ("[이메일]", re.compile(r"(?<![\w.+-])([A-Za-z0-9._%+-]{1,64})@[A-Za-z0-9.-]{1,253}"
                         r"\.[A-Za-z]{2,24}")),
)
#: Pieces of a contact line that stand alone in an answer: a number or an address.
_CONTACT_PIECES = re.compile(r"[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,253}\.[A-Za-z]{2,24}"
                             r"|(?<!\d)(?:\+?\d{1,3}[- ])?\d{2,4}[- .)]{1,2}\d{3,4}[- .]\d{4}(?!\d)")


def _fallback(line: str) -> tuple[str, int]:
    count = 0
    for label, pattern in _PATTERNS:
        def swap(match: re.Match, label: str = label) -> str:
            nonlocal count
            if label == "[이메일]" and match.group(1).lower() in _ROLE:
                return match.group(0)
            count += 1
            return label
        line = pattern.sub(swap, line)
    return line, count


def _mask(line: str) -> tuple[str, int]:
    if _governance is not None:
        return _governance.mask(line, scope="egress")
    return _fallback(line)


def kept(contact: str) -> list[str]:
    """What stays as written: the contact line and the numbers and addresses in it."""
    contact = (contact or "").strip()
    if not contact:
        return []
    pieces = [m.group(0) for m in _CONTACT_PIECES.finditer(contact)]
    return sorted({contact, *pieces}, key=len, reverse=True)


def mask_line(line: str, keep: list[str]) -> tuple[str, int]:
    holders: dict[str, str] = {}
    for i, value in enumerate(keep):
        if value and value in line:
            token = f"⁣K{i}⁣"
            line = line.replace(value, token)
            holders[token] = value
    masked, count = _mask(line)
    for token, value in holders.items():
        masked = masked.replace(token, value)
    return masked, count


def check(text: str, material: str = "", computed=None, options=None) -> list[dict]:
    """One finding per line holding a personal detail; `placeholder` is the masked line."""
    keep = kept(str((options or {}).get("contact") or ""))
    found = []
    for i, line in enumerate((text or "").split("\n")):
        masked, count = mask_line(line, keep)
        if count and masked != line:
            found.append({"kind": "personal", "text": line.strip()[:80], "line": i, "start": 0,
                          "end": len(line), "placeholder": masked, "count": count})
    return found


def fix(text: str, findings: list[dict]) -> str:
    lines = (text or "").split("\n")
    for finding in findings:
        i = int(finding.get("line", -1))
        if 0 <= i < len(lines):
            lines[i] = str(finding.get("placeholder") or lines[i])
    return "\n".join(lines)


def summary(findings: list[dict]) -> str:
    total = sum(int(f.get("count") or 1) for f in findings)
    return f"개인정보 {total}개 가림" if total else ""


def post(text: str, args: dict) -> dict:
    """The skill switched on for a chat turn: the answer with personal details masked."""
    found = check(text, "", None, {"contact": str(args.get("contact") or "")})
    out = {"text": fix(text, found) if found else text, "fixed": [], "rewrite": [], "flags": []}
    if found:
        out["fixed"] = [{"kind": "personal", "text": f["text"], "why": "개인정보 → 가림"}
                        for f in found]
    return out


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
