"""With a roster attached, the people an answer lists come from it."""

from pathlib import Path

from app.services import check_skills, files, skill_scripts

_SHEET = Path(__file__).parent / "fixtures" / "payroll" / "근로장학생_근무현황.xlsx"
_REQUEST = ("근로장학생 근무현황 엑셀이에요. 학생별로 9월 근무시간 합계 내 주시고, 시간당 11,000원 "
            "곱해서 지급액도 계산해 주세요. 이상한 행(중복, 학번 오류, 날짜 누락 등)은 "
            "따로 표시해 주세요.")
_MATERIAL = f"{_REQUEST}\n{files._from_xlsx(_SHEET.read_bytes())}"

#: An answer with names invented (김민준, 최지우, 이하늘, 정하늘, 오유진) and a real
#: name beside another student's 학번 (박서연 at 이준호's 32211876).
_ANSWER = """학생별 9월 근무시간 합계와 지급액

| 학번 | 성명 | 9월 근무시간 합계 |
|---|---|---|
| 32221045 | 김민준 | 12 |
| 32211876 | 박서연 | 11.5 |
| 32231102 | 최지우 | 8 |
| 32201533 | 이하늘 | 28 |
| 32220781 | 정하늘 | 8 |
| 32241290 | 오유진 | 8 |
| 합계 | | 75.5 |

이상 행

- 박서연(32211876): 9/1 날짜에 연도가 없음
- 한지민(32241290): 근무일자 누락
"""

_RIGHT = """| 학번 | 성명 | 9월 근무시간 합계 |
|---|---|---|
| 32221045 | 김민지 | 12 |
| 32211876 | 이준호 | 11.5 |
| 32231102 | 박서연 | 8 |
| 32201533 | 최하늘 | 28 |

- 정우성(3222O781): 학번에 영문 O가 섞임
- **한지민** — 근무일자 누락
"""


def _trace():
    return skill_scripts.entry("grounding-check")


def test_invented_and_swapped_names_are_blanked():
    found = _trace().check(_ANSWER, _MATERIAL, [], {})
    people = [f["text"] for f in found if f["kind"] == "person"]
    assert people == ["김민준", "박서연", "최지우", "이하늘", "정하늘", "오유진", "박서연"]
    fixed = _trace().fix(_ANSWER, found)
    assert "| 32221045 | [이름] | 12 |" in fixed
    assert "- [이름](32211876)" in fixed
    assert "- 한지민(32241290)" in fixed
    assert "합계" in fixed


def test_the_roster_s_own_names_stand():
    found = _trace().check(_RIGHT, _MATERIAL, [], {})
    assert [f for f in found if f["kind"] == "person"] == []


def test_a_name_the_person_typed_stands():
    answer = "| 학번 | 성명 |\n|---|---|\n| 32221045 | 김민지 |\n| 32299999 | 홍길동 |\n"
    found = _trace().check(answer, f"{_MATERIAL}\n홍길동 학생도 32299999로 추가해 주세요.", [], {})
    assert [f for f in found if f["kind"] == "person"] == []


def test_no_roster_no_name_check():
    answer = "| 성명 | 역할 |\n|---|---|\n| 김민준 | 발표 |\n"
    assert _trace().check(answer, "발표 순서를 표로 정리해 주세요.", [], {}) == []
    # Words shaped like names in an ordinary list are not people.
    listed = "- 정리: 32221045 행을 다시 볼 것\n- 이상 행 2건"
    assert [f for f in _trace().check(listed, _MATERIAL, [], {}) if f["kind"] == "person"] == []


def test_the_chat_answer_check_blanks_them():
    traced = check_skills.ground(("grounding-check",), _ANSWER, material=_MATERIAL, computed=[],
                                 options=check_skills.options_for(None, {}, surface="chat"))
    assert "김민준" not in traced.text and "[이름]" in traced.text
    assert "이름" in traced.summary
