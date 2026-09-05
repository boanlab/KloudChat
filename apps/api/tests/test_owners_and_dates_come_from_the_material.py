"""An action-item table names only the people and dates the request or material named."""

from app.services.report import _unsourced_owner_dates

SOURCE = (
    "- 10:31 정우성 롤백 결정\n"
    "재발 방지 표에 「배포 전 재색인 완료 확인」을 넣고, 담당은 김도윤, 기한은 9/10으로. "
    "회의는 2026-09-04."
)

TABLE = """### 재발 방지 대책
| 조치 | 담당 | 기한 |
| :--- | :--- | :--- |
| 재색인 절차 자동화 | 인프라팀 | 9/15 |
| 재시도 횟수 제한 | 개발팀 | 9월 10일 |
| 배포 전 재색인 완료 확인 | 김도윤 | 9/10 |
| 커넥션 풀 경보 | 정우성 (백엔드) | 미정 |
| 스키마 검증 | 미정 | 2026-09-04 |
"""


def test_invented_owners_and_dates_become_undecided() -> None:
    rows = _unsourced_owner_dates(TABLE, SOURCE).splitlines()
    assert rows[3] == "| 재색인 절차 자동화 | 미정 | 미정 |"
    assert rows[4] == "| 재시도 횟수 제한 | 미정 | 9월 10일 |"


def test_named_owners_and_dates_survive_in_any_spelling() -> None:
    rows = _unsourced_owner_dates(TABLE, SOURCE).splitlines()
    # The same day written three ways; a person named inside a longer cell.
    assert rows[5] == "| 배포 전 재색인 완료 확인 | 김도윤 | 9/10 |"
    assert rows[6] == "| 커넥션 풀 경보 | 정우성 (백엔드) | 미정 |"
    assert rows[7] == "| 스키마 검증 | 미정 | 2026-09-04 |"


def test_other_tables_and_prose_are_left_alone() -> None:
    text = "| 시각 | 사건 |\n| :--- | :--- |\n| 10:31 | 롤백 결정 |\n\n담당은 운영팀이 9/30까지."
    assert _unsourced_owner_dates(text, SOURCE) == text


def test_without_material_nothing_is_touched() -> None:
    assert _unsourced_owner_dates(TABLE, "   ") == TABLE
