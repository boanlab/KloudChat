"""Where an artifact is listed: the 아티팩트 page or the file box.

- 아티팩트 page: what a conversation made to view or run directly — HTML pages (`html`),
  standalone code files (`code`), charts (`chart`) and diagrams (`diagram`).
- 파일함 › 작업 문서: every report and slide deck (`document`).
- 파일함 › 만든 파일: generated pictures, sound and video (`media`), listed by their files.
- `snippet`: a stored inline code block; its conversation panel opens it but it is listed nowhere.
"""

from __future__ import annotations

from typing import Any

#: the 아티팩트 page's tabs, in order
GALLERY = ("html", "code", "chart", "diagram")
DOCUMENT_KINDS = ("report", "deck", "hwp", "docx", "pptx")


def section_of(kind: str, data: dict[str, Any] | None) -> str:
    """The list an artifact of `kind` with body `data` belongs to (never `snippet`)."""
    kind = str(getattr(kind, "value", kind))
    data = data if isinstance(data, dict) else {}
    if kind in DOCUMENT_KINDS:
        return "document"
    if kind in ("html", "code", "chart"):
        return kind
    if kind == "image":
        # a drawn figure keeps its source (mermaid or shapes); a code-drawn chart its engine
        if data.get("style") == "figure" or data.get("source"):
            return "diagram"
        if data.get("engine") == "matplotlib" or data.get("style") == "차트":
            return "chart"
    return "media"
