#!/usr/bin/env python3
"""LibreChat 빌드 번들 in-place 패치.

대상 디렉토리(=dist/assets) 의 모든 locales/index 번들을 처리.
- locales.*.js : i18n 키 치환 (WELCOME_BACK_MESSAGE, SIGNUP_HEADER)
- index.*.js   : 언어 셀렉터 축약(ko-KR/en-US), 기본 언어 ko-KR 고정,
                  "Upload to Provider" 메뉴 숨김
"""
import os, re, sys, pathlib

if len(sys.argv) != 2:
    sys.exit("usage: librechat-patch.py <assets-dir>")

assets = pathlib.Path(sys.argv[1])
locales_files = sorted(p for p in assets.glob("locales.*.js") if p.suffix == ".js")
index_files   = sorted(p for p in assets.glob("index.*.js")   if p.suffix == ".js")


def replace_i18n_value(text: str, key: str, value: str) -> str:
    """모든 locale 의 `key:"..."` 항목을 value 로 치환."""
    pattern = re.compile(rf'({re.escape(key)}:")[^"]*(")')
    return pattern.sub(lambda m: m.group(1) + value.replace('"', '\\"') + m.group(2), text)


def patch_lang_selector(text: str) -> str:
    """언어 셀렉터를 ko-KR(첫번째) + en-US 만 노출하도록 축약."""
    m = re.search(r'\{value:"auto",label:([a-zA-Z_$]+)\("com_nav_lang_auto"\)\}', text)
    if not m:
        return text
    t_fn = m.group(1)  # i18next t 함수 식별자 (빌드마다 minified name 변함)
    start = text.rfind("[", 0, m.start())
    depth, end = 0, start
    while end < len(text):
        c = text[end]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                break
        end += 1
    new_array = (
        f'[{{value:"ko-KR",label:{t_fn}("com_nav_lang_korean")}},'
        f'{{value:"en-US",label:{t_fn}("com_nav_lang_english")}}]'
    )
    return text[:start] + new_array + text[end + 1 :]


def force_default_korean(text: str) -> str:
    """저장된 lang 이 없거나 'auto' 면 ko-KR 로 강제 — 브라우저 자동감지 제거."""
    return re.sub(
        r'\("lang",\(\(\)=>\{const ([a-zA-Z_$]+)=navigator\.language\|\|navigator\.languages\[0\];'
        r'return ([a-zA-Z_$]+)\.get\("lang"\)\|\|localStorage\.getItem\("lang"\)\|\|\1\}\)\(\)\)',
        r'("lang",(()=>{const _v=\2.get("lang")||localStorage.getItem("lang");'
        r'return _v&&_v!=="auto"?_v:"ko-KR"})())',
        text,
    )


def hide_upload_to_provider(text: str) -> str:
    """"Upload to Provider" 메뉴 항목 숨김.

    두 곳에서 push 됨:
    1. 에이전트 컨텍스트 — `condition:<ident>` 필드를 `!1` 로 뒤집어 렌더에서 필터링.
    2. 일반 채팅 컨텍스트 — `?n.push({...upload_provider...}):n.push({...image_input...})`
       삼항 truthy 분기를 no-op `({})` 로 치환 — falsy 분기(이미지 업로드)가 항상 실행됨.
    """
    text = re.sub(
        r'(\.push\(\{label:[a-zA-Z]\("com_ui_upload_provider"\),'
        r'value:void 0,'
        r'icon:[a-zA-Z]\.jsx\([a-zA-Z]+,\{className:"icon-md"\}\),'
        r'condition:)[a-zA-Z](\}\))',
        r'\1!1\2',
        text,
    )
    text = re.sub(
        r'\?[a-zA-Z]\.push\(\{label:[a-zA-Z]\("com_ui_upload_provider"\)'
        r'[\s\S]*?'
        r'\}\):(?=[a-zA-Z]\.push\()',
        r'?({}):',
        text,
    )
    return text


for p in locales_files:
    text = p.read_text()
    if (msg := os.environ.get("WELCOME_BACK_MESSAGE", "").strip()):
        text = replace_i18n_value(text, "com_auth_welcome_back", msg)
    if (msg := os.environ.get("SIGNUP_HEADER", "").strip()):
        text = replace_i18n_value(text, "com_auth_create_account", msg)
    p.write_text(text)

for p in index_files:
    text = p.read_text()
    text = patch_lang_selector(text)
    text = force_default_korean(text)
    text = hide_upload_to_provider(text)
    p.write_text(text)
