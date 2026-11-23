"""Charts from a report's tables: parsed by code, values as the table has them."""

import types

import pytest

from app.services import diagrams, illustrations, settings_store, table_charts

RESULT = """설명 문단.

**표 2. 결과**

| 연도 | 불량률 (%) | 설비 가동률(OEE) (%) | 비고 |
|---|---|---|---|
| 2021 | 6.0 | 60.0 | 도입 전 기준선 |
| 2022 | 5.0 | 65.0 | 기초 수준 도입 |
| 2023 | 4.0 | 70.0 | 데이터 수집 확대 |
| 2024 | 3.0 | 75.0 | 모니터링 체계 구축 |
| 2025 | 2.0 | 80.0 | 예시 목표치 |

해석 문단. 본 분석에서 사용된 수치는 예시 값이며 실제 현장 데이터가 아니다.
"""


def test_a_year_table_becomes_a_trend_with_a_panel_per_unit_scale():
    [chart] = table_charts.find(RESULT)
    assert chart.kind == "line"
    assert chart.labels == ("2021", "2022", "2023", "2024", "2025")
    # 6 and 60 are not drawn on one axis: two charts side by side, nothing retyped
    assert [[s.name for s in panel] for panel in chart.panels] == [
        ["불량률"], ["설비 가동률(OEE)"]]
    assert chart.panels[0][0].values == (6.0, 5.0, 4.0, 3.0, 2.0)
    assert chart.panels[1][0].unit == "%"
    assert chart.caption == "연도별 불량률·설비 가동률(OEE) 추이 (예시)"


def test_a_chart_goes_right_after_its_table():
    [chart] = table_charts.find(RESULT)
    out = table_charts.insert(RESULT, chart, "![c](data:image/png;base64,AA)")
    rows = out.split("\n")
    at = rows.index("![c](data:image/png;base64,AA)")
    assert rows[at - 2].startswith("| 2025")
    assert out.index("해석 문단") > out.index("data:image")


def test_a_category_table_becomes_bars():
    table = ("| 제품 | 매출(억 원) | 점유율(%) |\n|---|---|---|\n| 제품 A | 120 | 40 |\n"
             "| 제품 B | 90 | 30 |\n| 제품 C | 60 | 20 |\n| 합계 | 270 | 90 |\n")
    [chart] = table_charts.find(table)
    assert chart.kind == "bar"
    assert chart.labels == ("제품 A", "제품 B", "제품 C")  # the total row is no category
    assert [p[0].unit for p in chart.panels] == ["억 원", "%"]
    assert chart.caption == "제품별 매출·점유율 비교"


def test_a_cell_with_a_comma_percent_and_note_is_read_as_its_number():
    assert table_charts.parse_cell("5.2%(확인 필요) (예시)") == (5.2, "%", "5.2")
    assert table_charts.parse_cell("1,200억 원") == (1200.0, "억 원", "1200")
    assert table_charts.parse_cell("약 3.5") == (3.5, "", "3.5")
    assert table_charts.parse_cell("도입 전") is None


def test_a_table_of_years_across_the_top_is_read_sideways():
    table = ("| 지표 | 2021년 | 2022년 | 2023년 |\n|---|---|---|---|\n"
             "| 매출 (억 원) | 10 | 12 | 15 |\n| 비용 (억 원) | 8 | 9 | 10 |\n")
    [chart] = table_charts.find(table)
    assert chart.kind == "line" and chart.labels == ("2021년", "2022년", "2023년")
    assert [s.name for s in chart.panels[0]] == ["매출", "비용"]


def test_a_small_or_wordy_table_gets_no_chart():
    two_rows = "| 연도 | 값 |\n|---|---|\n| 2021 | 1 |\n| 2022 | 2 |\n"
    two_points = ("| 항목 | 2021년 | 2025년 |\n|---|---|---|\n| OEE | 68% | 74% |\n"
                  "| 불량률 | 5% | 2% |\n")
    words = "| 단계 | 설명 |\n|---|---|\n| 수집 | 센서 |\n| 정제 | 결측 |\n| 산출 | 계산 |\n"
    mixed = "| 항목 | 값 |\n|---|---|\n| 투자 | 3억 원 |\n| 인원 | 5명 |\n| 기간 | 6개월 |\n"
    for table in (two_rows, two_points, words, mixed):
        assert table_charts.find(table) == []


def test_a_table_is_charted_once_per_report():
    taken: set[str] = set()
    assert len(table_charts.find(RESULT, taken=taken)) == 1
    assert table_charts.find(RESULT, taken=taken) == []


def test_a_table_that_already_has_a_figure_after_it_is_left_alone():
    done = RESULT.replace("해석 문단", "![그림](data:image/png;base64,AA)\n\n해석 문단")
    assert table_charts.find(done) == []


def test_a_chart_is_not_marked_example_when_nothing_says_so():
    real = RESULT.replace("본 분석에서 사용된 수치는 예시 값이며 실제 현장 데이터가 아니다.", "")
    [chart] = table_charts.find(real.replace("예시 목표치", "목표치"))
    assert "예시" not in chart.caption


def test_a_chart_script_carries_the_table_values_and_runs_as_python():
    [chart] = table_charts.find(RESULT)
    code = table_charts.code_for(chart)
    compile(code, "chart", "exec")
    assert "NanumGothic" in code and "6.0" in code and "80.0" in code


def test_a_request_declining_charts_is_read():
    assert table_charts.declined("그래프 없이 써 줘")
    assert not table_charts.declined("결과에는 그림을 넣어 줘")


STEPS_LIST = """그림 3과 같다.

```steps
데이터 수집 | IoT 센서와 MES에서 데이터를 모은다
정제 | 중복·결측·이상치를 걸러낸다
지표 산출 | OEE와 불량률을 계산한다
ROI 분석 | 투자 회수기간을 산출한다
시각화 | 표와 그림으로 정리한다
```
"""

FLOW = """flowchart LR
  a["데이터 수집"] --> b["전송"] --> c["정제·통합"] --> d["지표 산출"] --> e["결과 보고"]"""


def test_a_flow_repeating_the_steps_listed_beside_it_is_found():
    made = {"figure": "flow", "source": FLOW}
    assert diagrams.repeated_list(made, STEPS_LIST) is not None


def test_a_flow_of_other_things_than_the_list_is_kept():
    other = 'flowchart LR\n  a["고객"] --> b["주문"] --> c["배송"] --> d["정산"]'
    assert diagrams.repeated_list({"figure": "flow", "source": other}, STEPS_LIST) is None
    assert diagrams.repeated_list({"figure": "flow", "source": FLOW}, "목록 없는 글") is None


def test_a_numbered_list_is_a_step_list():
    text = "앞 글\n\n1. 수집한다\n2. 정제한다\n3. 산출한다\n\n뒷 글"
    assert [(a, b) for a, b, _ in diagrams.listed_steps(text)] == [(2, 5)]


def test_a_figure_request_is_no_request_for_illustrations():
    ask = "결과 절 다섯 장으로 방법과 결과에는 그림을 넣어 줘"
    assert not illustrations.wanted(ask)
    assert illustrations.wanted("표지 그림을 넣어 줘")
    assert illustrations.wanted("삽화를 곁들여 줘")
    assert not illustrations.wanted("삽화는 필요 없어")


def test_a_flow_repeating_a_list_is_left_out_unless_figures_were_asked_for():
    from app.pipeline import report_stages

    section = {"content": STEPS_LIST + "\n뒷 글"}
    made = {"figure": "flow", "source": FLOW}
    assert not report_stages._figure_says_more(made, [section], asked=False, own=section)
    # asked for figures: the figure and the list both stay
    assert report_stages._figure_says_more(made, [section], asked=True, own=section)
    assert "```steps" in section["content"]


@pytest.mark.asyncio
async def test_a_chart_goes_under_the_table_and_a_plan_without_tables_gets_none(monkeypatch):
    from app.pipeline import report_stages
    from app.pipeline.bundle import Bundle, Request
    from app.services import chart_code

    async def render(code):
        assert "2025" in code
        return b"\x89PNG-fake"

    async def backends():
        return types.SimpleNamespace(exec="http://sandbox")

    monkeypatch.setattr(chart_code, "render", render)
    monkeypatch.setattr(settings_store, "tools_config", backends)
    bundle = Bundle(request=Request(text="보고서 써 줘", model="m", api_key="k"))
    sections = [{"id": "a", "heading": "결과", "content": RESULT},
                {"id": "b", "heading": "기획", "content": "표 없는 글입니다.\n\n- 하나\n- 둘"}]
    events = [e async for e in report_stages._chart_tables(bundle, sections, "ko", False)]
    assert [e["sectionId"] for e in events if e["type"] == "section"] == ["a"]
    assert "![연도별 불량률·설비 가동률(OEE) 추이 (예시)](data:image/png;base64," in sections[0][
        "content"]
    assert sections[1]["content"] == "표 없는 글입니다.\n\n- 하나\n- 둘"
    # a second pass finds the figure and draws nothing more
    again = [e async for e in report_stages._chart_tables(bundle, sections, "ko", False)]
    assert not any(e["type"] == "section" for e in again)


@pytest.mark.asyncio
async def test_a_request_without_charts_gets_none(monkeypatch):
    from app.pipeline import report_stages
    from app.pipeline.bundle import Bundle, Request

    bundle = Bundle(request=Request(text="그래프 없이 써 줘", model="m", api_key="k"))
    sections = [{"id": "a", "heading": "결과", "content": RESULT}]
    assert [e async for e in report_stages._chart_tables(bundle, sections, "ko", False)] == []


@pytest.mark.asyncio
async def test_a_report_without_a_code_sandbox_announces_no_chart(monkeypatch):
    from app.pipeline import report_stages
    from app.pipeline.bundle import Bundle, Request

    async def backends():
        return types.SimpleNamespace(exec="")

    monkeypatch.setattr(settings_store, "tools_config", backends)
    bundle = Bundle(request=Request(text="보고서 써 줘", model="m", api_key="k"))
    sections = [{"id": "a", "heading": "결과", "content": RESULT}]
    assert [e async for e in report_stages._chart_tables(bundle, sections, "ko", False)] == []
    assert sections[0]["content"] == RESULT
