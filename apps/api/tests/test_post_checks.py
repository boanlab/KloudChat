"""스킬 사후 점검(post_check): 스킬이 켜진 채팅 답을 서버가 그 스킬의 스크립트로 점검해
코드로 고칠 것은 고치고, 나머지는 한 번 고쳐 쓰게 한 뒤 다시 점검한다. 도구 보고 줄은 답에서
빠지고, 단계 기록은 쓴 토큰을 남긴다."""

from __future__ import annotations

import asyncio
import json

from app.pipeline import registry
from app.pipeline.bundle import Bundle, Document, Material, Request
from app.pipeline.runner import Runner
from app.pipeline.stage import Base, Context
from app.services import internal_words, post_checks, skill_packages, skill_scripts

preserve = skill_scripts.module("copy-editing", "preserve")
scan = skill_scripts.module("generic-language-killer", "scan")
comms = skill_scripts.module("internal-comms", "check")
notes = skill_scripts.module("release-notes", "notes")
timeline = skill_scripts.module("eol-message", "timeline")
sources = skill_scripts.module("source-check", "sources")
source_post = skill_scripts.module("source-check", "post")
size = skill_scripts.module("market-sizing", "size")

CHECKED = {
    "copy-editing": ["copy-editing/preserve"],
    "generic-language-killer": ["generic-language-killer/scan"],
    "internal-comms": ["internal-comms/check"],
    "release-notes": ["release-notes/notes"],
    "eol-message": ["eol-message/timeline"],
    "company-research": ["source-check/post"],
    "competitor-analysis": ["source-check/post"],
    "competitive-battlecard": ["source-check/post"],
    "industry-analysis": ["source-check/post"],
    "market-segments": ["source-check/post"],
    "source-check": ["source-check/post"],
    "market-sizing": ["market-sizing/size", "source-check/post"],
}


def test_the_skills_name_their_post_check_scripts_and_each_resolves():
    for key, wanted in CHECKED.items():
        package = skill_packages.package_for(key)
        assert list(package.post_checks) == wanted, key
        assert package.post_judge and package.judge_focus, key
        assert [f"{k}/{s}" for k, s in post_checks.entries(key)] == wanted, key
    assert post_checks.on_turn(("market-sizing", "copywriting")) == ["market-sizing"]
    # A skill picked through `use_skill` is on the turn too.
    body = skill_packages.package_for("eol-message").body
    assert post_checks.on_turn((), ["# 스킬 지침\n\n" + body]) == ["eol-message"]


# ── copy-editing: original vs edited ─────────────────────────────────────

ORIGINAL = ("상담원 연결 대기 시간은 평균 4분 12초로 목표치인 3분 이내를 달성하지 못하였다. "
            "처리 건수는 52건으로 적정 처리량인 45건을 초과하는 수준이다. 비용은 1,200만 원이다.")
EDITED = ("### 수정본\n\n상담원 연결 대기 시간은 평균 4분 12초로, "
          "목표치인 3분 이내를 1분 12초 초과하여 "
          "달성하지 못하였다. 처리 건수는 52건으로, 적정 처리량인 45건을 초과하는 수준이다. "
          "비용은 1200만 원이다.\n\n### 바꾼 곳\n| 1 | a | b | 쉼표 |")
REQUEST = "문장 다듬어 줘. 숫자는 건드리지 마.\n\n" + ORIGINAL


def test_preserve_restores_a_rewritten_value_and_sends_a_new_one_to_the_writer():
    first = preserve.post(EDITED, {"request": REQUEST, "final": False})
    assert "1,200만 원" in first["text"] and "1200만 원" not in first["text"]
    assert [f["kind"] for f in first["fixed"]] == ["format"]
    assert [f["text"] for f in first["rewrite"] if f["kind"] == "added"] == ["1분"]
    # The change log under the edited text is not checked.
    assert "| 1 | a | b | 쉼표 |" in first["text"]
    final = preserve.post(first["text"], {"request": REQUEST, "final": True})
    assert "1분 12초 초과하여" not in final["text"]
    assert "목표치인 3분 이내를 달성하지 못하였다" in final["text"]
    assert final["rewrite"] == []


def test_preserve_leaves_an_answer_without_an_edited_section_alone():
    out = preserve.post("맞춤법은 문제없습니다.", {"request": REQUEST, "final": False})
    assert out == {"text": "맞춤법은 문제없습니다.", "fixed": [], "rewrite": [], "flags": []}


# ── generic-language-killer: only safe rewrites, hedges kept ─────────────


def test_scan_marks_a_plan_hedge_check_and_never_rewrites_it():
    found = scan.scan("4분기에 출시할 예정입니다. 도입이 가능할 것으로 판단됩니다. 검토 중입니다.")
    hedges = [f for f in found if f["category"] == "hedge"]
    assert hedges and all(f["action"] == "check" and not f["auto"] for f in hedges)
    assert scan.check("4분기에 출시할 예정입니다.", "") == []


def test_scan_post_sends_back_a_hedge_made_definite_and_applies_safe_fixes():
    original = "신규 기능은 4분기 출시 예정이며, 도입이 가능할 것으로 판단됩니다."
    request = "상투어 빼 줘\n\n" + original
    answer = ("## 고친 글\n신규 기능은 4분기에 출시합니다. 도입을 추진함에 "
              "있어서 문제가 없습니다.\n\n"
              "## 바꾼 곳\n- 예정 → 출시")
    first = scan.post(answer, {"request": request, "final": False})
    assert "추진할 때" in first["text"]
    assert {f["text"] for f in first["rewrite"]} >= {"예정", "가능할 것으로"}
    final = scan.post(first["text"], {"request": request, "final": True})
    assert final["rewrite"] == [] and {f["kind"] for f in final["flags"]} == {"hedge"}
    # The answer's own notes under 「바꾼 곳」 are not the edited text.
    assert "- 예정 → 출시" in final["text"]


# ── internal-comms, release-notes, eol-message: facts the material lacks ──

MEMO = ("[인사팀 메모] 2026-11-02(월)부터 새 HR 시스템(hr.example.kr)으로 신청. "
        "문의: 인사팀 내선 2231")


def test_a_notice_loses_a_date_and_a_contact_the_memo_does_not_give():
    text = "11월 2일(월)부터 새 시스템을 씁니다. 11월 9일(월) 설명회가 있습니다. 문의 02-123-4567."
    out = comms.post(text, {"material": MEMO, "today": "2026-10-08", "final": False})
    assert "11월 2일(월)" in out["text"]
    assert "[날짜](월) 설명회" in out["text"] or "[날짜] 설명회" in out["text"]
    assert "[연락처]" in out["text"]
    wrong = comms.post("11월 2일(화)부터 씁니다.", {"material": MEMO, "today": "2026-10-08"})
    assert "11월 2일(월)" in wrong["text"]


def test_release_notes_blank_a_date_the_list_does_not_give_and_keep_a_year_less_one():
    log = "앱 4.2.0, 10월 27일 배포\n- 품절 상품 한 번에 지우기 버튼"
    text = "# 오늘장보기 4.2.0 (2026. 10. 27.)\n## 새 기능\n- 품절 상품을 한 번에 지우는 버튼"
    assert notes.post(text, {"material": log, "final": False})["fixed"] == []
    other = notes.post(text.replace("10. 27.", "10. 9."), {"material": log, "final": False})
    assert "[배포일]" in other["text"] and other["fixed"][0]["kind"] == "date"
    made_up = "## 개선\n- 결제 화면이 40% 빨라졌습니다"
    first = notes.post(made_up, {"material": log, "final": False})
    assert [f["kind"] for f in first["rewrite"]][:1] == ["number"]
    assert "[수치]" in notes.post(made_up, {"material": log, "final": True})["text"]


def test_timeline_fix_is_a_question_not_a_date():
    result = timeline.check({"announce": "2026-12-01", "end": "2026-12-31"},
                            [{"from": "announce", "to": "end", "days": 90}])
    gap = next(f for f in result["findings"] if f["type"] == "gap")
    assert "2026" not in gap["fix"] and "정해 주세요" in gap["fix"]
    assert gap["question"].endswith("?")


def test_eol_post_blanks_an_invented_date_and_asks_for_it():
    material = "[결정] 공지: 2026-10-15\n서비스 종료: 2026-12-31\n데이터 삭제: 2027-02-28"
    text = ("| 2026. 10. 15.(목) | 공지 |\n| 2026. 12. 31.(목) | 서비스 종료 |\n"
            "| 2027. 1. 15.(금) | 환불 신청 마감 |\n\n신규 가입 중단일을 "
            "언제로 할까요? (예: 2026. 11. 1.)")
    out = timeline.post(text, {"material": material, "today": "2026-10-08"})
    assert "2027. 1. 15." not in out["text"] and "| [날짜] | 환불 신청 마감 |" in out["text"]
    assert "확인이 필요한 날짜" in out["text"]
    # A question's example date is the question's, not a fact.
    assert "(예: 2026. 11. 1.)" in out["text"]
    relative = timeline.post("종료일은 2026. 11. 30.입니다.",
                             {"material": "다음 달 말 종료", "today": "2026-10-08"})
    assert relative["fixed"] == []


# ── source-check: generalities, citations the source does not hold ───────

PACK = ("[자료 1] 한국HR테크협회 「2025 HR SaaS 시장 보고서」: 2024년 5,200억원, 연평균 성장률 8%\n"
        "[자료 2] 자사 고객 인터뷰(15곳): 제조 6곳 중 5곳 \"교대 근무 근태를 엑셀로 맞춤\"")
ANSWER = ("시장은 연 8% 성장 중이며 경쟁이 치열하다 [1].\n"
          "제조업은 공장장·경영진·IT 다중 승인 구조다 [2].\n"
          "도입률은 23%다 [1].\n\n## 출처\n[1] 한국HR테크협회, 「2025 "
          "HR SaaS 시장 보고서」, 2025-03-10\n"
          "[2] 자사 고객 인터뷰, 2026-08")


def test_sources_keeps_a_cited_generality_and_checks_the_citation_against_its_source():
    found = sources.check(ANSWER, PACK, {"today": "2026-10-08"})
    kinds = {(f["kind"], f.get("line")) for f in found}
    assert ("generic", 0) in kinds
    assert ("cite_mismatch", 1) in kinds and ("cite_mismatch", 2) in kinds
    # The growth figure is the source's.
    assert not any(f["kind"] == "cite_mismatch" and f["line"] == 0 for f in found)
    fixed = sources.fix_cites(ANSWER, [f for f in found if f["kind"] == "cite_mismatch"])
    assert "도입률은 23%다 「출처 미확인」." in fixed


def test_the_source_check_post_marks_numbers_and_retracts_a_citation_on_the_final_pass():
    text = "국내 사업체는 4,200곳이다.\n" + ANSWER
    first = source_post.post(text, {"material": PACK, "today": "2026-10-08"})
    assert "국내 사업체는 4,200곳이다. 「출처 미확인」" in first["text"]
    assert {f["kind"] for f in first["rewrite"]} == {"cite_mismatch", "generic"}
    final = source_post.post(first["text"], {"material": PACK, "today": "2026-10-08",
                                             "final": True})
    assert "구조다 「출처 미확인」." in final["text"]
    assert final["rewrite"] == [] and any(f["kind"] == "generic" for f in final["flags"])


# ── market-sizing: the written calculation worked again ──────────────────

SIZING = """## 결과
| 구분 | 상향식 |
|---|---:|
| SOM | 5.4억원 |

## 계산식
SOM(상향식) = 첫해 고객 수 1,500가구 × 연 객단가 36만원 = 5.4억원

## 가정과 출처
| 번호 | 항목 | 값 | 구분 | 근거·출처 |
|---:|---|---:|---|---|
| 3 | 연 객단가 | 36만원 | 가정 | 월 3만원 × 12개월 |
| 5 | 첫해 고객 수 | 1,500가구 | 가정 | 마케팅 예산 3억원 ÷ 고객 1가구 획득 비용 200만원 |

## 민감도
| 흔든 값 | SOM 범위 |
|---|---|
| 고객 수 | 4.3 ~ 6.5 |"""


def test_size_post_recomputes_the_basis_and_the_equation_and_carries_the_result():
    out = size.post(SIZING, {})
    text = out["text"]
    assert "| 5 | 첫해 고객 수 | 150가구 |" in text
    assert "첫해 고객 수 150가구 × 연 객단가 36만원 = 5,400만원" in text
    assert "| SOM | 5,400만원 |" in text
    # The row number and the right value are left as they are; the sensitivity table is flagged.
    assert "| 3 | 연 객단가 | 36만원 |" in text and "| 고객 수 | 4.3 ~ 6.5 |" in text
    assert [f["kind"] for f in out["flags"]] == ["sensitivity"]
    assert "※ 계산을 다시 해 고쳤습니다" in text
    chained = size.post("12만 곳 × 50% × 2% = 0.01곳, 1,200 × 336만 원 = 40억 3,200만 원.", {})
    assert chained["text"].startswith("12만 곳 × 50% × 2% = 1,200곳, 1,200 × "
                                      "336만 원 = 40억 3,200만 원.")
    assert size.post("TAM = 5,200 × (1 + 0.08)² = 6,065.28", {})["fixed"] == []


# ── the server's run: code, one rewrite, code again ──────────────────────


class _Model:
    def __init__(self, judged: list[dict], rewrite: str, evidence: list[dict] | None = None):
        self.judged, self.rewrite, self.prompts = judged, rewrite, []
        self.evidence = evidence or []

    async def __call__(self, model, messages, api_key, max_tokens):
        prompt = messages[0]["content"]
        self.prompts.append((model, prompt))
        if prompt.startswith("You check an answer"):
            return json.dumps({"unsupported": self.judged}, ensure_ascii=False), {
                "inputTokens": 10, "outputTokens": 2}
        if prompt.startswith("Below are the material an answer was written from and statements"):
            return json.dumps({"verdicts": self.evidence}, ensure_ascii=False), {
                "inputTokens": 5, "outputTokens": 1}
        return self.rewrite, {"inputTokens": 20, "outputTokens": 8}


def test_run_fixes_by_code_rewrites_once_and_drops_what_the_writer_left():
    memo = "[메모] 2026-11-02(월)부터 새 시스템. 문의: 인사팀 내선 2231"
    answer = ("11월 2일(월)부터 새 시스템을 씁니다. 11월 9일(월) 설명회가 있습니다.\n"
              "기존 신청분은 자동으로 옮겨집니다. 문의: 인사팀 내선 2231")
    model = _Model([{"quote": "기존 신청분은 자동으로 옮겨집니다", "why": "자료에 없음"}],
                   "11월 2일(월)부터 새 시스템을 씁니다. [날짜](월) 설명회가 있습니다.\n"
                   "기존 신청분은 자동으로 옮겨집니다. 문의: 인사팀 내선 2231")
    usage: dict = {}
    out = asyncio.run(post_checks.run(
        answer, keys=["internal-comms"], request="공지 써 줘", material=memo, attached="",
        computed=[], today="2026-10-08", complete=model, model="w", judge="j", api_key="k",
        usage=usage))
    assert "자동으로 옮겨집니다" not in out.text and "11월 9일" not in out.text
    assert out.rewritten and [m for m, _ in model.prompts] == ["j", "j", "w"]
    assert any(f["kind"] == "date" for f in out.fixed)
    assert out.label.startswith("스킬 점검: 코드로 고침")
    assert usage == {"inputTokens": 35, "outputTokens": 11}


def test_run_with_nothing_to_fix_asks_only_the_judge():
    model = _Model([], "")
    out = asyncio.run(post_checks.run(
        "11월 2일부터 씁니다.", keys=["internal-comms"], request="공지", material="11월 2일부터",
        attached="", computed=[], today="2026-10-08", complete=model, model="w", judge="j",
        api_key="k"))
    assert out.text == "11월 2일부터 씁니다." and not out.changed and len(model.prompts) == 1
    assert out.label == "스킬 점검: 고칠 것 없음"


def test_a_judged_statement_the_material_states_in_its_own_words_is_kept():
    memo = "[메모] 템플릿 폴더 기능 추가. 2026-11-02 배포"
    answer = "## 새 기능\n- 템플릿을 폴더로 묶어 관리할 수 있습니다.\n- 결제가 3배 빨라졌습니다."
    model = _Model([{"quote": "템플릿을 폴더로 묶어 관리할 수 있습니다", "why": "x"},
                    {"quote": "결제가 3배 빨라졌습니다", "why": "없음"}],
                   answer, evidence=[{"n": 1, "evidence": "템플릿 폴더 기능 추가"},
                                     {"n": 2, "evidence": "결제 속도 3배 개선"}])
    out = asyncio.run(post_checks.run(
        answer, keys=["release-notes"], request="릴리스 노트", material=memo, attached="",
        computed=[], today="2026-10-08", complete=model, model="w", judge="j", api_key="k"))
    # The first quote's evidence is the memo's own words; the second's is made up.
    assert "템플릿을 폴더로 묶어 관리할 수 있습니다." in out.text
    assert "3배" not in out.text


def test_the_judge_reply_is_read_through_a_model_s_broken_json_and_skips_non_statements():
    broken = ('{\n"unsupported": [\n{\n"quote": "자동 결제는 12월 31일에 중단됩니다.",\n'
              '"why": "자료에 없음.\n"\n},\n'
              '{\n"quote": "오터 대상 고객 규모",\n"why": "x"\n}\n]\n}')

    async def complete(model, messages, api_key, max_tokens):
        return broken, {}

    text = "자동 결제는 12월 31일에 중단됩니다.\n## 확인할 것\n- 오터 대상 고객 규모"
    found = asyncio.run(post_checks._judge(text, "자료", complete=complete, judge="j",
                                           api_key="k", usage=None))
    assert [f["text"] for f in found] == ["자동 결제는 12월 31일에 중단됩니다."]


def test_drop_takes_out_a_sentence_a_cell_or_a_line():
    assert post_checks.drop("가. 나는 틀렸다. 다.", "나는 틀렸다") == "가. 다."
    assert post_checks.drop("| a | 나는 틀렸다 |", "나는 틀렸다") == "| a | 확인 필요 |"
    assert post_checks.drop("- 나는 틀렸다\n- 다", "나는 틀렸다") == "- 다"


def _bundle(answer: str, **request) -> Bundle:
    return Bundle(request=Request(text="공지 써 줘", model="w", api_key="k", **request),
                  material=Material(given="[메모] 2026-11-02(월)부터"),
                  document=Document(streamed=answer, answer=answer))


def test_the_stage_runs_in_the_chat_finish_with_a_post_checked_skill_only(monkeypatch):
    from app.services import report

    stage = registry.stage("check:skills")
    for name in ("chat", "data-analysis", "career-docs"):
        flow = registry.pipeline(name).flow("finish")
        assert flow.index("check:copy") < flow.index("check:skills") < flow.index("answer-replace")
    assert stage.skip(_bundle("x"))
    assert stage.skip(_bundle("x", skill_keys=("copywriting",)))
    assert not stage.skip(_bundle("x", skill_keys=("internal-comms",)))
    monkeypatch.setattr(report, "_complete", _Model([], "11월 2일(월)부터 씁니다."))
    bundle = _bundle("11월 3일(화)부터 씁니다.", skill_keys=("internal-comms",),
                     today="2026-10-08")

    async def go():
        return [e async for e in stage.run(bundle, Context())]

    events = asyncio.run(go())
    # an unlisted date stays as written, marked; the guard leaves no 「[날짜]」 blank
    assert bundle.document.answer == "11월 3일(화)(확인 필요)부터 씁니다."
    step = bundle.document.kept_steps[-1]
    assert step["id"] == "skill_check" and events[0]["status"] == "running"


# ── tool reports never reach the answer ──────────────────────────────────


def test_strip_reports_takes_out_script_lines_and_keeps_the_answer():
    text = ("공지문입니다.\n\n점검 결과: `block` 0건, `check` 1건, `info` 0건.\n"
            "calculate: 171/312*100 = 54.807692 → 54.8%\n"
            "교정·윤문 preserve 검사에서 「In」이 사라졌다고 나옵니다.\n"
            "- 글자 수는 스크립트로 검증했고, 모두 한도 안입니다.\n"
            "최종 점검 결과: 출처 점검: 문제 없음\n"
            "검증: `claims` 0건.\n"
            "- 버전이 미정이므로 스크립트가 형식 오류를 냈지만, 헤더에 [버전]으로 "
            "두었습니다. 정해지면 바꿔 주세요.\n"
            '{"lost": [], "added": []}\n'
            "| 추진함에 있어서 | 추진할 때 | 스크립트 auto 교체 |\n"
            "```json\n{\"keep\": 1}\n```\n### 점검 결과\n맞춤법 지적 2개 반영")
    out = internal_words.strip_reports(text)
    assert out.startswith("공지문입니다.")
    for gone in ("`block`", "calculate:", "스크립트", '"lost"', "preserve"):
        assert gone not in out.split("```")[0]
    assert "- 정해지면 바꿔 주세요." in out
    assert "| 추진함에 있어서 | 추진할 때 | 자동 교체 |" in out
    assert '```json\n{"keep": 1}\n```' in out and "### 점검 결과\n맞춤법 지적 2개 반영" in out


def test_the_normalize_stage_strips_reports_only_when_skills_were_on():
    report_line = "답입니다.\n출처 점검 최종 결과: 문제 없음 (출처 없는 숫자 0)"

    def run(**request) -> str:
        bundle = _bundle(report_line, **request)

        async def go():
            await registry.stage("answer-normalize").apply(bundle, Context())

        asyncio.run(go())
        return bundle.document.answer

    assert run(skill_keys=("source-check",)) == "답입니다."
    assert "최종 결과" in run()


# ── stage records carry the tokens each stage spent ──────────────────────


class _Spend(Base):
    def __init__(self, name: str, spent: dict) -> None:
        self.name, self.spent = name, spent

    async def apply(self, bundle, ctx):
        for key, value in self.spent.items():
            bundle.usage[key] = bundle.usage.get(key, 0) + value


def test_each_stage_record_carries_the_tokens_it_spent():
    bundle = Bundle(request=Request(text="x", model="m", api_key="k"),
                    usage={"inputTokens": 5, "outputTokens": 1})
    runner = Runner([_Spend("plan", {"outlineInputTokens": 30, "outlineOutputTokens": 4}),
                     _Spend("write", {"inputTokens": 100, "outputTokens": 40}),
                     _Spend("noop", {})], emit_records=False)

    async def go():
        return [e async for e in runner.run(bundle)]

    asyncio.run(go())
    assert [r.tokens for r in bundle.steps] == [
        {"outlineInputTokens": 30, "outlineOutputTokens": 4},
        {"inputTokens": 100, "outputTokens": 40}, {}]
    assert bundle.steps[1].stage_wire()["tokens"] == {"inputTokens": 100, "outputTokens": 40}
    assert "tokens" not in bundle.steps[2].stage_wire()
    again = type(bundle.steps[0]).from_dict(bundle.steps[0].as_dict())
    assert again.tokens == {"outlineInputTokens": 30, "outlineOutputTokens": 4}


def test_a_particle_after_a_blank_agrees_with_it():
    assert post_checks.particles("공지일을 [날짜]으로 정하고 [연락처]을 넣고 [수치]이 오면") == (
        "공지일을 [날짜]로 정하고 [연락처]를 넣고 [수치]가 오면")
    assert post_checks.particles("[날짜]이며 [금액]을") == "[날짜]이며 [금액]을"


def test_the_source_check_post_leaves_worked_values_and_checklists_unmarked():
    text = ("## 계산식\nSAM = TAM 1,000억원 × 수도권 비중 45% = 450억원\n\n"
            "## 결과\n| SAM | 약 450억원 |\n\n"
            "## 확인할 것\n- 수도권 비중 45%: 통계청 자료로 확인\n\n"
            "## 전망\n시장은 연 30% 성장한다.")
    out = source_post.post(text, {"material": "TAM 1,000억원 [자료 1]", "today": "2026-10-08"})
    marked = [f["text"] for f in out["fixed"]]
    assert marked == ["시장은 연 30% 성장한다."]


def test_release_notes_drop_a_code_sample_the_list_does_not_hold_and_keep_years():
    log = "PAY-301 결제 취소 API에 부분 취소 금액 파라미터 cancel_amount 추가"
    text = ("## 새 기능\n- 부분 취소를 지원합니다.\n\n  ```\n  POST /v1/payments/{id}/cancel\n"
            '  {"cancel_amount": 5000}\n  ```\n\n## 개선\n- 2026년 하반기 기준입니다.')
    first = notes.post(text, {"material": log, "final": False})
    assert "code" in [f["kind"] for f in first["rewrite"]]
    final = notes.post(text, {"material": log, "final": True})
    assert "POST /v1" not in final["text"] and "\n  \n" not in final["text"]
    assert "2026년 하반기" in final["text"]


def test_eol_keeps_a_worked_legal_deadline_but_not_a_worked_schedule_date():
    material = "[메모] 종료 예정 2027-03-02. 공지 예정 2027-01-05."
    computed = ["legal announce_by 2027-01-01, report_by 2027-02-15"]
    text = ("| 공지 | 2027. 1. 1.(금) |\n| 종료 | 2027. 3. 2.(화) |\n\n"
            "신고 기한은 2027. 2. 15.(월)입니다(시행령 제33조).\n"
            "삭제는 2031. 3. 2.에 합니다.")
    out = timeline.post(text, {"material": material, "computed": computed,
                               "today": "2026-10-08"})
    assert "| 공지 | [날짜] |" in out["text"] and "| 종료 | 2027. 3. 2.(화) |" in out["text"]
    assert "2027. 2. 15.(월)" in out["text"] and "2031. 3. 2." not in out["text"]


def test_the_judge_is_told_what_the_skill_looks_at():
    model = _Model([], "")
    asyncio.run(post_checks.run(
        "자동 결제는 12월 31일에 중단됩니다.", keys=["eol-message"], request="종료 안내",
        material="종료 2026-12-31", attached="", computed=[], today="2026-10-08",
        complete=model, model="w", judge="j", api_key="k"))
    assert "Look in particular at: billing and automatic payments" in model.prompts[0][1]


def test_a_comparison_table_keeps_its_own_categories_and_worked_rates_unmarked():
    material = "[자료 1] 다글로 1인 월 9,900원\n[자료 2] 매출 1,284억원(2025), 1,102억원(2024)"
    text = ("| 경쟁사 | 구분 | 고른 이유 |\n|---|---|---|\n"
            "| 다글로 | 직접 | 1인 월 9,900원 [1] |\n\n"
            "## 리스크\n- 매출은 16.5% 늘었다.\n- 점유율은 23%다.")
    out = source_post.post(text, {"material": material, "skills": ["competitor-analysis"]})
    assert "| 다글로 | 직접 |" in out["text"]
    assert "- 매출은 16.5% 늘었다.\n" in out["text"]
    assert "점유율은 23%다. 「출처 미확인」" in out["text"]


# ── generic-language-killer attached to the agents: safe rewrites only ───

AGENT_KEYS = ("minutes", "gaejo-report", "client-proposal", "comparison-report", "career-docs")


def test_the_prose_agents_attach_the_cliche_check_after_grounding():
    from app.services import check_skills

    for key in AGENT_KEYS:
        keys = check_skills.attached(agent_key=key)
        assert keys.index("grounding-check") < keys.index("generic-language-killer"), key
    assert "generic-language-killer" not in check_skills.attached(agent_key="data-analysis")


def test_attached_it_only_rewrites_safely_and_keeps_numbers_names_and_hedges():
    from app.services import check_skills

    sections = [{"heading": "결론", "content": (
        "김민수 팀장은 2026. 11. 2.까지 예산 1억 2천만 원 확보를 추진함에 있어서 "
        "3개 지점 확대가 가능할 것으로 판단되며, 4분기 출시 예정입니다. 매우 중요한 일입니다.")}]
    out, outcome = check_skills.ground_sections(
        ("generic-language-killer",), sections, material="", computed=[], options={})
    text = out[0]["content"]
    assert "추진할 때" in text and "매우 " not in text and outcome.found
    for kept in ("김민수 팀장", "2026. 11. 2.", "1억 2천만 원", "3개 지점",
                 "가능할 것으로 판단되며", "출시 예정입니다"):
        assert kept in text, kept


def test_an_attached_cliche_check_does_not_also_run_the_judged_post_check():
    stage = registry.stage("check:skills")
    assert stage.skip(_bundle("x", check_keys=("grounding-check", "generic-language-killer")))
    assert not stage.skip(_bundle("x", skill_keys=("generic-language-killer",)))


def test_size_post_leaves_a_unit_conversion_alone_and_flags_a_unit_slip():
    text = ("- SOM = 900곳 × 40명 × 7,000원 × 12개월 ÷ 1억 = 30.24억원\n"
            "- TAM = 12만 곳 × 40명 × 7,000원 × 12개월 = 4,032원")
    out = size.post(text, {})
    assert out["text"] == text and out["fixed"] == []
    assert [f["kind"] for f in out["flags"]] == ["unit"]


def test_a_claim_marked_as_our_own_material_must_be_in_it():
    material = ("[자사 자료] 노트핏(우리): 1인 월 12,000원(VAT 별도). 국내 리전 저장.\n"
                "[자료 5] 고객 인터뷰(자사, 12곳): 슬랙/잔디 연동은 경쟁사 페이지에 언급 없음")
    text = ("| 연동 | 슬랙·잔디 연동 지원(자사 자료) |\n| 저장 | 국내 리전 저장(자사 자료) |\n"
            "| 가격 | VAT 포함 13,200원(자사 자료) |\n| 연동 | 노트핏 연동 지원 여부(자사 자료) |")
    found = [f for f in sources.check(text, material, {}) if f["kind"] == "cite_mismatch"]
    assert [f["line"] for f in found] == [0]


def test_a_pretty_printed_tool_result_is_taken_out_of_the_answer():
    text = ('[실행 결과: run_skill_script]\n{\n  "status": "ok",\n  "findings": [\n    {\n'
            '      "kind": "unsourced"\n    }\n  ]\n}\n고친 초안입니다.\n- 목록 [1]')
    assert internal_words.strip_reports(text) == "고친 초안입니다.\n- 목록 [1]"


def test_size_post_skips_prose_around_a_calculation():
    text = "객단가: 평균 7,000원으로 잡고, 평균 50명 사용 × 12개월 = 420만원"
    assert size.post(text, {})["fixed"] == []


def test_a_judged_sentence_the_person_wrote_is_never_taken_out():
    original = "다음 주 중에 한번 뵙겠습니다."
    answer = "### 수정본\n다음 주 중에 한번 뵙겠습니다.\n자료를 받으면 바로 보내 드리겠습니다."
    model = _Model([{"quote": "다음 주 중에 한번 뵙겠습니다", "why": "x"},
                    {"quote": "자료를 받으면 바로 보내 드리겠습니다", "why": "없음"}], answer)
    out = asyncio.run(post_checks.run(
        answer, keys=["copy-editing"], request="다듬어 줘\n\n" + original, material=original,
        attached="", computed=[], today="2026-10-08", complete=model, model="w", judge="j",
        api_key="k"))
    assert "한번 뵙겠습니다" in out.text and "바로 보내 드리겠습니다" not in out.text
