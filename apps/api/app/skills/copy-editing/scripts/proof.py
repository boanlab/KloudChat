"""한국어 글의 자주 틀리는 맞춤법·띄어쓰기·표준어를 규정 조항과 함께 찾고, 문장 끝 어투(합니다체·해요체·한다체·개조식)가 섞인 곳을 짚습니다."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass

_H = "가-힣"
_NB = rf"(?<![{_H}])"

# 한글 맞춤법 조항(국립국어원, 2017 개정 고시).
B2 = "한글 맞춤법 제2항(문장의 각 단어는 띄어 씀)"
B27 = "한글 맞춤법 제27항 붙임 2(「며칠」)"
B30 = "한글 맞춤법 제30항(두 음절 한자어의 사이시옷은 곳간·셋방·숫자·찻간·툇간·횟수만)"
B35 = "한글 맞춤법 제35항 붙임 2(「되어→돼」「뵈어→봬」 준 대로 적음)"
B42 = "한글 맞춤법 제42항(의존 명사는 띄어 씀)"
B43 = "한글 맞춤법 제43항(단위를 나타내는 명사는 띄어 씀)"
B47 = "한글 맞춤법 제47항(보조 용언: 「-지 않다」는 붙여 쓰기 허용 대상이 아님)"
B51 = "한글 맞춤법 제51항(부사 끝음절 「-이/-히」)"
B53 = "한글 맞춤법 제53항(「-(으)ㄹ게」「-(으)ㄹ걸」은 예사소리로 적음)"
S17 = "표준어 규정 제17항(「-습니다」가 표준어, 「-읍니다」 버림)"
S20 = "표준어 규정 제20항(「설거지」)"
DICT = "표준국어대사전"


def _jong(ch: str) -> int:
    code = ord(ch) - 0xAC00
    return code % 28 if 0 <= code < 11172 else -1


Replace = Callable[[re.Match], "str | None"] | str


@dataclass(frozen=True)
class Rule:
    kind: str
    pattern: str
    replace: Replace
    basis: str
    sure: bool = True
    why: str = ""


def _after_final(jongs: tuple[int, ...]):
    """A guard: the syllable just before the match ends in one of `jongs`."""
    def ok(line: str, start: int) -> bool:
        return start > 0 and _jong(line[start - 1]) in jongs
    return ok


L, N = 8, 4  # ㄹ, ㄴ finals (관형사형 -ㄹ/-ㄴ/-는)

RULES: tuple[tuple[Rule, Callable[[str, int], bool] | None], ...] = (
    (Rule("spelling", r"됬", "됐", B35), None),
    (Rule("spelling", r"되(?=요|서|야)", "돼", B35, why="「되어요·되어서·되어야」가 줄면 「돼요·돼서·돼야」."), None),
    (Rule("spelling", r"뵈(?=요)|뵜", lambda m: "봬" if m.group(0) == "뵈" else "뵀", B35), None),
    (Rule("spelling", r"몇\s?일(?![본반])", "며칠", B27), None),
    (Rule("spelling", r"(?<=[가-힣])께(?=요|\s|[.,!?]|$)", "게", B53, why="「-ㄹ께」는 「-ㄹ게」."), _after_final((L,))),
    (Rule("spelling", r"(?<=[가-힣])껄(?=요|\s|[.,!?]|$)", "걸", B53), _after_final((L,))),
    (Rule("spelling", _NB + r"(깨끗|일일|곰곰|틈틈|번번|겹겹|따뜻|느긋|반듯|버젓|산뜻)히", r"\1이", B51), None),
    (Rule("spelling", _NB + r"(꼼꼼|솔직|조용|섭섭|넉넉|가만|쓸쓸|당당|정확)이(?=[\s,.!?]|$)", r"\1히", B51), None),
    (Rule("spelling", r"갯수", "개수", B30), None),
    (Rule("spelling", r"댓가", "대가", B30), None),
    (Rule("spelling", r"촛점", "초점", B30), None),
    (Rule("spelling", r"싯가", "시가", B30), None),
    (Rule("spelling", r"(?<=[있없])슴(?=[\s.,]|$)", "음", DICT, why="명사형 어미는 「-음」(있음·없음)."), None),
    (Rule("spelling", r"(?<=[있없었았였겠])읍니다", "습니다", S17), None),
    (Rule("spelling", r"할려(?=고|면|는)", "하려", DICT, why="의도는 「-(으)려고」: 「하려고」."), None),
    (Rule("spelling", r"웬지", "왠지", DICT, why="「왠지」는 「왜인지」가 준 말."), None),
    (Rule("spelling", r"왠(?=일|만|걸|\s?떡)", "웬", DICT, why="「웬일·웬만하다」의 「웬」은 관형사."), None),
    (Rule("spelling", r"금새", "금세", DICT, sure=False, why="「금시에」가 준 말은 「금세」(「금새」는 물건값)."), None),
    (Rule("spelling", r"어떻해", "어떡해", DICT, sure=False, why="「어떻게 해」가 준 말은 「어떡해」. 「어떻게」를 쓰려던 것인지 본다."), None),
    (Rule("spelling", r"던지\s+말던지", "든지 말든지", DICT, why="고르는 뜻은 「-든지」, 지난 일은 「-던지」."), None),
    (Rule("spacing", r"수(?=\s?(?:있|없|도\s|밖에))", " 수", B42), _after_final((L,))),
    (Rule("spacing", r"것(?=\s?(?:같|이다|입니다|이에요|은|을|이|도|만|처럼)|[.,]|$)", " 것", B42), _after_final((L, N))),
    (Rule("spacing", r"때(?=[\s,.]|에|는|도|마다|까지|부터|가|의|$)", " 때", B2, why="「때」는 명사."), _after_final((L,))),
    (Rule("spacing", r"줄(?=\s?(?:알|안다|안다고|압니|아는|아세|아니|몰|모르|모릅))", " 줄", B42), _after_final((L,))),
    (Rule("spacing", r"대로(?=[\s,.]|$)", " 대로", B42, sure=False, why="관형사형 뒤 「대로」는 의존 명사."), _after_final((L, N))),
    (Rule("spacing", r"데로(?=[\s,.]|$)", " 대로", B42, why="「-ㄴ 대로」(그 모양과 같이)."), _after_final((L, N))),
    (Rule("spacing", r"만큼(?=[\s,.]|$)", " 만큼", B42, sure=False, why="관형사형 뒤는 의존 명사(「먹을 만큼」), 체언 뒤는 조사(「산만큼」)."),
     _after_final((L, N))),
    (Rule("spacing", r"뿐(?=[\s,.]|이|만|$)", " 뿐", B42, sure=False, why="관형사형 뒤는 의존 명사(「할 뿐」), 체언 뒤는 조사."), _after_final((L, N))),
    (Rule("spacing", r"(?<=[가-힣]지)(?=않|못하)", " ", B47), None),
    (Rule("spacing", _NB + r"(한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)(개|명|가지|곳|권|마리|살|시간|달|주일)"
          r"(?=[\s,.]|씩|가|를|는|의|도|와|에|만|이|은|을|과|로|$)", r"\1 \2", B43), None),
    (Rule("standard", r"설겆이", "설거지", S20), None),
    (Rule("standard", r"희안(?=하|한)", "희한", DICT), None),
    (Rule("standard", r"어의\s?없", "어이없", DICT), None),
    (Rule("standard", r"역활", "역할", DICT), None),
    (Rule("standard", r"오랫만", "오랜만", DICT), None),
    (Rule("standard", r"요컨데", "요컨대", DICT), None),
    (Rule("standard", r"내노라", "내로라", DICT), None),
    (Rule("standard", r"설레임", "설렘", DICT), None),
    (Rule("standard", r"삼가해", "삼가", DICT, why="기본형은 「삼가다」(「삼가 주세요」)."), None),
    (Rule("standard", r"삼가하", "삼가", DICT, why="기본형은 「삼가다」(「삼가시기」)."), None),
    (Rule("standard", r"(?<![가-힣])바램(?=[이을은과도]|\s|$)", "바람", DICT, sure=False,
          why="바라는 일은 「바람」. 색이 바랜 것이면 「바램」이 맞다."), None),
    (Rule("standard", r"건데기", "건더기", DICT), None),
    (Rule("standard", r"애띠|앳띠", "앳되", DICT), None),
    (Rule("standard", r"(?<![가-힣])구지(?=\s)", "굳이", DICT), None),
)

_COMPILED = tuple((rule, re.compile(rule.pattern), guard) for rule, guard in RULES)
_QUOTE = re.compile(r"「[^」\n]*」|『[^』\n]*』|“[^”\n]*”|\"[^\"\n]*\"|‘[^’\n]*’|`[^`\n]*`")
_URLISH = re.compile(r"https?://\S+|\S+@\S+\.\S+")
#: Words a spacing rule would wrongly split: 「말수」「별것」「그런대로」 …
_NOUNS = ("말수", "물수", "술수", "날것", "들것", "탈것", "별것", "물때", "그런대로", "그런데로", "이런대로")


def _skip_spans(line: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for rx in (_QUOTE, _URLISH) for m in rx.finditer(line)]


def scan(text: str) -> list[dict]:
    """Spelling, spacing and standard-word findings, line by line."""
    out: list[dict] = []
    for k, line in enumerate((text or "").split("\n")):
        skip = _skip_spans(line)
        taken: list[tuple[int, int]] = []
        for rule, rx, guard in _COMPILED:
            for m in rx.finditer(line):
                start, end = m.start(), m.end()
                if any(a <= start < b for a, b in skip) or any(a < max(end, start + 1) and start < b for a, b in taken):
                    continue
                if guard is not None and not guard(line, start):
                    continue
                if rule.kind == "spacing" and any(
                        n in line[max(0, start - len(n)):start + len(n)] for n in _NOUNS):
                    continue
                fixed = rule.replace(m) if callable(rule.replace) else m.expand(rule.replace)
                if fixed is None:
                    continue
                # A spacing fix inserts at `start`; show the eojeol around it.
                left = start
                while left > 0 and not line[left - 1].isspace():
                    left -= 1
                right = end
                while right < len(line) and not line[right].isspace():
                    right += 1
                before = line[left:right]
                after = line[left:start] + fixed + line[end:right]
                if before == after:
                    continue
                taken.append((start, max(end, start + 1)))
                out.append({"kind": rule.kind, "line": k, "start": start, "end": end, "match": m.group(0),
                            "replace": fixed, "before": before, "after": after, "basis": rule.basis,
                            "sure": rule.sure, "why": rule.why})
    out.sort(key=lambda f: (f["line"], f["start"]))
    return out


def apply(text: str, findings: list[dict], only_sure: bool = True) -> str:
    lines = (text or "").split("\n")
    by_line: dict[int, list[dict]] = {}
    for f in findings:
        if f.get("kind") == "register" or (only_sure and not f.get("sure")):
            continue
        by_line.setdefault(int(f["line"]), []).append(f)
    for k, items in by_line.items():
        line = lines[k]
        for f in sorted(items, key=lambda f: f["start"], reverse=True):
            if line[f["start"]:f["end"]] != f["match"]:
                continue
            line = line[:f["start"]] + f["replace"] + line[f["end"]:]
        lines[k] = line
    return "\n".join(lines)


REGISTERS = {"hamnida": "합니다체", "haeyo": "해요체", "handa": "한다체", "bullet": "개조식"}
_BULLET = re.compile(r"^\s*(?:[-*•·▪◦○●□■※]|\d{1,2}[.)]|[가-하][.)]|\(\d{1,2}\))\s*")
_HEADING = re.compile(r"^\s*(?:#{1,6}\s|제\s?\d+\s?[장절조항]|\[[^\]]+\]\s*$|[IVX]+\.)")
_ENDINGS = (
    ("hamnida", re.compile(r"(?:십시오|시오|[가-힣]시다)$")),
    ("haeyo", re.compile(r"(?:요|죠)$")),
    ("handa", re.compile(r"(?:[다라냐자]|는가|던가|은가|인가)$")),
    ("bullet", re.compile(r"(?:음|함|임|됨|짐|김|림|움|봄|옴|줌|함\)|기|것|예정|필요|완료|요망|바람|중|등|및)$")),
)
_SPLIT = re.compile(r"(?<=[.!?])\s+")
# a line with no full stop is split at a sentence ending; never inside 「생각보다 잘」
_SPLIT_BARE = re.compile(r"(?<!보)(?<=[다요까])\s+(?=[가-힣A-Z「\"])")


def _ending(sentence: str) -> str:
    s = _QUOTE.sub("", sentence).strip()
    s = re.sub(r"[\s.!?…~)\]」』”\"']+$", "", s)
    if not s:
        return ""
    if re.search(r"(?:니다|니까)$", s):
        # 합니다·했습니다·입니다 (ㅂ 받침 또는 「습」 뒤); 「아니다」는 한다체
        return "hamnida" if len(s) > 2 and (s[-3] == "습" or _jong(s[-3]) == 17) else "handa"
    for name, rx in _ENDINGS:
        if rx.search(s):
            return name
    return ""


def register(text: str, target: str = "") -> dict:
    """Sentence endings by register; prose sentences off the body's register are listed."""
    rows: list[dict] = []
    for k, line in enumerate((text or "").split("\n")):
        if not line.strip() or _HEADING.match(line):
            continue
        bullet = bool(_BULLET.match(line))
        body = _BULLET.sub("", line, count=1) if bullet else line
        split = _SPLIT if re.search(r"[.!?]", body) else _SPLIT_BARE
        for sentence in (s for s in split.split(body) if s.strip()):
            name = _ending(sentence)
            if not name:
                continue
            rows.append({"line": k, "sentence": sentence.strip()[:120], "register": name, "bullet": bullet})
    prose = [r for r in rows if not r["bullet"] and r["register"] != "bullet"]
    counts: dict[str, int] = {}
    for r in prose:
        counts[r["register"]] = counts.get(r["register"], 0) + 1
    bullets: dict[str, int] = {}
    for r in rows:
        if r["bullet"]:
            bullets[r["register"]] = bullets.get(r["register"], 0) + 1
    main = target if target in REGISTERS else (max(counts, key=counts.get) if counts else "")
    off = [r for r in prose if main and r["register"] != main]
    bullet_main = max(bullets, key=bullets.get) if bullets else ""
    off += [r for r in rows if r["bullet"] and bullet_main and r["register"] != bullet_main]
    return {
        "main": REGISTERS.get(main, ""), "main_key": main,
        "counts": {REGISTERS[k]: v for k, v in counts.items()},
        "bullet_main": REGISTERS.get(bullet_main, ""),
        "bullet_counts": {REGISTERS[k]: v for k, v in bullets.items()},
        "off": [{"kind": "register", "line": r["line"], "sentence": r["sentence"], "register": REGISTERS[r["register"]],
                 "expected": REGISTERS[main if not r["bullet"] else bullet_main]} for r in off],
        "mixed": bool(off),
    }


def _row(label: str, text: str, do_apply: bool, target: str) -> dict:
    found = scan(text)
    row = {"label": label, "findings": found, "register": register(text, target),
           "counts": {k: sum(1 for f in found if f["kind"] == k) for k in ("spelling", "spacing", "standard")},
           "unsure": sum(1 for f in found if not f["sure"])}
    if do_apply:
        row["corrected"] = apply(text, found)
        row["left"] = len(scan(row["corrected"]))
    return row


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    if not isinstance(options, dict):
        options = {}
    items = options.get("items")
    if not items and options.get("text"):
        items = [{"label": "글", "text": options["text"]}]
    if not isinstance(items, list) or not items:
        print(json.dumps({"ok": False, "error": "text 또는 items 가 필요합니다"}, ensure_ascii=False))
        return 1
    target = str(options.get("register") or "")
    if target and target not in REGISTERS:
        print(json.dumps({"ok": False, "error": f"register 는 {', '.join(REGISTERS)} 중 하나"}, ensure_ascii=False))
        return 1
    rows = []
    for n, item in enumerate(items[:100], start=1):
        item = item if isinstance(item, dict) else {"text": str(item)}
        rows.append(_row(str(item.get("label") or f"글 {n}"), str(item.get("text") or ""),
                         bool(options.get("apply")), target))
    for row in rows:
        marks = [f"{f['before']}→{f['after']}{'' if f['sure'] else '(?)'}" for f in row["findings"]]
        reg = row["register"]
        print(f"{row['label']}: " + (", ".join(marks[:30]) or "맞춤법·띄어쓰기 지적 없음")
              + f" · 어투 {reg['main'] or '-'}" + (f", 섞임 {len(reg['off'])}곳" if reg["mixed"] else ""))
    print(json.dumps({"ok": True, "items": rows,
                      "note": "자주 틀리는 것만 담은 목록입니다. 지적이 없어도 읽어 보고 고칩니다."}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
