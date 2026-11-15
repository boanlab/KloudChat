"""A report on a 한글 (HWPX) form: the form becomes the conversation's HWP document, marked with
its value cells, filled in place by the HWP tools, and the document says how much is filled."""

from __future__ import annotations

import pytest

from app.models.chat import ChatSession, SessionKind
from app.models.user import User
from app.models.workspace import StoredFile
from app.routers import sessions as sessions_router
from app.services import (
    form_files,
    form_marks,
    form_profile,
    hwp_doc,
    hwpx_edit,
    hwpx_form,
    report_export,
)

TABLE = "| 사업명 | |\n|---|---|\n| 기관명 | |\n| 대표자 | |\n| 연락처 | |"


def _form() -> bytes:
    return report_export.to_hwpx("지원 신청서", [{"heading": "신청 기관", "content": TABLE}])


def _resolved(source: str = "attached") -> form_files.Resolved:
    data = _form()
    profile = form_profile.profile("신청서_별지1호.hwpx", data)
    return form_files.Resolved(
        form_files.FormChoice("hwpx", "f1", "신청서_별지1호.hwpx", source, profile), data)


def test_a_hangul_form_is_followed_now():
    row = StoredFile(user_id="u", name="신청서_별지1호.hwpx")
    row.form_profile = form_profile.profile(row.name, _form())
    turn = form_files.choose([row], None, "이 양식에 채워 줘")
    assert turn.form_for("hwpx").file_id == row.id


def test_the_form_starts_the_document_marked_with_its_value_cells():
    form = _resolved()
    data = hwpx_form.start(form)
    mark = form_marks.read(data)
    assert mark["name"] == "신청서_별지1호.hwpx" and mark["source"] == "attached"
    assert {f["label"] for f in mark["fields"]} >= {"사업명", "기관명", "대표자", "연락처"}
    # still the same HWPX: it opens and reads as the form did
    assert hwpx_edit.plain_text(data) == hwpx_edit.plain_text(form.data)
    described = hwp_doc.describe(data, "hwpx")
    assert described["form"]["followed"]["cellsFilled"] == 0
    assert described["form"]["followed"]["cells"] == len(mark["fields"])


def test_filling_a_cell_counts_and_an_edit_keeps_the_mark():
    data = hwpx_form.start(_resolved())
    mark = form_marks.read(data)
    field = next(f for f in mark["fields"] if f["label"] == "사업명")
    edited = hwpx_edit.apply(data, [{"op": "set_cell", "table": field["table"],
                                     "row": field["row"], "col": field["col"],
                                     "text": "설비 점검 통합 서비스"}]).data
    assert form_marks.read(edited) == mark
    followed = hwp_doc.describe(edited, "hwpx")["form"]["followed"]
    assert followed["cellsFilled"] == 1 and "사업명" not in followed["cellsEmpty"]
    assert "기관명" in followed["cellsEmpty"]


def test_the_writer_is_told_to_fill_in_place_and_which_cells():
    mark = form_marks.read(hwpx_form.start(_resolved()))
    rule = hwpx_form.rule(mark)
    assert "never replace it with hwp_write" in rule and "set_cell" in rule
    assert "사업명(" in rule and "[담당자]" in rule


@pytest.mark.asyncio
async def test_a_report_asked_as_hwp_on_a_hangul_form_opens_the_form(monkeypatch):
    session = ChatSession(id="s1", user_id="u1", kind=SessionKind.report)
    form = _resolved()
    made: dict = {}

    async def for_session(_db, _session, kind, **_kwargs):
        return form if kind == "hwpx" else None

    async def create(_db, **fields):
        made.update(fields)

        class Row:
            id = "a1"
        return Row()

    class Db:
        def add(self, _row):
            pass

        async def commit(self):
            pass

        async def refresh(self, _row):
            pass

    monkeypatch.setattr(form_files, "for_session", for_session)
    monkeypatch.setattr(hwp_doc, "create", create)
    await sessions_router._hwpx_form_surface(Db(), User(id="u1", email="u@x", name="u"), session)
    assert session.artifact_id == "a1" and made["title"] == "신청서_별지1호"
    assert form_marks.read(made["data"])["kind"] == "hwpx"
    # a conversation that already has its document is left as it is
    session.artifact_id = "a0"
    await sessions_router._hwpx_form_surface(Db(), User(id="u1", email="u@x", name="u"), session)
    assert session.artifact_id == "a0"
