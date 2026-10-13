"""광고·SNS 문구를 채널·필드별 글자 수 한도(네이버·카카오·메타·구글·유튜브·인스타그램·링크드인)로 재고, 피드에서 「더 보기」 전에 보이는 부분을 알려 줍니다."""

from __future__ import annotations

import json
import re
import sys
import unicodedata

UNITS = ("chars", "chars_dw", "chars_no_spaces", "bytes_utf8")

#: channel → field → rule; `hard` = platform rejects/cuts, else advisory (`max` = ceiling).
#: `visible`: shown before 「더 보기」. Checked 2026-10-08; 「2차」 = no official source.
SPECS: dict[str, dict] = {
    "naver_powerlink": {
        "name": "네이버 파워링크",
        "source": "네이버 광고등록기준 https://ads.naver.com/adguide/269 (제목) /271 (설명) /1221 (추가제목) /1222 (홍보문구)",
        "fields": {
            "title": {"limit": 15, "unit": "chars", "hard": True, "note": "띄어쓰기 포함. 띄어쓰기 보정 뒤 15자를 넘으면 수정요청"},
            "description": {"limit": 45, "min": 20, "unit": "chars", "hard": True, "note": "20~45자, 띄어쓰기 포함. 공백 제외 15자 이상은 키워드 관련 내용. 전화번호·URL 금지, 같은 문장부호 반복 금지"},
            "extra_title": {"limit": 15, "unit": "chars", "hard": True},
            "promo_text": {"limit": 45, "unit": "chars", "hard": True},
        },
    },
    "kakao_keyword": {
        "name": "카카오 키워드광고",
        "source": "https://kakaobusiness.gitbook.io/main/ad/searchad/keywordad/content-guide",
        "fields": {
            "title": {"limit": 15, "unit": "chars", "hard": True, "note": "한/영 무관, 띄어쓰기도 한 글자"},
            "description": {"limit": 45, "unit": "chars", "hard": True},
            "extra_title": {"limit": 24, "unit": "chars", "hard": True},
            "sublink": {"limit": 6, "unit": "chars", "hard": True},
        },
    },
    "kakao_bizboard": {
        "name": "카카오 비즈보드",
        "source": "https://kakaobusiness.gitbook.io/main/ad/moment/performance/talkboard/content-guide",
        "fields": {
            "main_copy": {"unit": "chars", "px_em": 48, "px_max": 796, "note": "글자 수 한도 없음, 1029×258 이미지 안 픽셀 기준(메인 Spoqa Han Sans Bold 48pt). 두 줄 중 하나는 290px 이상, 최대 폭 796px 권장. 이모지·특수기호 금지. px 는 추정치이므로 카카오 소재 미리보기로 확인"},
            "sub_copy": {"unit": "chars", "px_em": 39, "px_max": 796, "note": "서브 Regular 39pt. 메인과 같은 문구 금지"},
        },
    },
    "kakao_display": {
        "name": "카카오 모먼트 디스플레이(이미지 네이티브)",
        "source": "https://kakaobusiness.gitbook.io/main/ad/moment/performance/displayad/content-guide",
        "fields": {
            "profile_name": {"limit": 20, "unit": "chars", "hard": True},
            "title": {"limit": 25, "unit": "chars", "hard": True, "note": "기기에 따라 「…」로 잘릴 수 있음. ☆♡☞ 같은 특수문자 금지"},
            "promo_text": {"limit": 45, "unit": "chars", "hard": True},
            "slide_title": {"limit": 25, "unit": "chars", "hard": True},
            "slide_price": {"limit": 10, "unit": "chars", "hard": True},
        },
    },
    "kakao_message": {
        "name": "카카오 채널 메시지",
        "source": "https://kakaobusiness.gitbook.io/main/ad/moment/messagead/channelmessage/content-guide",
        "fields": {
            "basic_text": {"limit": 1300, "unit": "chars", "hard": True, "note": "줄바꿈 99개까지"},
            "wide_image_text": {"limit": 76, "unit": "chars", "hard": True},
            "wide_list_title": {"limit": 20, "unit": "chars", "hard": True},
            "wide_list_first": {"limit": 25, "unit": "chars", "hard": True},
            "wide_list_item": {"limit": 30, "unit": "chars", "hard": True},
            "carousel_title": {"limit": 20, "unit": "chars", "hard": True},
            "carousel_promo": {"limit": 180, "unit": "chars", "hard": True},
            "button": {"limit": 8, "unit": "chars", "hard": True},
        },
    },
    "meta": {
        "name": "메타(페이스북·인스타그램) 광고",
        "source": "Meta 광고 가이드 https://www.facebook.com/business/ads-guide/update/image/instagram-feed (권장), Marketing API asset_feed_spec https://developers.facebook.com/docs/marketing-api/ad-creative/asset-feed-spec/options/ (최대)",
        "fields": {
            "primary_text": {"limit": 125, "max": 1024, "unit": "chars", "hard": False, "visible": 125, "note": "권장 125자(페이스북 피드 권장 50~150). 앞부분에 핵심"},
            "headline": {"limit": 40, "max": 255, "unit": "chars", "hard": False, "note": "인스타그램 피드 권장 40자, 페이스북 피드 권장 27자"},
            "description": {"limit": 30, "max": 255, "unit": "chars", "hard": False, "note": "권장 30자는 2차 자료(Meta 공식 문서에 없음). 노출되지 않는 지면이 많음"},
        },
    },
    "google_rsa": {
        "name": "Google 반응형 검색광고",
        "source": "https://support.google.com/google-ads/answer/7684791 (한국어 등 두 배 너비 문자는 2자로 셈)",
        "fields": {
            "headline": {"limit": 30, "unit": "chars_dw", "hard": True, "note": "한글은 2자 → 한글만이면 15자"},
            "description": {"limit": 90, "unit": "chars_dw", "hard": True, "note": "한글만이면 45자"},
            "path": {"limit": 15, "unit": "chars_dw", "hard": True},
        },
    },
    "google_demand_gen": {
        "name": "Google 디맨드젠",
        "source": "https://support.google.com/google-ads/answer/17091672 (두 배 너비 규칙은 RSA 문서 기준으로 적용한 추정)",
        "fields": {
            "headline": {"limit": 40, "unit": "chars_dw", "hard": True, "note": "적어도 하나는 30 이하"},
            "description": {"limit": 90, "unit": "chars_dw", "hard": True},
            "business_name": {"limit": 25, "unit": "chars_dw", "hard": True},
        },
    },
    "google_video": {
        "name": "Google 동영상(유튜브) 광고",
        "source": "https://support.google.com/google-ads/answer/17091270 (두 배 너비 규칙은 추정)",
        "fields": {
            "headline": {"limit": 30, "unit": "chars_dw", "hard": True},
            "long_headline": {"limit": 90, "unit": "chars_dw", "hard": True},
            "description": {"limit": 90, "unit": "chars_dw", "hard": True},
            "cta": {"limit": 10, "unit": "chars_dw", "hard": True},
        },
    },
    "youtube": {
        "name": "유튜브 영상",
        "source": "https://support.google.com/youtube/answer/57404, https://developers.google.com/youtube/v3/docs/videos",
        "fields": {
            "title": {"limit": 100, "unit": "chars", "hard": True, "visible": 40, "note": "「<」「>」 금지. 목록·모바일에서 앞부분만 보이므로 핵심을 앞에(40자는 관행)"},
            "description": {"limit": 5000, "unit": "bytes_utf8", "hard": True, "note": "API 기준 5,000바이트(한글 약 1,666자). 고객센터는 5,000자. 첫 2~3줄이 「더보기」 전에 보임"},
        },
    },
    "instagram": {
        "name": "인스타그램 게시물",
        "source": "해시태그 5개: https://www.facebook.com/help/instagram/351460621611097 ; 캡션 2,200자·더 보기 전 125자는 2차 자료",
        "fields": {
            "caption": {"limit": 2200, "unit": "chars", "hard": True, "visible": 125, "hashtags": 5, "note": "해시태그는 게시물당 최대 5개(초과 시 게시 안 됨)"},
            "bio": {"limit": 150, "unit": "chars", "hard": True, "note": "2차 자료"},
        },
    },
    "linkedin": {
        "name": "링크드인",
        "source": "게시물 https://www.linkedin.com/help/linkedin/answer/a528176 ; 광고 https://www.linkedin.com/help/lms/answer/a426534 ; 더 보기 전 약 210자는 2차 자료",
        "fields": {
            "post": {"limit": 3000, "unit": "chars", "hard": True, "visible": 210},
            "ad_intro": {"limit": 150, "max": 3000, "unit": "chars", "hard": False},
            "ad_headline": {"limit": 70, "max": 200, "unit": "chars", "hard": False},
            "ad_description": {"limit": 100, "max": 300, "unit": "chars", "hard": False},
        },
    },
    "naver_blog": {
        "name": "네이버 블로그",
        "source": "네이버 서치어드바이저 https://searchadvisor.naver.com/guide/markup-content (제목 글자 수 제한 없음, 검색 결과에 보이는 길이로)",
        "fields": {
            "title": {"unit": "chars", "visible": 30, "note": "공식 한도 없음. 검색어를 앞에, 같은 키워드 2번 이상 반복·관련 없는 홍보 문구는 검색 불이익. 30자는 관행"},
        },
    },
    "email": {
        "name": "이메일",
        "source": "공식 한도 없음(받은편지함 미리보기 관행)",
        "fields": {
            "subject": {"limit": 30, "unit": "chars", "hard": False, "note": "모바일 받은편지함에서 대략 30자 안팎이 보임. 광고성이면 맨 앞에 「(광고)」"},
            "preheader": {"limit": 40, "unit": "chars", "hard": False},
        },
    },
}

_JOINERS = {0x200D, 0xFE0E, 0xFE0F}
_HASHTAG = re.compile(r"(?<![\w&])#[^\s#]+")


def _wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def count(text: str, unit: str = "chars") -> int:
    if unit not in UNITS:
        raise ValueError(f"unit 은 {', '.join(UNITS)} 가운데 하나")
    text = text.replace("\r\n", "\n")
    if unit == "bytes_utf8":
        return len(text.encode("utf-8"))
    total, joined = 0, False
    for ch in text:
        code = ord(ch)
        if code in _JOINERS or unicodedata.combining(ch) or 0x1F3FB <= code <= 0x1F3FF:
            joined = code == 0x200D
            continue
        if joined:
            joined = False
            continue
        if unit == "chars_no_spaces" and ch.isspace():
            continue
        total += 2 if unit == "chars_dw" and _wide(ch) else 1
    return total


def _em(ch: str) -> float:
    """Rough glyph width in em for a Korean UI sans: Hangul/CJK 1, digits/Latin about 0.6, space 0.3."""
    if ch.isspace():
        return 0.3
    if _wide(ch):
        return 1.0
    return 0.62 if ch.isalnum() else 0.4


def cut(text: str, limit: int, unit: str = "chars") -> str:
    """The longest prefix of `text` that fits `limit`."""
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count(text[:mid], unit) <= limit:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def measure(item: dict) -> dict:
    text = str(item.get("text") or "")
    label = str(item.get("label") or "")
    channel, field = item.get("channel"), item.get("field")
    if channel:
        spec = SPECS.get(str(channel))
        if spec is None:
            raise ValueError(f"모르는 채널 {channel}. 있는 것: {', '.join(SPECS)}")
        fields = spec["fields"]
        if field not in fields:
            raise ValueError(f"{channel} 에 {field} 필드가 없습니다. 있는 것: {', '.join(fields)}")
        rule = dict(fields[field])
    else:
        if item.get("limit") in (None, ""):
            raise ValueError(f"「{label}」: channel/field 또는 limit 이 필요합니다")
        rule = {"limit": int(item["limit"]), "unit": item.get("unit") or "chars", "hard": bool(item.get("hard", True))}
    unit = rule.get("unit", "chars")
    n = count(text, unit)
    row = {"label": label, "channel": channel, "field": field, "count": n, "unit": unit}
    if field:
        # the field as the answer names it; the key is for calls only
        row["word"] = FIELD_WORDS.get(str(field), str(field))
    if rule.get("limit"):
        row.update(limit=rule["limit"], over=max(0, n - rule["limit"]), hard=rule.get("hard", True))
        if row["over"] and not row["hard"]:
            row["shown"] = cut(text, rule["limit"], unit)
    if rule.get("max"):
        row["max"] = rule["max"]
        if n > rule["max"]:
            row.update(over=n - rule["max"], hard=True)
    if rule.get("px_em"):
        row["est_px"] = round(sum(_em(ch) for ch in text) * rule["px_em"])
        row["px_max"] = rule["px_max"]
        row["over_px"] = max(0, row["est_px"] - rule["px_max"])
        if re.search(r"[\U0001F300-\U0001FAFF\u2600-\u27BF☆★♡♥☞]", text):
            row["banned_symbols"] = True
    if rule.get("min") and n < rule["min"]:
        row["under"] = rule["min"] - n
    if rule.get("visible"):
        row["visible"] = cut(text, rule["visible"], unit)
        row["visible_limit"] = rule["visible"]
    if "hashtags" in rule:
        tags = _HASHTAG.findall(text)
        row["hashtags"] = {"count": len(tags), "limit": rule["hashtags"], "over": max(0, len(tags) - rule["hashtags"])}
    if rule.get("note"):
        row["note"] = rule["note"]
    return row


def listing(channel: str | None) -> list[dict]:
    rows = []
    for key, spec in SPECS.items():
        if channel and key != channel:
            continue
        for field, rule in spec["fields"].items():
            rows.append({"channel": key, "name": spec["name"], "field": field,
                         "word": FIELD_WORDS.get(field, field), **rule, "source": spec["source"]})
    return rows


#: Channel names in a request (bounded patterns), first match per channel.
_CHANNEL_WORDS: tuple[tuple[str, re.Pattern], ...] = (
    ("naver_powerlink", re.compile(r"파워\s{0,2}링크|네이버\s{0,2}(?:검색\s{0,2})?광고")),
    ("kakao_keyword", re.compile(r"카카오\s{0,2}키워드")),
    ("kakao_display", re.compile(r"카카오\s{0,2}모먼트|모먼트\s{0,2}(?:네이티브|디스플레이)")),
    ("google_rsa", re.compile(r"반응형\s{0,2}검색\s{0,2}광고|구글\s{0,2}(?:검색\s{0,2})?광고|google\s{0,2}ads|\bRSA\b", re.I)),
    ("google_video", re.compile(r"유튜브\s{0,2}(?:동영상\s{0,2})?광고|구글\s{0,2}동영상\s{0,2}광고")),
    ("meta", re.compile(r"메타\s{0,2}(?:\(|광고)|페이스북\s{0,2}광고|인스타(?:그램)?\s{0,2}광고|\bmeta\s{0,2}ads?\b", re.I)),
    ("youtube", re.compile(r"유튜브\s{0,2}(?:영상\s{0,2})?제목")),
    ("email", re.compile(r"(?:메일|뉴스레터)\s{0,2}제목")),
    # sponsored content, not a member's own post (r3 A13 T1: an intro written as 148, 156 measured)
    ("linkedin", re.compile(r"링크드\s{0,2}인\s{0,2}(?:스폰서드|광고)|linked\s{0,2}in\s{0,2}(?:ads?|sponsored)", re.I)),
)
#: Fields whose text a judge can lift out of an answer whole: short ones.
_SHORT = 150
FIELD_WORDS = {"title": "제목", "description": "설명", "extra_title": "추가제목", "promo_text": "홍보문구",
               "sublink": "서브링크", "profile_name": "프로필명", "slide_title": "슬라이드 제목",
               "slide_price": "슬라이드 가격", "headline": "제목(헤드라인)", "long_headline": "긴 제목",
               "path": "표시 경로", "cta": "행동 유도 문구", "primary_text": "기본 문구",
               "business_name": "업체명", "subject": "메일 제목", "preheader": "미리보기 문구",
               "item": "후보 하나하나",
               # every other field, so no key reaches the person (r3 A13 T1: 「ad_intro 권장 150」)
               "main_copy": "메인 문구", "sub_copy": "서브 문구", "basic_text": "기본 텍스트",
               "wide_image_text": "와이드 이미지 문구", "wide_list_title": "와이드 리스트 제목",
               "wide_list_first": "와이드 리스트 첫 항목", "wide_list_item": "와이드 리스트 항목",
               "carousel_title": "캐러셀 제목", "carousel_promo": "캐러셀 홍보 문구",
               "button": "버튼", "caption": "캡션", "bio": "소개", "post": "게시물 본문",
               "ad_intro": "도입 문구", "ad_headline": "헤드라인", "ad_description": "설명"}
#: 「30자 안으로」「공백 포함 40자 이내」: a limit the person set for every piece.
_STATED = re.compile(r"(?<!\d)(\d{1,4})\s{0,2}(?:자|글자)\s{0,2}(?:안으로|안쪽|이내|이하|미만|까지|내로|를?\s{0,2}넘지)")


def targets(request: str) -> list[dict]:
    """Limits a request names: each channel's short fields and any stated 「N자 이내」."""
    out: list[dict] = []
    text = request or ""
    for channel, pattern in _CHANNEL_WORDS:
        if not pattern.search(text):
            continue
        spec = SPECS[channel]
        for name, rule in spec["fields"].items():
            if not rule.get("limit") or rule["limit"] > _SHORT:
                continue
            out.append({"key": f"{channel}.{name}", "name": spec["name"], "field": name,
                        "word": FIELD_WORDS.get(name, name), "limit": rule["limit"],
                        "unit": rule.get("unit", "chars"), "hard": rule.get("hard", True),
                        **({"min": rule["min"]} if rule.get("min") else {})})
    stated = _STATED.search(text)
    if stated and 2 <= int(stated.group(1)) <= _SHORT:
        # The person's own limit stands in for the channel's recommendations.
        out = [row for row in out if row["hard"]]
        out.append({"key": "stated.item", "name": "요청", "field": "item", "word": FIELD_WORDS["item"],
                    "limit": int(stated.group(1)), "unit": "chars", "hard": True})
    return out


def measured(items: list[dict], rows: list[dict]) -> list[dict]:
    """`items` (`{"key", "text"}`) measured against `rows` from `targets`; unknown keys skipped."""
    by_key = {row["key"]: row for row in rows}
    out = []
    for item in items:
        row = by_key.get(str(item.get("key") or ""))
        text = str(item.get("text") or "").strip()
        if row is None or not text:
            continue
        n = count(text, row["unit"])
        out.append({**row, "text": text, "count": n, "over": max(0, n - row["limit"]),
                    "under": max(0, row.get("min", 0) - n)})
    return out


#: Table headers that name a field: a column of 「제목」 cells is the channel's titles.
FIELD_HEADERS = {
    "title": ("제목", "타이틀"), "headline": ("제목", "헤드라인", "타이틀"), "description": ("설명",),
    "extra_title": ("추가제목", "추가 제목"), "promo_text": ("홍보문구", "홍보 문구"),
    "profile_name": ("프로필명",), "slide_title": ("슬라이드 제목",), "primary_text": ("기본 문구", "본문"),
    "long_headline": ("긴 제목",), "subject": ("제목", "메일 제목"), "item": ("헤드라인", "제목", "후보", "문구"),
    "ad_intro": ("도입 문구", "도입글", "소개 문구", "인트로"), "ad_headline": ("헤드라인", "제목"),
    "ad_description": ("설명",),
}


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def table_items(text: str, rows: list[dict]) -> list[dict]:
    """Copy pieces from answer tables: cells under a header naming a field of `rows`."""
    if not rows:
        return []
    ranked = sorted(rows, key=lambda r: (not r.get("hard", True), r["key"] != "stated.item"))
    out: list[dict] = []
    lines = (text or "").split("\n")
    k = 0
    while k < len(lines):
        line = lines[k]
        if not line.lstrip().startswith("|") or k + 1 >= len(lines) or not re.match(r"\s{0,8}\|[\s:|-]{3,}$", lines[k + 1]):
            k += 1
            continue
        keys = []
        for header in _cells(line):
            word = re.sub(r"[*`]", "", header).strip()
            keys.append(next((r["key"] for r in ranked if word in FIELD_HEADERS.get(r["field"], ())), None))
        k += 2
        while k < len(lines) and lines[k].lstrip().startswith("|"):
            for key, cell in zip(keys, _cells(lines[k]), strict=False):
                cell = re.sub(r"^\*\*|\*\*$", "", cell).strip().strip("「」\"")
                if key and cell and not re.fullmatch(r"[\d\s,.자]+|-+", cell):
                    out.append({"key": key, "text": cell})
            k += 1
    return out


#: A count the answer wrote beside a piece: 「(14자)」, or a table cell holding a number alone.
_PAREN_COUNT = re.compile(r"[ \t]{0,2}[(（][ \t]{0,2}(?:공백\s{0,2}포함\s{0,2})?(\d{1,4})[ \t]{0,2}(?:자|글자)[ \t]{0,2}[)）]")


def restate_counts(text: str, rows: list[dict]) -> str:
    """The answer's own counts beside each measured piece replaced by the measured ones."""
    lines = (text or "").split("\n")
    for row in rows:
        piece, n = row["text"], str(row["count"])
        for k, line in enumerate(lines):
            at = line.find(piece)
            if at < 0:
                continue
            end = at + len(piece)
            tail = line[end:]
            m = _PAREN_COUNT.match(tail)
            # A table cell holding the piece whole, then a cell holding a number alone.
            cell = re.match(r"(?:\*\*)?[ \t]{0,3}\|[ \t]{0,3}(\d{1,4})[ \t]{0,3}(?:자)?[ \t]{0,3}(?=\|)", tail)
            whole = re.search(r"\|[ \t]{0,3}(?:\*\*)?$", line[:at]) and re.match(r"(?:\*\*)?[ \t]{0,3}\|", tail)
            if m:
                lines[k] = line[:end] + tail[: m.start(1)] + n + tail[m.end(1):]
            elif line.lstrip().startswith("|") and cell and whole:
                lines[k] = line[:end] + tail[: cell.start(1)] + n + tail[cell.end(1):]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    if not isinstance(options, dict):
        options = {}
    if options.get("list"):
        rows = listing(options.get("channel"))
        for row in rows:
            limit = row.get("limit")
            print(f"{row['name']} {row['word']}: {limit or '-'} ({row.get('unit', 'chars')}, {'한도' if row.get('hard', True) else '권장'}) — 부를 때 field={row['field']}")
        print(json.dumps({"ok": True, "specs": rows}, ensure_ascii=False))
        return 0
    items = options.get("items")
    if not items and options.get("text"):
        items = [options]
    if not isinstance(items, list) or not items:
        print(json.dumps({"ok": False, "error": "items 목록이 필요합니다"}, ensure_ascii=False))
        return 1
    try:
        rows = [measure(item if isinstance(item, dict) else {"text": str(item)}) for item in items[:200]]
    except (TypeError, ValueError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    for row in rows:
        limit = f"/{row['limit']}" if "limit" in row else ""
        state = (f" {row['over']} 초과" + ("" if row.get("hard") else "(권장)")) if row.get("over") else ""
        state += f" {row['under']} 모자람" if row.get("under") else ""
        state += f" 해시태그 {row['hashtags']['count']}/{row['hashtags']['limit']}" if row.get("hashtags", {}).get("over") else ""
        print(f"{row['label']}: {row['count']}{limit}{state}")
    print(json.dumps({"ok": True, "items": rows,
                      "over_hard": sum(1 for r in rows if r.get("over") and r.get("hard")),
                      "over_recommended": sum(1 for r in rows if r.get("over") and not r.get("hard")),
                      "under": sum(1 for r in rows if r.get("under")),
                      "hashtags_over": sum(1 for r in rows if r.get("hashtags", {}).get("over"))},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
