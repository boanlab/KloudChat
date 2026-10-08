"""카피 점검(copy-check, stage post): 채팅 답의 근거 없는 표현과 채널 한도를 서버가 고치고,
붙여 넣은 자료의 「계산」이 계산 사전 점검을 부르지 않는다."""

from __future__ import annotations

import asyncio
import json

from app.pipeline import registry
from app.pipeline.bundle import Bundle, Document, Material, Request
from app.pipeline.stage import Context
from app.services import calculation_policy, copy_check, skill_packages, skill_scripts

claims = skill_scripts.module("copy-check", "claims")
length = skill_scripts.module("copy-check", "length")

# ── the calculation pre-check reads the person's own instruction ─────────────

HM2 = (
    "11월 뉴스레터 메일 제목 후보 15개 줘. 30자 안으로.\n\n"
    "[머니로그 11월 뉴스레터 — 개인 재무 앱 머니로그]\n"
    "- 이번 호 내용: 연말정산 미리보기 기능 출시 안내(앱에서 예상 환급액 계산), 11월 카드 실적 "
    "점검 방법, 독자 질문 답변(청년도약계좌 납입 일시 중지)\n"
    "- 연말정산 미리보기: 11월 20일 앱 업데이트로 제공\n"
    "- 독자: 20~30대 직장인 구독자"
)
SC4 = (
    "아래 블로그 글을 인스타그램 카드뉴스 8장 문구랑 링크드인 글 하나로 바꿔 줘.\n\n"
    "[원문 블로그 글 — 회계법인 바른숫자 「스타트업 첫 부가세 신고 전 확인할 5가지」 요약]\n"
    "1. 사업자등록 후 첫 신고 기간 확인: 일반과세자는 1월·7월 확정신고(법인은 4·7·10·1월)\n"
    "2. 매입 세금계산서 누락 확인: 홈택스에서 조회\n"
    "3. 사업용 카드 등록: 홈택스에 등록해야 매입세액 공제 내역 조회가 쉬움\n"
    "4. 공제 안 되는 매입: 접대비, 비영업용 소형승용차 관련 등\n"
    "5. 기한 후 신고 가산세: 신고·납부 지연 시 가산세 발생\n"
    "- 글쓴이: 바른숫자 김도윤 세무사"
)


def test_calculation_words_in_pasted_material_do_not_make_a_writing_request_a_calculation():
    assert not calculation_policy.requires_calculation(HM2)
    assert not calculation_policy.requires_calculation(SC4)
    # Under a 「---」 line too.
    assert not calculation_policy.requires_calculation(
        "제목 10개 써 줘\n---\n환급액 계산: 4,000,000원 - 3,200,000원")


def test_the_instruction_still_asks_for_a_calculation_over_pasted_operands():
    assert calculation_policy.requires_calculation(
        "다음 값으로 합계 계산해 줘\n\n- 단가 3000원\n- 수량 4개")
    assert calculation_policy.requires_calculation("단가 3000원\n수량 4개\n합계는 얼마야?")
    assert calculation_policy.requires_calculation(
        "자료\n\n단가 3000원, 수량 4개\n\n위 자료로 합계를 계산해 줘")


# ── claims: findings by span, fixed by code ──────────────────────────────


def test_copy_check_is_a_post_check_skill_entering_at_claims():
    package = skill_packages.package_for("copy-check")
    assert package.stage == "post" and package.entry == "claims"
    assert skill_scripts.entry("copy-check") is claims
    assert "resources/judge.md" in package.files


MATERIAL = "로스터리 온결. 매장 직접 로스팅. 원두 200g 12,000원. 무료 체험 14일."


def test_ungrounded_numbers_offers_and_qualifiers_are_fixed_by_code():
    text = ("매일 원두를 로스팅합니다. 원두 200g 15,000원.\n"
            "| 1 | 무료 상담 예약 | 13 |\n"
            "14일 무료 체험 신청하기")
    found = claims.check(text, MATERIAL)
    kinds = {(f["kind"], f["text"], f["action"]) for f in found}
    assert ("qualifier", "매일", "remove") in kinds
    assert ("number", "15,000원", "blank") in kinds
    assert ("offer", "무료 상담", "blank") in kinds
    # Grounded in the material: the 14-day trial stays.
    assert not any(f["line"] == 2 for f in found)
    fixed = claims.fix(text, found)
    assert fixed.splitlines()[0] == "원두를 로스팅합니다. 원두 200g [수치]."
    assert "| [혜택] 예약 |" in fixed and "14일 무료 체험 신청하기" in fixed
    assert claims.check(fixed, MATERIAL) == []


def test_risky_claims_and_judged_statements_go_to_the_writer_and_code_drops_what_is_left():
    text = "성수동 골목의 카페입니다. 업계 최고의 향을 드립니다.\n1. 업계 최고 로스터리"
    judged = [{"unsupported": [{"quote": "성수동 골목의 카페입니다", "why": "위치 없음"},
                               {"quote": "자료에 없는 문장", "why": "없음"}]}]
    found = claims.check(text, MATERIAL, judged)
    assert {(f["kind"], f["action"]) for f in found} == {
        ("superlative", "rewrite"), ("claim", "rewrite")}
    # A quote not in the answer finds nothing.
    assert not any(f["text"] == "자료에 없는 문장" for f in found)
    # A prose line loses the sentences; a one-sentence list item goes whole.
    assert claims.fix(text, found) == ""
    assert claims.fix(text + "\n2. 로스터리 온결", found) == "2. 로스터리 온결"
    assert claims.summary(found).startswith("근거 없는 표현 3개")


def test_a_table_row_left_with_only_its_keys_goes_and_a_fuller_row_keeps_a_blank():
    text = ("| 증거 | 12 | 실제 도입 사례로 보는 검수 | 18 |\n"
            "| 1 | 문제 | 세무사가 직접 갑니다 | 앱으로 증빙 사진만 보내면 세무사가 처리합니다 |")
    judged = [{"unsupported": [{"quote": "실제 도입 사례로 보는 검수"},
                               {"quote": "세무사가 직접 갑니다"}]}]
    assert claims.fix(text, claims.check(text, MATERIAL, judged)) == (
        "| 1 | 문제 | [확인 필요] | 앱으로 증빙 사진만 보내면 세무사가 처리합니다 |")


def test_mentions_spans_years_and_reviews_are_not_claims():
    # Named in a note, not claimed.
    assert claims.check("「당일 지급」「즉시 지급」으로 바꾸지 마세요.", "다음 영업일 지급") == []
    # A webinar from 2 to 3 o'clock is an hour long.
    assert claims.check("1시간 웨비나, 오후 2시 시작", "일시: 11월 12일 오후 2시~3시") == []
    # A year is checked by a person, not blanked; a review post is a 후기.
    assert claims.check("2026년 신메뉴, 2주 사용 후기", "신메뉴 2주 사용") == []


def test_disease_wording_is_a_block_for_a_health_food():
    assert claims.category_of("데일리밸런스 프로바이오틱스 30포") == "health_food"
    found = claims.check("면역력을 확 올려 줍니다.", "데일리밸런스 프로바이오틱스 30포")
    assert [(f["kind"], f["action"]) for f in found] == [("medical", "rewrite")]


# ── length: the channel a request names ──────────────────────────────────


def test_a_request_names_its_channel_limits_or_its_own():
    keys = {r["key"]: r for r in length.targets("세무온 네이버 파워링크 광고 소재 5세트")}
    assert keys["naver_powerlink.title"]["limit"] == 15
    assert keys["naver_powerlink.description"]["min"] == 20
    rows = length.targets("11월 뉴스레터 메일 제목 후보 15개 줘. 30자 안으로.")
    assert [(r["key"], r["limit"]) for r in rows] == [("stated.item", 30)]
    rsa = {r["key"] for r in length.targets("구글 반응형 검색광고 소재")}
    assert "google_rsa.headline" in rsa
    assert length.targets("카페 소개 문구 써 줘") == []


def test_measured_counts_replace_the_answers_own():
    rows = length.measured(
        [{"key": "naver_powerlink.title", "text": "담당 세무사가 직접 기장합니다"},
         {"key": "unknown.field", "text": "x"}],
        length.targets("파워링크"))
    assert len(rows) == 1 and rows[0]["count"] == 16 and rows[0]["over"] == 1
    text = "| 2 | 담당 세무사가 직접 기장합니다 | 14 |\n- 담당 세무사가 직접 기장합니다 (13자)"
    assert length.restate_counts(text, rows) == (
        "| 2 | 담당 세무사가 직접 기장합니다 | 16 |\n- 담당 세무사가 직접 기장합니다 (16자)")
    # A fragment of a cell leaves the cell's count alone.
    part = [{"text": "150,000원", "count": 9}]
    assert length.restate_counts("| 9 | 서버 5대까지 월 150,000원 | 26 |", part) == (
        "| 9 | 서버 5대까지 월 150,000원 | 26 |")


# ── the stage: judge, code fixes, one rewrite, check again ───────────────


class _Model:
    def __init__(self, judged: list[dict], rewrite: str) -> None:
        self.judged, self.rewrite, self.prompts = list(judged), rewrite, []

    async def __call__(self, model, messages, api_key, max_tokens):
        prompt = messages[0]["content"]
        self.prompts.append((model, prompt))
        if prompt.startswith("You check marketing copy"):
            return json.dumps(self.judged.pop(0), ensure_ascii=False), {"inputTokens": 10}
        return self.rewrite, {"outputTokens": 5}


def test_run_fixes_by_code_rewrites_once_and_checks_again():
    request = "세무온 네이버 파워링크 광고 제목 2개 만들어 줘."
    material = "세무온: 기장료 월 99,000원부터, VAT 별도. 첫 달 기장료 무료."
    answer = ("1. 세무온, 업계 1위 기장 대행 서비스 (14자)\n"
              "2. 월 89,000원 기장 (10자)\n"
              "세무사가 매일 연락드립니다.")
    model = _Model(
        [{"unsupported": [{"quote": "세무사가 매일 연락드립니다", "why": "연락 빈도 없음"}],
          "items": [{"key": "naver_powerlink.title", "text": "세무온, 업계 1위 기장 대행 서비스"},
                    {"key": "naver_powerlink.title", "text": "월 89,000원 기장"}]},
         {"unsupported": [],
          "items": [{"key": "naver_powerlink.title", "text": "세무온 기장 대행"}]}],
        "1. 세무온 기장 대행 (14자)\n2. 월 [수치] 기장 (10자)")
    usage: dict = {}
    out = asyncio.run(copy_check.run(answer, request=request, material=material, complete=model,
                                     model="writer", judge="judge", api_key="k", usage=usage))
    assert [m for m, _ in model.prompts] == ["judge", "writer", "judge"]
    rewrite = model.prompts[1][1]
    # The writer sees what code left for it: the ranking, the statement, the piece over 15.
    assert "업계 1위" in rewrite and "세무사가 매일 연락드립니다" in rewrite
    assert "make it at most 15 (cut at least 5)" in rewrite
    # The number was blanked by code before the rewrite.
    assert "월 [수치] 기장" in rewrite and "89,000" not in rewrite
    assert out.rewritten and out.left == []
    assert out.text == "1. 세무온 기장 대행 (9자)\n2. 월 [수치] 기장 (10자)"
    assert {f["kind"] for f in out.found} >= {"superlative", "number", "claim"}
    assert out.over and out.over[0]["count"] == 20
    assert "고쳐 쓰기 1회" in out.detail
    assert usage == {"inputTokens": 20, "outputTokens": 5}


def test_a_clean_answer_costs_one_judge_call_and_stays():
    model = _Model([{"unsupported": [], "items": []}], "")
    out = asyncio.run(copy_check.run("세무온, 첫 달 기장료 무료.", request="광고 문구 써 줘",
                                     material="세무온 첫 달 기장료 무료", complete=model,
                                     model="w", judge="j", api_key="k"))
    assert out.text == "세무온, 첫 달 기장료 무료." and not out.changed and len(model.prompts) == 1


def _bundle(answer: str, **request) -> Bundle:
    return Bundle(request=Request(text="카페 소개 문구 써 줘", model="w", api_key="k", **request),
                  material=Material(given="매장 직접 로스팅"),
                  document=Document(streamed=answer, answer=answer))


def test_the_stage_runs_only_with_a_copy_skill_on_the_turn():
    stage = registry.stage("check:copy")
    assert stage.skip(_bundle("x"))
    assert not stage.skip(_bundle("x", skill_keys=("copywriting",)))
    assert not stage.skip(_bundle("x", check_keys=("copy-check",)))
    body = skill_packages.package_for("social-content").body
    assert not stage.skip(_bundle("x", tool_outputs=["# 스킬 지침\n\n" + body]))
    assert "check:copy" in registry.pipeline("chat").flow("finish")


def test_the_stage_records_its_step(monkeypatch):
    from app.services import report

    model = _Model([{"unsupported": [{"quote": "매일 새벽", "why": "빈도 없음"}], "items": []},
                    {"unsupported": [], "items": []}], "매장에서 직접 로스팅합니다.")
    monkeypatch.setattr(report, "_complete", model)
    bundle = _bundle("매장에서 매일 새벽 직접 로스팅합니다.", skill_keys=("copywriting",))

    async def go():
        return [e async for e in registry.stage("check:copy").run(bundle, Context())]

    events = asyncio.run(go())
    assert bundle.document.answer == "매장에서 직접 로스팅합니다."
    step = bundle.document.kept_steps[-1]
    assert step["id"] == "copy_check" and step["status"] == "done"
    assert step["label"].startswith("카피 점검: 근거 없는 표현")
    assert events[0]["status"] == "running"


def test_a_content_plan_keeps_its_numbers_and_ranks_and_still_loses_invented_facts():
    material = "상담 질문: 연차 촉진(41회), 전자 근로계약(28회)"
    row = "| 1위 | 연차 촉진 | 2주차 | 8.5점 |"
    text = f"{row}\n법상 50인 이상 사업장만 의무입니다. 연차 촉진은 상담 1위입니다."
    judged = [{"unsupported": [{"quote": "법상 50인 이상 사업장만 의무입니다", "why": "법령"}]}]
    plan = claims.check(text, material, judged, {"kinds": ["claim", "medical", "guarantee"]})
    assert [f["kind"] for f in plan] == ["claim"]
    assert claims.fix(text, plan) == f"{row}\n연차 촉진은 상담 1위입니다."
    # Ordinals are not claims in any copy.
    assert not any(f["text"].startswith("2주") for f in claims.check(text, material))



def test_a_judged_quote_the_material_states_and_a_note_about_the_copy_are_left_alone():
    material = "바로정산: 2026년 3월 출시. 매장 직접 로스팅"
    judged = [{"unsupported": [{"quote": "2026년 3월 출시했습니다"},
                               {"quote": "골목 안쪽에 있습니다"}]}]
    found = claims.check("2026년 3월 출시했습니다. 골목 안쪽에 있습니다.", material, judged)
    assert [f["text"] for f in found] == ["골목 안쪽에 있습니다"]
    # A note on what to add later is not an offer; the call to action is.
    text = "무료 체험·상담 예약 등 혜택이 있으면 CTA에 넣으세요. 무료 상담 예약하기."
    assert [f["text"] for f in claims.check(text, "x")] == ["무료 상담"]


def test_table_cells_under_a_field_header_are_measured_by_code():
    text = ("| # | 제목 | 글자 | 설명 | 글자 |\n|---|---|---:|---|---:|\n"
            "| 1 | 시큐로그 접속 기록 관리 | 11 | 접속 기록을 자동 수집합니다. | 9 |\n"
            "| 2 | **개인정보 처리 시스템 접속 기록 관리** | 15 | 이상 접속을 알립니다. | 3 |")
    rows = length.targets("구글 반응형 검색광고")
    measured = length.measured(length.table_items(text, rows), rows)
    assert [(r["field"], r["count"], r["over"]) for r in measured] == [
        ("headline", 23, 0), ("description", 28, 0), ("headline", 35, 5), ("description", 21, 0)]
    restated = length.restate_counts(text, measured)
    assert "| 23 |" in restated and "** | 35 |" in restated and "| 28 |" in restated
