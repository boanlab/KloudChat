"""Office uploads read as the text a person sees: Korean, sheet names, column places."""

import io
import zipfile

from app.services.files import extract_text

_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _ent(text: str) -> str:
    """Every non-ASCII character as a numeric entity, as openpyxl-like writers do."""
    return "".join(c if ord(c) < 128 else f"&#{ord(c)};" for c in text)


def _zip(members: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, body in members.items():
            z.writestr(name, body)
    return buf.getvalue()


def _xlsx() -> bytes:
    workbook = (
        f'<workbook xmlns="{_MAIN}" xmlns:r="{_REL}"><sheets>'
        f'<sheet name="{_ent("연구비집행")}" sheetId="1" r:id="rId1"/>'
        f'<sheet name="{_ent("논문실적")}" sheetId="2" r:id="rId2"/>'
        "</sheets></workbook>"
    )
    rels = (
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Target="/xl/worksheets/sheet10.xml" Type="ws"/>'
        '<Relationship Id="rId2" Target="worksheets/sheet2.xml" Type="ws"/>'
        "</Relationships>"
    )
    first = (
        f'<worksheet xmlns="{_MAIN}"><sheetData>'
        f'<row r="1"><c r="A1" t="inlineStr"><is><t>{_ent("연도")}</t></is></c>'
        f'<c r="B1" t="s"><v>0</v></c>'
        f'<c r="C1" t="inlineStr"><is><t>{_ent("예산(천원)")}</t></is></c></row>'
        '<row r="2"><c r="A2"><v>2024</v></c><c r="C2"><v>42000</v></c></row>'
        "</sheetData></worksheet>"
    )
    second = (
        f'<worksheet xmlns="{_MAIN}"><sheetData>'
        '<row r="1"><c r="A1"><v>2022</v></c><c r="B1"><v>2</v></c></row>'
        "</sheetData></worksheet>"
    )
    shared = (
        f'<sst xmlns="{_MAIN}"><si><r><t>{_ent("항")}</t></r><r><t>{_ent("목")}</t></r></si></sst>'
    )
    return _zip(
        {
            "xl/workbook.xml": workbook,
            "xl/_rels/workbook.xml.rels": rels,
            "xl/worksheets/sheet10.xml": first,
            "xl/worksheets/sheet2.xml": second,
            "xl/sharedStrings.xml": shared,
        }
    )


def test_an_xlsx_reads_korean_sheet_names_in_workbook_order():
    text = extract_text("grant.xlsx", "", _xlsx())
    assert "&#" not in text
    assert text.splitlines()[0] == "[시트 1: 연구비집행]"
    assert "연도\t항목\t예산(천원)" in text
    # The empty B2 keeps 42000 under 예산, not under 항목.
    assert "2024\t\t42000" in text
    assert text.index("연구비집행") < text.index("논문실적")


def test_a_docx_and_pptx_unescape_their_text():
    w = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    docx = _zip(
        {
            "word/document.xml": (
                f'<w:document xmlns:w="{w}"><w:body>'
                f"<w:p><w:r><w:t>{_ent('연구 방법')} &amp; </w:t></w:r>"
                "<w:r><w:t>A&lt;B</w:t></w:r></w:p>"
                f"<w:tbl><w:tr><w:tc><w:p><w:r><w:t>{_ent('항목')}</w:t></w:r></w:p></w:tc>"
                "<w:tc><w:p><w:r><w:t>1</w:t></w:r></w:p></w:tc></w:tr></w:tbl>"
                "</w:body></w:document>"
            )
        }
    )
    assert extract_text("a.docx", "", docx) == "연구 방법 & A<B\n항목\t1"

    a = "http://schemas.openxmlformats.org/drawingml/2006/main"

    def slide(body: str) -> str:
        return f'<p:sld xmlns:p="p" xmlns:a="{a}"><a:p><a:r><a:t>{body}</a:t></a:r></a:p></p:sld>'

    pptx = _zip(
        {
            "ppt/slides/slide2.xml": slide(_ent("둘째")),
            "ppt/slides/slide10.xml": slide(_ent("열째")),
        }
    )
    text = extract_text("a.pptx", "", pptx)
    assert text == "[슬라이드 1]\n둘째\n\n[슬라이드 2]\n열째"
