#!/usr/bin/env python3
"""LibreChat 빌드 번들 in-place 패치.

대상 디렉토리(=dist/assets) 의 모든 locales/index 번들을 처리.
- locales.*.js : i18n 키 치환 (WELCOME_BACK_MESSAGE)
- index.*.js   : 언어 셀렉터 축약(ko-KR/en-US), 기본 언어 ko-KR 고정,
                 "Upload to Provider" 메뉴 숨김,
                 agent builder 메뉴 숨김 (Add Tools/Actions/Advanced 버튼),
                 agent builder 신규 폼 기본 모델·프로바이더 채움 + 폼 단순화,
                 사이드패널 탭(파일첨부/MCP 설정) 숨김,
                 브랜드명 LibreChat→KloudChat,
                 thinkingDisplay 기본값 auto→summarized,
                 Artifacts on/off 토글만 노출(세부 옵션 숨김) + 새 대화마다 off 강제,
                 [KloudChat] 마커 박힌 에러는 영어 wrapper 벗기기,
                 Data Controls(Agent API Keys / credential 회수) 숨김
- AccountSettings.*.js : Account 탭의 이단계 인증(2FA)·계정 삭제 행 숨김
"""
import os, re, sys, pathlib

if len(sys.argv) != 2:
    sys.exit("usage: librechat-patch.py <assets-dir>")

assets = pathlib.Path(sys.argv[1])
locales_files = sorted(p for p in assets.glob("locales.*.js") if p.suffix == ".js")
index_files   = sorted(p for p in assets.glob("index.*.js")   if p.suffix == ".js")
account_files = sorted(p for p in assets.glob("AccountSettings.*.js") if p.suffix == ".js")


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


def default_thinking_summarized(text: str) -> str:
    """대화 파라미터 thinkingDisplay 기본값 auto→summarized (전역 UI 기본).

    번들: thinkingDisplay:{default:NS.auto,options:[NS.auto,NS.summarized,NS.omitted]}
    NS 는 ThinkingDisplay enum 의 minified 네임스페이스(빌드/청크마다 다름) → \\w+ 캡처.
    default 만 교체하고 options 는 유지 — 사용자가 auto/omitted 도 그대로 선택 가능.
    한 index 청크에 2건(서로 다른 NS) 등장. 매칭 0건이면 앵커 stale 경고.
    """
    text, n = re.subn(
        r'(thinkingDisplay:\{default:\w+)\.auto(,options:\[)',
        r'\1.summarized\2', text)
    if n == 0:
        print("warn: default_thinking_summarized 매칭 0건 — 패턴 stale 가능", file=sys.stderr)
    return text


def show_artifacts_toggle_no_options(text: str) -> str:
    """Artifacts on/off 토글만 agents 포함 모든 endpoint 에 노출, 세부 옵션은 숨김.

    showEphemeralBadges 는 건드리지 않는다(그걸 켜면 attach 메뉴의 파일검색 업로드가
    깨지는 회귀). 인라인 Artifacts 배지만 손본다:

    (A) `!0===n&&Fragment[Web,Code,File,Artifacts,MCP]` 에서 Artifacts 컴포넌트만 n
        게이트 밖으로 → agents(n=false)에선 Artifacts 만 렌더. Artifacts 는 본문
        (NT()+jI()+?.artifacts)으로 식별 — 위치 가정 안 함.
    (B) `return m||o?badge:null` → `return !0?` 항상 렌더(off 라도 클릭 토글 노출).
    (C) on 일 때 펼쳐지는 세부 옵션 서브메뉴(shadcn/custom; com_ui_artifacts_options)
        `m&&t.jsxs(...chevron"w-7 rounded-l-none"...)` → `!1&&...` 로 숨김 → 단순 on/off.
    (D) 서브메뉴가 없으니 메인 토글 우측 모서리를 둥글게 유지
        (`m&&"rounded-r-none border-r-0"` → `!1&&...`).

    배지 컨텍스트(wI.Provider)는 showEphemeralBadges 무관하게 마운트 → 클릭 토글 동작.
    토글값=ephemeralAgent.artifacts → patch_librechat_artifacts_toggle.js(서버) 가
    agent.artifacts 로 override. A/B/C/D 각 1건 기대, 다르면 경고.
    """
    n_a = 0
    am = re.search(
        r'const ([\w$]+)=e\.memo\(\(function\(\)\{const [\w$]+=NT\(\),[\w$]+=jI\(\),'
        r'\{toggleState:[\w$]+,debouncedChange:[\w$]+,isPinned:[\w$]+\}=[\w$]+\?\.artifacts',
        text)
    frag = re.search(
        r'!0===n&&t\.jsxs\(t\.Fragment,\{children:\['
        r'(?:t\.jsx\([\w$]+,\{\}\),){4}t\.jsx\([\w$]+,\{\}\)\]\}\)', text)
    if am and frag:
        art = am.group(1)
        comps = re.findall(r't\.jsx\(([\w$]+),\{\}\)', frag.group(0))
        if art in comps:
            children = ",".join(
                (f"t.jsx({c},{{}})" if c == art else f"!0===n&&t.jsx({c},{{}})")
                for c in comps)
            text = (text[:frag.start()]
                    + f"t.jsxs(t.Fragment,{{children:[{children}]}})"
                    + text[frag.end():])
            n_a = 1
    text, n_b = re.subn(
        r'(return )m\|\|o(\?t\.jsxs\("div",\{className:"flex",children:'
        r'\[t\.jsx\([\w$]+,\{className:mw\("max-w-fit")',
        r'\1!0\2', text)
    text, n_c = re.subn(
        r'm&&(t\.jsxs\([\w$]+,\{open:[\w$]+,setOpen:[\w$]+,children:\['
        r't\.jsx\([\w$]+,\{className:mw\("w-7 rounded-l-none)',
        r'!1&&\1', text)
    text, n_d = re.subn(
        r'm&&"rounded-r-none border-r-0"',
        r'!1&&"rounded-r-none border-r-0"', text)
    if n_a != 1 or n_b != 1 or n_c != 1 or n_d != 1:
        print(f"warn: show_artifacts_toggle_no_options 매칭 A={n_a} B={n_b} "
              f"C={n_c} D={n_d} (각 기대 1) — 앵커 stale 가능", file=sys.stderr)
    return text


def reset_artifacts_on_new_convo(text: str) -> str:
    """새 대화(conversationId="new")에 진입할 때마다 artifacts 토글을 off 로 강제.

    artifacts 토글 상태는 ephemeralAgent 아톰(Gj(convoId))에 저장되는데, 새 대화는
    NEW_CONVO("new") 키를 공유해 세션 내에서 이전에 켠 값이 다음 새 대화로 캐리오버된다
    (하드 새로고침 전까지). 그래서 "새 대화는 항상 off" 가 안 된다.

    AD(토글 상태 팩토리, 5개 toolKey 가 공유)에 useEffect 를 주입 — 의존성 [l](convoId)이
    "new" 로 바뀔 때 한 번만 실행. toolKey 가 "artifacts" 인 인스턴스에서만 아톰의 artifacts
    키를 "" 로 비운다(런타임 스코핑 → web_search/file_search 등은 무영향). 사용자가 그 새
    대화에서 토글을 켜면 l 이 안 바뀌므로 리셋 안 됨 → 켜기는 정상 동작.

    AD 내부 minified 식별자(l/d/ug/toolKey)는 시그니처/본문에서 캡처. 앵커는 persist
    useEffect 직전. 캡처 실패 시 스킵(경고).
    """
    sig = re.search(
        r'function AD\(\{conversationId:([\w$]+),storageContextKey:([\w$]+),'
        r'toolKey:([\w$]+),localStorageKey:([\w$]+),isAuthenticated:([\w$]+),'
        r'setIsDialogOpen:([\w$]+),authConfig:([\w$]+)\}\)\{', text)
    if not sig:
        print("warn: reset_artifacts_on_new_convo AD 시그니처 못 찾음 — 스킵", file=sys.stderr)
        return text
    tk = sig.group(1)        # conversationId param
    a = sig.group(3)         # toolKey param
    body = text[sig.start():sig.start() + 1200]
    lm = re.search(r'const ([\w$]+)=' + re.escape(tk) + r'\?\?([\w$]+)\.NEW_CONVO', body)
    if not lm:
        print("warn: reset_artifacts_on_new_convo l/ug 못 찾음 — 스킵", file=sys.stderr)
        return text
    l, ug = lm.group(1), lm.group(2)
    dm = re.search(r'\[([\w$]+),([\w$]+)\]=[\w$]+\([\w$]+\(' + re.escape(l) + r'\)\)', body)
    if not dm:
        print("warn: reset_artifacts_on_new_convo d 못 찾음 — 스킵", file=sys.stderr)
        return text
    d = dm.group(2)          # 직접 아톰 setter
    anchor = re.search(
        r'e\.useEffect\(\(\(\)=>\{const [\w$]+=' + re.escape(dm.group(1)) +
        r'\?\.\[[\w$]+\];void 0!==[\w$]+&&\(localStorage\.setItem', text)
    if not anchor:
        print("warn: reset_artifacts_on_new_convo persist-effect 앵커 못 찾음 — 스킵", file=sys.stderr)
        return text
    inject = (
        f'e.useEffect((function(){{if({l}==={ug}.NEW_CONVO&&{a}==="artifacts"){{'
        f'{d}(function(__pr){{return __pr&&__pr[{a}]?'
        f'Object.assign({{}},__pr,{{[{a}]:""}}):__pr}})}}}}),[{l}]);'
    )
    return text[:anchor.start()] + inject + text[anchor.start():]


def unwrap_kloudchat_errors(text: str) -> str:
    """Error.tsx 의 defaultResponse 영어 wrapper 를 [KloudChat] 마커가 박힌
    메시지에서만 벗긴다.

    원본 번들: `Something went wrong. Here's the specific error message we
    encountered: ${e.length>512&&!n?e.slice(0,512)+"...":e}`
    → 메시지에 `[KloudChat]` 가 있으면 그 위치부터 슬라이스 (서버측 'An error
    occurred while processing the request:' prefix 까지 함께 제거). 마커 없는
    실제 upstream 에러는 기존 wrapper 그대로 — 디버깅 컨텍스트 유지.

    truncate_to_ctx.py 의 _make_ctx_error() 가 동일 마커를 선두에 박는다.
    다른 KloudChat 콜백도 같은 마커를 쓰면 자동으로 보호받음.
    """
    needle = (
        '`Something went wrong. Here\'s the specific error message we encountered: '
        '${e.length>512&&!n?e.slice(0,512)+"...":e}`'
    )
    if needle not in text:
        return text
    replace = (
        '(e.indexOf("[KloudChat]")>=0?e.slice(e.indexOf("[KloudChat]")):'
        '`Something went wrong. Here\'s the specific error message we encountered: '
        '${e.length>512&&!n?e.slice(0,512)+"...":e}`)'
    )
    return text.replace(needle, replace)


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


def hide_agent_builder_menus(text: str) -> str:
    """agent builder 를 일반 사용자용으로 단순화 — 일부 메뉴/버튼 숨김.

    런타임 capability(librechat.yaml endpoints.agents.capabilities)는 그대로 두고
    UI 진입점만 제거한다. tools/actions capability 를 끄면 ToolService 가 MCP 도구
    실행까지 막으므로(공유 에이전트 파손) 반드시 UI 레벨에서만 숨겨야 한다.

    앵커는 빌드마다 안 바뀌는 소스 레벨 문자열(i18n 키, panel enum prop, CSS class).
    minified 식별자(컴포넌트/변수명)는 \\w 로 매칭. 치환 건수를 stderr 로 보고한다.
    """
    subs = [
        # "Add Tools" 버튼: 선행 조건 (X??!1)&& 을 (!1)&& 로 → 항상 falsy → 미렌더.
        ("add_tools_btn",
         r'\(\w+\?\?!1\)(&&t\.jsx\("button",\{type:"button",onClick:\(\)=>\w+\(!0\),'
         r'className:"btn btn-neutral[^"]*","aria-haspopup":"dialog",children:t\.jsx\("div",'
         r'\{className:"flex w-full items-center justify-center gap-2",'
         r'children:\w+\("com_assistants_add_tools"\)\}\)\}\))',
         r'(!1)\1'),
        # "Add Actions" 버튼: 동일 방식.
        ("add_actions_btn",
         r'\(\w+\?\?!1\)(&&t\.jsx\("button",\{type:"button",disabled:\w+\(\w+\),onClick:\w+,'
         r'className:"btn btn-neutral[^"]*","aria-haspopup":"dialog",children:t\.jsx\("div",'
         r'\{className:"flex w-full items-center justify-center gap-2",'
         r'children:\w+\("com_assistants_add_actions"\)\}\)\}\))',
         r'(!1)\1'),
        # "Advanced" 진입 버튼 컴포넌트: 본문을 return null 로 → 버튼 자체 미렌더.
        # .advanced + com_ui_advanced 두 번 등장(aria-label + children)으로 version
        # 버튼(.version)과 구분. jsxs 내부는 세미콜론이 없으므로 [^;] lazy 로 안전하게 소거.
        # 컴포넌트 본문의 i18n 훅 식별자는 빌드마다 바뀌므로 \w+ 로 매칭(NT 고정 금지).
        ("advanced_btn",
         r'(\(\{setActivePanel:\w\}\)=>\{const \w=\w+\(\);return )'
         r't\.jsxs\([^;]{20,400}?\.advanced\),"aria-label":\w\("com_ui_advanced"\)'
         r'[^;]{20,200}?com_ui_advanced"\)\]\}\)',
         r'\1null'),
    ]
    for name, pat, repl in subs:
        text, n = re.subn(pat, repl, text)
        if n == 0:
            print(f"warn: hide_agent_builder_menus[{name}] 매칭 0건 — 패턴 stale 가능", file=sys.stderr)
    return text


# agent builder 신규 폼의 모델/프로바이더 기본값.
# 등록된 모델 전부 선택 가능하되, 빈 폼은 이 값으로 미리 채워둔다 (사용자 변경 가능).
AGENT_BUILDER_DEFAULT_MODEL = "local/gemma-4-26b"
AGENT_BUILDER_DEFAULT_PROVIDER = "LiteLLM"


def default_agent_builder_model(text: str) -> str:
    """신규 에이전트 폼 기본값 생성 함수 L_ 의 model/provider 빈 fallback 을 채운다.

    원본: L_=()=>({...hm,model:localStorage.getItem(mg.LAST_AGENT_MODEL)??"",
                  provider:P_(localStorage.getItem(mg.LAST_AGENT_PROVIDER)??""),...})
    → localStorage 값이 없을 때의 빈 문자열 fallback("")만 기본 모델/프로바이더로 교체.
    이전에 다른 모델을 골랐으면 localStorage 값이 우선하므로 자유 선택은 유지된다.
    앵커는 소스 식별자(LAST_AGENT_MODEL/PROVIDER, P_) — 빌드 불변.
    """
    model_js = AGENT_BUILDER_DEFAULT_MODEL.replace('"', '\\"')
    prov_js = AGENT_BUILDER_DEFAULT_PROVIDER.replace('"', '\\"')
    text, n1 = re.subn(
        r'(model:localStorage\.getItem\(\w+\.LAST_AGENT_MODEL\)\?\?)""',
        r'\1"' + model_js + '"', text)
    text, n2 = re.subn(
        r'(provider:\w+\(localStorage\.getItem\(\w+\.LAST_AGENT_PROVIDER\)\?\?)""(\))',
        r'\1"' + prov_js + r'"\2', text)
    if n1 != 1 or n2 != 1:
        print(f"warn: default_agent_builder_model 매칭 model={n1} provider={n2} (각 기대 1)",
              file=sys.stderr)
    return text


def _remove_jsx_node(text, anchor, tag):
    """anchor(literal)로 시작하는 JSX 노드 1개를 괄호 균형으로 통째 제거.

    minified 중첩 JSX 는 정규식으로 자르면 위험하므로 paren 깊이로 끝을 찾는다.
    문자열 리터럴(' " `) 안의 괄호는 무시. 노드 뒤 콤마 1개도 흡수.
    anchor 는 tag(예: 't.jsxs(') 로 시작해야 한다. 반환: (text, 제거건수0/1).
    """
    i = text.find(anchor)
    if i < 0:
        return text, 0
    p = i + tag.index('(')
    depth, k, q, esc = 0, i + tag.index('('), None, False
    while k < len(text):
        c = text[k]
        if q is not None:
            if esc: esc = False
            elif c == '\\': esc = True
            elif c == q: q = None
        elif c in '"\'`': q = c
        elif c == '(': depth += 1
        elif c == ')':
            depth -= 1
            if depth == 0:
                break
        k += 1
    end = k + 1
    if end < len(text) and text[end] == ',':
        end += 1
    return text[:i] + text[end:], 1


def simplify_agent_builder(text: str) -> str:
    """agent builder 폼을 일반 사용자용으로 단순화 (런타임 capability 무변경, UI 만).

    1) tools/actions 섹션 제목 라벨 제거(버튼은 hide_agent_builder_menus 가 이미 숨김)
    2) shadcn/ui 컴포넌트 안내 토글 제거(빌더 인스턴스만; 채팅 artifacts 설정은 유지)
    3) 모델 패널(eK): 제목 '모델 매개변수'→'모델 선택', 파라미터 슬라이더/리셋 버튼만
       제거. provider/model 셀렉터는 유지(등록된 모델 전부 선택 가능) — 기본값은
       default_agent_builder_model 이 채운다.
    4) 코드 인터프리터: 키버튼+info아이콘 제거, 체크박스만으로 on/off
       (키 다이얼로그 우회 — self-host 라 키 불필요). 라벨('코드 실행')은 유지.
    5) 웹 검색: 키버튼+info아이콘 제거, 체크박스만으로 on/off

    앵커는 i18n 키·고정 className·필드명 등 소스 레벨 문자열(빌드 불변).
    먼저 regex 치환, 그 다음 괄호균형 노드 제거(순서 의존 회피). 매칭 수 보고.
    """
    def rsub(name, pat, repl, expect=1):
        nonlocal text
        text, n = re.subn(pat, repl, text)
        if n != expect:
            print(f"warn: simplify_agent_builder[{name}] 매칭 {n}건 (기대 {expect})",
                  file=sys.stderr)

    def rnode(name, anchor, tag='t.jsxs('):
        nonlocal text
        text, n = _remove_jsx_node(text, anchor, tag)
        if n != 1:
            print(f"warn: simplify_agent_builder[{name}] 노드제거 {n}건 (기대 1)",
                  file=sys.stderr)

    # --- (1) tools/actions 제목 라벨 → null (children 배열 내라 허용) ---
    rsub('tools_actions_title',
         r't\.jsx\("label",\{className:[\w$]+,children:!0===[\w$]+&&!0===[\w$]+\?'
         r'[\w$]+\("com_ui_tools_and_actions"\):!0===[\w$]+\?[\w$]+\("com_ui_tools"\):'
         r'!0===[\w$]+\?[\w$]+\("com_assistants_actions"\):""\}\)',
         'null')

    # --- (2) shadcn 토글 컴포넌트 제거 (빌더의 id:"includeShadcnui") ---
    rsub('shadcn_toggle',
         r't\.jsx\([\w$]+,\{id:"includeShadcnui",label:[\w$]+\("com_ui_include_shadcnui"\),'
         r'checked:[\w$]+,onCheckedChange:[\w$]+=>\{[\w$]+\([\w$]+\.artifacts,[\w$]+\?'
         r'[\w$]+\.SHADCNUI:[\w$]+\.DEFAULT,\{shouldDirty:!0\}\)\},hoverCardText:[\w$]+\('
         r'"com_nav_info_include_shadcnui"\),disabled:![\w$]+\|\|[\w$]+\}\),',
         '')

    # --- (4) 코드 체크박스 keyless 핸들러 + disabled 해제 ---
    #     원형: g=e=>{a?i(Hf.execute_code,e,..):f?i(Hf.execute_code,!1,..):u(!0)}
    #     조건변수는 (\w+)\? 로 캡처(greedy [\w$]+ 는 백트래킹으로 비결정적이라 금지).
    #     키 없이 체크값(e) 그대로 setValue → 키 다이얼로그 우회.
    rsub('code_handler',
         r'(\w+)=(\w+)=>\{(\w+)\?(\w+)\((\w+)\.execute_code,\2,\{shouldDirty:!0\}\):'
         r'(\w+)\?\4\(\5\.execute_code,!1,\{shouldDirty:!0\}\):(\w+)\(!0\)\}',
         r'\1=\2=>{\4(\5.execute_code,\2,{shouldDirty:!0})}')
    rsub('code_disabled',
         r'disabled:![\w$]+&&![\w$]+(,"aria-labelledby":"execute-code-label")',
         r'disabled:!1\1')

    # --- (5) 웹 검색 체크박스 keyless 핸들러 + disabled 해제 ---
    rsub('web_handler',
         r'(\w+)=(\w+)=>\{(\w+)\?(\w+)\((\w+)\.web_search,\2,\{shouldDirty:!0\}\):'
         r'(\w+)\?\4\(\5\.web_search,!1,\{shouldDirty:!0\}\):(\w+)\(!0\)\}',
         r'\1=\2=>{\4(\5.web_search,\2,{shouldDirty:!0})}')
    rsub('web_disabled',
         r'disabled:![\w$]+&&![\w$]+(,"aria-labelledby":"web-search-label")',
         r'disabled:!1\1')

    # --- (3) 모델 패널 제목: '모델 매개변수' → '모델 선택' (i18n 키 대신 리터럴) ---
    rsub('model_panel_title',
         r'(className:"mb-2 mt-2 text-xl font-medium",children:)'
         r'[\w$]+\("com_ui_model_parameters"\)(\})',
         r'\1"모델 선택"\2')

    # --- (3) 파라미터 슬라이더 섹션 숨김: x&&t.jsx(div h-auto) → !1&& ---
    rsub('model_params_section',
         r'[\w$]+(&&t\.jsx\("div",\{className:"h-auto max-w-full")',
         r'!1\1')

    # --- 괄호균형 노드 제거 (literal anchor) ---
    # (3) 파라미터 리셋 버튼
    rnode('model_params_reset',
          't.jsxs("button",{type:"button",onClick:()=>{l("model_parameters",{})')
    # (4)(5) 키버튼+info아이콘 묶음 div(ml-2 flex gap-2) 2개 (code, web)
    for tag_name in ('code', 'web'):
        rnode(f'keybtn_div_{tag_name}', 't.jsxs("div",{className:"ml-2 flex gap-2"')

    return text


def hide_sidepanel_tabs(text: str) -> str:
    """우측 사이드패널(Nav) 탭 중 '파일 첨부'/'MCP 설정' 숨김.

    사이드패널 탭은 e.push({title,label,icon,id,Component}) 시퀀스로 빌드된다.
    해당 push 호출만 !1(no-op)로 치환 → 탭 미등록. 조건부(mcp)는 (cond)&&!1 이 되어
    역시 false. 컴포저의 파일 첨부 버튼(id:"attach-file"/"attach-file-menu-button")은
    별도 컴포넌트라 무영향. 앵커는 i18n 키+id(소스 불변), icon/Component 만 \\w+ 캡처.
    """
    text, na = re.subn(
        r'e\.push\(\{title:"com_sidepanel_attach_files",label:"",icon:[\w$]+,'
        r'id:"files",Component:[\w$]+\}\)',
        '!1', text)
    text, nm = re.subn(
        r'e\.push\(\{title:"com_nav_setting_mcp",label:"",icon:[\w$]+,'
        r'id:"mcp-builder",Component:[\w$]+\}\)',
        '!1', text)
    if na != 1 or nm != 1:
        print(f"warn: hide_sidepanel_tabs 매칭 attach={na} mcp={nm} (각 기대 1) "
              f"— 앵커 stale 가능", file=sys.stderr)
    return text


def rebrand_visible_librechat(text: str) -> str:
    """브라우저에 표기되는 브랜드명 'LibreChat' → 'KloudChat' (header/body 한정).

    index.*.js 번들에 정확히 4건 등장하며 전부 표시 문자열이다:
    - AI Agent Store 탭 title  `${t("com_agents_marketplace")} | LibreChat` (하드코딩)
    - document.title fallback `appTitle||"LibreChat"`
    - 로고 alt fallback       `appTitle??"LibreChat"`
    - 기본 footer            `[LibreChat vX](...)`  (CUSTOM_FOOTER 로 override 되지만 잔존)

    정확히 'LibreChat'(대문자 L,C)만 치환 — 소문자 librechat(URL https://librechat.ai,
    import 경로, localStorage 키, librechat.yaml)·글루 식별자는 미존재 확인됨(불변).
    locale(i18n) 110건은 범위 외(브라우저 텍스트가 아닌 다국어 본문)라 건드리지 않는다.
    appTitle fallback 들은 APP_TITLE=KloudChat 으로 이미 해소되지만, 미설정 환경 대비.
    """
    n = text.count("LibreChat")
    text = text.replace("LibreChat", "KloudChat")
    if n != 4:
        print(f"warn: rebrand_visible_librechat 치환 {n}건 (기대 4) — 번들 구조 변경 가능",
              file=sys.stderr)
    return text


def hide_datacontrols_items(text: str) -> str:
    """Settings → Data Controls 의 'Agent API Keys' / 'Revoke all user provided
    credentials' 블록을 숨긴다. 두 블록은 안정적 label id(api-keys-label /
    revoke-info-label)를 가진 div.pb-3 래퍼라, :has() CSS 를 head 에 주입해 숨긴다
    (어느 청크가 렌더하든 전역 적용 — minified JSX 수술 불필요)."""
    MARK = "KLOUDCHAT_HIDE_DATACONTROLS"
    if MARK in text:
        return text
    css = ("div.pb-3:has(#api-keys-label),"
           "div.pb-3:has(#revoke-info-label){display:none!important}")
    inject = (
        "\n;(function(){/*" + MARK + "*/try{var s=document.createElement('style');"
        "s.textContent=" + repr(css) + ";document.head.appendChild(s);}catch(e){}})();\n"
    )
    return text + inject


def hide_account_items(text: str) -> str:
    """Settings → Account 의 '이단계 인증(2FA)' / '계정 삭제' 행을 숨긴다 (AccountSettings 청크).
    delete-account 행 라벨은 안정적 id(delete-account-label)가 있으나 2FA 행 라벨엔 id 가
    없어 먼저 주입하고, 두 행 래퍼(div.flex.items-center.justify-between)를 :has() CSS 로
    숨긴다 (청크 로드 시 <style> head 주입 — 행 렌더 전에 적용)."""
    MARK = "KLOUDCHAT_HIDE_ACCOUNT"
    if MARK in text:
        return text
    new = re.sub(r'\{children:\[" ",(\w+)\("com_nav_2fa"\)\]\}',
                 r'{id:"kc-2fa-label",children:[" ",\1("com_nav_2fa")]}', text)
    if new == text:
        print("warn: hide_account_items 2FA 라벨 매칭 0건 — 패턴 stale 가능", file=sys.stderr)
    css = ("div.flex.items-center.justify-between:has(#kc-2fa-label),"
           "div.flex.items-center.justify-between:has(#delete-account-label)"
           "{display:none!important}")
    inject = (
        "\n;(function(){/*" + MARK + "*/try{var s=document.createElement('style');"
        "s.textContent=" + repr(css) + ";document.head.appendChild(s);}catch(e){}})();\n"
    )
    return new + inject


for p in locales_files:
    text = p.read_text()
    if (msg := os.environ.get("WELCOME_BACK_MESSAGE", "").strip()):
        text = replace_i18n_value(text, "com_auth_welcome_back", msg)
    # 'Agent Marketplace' → 'AI Agent Store' (브랜딩; 전 언어 동일 영문 라벨).
    # com_agents_marketplace / com_ui_marketplace 둘 다 — ':' 경계로 _subtitle,
    # _allow_use 같은 파생 키는 매칭 제외.
    text = replace_i18n_value(text, "com_agents_marketplace", "AI Agent Store")
    text = replace_i18n_value(text, "com_ui_marketplace", "AI Agent Store")
    p.write_text(text)

for p in index_files:
    text = p.read_text()
    text = patch_lang_selector(text)
    text = force_default_korean(text)
    text = hide_upload_to_provider(text)
    text = hide_agent_builder_menus(text)
    text = default_agent_builder_model(text)
    text = simplify_agent_builder(text)
    text = hide_sidepanel_tabs(text)
    text = rebrand_visible_librechat(text)
    text = default_thinking_summarized(text)
    text = show_artifacts_toggle_no_options(text)
    text = reset_artifacts_on_new_convo(text)
    text = unwrap_kloudchat_errors(text)
    text = hide_datacontrols_items(text)
    p.write_text(text)

for p in account_files:
    text = p.read_text()
    text = hide_account_items(text)
    p.write_text(text)
