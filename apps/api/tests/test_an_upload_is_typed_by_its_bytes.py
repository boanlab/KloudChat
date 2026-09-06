"""An upload's type comes from its bytes, and a download renders in place only when the
bytes prove a type a browser shows as data.

The client's `Content-Type` is a claim. Trusting it let a script dressed as `image/svg+xml`
or `image/png` be served inline on the API's own origin.
"""

from __future__ import annotations

import base64
import zlib

import pytest

from app.models.workspace import StoredFile
from app.routers import branding, workspace
from app.services import files

_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(document.domain)</script></svg>'
_HTML = b"<!DOCTYPE html><html><body><script>fetch('/api/me')</script></body></html>"


def _zip(name: str) -> bytes:
    return b"PK\x03\x04" + zlib.compress(name.encode())


@pytest.mark.parametrize(
    ("name", "data", "expected"),
    [
        ("a.png", _PNG, "image/png"),
        ("a.jpg", b"\xff\xd8\xff\xe0" + b"\0" * 16, "image/jpeg"),
        ("a.gif", b"GIF89a" + b"\0" * 16, "image/gif"),
        ("a.webp", b"RIFF\x10\0\0\0WEBPVP8 ", "image/webp"),
        ("a.wav", b"RIFF\x10\0\0\0WAVEfmt ", "audio/wav"),
        ("a.pdf", b"%PDF-1.7\n%\xe2\xe3\xcf\xd3", "application/pdf"),
        ("a.mp3", b"ID3\x04\0\0\0\0\0\0", "audio/mpeg"),
        ("a.m4a", b"\0\0\0\x18ftypM4A \0\0\0\0", "audio/mp4"),
        ("a.mp4", b"\0\0\0\x18ftypisom\0\0\0\0", "video/mp4"),
        ("a.webm", b"\x1aE\xdf\xa3\x01\0\0\0", "video/webm"),
        ("a.docx", _zip("word/document.xml"), files._ZIP_SUFFIXES[".docx"]),
        ("a.hwpx", _zip("Contents/section0.xml"), "application/hwp+zip"),
        ("a.hwp", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 8, "application/x-hwp"),
        ("logo.png", _SVG, "image/svg+xml"),
        ("logo.png", b"\xef\xbb\xbf  <?xml version='1.0'?>\n<svg></svg>", "image/svg+xml"),
        ("notes.png", _HTML, "text/html"),
        ("notes.txt", "회의록 초안\n".encode(), None),
        ("data.csv", b"a,b\n1,2\n", None),
    ],
)
def test_the_first_bytes_name_the_type(name, data, expected):
    assert files.sniff(name, data) == expected


def test_what_the_bytes_say_beats_what_the_client_declared():
    assert files.detected_mime("x.png", "image/png", _PNG) == "image/png"
    assert files.detected_mime("x.png", "image/png", _SVG) == "image/svg+xml"
    assert files.detected_mime("x.jpg", "image/jpeg", _PNG) == "image/png"
    # An `.m4a` shares its container with video; the declared side is kept.
    assert files.detected_mime("x.m4a", "audio/x-m4a", b"\0\0\0\x18ftypisom\0\0\0\0") == "audio/mp4"


def test_an_unconfirmed_picture_or_page_type_is_not_recorded():
    """`can_be_seen` and inline serving read the stored type; a claim the bytes do not back
    must not reach them."""
    assert files.detected_mime("x.png", "image/png", b"not a picture") == "application/octet-stream"
    assert files.detected_mime("x.svg", "image/svg+xml", b"plain") == "application/octet-stream"
    assert files.detected_mime("x.html", "text/html", b"plain") == "application/octet-stream"
    # Harmless declared types survive when the bytes are silent.
    assert files.detected_mime("x.txt", "text/plain; charset=utf-8", b"plain") == "text/plain"
    assert files.detected_mime("x.csv", "text/csv", b"a,b") == "text/csv"
    assert files.detected_mime("x.bin", "", b"\0\1\2") == "application/octet-stream"


def test_only_proven_pictures_media_and_pdf_render_in_place():
    assert files.served_as("a.png", "image/png", _PNG) == ("image/png", True)
    assert files.served_as("a.pdf", "application/pdf", b"%PDF-1.4") == ("application/pdf", True)
    assert files.served_as("a.mp3", "audio/mpeg", b"ID3\x04") == ("audio/mpeg", True)
    # SVG and HTML never render in place, whatever the row says, and leave as opaque bytes.
    assert files.served_as("a.svg", "image/svg+xml", _SVG) == ("application/octet-stream", False)
    assert files.served_as("a.png", "image/png", _SVG) == ("application/octet-stream", False)
    assert files.served_as("a.png", "image/png", _HTML) == ("application/octet-stream", False)
    # A stale row claiming a picture the bytes do not back is an opaque attachment too.
    assert files.served_as("a.png", "image/png", b"nothing") == ("application/octet-stream", False)
    # Documents keep their type but go as attachments.
    assert files.served_as("a.txt", "text/plain", b"hello") == ("text/plain", False)


def test_a_rendered_download_carries_no_script_and_no_type_guessing():
    inline = files.download_headers("image/png", True, "그림.png")
    assert inline["Content-Disposition"].startswith("inline; filename*=UTF-8''%EA%B7%B8")
    assert inline["X-Content-Type-Options"] == "nosniff"
    assert inline["Content-Security-Policy"] == "sandbox"
    assert "no-store" in inline["Cache-Control"]
    # Browsers show PDFs through a plugin a sandboxed document may not load.
    assert "Content-Security-Policy" not in files.download_headers("application/pdf", True, "a.pdf")
    assert files.download_headers("text/plain", False, "a.txt")["Content-Disposition"].startswith(
        "attachment;"
    )


class _Db:
    def __init__(self, row):
        self.row = row

    async def get(self, model, item_id):
        return self.row if item_id == self.row.id else None


class _User:
    id = "owner"


@pytest.mark.asyncio
async def test_a_script_uploaded_as_a_picture_is_downloaded_not_shown(monkeypatch):
    row = StoredFile(id="f1", user_id="owner", name="logo.png", mime="image/png", storage_key="k")
    monkeypatch.setattr(workspace.file_service, "read_blob", lambda key: _SVG)

    response = await workspace.download_file("f1", _User(), _Db(row))

    assert response.media_type == "application/octet-stream"
    assert response.headers["content-disposition"].startswith("attachment;")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-security-policy"] == "sandbox"


@pytest.mark.asyncio
async def test_a_real_picture_still_shows_in_place(monkeypatch):
    row = StoredFile(id="f2", user_id="owner", name="pixel.png", mime="image/png", storage_key="k")
    monkeypatch.setattr(workspace.file_service, "read_blob", lambda key: _PNG)

    response = await workspace.download_file("f2", _User(), _Db(row))

    assert response.media_type == "image/png"
    assert response.headers["content-disposition"].startswith("inline;")
    assert response.headers["content-security-policy"] == "sandbox"


def test_the_logo_upload_checks_the_bytes_against_the_declared_type():
    """`upload_logo` refuses an SVG or HTML file sent with a picture type."""
    assert files.sniff("logo.png", _SVG) not in branding.ALLOWED
    assert files.sniff("logo.png", _PNG) in branding.ALLOWED
