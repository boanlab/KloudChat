"""Words in quotes are set on the picture in a font; the model is asked for none."""

import io

from PIL import Image

from app.services import image_text

REQUEST = (
    "봄 도서관 행사 포스터, 파스텔 톤, 큰 글씨로 「벚꽃 책 축제」 아래에 “4월 12일 · 시립도서관”"
)


def _picture(colour=(240, 200, 210)) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (1360, 768), colour).save(out, format="PNG")
    return out.getvalue()


def test_the_quoted_words_are_found_in_order_without_repeats():
    assert image_text.quoted_strings(REQUEST + " 「벚꽃 책 축제」") == [
        "벚꽃 책 축제", "4월 12일 · 시립도서관",
    ]
    assert image_text.quoted_strings("도서관 열람실 사진") == []


def test_the_picture_prompt_loses_the_quoted_words():
    assert "벚꽃" not in image_text.without_quoted(REQUEST)
    assert "봄 도서관 행사 포스터" in image_text.without_quoted(REQUEST)


def test_the_planner_only_places_words_and_cannot_change_them():
    strings = image_text.quoted_strings(REQUEST)
    raw = ('{"prompt": "A spring poster, calm empty area in the upper half.", "layout": ['
           '{"where": "top", "size": "large"}, {"where": "nowhere", "size": "huge"}]}')
    prompt, lines = image_text.parse_plan(raw, strings, REQUEST)
    assert prompt.startswith("A spring poster")
    assert [line.text for line in lines] == strings
    assert (lines[0].where, lines[0].size) == ("top", "large")
    # a place or size that is not one of ours becomes the default for that line
    assert (lines[1].where, lines[1].size) == ("bottom", "small")


def test_a_picture_prompt_that_copies_a_quoted_word_is_replaced():
    strings = image_text.quoted_strings(REQUEST)
    raw = '{"prompt": "A poster titled 벚꽃 책 축제 in big letters.", "layout": []}'
    prompt, _ = image_text.parse_plan(raw, strings, REQUEST)
    assert "벚꽃" not in prompt


def test_an_unusable_plan_gives_the_default_layout():
    strings = ["제목", "부제"]
    prompt, lines = image_text.parse_plan("not json", strings, "그림 「제목」 「부제」")
    assert prompt == "그림"
    assert [(line.where, line.size) for line in lines] == [("upper", "large"), ("bottom", "small")]


def test_the_words_are_drawn_and_the_picture_keeps_its_size():
    plain = _picture()
    lines = image_text.default_layout(image_text.quoted_strings(REQUEST))
    drawn = image_text.overlay(plain, lines)
    assert drawn != plain
    picture = Image.open(io.BytesIO(drawn))
    assert picture.size == (1360, 768) and picture.format == "PNG"


def test_a_long_title_is_wrapped_or_shrunk_inside_the_picture():
    long = "도서관에서 함께 읽는 봄 이야기 책 축제와 저자와의 만남 행사 안내"
    drawn = image_text.overlay(_picture(), [image_text.Line(long, "center", "large")])
    assert Image.open(io.BytesIO(drawn)).size == (1360, 768)


def test_two_lines_asked_for_the_same_place_do_not_overlap():
    lines = [image_text.Line("첫째", "center", "large"), image_text.Line("둘째", "center", "large")]
    assert Image.open(io.BytesIO(image_text.overlay(_picture(), lines))).size == (1360, 768)


def test_an_unreadable_picture_is_returned_as_it_came():
    line = image_text.Line("글", "top", "small")
    assert image_text.overlay(b"not a picture", [line]) == b"not a picture"


def test_the_planner_picks_a_look_of_type_from_a_fixed_set():
    strings = ["여름 독서 캠프"]
    looks = (("serif", "serif"), ("brush", "brush"), ("comic", "gothic"), ("", "gothic"))
    for named, used in looks:
        raw = f'{{"prompt": "A beach.", "font": "{named}", "layout": []}}'
        _, lines = image_text.parse_plan(raw, strings, "그림 「여름 독서 캠프」")
        assert lines[0].font == used


def test_every_look_of_type_draws_even_when_its_file_is_not_installed():
    for role in image_text.FONTS:
        lines = [image_text.Line("여름 독서 캠프 · 4~6학년!", "center", "large", role)]
        assert Image.open(io.BytesIO(image_text.overlay(_picture(), lines))).size == (1360, 768)
