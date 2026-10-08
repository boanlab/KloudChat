"""광고·SNS 문구를 채널·필드별 글자 수 한도(네이버·카카오·메타·구글·유튜브·인스타그램·링크드인)로 재고, 피드에서 「더 보기」 전에 보이는 부분을 알려 줍니다.

인자(JSON 하나):
  {"items": [{"label": "1안 제목", "channel": "naver_powerlink", "field": "title", "text": "..."},
             {"label": "직접", "text": "...", "limit": 40, "unit": "chars"}]}
  {"list": true, "channel": "meta"(선택)}   채널·필드 한도표와 출처를 낸다
세는 법(unit):
  chars            공백 포함 글자 수. 줄바꿈 1자, 이모지(피부색·ZWJ 결합 포함) 한 덩이 1자
  chars_dw         한글·한자·전각 문자 2자, 그 밖 1자(Google Ads 의 두 배 너비 문자 규칙)
  chars_no_spaces  공백·줄바꿈 제외
  bytes_utf8       UTF-8 바이트
결과: count, limit, over(넘은 만큼), hard(넘으면 등록·노출이 안 됨) / 권장 한도면 hard=false 와 잘리는 부분,
  피드 글은 visible(「더 보기」 전 보이는 앞부분)과 hashtags(해시태그 수·한도).
마지막 줄에 요약 JSON을 냅니다. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import json
import re
import sys
import unicodedata

UNITS = ("chars", "chars_dw", "chars_no_spaces", "bytes_utf8")

#: channel → field → rule. `limit` with `hard: true` is rejected or cut by the platform; with
#: `hard: false` it is a recommendation (text shows truncated) and `max` is the hard ceiling.
#: `visible`: shown before 「더 보기」. Values checked against the sources on 2026-10-08;
#: 「2차」 marks a figure no official page states.
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
            rows.append({"channel": key, "name": spec["name"], "field": field, **rule, "source": spec["source"]})
    return rows


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
            print(f"{row['name']} {row['field']}: {limit or '-'} ({row.get('unit', 'chars')}, {'한도' if row.get('hard', True) else '권장'})")
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
