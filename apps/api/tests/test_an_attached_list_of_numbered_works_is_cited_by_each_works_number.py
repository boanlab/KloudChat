"""An attached file that is a numbered list of works (abstracts, a bibliography) is one source
per work, so the report cites each by its own number instead of the whole file as 「[1]」.

Rounds 1-3 r1r09, r2r09, r3r09 (both arms): a research-trend report from an attached abstract
collection 「[1] … [6] …」 cited every sentence 「[1]」 — the file was one source — although the
request asked for the material's own numbers, and the reference list held one entry."""

from __future__ import annotations

from app.services import citations

FILE = ("# 이번 요청에 첨부된 파일\n아래는 이미 읽어 둔 자료 본문입니다.\n\n"
        "## heat_island_abstracts.pdf\n[페이지 1]\n"
        "도시 열섬 완화 연구 초록 모음\n"
        "[1] 김도윤 외 (2025). 옥상 녹화의 표면 온도 저감 효과. 국토연구 112. "
        "— 옥상 표면 온도 최대 18℃ 저감.\n"
        "[2] Park, J. (2026). Cool pavement and pedestrian heat stress. Urban Climate 55. "
        "— 체감온도 1.4℃ 저감.\n"
        "[3] 이서진, 「가로수 수관 피복률과 야간 기온」, 환경정책, 2025\n"
        "수관 피복률 10%p 증가 시 야간 기온 0.6℃ 하강.\n")
SOURCES = [{"id": "f1", "ordinal": 1, "title": "도시 열섬 완화 연구 초록 모음",
            "fileName": "heat_island_abstracts.pdf", "attached": True, "origin": "file",
            "publisher": "첨부 파일", "originLabel": "첨부 파일"}]


def test_each_numbered_work_is_a_source_with_the_files_own_number():
    split = citations.split_numbered_works(SOURCES, [FILE])
    assert [s["ordinal"] for s in split] == [1, 2, 3]
    assert [s.get("year") for s in split] == ["2025", "2026", "2025"]
    assert split[1]["author"].startswith("Park")
    texts = citations.source_texts(split, "", [FILE])
    assert "18℃" in texts[1] and "1.4℃" in texts[2] and "0.6℃" in texts[3]
    assert "1.4℃" not in texts[1]


def test_the_works_keep_their_numbers_whatever_is_cited_first():
    split = citations.split_numbered_works(SOURCES, [FILE])
    sections = [{"heading": "동향",
                 "content": "보행자 체감온도는 1.4℃ 낮아졌다[2]. 옥상 녹화는 18℃[1]."}]
    out, kept = citations.cite_order(sections, split)
    assert out == sections and [s["ordinal"] for s in kept] == [1, 2, 3]


def test_a_file_that_is_no_list_of_works_stays_one_source():
    memo = "# 첨부\n\n## memo.txt\n[1] 회의실 예약 규칙\n[2] 주차 등록 안내\n[3] 비품 신청 방법\n"
    one = [{**SOURCES[0], "fileName": "memo.txt"}]
    assert citations.split_numbered_works(one, [memo]) is one


def test_sources_after_the_file_are_renumbered_after_its_works():
    more = SOURCES + [{"id": "p2", "ordinal": 2, "title": "기상청 폭염 통계", "origin": "file"}]
    split = citations.split_numbered_works(more, [FILE])
    assert [(s["ordinal"], s["title"][:6]) for s in split][-1] == (4, "기상청 폭염")
