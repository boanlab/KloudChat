"""A 한글 form's own lines (labels, headings, 「고려대학교 총장 귀하」) are held by code: a write
goes after them, a write over them or into a spacer row is left out and said, and every fixed
line is checked again after the edits."""

from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace

import pytest

from app.services import form_marks, form_profile, hwpx_edit, hwpx_form, report_export

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"


def _cell(text: str, r: int, c: int, *, h: int = 2000, cols: int = 1, rows: int = 1) -> str:
    paragraphs = "".join(
        f'<hp:p paraPrIDRef="7" styleIDRef="0"><hp:run charPrIDRef="0"><hp:t>{line}</hp:t>'
        "</hp:run></hp:p>" for line in text.split("\n"))
    return (
        '<hp:tc name="" header="0" hasMargin="1" protect="0" editable="0" dirty="0" '
        'borderFillIDRef="2"><hp:subList id="" textDirection="HORIZONTAL" lineWrap="BREAK" '
        'vertAlign="TOP" linkListIDRef="0" linkListNextIDRef="0" textWidth="0" textHeight="0" '
        f'hasTextRef="0" hasNumRef="0">{paragraphs}</hp:subList>'
        f'<hp:cellAddr colAddr="{c}" rowAddr="{r}"/><hp:cellSpan colSpan="{cols}" '
        f'rowSpan="{rows}"/><hp:cellSz width="20000" height="{h}"/>'
        '<hp:cellMargin left="141" right="141" top="141" bottom="141"/></hp:tc>')


def _form() -> bytes:
    rows = [
        _cell("사업명", 0, 0) + _cell("", 0, 1) + _cell("메모", 0, 2),
        _cell("①상호(단체명)", 1, 0, h=4536, cols=2),
        _cell("", 2, 0, h=0, cols=2),
        _cell("2. 기상사업의 목적\n -", 3, 0, h=9000, cols=2),
        _cell("3. 기상사업의 계획\n가. 사업의 방법\n나. 관련 기술 및 시설", 4, 0, h=9000, cols=2),
        _cell("\n".join(_RECEIPT), 5, 0, h=9000, cols=2),
    ]
    table = (
        '<hp:p paraPrIDRef="3" styleIDRef="0"><hp:run charPrIDRef="0"><hp:tbl id="0" '
        'zOrder="0" numberingType="TABLE" textWrap="TOP_AND_BOTTOM" textFlow="BOTH_SIDES" '
        'lock="0" dropcapstyle="None" pageBreak="CELL" repeatHeader="1" rowCnt="6" colCnt="2" '
        'cellSpacing="0" borderFillIDRef="1" noAdjust="0"><hp:sz width="42520" '
        'widthRelTo="ABSOLUTE" height="4000" heightRelTo="ABSOLUTE" protect="0"/>'
        + "".join(f"<hp:tr>{row}</hp:tr>" for row in rows) + "</hp:tbl></hp:run></hp:p>")
    base = report_export.to_hwpx("서식", [{"heading": "", "content": "x"}])
    source = zipfile.ZipFile(io.BytesIO(base))
    section = source.read("Contents/section0.xml").decode()
    head = section[:section.index("<hp:p ", section.index("</hp:secPr>") - 3000)]
    first = section[section.index("<hp:p "):section.index("</hp:p>") + 7]
    body = (first + table
            + '<hp:p paraPrIDRef="1" styleIDRef="0"><hp:run charPrIDRef="3"><hp:t>'
              "고려대학교 총장 귀하</hp:t></hp:run></hp:p>"
            + '<hp:p paraPrIDRef="1" styleIDRef="0"><hp:run charPrIDRef="3"><hp:t>'
              "      년        월        일</hp:t></hp:run></hp:p>")
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as copy:
        for info in source.infolist():
            body_bytes = source.read(info.filename)
            if info.filename == "Contents/section0.xml":
                body_bytes = (head + body + "</hs:sec>").encode()
            copy.writestr(info, body_bytes)
    return out.getvalue()


def _marked() -> bytes:
    data = _form()
    profile = form_profile._hwpx(data, "서식.hwpx")
    form = SimpleNamespace(data=data, profile=profile, choice=SimpleNamespace(
        name="서식.hwpx", source="attached", file_id="f" * 32))
    return hwpx_form.start(form)


def _text(data: bytes, table: str, row: int, col: int) -> str:
    _, doc = hwpx_edit._load(data)
    cell = hwpx_edit._cell(hwpx_edit._table(doc, table), row, col)
    return hwpx_edit._cell_text(cell)


def test_the_form_keeps_its_fixed_lines_in_its_mark():
    mark = form_marks.read(_marked())
    assert mark["fixed"]["cells"]["t1:0:0"] == ["사업명"]
    assert mark["fixed"]["cells"]["t1:3:0"] == ["2. 기상사업의 목적"]
    assert "고려대학교 총장 귀하" in mark["fixed"]["paras"]


def test_a_value_goes_after_a_label_that_shares_its_cell():
    done = hwpx_edit.apply(_marked(), [
        {"op": "set_cell", "table": "t1", "row": 1, "col": 0, "text": "새벽별소프트"}])
    assert _text(done.data, "t1", 1, 0) == "①상호(단체명)\n새벽별소프트"
    assert any("그 아래에 넣었습니다" in note for note in done.notes)


def test_a_heading_cell_is_written_whole_with_its_headings_kept():
    marked = _marked()
    done = hwpx_edit.apply(marked, [{
        "op": "set_cell", "table": "t1", "row": 3, "col": 0, "text": "클라우드 사업"}])
    assert _text(done.data, "t1", 3, 0).split("\n")[0] == "2. 기상사업의 목적"
    assert "클라우드 사업" in _text(done.data, "t1", 3, 0)
    # two headings: the writer must carry them, or the edit is left out and says why
    with pytest.raises(hwpx_edit.HwpxError, match="그 줄을 그대로 두고"):
        hwpx_edit.apply(marked, [{
            "op": "set_cell", "table": "t1", "row": 4, "col": 0, "text": "방법 내용"}])
    done = hwpx_edit.apply(marked, [{
        "op": "set_cell", "table": "t1", "row": 4, "col": 0,
        "text": "3. 기상사업의 계획\n가. 사업의 방법\n방법 내용\n나. 관련 기술 및 시설\n"
                "시설 내용"}])
    assert _text(done.data, "t1", 4, 0).split("\n") == [
        "3. 기상사업의 계획", "가. 사업의 방법", "방법 내용", "나. 관련 기술 및 시설", "시설 내용"]


def test_a_spacer_row_is_never_written_and_the_rest_of_the_batch_still_is():
    done = hwpx_edit.apply(_marked(), [
        {"op": "set_cell", "table": "t1", "row": 2, "col": 0, "text": "여기에"},
        {"op": "set_cell", "table": "t1", "row": 0, "col": 1, "text": "클라우드 사업"}])
    assert _text(done.data, "t1", 2, 0) == ""
    assert _text(done.data, "t1", 0, 1) == "클라우드 사업"
    assert any("구분선 줄" in note for note in done.notes)
    with pytest.raises(hwpx_edit.HwpxError, match="구분선 줄"):
        hwpx_edit.apply(_marked(), [
            {"op": "set_cell", "table": "t1", "row": 2, "col": 0, "text": "여기에"}])


def test_fill_fields_writes_labelled_values_at_once_into_the_value_cells():
    done = hwpx_edit.apply(_marked(), [{"op": "fill_fields", "values": [
        {"label": "사업명", "text": "별빛서치"}, {"label": "①상호", "text": "새벽별소프트"},
        {"label": "없는 라벨", "text": "x"}]}])
    assert _text(done.data, "t1", 0, 0) == "사업명"
    assert _text(done.data, "t1", 0, 1) == "별빛서치"
    assert _text(done.data, "t1", 1, 0) == "①상호(단체명)\n새벽별소프트"
    assert any("없는 라벨" in note for note in done.notes)


def test_fixed_body_text_is_not_replaced_but_a_date_line_takes_its_numbers():
    marked = _marked()
    _, doc = hwpx_edit._load(marked)
    ids = {hwpx_edit._own_text(p).strip(): f"p{i + 1}" for i, p in enumerate(doc.paragraphs)}
    letter = ids["고려대학교 총장 귀하"]
    date = next(v for k, v in ids.items() if k.endswith("일") and "년" in k)
    done = hwpx_edit.apply(marked, [
        {"op": "replace_text", "id": letter, "text": "정보대학 총장 귀하"},
        {"op": "replace_text", "id": date, "text": "2026년 8월 3일"}])
    text = hwpx_edit.plain_text(done.data)
    assert "고려대학교 총장 귀하" in text and "정보대학" not in text
    assert "2026년 8월 3일" in text
    assert any("서식이 정한 글" in note for note in done.notes)
    with pytest.raises(hwpx_edit.HwpxError, match="서식이 정한 글"):
        hwpx_edit.apply(marked, [{"op": "delete_paragraph", "id": letter}])


def test_what_the_progress_line_counts_is_what_the_file_holds():
    marked = _marked()
    mark = form_marks.read(marked)
    before = hwpx_form.progress(marked, mark)
    assert before["cellsFilled"] == 0 and before["cells"] >= 3
    done = hwpx_edit.apply(marked, [{"op": "fill_fields", "values": [
        {"label": "사업명", "text": "별빛서치"}, {"label": "①상호", "text": "새벽별소프트"}]}])
    after = hwpx_form.progress(done.data, mark)
    assert after["cellsFilled"] == 2 and after["cells"] == before["cells"]
    assert "사업명" not in after["cellsEmpty"]


def test_a_form_without_a_mark_is_edited_as_before():
    plain = _form()
    done = hwpx_edit.apply(plain, [
        {"op": "set_cell", "table": "t1", "row": 1, "col": 0, "text": "바뀜"}])
    assert _text(done.data, "t1", 1, 0) == "바뀜" and done.notes == []
    with pytest.raises(hwpx_edit.HwpxError, match="양식"):
        hwpx_edit.apply(plain, [{"op": "fill_fields", "values": [
            {"label": "사업명", "text": "x"}]}])


def test_a_turn_that_saved_nothing_says_so_instead_of_sending_the_person_to_type():
    said = hwpx_form.nothing_saved("앞쪽 표는 편집기에서 직접 입력해 주세요.", "")
    assert "아무것도 채우지 못했고" in said and "hwpx" in said and "직접 입력" not in said
    assert "구분선 줄" in hwpx_form.nothing_saved("", "고칠 수 있는 op가 없었습니다. 구분선 줄")
    # a plain answer about the form is no failure
    assert hwpx_form.nothing_saved("남은 빈칸은 대표자와 연락처입니다.", "") == ""


def test_filling_an_old_hwp_gets_the_way_to_hwpx_and_no_essay():
    from app.services import form_files

    turn = form_files.TurnForms(notes={"f1": form_profile.HWP_FORM_ADVICE})
    said = form_files.hwp_refusal(turn, "이 신청서 양식에 맞춰 채워 줘.")
    assert "hwpx" in said and "HWP로 열기" in said
    # a summary of the file is another request; a form that can be followed is not refused
    assert form_files.hwp_refusal(turn, "이 파일 내용을 요약해 줘.") == ""
    assert form_files.hwp_refusal(form_files.TurnForms(), "이 양식에 맞춰 채워 줘.") == ""


def test_a_fixed_line_may_take_a_value_in_its_blank_but_content_cannot_stand_for_it():
    assert hwpx_form._inserted("대학(nameofcollege):", "대학(nameofcollege):정보대학")
    assert not hwpx_form._inserted("사업명", "사업계획의명칭")
    assert not hwpx_form._inserted("대학", "대학교")


_RECEIPT = [
    "대학(Name) :", "위 학생의 복학원서를 접수함. The above student's form is hereby received.",
    "년 월 일"]


def test_a_cell_of_several_fixed_lines_keeps_its_long_lines_too():
    marked = _marked()
    assert form_marks.read(marked)["fixed"]["cells"]["t1:5:0"] == _RECEIPT
    # written whole with its lines carried (a value in the first blank): kept as written
    done = hwpx_edit.apply(marked, [{
        "op": "set_cell", "table": "t1", "row": 5, "col": 0,
        "text": "\n".join(["대학(Name) : 정보대학", *_RECEIPT[1:]])}])
    assert _text(done.data, "t1", 5, 0).split("\n") == ["대학(Name) : 정보대학", *_RECEIPT[1:]]
    # a write that drops the long line is left out, whatever else it says
    with pytest.raises(hwpx_edit.HwpxError, match="그 줄을 그대로 두고"):
        hwpx_edit.apply(marked, [{
            "op": "set_cell", "table": "t1", "row": 5, "col": 0,
            "text": "대학(Name) : 정보대학"}])


def test_a_row_grows_to_hold_what_is_written_in_it_and_never_shrinks():
    done = hwpx_edit.apply(_marked(), [
        {"op": "set_cell", "table": "t1", "row": 0, "col": 1, "text": "가\n나\n다\n라"},
        {"op": "set_cell", "table": "t1", "row": 3, "col": 0, "text": "목적 내용"}])
    _, doc = hwpx_edit._load(done.data)
    table = hwpx_edit._table(doc, "t1")
    first = hwpx_edit._cell(table, 0, 1).find(f"{{{hwpx_edit.HP}}}cellSz")
    tall = hwpx_edit._cell(table, 3, 0).find(f"{{{hwpx_edit.HP}}}cellSz")
    assert int(first.get("height")) >= 4 * 1700
    assert int(tall.get("height")) == 9000
