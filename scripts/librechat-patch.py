#!/usr/bin/env python3
"""LibreChat 빌드 번들에 환경변수 기반 패치 적용.
- WELCOME_BACK_MESSAGE → com_auth_welcome_back (all locales)
- SIGNUP_HEADER       → com_auth_create_account (all locales)
- 언어 셀렉터 → auto + en-US + ko-KR 만 노출 (항상)
"""
import os, re, sys, pathlib

if len(sys.argv) != 3:
    sys.exit("usage: librechat-patch.py <locales.js> <index.js>")

locales = pathlib.Path(sys.argv[1])
index   = pathlib.Path(sys.argv[2])


def replace_i18n_value(text: str, key: str, value: str) -> str:
    """모든 locale 의 `key:"..."` 항목을 value 로 치환."""
    pattern = re.compile(rf'({re.escape(key)}:")[^"]*(")')
    return pattern.sub(lambda m: m.group(1) + value.replace('"', '\\"') + m.group(2), text)


locales_text = locales.read_text()
if (msg := os.environ.get("WELCOME_BACK_MESSAGE", "").strip()):
    locales_text = replace_i18n_value(locales_text, "com_auth_welcome_back", msg)
if (msg := os.environ.get("SIGNUP_HEADER", "").strip()):
    locales_text = replace_i18n_value(locales_text, "com_auth_create_account", msg)
locales.write_text(locales_text)

# 언어 셀렉터: 첫 번째 `{value:"auto",label:s("com_nav_lang_auto")}` 가 포함된 배열을
# auto + en-US + ko-KR 3개로 축약.
index_text = index.read_text()
anchor = '{value:"auto",label:s("com_nav_lang_auto")}'
i = index_text.find(anchor)
if i >= 0:
    start = index_text.rfind("[", 0, i)
    depth = 0
    end = start
    while end < len(index_text):
        c = index_text[end]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                break
        end += 1
    new_array = (
        '[{value:"auto",label:s("com_nav_lang_auto")},'
        '{value:"en-US",label:s("com_nav_lang_english")},'
        '{value:"ko-KR",label:s("com_nav_lang_korean")}]'
    )
    index.write_text(index_text[:start] + new_array + index_text[end + 1 :])
