"""Governance policy: PII masking, intent filtering, retention, and privacy decision tokens.

Masking runs before the model call and before the write. Retention clears
message bodies only; rows and metadata stay for the audit trail.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import jwt
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.models.chat import Message
from app.models.governance import Governance
from app.models.user import utcnow

log = logging.getLogger(__name__)

_TTL = 15.0
_cache: dict = {"at": 0.0, "value": None}

DETECTOR_VERSION = "privacy-detector-v1"
POLICY_VERSION = "external-data-guard-v1"
_TOKEN_TTL_SEC = 300


class GovernanceUnavailable(RuntimeError):
    """The authoritative policy row could not be read for an egress decision."""


@dataclass(frozen=True, slots=True)
class _Detection:
    category: str
    label: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class Finding:
    """Value-free aggregate safe to return to a browser or audit log."""

    category: str
    source: str
    count: int

    def wire(self) -> dict[str, Any]:
        return {"category": self.category, "source": self.source, "count": self.count}


# Narrow candidates followed by format/checksum validation; deterministic, no
# semantic name/address detection.
_EMAIL_LOCAL = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%+-")
_EMAIL_DOMAIN = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
_EMAIL_TLD = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")
_KR_MOBILE = re.compile(r"(?<!\d)(?:\+?82[- ]?)?0?1[016-9][- ]?\d{3,4}[- ]?\d{4}(?!\d)")
_KR_LANDLINE = re.compile(
    r"(?<!\d)(?:\(0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4])\)\s*|"
    r"0(?:2|3[1-3]|4[1-4]|5[1-5]|6[1-4])[- ])\d{3,4}[- ]\d{4}(?!\d)"
)
# NANP area and exchange codes cannot start with 0 or 1; separators or a
# parenthesised area code are required.
_NANP = re.compile(
    r"(?<!\d)(?:\+?1[ .-])?(?:\([2-9]\d{2}\)\s*|[2-9]\d{2}[ .-])"
    r"[2-9]\d{2}[ .-]\d{4}(?!\d)"
)
# Candidate only; `_valid_international_phone` checks digit count and parentheses.
_INTERNATIONAL_PHONE = re.compile(r"(?<![\w+])\+[1-9][0-9 .()-]{5,28}[0-9](?!\w)")
_RRN = re.compile(r"(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
# Candidate only; `ipaddress` validates. At least two colons required.
_IPV6 = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
_LEGACY_RRN = re.compile(r"\b\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])-[1-4]\d{6}\b")
_LEGACY_CARD = re.compile(r"\b(?:\d{4}[- ]){3}\d{4}\b")
_SECRETS: list[tuple[str, re.Pattern[str]]] = [
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b")),
    ("api_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("api_key", re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{30,}\b")),
    ("api_key", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    (
        "api_key",
        re.compile(
            r"\b(?:aws[_ -]*secret(?:[_ -]*access)?[_ -]*key|secretaccesskey)"
            r"\s*[:=]\s*[\"']?[A-Za-z0-9/+=]{40}[\"']?",
            re.I,
        ),
    ),
    (
        "api_key",
        re.compile(
            r"\bAccountKey\s*=\s*[A-Za-z0-9+/]{40,}={0,2}",
            re.I,
        ),
    ),
    (
        "jwt",
        re.compile(
            r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\."
            r"[A-Za-z0-9_-]{8,}(?![A-Za-z0-9_-])"
        ),
    ),
    (
        "private_key",
        re.compile(
            r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----[\s\S]+?"
            r"-----END (?:RSA |EC |OPENSSH |DSA |ENCRYPTED )?PRIVATE KEY-----"
        ),
    ),
]

_LABELS = {
    "email": "[이메일]",
    "phone": "[전화번호]",
    "government_id": "[주민번호]",
    "payment_card": "[카드번호]",
    "ip_address": "[IP주소]",
    "api_key": "[API키]",
    "jwt": "[JWT]",
    "private_key": "[개인키]",
    # Organisational, not personal: a switchboard, a role mailbox, a private-range
    # address. Detected so the scopes below can leave them readable.
    "landline": "[전화번호]",
    "role_email": "[이메일]",
    "private_ip": "[IP주소]",
}

#: Details that identify a person. Masked before leaving for an external model.
PERSONAL = frozenset({"email", "phone", "government_id", "payment_card", "ip_address"})
#: Details whose mere exposure is an incident, wherever they turn up.
SECRETS = frozenset({"government_id", "payment_card", "api_key", "jwt", "private_key"})
#: Details of an organisation or a network range; never masked.
ORGANISATIONAL = frozenset({"landline", "role_email", "private_ip"})

#: What each place in the pipeline masks. `egress`: the user's own words and
#: files on their way to an external model. `tool`: pages fetched from the
#: public web — a company's switchboard is the answer, not a leak. `answer`:
#: the model's reply at rest, where only the user's own details and secrets
#: are taken out (see `mask(protected=...)`).
SCOPES: dict[str, frozenset[str]] = {
    "egress": PERSONAL | SECRETS,
    "tool": SECRETS,
    "answer": SECRETS,
}

#: Service and toll-free numbers: 15xx/16xx/18xx-xxxx and 080.
_KR_SERVICE = re.compile(r"(?<!\d)(?:1[568]\d{2}[- ]?\d{4}|080[- ]\d{3,4}[- ]\d{4})(?!\d)")
#: A mailbox that belongs to a desk, not a person.
_ROLE_MAILBOXES = frozenset(
    {
        "info",
        "contact",
        "press",
        "pr",
        "support",
        "help",
        "helpdesk",
        "service",
        "cs",
        "customer",
        "sales",
        "biz",
        "business",
        "partner",
        "partners",
        "marketing",
        "admin",
        "administrator",
        "webmaster",
        "postmaster",
        "hostmaster",
        "master",
        "office",
        "hello",
        "hr",
        "recruit",
        "recruiting",
        "jobs",
        "career",
        "careers",
        "noreply",
        "no-reply",
        "no_reply",
        "donotreply",
        "newsletter",
        "news",
        "minwon",
        "team",
        "staff",
        "billing",
        "account",
        "accounts",
        "security",
        "abuse",
        "legal",
        "privacy",
        "media",
        "ir",
        "inquiry",
        "inquiries",
        "enquiry",
        "enquiries",
    }
)
#: Well-known public resolvers that appear in every network how-to.
_WELL_KNOWN_IPS = frozenset({"8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1", "9.9.9.9"})


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def _valid_rrn(value: str) -> bool:
    digits = _digits(value)
    if len(digits) != 13:
        return False
    # The seventh digit encodes sex and century.
    century = 1900 if digits[6] in "1256" else 2000
    try:
        date(century + int(digits[:2]), int(digits[2:4]), int(digits[4:6]))
    except ValueError:
        return False
    weights = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
    check = (11 - sum(int(n) * w for n, w in zip(digits[:12], weights, strict=True)) % 11) % 10
    return check == int(digits[-1])


def _valid_luhn(value: str) -> bool:
    digits = _digits(value)
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, char in enumerate(digits):
        digit = int(char)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _valid_international_phone(value: str) -> bool:
    digits = _digits(value)
    if not 8 <= len(digits) <= 15:
        return False
    if value.count("(") != value.count(")") or value.count("(") > 2:
        return False
    depth = 0
    for char in value:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _is_word_character(value: str) -> bool:
    r"""Regex ``\w`` membership."""
    return value == "_" or value.isalnum()


def _email_spans(text: str) -> Iterator[tuple[int, int]]:
    """High-confidence email spans in linear time, anchored on each ``@`` to avoid regex
    backtracking.
    """
    resume = 0
    at = text.find("@")
    while at >= 0:
        segment_start = at
        while segment_start > 0 and text[segment_start - 1] in _EMAIL_LOCAL:
            segment_start -= 1

        start = max(segment_start, resume)
        while (
            start < at
            and start > 0
            and (_is_word_character(text[start - 1]) or text[start - 1] in ".+-")
        ):
            start += 1

        end = at + 1
        while end < len(text) and text[end] in _EMAIL_DOMAIN:
            end += 1

        local = text[start:at]
        domain = text[at + 1 : end]
        domain_head, dot, tld = domain.rpartition(".")
        leading_boundary = start == 0 or not (
            _is_word_character(text[start - 1]) or text[start - 1] in ".+-"
        )
        trailing_boundary = end == len(text) or not (
            _is_word_character(text[end]) or text[end] in ".-"
        )
        if (
            local
            and domain_head
            and dot
            and 2 <= len(tld) <= 63
            and all(char in _EMAIL_TLD for char in tld)
            and leading_boundary
            and trailing_boundary
        ):
            yield start, end
            # Resume after the match so touching addresses do not overlap.
            resume = end

        at = text.find("@", at + 1)


def _is_legacy_local_character(value: str) -> bool:
    return _is_word_character(value) or value in ".+-"


def _is_legacy_domain_character(value: str) -> bool:
    return _is_word_character(value) or value in ".-"


def _legacy_email_spans(text: str) -> Iterator[tuple[int, int]]:
    r"""``\b[\w.+-]+@[\w-]+\.[\w.-]+\b`` without backtracking; accepts Unicode word characters."""
    resume = 0
    at = text.find("@")
    while at >= 0:
        segment_start = at
        while segment_start > 0 and _is_legacy_local_character(text[segment_start - 1]):
            segment_start -= 1

        start = max(segment_start, resume)
        while start < at:
            previous_is_word = start > 0 and _is_word_character(text[start - 1])
            if previous_is_word != _is_word_character(text[start]):
                break
            start += 1

        domain_head_end = at + 1
        while domain_head_end < len(text) and (
            _is_word_character(text[domain_head_end]) or text[domain_head_end] == "-"
        ):
            domain_head_end += 1

        suffix_start = domain_head_end + 1
        scan_end = suffix_start
        if domain_head_end < len(text) and text[domain_head_end] == ".":
            while scan_end < len(text) and _is_legacy_domain_character(text[scan_end]):
                scan_end += 1

        # The trailing ``\b`` trims final dots and hyphens.
        end = scan_end
        while end > suffix_start and not _is_word_character(text[end - 1]):
            end -= 1

        if start < at and domain_head_end > at + 1 and end > suffix_start:
            yield start, end
            resume = end

        at = text.find("@", at + 1)


def _is_role_mailbox(address: str) -> bool:
    local = address.split("@", 1)[0].lower()
    return local in _ROLE_MAILBOXES or local.startswith(("noreply", "no-reply", "info@"))


def _is_non_personal_address(address: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """Loopback, private, link-local, documentation and reserved ranges, and the
    resolvers every how-to mentions: an address, not a person's location."""
    return (
        address.is_private
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or str(address) in _WELL_KNOWN_IPS
    )


def _detections(text: str) -> list[_Detection]:
    candidates: list[_Detection] = []

    def add(category: str, match: re.Match[str]) -> None:
        candidates.append(_Detection(category, _LABELS[category], match.start(), match.end()))

    for start, end in _email_spans(text):
        category = "role_email" if _is_role_mailbox(text[start:end]) else "email"
        candidates.append(_Detection(category, _LABELS[category], start, end))
    for match in _KR_MOBILE.finditer(text):
        add("phone", match)
    for match in _KR_LANDLINE.finditer(text):
        add("landline", match)
    for match in _KR_SERVICE.finditer(text):
        add("landline", match)
    for match in _NANP.finditer(text):
        add("phone", match)
    for match in _INTERNATIONAL_PHONE.finditer(text):
        if _valid_international_phone(match.group()):
            add("phone", match)
    for match in _RRN.finditer(text):
        if _valid_rrn(match.group()):
            add("government_id", match)
    for match in _CARD.finditer(text):
        if _valid_luhn(match.group()):
            add("payment_card", match)
    for match in _IPV4.finditer(text):
        try:
            address = ipaddress.IPv4Address(match.group())
        except ipaddress.AddressValueError:
            continue
        add("private_ip" if _is_non_personal_address(address) else "ip_address", match)
    for match in _IPV6.finditer(text):
        try:
            address = ipaddress.IPv6Address(match.group())
        except ipaddress.AddressValueError:
            continue
        add("private_ip" if _is_non_personal_address(address) else "ip_address", match)
    for category, pattern in _SECRETS:
        for match in pattern.finditer(text):
            add(category, match)

    # Same-start longer matches win (a private-key block contains token-like
    # fragments); later overlapping candidates are skipped.
    accepted: list[_Detection] = []
    covered_until = -1
    for item in sorted(candidates, key=lambda d: (d.start, -(d.end - d.start), d.category)):
        if item.start < covered_until:
            continue
        accepted.append(item)
        covered_until = item.end
    return accepted


async def current(force: bool = False) -> Governance:
    """Cached policy snapshot for non-authorizing work; egress decisions use
    `current_for_egress`."""
    now = time.monotonic()
    if not force and _cache["value"] is not None and now - _cache["at"] < _TTL:
        return _cache["value"]
    try:
        async with SessionLocal() as db:
            row = await db.get(Governance, "default")
            policy = row or Governance()
    except Exception as exc:  # noqa: BLE001
        # Never report guard-off / raw delivery after a read failure.
        log.warning("governance unreadable, enabling external data guard: %s", exc)
        stale = _cache["value"]
        if stale is not None:
            return stale.model_copy(
                update={
                    "external_data_guard": True,
                    "allow_user_raw_external": False,
                }
            )
        return Governance(external_data_guard=True, allow_user_raw_external=False)
    _cache.update(at=now, value=policy)
    return policy


async def current_for_egress() -> Governance:
    """Uncached policy read for egress authorization; the cache is process-local and multi-worker
    revocation must be immediate.

    Raises `GovernanceUnavailable` on a read failure; callers answer 503.
    """
    now = time.monotonic()
    try:
        async with SessionLocal() as db:
            row = await db.get(Governance, "default")
            policy = row or Governance()
    except Exception as exc:  # noqa: BLE001 — every DB failure denies egress
        log.error(
            "authoritative governance read failed; egress denied (%s)",
            type(exc).__name__,
        )
        raise GovernanceUnavailable from exc
    _cache.update(at=now, value=policy)
    return policy


def invalidate() -> None:
    _cache.update(at=0.0, value=None)


#: Categories compared by their digits alone, so 010-1234-5678 written back as
#: 01012345678 or 010 1234 5678 is still the same number.
_NUMERIC = frozenset({"phone", "landline", "payment_card", "government_id"})


def _normalised(category: str, value: str) -> str:
    if category in _NUMERIC:
        return _digits(value)
    return value.strip().lower()


def _in_scope(
    hits: list[_Detection], text: str, scope: str, protected: frozenset[str] | set[str]
) -> list[_Detection]:
    """The hits `scope` masks: its categories, plus any span whose value the user
    had masked on the way out (`protected`), so it does not resurface at rest —
    however the model re-spaced or re-cased it."""
    wanted = SCOPES[scope]
    if not protected:
        return [hit for hit in hits if hit.category in wanted]
    guarded = {_normalised(category, value) for category, value in _split_protected(protected)}
    return [
        hit
        for hit in hits
        if hit.category in wanted or _normalised(hit.category, text[hit.start : hit.end]) in guarded
    ]


def _split_protected(protected: frozenset[str] | set[str]) -> list[tuple[str, str]]:
    """`protected` holds raw spans; re-detect each to learn how to normalise it."""
    pairs: list[tuple[str, str]] = []
    for value in protected:
        hits = _detections(value)
        pairs.append((hits[0].category if hits else "", value))
    return pairs


def mask(text: str, *, scope: str = "egress", protected: set[str] | None = None) -> tuple[str, int]:
    """`(masked text, hit count)` for `scope` — see `SCOPES`."""
    hits = _in_scope(_detections(text), text, scope, protected or frozenset())
    return _render_masked(text, hits), len(hits)


def protected_values(text: str, *, legacy: bool = False) -> set[str]:
    """The raw spans `egress` masking takes out of `text` — carried to the
    answer's at-rest masking so the user's own details never come back."""
    detector = _legacy_detections if legacy else _detections
    return {
        text[hit.start : hit.end]
        for hit in detector(text or "")
        if hit.category in SCOPES["egress"]
    }


def _render_masked(text: str, hits: list[_Detection]) -> str:
    """Replaces each hit's span with its label."""
    if not hits:
        return text
    parts: list[str] = []
    cursor = 0
    for item in hits:
        parts.extend((text[cursor : item.start], item.label))
        cursor = item.end
    parts.append(text[cursor:])
    return "".join(parts)


def _legacy_detections(text: str) -> list[_Detection]:
    candidates = _detections(text)
    for category, pattern in (
        ("government_id", _LEGACY_RRN),
        ("payment_card", _LEGACY_CARD),
    ):
        for match in pattern.finditer(text):
            item = _Detection(category, _LABELS[category], match.start(), match.end())
            candidates.append(item)
    for start, end in _legacy_email_spans(text):
        category = "role_email" if _is_role_mailbox(text[start:end]) else "email"
        candidates.append(_Detection(category, _LABELS[category], start, end))
    accepted: list[_Detection] = []
    covered_until = -1
    for item in sorted(candidates, key=lambda d: (d.start, -(d.end - d.start), d.category)):
        if item.start < covered_until:
            continue
        accepted.append(item)
        covered_until = item.end
    return accepted


def mask_legacy(
    text: str, *, scope: str = "egress", protected: set[str] | None = None
) -> tuple[str, int]:
    """Mask with the broader legacy patterns; used when `pii_masking` is on."""
    hits = _in_scope(_legacy_detections(text), text, scope, protected or frozenset())
    return _render_masked(text, hits), len(hits)


def findings(
    sources: dict[str, str | list[str]],
    *,
    legacy: bool = False,
    scope: str = "egress",
    protected: set[str] | None = None,
) -> list[Finding]:
    """Scans source-labelled text and aggregates without retaining values.
    Only what `scope` would mask counts, so a page that names a switchboard
    is not a finding."""
    counts: dict[tuple[str, str], int] = {}
    for source, raw in sources.items():
        values = raw if isinstance(raw, list) else [raw]
        for value in values:
            detector = _legacy_detections if legacy else _detections
            for hit in _in_scope(
                detector(value or ""), value or "", scope, protected or frozenset()
            ):
                key = (hit.category, source)
                counts[key] = counts.get(key, 0) + 1
    return [
        Finding(category=category, source=source, count=count)
        for (category, source), count in sorted(counts.items())
    ]


def envelope_digest(sources: dict[str, str | list[str]]) -> str:
    canonical = json.dumps(sources, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def issue_decision_token(
    *, user_id: str, session_id: str, requested_models: list[str], digest: str
) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "typ": "privacy-decision",
            "sub": user_id,
            "sid": session_id,
            "models": requested_models,
            "envelope": digest,
            "iat": now,
            "exp": now + _TOKEN_TTL_SEC,
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )


def verify_decision_token(
    token: str | None,
    *,
    user_id: str,
    session_id: str,
    requested_models: list[str],
    digest: str,
) -> bool:
    if not token:
        return False
    try:
        claims = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        return False
    return (
        claims.get("typ") == "privacy-decision"
        and claims.get("sub") == user_id
        and claims.get("sid") == session_id
        and claims.get("models") == requested_models
        and claims.get("envelope") == digest
    )


def finding_metadata(rows: list[Finding]) -> dict[str, Any]:
    return {
        "policyVersion": POLICY_VERSION,
        "detectorVersion": DETECTOR_VERSION,
        "findings": [row.wire() for row in rows],
    }


def blocked_by(text: str, categories: list[str]) -> str | None:
    """The first configured category found as a substring of `text`, or None."""
    lowered = text.lower()
    for category in categories:
        needle = str(category).strip().lower()
        if needle and needle in lowered:
            return str(category)
    return None


async def sweep_expired(db: AsyncSession) -> int:
    """Blanks message bodies (not metadata) past the retention window. Returns how many."""
    policy = await current(force=True)
    if policy.retention_days <= 0:
        return 0

    cutoff = utcnow() - timedelta(days=policy.retention_days)
    rows = (
        await db.exec(
            select(Message).where(
                col(Message.created_at) < cutoff,
                col(Message.content) != "",
            )
        )
    ).all()
    for message in rows:
        message.content = ""
        message.variants = None
        db.add(message)
    if rows:
        log.info("retention: cleared %d message bodies older than %s", len(rows), cutoff.date())
    return len(rows)


__all__ = [
    "DETECTOR_VERSION",
    "POLICY_VERSION",
    "Finding",
    "GovernanceUnavailable",
    "blocked_by",
    "current",
    "current_for_egress",
    "envelope_digest",
    "finding_metadata",
    "findings",
    "invalidate",
    "issue_decision_token",
    "mask",
    "mask_legacy",
    "sweep_expired",
    "verify_decision_token",
]
