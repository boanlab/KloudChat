"""A shared sheet page shows values of the visible sheets (no formula, no hidden sheet, no formula
text in `data`, no owner file id), and an uncalculated formula says so."""

from __future__ import annotations

import io

import openpyxl

from app.models.workspace import Artifact, ArtifactKind
from app.routers import shares


def _book() -> bytes:
    book = openpyxl.Workbook()
    ws = book.active
    ws["A1"] = 5
    ws["A2"] = "=VLOOKUP(5,A1:A1,1,FALSE)"
    ws["A3"] = "=A1*2"
    secret = book.create_sheet("숨김")
    secret["A1"] = "비밀 메모"
    secret.sheet_state = "hidden"
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def test_the_shared_page_drops_formulas_and_hidden_sheets():
    shaped = shares._shaped_sheet(_book())  # noqa: SLF001
    assert [s["name"] for s in shaped["sheets"]] == ["Sheet"]
    cells = shaped["sheets"][0]["cells"]
    assert all("f" not in c for c in cells)
    assert "비밀 메모" not in str(shaped)
    shown = {(c["r"], c["c"]): c.get("v") for c in cells}
    assert shown[(0, 0)] == 5
    assert shown[(1, 0)] == "(계산 안 됨)"


def test_the_shared_data_keeps_no_search_text_or_owner_file_of_a_sheet():
    artifact = Artifact(
        id="a" * 32, user_id="u", kind=ArtifactKind.xlsx, title="표",
        data={"kind": "xlsx", "format": "xlsx", "cells": 3, "text": "=A1*2\n비밀 메모",
              "form": {"fileId": "f" * 32, "name": "양식.xlsx"}},
    )
    data = shares._shared_data("tok", artifact)  # noqa: SLF001
    assert "text" not in data and "form" not in data
    assert data["format"] == "xlsx"
