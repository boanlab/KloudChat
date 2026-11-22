"""Re-read the text of stored PDFs whose Korean came out without word spacing.

Reads each such PDF again from its blob (`services/pdf_text.py`), keeps the new text when it has
spacing, queues a fresh digest and re-indexes. Files with fine text, OCR scans and other kinds are skipped.

    python scripts/reextract_pdfs.py            # report only
    python scripts/reextract_pdfs.py --apply    # rewrite the text
    python scripts/reextract_pdfs.py --apply --project <id>
"""

from __future__ import annotations

import argparse
import asyncio
import re

from sqlmodel import col, select

from app.core.db import SessionLocal
from app.models.workspace import Project, StoredFile
from app.services import digest_jobs
from app.services import files as file_service

_HANGUL = re.compile(r"[가-힣]+")


def glued(text: str) -> bool:
    """Korean text whose Hangul runs are far longer than words: prose averages two to three
    letters between spaces or marks, run-together text six or more."""
    runs = _HANGUL.findall(text)
    letters = sum(len(run) for run in runs)
    return letters >= 150 and letters / len(runs) > 5


async def main(apply: bool, project_id: str | None) -> None:
    from app.routers.workspace import _index_document, _project_shelf_key

    async with SessionLocal() as db:
        query = select(StoredFile).where(col(StoredFile.name).ilike("%.pdf"))
        if project_id:
            query = query.where(StoredFile.project_id == project_id)
        rows = (await db.exec(query)).all()
        changed = 0
        for stored in rows:
            if stored.ocr_state or not stored.storage_key or not glued(stored.text or ""):
                continue
            try:
                data = file_service.read_blob(stored.storage_key)
                text = await asyncio.to_thread(file_service.extract_text, stored.name,
                                               stored.mime, data)
            except Exception as exc:  # noqa: BLE001 — one unreadable file must not stop the rest
                print(f"skip {stored.id} {stored.name}: {exc}")
                continue
            if glued(text):
                print(f"still glued {stored.id} {stored.name}")
                continue
            changed += 1
            print(f"{'fix' if apply else 'would fix'} {stored.id} {stored.name}")
            if not apply:
                continue
            stored.text = text
            stored.tokens = file_service.estimate_tokens(text)
            digest_jobs.mark(stored)
            db.add(stored)
            await db.commit()
            await db.refresh(stored)
            if stored.project_id and (project := await db.get(Project, stored.project_id)):
                await _index_document(db, stored.user_id, await _project_shelf_key(db, project),
                                      stored)
        print(f"{changed} file(s) {'re-read' if apply else 'to re-read'}")
    if apply and changed:
        digest_jobs.wake()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--project")
    options = parser.parse_args()
    asyncio.run(main(options.apply, options.project))
