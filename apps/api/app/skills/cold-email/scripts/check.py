"""B2B 콜드메일 초안의 제목 길이, 광고성 정보 표기(「(광고)」·전송자·연락처·수신거부), 개인화 변수·빈칸, 본문 길이·링크 수, 후속 메일 반복을 점검합니다."""

from __future__ import annotations

import json
import re
import sys

_AD_MARK = "(광고)"
_AD_VARIANT = re.compile(r"^\s*[(\[{<（［【〈]\s*(?:광\s*[/·.\-_ ]?\s*고|廣告|AD|ad|Ad|광고성)\s*[)\]}>）］】〉]")
_FAKE_REPLY = re.compile(r"^\s*(?:re|fw|fwd|회신|전달)\s*[:：]", re.IGNORECASE)
_TOKEN = re.compile(r"\{\{\s*([^{}\n]{1,30}?)\s*\}\}|\{\s*([^{}\n\s]{1,20})\s*\}|%([A-Za-z_가-힣]{1,20})%|<<\s*([^<>\n]{1,20})\s*>>")
_BRACKET = re.compile(r"\[([^\[\]\n]{1,30})\]")
_PHONE = re.compile(r"(?:0\d{1,2}[-.\s)]?\d{3,4}[-.\s]?\d{4}|1[5-9]\d{2}[-.\s]?\d{4})")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"https?://\S+|www\.\S+")
_ADDRESS = re.compile(r"(?:서울|부산|대구|인천|광주|대전|울산|세종|경기|강원|충북|충남|전북|전남|경북|경남|제주)[^\n]{0,30}?(?:로|길|동|읍|면)\s*\d+|\[\s*(?:회사\s*)?주소\s*\]")
_OPT_OUT_EN = re.compile(r"unsubscribe|opt[\s-]?out|(?:do\s*not|don'?t)\s*(?:wish|want)\s*to\s*receive", re.IGNORECASE)
_OPT_OUT_TOOL = re.compile(r"https?://\S+|\[[^\]\n]*(?:수신\s*거부|unsubscribe)[^\]\n]*\]|\{\{[^}]*(?:unsub|수신거부)[^}]*\}\}|수신\s*거부\s*(?:링크|버튼|하기)", re.IGNORECASE)
_OPT_OUT = re.compile(r"수신\s*거부|수신을\s*원(?:하지|치)\s*않|수신\s*동의\s*(?:를\s*)?철회|구독\s*(?:해지|취소)|unsubscribe", re.IGNORECASE)
_PROMO = re.compile(r"할인|프로모션|특가|이벤트|무료\s*(?:체험|상담|진단|제공)|체험판|가격|요금|견적|구매|도입\s*혜택|쿠폰|웨비나\s*초대")
_CONSENT_ASK = re.compile(r"수신\s*(?:에\s*)?동의|동의해\s*주시면|(?:자료|소식|안내)를?\s*(?:계속\s*)?(?:보내|드려)\s*(?:드려)?도\s*(?:될까요|괜찮을까요)")
_STALE = re.compile(r"확인\s*차\s*(?:다시\s*)?연락|메일\s*(?:은\s*)?확인(?:하셨|해\s*보셨)|혹시\s*보셨|다시\s*한\s*번\s*연락")


def _tokens(text: str) -> list[str]:
    return [next(g for g in m.groups() if g) for m in _TOKEN.finditer(text)]


def _brackets(text: str) -> list[str]:
    return [f"[{m.group(1)}]" for m in _BRACKET.finditer(text)]


def _fill(text: str, values: dict) -> tuple[str, list[str]]:
    missing: list[str] = []

    def put(match: re.Match) -> str:
        key = next(g for g in match.groups() if g).strip()
        value = values.get(key)
        if value in (None, ""):
            missing.append(key)
            return match.group(0)
        return str(value)

    return _TOKEN.sub(put, text), missing


def _bigrams(text: str) -> set[str]:
    norm = re.sub(r"\s+", "", text)
    return {norm[i:i + 2] for i in range(len(norm) - 1)}


def check_email(email: dict, *, advertising, sender: dict, subject_limit: int, consent=None) -> list[dict]:
    subject = " ".join(str(email.get("subject") or "").split())
    body = str(email.get("body") or "")
    out: list[dict] = []

    def add(severity: str, kind: str, message: str) -> None:
        out.append({"severity": severity, "kind": kind, "message": message})

    length = len(subject)
    if not subject:
        add("block", "subject", "제목이 비었다.")
    elif length > subject_limit:
        add("check", "subject", f"제목 {length}자 — 권장 {subject_limit}자를 {length - subject_limit}자 넘는다(모바일 받은편지함에서 잘림).")
    if _FAKE_REPLY.match(subject):
        add("block", "subject", "처음 보내는 메일에 RE:/FW: 를 붙이면 수신자를 속이는 제목이다.")
    if advertising is True:
        if not subject.startswith(_AD_MARK):
            variant = _AD_VARIANT.match(subject)
            add("block", "ad_label",
                f"광고성 메일은 제목 맨 앞에 「(광고)」를 그대로 쓴다. 지금: 「{variant.group(0).strip()}」(변칙 표기)" if variant
                else "광고성 메일은 제목 맨 앞에 「(광고)」를 쓴다(정보통신망법 제50조, 시행령 제61조).")
        name = str(sender.get("name") or "").strip()
        if name and name not in body:
            add("block", "sender", f"본문에 전송자 명칭 「{name}」이 없다.")
        elif not name:
            add("check", "sender", "본문에 전송자 명칭(회사·담당자)이 있는지 확인한다. sender.name 을 주면 대조한다.")
        gone = [name for name, found in (("전자우편주소", _EMAIL.search(body) or "[이메일]" in body),
                                         ("전화번호", _PHONE.search(body) or "[전화번호]" in body),
                                         ("주소", _ADDRESS.search(body)))
                if not found]
        if gone:
            add("block", "contact", f"본문에 전송자의 {'·'.join(gone)}가 없다(별표 6: 명칭·전자우편주소·전화번호 및 주소).")
        if not _OPT_OUT.search(body):
            add("block", "opt_out", "본문에 수신거부·수신동의 철회 안내문이 없다. 예: 「수신을 원하지 않으시면 [수신거부]를 눌러 주세요.」")
        if not _OPT_OUT_EN.search(body):
            add("block", "opt_out_en", "수신거부 안내문은 한글과 영문으로 쓴다. 예: 「If you do not wish to receive this email, click [Unsubscribe].」")
        if not _OPT_OUT_TOOL.search(body):
            add("check", "opt_out_tool", "수신거부를 간편하게 고를 수 있는 기술적 조치(링크·버튼)를 넣는다. 회신 요청만으로는 부족할 수 있다.")
        if consent == "none":
            add("block", "consent", "수신자의 사전 수신동의가 없다. 광고성 메일은 동의 없이 보낼 수 없다(법 제50조①; 거래관계 예외는 그 거래에서 받은 연락처로 6개월 안의 같은 종류 상품만). 동의 요청이나 비광고성 첫 연락으로 바꾼다.")
        elif consent is None:
            add("check", "consent", "수신 동의를 받았는지 확인한다(동의, 또는 6개월 안 거래관계에서 직접 받은 연락처). 명함·공개 대표메일만으로는 동의가 아니다.")
    elif advertising is None:
        cues = sorted(set(m.group(0) for m in _PROMO.finditer(subject + "\n" + body)))
        add("check", "ad_decision",
            "광고성 정보(영리 목적의 제품·서비스 홍보)인지 정해야 한다. 광고면 「(광고)」·전송자·연락처·수신거부가 필요하다."
            + (f" 홍보 단서: {', '.join(cues)}" if cues else ""))
    else:
        cues = sorted(set(m.group(0) for m in _PROMO.finditer(subject + "\n" + body)))
        if cues:
            add("check", "ad_decision", f"광고가 아니라고 했지만 홍보 단서가 있다: {', '.join(cues)}. 광고성 정보인지 다시 판단한다.")
    if advertising is not True and consent == "none" and _CONSENT_ASK.search(body):
        add("block", "consent_request", "수신 동의 없이 보내는 「수신 동의 요청」 메일도 광고성 정보다(KISA 불법스팸 방지 안내서 Q&A). 동의 없이는 보낼 수 없다.")
    tokens = _tokens(subject + "\n" + body)
    if tokens:
        add("info", "tokens", "개인화 변수: " + ", ".join(dict.fromkeys(tokens)) + " — 보내기 전에 수신자마다 채운다.")
    blanks = _brackets(subject + "\n" + body)
    if blanks:
        add("check", "blanks", "채우지 않은 빈칸: " + ", ".join(dict.fromkeys(blanks)))
    links = _URL.findall(body)
    if len(links) >= 3:
        add("check", "links", f"링크가 {len(links)}개다. 콜드메일은 링크 하나 이하가 스팸 판정·회신에 유리하다.")
    if len(body) > 800:
        add("check", "length", f"본문 {len(body)}자 — 800자 안쪽으로 줄인다(한 화면).")
    if _STALE.search(body) and len(body) < 250:
        add("info", "follow_up", "「확인차 연락드립니다」만 있는 후속 메일이다. 새 정보·다른 각도를 하나 넣는다.")
    return out


def main(argv: list[str]) -> int:
    try:
        options = json.loads(argv[1]) if len(argv) > 1 else {}
    except json.JSONDecodeError as exc:
        print(json.dumps({"ok": False, "error": f"인자 JSON 오류: {exc}"}, ensure_ascii=False))
        return 1
    if not isinstance(options, dict):
        options = {}
    emails = options.get("emails")
    if not emails and (options.get("subject") or options.get("body")):
        emails = [{"subject": options.get("subject"), "body": options.get("body")}]
    if not isinstance(emails, list) or not emails:
        print(json.dumps({"ok": False, "error": "emails 목록이 필요합니다"}, ensure_ascii=False))
        return 1
    advertising = options.get("advertising")
    if advertising not in (True, False, None):
        advertising = None
    sender = options.get("sender") if isinstance(options.get("sender"), dict) else {}
    limit = int(options.get("subject_limit") or 30)
    rows = []
    for n, email in enumerate(emails[:20], start=1):
        email = email if isinstance(email, dict) else {"body": str(email)}
        label = str(email.get("label") or f"{n}차")
        findings = check_email(email, advertising=advertising, sender=sender, subject_limit=limit,
                               consent=options.get("consent") if options.get("consent") in ("opt_in", "transaction", "none") else None)
        if n > 1:
            before = _bigrams(str(emails[n - 2].get("body") or "") if isinstance(emails[n - 2], dict) else "")
            now = _bigrams(str(email.get("body") or ""))
            if before and now and len(before & now) / len(before | now) >= 0.6:
                findings.append({"severity": "check", "kind": "follow_up", "message": "앞 메일과 거의 같다. 후속 메일마다 새 각도·근거를 하나씩 넣는다."})
        rows.append({"label": label, "subject_chars": len(" ".join(str(email.get("subject") or "").split())), "findings": findings})
    recipients = []
    for person in (options.get("recipients") or [])[:50]:
        if not isinstance(person, dict):
            continue
        missing: set[str] = set()
        subjects = []
        for email in emails[:20]:
            if not isinstance(email, dict):
                continue
            subject, gone = _fill(str(email.get("subject") or ""), person)
            _, gone_body = _fill(str(email.get("body") or ""), person)
            missing.update(gone + gone_body)
            subjects.append(len(" ".join(subject.split())))
        recipients.append({"recipient": person.get("이름") or person.get("name") or "", "missing": sorted(missing),
                           "subject_chars": subjects, "over_limit": [s for s in subjects if s > limit]})
    counts = {"block": 0, "check": 0, "info": 0}
    for row in rows:
        for finding in row["findings"]:
            counts[finding["severity"]] += 1
        marks = [f"{f['severity']}:{f['kind']}" for f in row["findings"]]
        print(f"{row['label']} (제목 {row['subject_chars']}자): " + (", ".join(marks) if marks else "문제 없음"))
    for person in recipients:
        if person["missing"]:
            counts["block"] += 1
            print(f"수신자 {person['recipient']}: 빠진 변수 {', '.join(person['missing'])}")
    print(json.dumps({"ok": True, "advertising": advertising, "emails": rows, "recipients": recipients, **counts},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
