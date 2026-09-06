"""Upload storage (local disk, per-user directory) and text extraction.

Every format is read with the stdlib or a small pure-Python reader; scans
needing OCR are reported as unreadable.
"""

from __future__ import annotations

import csv
import io
import logging
import os
import re
import shutil
import unicodedata
import zipfile
import zlib
from pathlib import Path
from urllib.parse import quote

from app.core.config import settings
from app.services import pictures, transcribe

log = logging.getLogger(__name__)

#: Rough characters per token for mixed Korean/English; used for warnings only.
_CHARS_PER_TOKEN = 3.0


def storage_root() -> Path:
    root = Path(settings.file_storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def remove_user_files(user_id: str) -> int:
    """Deletes one account's whole directory. Returns the bytes it held."""
    root = storage_root()
    # `user_id` came off a URL: only a directory directly under the root qualifies.
    directory = os.path.normpath(os.path.join(root, user_id))
    if os.path.dirname(directory) != os.path.normpath(root) or not os.path.isdir(directory):
        return 0
    freed = sum(p.stat().st_size for p in Path(directory).rglob("*") if p.is_file())
    shutil.rmtree(directory, ignore_errors=True)
    return freed


def safe_name(name: str) -> str:
    """Keeps the original name readable while making it useless as a path."""
    cleaned = unicodedata.normalize("NFC", name).replace("\x00", "")
    cleaned = re.sub(r"[/\\]", "_", cleaned).strip() or "file"
    return cleaned[:200]


def write_blob(user_id: str, file_id: str, name: str, data: bytes) -> str:
    """Writes the blob and returns its storage key (`<user_id>/<file_id>_<name>`)."""
    directory = storage_root() / user_id
    directory.mkdir(parents=True, exist_ok=True)
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


#: What a browser may render in place: raster pictures, PDF and media. Never SVG
#: or HTML, which can carry script and would run on the API's own origin.
INLINE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "application/pdf"}
)

#: Types a browser would execute or style rather than display as data.
_ACTIVE_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml"}
)

#: Zip and OLE containers are told apart by the name; the bytes alone cannot.
_ZIP_SUFFIXES = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".hwpx": "application/hwp+zip",
}
_OLE_SUFFIXES = {
    ".hwp": "application/x-hwp",
    ".doc": "application/msword",
    ".xls": "application/vnd.ms-excel",
    ".ppt": "application/vnd.ms-powerpoint",
}

#: Leading bytes that settle the type on their own.
_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"BM", "image/bmp"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
    (b"%PDF-", "application/pdf"),
    (b"ID3", "audio/mpeg"),
    (b"OggS", "audio/ogg"),
    (b"fLaC", "audio/flac"),
    (b"#!AMR", "audio/amr"),
    (b"\x1aE\xdf\xa3", "video/webm"),
)

_MARKUP_HEAD = re.compile(rb"<!doctype\s+html|<html[\s>]|<head[\s>]|<body[\s>]|<script[\s>]", re.I)


def sniff(name: str, data: bytes) -> str | None:
    """The MIME type the first bytes say, or None when they say nothing.

    The browser's `Content-Type` is whatever the client chose to send; what is stored and
    served is decided here so a script dressed as a picture is never rendered as one.
    """
    head = data[:2048]
    for magic, mime in _SIGNATURES:
        if head.startswith(magic):
            return mime
    if head[:4] == b"RIFF" and len(head) >= 12:
        return {b"WEBP": "image/webp", b"WAVE": "audio/wav", b"AVI ": "video/x-msvideo"}.get(
            head[8:12]
        )
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand.startswith(b"M4A"):
            return "audio/mp4"
        return "video/quicktime" if brand.startswith(b"qt") else "video/mp4"
    if head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg"
    if head[:2] in (b"\xff\xf1", b"\xff\xf9"):
        return "audio/aac"
    suffix = Path(name).suffix.lower()
    if head.startswith(b"PK\x03\x04"):
        return _ZIP_SUFFIXES.get(suffix, "application/zip")
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return _OLE_SUFFIXES.get(suffix, "application/x-ole-storage")
    text = head.lstrip(b"\xef\xbb\xbf\xff\xfe \t\r\n").lower()
    if text.startswith(b"<svg") or (text.startswith(b"<?xml") and b"<svg" in text):
        return "image/svg+xml"
    if _MARKUP_HEAD.search(text):
        return "text/html"
    return None


def detected_mime(name: str, declared: str | None, data: bytes) -> str:
    """The type to record for an upload: what the bytes say, else the declared type when it
    is harmless.

    A declared picture type the bytes do not confirm becomes `application/octet-stream`, so
    nothing downstream (the model's picture hand-off, inline serving) trusts it.
    """
    declared = (declared or "").split(";")[0].strip().lower()
    found = sniff(name, data)
    if found:
        # An `.m4a`/`.webm` audio file shares its container with video; the client knows.
        if found.startswith("video/") and declared.startswith("audio/"):
            return "audio/" + found.split("/", 1)[1]
        return found
    if declared.startswith("image/") or declared in _ACTIVE_TYPES:
        return "application/octet-stream"
    return declared or "application/octet-stream"


def served_as(name: str, mime: str, data: bytes) -> tuple[str, bool]:
    """`(media type, inline)` for a download: inline only when the bytes prove a type a browser
    renders as data.

    Anything else goes as an attachment; a type a browser would execute (HTML, SVG) is
    served as an opaque stream.
    """
    found = sniff(name, data)
    if found and (found in INLINE_TYPES or found.startswith(("audio/", "video/"))):
        return found, True
    mime = (mime or "").split(";")[0].strip().lower()
    if not mime or mime.startswith("image/") or mime in _ACTIVE_TYPES:
        return "application/octet-stream", False
    return mime, False


def download_headers(media: str, inline: bool, filename: str) -> dict[str, str]:
    """Headers for a stored file's bytes: disposition, no type sniffing, no script or origin."""
    # RFC 5987: non-ASCII filenames are percent-encoded in this header.
    headers = {
        "Content-Disposition": (
            f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(filename)}"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    # `sandbox` denies a rendered response script, forms and its origin. A PDF is exempt:
    # browsers show PDFs through a plugin that a sandboxed document may not load.
    if media != "application/pdf":
        headers["Content-Security-Policy"] = "sandbox"
    return headers


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
        log.info("pdf unreadable: %s", exc)
        raise RuntimeError("손상되었거나 PDF 형식이 아닌 파일입니다.") from None
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            body = (page.extract_text() or "").strip()
            if body:
                # Page markers survive chunking and let citations name the page.
                pages.append(f"[페이지 {i + 1}]\n{body}")
        except Exception as exc:  # noqa: BLE001 — one bad page must not lose the rest
            log.info("pdf page %d unreadable: %s", i, exc)
    text = "\n\n".join(pages)
    if not text.strip():
        raise RuntimeError("텍스트를 추출하지 못했습니다. 스캔본이라면 OCR 이 필요합니다.")
    return text


def _from_docx(data: bytes) -> str:
    """Paragraph text from word/document.xml."""
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
    """OWPML: `<hp:t>` runs per `<hp:p>` in `Contents/section*.xml`."""
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


#: HWP 5.x record header: 32 bits — tag 10, level 10, size 12; size 0xFFF means
#: the real size follows as 32 bits.
_HWPTAG_PARA_TEXT = 67

#: Control characters interleaved with body text. Extended controls (tables,
#: images) occupy 16 bytes.
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
    """Decodes one HWPTAG_PARA_TEXT record: UTF-16LE with control characters mixed in."""
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
    """HWP 5.x body text from the OLE compound file's BodyText streams."""
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
    ".txt",
    ".md",
    ".markdown",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".log",
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".go",
    ".rs",
    ".rb",
    ".sh",
    ".sql",
    ".html",
    ".css",
    ".xml",
    ".tex",
    ".r",
}


def is_speech(mime: str) -> bool:
    """Whether the MIME type is audio or video."""
    return mime.startswith(("audio/", "video/"))


async def text_of(name: str, mime: str, data: bytes) -> str:
    """The file as text, transcribing audio/video when a backend is configured. Routes call this."""
    if not is_speech(mime):
        return extract_text(name, mime, data)
    if not await transcribe.available():
        return extract_text(name, mime, data)
    if len(data) > transcribe.MAX_BYTES:
        limit = transcribe.MAX_BYTES // (1024 * 1024)
        raise RuntimeError(f"녹음이 너무 깁니다. {limit}MB 이하로 나눠 올려 주세요.")
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
        # A viewable picture has no text; `workspace_context.reads_pictures`
        # decides per turn whether it is sent.
        if pictures.can_be_seen(mime, len(data)):
            return ""
        raise RuntimeError(
            "이 그림은 읽을 수 없습니다. PNG·JPEG·GIF·WebP 로, 4MB 이하로 올려 주세요."
        )
    if is_speech(mime):
        # Reached only when transcription is unavailable; see `text_of`.
        raise RuntimeError(
            "말소리를 글로 옮기는 기능이 꺼져 있어 이 파일을 읽지 못했습니다. "
            "관리자에게 요청하거나, 회의록 텍스트를 올려 주세요."
        )

    # Unknown extension: try text, rejected if mostly unprintable.
    text = _from_text(data)
    printable = sum(1 for ch in text[:2000] if ch.isprintable() or ch.isspace())
    if printable < len(text[:2000]) * 0.8:
        raise RuntimeError("읽을 수 없는 형식입니다. 텍스트나 PDF 로 올려 주세요.")
    return text
