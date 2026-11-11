"""Word document artifacts: create one (blank, or from an uploaded `.docx`), fetch a
version's file for the editor, and save the editor's file as a new version."""

from __future__ import annotations

import re

from app.routers.office_docs import Routes, router_for
from app.services import docx_doc

router = router_for(Routes(
    slug="docx",
    kind=docx_doc.KIND,
    blank=docx_doc.blank,
    blank_title="새 Word 문서",
    suffix=re.compile(r"\.docx$", re.I),
    mime=lambda fmt: docx_doc.DOCX_MIME,
))
