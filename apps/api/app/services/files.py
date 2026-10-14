"""Upload storage (per-user dirs) and stdlib/pure-Python text extraction."""

from __future__ import annotations

import asyncio
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

#: chars per token estimate (warnings only)
_CHARS_PER_TOKEN = 3.0


def storage_root() -> Path:
    root = Path(settings.file_storage_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root


def remove_user_files(user_id: str) -> int:
    """Delete one account's directory; returns bytes freed."""
    root = storage_root()
    # user_id from URL: only direct children of the root
    directory = os.path.normpath(os.path.join(root, user_id))
    if os.path.dirname(directory) != os.path.normpath(root) or not os.path.isdir(directory):
        return 0
    freed = sum(p.stat().st_size for p in Path(directory).rglob("*") if p.is_file())
    shutil.rmtree(directory, ignore_errors=True)
    return freed


def safe_name(name: str) -> str:
    """Readable original name, useless as a path."""
    cleaned = unicodedata.normalize("NFC", name).replace("\x00", "")
    cleaned = re.sub(r"[/\\]", "_", cleaned).strip() or "file"
    return cleaned[:200]


def write_blob(user_id: str, file_id: str, name: str, data: bytes) -> str:
    """Write the blob; returns <user_id>/<file_id>_<name>."""
    directory = storage_root() / user_id
    directory.mkdir(parents=True, exist_ok=True)
    key = f"{user_id}/{file_id}_{safe_name(name)}"
    (storage_root() / key).write_bytes(data)
    return key


def read_blob(key: str) -> bytes:
    return (storage_root() / key).read_bytes()


def blob_size(key: str) -> int:
    """Size on disk."""
    return (storage_root() / key).stat().st_size


def copy_blob(user_id: str, file_id: str, name: str, source_key: str) -> str:
    """Copy a blob to a new key without loading it."""
    root = storage_root()
    key = f"{user_id}/{file_id}_{safe_name(name)}"
    destination = root / key
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(root / source_key, destination)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    return key


def delete_blob(key: str) -> None:
    try:
        (storage_root() / key).unlink(missing_ok=True)
    except OSError as exc:
        log.warning("could not remove blob %s: %s", key, exc)


#: inline-renderable types; never SVG/HTML (script on the API origin)
INLINE_TYPES = frozenset(
    {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp", "application/pdf"}
)

#: types a browser would execute or style
_ACTIVE_TYPES = frozenset(
    {"text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml"}
)

#: zip/OLE containers: told apart by name only
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

#: leading bytes that settle the type
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
    """MIME from magic bytes, or None; client Content-Type never trusted."""
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
    """Recorded type: sniffed, else declared if harmless; unconfirmed images → octet-stream."""
    declared = (declared or "").split(";")[0].strip().lower()
    found = sniff(name, data)
    if found:
        # m4a/webm audio shares a video container
        if found.startswith("video/") and declared.startswith("audio/"):
            return "audio/" + found.split("/", 1)[1]
        return found
    if declared.startswith("image/") or declared in _ACTIVE_TYPES:
        return "application/octet-stream"
    return declared or "application/octet-stream"


def served_as(name: str, mime: str, data: bytes) -> tuple[str, bool]:
    """(media type, inline): inline only for sniffed data types; executables opaque."""
    found = sniff(name, data)
    if found and (found in INLINE_TYPES or found.startswith(("audio/", "video/"))):
        return found, True
    mime = (mime or "").split(";")[0].strip().lower()
    if not mime or mime.startswith("image/") or mime in _ACTIVE_TYPES:
        return "application/octet-stream", False
    return mime, False


def download_headers(media: str, inline: bool, filename: str) -> dict[str, str]:
    """Stored-file headers: disposition, nosniff, no script/origin."""
    # RFC 5987 filename encoding
    headers = {
        "Content-Disposition": (
            f"{'inline' if inline else 'attachment'}; filename*=UTF-8''{quote(filename)}"
        ),
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    # sandbox CSP denies script/forms/origin; PDF exempt (plugin blocked in sandbox)
    if media != "application/pdf":
        headers["Content-Security-Policy"] = "sandbox"
    return headers


def estimate_tokens(text: str) -> int:
    return int(len(text) / _CHARS_PER_TOKEN)


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
                # page markers survive chunking for citations
                pages.append(f"[페이지 {i + 1}]\n{body}")
        except Exception as exc:  # noqa: BLE001 — one bad page must not lose the rest
            log.info("pdf page %d unreadable: %s", i, exc)
    text = "\n\n".join(pages)
    if not text.strip():
        raise RuntimeError("텍스트를 추출하지 못했습니다. 스캔본이라면 OCR 이 필요합니다.")
    return text


#: Office/HWPX unpack limits (zip bomb)
_MAX_UNPACKED = 64 * 1024 * 1024
_MAX_ENTRIES = 4_000
_MAX_MEMBER = 32 * 1024 * 1024


def _open_zip(data: bytes) -> zipfile.ZipFile:
    """Archive after declared-size checks."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    infos = archive.infolist()
    if len(infos) > _MAX_ENTRIES or sum(info.file_size for info in infos) > _MAX_UNPACKED:
        archive.close()
        raise RuntimeError("압축 문서가 너무 큽니다.")
    return archive


def _member(archive: zipfile.ZipFile, name: str) -> str:
    """One member's text, capped at _MAX_MEMBER."""
    with archive.open(name) as handle:
        raw = handle.read(_MAX_MEMBER + 1)
    if len(raw) > _MAX_MEMBER:
        raise RuntimeError("압축 문서의 항목이 너무 큽니다.")
    return raw.decode("utf-8", errors="replace")


def _local(tag: str) -> str:
    """Tag without namespace."""
    return tag.rsplit("}", 1)[-1]


def _xml_root(archive: zipfile.ZipFile, name: str):
    """Parsed member, or None if not well-formed."""
    import xml.etree.ElementTree as ET

    try:
        return ET.fromstring(_member(archive, name))
    except ET.ParseError:
        return None


def _part_targets(archive: zipfile.ZipFile, owner: str) -> dict[str, str]:
    """Relationship id → archive path for one part."""
    folder, _, base = owner.rpartition("/")
    rels = f"{folder}/_rels/{base}.rels"
    if rels not in archive.namelist():
        return {}
    root = _xml_root(archive, rels)
    if root is None:
        return {}
    out: dict[str, str] = {}
    for rel in root:
        rid, target = rel.get("Id"), rel.get("Target") or ""
        if not rid or rel.get("TargetMode") == "External":
            continue
        if target.startswith("/"):
            path = target.lstrip("/")
        else:
            parts = folder.split("/") if folder else []
            for piece in target.split("/"):
                if piece == "..":
                    if parts:
                        parts.pop()
                elif piece and piece != ".":
                    parts.append(piece)
            path = "/".join(parts)
        out[rid] = path
    return out


def _numbered(names: list[str], pattern: str) -> list[str]:
    """Members matching pattern, numeric order."""
    found = [(int(m.group(1)), n) for n in names if (m := re.fullmatch(pattern, n))]
    return [n for _, n in sorted(found)]


def _ordered_parts(archive: zipfile.ZipFile, owner: str, list_tag: str, pattern: str):
    """(name, path) of parts owner lists, falling back to numeric order."""
    names = archive.namelist()
    root = _xml_root(archive, owner) if owner in names else None
    targets = _part_targets(archive, owner)
    out: list[tuple[str, str]] = []
    if root is not None:
        for el in root.iter():
            if _local(el.tag) != list_tag:
                continue
            rid = next((v for k, v in el.attrib.items() if _local(k) == "id" and "}" in k), None)
            path = targets.get(rid or "")
            if path and path in names:
                out.append((el.get("name") or "", path))
    return out or [("", n) for n in _numbered(names, pattern)]


def _run_text(el) -> str:
    """Text of a WordprocessingML/DrawingML subtree."""
    parts: list[str] = []
    for node in el.iter():
        tag = _local(node.tag)
        if tag == "t" and node.text:
            parts.append(node.text)
        elif tag == "tab":
            parts.append("\t")
        elif tag in {"br", "cr"}:
            parts.append("\n")
    return "".join(parts)


def _from_docx(data: bytes) -> str:
    """Paragraphs from word/document.xml; table rows tab-separated."""
    with _open_zip(data) as archive:
        try:
            root = _xml_root(archive, "word/document.xml")
        except KeyError:
            raise RuntimeError("docx 구조를 읽지 못했습니다.") from None
    if root is None:
        raise RuntimeError("docx 구조를 읽지 못했습니다.")
    lines: list[str] = []

    def walk(el) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag == "p":
                lines.append(_run_text(child))
            elif tag == "tbl":
                for row in (r for r in child.iter() if _local(r.tag) == "tr"):
                    cells = [c for c in row if _local(c.tag) == "tc"]
                    lines.append(
                        "\t".join(
                            " ".join(_run_text(p) for p in c.iter() if _local(p.tag) == "p").strip()
                            for c in cells
                        )
                    )
            else:
                walk(child)

    walk(root)
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _from_pptx(data: bytes) -> str:
    """Slide text in order."""
    with _open_zip(data) as archive:
        slides = _ordered_parts(
            archive, "ppt/presentation.xml", "sldId", r"ppt/slides/slide(\d+)\.xml"
        )
        out = []
        for i, (_, name) in enumerate(slides, 1):
            root = _xml_root(archive, name)
            if root is None:
                continue
            paragraphs = [_run_text(p) for p in root.iter() if _local(p.tag) == "p"]
            body = "\n".join(p for p in paragraphs if p.strip()).strip()
            if body:
                out.append(f"[슬라이드 {i}]\n{body}")
    return "\n\n".join(out).strip()


def _column_index(ref: str) -> int | None:
    """0-based column of a cell ref like C7, or None."""
    m = re.match(r"([A-Z]{1,3})\d", ref or "")
    if not m:
        return None
    n = 0
    for ch in m.group(1):
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def _string_item(si) -> str:
    """Shared/inline string text, phonetic guides omitted."""
    parts: list[str] = []

    def walk(el) -> None:
        for child in el:
            tag = _local(child.tag)
            if tag == "rPh":
                continue
            if tag == "t":
                parts.append(child.text or "")
            else:
                walk(child)

    walk(si)
    return "".join(parts)


def _from_xlsx(data: bytes) -> str:
    """Sheet values by sheet, workbook order; formulas unevaluated, empty cells keep columns."""
    with _open_zip(data) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = _xml_root(archive, "xl/sharedStrings.xml")
            if root is not None:
                shared = [_string_item(si) for si in root if _local(si.tag) == "si"]

        sheets = _ordered_parts(
            archive, "xl/workbook.xml", "sheet", r"xl/worksheets/sheet(\d+)\.xml"
        )
        out = []
        for index, (title, name) in enumerate(sheets, 1):
            root = _xml_root(archive, name)
            if root is None:
                continue
            rows = []
            for row in (r for r in root.iter() if _local(r.tag) == "row"):
                cells: list[str] = []
                for cell in (c for c in row if _local(c.tag) == "c"):
                    col = _column_index(cell.get("r", ""))
                    if col is not None and col > len(cells):
                        cells.extend([""] * (col - len(cells)))
                    kind = cell.get("t", "")
                    value = next((v for v in cell if _local(v.tag) == "v"), None)
                    if kind == "inlineStr":
                        inline = next((v for v in cell if _local(v.tag) == "is"), None)
                        cells.append(_string_item(inline) if inline is not None else "")
                    elif value is None:
                        cells.append("")
                    elif kind == "s":  # shared-strings index
                        try:
                            cells.append(shared[int(value.text or "")])
                        except (ValueError, IndexError):
                            cells.append("")
                    elif kind == "b":
                        cells.append("TRUE" if (value.text or "") == "1" else "FALSE")
                    else:
                        cells.append(value.text or "")
                if any(c.strip() for c in cells):
                    rows.append("\t".join(cells).rstrip("\t"))
            if rows:
                label = f"[시트 {index}: {title}]" if title else f"[시트 {index}]"
                out.append(label + "\n" + "\n".join(rows))
    return "\n\n".join(out).strip()


def _from_hwpx(data: bytes) -> str:
    """OWPML <hp:t> runs per <hp:p> in section*.xml."""
    import xml.etree.ElementTree as ET

    try:
        archive = _open_zip(data)
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

        # local-name match (namespaces vary); each run read once from its nearest paragraph
        def local(tag: str) -> str:
            return tag.rsplit("}", 1)[-1]

        def runs_of(para) -> list[str]:
            found: list[str] = []
            stack = list(para)
            while stack:
                node = stack.pop(0)
                name = local(node.tag)
                if name == "p":
                    continue
                if name == "t" and node.text:
                    found.append(node.text)
                stack[:0] = list(node)
            return found

        for para in root.iter():
            if local(para.tag) != "p":
                continue
            runs = runs_of(para)
            if runs:
                paragraphs.append("".join(runs))
    return "\n".join(paragraphs)


#: HWP 5.x record header: tag 10, level 10, size 12 bits; 0xFFF → 32-bit size follows
_HWPTAG_PARA_TEXT = 67

#: inline control chars; extended controls take 16 bytes
_HWP_EXTENDED_CONTROLS = frozenset({1, 2, 3, 11, 12, 14, 15, 16, 17, 18, 21, 22, 23})
_HWP_INLINE_CONTROLS = frozenset({4, 5, 6, 7, 8, 19, 20})


def _hwp_records(stream: bytes):
    """Yield (tag, payload) in order."""
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
    """Decode HWPTAG_PARA_TEXT (UTF-16LE with controls)."""
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
    """HWP 5.x body text from BodyText streams."""
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
        # 32-byte signature, 4 version, flags: bit0 compressed, bit1 encrypted
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
    """Whether MIME is audio or video."""
    return mime.startswith(("audio/", "video/"))


async def text_of(name: str, mime: str, data: bytes) -> str:
    """File as text, transcribing audio/video if configured."""
    if not is_speech(mime):
        # CPU-bound parse in a worker thread
        return await asyncio.to_thread(extract_text, name, mime, data)
    if not await transcribe.available():
        # CPU-bound parse in a worker thread
        return await asyncio.to_thread(extract_text, name, mime, data)
    if len(data) > transcribe.MAX_BYTES:
        limit = transcribe.MAX_BYTES // (1024 * 1024)
        raise RuntimeError(f"녹음이 너무 깁니다. {limit}MB 이하로 나눠 올려 주세요.")
    return storable(await transcribe.transcribe(data, name))


def storable(text: str) -> str:
    """Text Postgres can store: surrogate pairs joined, lone surrogates and NULs dropped.

    Some HWP/PDF parsers hand back UTF-16 surrogates (and the odd NUL); asyncpg then refuses
    the whole row and the upload fails with a 500."""
    if not text:
        return text
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        # pairs → their character; what stays unpaired is dropped
        text = text.encode("utf-16", "surrogatepass").decode("utf-16", "ignore")
        text = "".join(ch for ch in text if not 0xD800 <= ord(ch) <= 0xDFFF)
    return text.replace("\x00", "")


def extract_text(name: str, mime: str, data: bytes) -> str:
    """Best-effort model text; raises with a Korean reason."""
    return storable(_extract_text(name, mime, data))


def _extract_text(name: str, mime: str, data: bytes) -> str:
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
        # pictures have no text; reads_pictures decides per turn
        if pictures.can_be_seen(mime, len(data)):
            return ""
        raise RuntimeError(
            "이 그림은 읽을 수 없습니다. PNG·JPEG·GIF·WebP 로, 4MB 이하로 올려 주세요."
        )
    if is_speech(mime):
        # only when transcription is unavailable
        raise RuntimeError(
            "말소리를 글로 옮기는 기능이 꺼져 있어 이 파일을 읽지 못했습니다. "
            "관리자에게 요청하거나, 회의록 텍스트를 올려 주세요."
        )

    # unknown extension: try text, reject if mostly unprintable
    text = _from_text(data)
    printable = sum(1 for ch in text[:2000] if ch.isprintable() or ch.isspace())
    if printable < len(text[:2000]) * 0.8:
        raise RuntimeError("읽을 수 없는 형식입니다. 텍스트나 PDF 로 올려 주세요.")
    return text
