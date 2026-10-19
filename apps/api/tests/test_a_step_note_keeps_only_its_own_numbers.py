"""A step note keeps a count only when the material says it of that step."""

from app.services import figure_kinds

_WHOLE = "공사는 여름 휴가 3주 동안 철거·배선과 조명·가구 설치·시범 운영 4단계로 한다."


def _steps(*notes: tuple[str, str]) -> dict:
    return {"kind": "steps", "steps": [{"name": n, "note": t} for n, t in notes]}


def test_a_count_said_of_the_whole_is_cut_from_one_step():
    s = figure_kinds.grounded(_steps(
        ("철거·배선", "기존 가구 철거"), ("조명 교체", "조명 교체와 배선 시공"),
        ("가구 설치", "2주간 가구 반입"), ("시범 운영", "3주간 시범 운영")), _WHOLE)
    notes = {p["name"]: p.get("note") for p in s["steps"]}
    assert notes["시범 운영"] is None  # only the name was left
    assert notes["가구 설치"] == "가구 반입"  # 2주 is said nowhere
    assert notes["철거·배선"] == "기존 가구 철거"


def test_a_count_said_of_its_own_step_stays():
    material = "철거는 1주, 설치는 2주가 걸린다. 시범 운영 1주 동안 의견을 받는다."
    s = figure_kinds.grounded(_steps(
        ("철거", "1주간 철거"), ("설치", "2주간 가구·조명 설치"),
        ("시범 운영", "1주 동안 의견 수렴")), material)
    assert [p["note"] for p in s["steps"]] == ["1주간 철거", "2주간 가구·조명 설치",
                                              "1주 동안 의견 수렴"]


def test_figures_without_material_or_steps_are_left_alone():
    s = _steps(("가", "3주간 진행"), ("나", "다음"), ("다", "끝"))
    assert figure_kinds.grounded(s, "")["steps"][0]["note"] == "3주간 진행"
    house = {"kind": "house", "goal": "3주 완공", "pillars": ["가", "나"], "base": []}
    assert figure_kinds.grounded(house, _WHOLE) == house
