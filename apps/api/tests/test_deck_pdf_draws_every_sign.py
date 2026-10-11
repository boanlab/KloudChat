"""A deck PDF draws the signs its Korean face lacks as look-alikes, not boxes.

Quality lane Q25: a quiz slide's 「완료 − 도착 − 실행」 came out 「완료 □ 도착 □ 실행」 —
NanumGothic has no U+2212 MINUS SIGN.
"""

from __future__ import annotations

from reportlab.pdfbase import pdfmetrics

from app.services import deck_export, fonts


def test_a_minus_sign_the_face_lacks_becomes_a_hyphen():
    font = fonts.korean("gothic")
    face = getattr(pdfmetrics.getFont(font), "face", None)
    drawn = fonts.drawable("대기 = 완료 − 도착 − 실행", font)
    if face is None or 0x2212 not in face.charToGlyph:
        assert drawn == "대기 = 완료 - 도착 - 실행"
    else:
        assert drawn == "대기 = 완료 − 도착 − 실행"
    # Characters with no stand-in, and text with none of them, are left alone.
    assert fonts.drawable("평균 4.75 (단위: 초)", font) == "평균 4.75 (단위: 초)"


def test_the_deck_pdf_carries_the_slides_through_the_stand_ins():
    slides = [{"title": "퀴즈", "layout": "bullets",
               "bullets": ["대기 시간 = 완료 − 도착 − 실행", "P1: 0 − 0 = 0"]}]
    data = deck_export.to_pdf("스케줄링 − 퀴즈", slides)
    assert data.startswith(b"%PDF")
    tree = fonts.drawable_tree(slides, fonts.korean("gothic"))
    assert tree[0]["layout"] == "bullets" and len(tree[0]["bullets"]) == 2
