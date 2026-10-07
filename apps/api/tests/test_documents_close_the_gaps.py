"""What the draft got wrong or left out is set right after it is written."""

import pytest

from app.services import calc, deck, report

COMPUTED = (
    "# 코드로 계산한 값\n설명\n\n"
    "100 Hz | 측정 이득 = 0.990 | 이론 출력 = 1.996 Vpp\n"
    "500 Hz | 측정 이득 = 0.955 | 이론 출력 = 1.908 Vpp\n"
    "1000 Hz | 측정 이득 = 0.845 | 이론 출력 = 1.693 Vpp\n"
    "3000 Hz | 측정 이득 = 0.470 | 이론 출력 = 0.937 Vpp\n"
)


def test_a_table_rebuilt_from_computed_rows_takes_the_computed_values():
    wrong = (
        "결과는 다음과 같다.\n\n| 주파수 | 측정 | 이론 출력 |\n|---|---|---|\n"
        "| 100 | 1.98 | 1.997 |\n| 500 | 1.91 | 1.959 |\n| 1 kHz | 1.69 | 1.789 |\n"
        "| 3 kHz | 0.94 | 0.988 |\n\n고찰은 다음 절에서.\n\n| 구분 | 담당 |\n|---|---|\n"
        "| 측정 | 김 |\n| 정리 | 이 |\n| 발표 | 박 |"
    )
    out, changed = calc.replace_tables([{"heading": "결과", "content": wrong}], COMPUTED)
    text = out[0]["content"]
    assert changed == 1
    assert "| 500 Hz | 0.955 | 1.908 Vpp |" in text and "1.959" not in text
    assert "| 항목 | 측정 이득 | 이론 출력 |" in text
    assert "| 측정 | 김 |" in text and text.startswith("결과는 다음과 같다.")


def test_notes_are_filled_for_cover_agenda_closing_and_empty_slides():
    slides = [
        {"layout": "title", "title": "RC 필터 실험"},
        {"layout": "agenda", "title": "목차", "bullets": ["목적", "이론", "결과"]},
        {"layout": "bullets", "title": "결과", "bullets": ["차단 주파수 1.59 kHz", "오차 0.2 dB"]},
        {"layout": "bullets", "title": "고찰", "bullets": ["프로브"], "notes": "있는 노트."},
        {"layout": "closing", "title": "감사합니다"},
    ]
    changed = deck.fill_missing_notes(slides, "발표 자료 6장, 발표자 노트 포함", subtitle="5분")
    assert changed == [0, 1, 2, 4]
    assert slides[0]["notes"].startswith("안녕하세요. 오늘은 「RC 필터 실험」")
    assert slides[1]["notes"] == "발표는 목적, 이론, 그리고 결과 순서로 진행하겠습니다."
    assert slides[2]["notes"].startswith("이 장에서는 결과를 살펴보겠습니다.")
    assert slides[3]["notes"] == "있는 노트."
    assert deck.fill_missing_notes([{"layout": "title", "title": "x"}], "발표 자료 6장") == []


def test_missing_citations_and_apis_are_asked_for_as_additions():
    body = "식민지 신문은 복합적 공론장을 만들었다. " * 20
    sections = [{"heading": "본론", "content": body}, {"heading": "참고문헌", "content": ""}]
    material = "- 김상현, 『동아일보의 역사』, 동아일보사, 1990년.\n" + "".join(
        f"| POST | /api/items/{i} | 등록 |\n" for i in range(6)
    )
    asks = report.additions_needed(sections, "리포트, 본문 괄호 인용(저자 연도)", material)
    assert asks[0][0] == 0 and "김상현, 『동아일보의 역사』" in asks[0][1]
    assert report.additions_needed(sections, "리포트를 써 줘", "") == []


def test_missing_endpoints_are_added_as_rows_in_code():
    sections = [
        {"heading": "배경", "content": "도서 대여 서비스다."},
        {"heading": "API 설계", "content": "| GET | /api/v1/books | 목록 |"},
        {"heading": "참고문헌", "content": ""},
    ]
    material = "\n".join([
        "GET /api/v1/books — 도서 목록", "GET /api/v1/books/{id} — 도서 상세",
        "POST /api/v1/rentals — 대여 신청", "POST /api/v1/rentals/{id}/return — 반납",
        "POST /api/v1/reservations — 예약", "DELETE /api/v1/reservations/{id} — 예약 취소",
        "- RESTful 동사: GET/POST/PUT/PATCH/DELETE 만으로 표현",
    ])
    out = report.add_missing_endpoints(sections, material)
    api = out[1]["content"]
    assert api.startswith("| GET | /api/v1/books | 목록 |")
    assert "| POST | `/api/v1/rentals/{id}/return` | 반납 |" in api
    assert api.count("| GET | `/api/v1/books` |") == 0 and out[0] == sections[0]
    assert "/POST/PUT" not in api


def test_only_additions_are_accepted():
    before = "신문은 공론장을 만들었다.\n\n| GET | /a | 조회 |"
    table = "\n\n| GET | /a | 조회 |"
    cited = "신문은 공론장을 만들었다 (김상현 1990)." + table + "\n| POST | /b | 등록 |"
    assert report.only_added(before, cited)
    assert not report.only_added(before, "신문은 여론을 이끌었다 (김상현 1990)." + table)


@pytest.mark.asyncio
async def test_unconfirmed_references_are_marked_not_removed(monkeypatch):
    async def verify(titles):
        return {"동아일보의 역사": True, "식민지 조선의 지식인 사회": False}

    monkeypatch.setattr(report.research, "verify_titles", verify)
    sections = [{"heading": "참고문헌", "content": (
        "김상현 (1990). 『동아일보의 역사』. 동아일보사.\n\n"
        "이영훈 (2005). 『식민지 조선의 지식인 사회』. 민음사.\n\n"
        "[3] 통계청. https://kostat.go.kr"
    )}]
    out = (await report._mark_unverified_references(sections))[0]["content"].split("\n\n")
    assert out[0].endswith("동아일보사.")
    assert out[1].endswith("민음사. (검색으로 확인되지 않음)")
    assert out[2] == "[3] 통계청. https://kostat.go.kr"


@pytest.mark.asyncio
async def test_a_report_call_is_retried_once_after_a_timeout(monkeypatch):
    import httpx

    calls = {"n": 0}

    class Response:
        status_code = 200
        text = ""
        headers = {}

        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "본문"}}], "usage": {}}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, *a, **k):
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadTimeout("busy")
            return Response()

    async def config():
        return "http://llm", ""

    async def no_sleep(_):
        return None

    monkeypatch.setattr(report.httpx, "AsyncClient", Client)
    monkeypatch.setattr(report.settings_store, "litellm_config", config)
    monkeypatch.setattr(report.asyncio, "sleep", no_sleep)
    text, _ = await report._complete("m", [{"role": "user", "content": "x"}], "k", 100)
    assert text == "본문" and calls["n"] == 2
