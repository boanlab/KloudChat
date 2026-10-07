"""Late-pipeline guards: a deck in another language, a deck pasted into a note, a settled
figure off by a power of ten, a per-unit formula, a run of months cut short."""

from app.services import deck, quality_gate, units
from app.services import key_figures as kf

FIGURES = [kf.Figure("박스당 판매가", "20,000원", ["판매가"]),
           kf.Figure("박스당 변동비", "8,000원", ["변동비"]),
           kf.Figure("월 고정비", "3,000만 원", ["고정비"]),
           kf.Figure("목표 판매량", "4,000박스", [])]
FIGURES += kf.derived(FIGURES)


def test_a_slide_written_in_english_for_a_korean_request_is_found():
    english = {"title": "활동", "layout": "myth-fact", "notes": "Work with a partner to test each "
               "claim against the examples and ask whether the joined atoms are the same kind.",
               "columns": [{"title": "Claim", "items": ["Joined atoms always mean a compound"]}]}
    korean = {"title": "AI 보안 동향", "notes": "EDR·XDR·SIEM 제품과 MITRE ATT&CK 기준을 "
              "비교합니다. CrowdStrike와 SentinelOne은 국내 공급사를 통해 들어옵니다.",
              "bullets": ["LLM 게이트웨이와 DLP 연동", "SOC 자동화"]}
    request = "중학교 2학년 원소와 화합물 수업 슬라이드를 만들어 줘."
    assert deck.foreign_slides([korean, english], request) == [1]
    assert deck.foreign_slides([english], "Make a lesson deck about elements.") == []


def test_the_deck_pasted_after_a_note_is_cut_off():
    slides = [{"title": "정리", "notes": "최종 판단은 사람 몫입니다. # AI 에이전트 보안 ## 1장. "
               "표지 *발표 노트** 오늘은 … ```mermaid flowchart LR A --> B```"}]
    assert deck.strip_json_residue(slides) == [0]
    assert slides[0]["notes"] == "최종 판단은 사람 몫입니다."


def test_a_settled_figure_off_by_a_power_of_ten_is_a_conflict():
    slipped = "박스 하나는 변동비 8,000만원을 쓰고 공헌이익 12,000원을 남깁니다."
    total = "2,000박스분 변동비 1,600만 원이 듭니다."
    assert [c[1].name for c in kf.conflicts(slipped, FIGURES)] == ["박스당 변동비"]
    assert kf.conflicts(total, FIGURES) == []
    kpi = "2,500박스 | 손익분기 판매량\n50만 원 | 손익분기 매출"
    assert [c[1].name for c in kf.conflicts(kpi, FIGURES)] == ["손익분기 매출"]


def test_a_per_unit_formula_is_worked_out():
    assert units.slipped_formulas("매출은 ‘4,000박스/월 × 20,000원/박스 = 8,000원/월’입니다.")
    right = "매출은 4,000박스/월 × 20,000원/박스 = 8,000만원/월입니다."
    assert not units.slipped_formulas(right)


def test_a_run_of_months_the_deck_drops_is_named():
    material = ("| 1개월 차 | 파일럿 |\n| 2개월 차 | 확대 |\n"
                "| 3개월 차 | 광고 |\n| 4개월 차 | 제휴 |")
    assert quality_gate.missing_sequence(material, "1개월 차 파일럿, 2개월 차 확대") == [
        ("개월 차", ["3 개월 차", "4 개월 차"])]
    assert quality_gate.missing_sequence("3월 5일, 4월 2일, 9월 1일 회의", "3월") == []


QUIZ = ("**4.** (2차시) 이산화 탄소(CO₂) 분자를 옳게 설명한 것은?\n"
        "① 탄소 원자 2개와 산소 원자 1개로 이루어져 있다. ② 탄소와 산소가 아무 비율로 섞인 "
        "혼합물이다. ③ 탄소 원자 1개와 산소 원자 2개로 이루어져 있다. ④ 산소 원자 1개와 "
        "탄소 원자 1개로 이루어져 있다.\n\n**5.** (2차시) 철가루와 황가루를 섞어 만든 혼합물에 "
        "대한 설명으로 옳은 것은?\n① 성분 비율이 항상 일정하다. ② 각 성분의 성질이 그대로 남아 "
        "있다. ③ 화학식 하나로 나타낼 수 있다. ④ 두 원소가 결합해 성질이 전혀 다른 물질이 되었다.")


def test_the_restatement_trimming_leaves_a_test_alone():
    """Trimming spares a test item's choices that repeat the body (「원자 2개」)."""
    from app.services.report import trim_restatements

    earlier = ["이산화 탄소는 탄소 원자 1개와 산소 원자 2개로 이루어져 있다. "
               "물은 산소 원자 1개와 수소 원자 2개로 이루어진다."]
    essay = "**서술형 1** (1차시, 6점) 물(H₂O), 산소(O₂)를 분류하고 근거를 쓰시오."
    kept, cut = trim_restatements(QUIZ + "\n\n" + essay, earlier)
    assert cut == [] and kept == QUIZ + "\n\n" + essay


def test_choices_the_document_lost_are_found_and_put_back():
    from app.services.report import restore_choices

    lost = QUIZ.replace(QUIZ.split("\n")[1], "② 탄소와 산소가 아무 비율로 섞인 혼합물이다.")
    assert quality_gate.lost_question_parts(QUIZ, lost) == [
        ("이산화 탄소(CO₂) 분자를 옳게 설명한 것은?", ["보기 ①", "보기 ③", "보기 ④"])]
    assert quality_gate.lost_question_parts(QUIZ, QUIZ) == []
    restored = restore_choices([{"heading": "평가", "content": lost}], QUIZ)
    assert restored[0]["content"] == QUIZ
    assert restore_choices(restored, QUIZ) == restored
    # An answer line naming choices is not a choice line.
    answer = "\n정답 ③. ②는 혼합물로 본 것, ①은 개수를 바꾼 것."
    keyed = QUIZ.replace("\n\n**5.**", answer + "\n\n**5.**")
    assert [q[0] for q in quality_gate.question_choices(keyed)][0].startswith("이산화 탄소")


def test_every_shape_of_a_json_answer_reads_as_its_prose():
    """Sections answered as ```json blocks, as an unclosed {"section": {"number": 7, "body": …,
    or as {"sections": [{"body": …}]} all read as their prose."""
    from app.services.report import final_tidy

    block = '```json\n{{\n  "section": "지도상 유의점",\n  "content": "{}"\n}}\n```'
    fenced = block.format("오개념을 짚습니다.") + "\n\n" + block.format("실험은 교사 시범입니다.")
    nested = ('{\n"section": {\n"number": 7,\n"title": "실행 로드맵",\n'
              '"body": "6개월 로드맵은 두 축입니다.\n\n| 월 | 목표 |\n| --- | --- |\n| 1 | 출시 |')
    listed = ('```json\n{\n "sections": [\n {\n "title": "자금 계획",\n'
              ' "body": "운전자금을 정합니다."\n }\n ]\n}\n```')
    out = final_tidy([{"heading": "a", "content": fenced}, {"heading": "b", "content": nested},
                      {"heading": "c", "content": listed}])
    assert out[0]["content"] == "오개념을 짚습니다.\n\n실험은 교사 시범입니다."
    assert out[1]["content"].startswith("6개월 로드맵은 두 축입니다.")
    assert "| 1 | 출시 |" in out[1]["content"]
    assert out[2]["content"] == "운전자금을 정합니다."
    assert final_tidy(out) == out


def test_a_named_metric_a_thousandfold_off_is_found():
    """「CAC 5,000만원」 is found where the material says 5만 원 throughout."""
    text = ("고객 획득 비용(CAC)은 5만 원으로 둡니다. 파일럿에서 CAC 5만 원 이하를 확인합니다.\n"
            "이는 월 마케팅 예산 1억원과 CAC 5,000만원 기준의 월 신규 가입 추이와 연관됩니다.\n"
            "누적 CAC 3,000만 원은 6개월 집행 합계입니다.")
    slips = quality_gate.scaled_metric_slips(text)
    assert [(s[1], s[2], s[3]) for s in slips] == [("CAC", 50_000_000, 50_000)]


def test_a_column_in_thousands_holding_won_is_relabelled():
    from app.services.report import relabel_unit_columns

    body = ("파일럿 첫 달 CAC 60,000원 이하를 목표로 합니다.\n\n"
            "| 월 | CAC(천 원) |\n|---|---|\n| 1 | 60,000 이하 |\n| 2 | 55,000 이하 |")
    assert quality_gate.mislabelled_columns(body) == [("CAC(천 원)", "CAC")]
    out = relabel_unit_columns([{"heading": "로드맵", "content": body}])
    assert "| 월 | CAC(원) |" in out[0]["content"]
    assert relabel_unit_columns(out) == out
    right = body.replace("60,000 이하 |\n| 2 | 55,000", "60 이하 |\n| 2 | 55")
    assert quality_gate.mislabelled_columns(right) == []


def test_lesson_rows_that_do_not_fill_the_lesson_are_found():
    plan = ("각 차시는 45분입니다.\n\n| 단계 | 활동 | 시간 |\n|---|---|---|\n"
            "| 도입 | 질문 | 5분 |\n| 전개 | 모형 | 25분 |\n| 정리 | 퀴즈 | 10분 |")
    assert quality_gate.lesson_time_gaps(plan) == [("| 도입 | 질문 | 5분 |", 40, 45)]
    assert quality_gate.lesson_time_gaps(plan.replace("10분", "15분")) == []
