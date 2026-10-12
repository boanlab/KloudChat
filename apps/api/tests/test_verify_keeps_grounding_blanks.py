"""A value grounding blanked is not written back by verify's repair."""

from types import SimpleNamespace

from app.pipeline.report_stages import keep_blanks


def test_a_blanked_value_written_back_by_the_repair_is_blanked_again():
    traced = SimpleNamespace(found=[
        {"kind": "number", "text": "94,080,000원", "placeholder": "[수치]"},
        {"kind": "claim", "text": "특히 좋다", "placeholder": ""},
    ])
    before = [{"heading": "비교", "content": "B안 연간: 14,000원 × 40명 × 12개월 = [수치]"},
              {"heading": "결론", "content": "A안을 권고한다."}]
    after = [{"heading": "비교", "content": "B안 연간: 14,000원 × 40명 × 12개월 = 94,080,000원"},
             {"heading": "결론", "content": "A안을 권고한다. 연 5,712,000원이다."}]
    out = keep_blanks(before, after, traced)
    assert out[0]["content"].endswith("= [수치]")
    assert out[1] is after[1]
    # a value the writer had (not blanked) stays
    kept = [{"heading": "비교", "content": "94,080,000원"}]
    assert keep_blanks(kept, kept, traced)[0]["content"] == "94,080,000원"
    assert keep_blanks(before, after, None) is after
