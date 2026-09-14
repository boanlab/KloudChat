"""Stored code can be downloaded without re-generation, execution or format guessing."""

from copy import deepcopy
from urllib.parse import unquote

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from app.core import deps
from app.core.db import get_session
from app.models.user import User
from app.models.workspace import Artifact, ArtifactKind
from app.routers import workspace
from app.services.tools.builtin import CREATE_ARTIFACT


class _ReadOnlyDb:
    def __init__(self, artifact):
        self.artifact = artifact
        self.reads = []

    async def get(self, model, item_id):
        self.reads.append((model, item_id))
        return self.artifact

    def add(self, _row):
        pytest.fail("Downloading stored code must not write to the database")

    async def commit(self):
        pytest.fail("Downloading stored code must not commit")


def _artifact(language="csv", *, title="합성 자료", content="label,value\r\n가,1\r\n"):
    data = {"content": content}
    if language is not None:
        data["language"] = language
    return Artifact(
        id="artifact-1", user_id="owner", kind=ArtifactKind.code, title=title, data=data
    )


def _user(user_id="owner"):
    return User(
        id=user_id, email="synthetic@example.test", password_hash="unused", name="Synthetic"
    )


def _filename(response):
    disposition = response.headers["content-disposition"]
    assert disposition.startswith("attachment; filename*=UTF-8''")
    return unquote(disposition.split("''", 1)[1])


@pytest.mark.asyncio
@pytest.mark.parametrize("language,suffix,media", [
    ("csv", "csv", "text/csv"),
    ("tsv", "tsv", "text/tab-separated-values"),
    ("json", "json", "application/json"),
    ("python", "py", "text/plain"),
    ("py", "py", "text/plain"),
    ("javascript", "js", "text/plain"),
    ("typescript", "ts", "text/plain"),
    ("yaml", "yaml", "text/plain"),
    ("yml", "yml", "text/plain"),
    ("bash", "sh", "text/plain"),
    ("sql", "sql", "text/plain"),
    ("markdown", "md", "text/plain"),
    ("text", "txt", "text/plain"),
    (None, "txt", "text/plain"),
    ("", "txt", "text/plain"),
    ("unknown-format", "txt", "text/plain"),
    ("html", "txt", "text/plain"),
])
async def test_source_download_keeps_exact_saved_bytes_and_explicit_format(language, suffix, media):
    artifact = _artifact(language)
    original = deepcopy(artifact.data)
    db = _ReadOnlyDb(artifact)
    response = await workspace.export_artifact(artifact.id, _user(), db, format="source")
    assert response.status_code == 200
    assert response.body == artifact.data["content"].encode("utf-8")
    assert response.headers["content-type"].split(";", 1)[0] == media
    assert response.headers["x-content-type-options"] == "nosniff"
    assert _filename(response) == f"합성 자료.{suffix}"
    assert artifact.data == original
    assert db.reads == [(Artifact, artifact.id)]


@pytest.mark.asyncio
@pytest.mark.parametrize("title,language,wanted", [
    ("data.csv", "csv", "data.csv"),
    ("data.CSV", " CSV ", "data.csv"),
    ("data.csv", None, "data.csv.txt"),
    ("../../자료\r\n.csv", "csv", "_.._자료_.csv"),
    ("", None, "code.txt"),
])
async def test_filename_uses_safe_title_without_guessing_language(title, language, wanted):
    artifact = _artifact(language, title=title)
    response = await workspace.export_artifact(
        artifact.id, _user(), _ReadOnlyDb(artifact), "source"
    )
    assert _filename(response) == wanted
    assert "\r" not in response.headers["content-disposition"]
    assert "\n" not in response.headers["content-disposition"]


@pytest.mark.asyncio
@pytest.mark.parametrize("artifact,user", [(_artifact(), _user("other")), (None, _user())])
async def test_other_owner_and_absent_ids_are_indistinguishable(artifact, user):
    with pytest.raises(HTTPException) as error:
        await workspace.export_artifact("requested-id", user, _ReadOnlyDb(artifact), "source")
    assert error.value.status_code == 404
    assert error.value.detail == "not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [kind for kind in ArtifactKind if kind is not ArtifactKind.code])
async def test_source_export_does_not_admit_other_artifact_kinds(kind):
    artifact = _artifact()
    artifact.kind = kind
    with pytest.raises(HTTPException) as error:
        await workspace.export_artifact(artifact.id, _user(), _ReadOnlyDb(artifact), "source")
    assert error.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("format", ["docx", "pdf", "csv", "html"])
async def test_code_export_does_not_add_conversions(format):
    artifact = _artifact()
    with pytest.raises(HTTPException) as error:
        await workspace.export_artifact(artifact.id, _user(), _ReadOnlyDb(artifact), format)
    assert error.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("content", [None, 17, {"guess": "not source"}])
async def test_non_text_source_is_not_stringified(content):
    artifact = _artifact(content=content)
    with pytest.raises(HTTPException) as error:
        await workspace.export_artifact(artifact.id, _user(), _ReadOnlyDb(artifact), "source")
    assert error.value.status_code == 400


def test_tool_description_explains_the_optional_language_download_contract():
    language = CREATE_ARTIFACT.parameters["properties"]["language"]["description"]
    assert "확장자" in language
    assert "csv" in language and "json" in language and ".txt" in language
    assert "language" not in CREATE_ARTIFACT.parameters["required"]


@pytest.mark.asyncio
@pytest.mark.parametrize("caller,status", [("owner", 200), ("other", 404)])
@pytest.mark.parametrize("origin,allowed", [
    ("https://app.example.test", True), ("https://other.example.test", False),
])
async def test_http_source_endpoint_preserves_bytes_and_owner_boundary(
    caller, status, origin, allowed
):
    artifact = _artifact(content='"label","value"\r\n"가,나","=1+1"\r\n')
    db = _ReadOnlyDb(artifact)
    app = FastAPI()
    app.add_middleware(CORSMiddleware, allow_origins=["https://app.example.test"])
    app.add_api_route("/artifacts/{artifact_id}/export", workspace.export_artifact, methods=["GET"])
    app.dependency_overrides[deps.current_user] = lambda: _user(caller)
    app.dependency_overrides[get_session] = lambda: db
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get(
            f"/artifacts/{artifact.id}/export?format=source",
            headers={"Origin": origin},
        )
    assert response.status_code == status
    if status == 200:
        assert response.content == artifact.data["content"].encode("utf-8")
        assert response.headers["content-type"] == "text/csv; charset=utf-8"
        assert _filename(response) == "합성 자료.csv"
        assert response.headers.get("access-control-allow-origin") == (origin if allowed else None)
        assert response.headers.get("access-control-expose-headers") == "Content-Disposition"
    else:
        assert response.json() == {"detail": "not_found"}


@pytest.mark.asyncio
async def test_code_with_markup_is_downloaded_as_inert_text_without_rendering():
    source = '<script>throw new Error("must not execute")</script>'
    artifact = _artifact("html", title="page.html", content=source)
    response = await workspace.export_artifact(
        artifact.id, _user(), _ReadOnlyDb(artifact), "source"
    )
    assert response.body == source.encode()
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert _filename(response) == "page.html.txt"
