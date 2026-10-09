"""원문과 고친 글을 견주어 숫자·날짜·시각·금액·이름·인용이 그대로 남았는지 확인하고, 바뀐 곳 목록과 뜻이 바뀔 수 있는 수정을 냅니다.

인자(JSON 하나):
  {"original": "원문",
   "edited": "고친 글",
   "protect": ["바꾸면 안 되는 말", ...],   # 선택: 제품명·법령명·고정 문구
   "allow": ["사용자가 바꾸라고 한 말", ...]} # 선택: 이 말은 사라지거나 바뀌어도 됨(사용자 요청)
지키는 값(kind):
  date · time · number(단위 포함: 원·%·명·개·개월 …) · weekday「(월)」 · phone · email · url
  name(영문·숫자 섞인 이름: KloudChat, GPT-4o) · org(㈜·주식회사·○○대학교 …) · person(「김민수 팀장」의 김민수)
  quote(「」·“” 안의 인용) · protect(사용자가 준 말)
결과:
  lost            원문에 있던 값이 고친 글에서 사라지거나 바뀜(되돌려야 함)
  format_changed  값은 같고 적는 법만 바뀜(「1,000원」→「1000원」) — 원문 표기로 되돌림
  added           원문에 없던 값이 생김(새 주장 — 빼야 함)
  changes         바뀐 곳(어절 단위): {line, before, after, type, risk}
                  type: spacing(띄어쓰기만) · edit · insert · delete
                  risk: negation(부정) · modality(의무·가능) · scope(범위·수량) · tense(시제) ·
                        connective(및↔또는·인과) · value(값이 걸림) · removed(내용 삭제) · inserted(내용 추가)
  risky           risk 가 있는 changes — 「의미가 바뀔 수 있는 수정」 목록의 바탕
  pass            lost·added 가 없으면 true
마지막 줄에 요약 JSON을 냅니다. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import difflib
import json
import re
import sys
from collections import Counter

_H = "가-힣"

# ── protected values ─────────────────────────────────────────────────────

_URL = re.compile(r"https?://[^\s<>()「」\"']+|www\.[^\s<>()「」\"']+")
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+82[\s\-]?)?0\d{1,2}[\s\-.)]\d{3,4}[\s\-.]\d{4}(?!\d)|(?<!\d)1[5-9]\d{2}-\d{4}(?!\d)")
_DATE_YMD = re.compile(r"(?<!\d)(\d{4})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{1,2})(?!\d)\.?")
_DATE_KO = re.compile(r"(?<!\d)(?:(\d{4})\s*년\s*)?(\d{1,2})\s*월(?:\s*(\d{1,2})\s*일)?")
_DATE_MD = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})(?![\d/])")
_YEAR = re.compile(r"(?<!\d)(\d{4})\s*년(?!\s*\d)")
_WEEKDAY = re.compile(r"\(([월화수목금토일])\)")
_TIME = re.compile(r"(?:오전|오후|새벽|저녁|밤)\s*\d{1,2}\s*시(?:\s*(?:\d{1,2}\s*분|반))?|(?<!\d)\d{1,2}:\d{2}(?::\d{2})?(?!\d)"
                   r"|(?<![\d.])\d{1,2}\s*시\s*(?:\d{1,2}\s*분|반)")
_KO_UNITS = ("개월", "개국", "개사", "분기", "시간", "주년", "주일", "학년", "학기", "가구", "세대", "페이지", "퍼센트",
             "%p", "%", "원", "달러", "엔", "위안", "유로", "명", "개", "건", "배", "회", "번", "차", "년", "달", "주", "일",
             "분", "초", "세", "살", "곳", "대", "권", "장", "쪽", "위", "등", "점", "호", "층", "평", "㎡", "㎞", "기", "인",
             "팀", "박", "시", "종", "가지", "단계", "포인트", "bp")
_LATIN_UNITS = ("km", "kg", "mg", "ml", "GB", "TB", "MB", "kWh", "MW", "kW", "m", "g", "t", "L", "x")
_MULT = r"(?:\s*(?:조|억|천만|백만|십만|만|천|백))*"
_NUMBER = re.compile(
    r"(?<![\w.])[-+]?\d[\d,]*(?:\.\d+)?" + _MULT
    + r"(?:\s*(?:" + "|".join(map(re.escape, _KO_UNITS)) + r")"
    + r"|\s?(?:" + "|".join(map(re.escape, _LATIN_UNITS)) + r")(?![A-Za-z]))?"
)
_WORD_NUMBER = re.compile(
    rf"(?<![{_H}])(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|스무|서른|마흔|쉰|수십|수백|수천|수만|몇)\s*"
    r"(?:명|개|번|배|곳|가지|달|해|시간|차례|사람|권|대|주|살|건)(?![가-힣]{2})"
)
_LATIN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9&+.\-]*[A-Za-z0-9+](?![A-Za-z0-9])")
_ORG = re.compile(
    rf"(?:\(주\)|㈜|주식회사|\(재\)|\(사\)|사단법인|재단법인)\s?[{_H}A-Za-z0-9]+"
    rf"|[{_H}A-Za-z0-9]+(?:\(주\)|㈜)"
    rf"|[{_H}]{{2,}}(?:대학교|대학원|병원|의료원|공사|공단|재단|협회|연구원|연구소|은행|증권|보험|시청|구청|군청|도청|"
    r"교육청|그룹|전자|건설|제약|바이오|학회|위원회|진흥원|센터)"
)
_TITLES = ("대표이사", "대표", "본부장", "센터장", "팀장", "부장", "과장", "차장", "대리", "사원", "주임", "교수", "박사",
           "원장", "이사", "상무", "전무", "실장", "소장", "선임", "책임", "수석", "위원", "의원", "장관", "선생님", "님", "씨")
_PERSON = re.compile(rf"(?<![{_H}])([{_H}]{{1,4}})\s?(?={'|'.join(_TITLES)})")
_NOT_NAMES = {"담당", "해당", "우리", "저희", "신임", "기획", "영업", "개발", "마케팅", "인사", "총무", "재무", "홍보",
              "고객", "회원", "여러분", "각", "현", "전", "부", "총", "신", "구", "본", "당", "귀", "모", "그", "이", "저",
              "사업", "운영", "연구", "기술", "품질", "생산", "구매", "경영", "전략", "디자인", "교육", "행정", "학과",
              "팀", "부서", "본부", "센터", "실무", "책임", "수석", "선임", "대표", "공동", "명예", "객원", "겸임", "초빙"}
_SURNAMES = set("김이박최정강조윤장임한오서신권황안송류전홍고문양손배백허유남심노하곽성차주우구민진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용예봉경")
_QUOTE = re.compile(r"「([^」\n]{1,80})」|『([^』\n]{1,80})』|“([^”\n]{1,80})”|\"([^\"\n]{1,80})\"")


def _number_key(raw: str) -> str:
    return re.sub(r"[\s,]", "", raw).lower()


def _ko_date(m: re.Match, raw: str) -> str:
    parts = [m.group(1)] if m.group(1) else []
    parts.append(f"{int(m.group(2)):02d}")
    if m.group(3):
        parts.append(f"{int(m.group(3)):02d}")
    return "-".join(parts)


def _md_date(m: re.Match, raw: str) -> str | None:
    month, day = int(m.group(1)), int(m.group(2))
    return f"{month:02d}-{day:02d}" if 1 <= month <= 12 and 1 <= day <= 31 else None


#: (pattern, kind, key of the match) in the order they are taken; a taken span is masked.
_TAKERS = (
    (_URL, "url", lambda m, r: r.rstrip(".,")),
    (_EMAIL, "email", lambda m, r: r.lower()),
    (_PHONE, "phone", lambda m, r: re.sub(r"\D", "", r)),
    (_DATE_YMD, "date", lambda m, r: f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"),
    (_DATE_KO, "date", _ko_date),
    (_YEAR, "date", lambda m, r: f"{m.group(1)}년"),
    (_DATE_MD, "date", _md_date),
    (_WEEKDAY, "weekday", lambda m, r: m.group(1)),
    (_TIME, "time", lambda m, r: re.sub(r"\s", "", r)),
    (_LATIN, "name", lambda m, r: r if (re.search(r"[A-Z0-9]", r) and len(r) > 1) else None),
    (_NUMBER, "number", lambda m, r: _number_key(r) if re.search(r"\d", r) else None),
    (_WORD_NUMBER, "number", lambda m, r: re.sub(r"\s", "", r)),
    (_ORG, "org", lambda m, r: re.sub(r"\s", "", r)),
)


def protected(text: str, protect: list[str] | None = None) -> list[dict]:
    """The values an edit must keep: `{"kind", "raw", "key", "line"}` in reading order."""
    out: list[dict] = []
    for k, line in enumerate((text or "").split("\n")):
        masked = line
        for rx, kind, key in _TAKERS:
            for m in list(rx.finditer(masked)):
                raw = line[m.start():m.end()].strip()
                value = key(m, raw) if raw else None
                if value is None:
                    continue
                out.append({"kind": kind, "raw": raw, "key": value, "line": k, "pos": m.start()})
                masked = masked[:m.start()] + "\0" * (m.end() - m.start()) + masked[m.end():]

        for m in _PERSON.finditer(masked):
            name = m.group(1)
            if name in _NOT_NAMES or (len(name) == 1 and name not in _SURNAMES):
                continue
            if len(name) >= 2 and any(name.endswith(t) for t in ("팀", "부", "실", "과", "처", "국")):
                continue
            out.append({"kind": "person", "raw": name, "key": name, "line": k, "pos": m.start()})
        for m in _QUOTE.finditer(line):
            inner = next(g for g in m.groups() if g is not None)
            out.append({"kind": "quote", "raw": inner, "key": re.sub(r"\s+", " ", inner).strip(), "line": k,
                        "pos": m.start()})
        for word in protect or []:
            word = str(word).strip()
            if word:
                for m in re.finditer(re.escape(word), line):
                    out.append({"kind": "protect", "raw": word, "key": word, "line": k, "pos": m.start()})
    out.sort(key=lambda t: (t["line"], t["pos"]))
    for t in out:
        t.pop("pos", None)
    return out


def _allowed(token: dict, allow: list[str]) -> bool:
    return any(a and (a in token["raw"] or token["raw"] in a) for a in allow)


def compare(original: str, edited: str, protect: list[str] | None = None,
            allow: list[str] | None = None) -> dict:
    """What survived: lost, format_changed and added protected values."""
    allow = [str(a).strip() for a in allow or [] if str(a).strip()]
    before = protected(original, protect)
    after = protected(edited, protect)
    flat_edited = re.sub(r"\s+", "", edited or "")
    flat_original = re.sub(r"\s+", "", original or "")
    count_before = Counter((t["kind"], t["key"]) for t in before)
    count_after = Counter((t["kind"], t["key"]) for t in after)
    keys_after = Counter(t["key"] for t in after)
    lost, changed, added = [], [], []
    seen: set[tuple[str, str]] = set()
    for t in before:
        ident = (t["kind"], t["key"])
        if ident in seen:
            continue
        seen.add(ident)
        if _allowed(t, allow):
            continue
        n_before = count_before[ident]
        if t["kind"] in ("name", "org", "person", "quote", "protect"):
            n_after = flat_edited.count(re.sub(r"\s+", "", t["raw"]))
        else:
            n_after = max(count_after[ident], keys_after[t["key"]])
        lines = sorted({x["line"] for x in before if (x["kind"], x["key"]) == ident})
        if n_after < n_before:
            lost.append({"kind": t["kind"], "raw": t["raw"], "original": n_before, "edited": n_after,
                         "lines": lines})
            continue
        raws = {x["raw"] for x in before if (x["kind"], x["key"]) == ident}
        missing = [r for r in raws if r not in (edited or "") and re.sub(r"\s+", "", r) not in flat_edited]
        if missing:
            now = sorted({x["raw"] for x in after if x["key"] == t["key"]})
            changed.append({"kind": t["kind"], "raw": missing[0], "now": now[0] if now else "", "lines": lines})
    keys_before = Counter(t["key"] for t in before)
    seen = set()
    for t in after:
        ident = (t["kind"], t["key"])
        if ident in seen or _allowed(t, allow):
            continue
        seen.add(ident)
        if t["kind"] in ("name", "org", "person", "quote", "protect"):
            extra = count_after[ident] > flat_original.count(re.sub(r"\s+", "", t["raw"]))
        else:
            extra = count_after[ident] > max(count_before[ident], keys_before[t["key"]])
        if extra:
            added.append({"kind": t["kind"], "raw": t["raw"], "line": t["line"]})
    return {"protected": len(before), "lost": lost, "format_changed": changed, "added": added}


# ── what changed ─────────────────────────────────────────────────────────

_RISKS: tuple[tuple[str, re.Pattern], ...] = (
    ("negation", re.compile(r"않|없|아니|못(?=[가-힣])|(?<![가-힣])안(?=[가-힣])|말(?:고|아|라|것)|금지|불가|\bnot\b|\bno\b|n't|never", re.I)),
    ("modality", re.compile(r"반드시|꼭|필수|의무|야(?=한|합|하|돼|됩|되|만)|수있|가능|권장|권고|허용|원칙적|예외|must|should|may|can|required", re.I)),
    ("scope", re.compile(r"모든|모두|전체|전부|일부|대부분|항상|늘|절대|전혀|이상|이하|미만|초과|이내|최대|최소|약|대략|정도|까지|부터|씩|"
                         r"매일|매주|매월|매년|다만|단,|only|all|some|every|always|never|at least|up to", re.I)),
    ("tense", re.compile(r"었|았|였|했|됐|겠|예정|할것|될것|완료")),
    ("connective", re.compile(r"및|또는|혹은|이나|거나|그리고|때문|따라서|그러므로|그래서|하지만|그러나|반면|오히려|으므로|이므로|경우|라면|"
                              r"\band\b|\bor\b|because|therefore|but", re.I)),
)


def _risk(before: str, after: str, values: set[str]) -> list[str]:
    flat_b, flat_a = re.sub(r"\s+", "", before), re.sub(r"\s+", "", after)
    reasons = [name for name, rx in _RISKS if Counter(rx.findall(flat_b)) != Counter(rx.findall(flat_a))]
    if any(v and (v in flat_b) != (v in flat_a) for v in values):
        reasons.append("value")
    if not after and len(before.split()) >= 4:
        reasons.append("removed")
    if not before and len(after.split()) >= 4:
        reasons.append("inserted")
    return reasons


def _chars(text: str) -> tuple[str, list[int]]:
    """`text` without whitespace, and each kept character's offset in `text`."""
    kept = [(i, ch) for i, ch in enumerate(text) if not ch.isspace()]
    return "".join(ch for _, ch in kept), [i for i, _ in kept]


def _eojeol(text: str, lo: int, hi: int) -> tuple[int, int]:
    """[lo, hi) widened to whole eojeol (whitespace-delimited words)."""
    while lo > 0 and not text[lo - 1].isspace():
        lo -= 1
    while hi < len(text) and not text[hi].isspace():
        hi += 1
    return lo, hi


def _span(pos: list[int], text: str, i1: int, i2: int) -> tuple[int, int]:
    """Offsets in `text` of stripped characters [i1, i2); an empty range sits at its neighbour."""
    if i1 < i2:
        return pos[i1], pos[i2 - 1] + 1
    if not pos:
        return 0, 0
    at = pos[i1] if i1 < len(pos) else pos[-1] + 1
    return at, at


def _attached(text: str, lo: int, hi: int) -> bool:
    """Whether [lo, hi) touches a word on either side (an edit inside a word, not a whole word)."""
    return (lo > 0 and not text[lo - 1].isspace()) or (hi < len(text) and not text[hi].isspace())


def changes(original: str, edited: str, protect: list[str] | None = None, limit: int = 300) -> list[dict]:
    """Edits by whole eojeol, original line numbers, each with the reasons it may change the meaning.

    Characters are compared without whitespace, so a spacing fix next to a wording change is
    still its own row; spacing-only rows have `type: spacing`."""
    original, edited = original or "", edited or ""
    a, pos_a = _chars(original)
    b, pos_b = _chars(edited)
    values = {re.sub(r"\s+", "", t["raw"]) for t in protected(original, protect) + protected(edited, protect)}
    spans: list[tuple[int, int, int, int, bool, bool]] = []  # offsets in the texts + whether to widen each side
    matcher = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for op, i1, i2, j1, j2 in matcher.get_opcodes():
        if op == "equal":
            # Spacing differences inside an equal run: compare the gap before each character.
            for k in range(1, i2 - i1):
                ga = original[pos_a[i1 + k - 1] + 1:pos_a[i1 + k]]
                gb = edited[pos_b[j1 + k - 1] + 1:pos_b[j1 + k]]
                if bool(ga.strip(" \t")) != bool(gb.strip(" \t")) or bool(ga) != bool(gb):
                    spans.append((pos_a[i1 + k - 1], pos_a[i1 + k], pos_b[j1 + k - 1], pos_b[j1 + k], True, True))
            continue
        lo_a, hi_a = _span(pos_a, original, i1, i2)
        lo_b, hi_b = _span(pos_b, edited, j1, j2)
        # A whole word added or dropped leaves the other text's neighbours out of the row.
        spans.append((lo_a, hi_a, lo_b, hi_b,
                       i1 < i2 or _attached(edited, lo_b, hi_b), j1 < j2 or _attached(original, lo_a, hi_a)))
    merged: list[list[int]] = []
    for lo_a, hi_a, lo_b, hi_b, widen_a, widen_b in sorted(spans):
        if widen_a:
            lo_a, hi_a = _eojeol(original, lo_a, hi_a)
        if widen_b:
            lo_b, hi_b = _eojeol(edited, lo_b, hi_b)
        if merged and (lo_a <= merged[-1][1] or lo_b <= merged[-1][3]):
            last = merged[-1]
            last[1], last[3] = max(last[1], hi_a), max(last[3], hi_b)
            continue
        merged.append([lo_a, hi_a, lo_b, hi_b])
    out: list[dict] = []
    for lo_a, hi_a, lo_b, hi_b in merged:
        before = " ".join(original[lo_a:hi_a].split())
        after = " ".join(edited[lo_b:hi_b].split())
        if before == after:
            continue
        if before and after and before.replace(" ", "") == after.replace(" ", ""):
            kind = "spacing"
        else:
            kind = "edit" if before and after else ("insert" if after else "delete")
        risk = [] if kind == "spacing" else _risk(before, after, values)
        out.append({"line": original.count("\n", 0, lo_a), "before": before, "after": after, "type": kind,
                    "risk": risk})
        if len(out) >= limit:
            break
    return out


RISK_WORDS = {"negation": "부정", "modality": "의무·가능", "scope": "범위·수량", "tense": "시제",
              "connective": "연결·인과", "value": "값", "removed": "내용 삭제", "inserted": "내용 추가"}


def report(original: str, edited: str, protect: list[str] | None = None, allow: list[str] | None = None) -> dict:
    kept = compare(original, edited, protect, allow)
    edits = changes(original, edited, protect)
    risky = [dict(c, risk_words=[RISK_WORDS[r] for r in c["risk"]]) for c in edits if c["risk"]]
    return {**kept, "changes": edits, "risky": risky,
            "counts": {"changes": len(edits), "spacing": sum(c["type"] == "spacing" for c in edits),
                       "risky": len(risky), "lost": len(kept["lost"]), "added": len(kept["added"]),
                       "format_changed": len(kept["format_changed"])},
            "pass": not kept["lost"] and not kept["added"]}


# ── the server's post check (`post_check: preserve`) ────────────────────

#: Headings the edited text stands under; the change log and notes after it are not checked.
EDITED_HEADINGS = ("수정본", "고친 글", "교정본", "다듬은 글", "윤문본", "수정안", "최종본", "교정 결과",
                   "수정한 글", "고친 문장", "다듬은 문장")
_HEADING = re.compile(r"^\s{0,3}(?:#{1,6}\s+|\*\*|\[|【)")
_SECTION_END = re.compile(r"^\s{0,3}(?:#{1,6}\s+|(?:---|\*\*\*)\s*$|\*\*[^*\n]{1,30}\*\*\s*:?\s*$)")
#: An estimate or a plan the original hedged: 「…때문인 것으로 보여진다」 must not become
#: 「…때문이다」.
HEDGES = {
    "것으로 보인다": re.compile(r"것\s?으로\s?(?:보|판단|예상|추정|생각)|(?:로|으로)\s?보(?:인|여|입|임)"),
    "예정": re.compile(r"예정"),
    "추정·가능성": re.compile(r"추정|가능성|듯(?:하|합|싶)|것\s?같"),
}
#: Kinds a new value of is a new claim (a re-worded person or quote is the change log's job).
_CLAIM_KINDS = ("date", "time", "number", "phone", "email", "url", "name", "org")


def section(text: str, names: tuple[str, ...] = EDITED_HEADINGS) -> tuple[int, int] | None:
    """Line range [lo, hi) of the body under the first heading that names one of `names`."""
    lines = (text or "").split("\n")
    widest = max(len(n) for n in names) + 12
    for i, line in enumerate(lines):
        bare = re.sub(r"[#*\[\]【】:：()\s]", "", line)
        if not bare or len(bare) > widest:
            continue
        if not any(bare.startswith(n.replace(" ", "")) for n in names):
            continue
        if not (_HEADING.match(line) or len(line.strip()) <= widest):
            continue
        lo = hi = i + 1
        while hi < len(lines):
            if hi > lo and _SECTION_END.match(lines[hi]) and any(x.strip() for x in lines[lo:hi]):
                break
            hi += 1
        return lo, hi
    return None


def original_text(args: dict) -> tuple[str, bool]:
    """The text the person gave to edit, and whether it could be told from the instruction."""
    attached = str(args.get("attached") or "")
    if attached.strip():
        return attached, True
    request = str(args.get("request") or "")
    fenced = re.search(r"```[^\n]{0,20}\n([\s\S]{20,}?)\n```", request)
    if fenced:
        return fenced.group(1), True
    blocks = [b for b in re.split(r"\n\s*\n", request.strip()) if b.strip()]
    if len(blocks) > 1 and len(blocks[0]) < 400:
        return "\n\n".join(blocks[1:]), True
    quoted = re.search(r"[「“]([^」”]{40,})[」”]", request)
    if quoted:
        return quoted.group(1), True
    after = re.search(r"[:：]\s*\n?([\s\S]{40,})$", request)
    if after and len(after.group(1)) > len(request) / 2:
        return after.group(1), True
    return request, False


def _replace_once(text: str, old: str, new: str) -> str:
    if old and old in text:
        return text.replace(old, new, 1)
    # Line breaks and runs of spaces in the edited text need not match the row's.
    pattern = r"\s+".join(re.escape(part) for part in old.split())
    return re.sub(pattern, lambda _m: new, text, count=1) if pattern else text


def post(text: str, args: dict) -> dict:
    """The edited section against the original: a value only re-written goes back to how the
    original wrote it (code); a new value, a lost one and an added sentence go to the writer;
    on the final pass a new value still there is taken out with the edit that brought it."""
    out: dict = {"text": text, "fixed": [], "rewrite": [], "flags": []}
    span = section(text)
    if span is None:
        return out
    lo, hi = span
    lines = text.split("\n")
    edited = "\n".join(line for line in lines[lo:hi] if not line.strip().startswith("```"))
    original, separated = original_text(args)
    if not edited.strip() or not original.strip():
        return out
    final = bool(args.get("final"))
    kept = compare(original, edited)
    allowed = {t["key"] for t in protected(original)} | {
        t["key"] for t in protected(str(args.get("request") or ""))}
    body = "\n".join(lines[lo:hi])
    for row in kept["format_changed"]:
        if row.get("now") and row.get("raw") and row["now"] in body:
            body = body.replace(row["now"], row["raw"], 1)
            out["fixed"].append({"kind": "format", "text": row["now"],
                                 "why": f"원문 표기 「{row['raw']}」로 되돌림"})
    new = []
    for token in protected(edited):
        if token["kind"] in _CLAIM_KINDS and token["key"] not in allowed and token["raw"] not in new:
            new.append(token["raw"])
    edits = changes(original, edited)
    if final:
        for raw in new:
            row = next((c for c in edits if raw in c["after"] and c["type"] in ("insert", "edit")), None)
            if row is not None and row["type"] == "insert":
                body = _replace_once(body, row["after"], "")
                how = "보탠 말을 뺌"
            elif row is not None and row["before"] and body.count(row["after"]) == 1:
                body = _replace_once(body, row["after"], row["before"])
                how = f"원문 「{row['before'][:30]}」로 되돌림"
            else:
                body = body.replace(raw, "[확인 필요]", 1)
                how = "빈칸으로 둠"
            out["fixed"].append({"kind": "added", "text": raw, "why": f"원문에 없는 값 — {how}"})
        body = re.sub(r"(?<=\S) {2,}", " ", body).replace(" ,", ",").replace(" .", ".")
        # A reverted row can meet the word the diff kept beside it: 「전분기 대비 대비」.
        body = re.sub(r"(?<!\S)(\S{1,20}) \1(?!\S)",
                      lambda m: m.group(0) if m.group(0) in original else m.group(1), body)
    else:
        out["rewrite"] += [{"kind": "added", "text": raw,
                            "why": "원문에 없는 값입니다. 빼고 원문의 표현으로 되돌리세요."} for raw in new]
    if separated:
        for row in kept["lost"]:
            item = {"kind": "lost", "text": row["raw"],
                    "why": "원문의 값이 수정본에서 사라지거나 바뀌었습니다. 원문대로 되돌리세요."}
            (out["flags"] if final else out["rewrite"]).append(item)
    for name, rx in HEDGES.items():
        if len(rx.findall(edited)) < len(rx.findall(original)):
            item = {"kind": "hedge", "text": name,
                    "why": "원문의 추정·계획 표현을 단정으로 바꿨습니다. 원문처럼 두세요."}
            (out["flags"] if final else out["rewrite"]).append(item)
    for row in edits:
        grown = len(row["after"].split()) - len(row["before"].split())
        # A sentence of its own the original does not have: 「…있습니다. 그 경우 미리 말씀드리겠습니다.」
        new_sentence = grown >= 3 and len(re.findall(r"[.!?](?:\s|$)", row["after"] + " ")) > \
            len(re.findall(r"[.!?](?:\s|$)", row["before"] + " ")) and not row["before"].endswith(
                ("며", "고", "는데", "으나", "지만"))
        if row["type"] in ("insert", "edit") and (grown >= 6 or new_sentence):
            item = {"kind": "inserted", "text": row["after"][:160],
                    "why": "원문에 없는 내용(새 주장)을 보탰습니다. 빼세요."}
            (out["flags"] if final else out["rewrite"]).append(item)
        elif set(row["risk"]) & {"negation", "modality", "scope"} and "value" not in row["risk"]:
            out["flags"].append({"kind": "risky", "text": f"{row['before']}→{row['after']}"[:160],
                                 "why": "뜻이 바뀔 수 있는 수정(" + "·".join(
                                     RISK_WORDS[r] for r in row["risk"]) + ")"})
    lines[lo:hi] = body.split("\n")
    out["text"] = "\n".join(lines)
    return out


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    if not isinstance(options, dict) or "original" not in options or "edited" not in options:
        print(json.dumps({"ok": False, "error": "original 과 edited 가 필요합니다"}, ensure_ascii=False))
        return 1
    result = report(str(options["original"] or ""), str(options["edited"] or ""),
                    [str(p) for p in options.get("protect") or []], [str(a) for a in options.get("allow") or []])
    for t in result["lost"]:
        print(f"사라짐/바뀜 {t['kind']}「{t['raw']}」 원문 {t['original']}회 → {t['edited']}회 (줄 {t['lines']})")
    for t in result["format_changed"]:
        print(f"표기 바뀜 {t['kind']}「{t['raw']}」→「{t['now']}」")
    for t in result["added"]:
        print(f"새로 생김 {t['kind']}「{t['raw']}」 (줄 {t['line']})")
    for c in result["risky"]:
        print(f"뜻 확인 줄 {c['line']}: 「{c['before']}」→「{c['after']}」 ({', '.join(c['risk_words'])})")
    print(f"바뀐 곳 {result['counts']['changes']}개 · 지킬 값 {result['protected']}개 · "
          + ("통과" if result["pass"] else "되돌릴 것 있음"))
    print(json.dumps({"ok": True, **result}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
