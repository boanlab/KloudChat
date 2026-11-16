"""A deck's numbers with a unit are the material's: one no source text has, and a range
(20.4–38.2%) given as one end (「최대 20.4%」), are sent back once, then saved with a note. A
result on a picture slide is on the slide, not only in the speaker notes."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.services import deck_numbers, pptx_doc
from app.services.tools import pptx as pptx_tools
from app.services.tools.base import ToolContext

PAPER = ("Only 20.4%-38.2% of the KV cache memory is effective in existing systems. "
         + "Filler about attention and batching. " * 30
         + "vLLM improves the throughput by 2.2× over Orca (Oracle) and 4.3× over Orca (Max), "
         "up to 22× over FasterTransformer; sharing gives 1.67× and 3.58×.")


def _ctx(sources=(PAPER,)):
    ctx = ToolContext(user_id="u", session_id="s")
    ctx.outputs.extend(sources)
    ctx.request = "vLLM 논문 발표 자료"
    return ctx


def _png(width, height):
    out = io.BytesIO()
    Image.new("RGB", (width, height), "#dddddd").save(out, format="PNG")
    return out.getvalue()


@pytest.mark.asyncio
async def test_a_narrowed_range_is_sent_back_once_then_saved_with_a_note():
    args = {"title": "t", "slides": [{"title": "문제",
                                      "bullets": ["유효 메모리는 최대 20.4%에 불과"]}]}
    ctx = _ctx()
    with pytest.raises(ValueError, match="20.4%"):
        await pptx_tools._prepare(args, ctx)
    again = await pptx_tools._prepare(args, ctx)
    assert any("20.4%" in note for note in again[pptx_tools.NOTES])


@pytest.mark.asyncio
async def test_a_number_no_source_has_is_named():
    args = {"title": "t", "slides": [{"title": "결과", "bullets": ["처리량 2.2배", "지연 57%"]}]}
    with pytest.raises(ValueError) as raised:
        await pptx_tools._prepare(args, _ctx())
    assert "57%" in str(raised.value) and "2.2" not in str(raised.value)


@pytest.mark.asyncio
async def test_whole_ranges_and_sourced_numbers_pass_and_a_draft_is_never_refused():
    ok = {"title": "t", "slides": [{"title": "문제", "bullets": ["유효 메모리는 20.4–38.2%"]},
                                   {"title": "결과", "bullets": ["최대 22배, 2.2배, 4.3배"]}]}
    assert not (await pptx_tools._prepare(ok, _ctx()))[pptx_tools.NOTES]
    bad = {"title": "t", "__draft__": True,
           "slides": [{"title": "결과", "bullets": ["지연 57%"]}]}
    assert (await pptx_tools._prepare(bad, _ctx()))["slides"]


@pytest.mark.asyncio
async def test_thin_sources_are_not_judged():
    args = {"title": "t", "slides": [{"title": "결과", "bullets": ["지연 57%"]}]}
    assert not (await pptx_tools._prepare(args, _ctx(["짧은 자료 12%"])))[pptx_tools.NOTES]


def test_numbers_only_in_the_notes_of_a_picture_slide_come_onto_the_slide():
    slides, promoted = pptx_tools._promote_numbers([
        {"title": "결과", "image": {"file": "f.png"},
         "notes": "같은 요청률에서 vLLM이 2.2배 많은 요청을 처리한다. 설명은 길게 이어진다."}])
    assert promoted == [1] and "2.2배" in slides[0]["bullets"][0]
    keep, none = pptx_tools._promote_numbers([
        {"title": "결과", "image": {"file": "f.png"}, "bullets": ["처리량 2.2배"],
         "notes": "4.3배도 있다."}])
    assert not none and keep[0]["bullets"] == ["처리량 2.2배"]


def test_a_wide_figure_keeps_its_numbers_on_the_slide():
    kept, moved = pptx_doc.strip_split(["배경 설명", "처리량 2.2배", "지연 20%", "4.3배", "마무리"])
    assert kept == ["처리량 2.2배", "지연 20%", "4.3배"] and moved == ["배경 설명", "마무리"]
    made = pptx_doc.deck("t", "", [{"title": "결과", "bullets": ["처리량 2.2배", "설명"],
                                    "image": {"data": _png(1200, 300), "caption": "Figure 13"}}])
    from app.services import pptx_edit

    text = pptx_edit.plain_text(made)
    assert "처리량 2.2배" in text and "설명" in text


def test_checks_are_regex_only_and_cheap():
    found = deck_numbers.check([{"title": "a", "bullets": ["3.9배"]}], [PAPER])
    assert [c.text for c in found.unsourced] == ["3.9배"]


@pytest.mark.parametrize("size", [(800, 1000), (1200, 900), (1600, 500)])
def test_a_three_line_caption_stays_above_the_footer(size):
    from pptx import Presentation

    caption = ("Figure 2. Average percentage of memory wastes in different LLM serving systems "
               "during the experiment in 4.2 (Kwon et al., 2023). Reserved slots, internal "
               "fragmentation and external fragmentation all appear here. More text.")
    made = pptx_doc.deck("t", "", [{"title": "그림", "bullets": ["처리량 2.2배"],
                                    "image": {"data": _png(*size), "caption": caption}}])
    slide = Presentation(io.BytesIO(made)).slides[1]
    box = next(s for s in slide.shapes if s.has_text_frame and "Figure 2." in s.text_frame.text)
    # the footer rule sits at 12700 * (540 - 60) EMU on the 960x540 page
    assert box.top + box.height <= 12700 * 482
