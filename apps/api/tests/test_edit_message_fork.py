"""Editing branches a saved prefix; it never rewrites or regenerates the original."""

import asyncio
import json
from copy import deepcopy
from datetime import timedelta

import pytest
from fastapi import HTTPException

from app.models.chat import ChatSession, Message, Role, SessionKind, TurnFailure
from app.models.governance import Governance
from app.models.user import User, utcnow
from app.models.workspace import Artifact, ArtifactKind, Job, StoredFile
from app.routers import sessions
from app.services import files


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return next(iter(self.rows), None)


class _Db:
    def __init__(self, source, messages, *, uploads=(), artifacts=(), jobs=()):
        self.source = source
        self.messages = list(messages)
        self.uploads = list(uploads)
        self.artifacts = list(artifacts)
        self.jobs = list(jobs)
        self.added = []
        self.committed = []
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit = False

    async def get(self, model, row_id):
        rows = [self.source, *self.messages, *self.uploads, *self.artifacts, *self.committed]
        return next((row for row in rows if isinstance(row, model) and row.id == row_id), None)

    async def exec(self, query):
        table = query.get_final_froms()[0].name
        params = query.compile().params
        values = list(params.values())
        if table == "sessions":
            assert query._for_update_arg is not None
            row = self.source
            return _Result([row] if row and row.id in values and row.user_id in values else [])
        if table == "messages":
            rows = [row for row in self.messages if row.session_id in values]
            return _Result(sorted(rows, key=lambda row: (row.created_at, row.id)))
        if table == "jobs":
            return _Result([
                row for row in self.jobs
                if row.session_id in values and row.status in ("queued", "running")
            ])
        if table in ("files", "artifacts"):
            ids = next((value for value in values if isinstance(value, list)), [])
            rows = self.uploads if table == "files" else self.artifacts
            return _Result([row for row in rows if row.id in ids and row.user_id in values])
        raise AssertionError(f"Unexpected query: {table}")

    def add(self, row):
        self.added.append(row)

    async def commit(self):
        if self.fail_commit:
            raise RuntimeError("synthetic commit failure")
        self.commits += 1
        self.committed.extend(self.added)

    async def rollback(self):
        self.rollbacks += 1
        self.added.clear()


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(files.settings, "file_storage_dir", str(tmp_path))
    monkeypatch.setattr(files.settings, "jwt_secret", "synthetic-message-edit-test-secret-20260914")
    monkeypatch.setattr(sessions, "_STOPPING", {})

    async def enabled():
        return ["chat"]

    monkeypatch.setattr(sessions.settings_store, "enabled_kinds", enabled)

    async def no_model(*_args, **_kwargs):
        pytest.fail("Fork must not obtain credentials, call models or query a remote index")

    monkeypatch.setattr(sessions.model_service, "list_models_for_egress", no_model)
    monkeypatch.setattr(sessions.litellm_service, "ensure_key", no_model)
    monkeypatch.setattr(sessions.index_client, "forget_collection", no_model)


def _user(user_id="owner"):
    return User(
        id=user_id, email="synthetic@example.test", password_hash="unused", name="Synthetic"
    )


def _source():
    return ChatSession(id="source", user_id="owner", title="Original", model="synthetic-model")


def _history(count=3):
    start = utcnow() - timedelta(days=1)
    return [
        Message(
            id=f"m{i}", session_id="source", role=Role.user if i % 2 == 0 else Role.assistant,
            content=f"saved-{i}", created_at=start + timedelta(seconds=i),
        )
        for i in range(count * 2)
    ]


def _upload(file_id, *, session_id="source", user_id="owner"):
    row = StoredFile(
        id=file_id, user_id=user_id, session_id=session_id, name=f"{file_id}.txt",
        mime="text/plain", size=4, text=f"extracted {file_id}", tokens=4, indexed_at=utcnow(),
    )
    row.storage_key = files.write_blob(user_id, file_id, row.name, b"body")
    return row


async def _fork(db, target="m2", user=None):
    return await sessions.fork_message("source", target, user or _user(), db)


@pytest.mark.asyncio
@pytest.mark.parametrize("target,prefix_length", [("m0", 0), ("m2", 2), ("m4", 4)])
async def test_first_middle_and_latest_user_create_a_new_prefix(target, prefix_length):
    source, history = _source(), _history()
    before = deepcopy([source.model_dump(), *[row.model_dump() for row in history]])
    db = _Db(source, history)
    result = await _fork(db, target)
    assert result.session.id != source.id
    assert result.session.model == source.model
    assert [m.content for m in result.session.messages] == [
        m.content for m in history[:prefix_length]
    ]
    assert not set(m.id for m in result.session.messages) & set(m.id for m in history)
    assert result.attachments == []
    assert db.commits == 1
    assert before == [source.model_dump(), *[row.model_dump() for row in history]]


@pytest.mark.asyncio
@pytest.mark.parametrize("source_user,target,code", [
    ("other", "m2", 404), ("owner", "absent", 404), ("owner", "m1", 422),
])
async def test_fork_target_validation_happens_before_any_write(source_user, target, code):
    source = _source()
    source.user_id = source_user
    db = _Db(source, _history())
    with pytest.raises(HTTPException) as error:
        await _fork(db, target)
    assert error.value.status_code == code
    assert db.added == [] and db.commits == 0


@pytest.mark.asyncio
async def test_absent_session_and_target_from_another_session_are_not_found():
    for source, history in [(None, _history()), (_source(), _history())]:
        if source:
            history[2].session_id = "another"
        db = _Db(source, history)
        with pytest.raises(HTTPException) as error:
            await _fork(db)
        assert error.value.status_code == 404
        assert db.added == []


@pytest.mark.asyncio
@pytest.mark.parametrize("busy", ["local", "pending", "job", "unanswered"])
async def test_running_or_pending_source_is_refused_without_canceling_it(busy):
    source, history = _source(), _history()
    jobs = []
    signal = asyncio.Event()
    if busy == "local":
        sessions._STOPPING[source.id] = {signal}
    elif busy == "pending":
        source.pending = {"request": "must not copy"}
    elif busy == "job":
        jobs.append(Job(user_id="owner", session_id=source.id, status="running"))
    else:
        history.pop()
    db = _Db(source, history, jobs=jobs)
    with pytest.raises(HTTPException) as error:
        await _fork(db)
    assert error.value.status_code == 409
    assert error.value.detail == "session_busy"
    assert db.added == [] and not signal.is_set()


@pytest.mark.asyncio
async def test_failed_latest_user_can_be_edited_and_tied_times_stay_ordered():
    source, history = _source(), _history()
    history.pop()
    history[-1].failure = TurnFailure.no_answer
    for message in history:
        message.created_at = history[0].created_at
    db = _Db(source, history)
    result = await _fork(db, "m4")
    cloned = [row for row in db.added if isinstance(row, Message)]
    assert [row.content for row in sorted(cloned, key=lambda m: (m.created_at, m.id))] == [
        m.content for m in history[:4]
    ]
    branch = next(row for row in db.added if isinstance(row, ChatSession))
    assert branch.pending is None and branch.id == result.session.id


@pytest.mark.asyncio
async def test_only_prefix_and_target_files_are_independent_copies():
    source, history = _source(), _history()
    prefix, target, future = (_upload(name) for name in ["prefix", "target", "future"])
    history[0].attachments = [{"id": prefix.id, "name": prefix.name}]
    history[2].attachments = [{"id": target.id, "name": target.name}]
    history[4].attachments = [{"id": future.id, "name": future.name}]
    original = deepcopy([row.model_dump() for row in [prefix, target, future]])
    db = _Db(source, history, uploads=[prefix, target, future])
    result = await _fork(db)
    clones = [row for row in db.added if isinstance(row, StoredFile)]
    assert len(clones) == 2
    assert {row.text for row in clones} == {prefix.text, target.text}
    copied_prefix = next(row for row in clones if row.text == prefix.text)
    copied_target = next(row for row in clones if row.text == target.text)
    assert copied_prefix.session_id == result.session.id
    assert copied_target.session_id is None
    assert result.session.messages[0].attachments[0]["id"] == copied_prefix.id
    assert [row.id for row in result.attachments] == [copied_target.id]
    assert result.attachment_id_map == {target.id: copied_target.id}
    assert all(
        row.indexed_at is None and row.project_id is None and row.agent_id is None for row in clones
    )
    for row in [prefix, target, future]:
        files.delete_blob(row.storage_key)
    assert all(files.read_blob(row.storage_key) == b"body" for row in clones)
    assert original == [row.model_dump() for row in [prefix, target, future]]


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", ["foreign", "row", "blob"])
async def test_unavailable_attachment_fails_without_partial_branch(missing):
    source, history = _source(), _history()
    first = _upload("first")
    second = _upload("second", user_id="other" if missing == "foreign" else "owner")
    history[0].attachments = [{"id": first.id}]
    history[2].attachments = [{"id": second.id}]
    if missing == "blob":
        files.delete_blob(second.storage_key)
    db = _Db(source, history, uploads=[first] if missing == "row" else [first, second])
    before_paths = {str(path) for path in files.storage_root().rglob("*") if path.is_file()}
    with pytest.raises(HTTPException) as error:
        await _fork(db)
    assert error.value.status_code == 404
    assert {str(path) for path in files.storage_root().rglob("*") if path.is_file()} == before_paths
    assert db.commits == 0 and db.added == []


@pytest.mark.asyncio
async def test_commit_failure_removes_all_new_blobs_and_leaves_original_rows():
    source, history = _source(), _history()
    uploaded = _upload("original")
    history[2].attachments = [{"id": uploaded.id}]
    db = _Db(source, history, uploads=[uploaded])
    db.fail_commit = True
    before = deepcopy([source.model_dump(), uploaded.model_dump()])
    with pytest.raises(RuntimeError, match="synthetic"):
        await _fork(db)
    assert db.rollbacks == 1 and db.added == []
    paths = [str(path.relative_to(files.storage_root()))
             for path in files.storage_root().rglob("*") if path.is_file()]
    assert paths == [uploaded.storage_key]
    assert before == [source.model_dump(), uploaded.model_dump()]


@pytest.mark.asyncio
async def test_prefix_artifact_snapshot_survives_original_blob_and_row_deletion():
    source, history = _source(), _history()
    artifact = Artifact(
        id="artifact-original", user_id="owner", session_id=source.id, kind=ArtifactKind.code,
        title="snapshot", data={"content": "saved code", "language": "python"}, version=3,
    )
    artifact.storage_key = files.write_blob("owner", artifact.id, "saved.txt", b"snapshot")
    history[1].artifact_ids = [artifact.id]
    history[1].usage = {"credits": 10}
    history[1].variants = [{"content": "alternative"}]
    history[1].steps = [{"type": "approval", "token": "synthetic-old-approval"}]
    source.artifact_id = artifact.id
    source.index_key = "original-index"
    db = _Db(source, history, artifacts=[artifact])
    result = await _fork(db)
    copied = next(row for row in db.added if isinstance(row, Artifact))
    assert copied.id != artifact.id and copied.session_id == result.session.id
    assert copied.data == artifact.data and copied.data is not artifact.data
    assert copied.version == 1
    files.delete_blob(artifact.storage_key)
    db.artifacts.clear()
    assert files.read_blob(copied.storage_key) == b"snapshot"
    assert result.session.messages[1].artifact_ids == [copied.id]
    assert result.session.messages[1].steps is None
    assert result.session.messages[1].usage is None
    assert result.session.messages[1].variants is None
    clone_session = next(row for row in db.added if isinstance(row, ChatSession))
    assert clone_session.artifact_id is None and clone_session.index_key is None


@pytest.mark.asyncio
async def test_foreign_artifact_is_not_copied_and_nonchat_is_not_forked():
    source, history = _source(), _history()
    artifact = Artifact(id="a", user_id="other", kind=ArtifactKind.code, data={"content": "other"})
    history[1].artifact_ids = [artifact.id]
    db = _Db(source, history, artifacts=[artifact])
    with pytest.raises(HTTPException) as error:
        await _fork(db)
    assert error.value.status_code == 404 and db.added == []
    source.kind = SessionKind.report
    with pytest.raises(HTTPException) as error:
        await _fork(db)
    assert error.value.status_code == 422 and db.added == []


@pytest.mark.asyncio
async def test_partial_blob_copy_failure_removes_destination_but_not_source(monkeypatch):
    source, history = _source(), _history()
    uploaded = _upload("original")
    history[2].attachments = [{"id": uploaded.id}]
    db = _Db(source, history, uploads=[uploaded])

    def incomplete(_source, destination):
        destination.write_bytes(b"partial")
        raise OSError("synthetic disk failure")

    monkeypatch.setattr(files.shutil, "copyfile", incomplete)
    with pytest.raises(OSError, match="synthetic"):
        await _fork(db)
    assert files.read_blob(uploaded.storage_key) == b"body"
    paths = [path for path in files.storage_root().rglob("*") if path.is_file()]
    assert paths == [files.storage_root() / uploaded.storage_key]
    assert db.added == [] and db.commits == 0


@pytest.mark.asyncio
async def test_source_privacy_consent_cannot_be_replayed_in_the_new_branch():
    from test_privacy import _external_model

    source, history = _source(), _history()
    user = _user()
    db = _Db(source, history)
    result = await _fork(db)
    branch = next(row for row in db.added if isinstance(row, ChatSession))
    model = _external_model("synthetic/model")
    policy = Governance(external_data_guard=True)
    sources = {"message": "연락처: synthetic-person@example.test"}
    first = await sessions._resolve_privacy(
        user=user, session=source, policy=policy, catalogue=[model], requested=[model],
        sources=sources, explicit_action=None, decision_token=None,
    )
    assert first.status_code == 409
    old_token = json.loads(first.body)["decisionToken"]
    retried = await sessions._resolve_privacy(
        user=user, session=branch, policy=policy, catalogue=[model], requested=[model],
        sources=sources, explicit_action="mask_external", decision_token=old_token,
    )
    assert retried.status_code == 409
    new_token = json.loads(retried.body)["decisionToken"]
    assert new_token != old_token
    accepted = await sessions._resolve_privacy(
        user=user, session=branch, policy=policy, catalogue=[model], requested=[model],
        sources=sources, explicit_action="mask_external", decision_token=new_token,
    )
    assert accepted.action == "mask_external"
    assert result.session.id == branch.id


@pytest.mark.asyncio
async def test_one_file_shared_by_prefix_and_target_is_copied_once():
    source, history = _source(), _history()
    shared = _upload("shared")
    history[0].attachments = [{"id": shared.id}]
    history[2].attachments = [{"id": shared.id}, {"id": shared.id}]
    db = _Db(source, history, uploads=[shared])
    result = await _fork(db)
    copies = [row for row in db.added if isinstance(row, StoredFile)]
    assert len(copies) == 1
    assert copies[0].session_id == result.session.id
    assert result.attachment_id_map == {shared.id: copies[0].id}
    assert len(result.attachments) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("oversized", ["file", "combined", "artifact"])
async def test_actual_total_blob_size_is_bounded_before_any_write(monkeypatch, oversized):
    source, history = _source(), _history()
    uploaded = _upload("original")
    history[2].attachments = [{"id": uploaded.id}]
    artifact = Artifact(id="original-artifact", user_id="owner", kind=ArtifactKind.code)
    artifact.storage_key = files.write_blob("owner", artifact.id, "original.txt", b"body")
    history[1].artifact_ids = [artifact.id]
    # Deliberately understate StoredFile.size: only actual blob size is authoritative.
    file_size = 1024 * 1024 + 1 if oversized == "file" else 600 * 1024
    artifact_size = 1024 * 1024 + 1 if oversized == "artifact" else 600 * 1024
    (files.storage_root() / uploaded.storage_key).write_bytes(b"f" * file_size)
    (files.storage_root() / artifact.storage_key).write_bytes(b"a" * artifact_size)
    monkeypatch.setattr(sessions.settings, "max_upload_mb", 1)
    db = _Db(source, history, uploads=[uploaded], artifacts=[artifact])
    before = {str(path) for path in files.storage_root().rglob("*") if path.is_file()}
    with pytest.raises(HTTPException) as error:
        await _fork(db)
    assert error.value.status_code == 413
    assert error.value.detail == "fork_files_too_large_1mb"
    assert db.added == [] and db.commits == 0
    assert {str(path) for path in files.storage_root().rglob("*") if path.is_file()} == before
