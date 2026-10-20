"""English speaker notes: no 「?.」 after a question title, no enumeration short of its first."""

from __future__ import annotations

from app.services import deck

TITLE = "Can Small On-Premise Models Replace Cloud APIs?"
#: the round-11 T8 closing slide
TAKEAWAYS = {"layout": "bullets", "title": "Key Takeaways for Document Drafting",
             "bullets": ["Local is cheaper: $4 vs $38", "Long docs need cloud: 64% vs 91%"]}
REQUEST = ("Create a 5-slide English presentation. cost per 1,000 documents was $4 vs $38, "
           "64% vs 91% above 20 pages. Include speaker notes.")


def test_a_question_title_is_not_spliced_into_a_sentence_with_a_stop_after_it():
    slides = [{"layout": "title", "title": TITLE},
              {"layout": "bullets", "title": "Route by task?",
               "bullets": ["Short drafts run local."]},
              {"layout": "closing", "title": "Thanks"}]
    assert deck.fill_missing_notes(slides, REQUEST, title=TITLE,
                                   subtitle="Conference findings on cost") == [0, 1, 2]
    assert slides[0]["notes"] == (
        "Hello, everyone. Today's question: Can Small On-Premise Models Replace Cloud APIs? "
        "Conference findings on cost.")
    assert slides[1]["notes"] == (
        "On this slide, we look at Route by task? The key points are Short drafts run local.")
    assert not any("?." in s["notes"] or ".." in s["notes"] for s in slides)


def test_an_enumeration_keeps_its_first_point_when_it_reads_the_bullet():
    notes = ("Three things to remember. First, local is cheaper: $4 vs $38. "
             "Second, long documents still need the cloud at 64% against 91%. "
             "Third, route each task to the model that fits it.")
    kept = deck.notes_without_echo(notes, TAKEAWAYS)
    assert kept.startswith("Three things to remember. First, local is cheaper")


def test_an_enumeration_that_lost_a_point_loses_its_ordinals_and_its_count():
    notes = ("Three things to remember. First, local drafts are 9 times cheaper. "
             "Second, long documents still need the cloud at 64% against 91%. "
             "Third, route each task to the model that fits it.")
    kept = deck.notes_grounded(notes, set(), REQUEST)
    assert kept == ("Long documents still need the cloud at 64% against 91%. "
                    "Route each task to the model that fits it.")
    # a whole enumeration is left alone
    whole = "Two points. First, local is cheaper at $4. Second, cloud is better at 91%."
    assert deck.notes_grounded(whole, set(), REQUEST) == whole
    korean = "세 가지입니다. 둘째, 긴 문서는 클라우드가 낫습니다. 셋째, 작업별로 나눕니다."
    assert deck.notes_grounded(korean, set(), "") == (
        "긴 문서는 클라우드가 낫습니다. 작업별로 나눕니다.")
