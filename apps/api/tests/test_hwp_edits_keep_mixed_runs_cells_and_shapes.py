"""HWP edits touch only what they change: a partial edit keeps the runs around it (a bold
label, a red word), a cell edit keeps the cell's other paragraphs and nested tables, and a
table added to a file made elsewhere brings its own shapes instead of borrowing the file's ids."""

from __future__ import annotations

import io
import zipfile

from lxml import etree

from app.services import hwp_doc, hwpx_edit

HP = "{http://www.hancom.co.kr/hwpml/2011/paragraph}"
HH = "{http://www.hancom.co.kr/hwpml/2011/head}"


def _parts(data: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _zip(parts: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), parts["mimetype"], zipfile.ZIP_STORED)
        for name, body in parts.items():
            if name != "mimetype":
                archive.writestr(name, body)
    return out.getvalue()


def _section(data: bytes) -> etree._Element:
    return etree.fromstring(_parts(data)["Contents/section0.xml"])


def _with_section(data: bytes, edit) -> bytes:
    parts = _parts(data)
    root = etree.fromstring(parts["Contents/section0.xml"])
    edit(root)
    parts["Contents/section0.xml"] = etree.tostring(root, xml_declaration=True,
                                                    encoding="UTF-8")
    return _zip(parts)


def _mixed() -> bytes:
    """A paragraph in three runs: 「담당: 」 bold, 「김민수」 red, 「 주무관」 plain."""
    data = hwp_doc.plain("안내", "자리")

    def edit(root):
        p = next(p for p in root.iter(f"{HP}p") if "자리" in "".join(p.itertext()))
        for run in list(p.iterchildren(f"{HP}run")):
            p.remove(run)
        for shape, text in (("1", "담당: "), ("5", "김민수"), ("0", " 주무관")):
            run = etree.SubElement(p, f"{HP}run", charPrIDRef=shape)
            etree.SubElement(run, f"{HP}t").text = text
    return _with_section(data, edit)


def _runs(data: bytes, starts: str) -> list[tuple[str, str]]:
    p = next(p for p in _section(data).iter(f"{HP}p")
             if "".join(p.itertext()).startswith(starts))
    return [(r.get("charPrIDRef"), "".join(r.itertext())) for r in p.iterchildren(f"{HP}run")]


def _pid(data: bytes, starts: str) -> str:
    return next(line.split(":")[0].split(" ")[0] for line in hwpx_edit.outline(data).splitlines()
                if starts in line)


def test_a_partial_edit_keeps_the_runs_around_it():
    data = _mixed()
    pid = _pid(data, "담당: 김민수")
    edited = hwpx_edit.apply(data, [{"op": "replace_text", "id": pid,
                                     "text": "담당: 이영희 주무관"}]).data
    assert _runs(edited, "담당") == [("1", "담당: "), ("5", "이영희"), ("0", " 주무관")]
    # a word added at the end goes into the run before it, the others stay
    edited = hwpx_edit.apply(edited, [{"op": "replace_text", "id": pid,
                                       "text": "담당: 이영희 주무관(내선 123)"}]).data
    assert _runs(edited, "담당") == [("1", "담당: "), ("5", "이영희"), ("0", " 주무관(내선 123)")]


def test_a_blank_across_runs_is_filled_in_the_run_it_starts_in():
    data = hwp_doc.plain("안내", "자리")

    def edit(root):
        p = next(p for p in root.iter(f"{HP}p") if "자리" in "".join(p.itertext()))
        for run in list(p.iterchildren(f"{HP}run")):
            p.remove(run)
        for shape, text in (("1", "기관: [기관"), ("0", "명] 귀하")):
            run = etree.SubElement(p, f"{HP}run", charPrIDRef=shape)
            etree.SubElement(run, f"{HP}t").text = text
    data = _with_section(data, edit)
    edited = hwpx_edit.apply(data, [{"op": "fill_blank", "blank": "[기관명]",
                                     "value": "한빛시청"}]).data
    assert _runs(edited, "기관") == [("1", "기관: 한빛시청"), ("0", " 귀하")]


def _cell_with_more() -> bytes:
    """t1 r1c1 holds two paragraphs and a nested table."""
    data = hwp_doc.plain("안내", "| 구분 | 내용 |\n|---|---|\n| 일정 | 첫 줄 |")

    def edit(root):
        cells = [c for c in root.iter(f"{HP}tc")]
        target = cells[-1]
        sub = target.find(f"{HP}subList")
        first = sub.find(f"{HP}p")
        second = etree.fromstring(etree.tostring(first))
        second.find(f".//{HP}t").text = "둘째 줄"
        first.addnext(second)
        holder = etree.fromstring(etree.tostring(first))
        for run in holder.iterchildren(f"{HP}run"):
            for t in list(run):
                run.remove(t)
        inner = etree.fromstring(etree.tostring(next(root.iter(f"{HP}tbl"))))
        holder.find(f"{HP}run").append(inner)
        second.addnext(holder)
    return _with_section(data, edit)


def test_a_cell_edit_keeps_its_other_paragraphs_and_nested_table():
    data = _cell_with_more()
    assert "첫 줄 / 둘째 줄" in hwpx_edit.outline(data)
    edited = hwpx_edit.apply(data, [{"op": "set_cell", "table": "t1", "row": 1, "col": 1,
                                     "text": "첫 줄 고침\n둘째 줄"}]).data
    cell = list(_section(edited).iter(f"{HP}tc"))[3]
    texts = ["".join(t.text or "" for t in p.iter(f"{HP}t"))
             for p in cell.find(f"{HP}subList").iterchildren(f"{HP}p")]
    assert texts[:2] == ["첫 줄 고침", "둘째 줄"]
    assert cell.find(f".//{HP}tbl") is not None
    # only when the whole cell is asked to go does the nested table go
    whole = hwpx_edit.apply(data, [{"op": "set_cell", "table": "t1", "row": 1, "col": 1,
                                    "text": "새 칸", "whole": True}]).data
    cell = list(_section(whole).iter(f"{HP}tc"))[3]
    assert cell.find(f".//{HP}tbl") is None
    assert [("".join(p.itertext())) for p in cell.find(f"{HP}subList")] == ["새 칸"]


def _foreign() -> bytes:
    """A file whose header gives the house ids other meanings, as one made in 한글 does."""
    data = hwp_doc.plain("안내", "본문")
    parts = _parts(data)
    head = etree.fromstring(parts["Contents/header.xml"])
    for shape in head.iter(f"{HH}charPr"):
        if shape.get("id") == "1":
            shape.set("height", "2000")
            shape.set("textColor", "#FF0000")
    for fill in head.iter(f"{HH}borderFill"):
        if fill.get("id") in ("2", "3"):
            for side in fill:
                if side.get("type") is not None and side.tag.endswith("Border"):
                    side.set("type", "NONE")
    parts["Contents/header.xml"] = etree.tostring(head, xml_declaration=True, encoding="UTF-8")
    return _zip(parts)


def test_a_new_table_in_a_foreign_file_brings_its_own_shapes():
    data = _foreign()
    edited = hwpx_edit.apply(data, [{"op": "insert_table", "after": "p1",
                                     "rows": [["구분", "내용"], ["일정", "3월"]]}]).data
    head = etree.fromstring(_parts(edited)["Contents/header.xml"])
    shapes = {s.get("id"): s for s in head.iter(f"{HH}charPr")}
    fills = {f.get("id"): f for f in head.iter(f"{HH}borderFill")}
    table = next(_section(edited).iter(f"{HP}tbl"))
    head_cell = next(table.iter(f"{HP}tc"))
    run = next(head_cell.iter(f"{HP}run"))
    # the head row is bold body text, not the file's red 20 pt shape 1
    used = shapes[run.get("charPrIDRef")]
    assert run.get("charPrIDRef") != "1"
    assert used.get("height") != "2000" and used.find(f"{HH}bold") is not None
    border = fills[head_cell.get("borderFillIDRef")]
    assert any(side.get("type") == "SOLID" for side in border)
    # counts follow what is defined
    listing = next(head.iter(f"{HH}charProperties"))
    assert listing.get("itemCnt") == str(len(listing.findall(f"{HH}charPr")))
    # a file of our own reuses its shapes: nothing added
    own = hwp_doc.plain("안내", "본문")
    again = hwpx_edit.apply(own, [{"op": "insert_table", "after": "p1",
                                   "rows": [["구분", "내용"], ["일정", "3월"]]}]).data
    before = etree.fromstring(_parts(own)["Contents/header.xml"])
    after = etree.fromstring(_parts(again)["Contents/header.xml"])
    assert len(list(after.iter(f"{HH}charPr"))) == len(list(before.iter(f"{HH}charPr")))
    assert len(list(after.iter(f"{HH}borderFill"))) == len(list(before.iter(f"{HH}borderFill")))
