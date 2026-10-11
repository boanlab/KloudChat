"""Document handover as chat files via the 파일 탭 exporters."""

from __future__ import annotations

import logging

from app.core import logs
from app.pipeline import registry
from app.pipeline.bundle import Bundle, Document, Request
from app.pipeline.stage import Base, Context
from app.services import sandbox

log = logging.getLogger("app.routers.sessions")

FORMAT_NAMES = {
    "pdf": "PDF",
    "hwpx": "한글(HWPX)",
    "docx": "Word(DOCX)",
    "pptx": "PowerPoint(PPTX)",
    "md": "마크다운",
}


class Export(Base):
    name = "export"
    kind = "io"
    reads = ("request", "document.artifact")
    writes = ("files", "document.handed", "document.reply")

    async def apply(self, bundle: Bundle, ctx: Context) -> None:
        # late import: exporters live with 파일 탭 routes
        from app.routers.workspace import artifact_file, export_formats

        req, artifact = bundle.request, bundle.document.artifact
        offered = export_formats(artifact)
        wanted = list(dict.fromkeys(req.export_formats)) or list(offered[:1])
        makeable = [f for f in wanted if f in offered]
        refused = [f for f in wanted if f not in offered]
        outputs: list[dict] = []
        failed: list[str] = []
        for fmt in makeable:
            try:
                body, name = await artifact_file(artifact, fmt)
                outputs.append(await sandbox.store_output(req.user_id, req.session_id, name,
                                                          body))
            except Exception as exc:  # noqa: BLE001 — one failing still hands the others over
                log.warning("export %s of %s failed: %s", fmt, artifact.id, logs.safe(exc))
                failed.append(fmt)
        said: list[str] = []
        if outputs:
            said.append(
                " · ".join(f"「{row['name']}」" for row in outputs)
                + " 파일을 만들었습니다. 아래에서 내려받으세요."
            )
        if refused:
            what = "슬라이드는" if "pptx" in offered else "이 문서는"
            names = ", ".join(FORMAT_NAMES.get(f, f) for f in refused)
            said.append(
                f"{what} {names}(으)로 내보낼 수 없습니다. "
                f"가능한 형식: {', '.join(FORMAT_NAMES.get(f, f) for f in offered)}."
            )
        if failed:
            said.append(
                f"{', '.join(FORMAT_NAMES.get(f, f) for f in failed)} 파일은 만들지 못했습니다. "
                "파일 탭 → 내보내기에서 다시 받아 주세요."
            )
        bundle.files = outputs
        bundle.document.handed = (
            {
                "id": "export",
                "label": f"파일 {len(outputs)}개 만듦",
                "status": "done",
                "detail": " · ".join(FORMAT_NAMES.get(f, f) for f in makeable
                                     if f not in failed),
                "outputs": outputs,
            }
            if outputs
            else None
        )
        bundle.document.reply = " ".join(said)


registry.register(Export())


def bundle_for(*, artifact, formats: list[str], user_id: str, session_id: str) -> Bundle:
    return Bundle(
        request=Request(text="", model="", api_key="", user_id=user_id, session_id=session_id,
                        export_formats=list(formats)),
        document=Document(artifact=artifact),
    )


__all__ = ["FORMAT_NAMES", "bundle_for"]
