"""Health red flags in the person's own turns: the answer opens with the emergency action.

A person who describes stroke signs, chest pain with breathlessness, a severe allergic
reaction, suicidal intent, a swallowed poison or overdose, or a fever in a young infant gets
「지금 바로 119…」 (109 for a suicide crisis) as the answer's first line — before any
diagnosis, list or clinic advice, and even when they restricted the form (「진단만, 설명
쓰지 마」). `detect` reads the turn; `instruction` tells the writer; `lead` prepends one
line when the written answer's opening lacks the action. Code only: strict-local turns
and agents keep it, and no model call is made.

The table is small on purpose: each kind needs a combination of signs, or one sign that is
an emergency by itself. A general question about the signs (「뇌졸중 전조 증상이 뭐야?」), a
piece about them to read or write (an article, a story, a translation) and a turn that
says the emergency care already happened are left alone.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from app.services import output_language
from app.services.freshness import without_quoted_transform_sources

_I = re.I


def _p(pattern: str) -> re.Pattern:
    return re.compile(pattern, _I)


# ── signs ────────────────────────────────────────────────────────────────────────────────
#: One side of the body numb or weak (「한쪽 팔이 좀 저렸어」, 「왼팔에 힘이 없어」).
_ONE_SIDE = _p(
    r"(?:(?:한\s*쪽|한편|반\s*쪽|왼\s*쪽|오른\s*쪽|편측)\s*(?:팔|다리|손|발|얼굴|몸|팔다리)|"
    r"(?:왼|오른)(?:팔|손|다리|발))"
    r"(?:이|가|에|은|는|도|만|쪽)?\s*(?:[가-힣]{1,4}\s*){0,2}?"
    r"(?:저리|저려|저렸|저림|마비|감각이?\s*(?:없|둔|이상)|힘이?\s*(?:빠|없|안\s*들)|"
    r"안\s*움직|못\s*움직|축\s*처)|"
    r"\b(?:numb\w*|weak\w*|tingl\w*|paraly\w*)\b[^.!?\n]{0,30}"
    r"\b(?:one\s+side|left|right)\s*(?:side|arm|leg|hand|face)?|"
    r"\b(?:left|right|one)\s+(?:arm|leg|hand|side\s+of\s+(?:my|the|his|her)\s+\w+)\b"
    r"[^.!?\n]{0,20}\b(?:numb|weak|tingl\w*|went\s+dead|paraly\w*)"
)
#: Face drooping (「입이 돌아갔어」, 「얼굴 한쪽이 처졌어」).
_FACE = _p(
    r"입(?:꼬리)?(?:이|가)?\s*(?:한\s*쪽으로\s*)?돌아|"
    r"얼굴[^.!?\n]{0,8}(?:처지|처졌|처짐|비뚤|일그러|마비)|"
    r"\b(?:face|facial)\s+(?:is\s+)?droop\w*|\bdrooping\s+(?:face|mouth|smile)\b"
)
#: Speech suddenly slurred or gone.
_SPEECH = _p(
    r"말(?:이|을)?\s*(?:어눌|안\s*나와|안\s*나오|꼬이|꼬여|잘\s*안\s*(?:나|돼)|못\s*하)|"
    r"발음이?\s*(?:이상|꼬|새)|"
    r"\b(?:slurred\s+speech|speech\s+(?:is\s+)?slurred|slurring|can'?t\s+(?:speak|talk|get\s+words\s+out)|"
    r"trouble\s+(?:speaking|talking))\b"
)
#: Sudden loss of vision in one eye or half the field.
_VISION = _p(
    r"(?:갑자기|순간)[^.!?\n]{0,12}(?:눈|시야)[^.!?\n]{0,8}(?:안\s*보|흐려|캄캄|까매|잃|이상)|"
    r"(?:한\s*쪽\s*눈|시야\s*(?:절반|반쪽))[^.!?\n]{0,8}(?:안\s*보|캄캄|까매)|"
    r"\b(?:sudden(?:ly)?\s+(?:lost|loss\s+of|blurr\w*)\s+(?:my\s+)?vision|"
    r"can'?t\s+see\s+out\s+of\s+(?:one|my\s+(?:left|right))\s+eye)\b"
)
#: The worst headache of one's life, all at once.
_THUNDERCLAP = _p(
    r"벼락\s*두통|(?:태어나서|살면서|평생|인생에서)\s*(?:가장|제일|처음\s*겪는)\s*(?:심한|아픈|극심한)?\s*"
    r"(?:두통|머리)|갑자기[^.!?\n]{0,10}(?:깨질\s*듯|터질\s*듯|극심한|엄청난)\s*(?:두통|머리)|"
    r"\b(?:worst\s+headache\s+of\s+(?:my|his|her|their)\s+life|thunderclap\s+headache|"
    r"sudden\s+(?:severe|excruciating)\s+headache)\b"
)
#: Headache or dizziness, the company stroke signs often keep.
_HEAD = _p(
    r"두통|머리가?\s*(?:아프|아파|지끈)|어지러|어지럼|현기증|\b(?:headache|dizz\w*|vertigo)\b"
)

#: Chest pain, pressure or tightness.
_CHEST = _p(
    r"(?:가슴|흉부|명치)(?:이|가|에|쪽이?|\s*한가운데가?)?\s*(?:[가-힣]{1,3}\s*)?"
    r"(?:아프|아파|아팠|통증|조이|조여|조였|짓눌|누르는|답답|쥐어짜|뻐근)|흉통|"
    r"\bchest\s+(?:pain|tightness|pressure|hurts?)\b|\bmy\s+chest\s+(?:hurts|is\s+tight)\b"
)
#: What makes chest pain an emergency: breathlessness, sweat, spread, nausea, faintness.
_CHEST_WITH = _p(
    r"숨(?:이|을|쉬기)?\s*(?:차|가빠|가쁘|막히|쉬기\s*(?:힘|어렵)|못\s*쉬|안\s*쉬어)|호흡\s*곤란|"
    r"식은\s*땀|땀이?\s*(?:나|비\s*오|흘|줄줄)|"
    r"(?:왼\s*팔|왼쪽\s*팔|턱|등|어깨)[^.!?\n]{0,10}(?:뻗|퍼지|번지|저리|저려|아프|아파)|"
    r"토할|구역|메스꺼|어지러|쓰러질|기절|"
    r"\b(?:short(?:ness)?\s+of\s+breath|breathless|can'?t\s+breathe|sweat\w*|clammy|"
    r"radiat\w*|left\s+arm|jaw|nause\w*|faint\w*|lightheaded)\b"
)

#: What set off an allergic reaction.
_ALLERGEN = _p(
    r"알레르기|알러지|땅콩|견과|갑각류|새우|게를?\s*먹|벌(?:에|한테)?\s*(?:쏘|물)|벌침|"
    r"(?:약|주사|항생제|조영제)[^.!?\n]{0,10}(?:먹고|맞고|먹은\s*뒤|맞은\s*뒤|후에)|"
    r"\b(?:allerg\w*|peanuts?|tree\s+nuts?|shellfish|bee\s+sting|stung|wasp|"
    r"after\s+(?:taking|eating)|anaphyla\w*)\b"
)
#: The airway or circulation signs that make it anaphylaxis.
_AIRWAY = _p(
    r"목(?:이|구멍이|\s*안이)?\s*(?:붓|부어|부었|조이|조여|막히|막혀|막힌)|"
    r"(?:입술|혀|얼굴|눈두덩)(?:이|가)?\s*(?:퉁퉁\s*)?(?:붓|부어|부었)|"
    r"숨(?:이|을|쉬기)?\s*(?:차|막히|막혀|쉬기\s*(?:힘|어렵)|못\s*쉬|안\s*쉬어)|호흡\s*곤란|"
    r"쌕쌕|색색\s*소리|어지러|쓰러|기절|의식이?\s*(?:없|흐려)|"
    r"\b(?:throat\s+(?:is\s+)?(?:swell\w*|tight\w*|closing)|(?:tongue|lips?)\s+(?:is\s+|are\s+)?"
    r"swell\w*|swollen\s+(?:tongue|lips?|throat)|can'?t\s+breathe|trouble\s+breathing|"
    r"wheez\w*|faint\w*|passed\s+out)\b"
)

#: Suicidal intent in the person's words (not 「자살 예방 정책」, not 「죽고 싶을 만큼 창피」).
_SUICIDE = _p(
    r"죽고\s*싶(?!을\s*(?:만큼|정도|지경))|살고\s*싶지\s*(?:않|안)|더\s*(?:는\s*)?살\s*이유가?\s*없|"
    r"사라지고\s*싶|(?:목숨을?|생을?)\s*(?:끊|마감)|자살(?:하고\s*싶|할\s*(?:거|까|생각)|\s*생각|\s*충동|"
    r"\s*계획|을\s*(?:하려|생각))|극단적(?:인)?\s*(?:선택|생각)을?\s*(?:하고|할|하려|생각)|"
    r"유서를?\s*(?:썼|쓰고|써\s*놓)|뛰어내리고\s*싶|"
    r"\b(?:kill\s+myself|end\s+(?:my\s+life|it\s+all)|(?:i\s+)?want\s+to\s+die|suicidal|"
    r"don'?t\s+want\s+to\s+(?:live|be\s+alive)|thinking\s+(?:about|of)\s+suicide)\b"
)

#: Something poisonous swallowed.
_POISON = _p(
    r"(?:세제|락스|표백제|농약|살충제|제초제|부동액|메탄올|공업용\s*알코올|버튼\s*(?:전지|배터리)|"
    r"단추형?\s*(?:전지|건전지)|수은|니코틴\s*액|쥐약|가성\s*소다|하수구\s*(?:세정제|뚫)|"
    r"\b(?:bleach|detergent|pesticide|antifreeze|button\s+batter\w*|drain\s+cleaner|rat\s+poison|"
    r"methanol|lye)\b)[^.!?\n]{0,16}"
    r"(?:삼켰|삼킨|먹었|먹은|마셨|마신|핥았|입에\s*넣|\b(?:swallow\w*|ate|eaten|drank|drunk|ingest\w*)\b)"
)
#: Far too much of a medicine taken.
_OVERDOSE = _p(
    r"(?:약|수면제|진통제|해열제|타이레놀|아세트아미노펜|신경안정제|항우울제|알약)(?:을|를)?\s*"
    r"(?:한\s*(?:통|움큼|주먹)|\d{2,}\s*(?:알|정|개)|잔뜩|통째로|한꺼번에\s*(?:많이|\d+))"
    r"[^.!?\n]{0,8}(?:먹었|삼켰|먹은|삼킨|털어)|"
    r"(?:약|수면제|진통제)\s*(?:을|를)?\s*과다\s*복용(?:했|한)|"
    r"\b(?:overdos\w*|took\s+(?:a\s+(?:whole\s+)?bottle|\d{2,}\s+(?:pills|tablets)))\b"
)

#: A baby under three months (생후 N일·주, 1–2개월, 신생아).
_INFANT = _p(
    r"신생아|생후\s*(?:\d{1,2}\s*(?:일|주)|[12]\s*(?:개월|달))|(?<!\d)[12]\s*개월\s*(?:된|짜리)?\s*"
    r"(?:아기|애기|아이|우리\s*애|딸|아들)|"
    r"\b(?:newborn|(?:[1-9]|1[0-2])[- ](?:day|week)[- ]old|[12][- ]month[- ]old|"
    r"(?:one|two)[- ]month[- ]old)\b"
)
#: A young child of any age.
_CHILD = _p(
    r"아기|애기|아이(?:가|는|의)|우리\s*애|(?<![가-힣])애(?:가|는)\s|\d+\s*(?:개월|살|세)\s*(?:된\s*)?"
    r"(?:아기|아이|딸|아들|애)|\b(?:baby|toddler|my\s+(?:son|daughter|child|kid))\b"
)
_FEVER = _p(
    r"열(?:이|은)?\s*(?:나|났|올라|있|펄펄|39|38|40)|발열|고열|미열|"
    r"(?:체온|열)[^.!?\n]{0,6}(?:3[89]|4[01])(?:\.\d)?\s*(?:도|℃|°)|"
    r"\b(?:fever|temperature\s+of\s+(?:3[89]|4[01]|10[0-4])|10[0-4](?:\.\d)?\s*°?\s*f)\b"
)
#: A feverish child's emergency signs: fitting, floppy, unrousable, blue, stiff neck, purple rash.
_CHILD_DANGER = _p(
    r"경련|발작|축\s*처|깨워도|안\s*깨|의식|입술이?\s*(?:파래|퍼래|새파래)|목이?\s*뻣뻣|"
    r"숨(?:을|이)?\s*(?:헐떡|가빠|가쁘|못\s*쉬)|눌러도\s*(?:안\s*없어지|그대로)|보라색\s*반점|"
    r"\b(?:seizure|convuls\w*|fitting|floppy|limp|won'?t\s+wake|unresponsive|blue\s+lips|"
    r"stiff\s+neck|rash\s+(?:that\s+)?(?:doesn'?t|does\s+not)\s+fade)\b"
)


@dataclass(frozen=True, slots=True)
class Flag:
    #: `KINDS` key
    kind: str
    #: the signs read, for the step and tests
    signs: tuple[str, ...]


#: kind → (what the writer is told the person describes, Korean lead line, English lead line).
#: Lines say what to do now, in one or two sentences; no lecture.
KINDS: dict[str, tuple[str, str, str]] = {
    "suicide": (
        "thoughts of ending their life",
        "**혼자 견디지 말고 지금 자살예방상담전화 109(24시간)에 전화하세요. "
        "당장 위험하다면 119에 연락하세요.**",
        "**Please don't carry this alone — call the suicide crisis line 109 in Korea "
        "(24 hours), or 119 / your local emergency number if you are in danger now.**",
    ),
    "anaphylaxis": (
        "signs of a severe allergic reaction (anaphylaxis)",
        "**중증 알레르기 반응(아나필락시스)일 수 있어요. 지금 바로 119에 전화하고, "
        "에피네프린 자가주사기가 있으면 쓰세요.**",
        "**This may be anaphylaxis. Call 119 (Korea) or your local emergency number now, "
        "and use an epinephrine auto-injector if you have one.**",
    ),
    "poison": (
        "a poison or an overdose swallowed",
        "**지금 바로 119에 전화해 무엇을 얼마나 먹었는지 알리세요. 억지로 토하게 하지 마세요.**",
        "**Call 119 (Korea) or your local emergency number now and say what was taken and "
        "how much. Do not try to make them vomit.**",
    ),
    "chest": (
        "chest pain with warning signs of a heart attack",
        "**심근경색 같은 응급 상황일 수 있어요. 지금 바로 119에 전화하세요. 직접 운전해서 "
        "가지 마세요.**",
        "**This could be a heart attack. Call 119 (Korea) or your local emergency number now; "
        "do not drive yourself.**",
    ),
    "stroke": (
        "warning signs of a stroke or TIA",
        "**뇌졸중이나 일과성 허혈 발작의 신호일 수 있어요. 증상이 지금 나아졌더라도 바로 "
        "119에 전화하거나 응급실로 가세요.**",
        "**These can be signs of a stroke or TIA. Even if they have eased, call 119 (Korea) or "
        "your local emergency number, or go to the emergency room now.**",
    ),
    "infant_fever": (
        "a fever in a baby under three months or a feverish child with danger signs",
        "**아기의 이런 열은 응급일 수 있어요. 지금 바로 응급실로 가거나 119에 연락하세요.**",
        "**A fever like this in a baby can be an emergency. Go to the emergency room or call "
        "119 (Korea) / your local emergency number now.**",
    ),
}


def _signs(text: str) -> tuple[str, tuple[str, ...]] | None:
    """The first kind whose combination this text (one or more turns) carries."""
    if _SUICIDE.search(text):
        return "suicide", ("suicidal intent",)
    if _ALLERGEN.search(text) and _AIRWAY.search(text):
        return "anaphylaxis", ("allergen", "airway or faint")
    if _POISON.search(text) or _OVERDOSE.search(text):
        return "poison", ("swallowed poison or overdose",)
    if _CHEST.search(text) and _CHEST_WITH.search(text):
        return "chest", ("chest pain", "breathless, sweat, spread or faint")
    stroke = [name for name, pattern in (("one side numb or weak", _ONE_SIDE),
                                         ("face droop", _FACE), ("speech", _SPEECH),
                                         ("sudden vision loss", _VISION),
                                         ("headache or dizziness", _HEAD))
              if pattern.search(text)]
    if _THUNDERCLAP.search(text):
        return "stroke", ("worst sudden headache",)
    if "face droop" in stroke or len(stroke) >= 2:
        return "stroke", tuple(stroke)
    if _FEVER.search(text) and (_INFANT.search(text)
                                or (_CHILD.search(text) and _CHILD_DANGER.search(text))):
        return "infant_fever", ("fever", "infant or danger sign")
    return None


#: A turn about the signs, not of them: a general question, or a text to read or write.
_GENERAL = _p(
    r"(?:증상|징후|전조|신호|위험\s*신호)(?:이|은|는|에는|엔|으로|를)?\s*"
    r"(?:뭐|무엇|어떤|어떻게|알려|정리|설명|구분)|"
    r"(?:이란|란)\s*(?:뭐|무엇)|"
    r"\bwhat\s+are\s+(?:the\s+)?(?:signs|symptoms|warning\s+signs)\b|"
    r"\b(?:signs|symptoms)\s+of\b[^.!?\n]{0,30}\?"
)
_ABOUT = _p(
    r"기사|논문|보고서|요약|발표\s*자료|슬라이드|교육\s*자료|포스터|캠페인|퀴즈|시험\s*문제|"
    r"소설|동화|시나리오|대본|웹툰|캐릭터|주인공|등장\s*인물|번역|통역|"
    r"\b(?:article|paper|summar\w*|report|slides?|poster|campaign|quiz|novel|story|script|"
    r"screenplay|character|protagonist|translat\w*)\b"
)
#: The person speaks of themselves or someone with them — a general question is then theirs.
_PERSONAL = _p(
    r"(?<![가-힣])(?:나|내가|내|저|제가|저는|제)(?=\s|가|는|도|의|$)|우리\s*(?:아이|애|엄마|아빠|남편|아내)|"
    r"(?:아이|아기|애기|엄마|아빠|어머니|아버지|남편|아내|할머니|할아버지|친구|동생)(?:가|께서|이)\s|"
    r"\b(?:i|i'm|i've|my|me)\b"
)
#: The emergency care already happened: no line to repeat.
_CARED = _p(
    r"응급실(?:에|에서)?\s*(?:다녀|갔다|갔었|왔|있어|있는\s*중|검사)|119(?:를|에)?\s*(?:불렀|불러서|신고했|"
    r"전화했)|구급차(?:를)?\s*(?:불렀|타고)|병원에서\s*(?:괜찮|이상\s*없|검사\s*(?:받|했))|"
    r"\b(?:(?:went|been)\s+to\s+the\s+(?:er|emergency)|called\s+(?:911|119|an\s+ambulance)|"
    r"(?:er|hospital)\s+(?:said|cleared))\b"
)

#: Earlier user turns read with the latest one (P14: headache in turn 1, the arm in turn 2).
_EARLIER = 2


def _clean(text: str) -> str:
    return without_quoted_transform_sources(unicodedata.normalize("NFC", text or ""))[:6000]


def _about(text: str) -> bool:
    """A general question about the signs, or a text about them to read or write."""
    if _ABOUT.search(text):
        return True
    return bool(_GENERAL.search(text) and not _PERSONAL.search(text))


def detect(request: str, earlier: Sequence[str] = ()) -> Flag | None:
    """The red flag the person's latest turn raises, with its earlier turns, or None.

    The latest turn must carry a sign of its own; earlier turns may complete the
    combination. A conversation opened as a story or a translation stays out.
    """
    latest = _clean(request)
    if not latest.strip() or _about(latest) or _CARED.search(latest):
        return None
    if earlier and _ABOUT.search(_clean(earlier[0])) and not _PERSONAL.search(latest):
        return None
    own = _signs(latest)
    if own is None:
        joined = "\n".join([*(_clean(t) for t in list(earlier)[-_EARLIER:]), latest])
        own = _signs(joined)
        # the latest turn adds a sign of that kind; a later unrelated turn raises nothing
        if own is None or not _latest_adds(own[0], latest):
            return None
    return Flag(kind=own[0], signs=own[1])


def _latest_adds(kind: str, latest: str) -> bool:
    patterns = {
        "stroke": (_ONE_SIDE, _FACE, _SPEECH, _VISION, _THUNDERCLAP),
        "chest": (_CHEST, _CHEST_WITH),
        "anaphylaxis": (_AIRWAY,),
        "infant_fever": (_FEVER, _CHILD_DANGER),
    }.get(kind, ())
    return any(p.search(latest) for p in patterns)


URGENT_INSTRUCTION = (
    "Safety first: the person's message describes {what}, which can be an emergency. Open "
    "the answer with one or two short sentences telling them to {action} now, in their "
    "language (예: 「{example}」), before any diagnosis, list or other advice. Do this even "
    "when they asked for a fixed form (「진단만」, 「설명 쓰지 마」, a role); after that "
    "line, keep their form. No lecture and no disclaimer list."
)
_ACTION = {
    "suicide": ("call the suicide crisis line 109 (24 hours), or 119 if they are in danger now",
                "혼자 견디지 말고 지금 109(자살예방상담전화)에 전화하세요."),
}
_DEFAULT_ACTION = ("call 119 or go to the emergency room",
                   "지금 바로 119에 전화하거나 응급실로 가세요.")


def instruction(kind: str) -> str:
    """The writer's rule for a turn of this `KINDS` kind; empty for any other."""
    if kind not in KINDS:
        return ""
    action, example = _ACTION.get(kind, _DEFAULT_ACTION)
    return URGENT_INSTRUCTION.format(what=KINDS[kind][0], action=action, example=example)


#: The emergency action, as an answer would say it.
_ACTIONS = _p(
    r"119|응급실|응급\s*의료\s*센터|구급차|"
    r"\b(?:911|999|emergency\s+(?:room|department|services|number)|ambulance|\ber\b)"
)
_CRISIS = _p(
    r"(?<!\d)(?:109|1393|988)(?!\d)|119|\b(?:crisis\s+line|hotline)\b"
)
#: How much of the answer counts as its opening: what a reader sees before the details —
#: the first two paragraphs past bare headings, at most this many characters. An action the
#: writer put there (「치료 계획: 지금 바로 119에…」 under a short diagnosis) is not repeated.
_OPENING = 600
_OPENING_BLOCKS = 2
_BARE_LINE = re.compile(r"\s*(?:#{1,6}\s+.*|\*\*[^*\n]{1,40}\*\*:?|[^\n]{1,20}:)\s*")


def _opening(answer: str) -> str:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", answer.strip()) if b.strip()]
    taken: list[str] = []
    content = 0
    for block in blocks:
        taken.append(block)
        if not all(_BARE_LINE.fullmatch(line) for line in block.splitlines()):
            content += 1
            if content == _OPENING_BLOCKS:
                break
    return "\n\n".join(taken)[:_OPENING]


def leads_with_action(answer: str, flag: Flag) -> bool:
    """The answer's opening already tells them what to do now."""
    opening = _opening(answer)
    return bool((_CRISIS if flag.kind == "suicide" else _ACTIONS).search(opening))


def lead(answer: str, flag: Flag | None) -> str:
    """The answer with the emergency line first, when its opening lacks the action."""
    if flag is None or flag.kind not in KINDS or not answer.strip():
        return answer
    if leads_with_action(answer, flag):
        return answer
    _, korean, english = KINDS[flag.kind]
    # the answer's language as the finish stages read it (`output_language.reads_english`)
    line = english if output_language.reads_english(answer) else korean
    return f"{line}\n\n{answer.lstrip()}"


__all__ = ["KINDS", "Flag", "detect", "instruction", "lead", "leads_with_action"]
