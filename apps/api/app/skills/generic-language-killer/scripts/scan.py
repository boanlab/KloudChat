"""글에서 상투어·군더더기(채움말·얼버무림·빈 강조어·유행어·AI 투·번역투·영어 상투어)를 찾아 위치·고칠 방향·밀도를 냅니다.

인자(JSON 하나):
  {"text": "고칠 글",                               # 또는 "items": [{"label": "...", "text": "..."}]
   "material": "사용자가 준 자료 원문(선택)",        # 자료에 있는 표현은 in_material=true
   "categories": ["filler", "hedge", ...],         # 선택: 이 분류만
   "apply": true}                                  # 선택: 안전한 자동 고침(auto)만 적용한 글을 fixed 로
분류(category):
  filler          채움말: 「~라고 할 수 있습니다」「~하도록 하겠습니다」「다양한」「기본적으로」
  hedge           얼버무림: 「~것 같습니다」「~로 보입니다」「어느 정도」「다소」
  intensifier     빈 강조어·평가어: 「매우」「획기적인」「최고의」「효과적으로」「극대화」
  buzzword        유행어·기업어: 「시너지」「패러다임」「니즈」「고도화」「한 단계 도약」
  ai              AI 투: 「단순한 ~가 아닙니다」「~의 시대」「살펴보겠습니다」「결론적으로」, 줄표(—)
  translationese  번역투: 「~에 있어서」「~를 통해」「~하는 데 있어」「되어지다」「~에 의해」
  english         영어 상투어: leverage, seamless, cutting-edge, in order to …
처리(action):
  delete      지운다(문장이 그대로 선다)
  replace     replace 에 적힌 말로 바꾼다(auto=true 면 코드가 바꿔도 뜻이 같다)
  concretize  자료의 사실(무엇을·얼마나·누가)로 바꾼다. 자료에 없으면 placeholder(「[구체적 수치]」 등)를 둔다
  rewrite     문장 틀을 풀어 사실만 남긴다
  check       맥락에 따라 괜찮을 수 있어 사람이 본다
밀도(density)는 어절 100개당 가중 건수, 등급은 낮음(3 이하)·보통(8 이하)·높음.
따옴표 「」 『』 “” "" 안의 말은 인용이라 자동으로 고치지 않습니다(quoted=true).
마지막 줄에 요약 JSON을 냅니다. 파일은 만들지 않습니다.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Callable
from dataclasses import dataclass

CATEGORIES = ("filler", "hedge", "intensifier", "buzzword", "ai", "translationese", "english")
CATEGORY_WORDS = {
    "filler": "채움말", "hedge": "얼버무림", "intensifier": "빈 강조어", "buzzword": "유행어",
    "ai": "AI 투", "translationese": "번역투", "english": "영어 상투어",
}
WEIGHTS = {"delete": 1.0, "replace": 1.0, "concretize": 1.5, "rewrite": 1.5, "check": 0.5}

_H = "가-힣"
_NB = rf"(?<![{_H}A-Za-z])"  # not inside a word on the left


# ── Hangul helpers ───────────────────────────────────────────────────────

def _jong(ch: str) -> int:
    """Final consonant index of a Hangul syllable (0 = open), -1 for anything else."""
    code = ord(ch) - 0xAC00
    return code % 28 if 0 <= code < 11172 else -1


def _with_jong(ch: str, jong: int) -> str:
    code = ord(ch) - 0xAC00
    return chr(0xAC00 + code - code % 28 + jong)


def _formal(stem: str) -> str | None:
    """「중요하」→「중요합니다」, 「했」→「했습니다」, 「한」(한다)→「합니다」; None when unsure."""
    if not stem or _jong(stem[-1]) < 0:
        return None
    last, jong = stem[-1], _jong(stem[-1])
    if stem.endswith("는") and len(stem) > 1 and _jong(stem[-2]) > 0:  # 먹는다 → 먹습니다
        return stem[:-1] + "습니다"
    if jong == 0:
        return stem[:-1] + _with_jong(last, 17) + "니다"
    if jong in (20, 18):  # ㅆ, ㅄ: 했·있·없
        return stem + "습니다"
    if last in ("한", "된"):  # 한다 → 합니다, 된다 → 됩니다
        return stem[:-1] + _with_jong(last, 17) + "니다"
    return None


# ── the phrase list ──────────────────────────────────────────────────────

Replace = Callable[[re.Match], "str | None"] | str | None


@dataclass(frozen=True)
class Rule:
    category: str
    pattern: str
    action: str
    suggestion: str
    placeholder: str = ""
    #: Code fix: a template (`\\1` groups) or a function of the match; None = no safe fix.
    replace: Replace = None
    flags: int = 0


def _said_plain(m: re.Match) -> str | None:
    """「X다고 할 수 있습니다」→「X습니다/X합니다」, 「X(이)라고 할 수 있다」→「X이다」."""
    stem, kind, end = m.group("stem"), m.group("kind"), m.group("end")
    if kind == "다고":
        if end.startswith("있다"):
            return stem + "다"
        return _formal(stem)
    if stem.endswith("아니"):
        return None
    return stem + ("이다" if end.startswith("있다") else "입니다")


def _need(m: re.Match) -> str:
    return f"{m.group(1)}해야 {'합니다' if m.group(2) == '있습니다' else '한다'}"


#: Plans and judgments: 「예정」「검토 중」「가능할 것으로 판단」 (one class per hedge, for `post`).
PLAN_HEDGES = {
    "예정": r"예정",
    "계획": r"계획(?=입니다|이다|이며|이고|임|에\s)",
    "검토 중": r"검토\s?(?:중|하고\s?있)",
    "추진 중": r"추진\s?(?:중|하고\s?있)",
    "협의 중": r"협의\s?(?:중|하고\s?있)",
    "가능할 것으로": r"가능할\s?것으로|(?:할|될)\s?수\s?있을\s?것으로",
    "것으로 판단·예상": r"것으로\s?(?:판단|예상|전망|보입|보인|보여|추정|생각)",
}
PLAN_HEDGE = "|".join(f"(?:{p})" for p in PLAN_HEDGES.values())

RULES: tuple[Rule, ...] = (
    # ── 채움말 ──
    Rule("filler", rf"(?P<stem>[{_H}A-Za-z0-9]+?)(?P<kind>다고|(?:이)?라고)\s+(?:할|볼)\s+수\s+(?P<end>있습니다|있다)",
         "replace", "돌려 말하지 말고 바로 맺는다: 「~라고 할 수 있습니다」→「~입니다」.", replace=_said_plain),
    Rule("filler", rf"([{_H}]*)할\s+필요가\s+(있습니다|있다)", "replace",
         "「~할 필요가 있습니다」→「~해야 합니다」. 왜 필요한지는 자료의 이유로.", replace=_need),
    Rule("filler", rf"([{_H}])도록\s+하겠(습니다|다)", "replace", "「~하도록 하겠습니다」→「~하겠습니다」.",
         replace=r"\1겠\2"),
    Rule("filler", rf"[{_H}]+[는은]\s+것이\s+(?:중요|필요|바람직)(?:합니다|하다|해요)", "rewrite",
         "무엇이 왜 중요한지 사실로 바로 쓴다(「~해야 합니다」 + 이유). 이유가 자료에 없으면 「[이유]」.", "[이유]"),
    Rule("filler", rf"([{_H}]+)하게\s+되었습니다", "check",
         "스스로 한 일이면 「~했습니다」(예: 「출시하게 되었습니다」→「출시했습니다」)."),
    Rule("filler", rf"{_NB}(?:기본적으로|근본적으로|본질적으로|전반적으로|전반적인|일반적으로)(?=[\s,])", "delete",
         "뜻을 더하지 않으면 지운다. 범위를 말하려던 것이면 그 범위를 쓴다."),
    Rule("filler", rf"{_NB}(?:다양한|여러\s+가지|각종|제반|여러)\s*(?=[{_H}])", "concretize",
         "무엇무엇인지 자료에서 2~3개를 나열한다. 자료에 없으면 「[구체적 항목]」.", "[구체적 항목]"),
    Rule("filler", rf"{_NB}다양하게|{_NB}다양화", "concretize",
         "어떻게 다양한지 항목으로 쓴다. 자료에 없으면 「[구체적 항목]」.", "[구체적 항목]"),
    Rule("filler", rf"[{_H}]+적인\s+측면에서|[{_H}]+(?:라는|하다는)\s+점에서", "check",
         "「~적인 측면에서」는 빼거나 「~에서」로 줄인다."),
    Rule("filler", rf"최선을\s+다하(?:는|겠|며|고)|열정(?:을\s+가지고|적으로|적인)|성실한\s+(?:사람|인재)|책임감\s+있는\s+(?:사람|인재)|"
         rf"끊임없이\s+노력|맡은\s+(?:바|업무)를\s+[{_H}]+\s+인재|[{_H}]+하는\s+인재가\s+되겠습니다",
         "concretize", "다짐·자기평가 대신 그렇게 한 일을 쓴다(무엇을·언제·결과). 자료에 없으면 「[구체적 경험]」.",
         "[구체적 경험]"),
    Rule("filler", rf"{_NB}등등", "replace", "「등등」→「등」, 나머지를 알면 나열한다.", replace="등"),
    Rule("filler", rf"{_NB}우선적으로", "replace", "「우선적으로」→「먼저」.", replace="먼저"),
    # ── 얼버무림 ──
    # A plan or a judgment the author hedged on purpose: kept, never made definite.
    Rule("hedge", PLAN_HEDGE, "check",
         "계획·판단을 나타내는 말이다. 그대로 두고, 확정된 일이나 단정문으로 바꾸지 않는다."),
    Rule("hedge", rf"[{_H}]+\s*것\s+같(?:습니다|아요|다)", "check",
         "근거가 있으면 단정하고, 추측이면 무엇을 근거로 한 추측인지 밝힌다(「[근거]」)."),
    Rule("hedge", r"(?:것으로|으로|로)\s+(?:보입니다|보인다|보여요|보이며|보여집니다|(?:예상|판단|사료|생각)(?:됩니다|된다|되며|되고|되어))", "check",
         "판단의 근거(자료·수치·출처)가 자료에 있으면 붙인다. 판단·예상을 단정문으로 바꾸지 않는다.", "[근거]"),
    Rule("hedge", r"라고\s+(?:생각됩니다|판단됩니다|사료됩니다)", "replace",
         "누가 판단하는지 드러낸다: 「~라고 판단됩니다」→「~라고 판단합니다」.",
         replace=lambda m: m.group(0).replace("됩니다", "합니다")),
    Rule("hedge", rf"{_NB}(?:어느\s+정도|다소|일정\s+부분|어떤\s+의미에서|아마도?|대체로|비교적)(?=[\s,])", "concretize",
         "얼마나인지 자료의 수치로 쓰거나 지운다. 자료에 없으면 「[정도]」.", "[정도]"),
    # ── 빈 강조어·평가어 ──
    Rule("intensifier", rf"{_NB}(?:매우|정말(?:로)?|굉장히|대단히|너무나|진짜|엄청)\s+(?=[{_H}])", "delete",
         "강조 부사는 지운다. 정도가 중요하면 수치로 쓴다.", replace=""),
    Rule("intensifier", rf"{_NB}(?:상당히|상당한|크게|대폭|현저히|획기적으로|비약적으로|월등히)(?=[\s])", "concretize",
         "얼마나인지 자료의 수치로 바꾼다(「크게 줄었다」→「12%에서 7%로 줄었다」). 자료에 없으면 「[구체적 수치]」.",
         "[구체적 수치]"),
    Rule("intensifier", rf"{_NB}(?:혁신적|획기적|독보적|압도적|차별화된|탁월한|뛰어난|우수한|강력한|완벽한|놀라운|최첨단|완벽하게|완벽히|최고의|최상의|최적의|최고\s+수준의?)",
         "concretize",
         "평가어 대신 무엇이 다른지 사실로 쓴다(기능·수치·대상). 근거가 자료에 없으면 지우거나 「[차별점]」.", "[차별점]"),
    Rule("intensifier", rf"{_NB}(?:효과적|효율적|체계적|성공적|적극적|지속적|원활)(?:으로|인|하게|한)", "concretize",
         "무엇이 어떻게 나아지는지 쓴다(「효과적으로 관리」→「매주 월요일 재고를 대조」). 자료에 없으면 「[방법]」.",
         "[방법]"),
    Rule("intensifier", rf"(?:극대화|최적화|최소화|극소화)(?=[{_H}\s.,]|$)", "concretize",
         "무엇을 얼마나인지 쓴다. 자료에 없으면 「[구체적 수치]」.", "[구체적 수치]"),
    Rule("intensifier", rf"{_NB}(?:수많은|많은|다수의|대부분의|상당수의?)\s*(?=[{_H}])", "concretize",
         "몇인지 자료의 수치로 쓴다. 자료에 없으면 「[구체적 수치]」.", "[구체적 수치]"),
    Rule("intensifier", rf"{_NB}(?:고품질|프리미엄|하이엔드|최고급)", "concretize",
         "무엇이 고품질인지(소재·공정·인증)를 자료로 쓴다. 없으면 「[근거]」.", "[근거]"),
    # ── 유행어·기업어 ──
    Rule("buzzword", rf"{_NB}(?:시너지(?:\s*효과)?|패러다임(?:\s*전환|\s*시프트)?|게임\s*체인저|퀀텀\s*점프|레벨\s*업)",
         "concretize", "무엇이 어떻게 바뀌는지 사실로 쓴다. 없으면 「[구체적 변화]」.", "[구체적 변화]"),
    Rule("buzzword", rf"{_NB}(?:고객\s*)?니즈", "replace", "「니즈」→「요구」 또는 구체적으로 무엇을 원하는지.",
         replace=lambda m: m.group(0).replace("니즈", "요구")),
    Rule("buzzword", rf"{_NB}(?:인사이트|원스톱|토털\s*솔루션|차세대|미래\s*지향적인?|새로운\s+지평|생태계\s+구축)", "concretize",
         "무엇을 말하는지 사실로 쓴다. 없으면 「[구체적 내용]」.", "[구체적 내용]"),
    Rule("buzzword", r"한\s+단계\s+(?:더\s+)?(?:도약|성장|업그레이드|발전|진화)", "concretize",
         "어디서 어디로 가는지 쓴다. 없으면 「[구체적 목표]」.", "[구체적 목표]"),
    Rule("buzzword", r"(?:가치|부가가치)\s*창출|역량\s*강화|경쟁력\s*(?:강화|제고)|고도화|선도(?:하는|적인|적으로)|디지털\s*전환|내재화",
         "concretize", "무엇을 어떻게 하는지 쓴다(「역량 강화」→「상담원 12명 대상 4주 교육」). 없으면 「[구체적 내용]」.",
         "[구체적 내용]"),
    # ── AI 투 ──
    Rule("ai", rf"단순한\s+[{_H}A-Za-z ]{{1,20}}(?:이|가)\s+아닙니다|[{_H}]+(?:이|가)\s+아닌,\s*[{_H}]+",
         "rewrite", "부정→반전 틀을 풀고 무엇인지 바로 쓴다."),
    Rule("ai", rf"(?:오늘날|요즘처럼|현대\s+사회에서)?\s*(?:빠르게|급격히|끊임없이)\s+변화하는|급변하는|[{_H}]+의\s+시대(?:입니다|이다|에|를)",
         "delete", "배경 깔기 문장은 지우고 독자의 문제나 사실로 시작한다."),
    Rule("ai", r"(?:에\s+대해\s+)?(?:살펴보겠습니다|알아보겠습니다|살펴보도록\s+하겠습니다|알아봅시다)", "rewrite",
         "예고 문장은 지우고 내용부터 쓴다."),
    Rule("ai", rf"{_NB}(?:결론적으로|요약하자면|정리하자면|마무리하며|한마디로)(?=[\s,])", "delete",
         "맺음 표지는 지운다. 결론 문장이 스스로 선다."),
    Rule("ai", rf"[{_H}]+(?:의|은|는)\s+핵심은\s+바로|바로\s+[{_H}]+(?:입니다|이다)(?=[.!]|$)", "rewrite",
         "「핵심은 바로」 같은 뜸 들이기를 빼고 바로 말한다."),
    Rule("ai", r"(?:결과|정답|해답|비결)는\?", "rewrite", "스스로 묻고 답하는 틀을 풀고 답만 쓴다."),
    Rule("ai", r"(?:손쉽게|간편하게|간단하게|언제\s+어디서나|누구나\s+쉽게)", "concretize",
         "얼마나 쉬운지 사실로 쓴다(「3단계」「5분」). 자료에 없으면 「[단계·시간]」.", "[단계·시간]"),
    Rule("ai", rf"[{_H}]+(?:을|를)\s+(?:경험하세요|만나\s*보세요|느껴\s*보세요)|여정(?:을|이|의)?(?=[\s.,])", "rewrite",
         "광고 상투 표현 대신 독자가 할 일을 쓴다(「14일 무료로 써 보기」 — 자료에 있을 때)."),
    Rule("ai", r"—|―|!{2,}", "replace", "줄표·느낌표 겹침은 쉼표·마침표로.",
         replace=lambda m: "!" if m.group(0).startswith("!") else ", "),
    # ── 번역투 ──
    Rule("translationese", rf"([{_H}]+)(?:함에|하는\s*데)\s+있어(?:서)?", "replace",
         "「~함에 있어서」「~하는 데 있어」→「~할 때」.", replace=r"\1할 때"),
    Rule("translationese", rf"[{_H}A-Za-z0-9]+(?:에|에게)\s+있어(?:서)?(?=[\s,])(?!\s*(?:못|안|좋|다행))", "check",
         "번역투(in/for)면 「~에서」「~에게」로 줄인다. 「집에 있어서」처럼 실제로 있다는 뜻이면 그대로 둔다."),
    Rule("translationese", rf"[{_H}A-Za-z0-9]+(?:을|를)\s+통(?:해서|해|한)", "check",
         "「~을 통해」→「~으로/~로」 또는 「~해서」(「교육을 통해 개선」→「교육으로 개선」)."),
    Rule("translationese", r"되어집니다|보여집니다|되어지(?=[는고며면어었다])|되어진|되어졌|되어질|보여지(?=[는고며면다])|보여진|보여졌|보여질|"
         r"쓰여지(?=[는고며면다])|쓰여진|만들어지게\s+되", "replace",
         "이중 피동은 피한다(국립국어원 안내): 「되어지는」→「되는」, 「보여진」→「보인」.",
         replace=lambda m: {
             "되어집니다": "됩니다", "보여집니다": "보입니다", "되어지": "되", "되어진": "된", "되어졌": "되었", "되어질": "될", "보여지": "보이", "보여진": "보인", "보여졌": "보였",
             "보여질": "보일",
             "쓰여지": "쓰이", "쓰여진": "쓰인",
         }.get(m.group(0))),
    Rule("translationese", rf"([{_H}]*)하는\s+중에\s+있", "replace", "「~하는 중에 있다」→「~하고 있다」.",
         replace=r"\1하고 있"),
    Rule("translationese", r"에\s+위치한", "replace", "「~에 위치한」→「~에 있는」.", replace="에 있는"),
    Rule("translationese", rf"([{_H}]*)하는\s+것이\s+가능(합니다|하다)", "replace",
         "「~하는 것이 가능합니다」→「~할 수 있습니다」.",
         replace=lambda m: f"{m.group(1)}할 수 {'있습니다' if m.group(2) == '합니다' else '있다'}"),
    Rule("translationese", r"(?:을|를)\s+필요로\s+(?:하|한|합)", "check", "「~을 필요로 한다」→「~이 필요하다」."),
    Rule("translationese", rf"[{_H}]+에\s+의(?:해|하여|한)(?=\s)", "check",
         "피동 번역투. 행위자를 주어로 세운다(「A에 의해 결정됐다」→「A가 결정했다」)."),
    Rule("translationese", rf"[{_H}]+(?:로|으로)부터", "check", "「~로부터」→「~에서」「~에게서」."),
    Rule("translationese", r"(?:으로|로)\s+인해(?:서)?", "check", "「~으로 인해」→「~으로」「~ 때문에」."),
    Rule("translationese", r"(?:회의|모임|간담회|행사|시간|자리|기회)(?:를|을)\s+가(?:졌|지|집|질)", "check",
         "「회의를 가졌다」→「회의를 했다」."),
    Rule("translationese", r"에\s+다름\s+아니|임에\s+틀림없|에\s+다름없", "rewrite", "「~에 다름 아니다」→「바로 ~이다」 또는 「~이다」."),
    Rule("translationese", rf"[{_H}A-Za-z0-9]+에\s+대(?:한|해서?)(?=\s)", "check",
         "「~에 대한」이 이어지면 줄인다(「품질에 대한 점검」→「품질 점검」)."),
    # ── 영어 상투어 ──
    Rule("english", r"\b(?:in today's (?:fast-paced|ever-changing|digital) world|at the end of the day|moving forward)\b,?\s*",
         "delete", "Stock opener; cut it and start with the fact.", replace="", flags=re.I),
    Rule("english", r"\bit(?:'s| is) (?:important|worth) (?:to note|noting) that\s+", "delete",
         "Cut the throat-clearing; state the point.", replace="", flags=re.I),
    Rule("english", r"\bnot just (?:a|an|another)\b|\bfull potential\b|\bnext level\b", "rewrite",
         "Drop the contrast frame or the stock phrase; say what it does.", flags=re.I),
    Rule("english", r"\bin order to\b", "replace", "「in order to」→「to」.", replace="to", flags=re.I),
    Rule("english", r"\b(?:very|really|extremely|incredibly|basically|actually)\s+(?=[a-z])", "delete",
         "Weak intensifier or filler; cut it.", replace="", flags=re.I),
    Rule("english", r"\b(?:leverag(?:e|es|ed|ing)|utiliz(?:e|es|ed|ing)|synerg(?:y|ies)|paradigm(?: shift)?|holistic|delv(?:e|es|ing))\b",
         "replace", "Plain word: leverage/utilize → use; synergy/paradigm → say what changes.", flags=re.I),
    Rule("english", r"\b(?:cutting-edge|state-of-the-art|best-in-class|world-class|innovative|next-generation|"
         r"game-?changer|revolutioni[sz]e[sd]?|unparalleled|seamless(?:ly)?|robust|powerful|streamlin(?:e|es|ed|ing)|"
         r"empower(?:s|ed|ing)?|unlock(?:s|ed|ing)?|elevat(?:e|es|ed|ing))\b",
         "concretize", "Replace with the fact it stands for (what, how much, for whom); else [specific fact].",
         "[specific fact]", flags=re.I),
)

_COMPILED = tuple((rule, re.compile(rule.pattern, rule.flags)) for rule in RULES)

#: Quoted spans are the author's or a source's words: reported, never fixed by code.
_QUOTE = re.compile(r"「[^」\n]*」|『[^』\n]*』|“[^”\n]*”|\"[^\"\n]*\"|‘[^’\n]*’")


def _quoted(line: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _QUOTE.finditer(line)]


def _fix_for(rule: Rule, m: re.Match) -> str | None:
    if rule.replace is None:
        return None
    if callable(rule.replace):
        return rule.replace(m)
    return m.expand(rule.replace)


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def scan(text: str, material: str = "", categories: list[str] | None = None) -> list[dict]:
    """Every phrase found, line by line, overlaps resolved in favour of the earlier, longer one."""
    wanted = set(categories or CATEGORIES)
    flat_material = _norm(material)
    out: list[dict] = []
    for k, line in enumerate((text or "").split("\n")):
        quotes = _quoted(line)
        hits = []
        for order, (rule, rx) in enumerate(_COMPILED):
            if rule.category not in wanted:
                continue
            for m in rx.finditer(line):
                if m.end() == m.start():
                    continue
                hits.append((m.start(), -(m.end() - m.start()), order, rule, m))
        taken: list[tuple[int, int]] = []
        for start, _, _, rule, m in sorted(hits, key=lambda h: (h[0], h[1], h[2])):
            end = m.end()
            if any(a < end and start < b for a, b in taken):
                continue
            taken.append((start, end))
            match = m.group(0)
            quoted = any(a <= start and end <= b for a, b in quotes)
            fixed = _fix_for(rule, m)
            auto = fixed is not None and not quoted
            core = match.strip()
            in_material = bool(core) and _norm(core) in flat_material
            out.append({
                "category": rule.category, "kind": rule.category, "match": match, "text": match,
                "line": k, "start": start, "end": end, "action": rule.action,
                "suggestion": rule.suggestion, "placeholder": rule.placeholder,
                "replace": fixed if fixed is not None else None, "auto": auto,
                "quoted": quoted, "in_material": in_material, "weight": WEIGHTS[rule.action],
            })
    return out


def apply(text: str, findings: list[dict]) -> str:
    """`text` with the `auto` findings replaced; everything else is left for the writer."""
    by_line: dict[int, list[dict]] = {}
    for f in findings or []:
        if f.get("auto") and f.get("replace") is not None:
            by_line.setdefault(int(f["line"]), []).append(f)
    lines = (text or "").split("\n")
    for k, items in by_line.items():
        if not 0 <= k < len(lines):
            continue
        line = lines[k]
        for f in sorted(items, key=lambda f: f["start"], reverse=True):
            if line[f["start"]:f["end"]] != f["match"]:
                continue
            head, tail = line[:f["start"]], line[f["end"]:]
            if not f["replace"] and tail[:1].isascii() and tail[:1].islower() and re.search(r"(?:^|[.!?]\s+)$", head):
                tail = tail[0].upper() + tail[1:]
            line = head + f["replace"] + tail
        lines[k] = re.sub(r"(?<=\S) {2,}", " ", line).replace(" ,", ",")
    return "\n".join(lines)


def eojeol(text: str) -> int:
    return len((text or "").split())


def density(text: str, findings: list[dict]) -> dict:
    words = eojeol(text)
    weighted = sum(float(f.get("weight", 1.0)) for f in findings)
    value = round(100.0 * weighted / words, 1) if words else 0.0
    grade = "낮음" if value <= 3 else "보통" if value <= 8 else "높음"
    counts = {c: 0 for c in CATEGORIES}
    for f in findings:
        counts[f["category"]] = counts.get(f["category"], 0) + 1
    return {"eojeol": words, "hits": len(findings), "weighted": round(weighted, 1),
            "density": value, "grade": grade, "by_category": {c: n for c, n in counts.items() if n}}


# ── the server's check (stage: post) ─────────────────────────────────────


def check(text: str, material: str, computed=None, options=None) -> list[dict]:
    """Findings `{"kind", "text", "line", "start", "end", "placeholder", ...}`.

    Only the phrases code can fix without changing the meaning (`auto`), outside quotes and not
    the material's own wording; the rest is the writer's job, listed by `scan`."""
    options = dict(options or {})
    found = scan(text, material or "", options.get("categories"))
    return [dict(f, placeholder=f["replace"]) for f in found if f["auto"] and not f["in_material"]]


def fix(text: str, findings: list[dict]) -> str:
    return apply(text, findings)


def summary(findings: list[dict]) -> str:
    """「상투어 4개 고침 (번역투 2 · 채움말 2): 「~에 있어서」, …」."""
    if not findings:
        return ""
    counts: dict[str, int] = {}
    for f in findings:
        word = CATEGORY_WORDS.get(f["kind"], f["kind"])
        counts[word] = counts.get(word, 0) + 1
    kinds = " · ".join(f"{word} {n}" for word, n in counts.items())
    shown = ", ".join(dict.fromkeys(f"「{f['text'].strip()[:30]}」" for f in findings))
    return f"상투어 {len(findings)}개 고침 ({kinds}): {shown[:400]}"


# ── the server's post check on a chat answer (`post_check: scan`) ───────

EDITED_HEADINGS = ("고친 글", "다듬은 글", "수정본", "바꾼 글", "수정안", "개선안", "최종본", "고쳐 쓴 글",
                   "다시 쓴 글", "교정본")
_HEADING = re.compile(r"^\s{0,3}(?:#{1,6}\s+|\*\*|\[|【)")
_SECTION_END = re.compile(r"^\s{0,3}(?:#{1,6}\s+|(?:---|\*\*\*)\s*$|\*\*[^*\n]{1,30}\*\*\s*:?\s*$)")
_VALUE = re.compile(r"(?<![\w.])\d[\d,]*(?:\.\d+)?\s?(?:%|퍼센트|배|명|건|개|곳|원|만\s?원|억\s?원|시간|분|초|일|주|개월|년|회|점|위|km|kg|GB)?")


def section(text: str, names: tuple[str, ...] = EDITED_HEADINGS) -> tuple[int, int] | None:
    """Line range [lo, hi) of the body under the first heading that names one of `names`."""
    lines = (text or "").split("\n")
    widest = max(len(n) for n in names) + 12
    for i, line in enumerate(lines):
        bare = re.sub(r"[#*\[\]【】:：()\s]", "", line)
        if not bare or len(bare) > widest or not any(bare.startswith(n.replace(" ", "")) for n in names):
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


def original_text(args: dict) -> str:
    """The text the person gave: the attachment, a fenced block, or the request after its
    first paragraph."""
    attached = str(args.get("attached") or "")
    if attached.strip():
        return attached
    request = str(args.get("request") or "")
    fenced = re.search(r"```[^\n]{0,20}\n([\s\S]{20,}?)\n```", request)
    if fenced:
        return fenced.group(1)
    blocks = [b for b in re.split(r"\n\s*\n", request.strip()) if b.strip()]
    if len(blocks) > 1 and len(blocks[0]) < 400:
        return "\n\n".join(blocks[1:])
    return request


def _hedges(text: str) -> dict[str, int]:
    flat = re.sub(r"\s+", " ", text or "")
    return {name: len(re.findall(rx, flat)) for name, rx in PLAN_HEDGES.items()}


def _digits(token: str) -> str:
    return re.sub(r"[^\d.]", "", token).strip(".")


def post(text: str, args: dict) -> dict:
    """The rewritten text: safe code rewrites only (`auto`); a hedge of the original made
    definite and a number the original lacks go to the writer, then are listed (hedge) or
    blanked (number) on the final pass."""
    out: dict = {"text": text, "fixed": [], "rewrite": [], "flags": []}
    lines = (text or "").split("\n")
    span = section(text)
    lo, hi = span if span is not None else (0, len(lines))
    body = "\n".join(lines[lo:hi])
    original = original_text(args)
    if not body.strip():
        return out
    final = bool(args.get("final"))
    found = check(body, original + "\n" + str(args.get("material") or ""))
    if found:
        body = fix(body, found)
        out["fixed"] += [{"kind": f["kind"], "text": f["text"].strip(),
                          "why": f"→「{f['replace']}」" if f["replace"] else "→ 지움"} for f in found]
    if span is not None:
        before, after = _hedges(original), _hedges(body)
        for name, n in before.items():
            if after.get(name, 0) < n:
                item = {"kind": "hedge", "text": name,
                        "why": "원문의 계획·판단을 확정된 일처럼 바꿨습니다. 원문처럼 계획·판단으로 되돌리세요."}
                (out["flags"] if final else out["rewrite"]).append(item)
    known = {_digits(m.group(0)) for m in _VALUE.finditer(original + "\n" + str(args.get("request") or ""))}
    new = [m.group(0).strip() for m in _VALUE.finditer(body)
           if _digits(m.group(0)) and _digits(m.group(0)) not in known]
    for raw in dict.fromkeys(new):
        if final:
            body = re.sub(rf"(?<![\w.]){re.escape(raw)}", "[구체적 수치]", body, count=1)
            out["fixed"].append({"kind": "number", "text": raw, "why": "원문에 없는 수치 — 빈칸으로 둠"})
        else:
            out["rewrite"].append({"kind": "number", "text": raw,
                                   "why": "원문에 없는 수치입니다. 빼거나 「[구체적 수치]」 빈칸으로 두세요."})
    lines[lo:hi] = body.split("\n")
    out["text"] = "\n".join(lines)
    return out


# ── CLI ──────────────────────────────────────────────────────────────────


def _row(label: str, text: str, material: str, categories, do_apply: bool) -> dict:
    found = scan(text, material, categories)
    row = {"label": label, "findings": found, **density(text, found)}
    if do_apply:
        fixed = apply(text, found)
        row["fixed"] = fixed
        row["after"] = density(fixed, scan(fixed, material, categories))
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
    categories = options.get("categories")
    if categories is not None:
        unknown = [c for c in categories if c not in CATEGORIES]
        if unknown:
            print(json.dumps({"ok": False, "error": f"모르는 분류: {', '.join(map(str, unknown))}",
                              "categories": list(CATEGORIES)}, ensure_ascii=False))
            return 1
    material = str(options.get("material") or "")
    rows = []
    for n, item in enumerate(items[:100], start=1):
        item = item if isinstance(item, dict) else {"text": str(item)}
        rows.append(_row(str(item.get("label") or f"글 {n}"), str(item.get("text") or ""), material,
                         categories, bool(options.get("apply"))))
    for row in rows:
        marks = [f"{CATEGORY_WORDS[f['category']]}「{f['match'].strip()}」" for f in row["findings"]]
        print(f"{row['label']}: 밀도 {row['density']}({row['grade']}) · " + (", ".join(marks[:30]) or "상투어 없음"))
    placeholders = sorted({f["placeholder"] for r in rows for f in r["findings"]
                           if f["placeholder"] and not f["auto"]})
    summary_row = {"ok": True, "items": rows, "placeholders": placeholders,
                   "guide": {a: g for a, g in _GUIDE.items()
                             if any(f["action"] == a for r in rows for f in r["findings"])}}
    print(json.dumps(summary_row, ensure_ascii=False))
    return 0


_GUIDE = {
    "delete": "지운다. 지운 뒤 문장이 서는지 읽어 본다.",
    "replace": "replace 의 말로 바꾼다. auto=true 는 뜻이 같아 그대로 바꿔도 된다.",
    "concretize": "자료에 있는 사실(수치·대상·방법)로 바꾸고 그 자료 줄을 밝힌다. 자료에 없으면 placeholder 를 두고 사용자에게 묻는다. 지어내지 않는다.",
    "rewrite": "문장 틀을 풀고 사실만 남긴다. 사실이 없으면 그 문장을 지운다.",
    "check": "맥락을 보고 정한다. 그대로 둘 수도 있다.",
}


if __name__ == "__main__":
    sys.exit(main(sys.argv))
