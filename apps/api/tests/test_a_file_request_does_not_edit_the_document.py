"""A follow-up that asks for the document as a file hands the file over and edits nothing.

Sentences are the lineup v1 diagnostic runs' own follow-ups (client-proposal-1..5,
minutes-1, gaejo-report-1, comparison-report-1).
"""

from __future__ import annotations

import inspect
import json

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.chat import ChatSession, Message, SessionKind
from app.models.workspace import Artifact, ArtifactKind
from app.routers import sessions
from app.services import revise, sandbox


@pytest.mark.parametrize(
    ("sentence", "formats"),
    [
        ("좋아, PDF로 줘.", ["pdf"]),
        ("워드 파일로 줘. 인사팀장님이 고쳐서 위에 올린대.", ["docx"]),
        ("공공기관이라 한글 파일도 같이 줘.", ["hwpx"]),
        ("한글 파일이랑 PDF 둘 다.", ["hwpx", "pdf"]),
        ("PDF로.", ["pdf"]),
        ("한글 파일로 줘.", ["hwpx"]),
        ("좋아, 이대로 회의록 문서로 만들어 줘. 한글 파일로 내려받을 거야.", ["hwpx"]),
        ("HWPX도 같이", ["hwpx"]),
        ("고마워, 워드로 줘", ["docx"]),
        ("PDF로 바꿔 줘", ["pdf"]),
        ("다운로드", []),
        ("내보내 줘", []),
    ],
)
def test_a_file_request_is_recognised_before_routing(sentence, formats):
    assert revise.export_only(sentence) == formats


@pytest.mark.parametrize(
    "sentence",
    [
        "견적 고치고 PDF로",
        "예산 안에 들어오게 만들 방법도 같이 정리돼 있으면 좋겠어. 단가는 못 깎아.",
        "영어 말고 한글로 써 줘",
        "PDF 첨부 자료의 수치를 반영해 줘",
        "다운로드 버튼 설명을 추가해 줘",
        "결론을 짧게 줄여 줘",
    ],
)
def test_an_edit_is_not_a_file_request(sentence):
    assert revise.export_only(sentence) is None


def test_an_edit_with_a_file_revises_then_hands_the_file_over():
    assert revise.export_request("견적 고치고 PDF로") == (["pdf"], True)
    assert revise.without_export("견적 고치고 PDF로") == "견적 고치고"


def test_a_reply_to_the_person_is_not_a_section():
    leaked = (
        "PDF로 보내드리기는 여기에서 직접 처리할 수 없습니다. 다만 아래에 견적 절 본문을 "
        "완성해 두었으니, 그대로 제안서 파일에 붙여 넣으신 뒤 PDF로 내보내시면 됩니다.\n\n## 견적"
    )
    assert revise.meta_talk(leaked)
    assert not revise.meta_talk(
        "본 견적은 RFP에서 밝힌 사업 기간(2026. 11. 2. ~ 2027. 1. 29., 3개월)과 내부 투입 "
        "계획에 근거하여 산정했습니다."
    )
    assert not revise.meta_talk("산출물: 결과 보고서(PDF), 소스코드, 운영 매뉴얼을 제출한다.")


def test_the_revision_rejects_a_reply_in_place_of_the_part():
    from app.pipeline import revise_stages

    source = inspect.getsource(revise_stages.Rewrite.run)
    assert source.count("revise.meta_talk(") == 2


def test_the_file_turn_is_routed_before_revision():
    source = inspect.getsource(sessions.send_message)
    assert source.find("_hand_over_document(") < source.find("_revise_document(")
    assert "revise.export_request(typed_content)" in source


_DDL = (
    """
    CREATE TABLE sessions (
        id TEXT PRIMARY KEY, user_id TEXT, kind TEXT, title TEXT DEFAULT '', project_id TEXT,
        agent_id TEXT, model TEXT DEFAULT '', routing_mode TEXT, artifact_id TEXT,
        render_template_id TEXT, pipeline_id TEXT, index_key TEXT, pending TEXT, summary TEXT,
        skill_ids TEXT,
        running_turn TEXT, running_since DATETIME, pinned BOOLEAN DEFAULT 0,
        publication_id TEXT, visitor_id TEXT, publication_version_id TEXT,
        shared_at DATETIME, viewable BOOLEAN NOT NULL DEFAULT 0,
        created_at DATETIME, updated_at DATETIME,
        title_set_by_user BOOLEAN NOT NULL DEFAULT 0
    )
    """,
    """
    CREATE TABLE artifacts (
        id TEXT PRIMARY KEY, user_id TEXT, session_id TEXT, project_id TEXT, kind TEXT,
        title TEXT, data TEXT, storage_key TEXT, version INTEGER DEFAULT 1,
        created_at DATETIME, updated_at DATETIME,
        search_text TEXT NOT NULL DEFAULT ''
    )
    """,
    """
    CREATE TABLE messages (
        id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT DEFAULT '', steps TEXT,
        attachments TEXT, usage TEXT, variants TEXT, superseded TEXT, routing TEXT,
        artifact_ids TEXT, started_from TEXT, model TEXT, rating TEXT, failure TEXT,
        created_at DATETIME
    )
    """,
)

_QUOTE = (
    "| 항목 | 금액(원) |\n| --- | --- |\n| PM 1.0MM | 9,000,000 |\n"
    "| AI 엔지니어 3.0MM | 24,000,000 |\n| **공급가액** | **47,100,000** |"
)


@pytest.fixture
async def maker(monkeypatch):
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        for statement in _DDL:
            await conn.exec_driver_sql(statement)
    made = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(sessions, "SessionLocal", made)
    yield made
    await engine.dispose()


async def _report_session(maker) -> tuple[str, str]:
    async with maker() as db:
        session = ChatSession(user_id="u1", kind=SessionKind.report)
        artifact = Artifact(
            user_id="u1",
            session_id=session.id,
            kind=ArtifactKind.report,
            title="한빛물류 제안서",
            data={
                "kind": "report",
                "sections": [
                    {"id": "s6", "heading": "견적", "content": _QUOTE, "format": "markdown"}
                ],
            },
        )
        session.artifact_id = artifact.id
        db.add(session)
        db.add(artifact)
        await db.commit()
        return session.id, artifact.id


async def test_pdf_please_hands_over_a_pdf_and_leaves_the_document_alone(maker, monkeypatch):
    kept: list[tuple[str, bytes]] = []

    async def store_output(user_id, session_id, name, data):
        kept.append((name, data))
        return {"id": f"f{len(kept)}", "name": name, "size": len(data)}

    monkeypatch.setattr(sandbox, "store_output", store_output)
    session_id, artifact_id = await _report_session(maker)
    async with maker() as db:
        before = (await db.get(Artifact, artifact_id)).data

    events = [
        json.loads(chunk.removeprefix("data: "))
        async for chunk in sessions._hand_over_document(
            user_id="u1", session_id=session_id, formats=revise.export_only("좋아, PDF로 줘.")
        )
    ]

    assert [name for name, _ in kept] == ["한빛물류 제안서.pdf"]
    assert kept[0][1].startswith(b"%PDF")
    chips = [e for e in events if e.get("type") == "step" and e.get("outputs")]
    assert chips and chips[0]["outputs"][0]["name"] == "한빛물류 제안서.pdf"
    said = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
    assert "「한빛물류 제안서.pdf」" in said
    assert not any(e.get("type") in ("section", "artifact") for e in events)
    async with maker() as db:
        artifact = await db.get(Artifact, artifact_id)
        assert artifact.version == 1
        assert artifact.data == before
        reply = (await db.exec(select(Message).where(Message.session_id == session_id))).one()
        assert reply.content == said
        assert reply.steps[0]["outputs"][0]["id"] == "f1"


async def test_both_formats_are_handed_over_and_a_slide_format_is_declined(maker, monkeypatch):
    kept: list[str] = []

    async def store_output(user_id, session_id, name, data):
        kept.append(name)
        return {"id": name, "name": name, "size": len(data)}

    monkeypatch.setattr(sandbox, "store_output", store_output)
    session_id, _ = await _report_session(maker)

    events = [
        json.loads(chunk.removeprefix("data: "))
        async for chunk in sessions._hand_over_document(
            user_id="u1", session_id=session_id, formats=["hwpx", "pdf", "pptx"]
        )
    ]

    assert kept == ["한빛물류 제안서.hwpx", "한빛물류 제안서.pdf"]
    said = "".join(e.get("text", "") for e in events if e.get("type") == "delta")
    assert "PowerPoint(PPTX)" in said and "내보낼 수 없습니다" in said


def test_a_proposal_sentence_about_files_is_not_meta_talk():
    for sentence in (
        "결과 보고서는 PDF로 전달해 드립니다.",
        "귀사가 요청하신 내용을 모두 반영했습니다.",
        "관리자 화면에서 제공할 수 있는 통계는 일별 문의 수입니다.",
    ):
        assert not revise.meta_talk(sentence), sentence
