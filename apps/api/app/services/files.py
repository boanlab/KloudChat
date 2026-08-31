"""Upload storage and text extraction.

Blobs go to local disk under a per-user directory; extracted text goes in the
row. `storage_key` is the indirection that would make object storage a
one-module change.

Extraction is dependency-light on purpose: every format here has a stdlib or
small pure-Python reader. Anything needing a system binary — tesseract for
scans — reports that it could not be read rather than adding 400 MB to the
image.
"""

from __future__ import annotations

import csv
import io
import logging
import re
import unicodedata
import zipfile
import zlib
from pathlib import Path

from app.core.config import settings
from app.services import pictures, transcribe

log = logging.getLogger(__name__)

#: Rough tokens-per-character for mixed Korean/English. Only used for warnings.
_CHARS_PER_TOKEN = 3.0


def storage_root() -> Path:
    root = Path(settings.file_storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_name(name: str) -> str:
    """Keeps the original name readable while making it useless as a path."""
    cleaned = unicodedata.normalize("NFC", name).replace("\x00", "")
    cleaned = re.sub(r"[/\\]", "_", cleaned).strip() or "file"
    return cleaned[:200]


def write_blob(user_id: str, file_id: str, name: str, data: bytes) -> str:
    """Returns the storage key. Directory per user keeps listings small."""
    directory = storage_root() / user_id
    directory.mkdir(parents=True, exist_ok=True)
    # The id prefix means two uploads of "report.pdf" never collide.
    key = f"{user_id}/{file_id}_{safe_name(name)}"
    (storage_root() / key).write_bytes(data)
    return key


def read_blob(key: str) -> bytes:
    return (storage_root() / key).read_bytes()


def delete_blob(key: str) -> None:
    try:
        (storage_root() / key).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("could not remove blob %s: %s", key, exc)


def estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


# ── extraction ─────────────────────────────────────────────────────────


def _from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError:  # pragma: no cover — dependency is pinned
        raise RuntimeError(
            "이 서버에서는 PDF 를 읽을 수 없습니다. 관리자에게 문의하세요."
        ) from None
    try:
        reader = PdfReader(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — pypdf raises a dozen different types
        # The raw message would reach the person who uploaded the file.
        log.info("pdf unreadable: %s", exc)
        raise RuntimeError("손상되었거나 PDF 형식이 아닌 파일입니다.") from None
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            pages.append(page.extract_text() or "")
        except Exception as exc:  # noqa: BLE001 — one bad page must not lose the rest
            log.info("pdf page %d unreadable: %s", i, exc)
    text = "\n\n".join(p for p in pages if p.strip())
    if not text.strip():
        # Almost always a scan — more useful said than shown as an empty file.
        raise RuntimeError("텍스트를 추출하지 못했습니다. 스캔본이라면 OCR 이 필요합니다.")
    return text


def _from_docx(data: bytes) -> str:
    """Reads word/document.xml directly: a .docx is a zip, and paragraph text is
    all that is wanted.
    """
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        try:
            xml = archive.read("word/document.xml").decode("utf-8", errors="replace")
        except KeyError:
            raise RuntimeError("docx 구조를 읽지 못했습니다.") from None
    # Paragraph boundaries first, then drop every remaining tag.
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    text = re.sub(r"<[^>]+>", "", xml)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _from_pptx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        slides = sorted(
            n for n in archive.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)
        )
        out = []
        for i, name in enumerate(slides, 1):
            xml = archive.read(name).decode("utf-8", errors="replace")
            xml = re.sub(r"</a:p>", "\n", xml)
            body = re.sub(r"<[^>]+>", "", xml).strip()
            if body:
                out.append(f"[슬라이드 {i}]\n{body}")
    return "\n\n".join(out).strip()


def _from_xlsx(data: bytes) -> str:
    """Sheet values via the shared-strings table. Formulas are not evaluated."""
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            raw = archive.read("xl/sharedStrings.xml").decode("utf-8", errors="replace")
            shared = [re.sub(r"<[^>]+>", "", m) for m in re.findall(r"<si>(.*?)</si>", raw, re.S)]

        sheets = sorted(
            n for n in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)
        )
        out = []
        for index, name in enumerate(sheets, 1):
            xml = archive.read(name).decode("utf-8", errors="replace")
            rows = []
            for row in re.findall(r"<row[^>]*>(.*?)</row>", xml, re.S):
                cells = []
                for cell in re.findall(r"<c([^>]*)>(.*?)</c>", row, re.S):
                    attrs, body = cell
                    value = re.search(r"<v>(.*?)</v>", body, re.S)
                    if not value:
                        cells.append("")
                        continue
                    raw = value.group(1)
                    if 't="s"' in attrs:  # index into the shared-strings table
                        try:
                            cells.append(shared[int(raw)])
                        except (ValueError, IndexError):
                            cells.append("")
                    else:
                        cells.append(raw)
                if any(c.strip() for c in cells):
                    rows.append("\t".join(cells))
            if rows:
                out.append(f"[시트 {index}]\n" + "\n".join(rows))
    return "\n\n".join(out).strip()


def _from_hwpx(data: bytes) -> str:
    """`.hwpx` is OWPML — an XML zip, so the standard library is enough.

    Body text is in the `<hp:t>` runs of `Contents/section*.xml`; paragraph
    boundaries come from `<hp:p>`.
    """
    import xml.etree.ElementTree as ET

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise RuntimeError(
            "한글 문서(.hwpx)를 열지 못했습니다. 파일이 손상되었을 수 있습니다."
        ) from exc

    sections = sorted(
        n for n in archive.namelist() if n.startswith("Contents/section") and n.endswith(".xml")
    )
    if not sections:
        raise RuntimeError("한글 문서(.hwpx)에서 본문을 찾지 못했습니다.")

    paragraphs: list[str] = []
    for name in sections:
        try:
            root = ET.fromstring(archive.read(name))
        except ET.ParseError:
            continue
        # Namespaces vary by producer version, so match on the local name.
        for para in root.iter():
            if not para.tag.endswith("}p") and para.tag != "p":
                continue
            runs = [
                node.text
                for node in para.iter()
                if (node.tag.endswith("}t") or node.tag == "t") and node.text
            ]
            if runs:
                paragraphs.append("".join(runs))
    return "\n".join(paragraphs)


#: HWP 5.x record header: 32 bits — tag 10, level 10, size 12. A size of 0xFFF
#: means the real size is the following 32 bits.
_HWPTAG_PARA_TEXT = 67

#: Control characters interleaved with body text. Extended controls — tables,
#: images — occupy 16 bytes, and reading those as characters corrupts the text.
_HWP_EXTENDED_CONTROLS = frozenset({1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23})
_HWP_INLINE_CONTROLS = frozenset({4, 5, 6, 7, 8, 19, 20})


def _hwp_records(stream: bytes):
    """Yields `(tag, payload)` in order."""
    offset, size = 0, len(stream)
    while offset + 4 <= size:
        header = int.from_bytes(stream[offset : offset + 4], "little")
        offset += 4
        tag = header & 0x3FF
        length = (header >> 20) & 0xFFF
        if length == 0xFFF:
            if offset + 4 > size:
                return
            length = int.from_bytes(stream[offset : offset + 4], "little")
            offset += 4
        if offset + length > size:
            return
        yield tag, stream[offset : offset + length]
        offset += length


def _hwp_paragraph(body: bytes) -> str:
    """Decodes one HWPTAG_PARA_TEXT record to a string.

    UTF-16LE with control characters mixed in. An extended control occupies
    eight characters; skipping fewer leaks object bytes into the text.
    """
    out: list[str] = []
    index, count = 0, len(body) // 2
    while index < count:
        code = int.from_bytes(body[index * 2 : index * 2 + 2], "little")
        if code in _HWP_EXTENDED_CONTROLS:
            index += 8
            continue
        if code in _HWP_INLINE_CONTROLS:
            index += 1
            continue
        if code in (10, 13):
            out.append("\n")
        elif code >= 32:
            out.append(chr(code))
        index += 1
    return "".join(out)


def _from_hwp(data: bytes) -> str:
    """`.hwp` 5.x — body text from the record tree of an OLE compound file.

    `olefile` reads the container, the standard library decompresses.
    Formatting and embedded objects are discarded.
    """
    import olefile

    try:
        ole = olefile.OleFileIO(io.BytesIO(data))
    except Exception as exc:  # noqa: BLE001 — corrupt and encrypted files alike
        log.info("hwp open failed: %s", exc)
        raise RuntimeError(
            "한글 문서(.hwp)를 읽지 못했습니다. 손상되었거나 암호가 걸려 있을 수 있습니다. "
            "PDF 로 변환해 올려 주세요."
        ) from exc

    try:
        if not ole.exists("FileHeader"):
            raise RuntimeError("한글 문서(.hwp) 형식이 아닙니다. 5.0 이상인지 확인해 주세요.")
        header = ole.openstream("FileHeader").read()
        # 32 bytes signature, 4 version, then flags: bit 0 compression,
        # bit 1 encryption.
        flags = int.from_bytes(header[36:40], "little") if len(header) >= 40 else 0
        if flags & 0x02:
            raise RuntimeError("암호가 걸린 한글 문서(.hwp)는 읽을 수 없습니다.")
        compressed = bool(flags & 0x01)

        sections = sorted(
            ("/".join(entry) for entry in ole.listdir() if entry[0] == "BodyText"),
            key=lambda name: int(re.sub(r"\D", "", name.rsplit("/", 1)[-1]) or 0),
        )
        if not sections:
            raise RuntimeError("한글 문서(.hwp)에서 본문을 찾지 못했습니다.")

        paragraphs: list[str] = []
        for name in sections:
            raw = ole.openstream(name).read()
            if compressed:
                try:
                    raw = zlib.decompress(raw, -15)
                except zlib.error:
                    continue
            for tag, body in _hwp_records(raw):
                if tag != _HWPTAG_PARA_TEXT:
                    continue
                text = _hwp_paragraph(body).strip()
                if text:
                    paragraphs.append(text)
    finally:
        ole.close()

    if not paragraphs:
        raise RuntimeError("한글 문서(.hwp)에서 읽을 수 있는 본문이 없습니다.")
    return "\n".join(paragraphs)


def _from_csv(data: bytes) -> str:
    text = data.decode("utf-8-sig", errors="replace")
    try:
        dialect = csv.Sniffer().sniff(text[:4000])
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text), dialect))
    return "\n".join("\t".join(r) for r in rows)


def _from_text(data: bytes) -> str:
    for encoding in ("utf-8", "cp949", "euc-kr", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


_TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".json", ".yaml", ".yml", ".toml", ".ini", ".log",
    ".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".c", ".h", ".cpp", ".go", ".rs",
    ".rb", ".sh", ".sql", ".html", ".css", ".xml", ".tex", ".r",
}


def is_speech(mime: str) -> bool:
    """Whether this is something somebody said rather than something they wrote."""
    return mime.startswith(("audio/", "video/"))


async def text_of(name: str, mime: str, data: bytes) -> str:
    """The file as text, transcribing it when the text is speech.

    A meeting does not arrive as somebody re-speaking it into the composer. It
    arrives as a recording — the room's file, an hour of it, made by whoever
    was there. Refusing that and offering a microphone instead asks the one
    person who already sat through the meeting to sit through it again.

    The same backend the microphone uses. It was wired to one button and to
    nothing else, so the capability was in the deployment and out of reach of
    the job it exists for.

    Async because transcription is a call and `extract_text` is not; the
    callers are routes, and this is the one they should reach for.
    """
    if not is_speech(mime):
        return extract_text(name, mime, data)
    if not await transcribe.available():
        return extract_text(name, mime, data)
    if len(data) > transcribe.MAX_BYTES:
        raise RuntimeError(
            f"녹음이 너무 깁니다. {transcribe.MAX_BYTES // (1024 * 1024)}MB 이하로 나눠 올려 주세요."
        )
    return await transcribe.transcribe(data, name)


def extract_text(name: str, mime: str, data: bytes) -> str:
    """Best-effort text for the model. Raises with a Korean reason on failure."""
    suffix = Path(name).suffix.lower()

    if suffix == ".pdf" or mime == "application/pdf":
        return _from_pdf(data)
    if suffix == ".docx":
        return _from_docx(data)
    if suffix == ".pptx":
        return _from_pptx(data)
    if suffix == ".xlsx":
        return _from_xlsx(data)
    if suffix in {".csv", ".tsv"}:
        return _from_csv(data)
    if suffix in _TEXT_SUFFIXES or mime.startswith("text/"):
        return _from_text(data)
    if suffix == ".hwpx":
        return _from_hwpx(data)
    if suffix == ".hwp":
        return _from_hwp(data)
    if suffix in {".doc", ".ppt", ".xls"}:
        raise RuntimeError("구형 오피스 형식입니다. docx/pptx/xlsx 또는 PDF 로 변환해 주세요.")
    if mime.startswith("image/"):
        # A picture is not a document that failed to parse — it has no text and
        # does not need any. Whether it reaches a model is decided per turn by
        # `workspace_context.reads_pictures`, so recording a failure here would
        # put a warning on a file that is about to be read perfectly well.
        if pictures.can_be_seen(mime, len(data)):
            return ""
        raise RuntimeError(
            "이 그림은 읽을 수 없습니다. PNG·JPEG·GIF·WebP 로, 4MB 이하로 올려 주세요."
        )
    if is_speech(mime):
        # Reached only when transcription is off or has failed — `text_of`
        # takes this branch away when a backend is configured.
        raise RuntimeError(
            "말소리를 글로 옮기는 기능이 꺼져 있어 이 파일을 읽지 못했습니다. "
            "관리자에게 요청하거나, 회의록 텍스트를 올려 주세요."
        )

    # Unknown extension: try text, and let the mojibake check below decide.
    text = _from_text(data)
    printable = sum(1 for ch in text[:2000] if ch.isprintable() or ch.isspace())
    if printable < len(text[:2000]) * 0.8:
        raise RuntimeError("읽을 수 없는 형식입니다. 텍스트나 PDF 로 올려 주세요.")
    return text
