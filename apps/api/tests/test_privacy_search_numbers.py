"""Narrow public-number exceptions never relax outbound or legacy PII masking."""

import pytest

from app.services import governance

STAMP = "20260912120009"
ARTICLE = f"https://example.test/article/{STAMP}"
YEARS = "2023 2024 2025 2026"
PAN = "4111111111111111"
KEY = "sk-syntheticabcdefghijklmnopqrstuvwxyz"


def _assert_consistent(text, *, scope, expected, count, protected=None, legacy=False):
    masker = governance.mask_legacy if legacy else governance.mask
    assert masker(text, scope=scope, protected=protected) == (expected, count)
    findings = governance.findings(
        {"synthetic": text}, scope=scope, protected=protected, legacy=legacy,
    )
    assert sum(finding.count for finding in findings) == count


@pytest.mark.parametrize("scope", ["tool", "answer"])
@pytest.mark.parametrize("text", [
    ARTICLE,
    f"[Public article]({ARTICLE})",
    f"{ARTICLE}/overview?language=ko",
    f"Year {YEARS}",
    f"연도: {YEARS}",
    f"Card information is a different field.\nSource: {ARTICLE}",
    f"API key: {KEY}\nYear {YEARS}",
])
def test_clear_public_number_structures_survive_tool_and_answer_masking(scope, text):
    expected = text.replace(KEY, "[API키]")
    _assert_consistent(text, scope=scope, expected=expected, count=int(KEY in text))


@pytest.mark.parametrize("text", [ARTICLE, f"Year {YEARS}"])
def test_outbound_and_legacy_contracts_remain_conservative(text):
    for scope, legacy in [("egress", False), ("egress", True), ("tool", True), ("answer", True)]:
        masked, count = (governance.mask_legacy if legacy else governance.mask)(text, scope=scope)
        assert "[카드번호]" in masked
        assert count == 1
        _assert_consistent(text, scope=scope, expected=masked, count=count, legacy=legacy)


@pytest.mark.parametrize("scope", ["tool", "answer"])
@pytest.mark.parametrize("text", [
    f"Card number: {ARTICLE}",
    f"결제 정보: {ARTICLE}",
    f"Secret token: {ARTICLE}",
    f"https://example.test/cards/{STAMP}",
    f"https://example.test/%63ard/{STAMP}",
    f"Credit card year {YEARS}",
    f"https://example.test/article?id={STAMP}",
    f"https://example.test/article/number-{STAMP}",
    f"https://example.test/article/{STAMP}.html",
    STAMP,
    YEARS,
    "USD/KRW 1350 1360 1370 1384",
    f"Card {PAN}",
    f"https://example.test/article/{PAN}",
    f"https://example.test/article?card={PAN}",
    f"0.{PAN}",
])
def test_card_secret_and_ambiguous_numeric_shapes_still_mask(scope, text):
    masked, count = governance.mask(text, scope=scope)
    assert "[카드번호]" in masked
    assert count >= 1
    _assert_consistent(text, scope=scope, expected=masked, count=count)


@pytest.mark.parametrize("scope", ["tool", "answer"])
@pytest.mark.parametrize("text,protected_text", [
    (ARTICLE, STAMP),
    (f"Year {YEARS}", YEARS),
])
def test_user_protected_values_override_public_number_exceptions(scope, text, protected_text):
    protected = governance.protected_values(protected_text)
    assert protected
    masked, count = governance.mask(text, scope=scope, protected=protected)
    assert "[카드번호]" in masked
    assert count == 1
    _assert_consistent(text, scope=scope, expected=masked, count=count, protected=protected)


@pytest.mark.parametrize("scope", ["egress", "tool", "answer"])
def test_normal_decimal_rates_remain_readable(scope):
    text = "USD/KRW 1,350.50 KRW; EUR/KRW 1350.50 KRW; quote date 2026-09-13."
    _assert_consistent(text, scope=scope, expected=text, count=0)


@pytest.mark.parametrize("scope", ["tool", "answer"])
def test_actual_secret_categories_and_user_values_remain_masked(scope):
    first = "900101112345"
    weights = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
    check = (11 - sum(int(n) * w for n, w in zip(first, weights, strict=True)) % 11) % 10
    rrn = first[:6] + "-" + first[6:] + str(check)
    protected = governance.protected_values("me@example.com; 010-1234-5678")
    text = f"{PAN}; {rrn}; {KEY}; me@example.com; 01012345678"
    _assert_consistent(
        text, scope=scope, protected=protected, count=5,
        expected="[카드번호]; [주민번호]; [API키]; [이메일]; [전화번호]",
    )
