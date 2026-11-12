"""PowerPoint document artifacts: create one (blank, or from an uploaded `.pptx`), fetch a
version's file for the editor, and save the editor's file as a new version."""

from __future__ import annotations

import re

from app.routers.office_docs import Routes, router_for
from app.services import pptx_doc

router = router_for(Routes(
    slug="pptx",
    kind=pptx_doc.KIND,
    blank=pptx_doc.blank,
    blank_title="새 PowerPoint 문서",
    suffix=re.compile(r"\.pptx$", re.I),
    mime=lambda fmt: pptx_doc.PPTX_MIME,
))
