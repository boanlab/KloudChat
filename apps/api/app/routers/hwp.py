"""HWP document artifacts: create one (blank, or from an uploaded `.hwp`/`.hwpx`), fetch a
version's file for the editor, and save the editor's file as a new version."""

from __future__ import annotations

import re

from app.routers.office_docs import Routes, router_for
from app.services import hwp_doc

router = router_for(Routes(
    slug="hwp",
    kind=hwp_doc.KIND,
    blank=hwp_doc.blank,
    blank_title="새 한글 문서",
    suffix=re.compile(r"\.hwpx?$", re.I),
    mime=lambda fmt: hwp_doc.HWPX_MIME if fmt == "hwpx" else "application/x-hwp",
))
