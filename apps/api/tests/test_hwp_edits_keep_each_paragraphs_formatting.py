"""HWP edit ops change text and keep each paragraph's own shapes; a bad op changes nothing."""

from __future__ import annotations

import io
import zipfile

import pytest
from lxml import etree

from app.services import hwp_doc, hwpx_edit

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"

BODY = """1. 관련: [관련 근거]
2. 아래와 같이 연수를 실시하오니 참석하여 주시기 바랍니다.
  가. 연수명: [연수명]
  나. 일시: [YYYY. MM. DD.]
  다. 장소: [장소]

| 구분 | 내용 |
|---|---|
| 연수명 | [연수명] |
| 대상 | [참석 대상] |"""


def official() -> bytes:
    return hwp_doc.official("직원 연수 참석 요청", BODY)


def section(data: bytes) -> etree._Element:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return etree.fromstring(archive.read("Contents/section0.xml"))


def paragraph_with(data: bytes, text: str) -> etree._Element:
    for p in section(data).iter(f"{HP}p"):
        if "".join(p.itertext()).startswith(text):
            return p
    raise AssertionError(text)


def shapes(p: etree._Element) -> tuple[str, list[str]]:
    return p.get("paraPrIDRef"), [r.get("charPrIDRef") for r in p.iterchildren(f"{HP}run")]


def test_the_outline_numbers_paragraphs_and_tables_and_lists_the_blanks():
    text = hwpx_edit.outline(official())
    assert "t1 (표 " in text and "  r0: 수신 | [수신]" in text
    assert "가. 연수명: [연수명]" in text
    blanks_line = text.splitlines()[-1]
    assert blanks_line.startswith("빈칸: ")
    for blank in ("[수신]", "[관련 근거]", "[연수명]", "[YYYY. MM. DD.]", "[참석 대상]"):
        assert blank in blanks_line
    # one blank, every place it stands
    assert "[연수명] (p" in blanks_line and "t2 r1c1" in blanks_line


def test_filling_a_blank_and_replacing_a_line_keep_their_shapes():
    data = official()
    before_item = shapes(paragraph_with(data, "나. 일시"))
    pid = next(
        line.split(":")[0]
        for line in hwpx_edit.outline(data).splitlines()
        if "나. 일시" in line
    )
    result = hwpx_edit.apply(data, [
        {"op": "fill_blank", "blank": "[연수명]", "value": "교원연수"},
        {"op": "replace_text", "id": pid, "text": "나. 일시: 2027. 2. 11.(목) 10:00~12:00"},
        {"op": "set_cell", "table": "t1", "row": 0, "col": 1, "text": "전 교직원"},
    ])
    assert result.changes[0] == "[연수명] → 「교원연수」 (2곳)"
    after = result.data
    assert shapes(paragraph_with(after, "나. 일시: 2027. 2. 11.")) == before_item
    assert shapes(paragraph_with(after, "가. 연수명: 교원연수")) == shapes(
        paragraph_with(data, "가. 연수명: [연수명]"))
    left = hwpx_edit.blanks(after)
    assert "[연수명]" not in left and "[YYYY. MM. DD.]" not in left and "[장소]" in left
    assert "r0: 수신 | 전 교직원" in hwpx_edit.outline(after)


def test_an_inserted_paragraph_takes_its_neighbours_shapes_and_rows_can_be_added():
    data = official()
    pid = next(
        line.split(":")[0] for line in hwpx_edit.outline(data).splitlines() if "다. 장소" in line
    )
    result = hwpx_edit.apply(data, [
        {"op": "insert_paragraph", "after": pid, "text": "라. 참석 대상: 전체 교직원"},
        {"op": "insert_table_row", "table": "t2", "after": 2},
        {"op": "delete_table_row", "table": "t2", "row": 1},
    ])
    after = result.data
    assert shapes(paragraph_with(after, "라. 참석 대상")) == shapes(
        paragraph_with(data, "다. 장소"))
    table = [t for t in section(after).iter(f"{HP}tbl")][1]
    assert table.get("rowCnt") == "3"
    rows = [[a.get("rowAddr") for a in tr.iter(f"{HP}cellAddr")] for tr in table.iter(f"{HP}tr")]
    assert rows == [["0", "0"], ["1", "1"], ["2", "2"]]
    assert "  r2:  | " in hwpx_edit.outline(after)
    # still a package the editor opens: mimetype first and stored
    with zipfile.ZipFile(io.BytesIO(after)) as archive:
        first = archive.infolist()[0]
        assert first.filename == "mimetype" and first.compress_type == zipfile.ZIP_STORED


@pytest.mark.parametrize(
    "bad",
    [
        {"op": "replace_text", "id": "p999", "text": "x"},
        {"op": "fill_blank", "blank": "[없는 빈칸]", "value": "x"},
        {"op": "fill_blank", "blank": "연수명", "value": "x"},
        {"op": "set_cell", "table": "t1", "row": 9, "col": 0, "text": "x"},
        {"op": "delete_paragraph", "id": "p1"},
        {"op": "rewrite_everything"},
        {"op": "replace_text", "id": "p2", "text": 5},
    ],
)
def test_one_bad_op_leaves_the_whole_edit_undone(bad):
    with pytest.raises(hwpx_edit.HwpxError):
        hwpx_edit.apply(official(), [{"op": "fill_blank", "blank": "[장소]", "value": "본관"},
                                     bad])


def test_a_blank_page_is_one_body_paragraph():
    data = hwp_doc.blank()
    assert hwp_doc.file_format(data) == "hwpx"
    assert hwpx_edit.outline(data).splitlines() == ["p1: (빈 줄)", "빈칸: 없음"]
    first = section(data).find(f"{HP}p")
    assert shapes(first) == ("3", ["0"])


def test_an_unknown_file_is_neither_hwp_nor_hwpx():
    assert hwp_doc.file_format(b"%PDF-1.7") == ""
    assert hwp_doc.file_format(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 8) == "hwp"
    with pytest.raises(hwpx_edit.HwpxError):
        hwpx_edit.outline(b"PK\x03\x04broken")


@pytest.mark.parametrize(
    ("line", "value", "after"),
    [
        ("[연수명]을 실시하오니", "교원연수", "교원연수를 실시하오니"),
        ("[연수명]을 실시하오니", "직무연수 과정", "직무연수 과정을 실시하오니"),
        ("[장소]로 오십시오", "본관", "본관으로 오십시오"),
        ("[장소]로 오십시오", "중앙홀", "중앙홀로 오십시오"),
        ("[담당자]가 안내합니다", "김민지", "김민지가 안내합니다"),
        ("[일시]에 시작", "2027. 2. 11.", "2027. 2. 11.에 시작"),
    ],
)
def test_a_filled_blank_takes_the_particle_its_value_needs(line, value, after):
    data = hwp_doc.plain("제목", line)
    blank = line[: line.index("]") + 1]
    result = hwpx_edit.apply(data, [{"op": "fill_blank", "blank": blank, "value": value}])
    assert after in hwpx_edit.plain_text(result.data)


def test_a_blank_that_stands_for_two_things_is_filled_in_one_place():
    data = hwp_doc.official("연수", "1. 일시: [YYYY. MM. DD.]", {"시행일": "[YYYY. MM. DD.]"})
    outline = hwpx_edit.outline(data)
    # a blank given as a head value is not a value: the head keeps its own named blank
    assert "r2: 시행일 | [시행일]" in outline
    data = hwp_doc.official("연수", "1. 일시: [YYYY. MM. DD.]\n2. 회신: [YYYY. MM. DD.]까지")
    pid = next(line.split(":")[0] for line in hwpx_edit.outline(data).splitlines()
               if "1. 일시" in line)
    result = hwpx_edit.apply(data, [{"op": "fill_blank", "blank": "[YYYY. MM. DD.]",
                                     "value": "2027. 2. 11.", "at": pid}])
    text = hwpx_edit.plain_text(result.data)
    assert "1. 일시: 2027. 2. 11." in text and "2. 회신: [YYYY. MM. DD.]까지" in text
    assert result.changes == [f"{pid}: [YYYY. MM. DD.] → 「2027. 2. 11.」 (1곳)"]
    in_cell = hwpx_edit.apply(data, [{"op": "fill_blank", "blank": "[수신]", "value": "각 부서",
                                      "at": "t1 r0c1"}])
    assert "수신 | 각 부서" in hwpx_edit.outline(in_cell.data)
    with pytest.raises(hwpx_edit.HwpxError):
        hwpx_edit.apply(data, [{"op": "fill_blank", "blank": "[수신]", "value": "x", "at": pid}])
