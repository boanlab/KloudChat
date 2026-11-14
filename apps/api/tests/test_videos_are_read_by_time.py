"""Videos understood by time: pieces, merging, the time-coded document, the background job,
pieced uploads, the player's signed URLs, and citations that point at a moment."""

from __future__ import annotations

import importlib.util
import io
import json
import time
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlmodel import SQLModel, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.core.deps import current_user
from app.models.user import CreditLedger, User
from app.models.workspace import FilePage, FileUpload, MediaPart, StoredFile
from app.routers import media as media_router
from app.services import (
    agent,
    knowledge,
    media_client,
    transcribe,
    video_doc,
    video_jobs,
    video_read,
)
from app.services import files as file_service


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(type_, compiler, **kw) -> str:
    return "JSON"


# ── the pieces of a long recording ─────────────────────────────────────


def test_the_audio_is_cut_into_overlapping_ten_minute_pieces():
    pieces = video_doc.audio_pieces(1500.0)
    assert pieces == [(0.0, 580.0), (575.0, 580.0), (1150.0, 350.0)]
    # each piece stays under vLLM Whisper's 600 s ceiling
    assert all(length <= 590 for _, length in video_doc.audio_pieces(1158.0))
    # each piece starts before the last one ends: a word at the seam is heard whole once
    for (start, length), (after, _) in zip(pieces, pieces[1:], strict=False):
        assert after < start + length
    assert pieces[-1][0] + pieces[-1][1] == 1500.0


def test_a_sliver_at_the_end_joins_the_piece_before_it():
    assert video_doc.audio_pieces(1156.0) == [(0.0, 580.0), (575.0, 581.0)]
    assert video_doc.audio_pieces(30.0) == [(0.0, 30.0)]
    assert video_doc.audio_pieces(0) == []


def test_pieces_join_on_one_timeline_and_the_overlap_is_kept_once():
    pieces = [
        (0.0, [{"start": 0.0, "end": 4.0, "text": "안녕하세요"},
               {"start": 595.5, "end": 599.0, "text": "경계에 걸친 말"}]),
        # the second piece hears the seam again from its own start
        (595.0, [{"start": 0.5, "end": 4.0, "text": "경계에 걸친 말"},
                 {"start": 10.0, "end": 12.0, "text": "다음 이야기"}]),
    ]
    said = video_doc.merge_said(pieces)
    assert [s.text for s in said] == ["안녕하세요", "경계에 걸친 말", "다음 이야기"]
    assert said[-1].start == 605.0


def test_a_server_without_timings_gives_one_line_per_piece():
    said = video_doc.merge_said([
        (0.0, [{"start": 0.0, "end": 600.0, "text": "전체", "whole": True}]),
        (595.0, [{"start": 0.0, "end": 400.0, "text": "나머지", "whole": True}]),
    ])
    assert [(s.start, s.text) for s in said] == [(0.0, "전체"), (597.5, "나머지")]


# ── scenes and the document ────────────────────────────────────────────


def _said(*rows: tuple[float, str]) -> list[video_doc.Said]:
    return [video_doc.Said(t, t + 3, text) for t, text in rows]


def test_speech_falls_into_the_scene_on_screen_when_it_was_said():
    shown = [video_doc.Shown(t=0.0, kind="slide", text="Intro", about="제목 슬라이드", frame=0),
             video_doc.Shown(t=30.0, kind="code", text="def f(): return 1", about="코드", frame=1)]
    parts = video_doc.sections(60.0, shown, _said((2, "시작합니다"), (31, "이 함수는")))
    assert [(p.start, p.speech, p.shown.frame) for p in parts] == [
        (0.0, "시작합니다", 0), (30.0, "이 함수는", 1)]


def test_a_long_talk_over_one_picture_is_split_so_times_stay_close():
    rows = [(float(t), f"문장 {t}") for t in range(0, 300, 20)]
    parts = video_doc.sections(300.0, [], _said(*rows), split_sec=75)
    assert len(parts) >= 4
    assert all(p.said[0].start - p.start < 1 for p in parts)
    assert parts[0].shown is None


def test_the_document_has_a_time_mark_per_section_with_what_was_said_and_shown():
    shown = [video_doc.Shown(t=0.0, kind="slide", text="Reward circuit", about="뇌 회로 슬라이드",
                             frame=0),
             video_doc.Shown(t=750.0, kind="chart", text="2010 2020", about="막대그래프",
                             frame=1)]
    parts = video_doc.sections(800.0, shown, _said((1, "보상 회로"), (751, "늘었습니다")))
    text = video_doc.render("강의.mp4", 800.0, parts, said_any=True, shown_any=True)
    assert text.startswith("# 강의.mp4 — 영상 00:13:20")
    assert ("[00:00:00]\n(화면) 슬라이드 — 뇌 회로 슬라이드 | 글자: Reward circuit\n"
            "(말) 보상 회로") in text
    assert "[00:12:30]\n(화면) 도표 — 막대그래프 | 글자: 2010 2020\n(말) 늘었습니다" in text
    segments = video_doc.segments(parts)
    assert segments[1] == {"start": 750.0, "end": 800.0, "said": "늘었습니다",
                           "shown": "도표 — 막대그래프 | 글자: 2010 2020", "frame": 1}


def test_stamps_always_carry_hours():
    assert video_doc.stamp(750) == "00:12:30"
    assert video_doc.stamp(3723.9) == "01:02:03"
    assert video_doc.seconds_of("앞 [01:02:03] 뒤") == 3723


def test_near_duplicate_frames_hash_close_and_different_slides_far():
    def slide(text: str, shift: int = 0) -> bytes:
        image = Image.new("RGB", (640, 360), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((40 + shift, 40, 300 + shift, 120), fill="black")
        draw.text((60, 200), text, fill="black")
        if text == "B":
            draw.rectangle((350, 200, 600, 330), fill="navy")
        out = io.BytesIO()
        image.save(out, "JPEG")
        return out.getvalue()

    a, a2, b = (video_read.picture_hash(slide("A")), video_read.picture_hash(slide("A", 2)),
                video_read.picture_hash(slide("B")))
    assert video_doc.distance(a, a2) <= video_doc.SAME_PICTURE
    assert video_doc.distance(a, b) > video_doc.SAME_PICTURE


def _template_slide(lines: list[str], speaker_x: int | None = None) -> bytes:
    """A slide of one template (title bar, bullets) — and optionally a speaker inset."""
    image = Image.new("RGB", (1280, 720), "white")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, 1280, 110), fill="navy")
    draw.rectangle((0, 660, 1280, 720), fill="navy")
    font = ImageFont.load_default(size=34)
    for i, line in enumerate(lines):
        draw.text((80, 170 + i * 70), "■ " + line, fill="black", font=font)
    if speaker_x is not None:
        draw.ellipse((speaker_x, 420, speaker_x + 160, 640), fill=(205, 170, 140))
    out = io.BytesIO()
    image.save(out, "JPEG", quality=80)
    return out.getvalue()


def test_template_slides_with_different_text_are_different_pictures():
    first = ["Drug-resistant TB is a serious threat", "Among 10,528 U.S. TB cases in 2011",
             "124 cases of MDR TB = 1.2% of TB cases", "6 cases of XDR TB = 4.8% of MDR",
             "Treatment takes up to two years"]
    second = ["Compared to drug-susceptible TB, treatment", "Less effective, more toxic",
              "Lower cure rates (48-54%)", "Loss to follow-up 14-23%",
              "10-100 times more costly ($200 and up)"]
    a, b, again = _template_slide(first), _template_slide(second), _template_slide(first)
    ha, hb, h2 = (video_read.picture_hash(x) for x in (a, b, again))
    ga, gb, g2 = (video_read.small(x) for x in (a, b, again))
    # the difference hashes alone call them the same …
    assert video_doc.distance(ha, hb) <= video_doc.SAME_PICTURE
    # … the edges do not
    assert not video_read.same_picture(ha, ga, hb, gb)
    assert video_read.same_picture(ha, ga, h2, g2)


def test_a_speaker_moving_is_still_the_same_picture():
    left = _template_slide(["Summary", "Diagnostics and new drugs"], speaker_x=900)
    moved = _template_slide(["Summary", "Diagnostics and new drugs"], speaker_x=915)
    assert video_read.same_picture(video_read.picture_hash(left), video_read.small(left),
                                   video_read.picture_hash(moved), video_read.small(moved))


def test_frames_are_capped_by_length_and_thinned_evenly():
    assert video_doc.frame_cap(600, 4.0, 120) == 40
    assert video_doc.frame_cap(3 * 3600, 4.0, 120) == 120
    assert video_doc.thin([float(i) for i in range(10)], 4) == [0.0, 3.0, 6.0, 9.0]


def test_the_vision_answer_is_read_as_json_and_kept_as_text_otherwise():
    answer = '```json\n{"kind": "code", "text": "x = 1", "about": "편집기"}\n```'
    found = video_read.parse(answer, 5)
    assert (found.kind, found.text, found.about, found.t) == ("code", "x = 1", "편집기", 5)
    loose = video_read.parse("그냥 글자", 7)
    assert (loose.kind, loose.text) == ("other", "그냥 글자")


# ── the media worker's frame picking (apps/media/server.py) ─────────────


def _media_server():
    path = Path(__file__).parents[2] / "media" / "server.py"
    spec = importlib.util.spec_from_file_location("media_server", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_worker_keeps_settled_changes_fills_long_gaps_and_caps_the_count():
    server = _media_server()
    picked = server.pick([(1.0, 0.9), (10.0, 0.5), (11.0, 0.9), (50.0, 0.4)], 400.0, min_gap=2,
                         max_gap=90, max_frames=100)
    times = [p["t"] for p in picked]
    # the first frame is always there (a change a second later is the same scene);
    # 10 s and 11 s are one change, the later (settled) one kept
    assert times[:3] == [0.0, 11.0, 50.0]
    # 50 → 399 has no change: fillers keep every gap under 90 s
    assert all(b - a <= 90 for a, b in zip(times, times[1:], strict=False))
    capped = server.pick([(float(t), 0.5 + t / 10000) for t in range(5, 3000, 5)], 3000.0,
                         min_gap=2, max_gap=90, max_frames=20)
    assert len(capped) == 20


def test_the_worker_counts_changed_edges_against_the_last_kept_frame():
    server = _media_server()
    blank, slide, other = bytes(100), bytes([255] * 50 + [0] * 50), bytes([0] * 50 + [255] * 50)
    assert server.changed(blank, blank) == 0 and server.changed(blank, slide) == 0.5
    frames = [(0.0, blank), (1.0, blank), (2.0, slide), (3.0, slide), (4.0, other)]
    assert server.detect(frames, 0.04) == [(2.0, 0.5), (4.0, 1.0)]


def test_the_worker_refuses_keys_outside_its_root(tmp_path, monkeypatch):
    server = _media_server()
    monkeypatch.setattr(server, "ROOT", str(tmp_path))
    (tmp_path / "u").mkdir()
    (tmp_path / "u" / "v.mp4").write_bytes(b"x")
    assert server.path_of("u/v.mp4").endswith("v.mp4")
    for bad in ("../etc/passwd", "/etc/passwd", "u/../../x", "", None):
        with pytest.raises(server.Refused):
            server.path_of(bad)


# ── citations that point at a moment ──────────────────────────────────


DOC = "\n\n".join([
    "# 강의.mp4 — 영상 00:20:00 (말·화면을 시간 순서로 옮김)\n"
    "[00:00:00]\n(말) 오늘은 도파민 이야기",
    "[00:12:30]\n(화면) 도표 — 막대그래프 | 글자: 2010 2020\n(말) 중독 환자가 크게 늘었습니다",
    "[00:15:00]\n(말) 치료 방법을 봅시다",
])


def test_a_video_passage_carries_its_time_and_player_link():
    link = knowledge.video_link("file-1")
    passages, ranked = knowledge.gather([("강의.mp4", DOC, link)], "중독 환자")
    assert not ranked  # a small video is given whole, one passage per time mark
    hit = next(p for p in passages if "늘었습니다" in p.text)
    assert hit.time == 750 and hit.label == "강의.mp4 · 00:12:30"
    assert hit.player == "/files?open=file-1&t=750"
    rendered = knowledge.render([hit])
    assert "[1] 자료: 강의.mp4 · 00:12:30\n영상: /files?open=file-1&t=750\n" in rendered
    assert "출처:" not in rendered


def test_a_ranked_passage_finds_the_mark_it_sits_under():
    long_doc = DOC + "\n\n" + "\n\n".join(
        f"[00:{m:02d}:00]\n(말) " + "다른 이야기 " * 80 for m in range(16, 40))
    passages = knowledge.search([("강의.mp4", long_doc, knowledge.video_link("f"))], "중독 환자")
    assert passages and passages[0].time == 750


def test_a_cited_video_passage_links_to_its_moment():
    sources: list[str] = []
    content = "[1] 자료: 강의.mp4 · 00:12:30\n영상: /files?open=file-1&t=750\n본문"
    numbered = agent._number_sources(content, sources, {"name": "search_knowledge"})
    assert numbered.startswith("[1] 자료: 강의.mp4 · 00:12:30\n")
    linked, cited = agent._link_citations("중독 환자가 늘었다 [1].", sources)
    assert linked == "중독 환자가 늘었다 [[1]](/files?open=file-1&t=750)." and cited == [1]
    assert agent._material_entry(1, sources[0]) == (
        "- [1] [강의.mp4 · 00:12:30](/files?open=file-1&t=750)")


def test_other_material_still_cites_with_a_plain_number():
    sources: list[str] = []
    agent._number_sources("[1] 자료: 학칙 · 3쪽\n본문", sources, {"name": "search_knowledge"})
    assert agent._link_citations("휴학은 4년 [1].", sources)[0] == "휴학은 4년 [1]."
    assert agent._material_entry(1, sources[0]) == "- [1] 학칙 · 3쪽"


# ── the background job ────────────────────────────────────────────────


_USERS_DDL = """
CREATE TABLE users (
    id TEXT PRIMARY KEY, email TEXT, password_hash TEXT, name TEXT, role TEXT, status TEXT,
    monthly_credits INTEGER DEFAULT 0, credits_used INTEGER DEFAULT 0, cycle_resets_at DATETIME,
    litellm_user_id TEXT, litellm_key TEXT, litellm_key_preview TEXT,
    litellm_key_issued_at DATETIME, avatar_color TEXT, allowed_models TEXT, preferences TEXT,
    created_at DATETIME, last_active_at DATETIME, email_verified_at DATETIME
)
"""


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(file_service.settings, "file_storage_dir", str(tmp_path))
    # a file, not memory: the job writes through sessions of its own, side by side
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/db.sqlite")
    tables = [m.__table__ for m in (StoredFile, FilePage, MediaPart, FileUpload, CreditLedger)]
    async with engine.begin() as connection:
        await connection.execute(sa.text(_USERS_DDL))
        await connection.run_sync(lambda c: SQLModel.metadata.create_all(c, tables=tables))

    def session():
        return AsyncSession(engine, expire_on_commit=False)

    monkeypatch.setattr(video_jobs.core_db, "SessionLocal", session)
    async with session() as s:
        s.add(User(id="me", email="me@example.test", password_hash="x", name="Me",
                   monthly_credits=1000))
        await s.commit()
    yield session


def _jpeg(layout: str) -> bytes:
    """A slide; each layout is drawn differently (a near-duplicate check looks at shapes)."""
    image = Image.new("RGB", (320, 180), "white")
    draw = ImageDraw.Draw(image)
    if layout == "title":
        draw.rectangle((20, 20, 300, 70), fill="navy")
    else:
        draw.rectangle((180, 20, 300, 160), fill="darkgreen")
        draw.ellipse((20, 90, 120, 170), fill="black")
    out = io.BytesIO()
    image.save(out, "JPEG")
    return out.getvalue()


class Worker:
    """The media worker, Whisper and the vision model, recorded."""

    def __init__(self, duration: float = 1250.0):
        self.duration = duration
        self.audio_calls: list[float] = []
        self.frame_calls: list[float] = []
        self.vision_calls: list[float] = []
        self.fail_audio = 0
        self.has_video = True

    async def probe(self, key):
        return {"duration": self.duration, "hasAudio": True, "hasVideo": self.has_video,
                "width": 1280, "height": 720}

    async def scenes(self, key, *, max_frames, **kw):
        return [{"t": 0.0, "score": 1}, {"t": 2.0, "score": 0.3}, {"t": 700.0, "score": 0.5},
                {"t": 900.0, "score": 0.5}]

    async def frame(self, key, t, width=1280):
        self.frame_calls.append(t)
        # 2 s repeats the first picture; 900 s shows the first slide again
        return _jpeg({0.0: "title", 2.0: "title", 700.0: "chart", 900.0: "title"}[t])

    async def audio(self, key, start, duration):
        if self.fail_audio:
            self.fail_audio -= 1
            raise media_client.MediaError("worker busy")
        self.audio_calls.append(start)
        return b"fLaC" + str(start).encode()

    async def segments(self, data, filename, language=None):
        start = float(data[4:].decode())
        return [{"start": 4.0, "end": 7.0, "text": f"{int(start)}초 조각의 말"}], "ko"

    async def model(self):
        return {"id": "strict-local/qwen3.8-flash"}

    async def read(self, entry, picture, t, key):
        self.vision_calls.append(t)
        return video_read.Reading(
            shown=video_doc.Shown(t=t, kind="slide", text=f"글자 {int(t)}", about="슬라이드"),
            input_tokens=900, output_tokens=40)


@pytest.fixture
def worker(monkeypatch):
    fake = Worker()
    monkeypatch.setattr(settings, "media_url", "http://media.test:8300")
    monkeypatch.setattr(media_client, "probe", fake.probe)
    monkeypatch.setattr(media_client, "scenes", fake.scenes)
    monkeypatch.setattr(media_client, "frame", fake.frame)
    monkeypatch.setattr(media_client, "audio", fake.audio)
    monkeypatch.setattr(transcribe, "segments_of", fake.segments)
    monkeypatch.setattr(video_read, "model", fake.model)
    monkeypatch.setattr(video_read, "read", fake.read)

    async def no_reindex(db, stored):
        return None

    from app.services import ocr_jobs
    monkeypatch.setattr(ocr_jobs, "_reindex", no_reindex)

    async def key_for(user):
        return "k"

    from app.services import ocr
    monkeypatch.setattr(ocr, "api_key_for", key_for)
    return fake


async def _video(session, name: str = "강의.mp4") -> StoredFile:
    async with session() as s:
        row = StoredFile(user_id="me", name=name, size=10, mime="video/mp4")
        row.storage_key = file_service.write_blob("me", row.id, name, b"\x00\x00\x00\x18ftypmp42")
        assert video_jobs.mark(row)
        s.add(row)
        await s.commit()
        return row


async def _reload(session, file_id: str) -> tuple[StoredFile, list[MediaPart]]:
    async with session() as s:
        row = await s.get(StoredFile, file_id)
        parts = (await s.exec(select(MediaPart).where(MediaPart.file_id == file_id))).all()
        return row, list(parts)


async def test_a_video_becomes_a_time_coded_document(db, worker):
    stored = await _video(db)
    assert await video_jobs.run_one()
    row, parts = await _reload(db, stored.id)
    assert row.media_state == "done" and row.error is None
    # three pieces for 1250 s; three distinct pictures (the 2 s repeat dropped, 900 s read once)
    assert worker.audio_calls == [0.0, 575.0, 1150.0]
    assert len(row.media["frames"]) == 3 and sorted(worker.vision_calls) == [0.0, 700.0]
    assert row.media_done == row.media_total == 6
    assert "[00:00:00]\n(화면) 슬라이드 — 슬라이드 | 글자: 글자 0\n(말) 0초 조각의 말" in row.text
    assert "[00:11:40]\n(화면) 슬라이드 — 슬라이드 | 글자: 글자 700" in row.text
    # the slide shown again at 15:00 is in the document under its own time, read once
    assert "[00:15:00]\n(화면) 슬라이드 — 슬라이드 | 글자: 글자 0" in row.text
    assert {p.kind for p in parts} == {"audio", "frame"}
    assert row.media["segments"][0]["frame"] == 0
    # thumbnails beside the video
    frames = file_service.blob_path(file_service.frames_dir(row.storage_key))
    assert sorted(p.name for p in frames.iterdir()) == ["0.jpg", "0s.jpg", "1.jpg", "1s.jpg",
                                                        "2.jpg", "2s.jpg"]
    async with db() as s:
        ledger = (await s.exec(select(CreditLedger))).all()
    speech = [r for r in ledger if r.reason == video_jobs.REASON_SPEECH]
    vision = [r for r in ledger if r.reason == video_jobs.REASON_VISION]
    assert sum(r.units for r in speech) == 1250 and {r.unit for r in speech} == {"seconds"}
    # a self-hosted vision model is free: its tokens are recorded, nothing deducted; the
    # picture shown again was not read again, so it has no row
    assert len(vision) == 2 and all(r.delta == 0 for r in vision)
    assert sum(r.units for r in vision) == 2 * 940


async def test_a_job_taken_over_resumes_where_it_stopped(db, worker):
    stored = await _video(db)
    async with db() as s:
        row = await s.get(StoredFile, stored.id)
        row.media = {"duration": 1250.0, "hasAudio": True, "hasVideo": True,
                     "frames": [{"n": 0, "t": 0.0, "hash": "0" * 16}]}
        s.add(MediaPart(file_id=stored.id, kind="audio", idx=0, start=0.0,
                        data={"segments": [{"start": 0, "end": 2, "text": "이미 옮긴 말"}]}))
        s.add(MediaPart(file_id=stored.id, kind="frame", idx=0, start=0.0,
                        data={"kind": "slide", "text": "이미 읽은 슬라이드", "about": ""}))
        s.add(row)
        await s.commit()
    assert await video_jobs.run_one()
    row, _ = await _reload(db, stored.id)
    assert row.media_state == "done"
    assert worker.audio_calls == [575.0, 1150.0] and worker.vision_calls == []
    assert worker.frame_calls == []
    assert "이미 옮긴 말" in row.text and "이미 읽은 슬라이드" in row.text


async def test_a_passing_failure_is_tried_again_then_given_up(db, worker, monkeypatch):
    stored = await _video(db)
    worker.fail_audio = 10
    assert await video_jobs.run_one()
    row, _ = await _reload(db, stored.id)
    assert row.media_state == "queued" and row.media_attempts == 1
    for _ in range(video_jobs.MAX_ATTEMPTS - 1):
        async with db() as s:
            again = await s.get(StoredFile, stored.id)
            again.media_claimed_at = None
            s.add(again)
            await s.commit()
        assert await video_jobs.run_one()
    row, _ = await _reload(db, stored.id)
    assert row.media_state == "failed" and row.error == video_jobs.GAVE_UP


async def test_a_video_too_long_fails_at_once_with_the_reason(db, worker, monkeypatch):
    worker.duration = (settings.video_max_minutes + 1) * 60.0
    stored = await _video(db)
    assert await video_jobs.run_one()
    row, _ = await _reload(db, stored.id)
    assert row.media_state == "failed"
    assert row.error == video_jobs.too_long(settings.video_max_minutes)
    assert worker.audio_calls == []


async def test_a_recording_without_pictures_is_read_for_speech_only(db, worker):
    worker.has_video = False
    stored = await _video(db, "녹음.m4a")
    assert await video_jobs.run_one()
    row, _ = await _reload(db, stored.id)
    assert row.media_state == "done" and row.media["frames"] == []
    assert "(화면)" not in row.text and "(말) 575초 조각의 말" in row.text


async def test_without_a_vision_model_the_speech_still_counts(db, worker, monkeypatch):
    async def none():
        raise video_read.VisionUnavailable("no model")

    monkeypatch.setattr(video_read, "model", none)
    stored = await _video(db)
    assert await video_jobs.run_one()
    row, _ = await _reload(db, stored.id)
    assert row.media_state == "done" and "vision_unavailable" in row.media["notes"]
    assert "(말) 0초 조각의 말" in row.text


async def test_nothing_is_queued_without_a_media_worker(db, monkeypatch):
    monkeypatch.setattr(settings, "media_url", "")
    row = StoredFile(user_id="me", name="a.mp4", mime="video/mp4")
    assert not video_jobs.mark(row) and row.media_state is None
    monkeypatch.setattr(settings, "media_url", "http://media.test")
    assert not video_jobs.mark(StoredFile(user_id="me", name="a.pdf", mime="application/pdf"))


def test_the_estimate_counts_frames_and_charges_only_a_priced_model():
    free = video_jobs.estimate(600, {"id": "strict-local/x"})
    assert free["frames"] == 40 and free["credits"] == 0 and free["allowed"]
    priced = video_jobs.estimate(600, {"id": "p", "inputCreditCost": 10, "creditCost": 30})
    assert priced["credits"] > 0
    assert not video_jobs.estimate((settings.video_max_minutes + 1) * 60, None)["allowed"]


# ── pieced uploads and the player ─────────────────────────────────────


@pytest.fixture
async def client(db, monkeypatch):
    monkeypatch.setattr(settings, "media_url", "http://media.test:8300")
    monkeypatch.setattr(settings, "upload_piece_mb", 1)
    monkeypatch.setattr(video_jobs, "wake", lambda: None)
    app = FastAPI()
    app.include_router(media_router.router)

    async def session():
        async with db() as s:
            yield s

    me = User(id="me", email="me@example.test", password_hash="x", name="Me")
    app.dependency_overrides[get_session] = session
    app.dependency_overrides[current_user] = lambda: me
    async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as http:
        yield http


MB = 1024 * 1024
VIDEO = b"\x00\x00\x00\x18ftypmp42" + bytes(range(256)) * (10 * 1024)  # ~2.5 MB


async def test_a_video_goes_up_in_pieces_and_is_queued(client, db):
    started = (await client.post("/uploads", json={
        "name": "강의.mp4", "size": len(VIDEO), "type": "video/mp4", "seconds": 600})).json()
    assert started["pieceBytes"] == MB and started["received"] == 0
    offset = 0
    while offset < len(VIDEO):
        piece = VIDEO[offset:offset + MB]
        answer = await client.put(f"/uploads/{started['id']}?offset={offset}", content=piece)
        assert answer.status_code == 200
        offset = answer.json()["received"]
    # a piece sent again after a dropped answer is acknowledged, not written twice
    again = await client.put(f"/uploads/{started['id']}?offset=0", content=VIDEO[:MB])
    assert again.json()["received"] == len(VIDEO)
    made = await client.post(f"/uploads/{started['id']}/finish")
    assert made.status_code == 201
    row = made.json()
    assert row["mediaState"] == "queued" and row["mime"] == "video/mp4"
    async with db() as s:
        stored = await s.get(StoredFile, row["id"])
        assert file_service.read_blob(stored.storage_key) == VIDEO
        assert (await s.exec(select(FileUpload))).all() == []


async def test_a_piece_ahead_of_what_arrived_is_refused_with_where_to_resume(client):
    started = (await client.post("/uploads", json={
        "name": "a.mp4", "size": len(VIDEO), "type": "video/mp4"})).json()
    ahead = await client.put(f"/uploads/{started['id']}?offset={MB}", content=VIDEO[MB:2 * MB])
    assert ahead.status_code == 409 and ahead.json()["detail"] == "expected_offset_0"
    assert (await client.get(f"/uploads/{started['id']}")).json()["received"] == 0
    early = await client.post(f"/uploads/{started['id']}/finish")
    assert early.status_code == 409


async def test_uploads_that_are_not_media_or_too_large_or_too_long_are_refused(client):
    assert (await client.post("/uploads", json={
        "name": "a.pdf", "size": 10, "type": "application/pdf"})).status_code == 415
    too_large = await client.post("/uploads", json={
        "name": "a.mp4", "size": (settings.max_video_upload_mb + 1) * MB, "type": "video/mp4"})
    assert too_large.status_code == 413
    assert too_large.json()["detail"] == f"file_too_large_{settings.max_video_upload_mb}mb"
    too_long = await client.post("/uploads", json={
        "name": "a.mp4", "size": 10, "type": "video/mp4",
        "seconds": settings.video_max_minutes * 60 + 1})
    assert too_long.json()["detail"] == f"video_too_long_{settings.video_max_minutes}min"


async def test_a_file_that_is_not_media_inside_is_refused_at_the_end(client, db):
    data = b"%PDF-1.7 not a video" * 10
    started = (await client.post("/uploads", json={
        "name": "a.mp4", "size": len(data), "type": "video/mp4"})).json()
    await client.put(f"/uploads/{started['id']}?offset=0", content=data)
    assert (await client.post(f"/uploads/{started['id']}/finish")).status_code == 415
    async with db() as s:
        assert (await s.exec(select(StoredFile))).all() == []


async def test_someone_elses_upload_is_not_found(client, db):
    async with db() as s:
        s.add(User(id="other", email="o@example.test", password_hash="x", name="O"))
        theirs = FileUpload(user_id="other", name="x.mp4", size=10, storage_key="other/x.part")
        s.add(theirs)
        await s.commit()
    assert (await client.get(f"/uploads/{theirs.id}")).status_code == 404
    assert (await client.put(f"/uploads/{theirs.id}?offset=0", content=b"x")).status_code == 404


async def test_the_player_streams_with_seeking_and_signed_thumbnails(client, db, worker):
    stored = await _video(db)
    assert await video_jobs.run_one()
    info = (await client.get(f"/files/{stored.id}/media")).json()
    assert info["state"] == "done" and info["name"] == "강의.mp4"
    assert info["segments"][0]["start"] == 0 and info["frames"][0] == {"n": 0, "t": 0.0}
    # the app mounts the router under /api; the test app has no prefix
    stream = info["streamUrl"].removeprefix(settings.api_prefix)
    part = await client.get(stream, headers={"Range": "bytes=0-3"})
    assert part.status_code == 206 and part.content == b"\x00\x00\x00\x18"
    assert part.headers["content-type"].startswith("video/")
    thumb = info["frameUrl"].removeprefix(settings.api_prefix).replace("{n}", "1s")
    shot = await client.get(thumb)
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/jpeg"
    # a changed or expired signature opens nothing
    assert (await client.get(stream.replace("s=", "s=0"))).status_code == 404
    expired = media_router.signed_query(stored, now=time.time() - 2 * media_router.URL_TTL_SEC)
    assert (await client.get(f"/files/{stored.id}/stream?{expired}")).status_code == 404
    assert (await client.get(thumb.replace("1s.jpg", "..%2F..%2Fx.jpg"))).status_code == 404
    # the poster: the picture at the very moment, by the worker
    poster = info["posterUrl"].removeprefix(settings.api_prefix).replace("{t}", "700")
    shot = await client.get(poster)
    assert shot.status_code == 200 and shot.headers["content-type"] == "image/jpeg"
    assert worker.frame_calls[-1] == 700.0
    assert (await client.get(poster.replace("s=", "s=0"))).status_code == 404


async def test_a_failed_video_can_be_read_again(client, db, worker):
    worker.duration = (settings.video_max_minutes + 1) * 60.0
    stored = await _video(db)
    assert await video_jobs.run_one()
    worker.duration = 100.0
    again = await client.post(f"/files/{stored.id}/media/retry")
    assert again.status_code == 200 and again.json()["mediaState"] == "queued"
    assert await video_jobs.run_one()
    row, _ = await _reload(db, stored.id)
    assert row.media_state == "done"


def test_deleting_a_video_removes_its_frames(tmp_path, monkeypatch):
    monkeypatch.setattr(file_service.settings, "file_storage_dir", str(tmp_path))
    key = file_service.write_blob("me", "f1", "a.mp4", b"x")
    frames = file_service.blob_path(file_service.frames_dir(key))
    frames.mkdir(parents=True)
    (frames / "0.jpg").write_bytes(b"j")
    file_service.delete_blob(key)
    assert not frames.exists() and not file_service.blob_path(key).exists()


# ── the migration ─────────────────────────────────────────────────────


def test_the_migration_adds_and_drops_what_video_jobs_need_on_sqlite():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = Path(__file__).parents[1] / "alembic" / "versions" / "0087_video_understanding.py"
    spec = importlib.util.spec_from_file_location("m0087", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert (module.revision, module.down_revision) == ("0087", "0081")
    engine = sa.create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE users (id VARCHAR PRIMARY KEY)"))
        conn.execute(sa.text("CREATE TABLE files (id VARCHAR PRIMARY KEY, name VARCHAR)"))
        ops = Operations(MigrationContext.configure(conn))
        module.op = ops
        module.upgrade()
        inspector = sa.inspect(conn)
        columns = {c["name"] for c in inspector.get_columns("files")}
        assert {"media_state", "media_stage", "media", "media_claim"} <= columns
        assert {"media_parts", "file_uploads"} <= set(inspector.get_table_names())
        module.downgrade()
        inspector = sa.inspect(conn)
        assert "media_parts" not in inspector.get_table_names()
        assert "media" not in {c["name"] for c in inspector.get_columns("files")}


def test_wire_names_are_what_the_web_reads():
    out = media_router.MediaOut(stream_url="u", frame_url="f", has_video=True)
    wire = json.loads(out.model_dump_json(by_alias=True))
    assert {"streamUrl", "frameUrl", "hasVideo", "segments"} <= set(wire)
