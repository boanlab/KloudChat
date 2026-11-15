"""A focused turn about one project file (「vLLM 논문 발표 자료」) is given the knowledge room
the best passages left over, from that file, spread over its length; and a deck that asks
for the paper's figures has them cut before it is written."""

from __future__ import annotations

from types import SimpleNamespace

from app.routers import sessions
from app.services import knowledge as knowledge_service
from app.services import workspace_context


def _file(fid: str, name: str, text: str):
    return SimpleNamespace(id=fid, name=name, text=text, source_url=None)


def _paper(word: str, pieces: int = 60) -> str:
    return "\n\n".join(f"{word} section {i}. " + "Paged memory blocks are mapped. " * 25
                       for i in range(pieces))


def test_the_lead_file_fills_the_room_from_across_its_length():
    lead = _file("a", "2309.06180.pdf", _paper("vLLM"))
    other = _file("b", "2401.09670.pdf", _paper("DistServe", 10))
    picked = {
        "a": [knowledge_service.Passage(lead.name, 3, "x", 2.0),
              knowledge_service.Passage(lead.name, 4, "y", 1.5)],
        "b": [knowledge_service.Passage(other.name, 1, "z", 0.5)],
    }
    room = workspace_context._fill_from_lead([lead, other], picked, 20_000)
    indexes = [p.index for p in picked["a"]]
    assert room < 6_000
    assert indexes == sorted(indexes) and len(indexes) > 10
    # from the beginning to the end of the paper, not only its opening
    total = len(knowledge_service.chunk(lead.text))
    assert max(indexes) > total * 0.8 and min(indexes) <= 4
    assert len(picked["b"]) == 1


def test_no_lead_when_the_passages_are_spread_over_files():
    files = [_file(str(i), f"{i}.pdf", _paper(str(i), 5)) for i in range(4)]
    picked = {f.id: [knowledge_service.Passage(f.name, 1, "x", 1.0)] for f in files}
    assert workspace_context._fill_from_lead(files, picked, 20_000) == 20_000


def test_the_focus_pdf_and_a_figure_request():
    item = workspace_context.ContextFile
    knowledge = [item("2309.06180.pdf", "truncated", 40_000, 82_000),
                 item("2401.09670.pdf", "truncated", 900, 90_000),
                 item("notes.md", "included", 50_000, 50_000)]
    assert sessions._focus_pdf(knowledge) == "2309.06180.pdf"
    assert sessions._focus_pdf(knowledge[:1] + [item("x.pdf", "truncated", 30_000, 1)]) is None
    asked = "vLLM 논문 발표할 PowerPoint 12장. 논문 그림이나 표도 넣고"
    assert sessions._ASKS_FIGURES.search(asked)
    assert not sessions._ASKS_FIGURES.search("매출 실적 엑셀로 분기 실적 PowerPoint 8장")
