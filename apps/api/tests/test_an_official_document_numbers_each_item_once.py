"""A 공문 numbers each item once: a nested numbered list is its second level (가. 나.), and a
number the writer typed after the one the format gives is dropped."""

from __future__ import annotations

from app.services import docx_doc, docx_edit, hwp_doc, hwpx_edit
from app.services.tools import hwp as hwp_tools


def _body(text: str) -> list[str]:
    lines = docx_edit.plain_text(docx_doc.official("안내", text)).split("\n")
    return lines[lines.index("제목  안내") + 1:]


def test_a_nested_list_is_the_second_level():
    assert _body("1. 관련\n2. 안내\n   1. 일시: 3월\n   2. 장소: 본관\n3. 기타") == [
        "1.\t관련", "2.\t안내", "가.\t일시: 3월", "나.\t장소: 본관", "3.\t기타  끝."]


def test_a_typed_second_number_goes():
    lines = _body("1. 관련\n2. 안내\n  가. 1. 일시: 3월")
    assert "가.\t일시: 3월  끝." in lines
    text = hwpx_edit.plain_text(hwp_doc.official("안내", "1. 관련\n  가. 1. 일시: 3월"))
    assert "가. 1." not in text


def test_the_write_tool_asks_for_no_invented_attachment():
    description = hwp_tools.HWP_WRITE.description
    assert "only for attachments the person named" in description
