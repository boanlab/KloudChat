"""Turns whose answer rests on facts that change or must be exact.

`search_reason` names why the first web search of a chat turn is forced (with search 「자동」
or on): contact points and hotlines, laws, tax rules and benefits, prices, rates and limits,
event schedules, availability, organizations, statistics, and legal, tax, financial or
medical advice. `advice_domain` names the advice domains whose answers get the grounding
check by default. Creative writing, translation and proofreading, code and spreadsheet
formulas, role-play, drafting and pure concept explanations are left alone.

Earlier user turns of the conversation count for a short follow-up (「1년 안에 쓸 3천만
원이야」 after an investment question); a role the conversation was set up as (a translator,
a salesperson on a pretend call, a formula generator) keeps its turns out.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from app.services.freshness import without_quoted_transform_sources

#: Turns that are not fact lookups whatever words they carry.
_TRANSLATE = re.compile(
    r"번역|통역|맞춤법|띄어쓰기|교정해|윤문|문장(?:을|만)?\s*(?:고쳐|다듬)|"
    r"\b(?:translat\w*|proofread\w*|spelling\s+correct\w*)\b",
    re.I,
)
_CREATIVE = re.compile(
    r"(?<![가-힣])시(?:를|\s*한\s*편|\s*(?:써|지어|작성))|소설|동화|그림책|우화|"
    r"이야기(?:를)?\s*(?:써|만들|지어|들려)|에세이|수필|가사|노랫말|슬로건|카피(?:를|\s*써)|"
    r"자기소개서|커버\s*레터|링크드인|"
    r"\b(?:poem|poetry|short\s+story|story|novel|lyrics|essay|slogan|cover\s+letter|"
    r"linkedin\s+post)\b",
    re.I,
)
#: Code or a formula to write or read, not a question about a language's releases.
_CODE = re.compile(
    r"엑셀|스프레드시트|구글\s*시트|수식|함수|매크로|정규식|쿼리|"
    r"코드(?:를|로|가|\s*(?:짜|작성|고쳐|설명|리뷰|검토))|"
    r"\b(?:excel|spreadsheet|formula|vba|sql|regex)\b|"
    r"\b(?:write|fix|debug|explain|review)\s+(?:this\s+|the\s+|a\s+|my\s+)?(?:code|function|script)\b",
    re.I,
)
_ROLEPLAY = re.compile(
    r"역할극|롤플레이|상황극|(?:다|라)고\s*치고|연기해|흉내|모의\s*(?:면접|인터뷰|협상)|"
    r"면접관|영업\s*사원|판매원|손님\s*역할|고객\s*역할|"
    r"\b(?:role[- ]?play|pretend|mock\s+interview|interviewer|act\s+as\s+(?:an?\s+)?"
    r"(?:customer|caller|client|salesperson|interviewer))\b",
    re.I,
)
_DRAFT = re.compile(
    r"초안|양식|템플릿|서식|(?:메일|이메일|편지|공지|안내문|계약서|합의서)[^.!?\n]{0,12}"
    r"(?:써|작성|만들)|\b(?:draft|template|write\s+(?:an?\s+)?(?:email|letter|agreement|"
    r"contract))\b",
    re.I,
)
_EXPLAIN = re.compile(
    r"개념|원리|정의(?:가|는|를)|뜻(?:이|은|을)|어원|비유로|차이(?:가|는|점)|소크라테스|퀴즈|"
    r"문제(?:를)?\s*(?:내|출제)|\b(?:concept|etymology|analogy|quiz|socratic)\b",
    re.I,
)
#: An explanation that still asks for a current or exact value.
_EXACT_ASK = re.compile(
    r"지금|현재|요즘|올해|최신|얼마|몇|번호|연락처|한도|세율|금리|기준(?:액|금액)|"
    r"\b(?:current|now|latest|how\s+much|what\s+is\s+the\s+(?:rate|limit|number))\b",
    re.I,
)

#: Medical and health guidance.
_MEDICAL = re.compile(
    r"증상|진단|치료|처방|약물|복용|용량|투약|부작용|진통제|해열제|항생제|수면제|항우울제|"
    r"영양제|보충제|두통|어지럼|어지러|현기증|저림|저려|저렸|마비|흉통|가슴\s*통증|통증|"
    r"복통|발열|구토|설사|출혈|우울|불안\s*(?:장애|증)|공황|자살|자해|극단적\s*선택|불면|"
    r"병원|응급실|응급|질환|질병|감염|혈압|당뇨|임신|이가?\s*시려|이\s*시림|잇몸|충치|"
    r"진료|건강\s*검진|의사(?:가|로|에게|\s*선생)|"
    r"\b(?:symptoms?|medications?|dosage|side\s+effects?|"
    r"headaches?|dizz\w*|chest\s+pain|depress\w*|suicid\w*|self[- ]harm|"
    r"anxiety\s+disorder|emergency\s+room|prescription|doctor)\b",
    re.I,
)
#: Legal matters (laws in general are under `_LAW_TAX` too).
_LEGAL = re.compile(
    r"법률|법적|법령|판례|소송|고소|고발|합의(?:금|서|하자|를|해|\s*제안)|과실\s*비율|"
    r"교통\s*사고|사고\s*처리|보험\s*처리|손해\s*배상|위자료|형사|민사|처벌|벌금|과태료|"
    r"변호사|노무사|근로\s*기준|부당\s*해고|퇴직금|임금\s*체불|임대차|전세\s*(?:사기|계약)|"
    r"보증금\s*반환|계약\s*해지|위약금|상속|이혼|양육권|"
    r"\b(?:legal|lawyer|attorney|lawsuit|sue|liabilit\w*|criminal|statute|court)\b",
    re.I,
)
_TAX = re.compile(
    r"세금|세율|세법|소득세|부가(?:가치)?세|법인세|양도(?:소득)?세|상속세|증여세|종부세|"
    r"종합\s*부동산세|취득세|재산세|간이\s*과세|일반\s*과세|과세|비과세|공제|감면|절세|"
    r"연말\s*정산|종합\s*소득|세무|"
    r"\b(?:tax(?:es|ation)?|deductions?|vat|irs)\b",
    re.I,
)
_FINANCIAL = re.compile(
    r"금리|(?<![가-힣])이자|예금|적금|파킹\s*통장|CMA|MMF|ETF|채권|국채|펀드|주식|주가|증시|주식\s*시장|"
    r"투자|수익률|(?<!지)원금|예금자\s*보호|대출|연금|보험료|재무|자산\s*(?:관리|배분)|저축|"
    r"스톡\s*옵션|환율|돈을?\s*굴리|"
    r"\b(?:interest\s+rates?|savings|deposits?|invest\w*|stocks?|bonds?|portfolio|"
    r"loans?|mortgage|pension|retirement\s+fund|etf|yield)\b",
    re.I,
)
#: Facts that change or must be exact, outside the advice domains.
_CONTACT = re.compile(
    r"전화\s*번호|연락처|상담\s*(?:전화|번호|센터|기관)|핫라인|콜\s*센터|고객\s*센터|"
    r"신고\s*(?:번호|전화|센터|처)|긴급\s*(?:전화|번호)|문의처|"
    r"어디(?:에|로)\s*(?:연락|신고|문의|전화|상담)|"
    r"\b(?:hotline|helpline|crisis\s+line|phone\s+number|contact\s+(?:number|details))\b",
    re.I,
)
_RULES = re.compile(
    r"법령|법규|시행령|조례|규정|규제|지원금|보조금|수당|급여\s*(?:기준|대상)|혜택|"
    r"자격\s*(?:요건|조건)|신청\s*(?:자격|기간|방법)|접수\s*기간|"
    r"\b(?:regulations?|eligibility|subsid\w*|grants?|allowance)\b",
    re.I,
)
_PRICE = re.compile(
    r"가격|시세|요금|수수료|입장료|물가|최저가|항공권|티켓|기차표|열차|운임|한도|상한|"
    r"\b(?:prices?|fees?|fares?|tickets?|rates?|limits?|cost\s+of)\b",
    re.I,
)
_SCHEDULE = re.compile(
    r"개최|행사|박람회|컨퍼런스|축제|시험\s*일정|운영\s*시간|영업\s*시간|휴관|휴무|공휴일|"
    r"예약|예매|박물관|미술관|"
    r"\b(?:opening\s+hours|museums?|conference|festival)\b",
    re.I,
)
_AVAILABILITY = re.compile(
    r"출시|판매\s*(?:중|처|여부)|단종|서비스\s*(?:종료|중단)|지원\s*(?:종료|여부)|직항|노선|"
    r"항공편|자격증|부트캠프|플랫폼|어디서\s*(?:사|팔|구매|구입|살)|"
    r"\b(?:availab\w*|in\s+stock|discontinued|direct\s+flights?|certifications?|bootcamps?)\b",
    re.I,
)
_ORGANIZATION = re.compile(
    r"담당\s*기관|소관|관할|주무\s*부처|어느\s*기관|기록관|문서고|소장(?:처|\s*기관)|"
    r"\b(?:which\s+(?:agency|organi[sz]ation|office)|archives?|where\s+(?:are|is)\s+"
    r"(?:they|it)\s+(?:held|kept))\b",
    re.I,
)
_STATISTICS = re.compile(
    r"통계|점유율|인구|실업률|발생률|유병률|사망률|평균\s*(?:연봉|소득|임금|수명)|"
    r"\b(?:statistics|prevalence|market\s+share|unemployment\s+rate)\b",
    re.I,
)

_DOMAINS = (("medical", _MEDICAL), ("legal", _LEGAL), ("tax", _TAX), ("financial", _FINANCIAL))
_FACTS = (
    ("contact", _CONTACT), ("rules", _RULES), ("price", _PRICE), ("schedule", _SCHEDULE),
    ("availability", _AVAILABILITY), ("organization", _ORGANIZATION),
    ("statistics", _STATISTICS),
)

#: Korean names of the reasons, for a turn's steps.
LABELS = {
    "medical": "건강·의료", "legal": "법률", "tax": "세금", "financial": "금융",
    "contact": "연락처", "rules": "제도·규정", "price": "가격·요금", "schedule": "일정",
    "availability": "이용 가능 여부", "organization": "기관", "statistics": "통계",
}

#: Earlier user turns read for a follow-up.
_EARLIER = 3


def _clean(text: str) -> str:
    return without_quoted_transform_sources(unicodedata.normalize("NFC", text or ""))[:6000]


def not_a_lookup(request: str) -> bool:
    """Writing, translation, code or formulas, role-play, drafting or a concept explanation."""
    return _left_alone(_clean(request))


def _left_alone(text: str) -> bool:
    """A turn kind that is not a fact lookup."""
    if _TRANSLATE.search(text) or _CREATIVE.search(text) or _CODE.search(text):
        return True
    if _ROLEPLAY.search(text) or _DRAFT.search(text):
        return True
    return bool(_EXPLAIN.search(text) and not _EXACT_ASK.search(text))


def _set_up_as_other(earlier: Sequence[str]) -> bool:
    """The conversation opened as a translator, a role-play, a formula generator…"""
    if not earlier:
        return False
    opening = _clean(earlier[0])
    return bool(_TRANSLATE.search(opening) or _ROLEPLAY.search(opening)
                or _CODE.search(opening) or _CREATIVE.search(opening))


def _texts(request: str, earlier: Sequence[str]) -> list[str] | None:
    """Latest turn first, then the earlier ones it continues; None when left alone."""
    latest = _clean(request)
    if not latest.strip() or _left_alone(latest) or _set_up_as_other(earlier):
        return None
    return [latest, *(_clean(t) for t in list(earlier)[-_EARLIER:][::-1])]


def _first(texts: list[str], table: tuple[tuple[str, re.Pattern], ...]) -> str | None:
    for text in texts:
        for name, pattern in table:
            if pattern.search(text):
                return name
    return None


def advice_domain(request: str, earlier: Sequence[str] = ()) -> str | None:
    """「medical」 「legal」 「tax」 「financial」 for an advice turn, else None."""
    texts = _texts(request, earlier)
    return _first(texts, _DOMAINS) if texts else None


def search_reason(request: str, earlier: Sequence[str] = ()) -> str | None:
    """Why this turn's first web search is forced, or None.

    The latest turn decides first; earlier turns count only when the latest names no reason
    of its own (a follow-up giving the person's details).
    """
    texts = _texts(request, earlier)
    if not texts:
        return None
    for text in texts:
        if (found := _first([text], _DOMAINS) or _first([text], _FACTS)):
            return found
    return None


__all__ = ["LABELS", "advice_domain", "not_a_lookup", "search_reason"]
