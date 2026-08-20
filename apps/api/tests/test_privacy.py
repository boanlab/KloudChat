from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.models.chat import ChatSession
from app.models.governance import Governance
from app.models.user import User
from app.models.workspace import Artifact, Memory, MemoryType, StoredFile
from app.routers import auth as auth_router
from app.routers import sessions as sessions_router
from app.routers.sessions import _PrivacyResolution, _resolve_privacy
from app.schemas.auth import ProfilePatch
from app.schemas.chat import CompareRequest, SendMessage
from app.services import agent, auto_memory, chat, governance
from app.services import litellm as litellm_service
from app.services import models as model_service
from app.services.models import _shape
from app.services.tools import builtin as builtin_tools
from app.services.tools import registry as tool_registry
from app.services.tools.base import Tool, ToolContext, ToolResult, openai_snapshot
from app.services.workspace_context import (
    ContextBlock,
    WorkspaceContext,
    WorkspaceContextError,
)


def _block(source: str, text: str) -> ContextBlock:
    trusted = source in {"agent", "project_instructions", "skills"}
    return ContextBlock(source=source, text=text, trusted=trusted)


def _workspace(blocks: list[ContextBlock]) -> WorkspaceContext:
    return WorkspaceContext(tuple(blocks), ())


def _rrn(prefix: str = "900101-1") -> str:
    first = prefix.replace("-", "")
    assert len(first) == 7
    weights = (2, 3, 4, 5, 6, 7, 8, 9, 2, 3, 4, 5)
    first += "12345"
    check = (11 - sum(int(n) * w for n, w in zip(first, weights, strict=True)) % 11) % 10
    digits = first + str(check)
    return f"{digits[:6]}-{digits[6:]}"


def test_detector_masks_only_valid_high_precision_values() -> None:
    rrn = _rrn()
    text = (
        f"mail person@example.com, phone +14155552671, local 010-1234-5678, "
        f"rrn {rrn}, card 4111 1111 1111 1111, ip 192.168.10.12, "
        "key sk-abcdefghijklmnopqrstuvwxyz123456"
    )

    masked, count = governance.mask(text)

    assert count == 7
    assert "person@example.com" not in masked
    assert "+14155552671" not in masked
    assert rrn not in masked
    assert "4111 1111 1111 1111" not in masked
    assert "[이메일]" in masked
    assert "[전화번호]" in masked
    assert "[주민번호]" in masked
    assert "[카드번호]" in masked
    assert "[IP주소]" in masked
    assert "[API키]" in masked


def test_detector_accepts_valid_ipv6_and_rejects_malformed_ipv6() -> None:
    text = "remote 2001:db8:85a3::8a2e:370:7334, malformed 2001:db8:::1"

    masked, count = governance.mask(text)

    assert count == 1
    assert "2001:db8:85a3::8a2e:370:7334" not in masked
    assert "2001:db8:::1" in masked


def test_legacy_mask_preserves_the_preexisting_broad_rules() -> None:
    # Neither value passes the new checksum/TLD constraints, but an
    # organisation that enabled the old always-mask setting must not regress.
    text = "card 1234-5678-9012-3456 and mail person@example.x"

    assert governance.mask(text) == (text, 0)
    masked, count = governance.mask_legacy(text)
    assert count == 2
    assert "1234-5678-9012-3456" not in masked
    assert "person@example.x" not in masked


@pytest.mark.parametrize(
    "value",
    [
        "사용자.이름@도메인.한국",
        "prefix/.person@example.x",
        "person@example.x.",
        "person@-.example",
    ],
)
def test_legacy_email_scan_preserves_broad_unicode_and_boundary_rules(value: str) -> None:
    masked, count = governance.mask_legacy(value)

    assert count == 1
    assert value.rstrip(".") not in masked
    assert "[이메일]" in masked


def test_legacy_email_scan_resists_punctuation_repetition() -> None:
    text = "a." * 50_000
    started = time.monotonic()

    assert governance.mask_legacy(text) == (text, 0)
    assert time.monotonic() - started < 2.0


def test_legacy_email_scan_resumes_after_an_adjacent_match() -> None:
    assert governance.mask_legacy("a@b.co+@d.ef") == ("[이메일][이메일]", 2)


def test_detector_handles_long_clean_input_in_linear_time() -> None:
    text = ("ordinary project notes and order 1234-5678. " * 10_000).strip()
    started = time.monotonic()

    assert governance.mask(text) == (text, 0)
    assert time.monotonic() - started < 2.0


def test_detector_email_scan_resists_percent_repetition() -> None:
    text = "%" * 250_000
    started = time.monotonic()

    assert governance.mask(text) == (text, 0)
    assert time.monotonic() - started < 2.0


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("tagged person+tag@example.co.uk", "person+tag@example.co.uk"),
        ("unicode α%person@example.com boundary", "person@example.com"),
        ("진행률50%person@example.com", "person@example.com"),
        ("unicode αperson@example.com boundary", None),
        ("suffix person@example.com_name", None),
        ("bad person@example.1com tld", None),
    ],
)
def test_detector_email_scan_preserves_boundaries(text: str, expected: str | None) -> None:
    masked, count = governance.mask(text)

    assert count == (1 if expected else 0)
    if expected:
        assert expected not in masked
        assert "[이메일]" in masked
    else:
        assert masked == text


def test_detector_masks_finding_heavy_input_with_one_pass_render() -> None:
    text = " ".join(f"person{index}@example.com" for index in range(5_000))
    started = time.monotonic()

    masked, count = governance.mask(text)

    assert count == 5_000
    assert "@example.com" not in masked
    assert masked.count("[이메일]") == 5_000
    assert time.monotonic() - started < 2.0


@pytest.mark.parametrize(
    "value",
    [
        "02-1234-5678",
        "(031) 234-5678",
        "(415) 555-2671",
        "+1 (415) 555-2671",
        "+44 20 7183 8750",
        "+81-3-1234-5678",
    ],
)
def test_detector_covers_regional_and_separated_phone_formats(value: str) -> None:
    assert governance.mask(value) == ("[전화번호]", 1)


def test_rrn_requires_a_real_calendar_date() -> None:
    invalid_calendar = _rrn("990231-1")
    assert governance.mask(invalid_calendar) == (invalid_calendar, 0)


@pytest.mark.parametrize(
    ("value", "label"),
    [
        ("AKIAABCDEFGHIJKLMNOP", "[API키]"),
        ("ASIAABCDEFGHIJKLMNOP", "[API키]"),
        ("AWS_SECRET_ACCESS_KEY=" + "a" * 40, "[API키]"),
        ("AccountKey=" + "a" * 44, "[API키]"),
        ("AIza" + "a" * 35, "[API키]"),
        ("ghp_" + "a" * 36, "[API키]"),
        ("xoxb-" + "a" * 24, "[API키]"),
        ("eyJabcdefgh.abcdefghijk.abcdefghijk", "[JWT]"),
        (
            "-----BEGIN PRIVATE KEY-----\nsecret material\n-----END PRIVATE KEY-----",
            "[개인키]",
        ),
        (
            "-----BEGIN ENCRYPTED PRIVATE KEY-----\nsecret\n-----END ENCRYPTED PRIVATE KEY-----",
            "[개인키]",
        ),
    ],
)
def test_detector_covers_major_credentials(value: str, label: str) -> None:
    assert governance.mask(value) == (label, 1)


def test_detector_rejects_order_numbers_and_invalid_checksums() -> None:
    text = (
        "주문 1234-5678-9012-3456, 번호 1111111111111111, 주소 999.2.3.4, "
        "날짜 2026-08-16, 표 123-012-4567, secret=" + "a" * 40
    )
    assert governance.mask(text) == (text, 0)


def test_findings_keep_source_and_never_the_value() -> None:
    rows = governance.findings(
        {
            "current_input": "clean",
            "conversation_history": ["person@example.com"],
            "attachments": ["+442071838750"],
        }
    )
    wire = [row.wire() for row in rows]
    encoded = json.dumps(wire)

    assert wire == [
        {"category": "email", "source": "conversation_history", "count": 1},
        {"category": "phone", "source": "attachments", "count": 1},
    ]
    assert "person@example.com" not in encoded
    assert "+442071838750" not in encoded


def test_outbound_envelope_preserves_every_workspace_source() -> None:
    history = [
        sessions_router.Message(
            session_id="session",
            role=sessions_router.Role.user,
            content="history@example.com",
        )
    ]
    raw_sources = [
        "attachment",
        "project.instructions",
        "project.knowledge",
        "memory",
        "agent.instructions",
        "skill:selected-id",
    ]
    blocks = [_block(source, f"{source}@example.com") for source in raw_sources]
    sources = sessions_router._privacy_sources(
        "current@example.com",
        history,
        blocks,
    )

    findings = governance.findings(sources)
    assert {row.source for row in findings} == {
        "current_input",
        "conversation_history",
        "attachments",
        "project_instructions",
        "project_knowledge",
        "memory",
        "agent",
        "skills",
    }


@pytest.mark.asyncio
async def test_governance_read_failure_fails_closed_even_with_stale_guard_off(
    monkeypatch,
) -> None:
    class BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, *_args):
            return None

    previous = dict(governance._cache)
    governance._cache.update(
        at=0.0,
        value=Governance(
            external_data_guard=False,
            allow_user_raw_external=True,
            privacy_safe_model_ids=["strict-local/known"],
        ),
    )
    monkeypatch.setattr(governance, "SessionLocal", BrokenSession)
    try:
        policy = await governance.current(force=True)
    finally:
        governance._cache.clear()
        governance._cache.update(previous)

    assert policy.external_data_guard is True
    assert policy.allow_user_raw_external is False
    assert policy.privacy_safe_model_ids == ["strict-local/known"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stale",
    [
        None,
        Governance(
            external_data_guard=False,
            pii_masking=False,
            intent_filter=False,
            allow_user_raw_external=True,
            privacy_safe_model_ids=["strict-local/stale"],
        ),
    ],
    ids=["cold-worker", "hot-stale-worker"],
)
async def test_egress_policy_read_failure_never_returns_default_or_stale_snapshot(
    monkeypatch,
    stale,
) -> None:
    class BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, *_args):
            return None

    previous = dict(governance._cache)
    governance._cache.update(
        at=time.monotonic() if stale is not None else 0.0,
        value=stale,
    )
    monkeypatch.setattr(governance, "SessionLocal", BrokenSession)
    try:
        with pytest.raises(governance.GovernanceUnavailable):
            await governance.current_for_egress()
    finally:
        governance._cache.clear()
        governance._cache.update(previous)


@pytest.mark.asyncio
async def test_egress_policy_denies_raw_immediately_after_other_worker_revokes_it(
    monkeypatch,
) -> None:
    """A hot process-local cache must not authorize an outbound retry."""

    stale = Governance(
        external_data_guard=True,
        allow_user_raw_external=True,
    )
    revoked = Governance(
        external_data_guard=True,
        allow_user_raw_external=False,
    )
    reads = 0

    class FreshPolicySession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, row_id):
            nonlocal reads
            assert model is Governance
            assert row_id == "default"
            reads += 1
            return revoked

    previous = dict(governance._cache)
    governance._cache.update(at=time.monotonic(), value=stale)
    monkeypatch.setattr(governance, "SessionLocal", FreshPolicySession)
    monkeypatch.setattr(
        governance.settings,
        "jwt_secret",
        "test-secret-that-is-at-least-32-bytes",
    )
    sources = {"current_input": "owner@example.com"}
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    external = {
        "id": "external/model",
        "label": "External model",
        "dataBoundary": "external",
        "strictLocal": False,
        "kinds": ["chat"],
    }
    token = governance.issue_decision_token(
        user_id=user.id,
        session_id=session.id,
        requested_models=[external["id"]],
        digest=governance.envelope_digest(sources),
    )
    try:
        policy = await governance.current_for_egress()
        resolved = await _resolve_privacy(
            user=user,
            session=session,
            policy=policy,
            catalogue=[external],
            requested=[external],
            sources=sources,
            explicit_action="send_raw_external",
            decision_token=token,
        )
    finally:
        governance._cache.clear()
        governance._cache.update(previous)

    assert reads == 1
    assert policy.allow_user_raw_external is False
    assert isinstance(resolved, JSONResponse)
    contract = json.loads(resolved.body)
    assert contract["code"] == "privacy_decision_required"
    assert "send_raw_external" not in contract["allowedActions"]


def test_model_boundary_uses_only_explicit_proxy_metadata() -> None:
    strict = _shape(
        {
            "model_name": "strict-local/qwen",
            "model_info": {
                "mode": "chat",
                "litellm_provider": "hosted_vllm",
                "kchat_data_boundary": "self_hosted",
                "kchat_strict_local": True,
                "kchat_privacy_only": True,
            },
            "litellm_params": {"api_base": "http://vllm:8000"},
        }
    )
    misleading = _shape(
        {
            "model_name": "local/qwen",
            "model_info": {"mode": "chat", "litellm_provider": "hosted_vllm"},
            "litellm_params": {"api_base": "http://vllm:8000"},
        }
    )
    malformed = _shape(
        {
            "model_name": "malformed/model",
            "model_info": {
                "mode": "chat",
                "litellm_provider": "hosted_vllm",
                "kchat_data_boundary": ["self_hosted"],
                "kchat_strict_local": True,
            },
            "litellm_params": {"api_base": "http://vllm:8000"},
        }
    )

    assert strict is not None
    assert strict["dataBoundary"] == "self_hosted"
    assert strict["strictLocal"] is True
    assert strict["privacyOnly"] is True
    assert misleading is not None
    assert misleading["dataBoundary"] == "unknown"
    assert misleading["strictLocal"] is False
    assert malformed is not None
    assert malformed["dataBoundary"] == "unknown"
    assert malformed["strictLocal"] is False


@pytest.mark.asyncio
async def test_egress_catalogue_rechecks_hot_strict_alias_and_denies_remap(
    monkeypatch,
) -> None:
    """A display-cache hit cannot authorize a privacy-only route."""
    alias = "strict-local/qwen"
    cached_strict = {
        **_external_model(alias),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
    }

    async def live_info():
        return [
            {
                "model_name": alias,
                "model_info": {
                    "mode": "chat",
                    "litellm_provider": "openrouter",
                    "input_cost_per_token": 0.000001,
                    "output_cost_per_token": 0.000002,
                    "kchat_data_boundary": "external",
                    "kchat_strict_local": False,
                },
                "litellm_params": {"custom_llm_provider": "openrouter"},
            }
        ]

    previous_cache = dict(model_service._CACHE)
    previous_unpriced = dict(model_service._unpriced)
    model_service._CACHE.update(
        at=time.monotonic(),
        value={
            "models": [cached_strict],
            "litellmAvailable": True,
            "defaultChatModel": alias,
        },
    )
    monkeypatch.setattr(litellm_service, "model_info", live_info)
    monkeypatch.setattr(
        governance.settings,
        "jwt_secret",
        "test-secret-that-is-at-least-32-bytes",
    )
    try:
        catalogue = await model_service.list_models_for_egress()
        live = model_service.find(catalogue["models"], alias)
        assert live is not None
        assert live["dataBoundary"] == "external"
        assert live["strictLocal"] is False

        user = User(
            email="person@example.test",
            password_hash="hash",
            name="Person",
            preferences={"privacyDefaultAction": "route_strict_local"},
        )
        session = ChatSession(user_id=user.id)
        sources = {"current_input": "owner@example.com"}
        token = governance.issue_decision_token(
            user_id=user.id,
            session_id=session.id,
            requested_models=[alias],
            digest=governance.envelope_digest(sources),
        )
        resolved = await _resolve_privacy(
            user=user,
            session=session,
            policy=Governance(
                external_data_guard=True,
                privacy_safe_model_ids=[alias],
            ),
            catalogue=catalogue["models"],
            requested=[live],
            sources=sources,
            explicit_action="route_strict_local",
            decision_token=token,
        )

        async def owned(*_args, **_kwargs):
            return session

        async def current_policy(*_args, **_kwargs):
            return Governance(
                external_data_guard=True,
                privacy_safe_model_ids=[alias],
            )

        async def no_rows(*_args, **_kwargs):
            return []

        async def no_context(*_args, **_kwargs):
            return _workspace([])

        async def no_settings(*_args, **_kwargs):
            return None, [], None

        async def no_audit(*_args, **_kwargs):
            return None

        async def forbidden_upstream(*_args, **_kwargs):
            pytest.fail("cached strict alias reached upstream after live remap")

        monkeypatch.setattr(sessions_router, "_owned", owned)
        monkeypatch.setattr(
            sessions_router.governance,
            "current_for_egress",
            current_policy,
        )
        monkeypatch.setattr(sessions_router, "_history", no_rows)
        monkeypatch.setattr(sessions_router, "assemble", no_context)
        monkeypatch.setattr(sessions_router, "agent_settings", no_settings)
        monkeypatch.setattr(sessions_router, "_audit_policy", no_audit)
        monkeypatch.setattr(
            sessions_router,
            "has_headroom",
            lambda *_args: pytest.fail("credit check ran before live-boundary refusal"),
        )
        monkeypatch.setattr(
            sessions_router.litellm_service,
            "ensure_key",
            forbidden_upstream,
        )
        monkeypatch.setattr(sessions_router, "_run_turn", forbidden_upstream)
        endpoint_db = _NoWriteDb()
        endpoint_response = await sessions_router.send_message(
            session.id,
            SendMessage(content="owner@example.com", model=alias),
            _request(),
            user,
            endpoint_db,
        )
    finally:
        model_service._CACHE.clear()
        model_service._CACHE.update(previous_cache)
        model_service._unpriced.clear()
        model_service._unpriced.update(previous_unpriced)

    assert isinstance(resolved, JSONResponse)
    contract = json.loads(resolved.body)
    assert contract["safeModels"] == []
    assert "route_strict_local" not in contract["allowedActions"]
    assert isinstance(endpoint_response, JSONResponse)
    assert endpoint_response.status_code == 409
    assert json.loads(endpoint_response.body)["safeModels"] == []
    assert endpoint_db.added == []
    assert endpoint_db.commits == 0


@pytest.mark.asyncio
async def test_egress_catalogue_fails_closed_when_live_gateway_is_unavailable(
    monkeypatch,
) -> None:
    alias = "strict-local/qwen"
    cached_strict = {
        **_external_model(alias),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
    }

    async def unavailable():
        raise litellm_service.LiteLLMError("gateway unavailable")

    previous_cache = dict(model_service._CACHE)
    previous_unpriced = dict(model_service._unpriced)
    model_service._CACHE.update(
        at=time.monotonic(),
        value={
            "models": [cached_strict],
            "litellmAvailable": True,
            "defaultChatModel": alias,
        },
    )
    monkeypatch.setattr(litellm_service, "model_info", unavailable)
    try:
        catalogue = await model_service.list_models_for_egress()
    finally:
        model_service._CACHE.clear()
        model_service._CACHE.update(previous_cache)
        model_service._unpriced.clear()
        model_service._unpriced.update(previous_unpriced)

    assert catalogue["litellmAvailable"] is False
    assert catalogue["models"] == []
    assert catalogue["defaultChatModel"] == ""


@pytest.mark.asyncio
async def test_decision_token_binds_retry_to_same_envelope_and_models(monkeypatch) -> None:
    monkeypatch.setattr(governance.settings, "jwt_secret", "test-secret-that-is-at-least-32-bytes")
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    external = {
        "id": "external/model",
        "label": "External",
        "dataBoundary": "external",
        "strictLocal": False,
        "kinds": ["chat"],
    }
    strict = {
        "id": "strict-local/model",
        "label": "Strict",
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "kinds": ["chat"],
    }
    policy = Governance(
        external_data_guard=True,
        privacy_safe_model_ids=[strict["id"]],
    )
    sources = {"current_input": "send person@example.com", "conversation_history": []}

    first = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=[external, strict],
        requested=[external],
        sources=sources,
        explicit_action=None,
        decision_token=None,
    )
    assert isinstance(first, JSONResponse)
    contract = json.loads(first.body)
    assert contract["code"] == "privacy_decision_required"
    assert "person@example.com" not in first.body.decode()

    accepted = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=[external, strict],
        requested=[external],
        sources=sources,
        explicit_action="route_strict_local",
        decision_token=contract["decisionToken"],
    )
    assert isinstance(accepted, _PrivacyResolution)
    assert accepted.models == [strict]
    assert accepted.action == "route_strict_local"

    changed = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=[external, strict],
        requested=[external],
        sources={**sources, "current_input": "changed person@example.com"},
        explicit_action="route_strict_local",
        decision_token=contract["decisionToken"],
    )
    assert isinstance(changed, JSONResponse)


@pytest.mark.asyncio
async def test_mask_raw_and_legacy_upper_bound_actions(monkeypatch) -> None:
    monkeypatch.setattr(governance.settings, "jwt_secret", "test-secret-that-is-at-least-32-bytes")
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    external = _external_model("external/model")
    sources = {"current_input": "send person@example.com"}
    policy = Governance(external_data_guard=True, allow_user_raw_external=True)

    first = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=[external],
        requested=[external],
        sources=sources,
        explicit_action=None,
        decision_token=None,
    )
    assert isinstance(first, JSONResponse)
    token = json.loads(first.body)["decisionToken"]

    masked = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=[external],
        requested=[external],
        sources=sources,
        explicit_action="mask_external",
        decision_token=token,
    )
    assert isinstance(masked, _PrivacyResolution)
    assert masked.action == "mask_external"
    assert masked.mask_outbound is True

    raw = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=[external],
        requested=[external],
        sources=sources,
        explicit_action="send_raw_external",
        decision_token=token,
    )
    assert isinstance(raw, _PrivacyResolution)
    assert raw.action == "send_raw_external"
    assert raw.mask_outbound is False

    legacy = await _resolve_privacy(
        user=user,
        session=session,
        policy=Governance(
            pii_masking=True,
            external_data_guard=True,
            allow_user_raw_external=True,
        ),
        catalogue=[external],
        requested=[external],
        sources=sources,
        explicit_action="send_raw_external",
        decision_token=token,
    )
    assert isinstance(legacy, _PrivacyResolution)
    assert legacy.action == "mask_external"
    assert legacy.mask_outbound is True


@pytest.mark.asyncio
async def test_raw_external_still_carries_what_the_record_was_masked_by(monkeypatch) -> None:
    """Raw egress is not a raw record.

    `send_raw_external` sends the sentence untouched and stores the Message
    masked all the same. The transcript explains that substitution from the
    findings kept on the routing, so they have to survive the one action whose
    name says nothing was hidden.
    """
    monkeypatch.setattr(governance.settings, "jwt_secret", "test-secret-that-is-at-least-32-bytes")
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    external = _external_model("external/model")
    sources = {"current_input": "send person@example.com"}
    policy = Governance(external_data_guard=True, allow_user_raw_external=True)

    asked = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=[external],
        requested=[external],
        sources=sources,
        explicit_action=None,
        decision_token=None,
    )
    assert isinstance(asked, JSONResponse)

    raw = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=[external],
        requested=[external],
        sources=sources,
        explicit_action="send_raw_external",
        decision_token=json.loads(asked.body)["decisionToken"],
    )
    assert isinstance(raw, _PrivacyResolution)
    assert raw.mask_outbound is False
    assert raw.findings
    assert raw.routing["findingCounts"] == [
        {"category": "email", "source": "current_input", "count": 1}
    ]
    # What is written where the person's sentence used to be.
    assert governance.mask(sources["current_input"])[0] == "send [이메일]"


@pytest.mark.asyncio
async def test_safe_route_priority_matches_visible_catalogue_order(monkeypatch) -> None:
    monkeypatch.setattr(governance.settings, "jwt_secret", "test-secret-that-is-at-least-32-bytes")
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    external = _external_model("external/model")
    strict_a = {
        **_external_model("strict-local/a"),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
    }
    strict_b = {
        **_external_model("strict-local/b"),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
    }
    policy = Governance(
        external_data_guard=True,
        # Deliberately opposite to the visible catalogue order. Legacy rows
        # are normalized at resolution as well as on the next admin save.
        privacy_safe_model_ids=[strict_b["id"], strict_a["id"]],
    )
    sources = {"current_input": "send person@example.com"}
    catalogue = [strict_a, strict_b, external]

    first = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=catalogue,
        requested=[external],
        sources=sources,
        explicit_action=None,
        decision_token=None,
    )
    assert isinstance(first, JSONResponse)
    token = json.loads(first.body)["decisionToken"]
    routed = await _resolve_privacy(
        user=user,
        session=session,
        policy=policy,
        catalogue=catalogue,
        requested=[external],
        sources=sources,
        explicit_action="route_strict_local",
        decision_token=token,
    )

    assert isinstance(routed, _PrivacyResolution)
    assert routed.models == [strict_a]


class _NoWriteDb:
    def __init__(self) -> None:
        self.added: list[object] = []
        self.commits = 0

    def add(self, value) -> None:
        self.added.append(value)

    async def commit(self) -> None:
        self.commits += 1


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("stale", "kind", "model_id"),
    [
        (None, sessions_router.SessionKind.chat, "external/model"),
        (
            Governance(
                external_data_guard=False,
                allow_user_raw_external=True,
                privacy_safe_model_ids=["strict-local/model"],
            ),
            sessions_router.SessionKind.chat,
            "strict-local/model",
        ),
        (
            Governance(pii_masking=True, intent_filter=True),
            sessions_router.SessionKind.report,
            "external/report-model",
        ),
    ],
    ids=["cold-external", "hot-strict", "hot-legacy-report"],
)
async def test_governance_unavailable_blocks_send_before_catalogue_or_write(
    monkeypatch,
    stale,
    kind,
    model_id,
) -> None:
    class BrokenPolicySession:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, *_args):
            return None

    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id, kind=kind, model=model_id)
    catalogue_calls = 0

    async def owned(*_args, **_kwargs):
        return session

    async def catalogue(*_args, **_kwargs):
        nonlocal catalogue_calls
        catalogue_calls += 1
        return {"models": []}

    async def forbidden_upstream(*_args, **_kwargs):
        pytest.fail("governance-unavailable turn reached an upstream path")

    previous = dict(governance._cache)
    governance._cache.update(
        at=time.monotonic() if stale is not None else 0.0,
        value=stale,
    )
    monkeypatch.setattr(governance, "SessionLocal", BrokenPolicySession)
    monkeypatch.setattr(sessions_router, "_owned", owned)
    monkeypatch.setattr(
        sessions_router.model_service,
        "list_models_for_egress",
        catalogue,
    )
    monkeypatch.setattr(
        sessions_router.litellm_service,
        "ensure_key",
        forbidden_upstream,
    )
    monkeypatch.setattr(sessions_router, "_run_turn", forbidden_upstream)
    db = _NoWriteDb()
    try:
        with pytest.raises(Exception) as caught:
            await sessions_router.send_message(
                session.id,
                SendMessage(content="clean request"),
                _request(),
                user,
                db,
            )
    finally:
        governance._cache.clear()
        governance._cache.update(previous)

    assert getattr(caught.value, "status_code", None) == 503
    assert getattr(caught.value, "detail", None) == "governance_unavailable"
    assert catalogue_calls == 0
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_governance_unavailable_blocks_compare_before_catalogue_or_fanout(
    monkeypatch,
) -> None:
    class BrokenPolicySession:
        async def __aenter__(self):
            raise RuntimeError("database unavailable")

        async def __aexit__(self, *_args):
            return None

    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    catalogue_calls = 0

    async def owned(*_args, **_kwargs):
        return session

    async def catalogue(*_args, **_kwargs):
        nonlocal catalogue_calls
        catalogue_calls += 1
        return {"models": []}

    previous = dict(governance._cache)
    governance._cache.update(
        at=time.monotonic(),
        value=Governance(
            external_data_guard=False,
            allow_user_raw_external=True,
        ),
    )
    monkeypatch.setattr(governance, "SessionLocal", BrokenPolicySession)
    monkeypatch.setattr(sessions_router, "_owned", owned)
    monkeypatch.setattr(
        sessions_router.model_service,
        "list_models_for_egress",
        catalogue,
    )
    db = _NoWriteDb()
    try:
        with pytest.raises(Exception) as caught:
            await sessions_router.compare_models(
                session.id,
                CompareRequest(
                    content="clean request",
                    models=["external/one", "external/two"],
                ),
                _request(),
                user,
                db,
            )
    finally:
        governance._cache.clear()
        governance._cache.update(previous)

    assert getattr(caught.value, "status_code", None) == 503
    assert getattr(caught.value, "detail", None) == "governance_unavailable"
    assert catalogue_calls == 0
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_raw_external_default_save_returns_503_when_policy_is_unavailable(
    monkeypatch,
) -> None:
    async def unavailable():
        raise governance.GovernanceUnavailable

    user = User(email="person@example.test", password_hash="hash", name="Person")
    monkeypatch.setattr(
        auth_router.governance_service,
        "current_for_egress",
        unavailable,
    )
    db = _NoWriteDb()

    with pytest.raises(Exception) as caught:
        await auth_router.update_me(
            ProfilePatch(preferences={"privacyDefaultAction": "send_raw_external"}),
            user,
            db,
        )

    assert getattr(caught.value, "status_code", None) == 503
    assert getattr(caught.value, "detail", None) == "governance_unavailable"
    assert user.preferences == {}
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_privacy_refusal_audit_targets_session_not_user_email(monkeypatch) -> None:
    user = User(email="person@example.test", password_hash="hash", name="Person")
    added: list[object] = []

    class AuditDb:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add(self, value):
            added.append(value)

        async def commit(self):
            return None

    monkeypatch.setattr(sessions_router, "SessionLocal", AuditDb)
    await sessions_router._audit_policy(
        user,
        _request(),
        "privacy.decision_required",
        governance.DETECTOR_VERSION,
        metadata={"sessionId": "session-1", "findings": []},
    )

    event = next(row for row in added if isinstance(row, sessions_router.AuditEvent))
    assert event.target == "session-1"
    assert user.email not in json.dumps(event.event_metadata)


def _external_model(model_id: str) -> dict:
    return {
        "id": model_id,
        "label": model_id,
        "dataBoundary": "external",
        "strictLocal": False,
        "kinds": ["chat"],
        "creditCost": 1,
    }


async def _patch_guard_dependencies(
    monkeypatch,
    *,
    session: ChatSession,
    models: list[dict],
    blocks: list[ContextBlock],
) -> None:
    monkeypatch.setattr(
        governance.settings,
        "jwt_secret",
        "test-secret-that-is-at-least-32-bytes",
    )

    async def owned(*_args, **_kwargs):
        return session

    async def current(*_args, **_kwargs):
        return Governance(external_data_guard=True)

    async def catalogue(*_args, **_kwargs):
        return {"models": models}

    async def no_history(*_args, **_kwargs):
        return []

    async def context(*_args, **_kwargs):
        return _workspace(blocks)

    async def settings(*_args, **_kwargs):
        return None, [], None

    async def audit(*_args, **_kwargs):
        return None

    monkeypatch.setattr(sessions_router, "_owned", owned)
    monkeypatch.setattr(sessions_router.governance, "current_for_egress", current)
    monkeypatch.setattr(sessions_router.model_service, "list_models_for_egress", catalogue)
    monkeypatch.setattr(sessions_router, "_history", no_history)
    monkeypatch.setattr(sessions_router, "assemble", context)
    monkeypatch.setattr(sessions_router, "agent_settings", settings)
    monkeypatch.setattr(sessions_router, "_audit_policy", audit)
    monkeypatch.setattr(
        sessions_router,
        "has_headroom",
        lambda *_args, **_kwargs: pytest.fail("credit check ran before privacy decision"),
    )


@pytest.mark.asyncio
async def test_chat_409_precedes_message_write_credit_and_upstream(monkeypatch) -> None:
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    model = _external_model("external/one")
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[model],
        blocks=[],
    )
    db = _NoWriteDb()

    response = await sessions_router.send_message(
        session.id,
        SendMessage(content="send person@example.com"),
        _request(),
        user,
        db,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_auto_privacy_refusal_precedes_classifier_key_and_write(monkeypatch) -> None:
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(
        user_id=user.id,
        model="external/quality",
        routing_mode=sessions_router.RoutingMode.auto,
    )
    quality = {**_external_model("external/quality"), "inputCreditCost": 10}
    classifier = {
        **_external_model("strict-local/classifier"),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "privacyOnly": True,
        "inputCreditCost": 0,
        "creditCost": 0,
        "contextWindow": 32_000,
    }
    economy = {
        **_external_model("external/economy"),
        "inputCreditCost": 1,
        "contextWindow": 32_000,
    }
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[quality, classifier, economy],
        blocks=[_block("memory", "workspace owner owner@example.com")],
    )

    async def policy(*_args, **_kwargs):
        return Governance(
            external_data_guard=True,
            adaptive_routing_enabled=True,
            adaptive_classifier_model_id=classifier["id"],
            adaptive_economy_model_ids=[economy["id"]],
        )

    async def forbidden(*_args, **_kwargs):
        pytest.fail("classifier or key issuance ran before privacy refusal")

    monkeypatch.setattr(sessions_router.governance, "current_for_egress", policy)
    monkeypatch.setattr(sessions_router.adaptive_routing, "classify", forbidden)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", forbidden)
    db = _NoWriteDb()

    response = await sessions_router.send_message(
        session.id,
        SendMessage(content="clean current input"),
        _request(),
        user,
        db,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_auto_no_candidate_skips_classifier_and_key(monkeypatch) -> None:
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(
        user_id=user.id,
        model="external/quality",
        routing_mode=sessions_router.RoutingMode.auto,
    )
    quality = {
        **_external_model("external/quality"),
        "inputCreditCost": 1,
        "contextWindow": 32_000,
    }
    classifier = {
        **_external_model("strict-local/classifier"),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "privacyOnly": True,
        "inputCreditCost": 0,
        "creditCost": 0,
        "contextWindow": 32_000,
    }
    # Equal in both directions, so this is not a saving candidate.
    economy = {
        **_external_model("external/not-cheaper"),
        "inputCreditCost": 1,
        "creditCost": 1,
        "contextWindow": 32_000,
    }
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[quality, classifier, economy],
        blocks=[],
    )

    async def policy(*_args, **_kwargs):
        return Governance(
            external_data_guard=True,
            adaptive_routing_enabled=True,
            adaptive_classifier_model_id=classifier["id"],
            adaptive_economy_model_ids=[economy["id"]],
        )

    async def forbidden(*_args, **_kwargs):
        pytest.fail("no-candidate Auto turn called classifier or issued a key")

    monkeypatch.setattr(sessions_router.governance, "current_for_egress", policy)
    monkeypatch.setattr(sessions_router.adaptive_routing, "classify", forbidden)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", forbidden)
    monkeypatch.setattr(
        sessions_router.litellm_service,
        "user_key",
        lambda *_args: pytest.fail("no-candidate Auto turn read a key"),
    )
    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: False)
    db = _NoWriteDb()

    with pytest.raises(Exception) as caught:
        await sessions_router.send_message(
            session.id,
            SendMessage(content="clean"),
            _request(),
            user,
            db,
        )

    assert getattr(caught.value, "detail", None) == "insufficient_credits"
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_auto_routed_economy_turn_strips_exposed_tools_and_fallback(
    monkeypatch,
) -> None:
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(
        user_id=user.id,
        model="external/quality",
        routing_mode=sessions_router.RoutingMode.auto,
    )
    quality = {
        **_external_model("external/quality"),
        "inputCreditCost": 10,
        "creditCost": 20,
        "contextWindow": 64_000,
        "supportsTools": True,
    }
    classifier = {
        **_external_model("strict-local/classifier"),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "privacyOnly": True,
        "inputCreditCost": 0,
        "creditCost": 0,
        "contextWindow": 32_000,
    }
    economy = {
        **_external_model("external/economy"),
        "inputCreditCost": 1,
        "creditCost": 2,
        "contextWindow": 4_096,
        "supportsTools": False,
    }
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[quality, classifier, economy],
        blocks=[_block("memory", "global memory requiring specialised analysis")],
    )

    async def policy(*_args, **_kwargs):
        return Governance(
            external_data_guard=True,
            adaptive_routing_enabled=True,
            adaptive_classifier_model_id=classifier["id"],
            adaptive_economy_model_ids=[economy["id"]],
        )

    async def runner(_arguments):
        return ToolResult(content="x" * 100_000)

    exposed = Tool(
        name="large_result_tool",
        label="Large result tool",
        description="May return a result larger than a small model context window",
        parameters={"type": "object", "properties": {}},
        run=runner,
    )

    async def tools(*_args, **_kwargs):
        return [exposed]

    classifier_envelope: dict = {}

    async def classify(**kwargs):
        classifier_envelope.update(json.loads(kwargs["context"]))
        return sessions_router.adaptive_routing.Classification(
            "low", 0.99, "simple_factual", 30, 4
        )

    captured: dict = {}

    async def stream(**kwargs):
        captured.update(kwargs)
        yield sessions_router.chat_service.sse({"type": "done"})

    async def ensure_key(*_args, **_kwargs):
        return "virtual-key"

    async def credentials(*_args, **_kwargs):
        return "http://litellm.test", "virtual-key"

    class AcceptedDb(_NoWriteDb):
        def is_modified(self, _value):
            return False

    monkeypatch.setattr(sessions_router.governance, "current_for_egress", policy)
    monkeypatch.setattr(sessions_router, "build_tools", tools)
    monkeypatch.setattr(sessions_router.adaptive_routing, "classify", classify)
    monkeypatch.setattr(sessions_router.litellm_service, "user_key", lambda _user: "virtual-key")
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions_router, "_run_turn", stream)

    response = await sessions_router.send_message(
        session.id,
        SendMessage(content="What is two plus two?"),
        _request(),
        user,
        AcceptedDb(),
    )
    _ = [chunk async for chunk in response.body_iterator]

    assert classifier_envelope["qualityModelTools"][0]["function"]["name"] == exposed.name
    assert "global memory requiring specialised analysis" in json.dumps(
        classifier_envelope["messages"], ensure_ascii=False
    )
    assert captured["model"]["id"] == economy["id"]
    assert captured["tools"] == []
    assert captured["tool_definitions"] == []
    assert captured["disable_fallbacks"] is True
    assert "도구 사용 규칙" not in captured["messages"][0]["content"]


@pytest.mark.asyncio
async def test_auto_requires_persisted_quality_model_instead_of_agent_fallback(
    monkeypatch,
) -> None:
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(
        user_id=user.id,
        model="",
        agent_id="agent-1",
        routing_mode=sessions_router.RoutingMode.auto,
    )
    agent_model = _external_model("external/agent-default")
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[agent_model],
        blocks=[],
    )

    async def settings(*_args, **_kwargs):
        return agent_model["id"], [], None

    monkeypatch.setattr(sessions_router, "agent_settings", settings)

    with pytest.raises(Exception) as caught:
        await sessions_router.send_message(
            session.id,
            SendMessage(content="clean"),
            _request(),
            user,
            _NoWriteDb(),
        )

    assert getattr(caught.value, "status_code", None) == 409
    assert getattr(caught.value, "detail", None) == "auto_quality_model_required"


@pytest.mark.asyncio
async def test_auto_rejects_privacy_only_quality_model_at_send(monkeypatch) -> None:
    user = User(email="person@example.test", password_hash="hash", name="Person")
    reserved = {
        **_external_model("strict-local/classifier-only"),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "privacyOnly": True,
    }
    session = ChatSession(
        user_id=user.id,
        model=reserved["id"],
        routing_mode=sessions_router.RoutingMode.auto,
    )
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[reserved],
        blocks=[],
    )

    with pytest.raises(Exception) as caught:
        await sessions_router.send_message(
            session.id,
            SendMessage(content="ordinary answer request"),
            _request(),
            user,
            _NoWriteDb(),
        )

    assert getattr(caught.value, "status_code", None) == 409
    assert getattr(caught.value, "detail", None) == "auto_quality_model_required"


@pytest.mark.asyncio
async def test_auto_turn_model_override_does_not_replace_quality_ceiling(
    monkeypatch,
) -> None:
    user = User(email="person@example.test", password_hash="hash", name="Person")
    quality = _external_model("external/quality-a")
    one_turn = _external_model("external/one-turn-b")
    session = ChatSession(
        user_id=user.id,
        model=quality["id"],
        routing_mode=sessions_router.RoutingMode.auto,
    )
    # The database column is String, so production hydration may yield the raw
    # value even though the model annotation is RoutingMode.
    session.routing_mode = sessions_router.RoutingMode.auto.value
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[quality, one_turn],
        blocks=[],
    )

    executed: list[str] = []

    async def stream(**kwargs):
        executed.append(kwargs["model"]["id"])
        yield sessions_router.chat_service.sse({"type": "done"})

    async def ensure_key(*_args, **_kwargs):
        return "virtual-key"

    async def credentials(*_args, **_kwargs):
        return "http://litellm.test", "virtual-key"

    class AcceptedDb(_NoWriteDb):
        def is_modified(self, _value):
            return False

    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions_router, "_run_turn", stream)
    db = AcceptedDb()

    response = await sessions_router.send_message(
        session.id,
        SendMessage(content="run once with B", model=one_turn["id"]),
        _request(),
        user,
        db,
    )
    _ = [chunk async for chunk in response.body_iterator]

    assert executed == [one_turn["id"]]
    assert session.model == quality["id"]
    assert session.routing_mode == sessions_router.RoutingMode.auto

    response = await sessions_router.send_message(
        session.id,
        SendMessage(content="use Auto quality ceiling again"),
        _request(),
        user,
        db,
    )
    _ = [chunk async for chunk in response.body_iterator]

    assert executed == [one_turn["id"], quality["id"]]
    assert session.model == quality["id"]


@pytest.mark.asyncio
async def test_tool_schema_is_preflighted_and_mask_retry_sends_only_clean_snapshot(
    monkeypatch,
) -> None:
    """The inspected schema and every streamed hop share one exact snapshot."""
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    model = {**_external_model("external/tools"), "supportsTools": True}
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[model],
        blocks=[],
    )

    async def runner(_arguments):
        return ToolResult(content="ok")

    clean = Tool(
        name="a_clean",
        description="A deterministic safe helper",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
        run=runner,
        label="clean",
    )
    raw_email = "schema-owner@example.com"
    raw_key = "sk-abcdefghijklmnopqrstuvwxyz123456"
    sensitive = Tool(
        name="z_sensitive",
        description=f"Look up the record owned by {raw_email}",
        parameters={
            "type": "object",
            "properties": {
                "credential": {
                    "type": "string",
                    "examples": [{"nested": [raw_key]}],
                }
            },
        },
        run=runner,
        label="sensitive",
        source="connector",
    )
    builds = 0

    async def tools(*_args, **_kwargs):
        nonlocal builds
        builds += 1
        # Opposite registry order on retry must not invalidate an otherwise
        # identical decision token.
        return [sensitive, clean] if builds == 1 else [clean, sensitive]

    monkeypatch.setattr(sessions_router, "build_tools", tools)
    refusal_db = _NoWriteDb()
    first = await sessions_router.send_message(
        session.id,
        SendMessage(content="clean request"),
        _request(),
        user,
        refusal_db,
    )

    assert isinstance(first, JSONResponse)
    contract = json.loads(first.body)
    assert first.status_code == 409
    assert {(finding["category"], finding["source"]) for finding in contract["findings"]} == {
        ("email", "tool_definitions"),
        ("api_key", "tool_definitions"),
    }
    assert raw_email not in first.body.decode()
    assert raw_key not in first.body.decode()
    assert refusal_db.added == []
    assert refusal_db.commits == 0

    captured: dict = {}

    async def stream(**kwargs):
        captured.update(kwargs)
        yield sessions_router.chat_service.sse({"type": "done"})

    async def ensure_key(*_args, **_kwargs):
        return None

    async def credentials(*_args, **_kwargs):
        return "http://litellm.test", "key"

    class AcceptedDb(_NoWriteDb):
        def is_modified(self, _value):
            return False

    monkeypatch.setattr(sessions_router, "_run_turn", stream)
    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)
    accepted_db = AcceptedDb()
    accepted = await sessions_router.send_message(
        session.id,
        SendMessage(
            content="clean request",
            privacy_action="mask_external",
            privacy_decision_token=contract["decisionToken"],
        ),
        _request(),
        user,
        accepted_db,
    )
    _ = [chunk async for chunk in accepted.body_iterator]

    assert builds == 2
    assert [tool.name for tool in captured["tools"]] == ["a_clean"]
    assert captured["tool_definitions"] == openai_snapshot([clean])
    assert captured["mask_at_rest"] is True
    encoded = json.dumps(captured["tool_definitions"], ensure_ascii=False)
    assert raw_email not in encoded
    assert raw_key not in encoded


@pytest.mark.asyncio
async def test_compare_context_finding_blocks_every_column_before_write(monkeypatch) -> None:
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    models = [_external_model("external/one"), _external_model("external/two")]
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=models,
        blocks=[_block("project_instructions", "owner person@example.com")],
    )
    db = _NoWriteDb()

    response = await sessions_router.compare_models(
        session.id,
        CompareRequest(content="clean request", models=[model["id"] for model in models]),
        _request(),
        user,
        db,
    )

    assert isinstance(response, JSONResponse)
    contract = json.loads(response.body)
    assert contract["findings"] == [
        {"category": "email", "source": "project_instructions", "count": 1}
    ]
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_compare_attachment_only_finding_blocks_before_write_or_fanout(
    monkeypatch,
) -> None:
    user = User(email="person@example.test", password_hash="hash", name="Person")
    session = ChatSession(user_id=user.id)
    models = [_external_model("external/one"), _external_model("external/two")]
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=models,
        blocks=[],
    )
    stored = StoredFile(
        id="attachment-1",
        user_id=user.id,
        name="notes.txt",
        text="owner person@example.com",
    )
    seen_ids: list[str] | None = None

    async def owned_attachments(_db, _user, attachment_ids):
        return [stored], [{"id": stored.id, "name": stored.name, "size": 0, "type": "text/plain"}]

    async def exact_context(_db, _user, _session, *, attachment_ids=None, **_kwargs):
        nonlocal seen_ids
        seen_ids = attachment_ids
        return _workspace([_block("attachments", stored.text)])

    monkeypatch.setattr(sessions_router, "_owned_attachments", owned_attachments)
    monkeypatch.setattr(sessions_router, "assemble", exact_context)
    db = _NoWriteDb()

    response = await sessions_router.compare_models(
        session.id,
        CompareRequest(
            content="clean request",
            models=[model["id"] for model in models],
            attachments=[stored.id],
        ),
        _request(),
        user,
        db,
    )

    assert isinstance(response, JSONResponse)
    assert response.status_code == 409
    assert json.loads(response.body)["findings"] == [
        {"category": "email", "source": "attachments", "count": 1}
    ]
    assert seen_ids == [stored.id]
    assert stored.session_id is None
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_send_rejects_explicit_model_outside_user_allowlist(monkeypatch) -> None:
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        allowed_models=["external/allowed"],
    )
    session = ChatSession(user_id=user.id)
    models = [_external_model("external/allowed"), _external_model("external/blocked")]
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=models,
        blocks=[],
    )

    with pytest.raises(Exception) as caught:
        await sessions_router.send_message(
            session.id,
            SendMessage(content="clean", model="external/blocked"),
            _request(),
            user,
            _NoWriteDb(),
        )

    assert getattr(caught.value, "status_code", None) == 403
    assert getattr(caught.value, "detail", None) == "model_not_allowed"


@pytest.mark.asyncio
async def test_send_stale_model_fallback_stays_inside_allowlist(monkeypatch) -> None:
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        allowed_models=["external/allowed"],
    )
    session = ChatSession(user_id=user.id, model="external/now-blocked")
    allowed = _external_model("external/allowed")
    blocked = _external_model("external/now-blocked")
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[blocked, allowed],
        blocks=[],
    )

    def headroom(_user, model):
        assert model["id"] == allowed["id"]
        return False

    monkeypatch.setattr(sessions_router, "has_headroom", headroom)
    with pytest.raises(Exception) as caught:
        await sessions_router.send_message(
            session.id,
            SendMessage(content="clean"),
            _request(),
            user,
            _NoWriteDb(),
        )

    assert getattr(caught.value, "detail", None) == "insufficient_credits"


@pytest.mark.asyncio
async def test_a_revoked_model_says_so_and_does_not_become_the_session(monkeypatch) -> None:
    """A fallback the person can see, and that ends when the revocation does.

    The turn still runs — refusing it would be worse — but on another model at
    another price, so the routing metadata reports the model that was asked for
    rather than the one that answered, which is what the transcript's badge
    compares. And the substitute is not written back: a session silently moved
    to the cheapest row would stay there long after the allowlist was restored.
    """
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        allowed_models=["external/allowed"],
    )
    revoked = _external_model("external/now-blocked")
    allowed = _external_model("external/allowed")
    session = ChatSession(user_id=user.id, model=revoked["id"])
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[revoked, allowed],
        blocks=[],
    )

    async def ensure_key(*_args, **_kwargs):
        return None

    async def credentials(*_args, **_kwargs):
        return "http://litellm.test", "key"

    async def build_tools(*_args, **_kwargs):
        return []

    class AcceptedDb(_NoWriteDb):
        def is_modified(self, _value):
            return False

    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions_router, "build_tools", build_tools)
    db = AcceptedDb()

    response = await sessions_router.send_message(
        session.id,
        SendMessage(content="clean"),
        _request(),
        user,
        db,
    )

    assert response.status_code == 200
    assert session.model == revoked["id"]
    user_message = next(
        row
        for row in db.added
        if isinstance(row, sessions_router.Message) and row.role == sessions_router.Role.user
    )
    assert user_message.routing["requestedModels"] == [revoked["id"]]
    assert user_message.routing["routedModels"] == [allowed["id"]]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "runner_name"),
    [
        (sessions_router.SessionKind.report, "_run_report"),
        (sessions_router.SessionKind.slides, "_run_deck"),
    ],
)
async def test_a_revoked_model_says_so_on_report_and_slides(
    monkeypatch, kind, runner_name: str
) -> None:
    """A substitution is news on every surface, not only the one that resolves privacy.

    Report and slide turns build no privacy resolution, so there was nothing on
    the turn to carry the swap: the document came out written by a model nobody
    chose and the transcript agreed with itself. The note the runner is handed
    is what the reader's badge compares.
    """
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        allowed_models=["external/allowed"],
    )
    revoked = {**_external_model("external/now-blocked"), "kinds": [kind.value]}
    allowed = {**_external_model("external/allowed"), "kinds": [kind.value]}
    session = ChatSession(user_id=user.id, kind=kind, model=revoked["id"])
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[revoked, allowed],
        blocks=[],
    )

    async def ensure_key(*_args, **_kwargs):
        return None

    async def credentials(*_args, **_kwargs):
        return "http://litellm.test", "key"

    captured: dict = {}

    async def run_document(**kwargs):
        captured.update(kwargs)
        yield 'data: {"type":"done"}\n\n'

    class AcceptedDb(_NoWriteDb):
        def is_modified(self, _value):
            return False

    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions_router, runner_name, run_document)
    db = AcceptedDb()

    response = await sessions_router.send_message(
        session.id,
        SendMessage(content="clean"),
        _request(),
        user,
        db,
    )
    async for _chunk in response.body_iterator:
        pass

    routing = captured["routing"]
    assert routing["requestedModels"] == [revoked["id"]]
    assert routing["routedModels"] == [allowed["id"]]
    # What `actualModelChanged` reads in the transcript.
    assert routing["actualModel"] == allowed["id"]
    assert routing["action"] == "none"
    user_message = next(
        row
        for row in db.added
        if isinstance(row, sessions_router.Message) and row.role == sessions_router.Role.user
    )
    assert user_message.routing["requestedModels"] == [revoked["id"]]
    # The revocation owns this turn only, exactly as it does on chat.
    assert session.model == revoked["id"]


@pytest.mark.asyncio
async def test_a_document_turn_on_the_model_it_asked_for_carries_no_route_note(
    monkeypatch,
) -> None:
    """Nothing happened, so the turn says nothing — an empty badge row is noise."""
    user = User(email="person@example.test", password_hash="hash", name="Person")
    allowed = {**_external_model("external/allowed"), "kinds": ["report"]}
    session = ChatSession(
        user_id=user.id,
        kind=sessions_router.SessionKind.report,
        model=allowed["id"],
    )
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[allowed],
        blocks=[],
    )

    async def ensure_key(*_args, **_kwargs):
        return None

    async def credentials(*_args, **_kwargs):
        return "http://litellm.test", "key"

    captured: dict = {}

    async def run_document(**kwargs):
        captured.update(kwargs)
        yield 'data: {"type":"done"}\n\n'

    class AcceptedDb(_NoWriteDb):
        def is_modified(self, _value):
            return False

    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions_router, "_run_report", run_document)
    db = AcceptedDb()

    response = await sessions_router.send_message(
        session.id,
        SendMessage(content="clean"),
        _request(),
        user,
        db,
    )
    async for _chunk in response.body_iterator:
        pass

    assert captured["routing"] is None


@pytest.mark.asyncio
async def test_strict_privacy_route_does_not_persist_over_requested_session_model(
    monkeypatch,
) -> None:
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        preferences={"privacyDefaultAction": "route_strict_local"},
    )
    external = _external_model("external/requested")
    external["supportsTools"] = True
    strict = {
        **_external_model("strict-local/safe"),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "supportsTools": True,
    }
    session = ChatSession(user_id=user.id, model=external["id"])
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[external, strict],
        blocks=[],
    )

    async def current(*_args, **_kwargs):
        return Governance(
            external_data_guard=True,
            privacy_safe_model_ids=[strict["id"]],
        )

    async def ensure_key(*_args, **_kwargs):
        return None

    async def credentials(*_args, **_kwargs):
        return "http://litellm.test", "key"

    tool_build: dict = {}

    async def build_tools(*_args, **kwargs):
        tool_build.update(kwargs)
        return []

    class AcceptedDb(_NoWriteDb):
        def is_modified(self, _value):
            return False

    monkeypatch.setattr(sessions_router.governance, "current_for_egress", current)
    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(
        sessions_router.litellm_service,
        "credentials_for",
        credentials,
    )
    monkeypatch.setattr(sessions_router, "build_tools", build_tools)
    db = AcceptedDb()

    response = await sessions_router.send_message(
        session.id,
        SendMessage(content="send person@example.com"),
        _request(),
        user,
        db,
    )

    assert response.status_code == 200
    assert session.model == external["id"]
    user_message = next(
        row
        for row in db.added
        if isinstance(row, sessions_router.Message) and row.role == sessions_router.Role.user
    )
    assert user_message.routing["routedModels"] == [strict["id"]]
    assert tool_build["web_search"] is False
    assert tool_build["knowledge_collection"] == ""


@pytest.mark.asyncio
async def test_strict_local_turn_admits_it_could_not_search(monkeypatch) -> None:
    """A privacy route removes the search tool; the answer has to say so.

    Dropping the toggle quietly produced a turn indistinguishable from a
    searched one, which is how a remembered fact ends up read as a checked one.
    """
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        preferences={"privacyDefaultAction": "route_strict_local"},
    )
    external = _external_model("external/requested")
    external["supportsTools"] = True
    strict = {
        **_external_model("strict-local/safe"),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "supportsTools": True,
    }
    session = ChatSession(user_id=user.id, model=external["id"])
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[external, strict],
        blocks=[],
    )

    async def current(*_args, **_kwargs):
        return Governance(
            external_data_guard=True,
            privacy_safe_model_ids=[strict["id"]],
        )

    async def ensure_key(*_args, **_kwargs):
        return None

    async def credentials(*_args, **_kwargs):
        return "http://litellm.test", "key"

    async def build_tools(*_args, **_kwargs):
        return []

    class AcceptedDb(_NoWriteDb):
        def is_modified(self, _value):
            return False

    captured: dict = {}

    async def no_events():
        return
        yield b""  # pragma: no cover - shape only

    def run_turn(**kwargs):
        captured.update(kwargs)
        return no_events()

    monkeypatch.setattr(sessions_router.governance, "current_for_egress", current)
    monkeypatch.setattr(sessions_router, "has_headroom", lambda *_args: True)
    monkeypatch.setattr(sessions_router.litellm_service, "ensure_key", ensure_key)
    monkeypatch.setattr(sessions_router.litellm_service, "credentials_for", credentials)
    monkeypatch.setattr(sessions_router, "build_tools", build_tools)
    monkeypatch.setattr(sessions_router, "_run_turn", run_turn)

    response = await sessions_router.send_message(
        session.id,
        SendMessage(content="send person@example.com", web_search=True),
        _request(),
        user,
        AcceptedDb(),
    )

    assert response.status_code == 200
    prompt = captured["messages"][0]["content"]
    assert "검색 도구가 없습니다" in prompt
    assert "web_search 를 최소 한 번" not in prompt


@pytest.mark.asyncio
async def test_strict_local_never_exposes_network_backed_code_or_vector_tools(
    monkeypatch,
) -> None:
    called = False

    async def runner(_arguments):
        nonlocal called
        called = True
        return ToolResult(content="should not run")

    def tool(name: str, *, source: str = "builtin") -> Tool:
        return Tool(
            name=name,
            description="test",
            parameters={"type": "object"},
            run=runner,
            label=name,
            source=source,
        )

    filtered = sessions_router._strict_local_tools(
        [
            tool("execute_code"),
            tool("search_knowledge"),
            tool("create_artifact"),
            tool("create_chart"),
            tool("fetch_url"),
            tool("connector_lookup", source="remote-connector"),
        ]
    )
    assert [item.name for item in filtered] == [
        "search_knowledge",
        "create_artifact",
        "create_chart",
    ]

    calls = 0

    async def fake_stream_once(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        acc = agent._Accumulator()
        if calls == 1:
            # A model can still hallucinate a function it was not offered.
            # The agent must answer "unknown tool" rather than dispatching the
            # configured HTTP code interpreter.
            acc.calls[0] = {
                "id": "call_0",
                "name": "execute_code",
                "arguments": '{"code":"print(\\"person@example.com\\")"}',
            }
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", fake_stream_once)
    events = [
        event
        async for event in agent.run_turn(
            "strict-local/model",
            [{"role": "user", "content": "person@example.com"}],
            filtered,
            ToolContext(user_id="user", session_id="session", api_key="key"),
            strict_local=True,
        )
    ]

    assert called is False
    assert calls == 2
    assert any(event.get("type") == "step" and event.get("status") == "error" for event in events)


@pytest.mark.asyncio
async def test_strict_route_revalidates_selected_skill_after_tools_are_removed(
    monkeypatch,
) -> None:
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        preferences={"privacyDefaultAction": "route_strict_local"},
    )
    session = ChatSession(user_id=user.id)
    external = {
        **_external_model("external/tools"),
        "supportsTools": True,
    }
    strict = {
        **_external_model("strict-local/safe"),
        "dataBoundary": "self_hosted",
        "strictLocal": True,
        "supportsTools": True,
    }
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=[external, strict],
        blocks=[],
    )

    async def current(*_args, **_kwargs):
        return Governance(
            external_data_guard=True,
            privacy_safe_model_ids=[strict["id"]],
        )

    async def settings(*_args, **_kwargs):
        return None, None, None

    async def runner(_arguments):
        return ToolResult(content="ok")

    def tool(name: str) -> Tool:
        return Tool(
            name=name,
            description=name,
            parameters={"type": "object"},
            run=runner,
            label=name,
            source="builtin",
        )

    async def build(*_args, strict_local=False, **_kwargs):
        return [tool("create_artifact")] if strict_local else [tool("execute_code")]

    validation: list[set[str]] = []

    async def context(*_args, available_tool_names, **_kwargs):
        validation.append(set(available_tool_names))
        if "execute_code" not in available_tool_names:
            raise WorkspaceContextError("skill_tools_unavailable:execute_code")
        return _workspace([_block("skill:selected", "검산 절차")])

    monkeypatch.setattr(sessions_router.governance, "current_for_egress", current)
    monkeypatch.setattr(sessions_router, "agent_settings", settings)
    monkeypatch.setattr(sessions_router, "build_tools", build)
    monkeypatch.setattr(sessions_router, "assemble", context)
    db = _NoWriteDb()

    with pytest.raises(Exception) as caught:
        await sessions_router.send_message(
            session.id,
            SendMessage(
                content="send person@example.com",
                activated_skill_ids=["selected"],
            ),
            _request(),
            user,
            db,
        )

    assert getattr(caught.value, "detail", None) == (
        "skill_tools_unavailable:execute_code"
    )
    assert validation == [{"execute_code"}, {"create_artifact"}]
    assert db.added == []
    assert db.commits == 0


@pytest.mark.asyncio
async def test_strict_registry_does_not_resolve_remote_tools_or_backends(
    monkeypatch,
) -> None:
    async def forbidden(*_args, **_kwargs):
        pytest.fail("strict tool build touched a remote registry/backend")

    monkeypatch.setattr(tool_registry, "available_builtins", forbidden)
    monkeypatch.setattr(tool_registry, "connector_tools", forbidden)
    user = User(email="person@example.test", password_hash="hash", name="Person")
    tools = await tool_registry.build_tools(
        object(),
        user,
        web_search=True,
        knowledge=[("notes.txt", "# Local notes\ncontent", None)],
        knowledge_collection="remote-vector-index",
        strict_local=True,
    )

    assert [tool.name for tool in tools] == [
        "create_artifact",
        "create_chart",
        "search_knowledge",
    ]


@pytest.mark.asyncio
async def test_compare_rejects_any_missing_or_disallowed_model(monkeypatch) -> None:
    user = User(
        email="person@example.test",
        password_hash="hash",
        name="Person",
        allowed_models=["external/one", "external/two"],
    )
    session = ChatSession(user_id=user.id)
    models = [
        _external_model("external/one"),
        _external_model("external/two"),
        _external_model("external/blocked"),
    ]
    await _patch_guard_dependencies(
        monkeypatch,
        session=session,
        models=models,
        blocks=[],
    )

    for requested, expected in (
        (["external/one", "external/two", "missing/model"], "models_unavailable"),
        (["external/one", "external/blocked"], "model_not_allowed"),
    ):
        with pytest.raises(Exception) as caught:
            await sessions_router.compare_models(
                session.id,
                CompareRequest(content="clean", models=requested),
                _request(),
                user,
                _NoWriteDb(),
            )
        assert getattr(caught.value, "detail", None) == expected


def test_migration_backfills_only_an_existing_install_without_policy(monkeypatch) -> None:
    migration_path = Path(__file__).parents[1] / "alembic" / "versions" / "0019_privacy_guard.py"
    spec = importlib.util.spec_from_file_location("privacy_migration_0019", migration_path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    assert migration._needs_existing_install_policy(True, False) is True
    assert migration._needs_existing_install_policy(False, False) is False
    assert migration._needs_existing_install_policy(True, True) is False

    class ScalarResult:
        def __init__(self, value: bool | None):
            self.value = value

        def scalar(self):
            return self.value

    class MigrationBind:
        def __init__(self, *, has_users: bool, has_policy: bool):
            self.answers = iter((has_users, has_policy))
            self.statements: list[str] = []

        def execute(self, statement):
            rendered = str(statement)
            self.statements.append(rendered)
            if rendered.lstrip().upper().startswith("SELECT EXISTS"):
                return ScalarResult(next(self.answers))
            return ScalarResult(None)

    monkeypatch.setattr(migration.op, "add_column", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(migration.op, "alter_column", lambda *_args, **_kwargs: None)

    existing = MigrationBind(has_users=True, has_policy=False)
    monkeypatch.setattr(migration.op, "get_bind", lambda: existing)
    migration.upgrade()
    assert any("INSERT INTO governance" in statement for statement in existing.statements)

    fresh = MigrationBind(has_users=False, has_policy=False)
    monkeypatch.setattr(migration.op, "get_bind", lambda: fresh)
    migration.upgrade()
    assert not any("INSERT INTO governance" in statement for statement in fresh.statements)


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _AttachmentDb:
    def __init__(self, rows):
        self.rows = rows

    async def exec(self, _query):
        return _Rows(self.rows)


@pytest.mark.asyncio
async def test_attachment_resolution_requires_every_id_to_be_owned() -> None:
    user = User(email="person@example.test", password_hash="hash", name="Person")
    owned = StoredFile(id="owned", user_id=user.id, name="owned.txt")

    rows, metadata = await sessions_router._owned_attachments(
        _AttachmentDb([owned]), user, [owned.id]
    )
    assert rows == [owned]
    assert metadata and metadata[0]["id"] == owned.id

    with pytest.raises(Exception) as caught:
        await sessions_router._owned_attachments(
            _AttachmentDb([owned]), user, [owned.id, "foreign-or-missing"]
        )
    assert getattr(caught.value, "status_code", None) == 404
    assert getattr(caught.value, "detail", None) == "attachment_not_found"


class _MemoryDb:
    def __init__(self, rows):
        self.rows = rows
        self.added: list[object] = []

    async def exec(self, _query):
        return _Rows(self.rows)

    def add(self, value):
        self.added.append(value)


class _JsonResponse:
    def __init__(self, content: str = "[]"):
        self.content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self.content}}]}


@pytest.mark.asyncio
async def test_title_and_auto_memory_mask_every_prompt_and_harden_strict_calls(
    monkeypatch,
) -> None:
    calls: list[dict] = []

    class CaptureClient:
        def __init__(self, **kwargs):
            self.headers = kwargs.get("headers") or {}

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _path, *, json):
            calls.append({"headers": self.headers, "json": json})
            return _JsonResponse()

    async def config():
        return "http://litellm.test", "master"

    monkeypatch.setattr(chat.httpx, "AsyncClient", CaptureClient)
    monkeypatch.setattr(auto_memory.httpx, "AsyncClient", CaptureClient)
    monkeypatch.setattr(chat.settings_store, "litellm_config", config)
    monkeypatch.setattr(auto_memory.settings_store, "litellm_config", config)

    title, _ = await chat.generate_title(
        "strict-local/model",
        "current person@example.com",
        "reply +1 (415) 555-2671",
        "key",
        masker=governance.mask,
        strict_local=True,
    )
    assert title == "[]"

    user = User(email="person@example.test", password_hash="hash", name="Person")
    known = Memory(
        user_id=user.id,
        name="Known",
        body="known owner@example.com",
        type=MemoryType.user,
    )
    written, _ = await auto_memory.extract(
        _MemoryDb([known]),
        user,
        user_message="current person@example.com",
        assistant_message="reply +44 20 7183 8750",
        api_key="key",
        model="strict-local/model",
        masker=governance.mask,
        strict_local=True,
    )

    assert written == 0
    assert len(calls) == 2
    for call in calls:
        encoded = json.dumps(call["json"], ensure_ascii=False)
        assert "person@example.com" not in encoded
        assert "owner@example.com" not in encoded
        assert call["headers"]["x-litellm-enable-message-redaction"] == "true"
        assert call["json"]["disable_fallbacks"] is True


@pytest.mark.asyncio
async def test_litellm_redaction_header_is_request_scoped() -> None:
    client = await agent._client("secret", redact_logging=True)
    try:
        assert client.headers["x-litellm-enable-message-redaction"] == "true"
    finally:
        await client.aclose()


@pytest.mark.asyncio
async def test_agent_reuses_preflighted_tool_definitions_on_every_hop(monkeypatch) -> None:
    async def runner(_arguments):
        return ToolResult(content="ok")

    tool = Tool(
        name="lookup",
        description="Original inspected description",
        parameters={"type": "object", "properties": {}},
        run=runner,
        label="lookup",
    )
    definitions = openai_snapshot([tool])
    # A detached snapshot cannot be changed by later registry mutation.
    tool.description = "mutated after preflight owner@example.com"
    seen: list[list[dict] | None] = []

    async def fake_stream_once(*_args, tool_definitions=None, **_kwargs):
        seen.append(tool_definitions)
        acc = agent._Accumulator()
        if len(seen) == 1:
            acc.calls[0] = {"id": "call_0", "name": "lookup", "arguments": "{}"}
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", fake_stream_once)
    _ = [
        event
        async for event in agent.run_turn(
            "external/model",
            [{"role": "user", "content": "clean"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
            tool_definitions=definitions,
        )
    ]

    assert seen == [definitions, definitions]
    assert "owner@example.com" not in json.dumps(seen, ensure_ascii=False)


@pytest.mark.asyncio
async def test_sensitive_tool_result_is_masked_before_followup_and_enables_redaction(
    monkeypatch,
) -> None:
    calls: list[tuple[bool, list[dict]]] = []

    async def fake_stream_once(
        model,
        messages,
        tools,
        user_id,
        api_key,
        *,
        strict_local=False,
        redact_logging=False,
    ):
        calls.append((redact_logging, [dict(message) for message in messages]))
        acc = agent._Accumulator()
        if len(calls) == 1:
            acc.calls[0] = {
                "id": "call_0",
                "name": "lookup",
                "arguments": "{}",
            }
        yield "done", acc

    async def run_tool(_arguments):
        return ToolResult(
            content="result person@example.com",
            detail="owner +14155552671",
        )

    monkeypatch.setattr(agent, "_stream_once", fake_stream_once)
    tool = Tool(
        name="lookup",
        description="test",
        parameters={"type": "object"},
        run=run_tool,
        label="lookup",
    )

    events = [
        event
        async for event in agent.run_turn(
            "external/model",
            [{"role": "user", "content": "clean"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
            sanitize_tool_output=governance.mask,
        )
    ]

    assert [redact for redact, _ in calls] == [False, True]
    followup = json.dumps(calls[1][1], ensure_ascii=False)
    assert "person@example.com" not in followup
    assert "+14155552671" not in json.dumps(events, ensure_ascii=False)
    assert any(
        event.get("type") == "privacy_route"
        and event.get("source") == "tool_output"
        and event.get("count") == 2
        for event in events
    )


@pytest.mark.asyncio
async def test_strict_tool_detail_is_masked_for_storage_but_raw_result_stays_local(
    monkeypatch,
) -> None:
    calls: list[tuple[bool, list[dict]]] = []

    async def fake_stream_once(
        model,
        messages,
        tools,
        user_id,
        api_key,
        *,
        strict_local=False,
        redact_logging=False,
    ):
        calls.append((redact_logging, [dict(message) for message in messages]))
        acc = agent._Accumulator()
        acc.actual_model = "provider/strict-model"
        if len(calls) == 1:
            acc.calls[0] = {"id": "call_0", "name": "lookup", "arguments": "{}"}
        yield "done", acc

    async def run_tool(_arguments):
        return ToolResult(
            content="local result person@example.com",
            detail="timeline owner +1 (415) 555-2671",
        )

    monkeypatch.setattr(agent, "_stream_once", fake_stream_once)
    tool = Tool(
        name="lookup",
        description="test",
        parameters={"type": "object"},
        run=run_tool,
        label="lookup label-owner@example.com",
    )

    events = [
        event
        async for event in agent.run_turn(
            "strict-local/model",
            [{"role": "user", "content": "clean"}],
            [tool],
            ToolContext(user_id="user", session_id="session", api_key="key"),
            sanitize_step_detail=governance.mask,
            classify_tool_output=lambda value: [
                row.wire() for row in governance.findings({"tool_output": value})
            ],
            strict_local=True,
        )
    ]

    # The local model may use the raw result; its next proxy spend-log request
    # is nevertheless redacted, while the persisted/SSE step never has it.
    assert "person@example.com" in json.dumps(calls[1][1])
    assert [redact for redact, _ in calls] == [False, True]
    step_events = [event for event in events if event["type"] == "step"]
    assert "label-owner@example.com" not in json.dumps(step_events)
    assert "+1 (415) 555-2671" not in json.dumps(step_events)
    assert any("[전화번호]" in event.get("detail", "") for event in step_events)
    route = next(
        event
        for event in events
        if event.get("type") == "privacy_route" and event.get("source") == "tool_output"
    )
    assert route["action"] == "strict_local"
    assert route["findings"] == [
        {"category": "email", "source": "tool_output", "count": 1},
        {"category": "phone", "source": "tool_output", "count": 1},
    ]


@pytest.mark.asyncio
async def test_protected_strict_create_artifact_is_deep_masked_without_mutation(
    monkeypatch,
) -> None:
    sensitive = "artifact-owner@example.com"
    ctx = ToolContext(user_id="user", session_id="session", api_key="key")
    result = await builtin_tools.create_artifact(
        {
            "kind": "html",
            "title": f"Dashboard for {sensitive}",
            "content": f"<main data-owner='{sensitive}'>{sensitive}</main>",
        },
        ctx,
    )
    assert result.failed is False
    # Exercise future nested metadata as well as create_artifact's current
    # title and content. Persistence must not depend on a fixed payload shape.
    ctx.pending_artifacts[0]["data"]["metadata"] = {
        "owner": sensitive,
        "labels": [sensitive, {"note": sensitive}],
    }
    raw_request = json.dumps(ctx.pending_artifacts, ensure_ascii=False)

    user = User(
        id="user",
        email="person@example.test",
        password_hash="hash",
        name="Person",
    )
    session = ChatSession(id="session", user_id=user.id)
    added: list[object] = []

    class ArtifactDb:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, _id):
            if model is ChatSession:
                return session
            if model is User:
                return user
            return None

        async def exec(self, _query):
            return _Rows([])

        def add(self, value):
            added.append(value)

        async def commit(self):
            return None

    monkeypatch.setattr(sessions_router, "SessionLocal", ArtifactDb)
    artifact_id, _memory_step = await sessions_router._enrich(
        user_id=user.id,
        session_id=session.id,
        content="safe assistant reply",
        first_user_message="safe user request",
        api_key="key",
        model={"id": "strict-local/model"},
        auto_memory=False,
        requested_artifacts=ctx.pending_artifacts,
        protect_privacy=True,
        strict_local=True,
        redact_logging=True,
    )

    artifacts = [row for row in added if isinstance(row, Artifact)]
    assert len(artifacts) == 1
    stored = artifacts[0]
    encoded = json.dumps({"title": stored.title, "data": stored.data}, ensure_ascii=False)
    assert artifact_id == stored.id
    assert session.artifact_id == stored.id
    assert sensitive not in encoded
    assert encoded.count("[이메일]") >= 5
    # A detached tree was persisted; the strict-local tool context was not
    # mutated after the agent loop finished using the raw local request.
    assert json.dumps(ctx.pending_artifacts, ensure_ascii=False) == raw_request
    assert sensitive in raw_request


@pytest.mark.asyncio
async def test_agent_reports_provider_actual_model_separately(monkeypatch) -> None:
    async def fake_stream_once(*_args, **_kwargs):
        acc = agent._Accumulator()
        acc.actual_model = "provider/fallback-model"
        yield "done", acc

    monkeypatch.setattr(agent, "_stream_once", fake_stream_once)
    events = [
        event
        async for event in agent.run_turn(
            "hybrid/alias",
            [{"role": "user", "content": "clean"}],
            [],
            ToolContext(user_id="user", session_id="session", api_key="key"),
        )
    ]

    assert events[0] == {
        "type": "model_route",
        "routedModel": "hybrid/alias",
        "actualModel": "provider/fallback-model",
    }
    routing = sessions_router._with_actual_model(
        {
            "requestedModels": ["hybrid/alias"],
            "routedModels": ["hybrid/alias"],
            "effectiveModels": ["hybrid/alias"],
            "actualModels": [],
            "dataBoundary": "hybrid",
            "modelRoutes": [
                {
                    "routedModel": "hybrid/alias",
                    "actualModel": None,
                    "dataBoundary": "hybrid",
                }
            ],
        },
        "hybrid/alias",
        "provider/fallback-model",
    )
    assert routing["dataBoundary"] == "hybrid"
    assert routing["actualModels"] == ["provider/fallback-model"]
    assert routing["modelRoutes"][0]["actualModel"] == "provider/fallback-model"


@pytest.mark.asyncio
async def test_guard_masks_clean_turn_assistant_steps_and_routing_at_rest(
    monkeypatch,
) -> None:
    raw_email = "reply-owner@example.com"
    raw_key = "sk-abcdefghijklmnopqrstuvwxyz123456"
    raw_actual = "provider/model-owner@example.com"
    user = User(
        id="user",
        email="person@example.test",
        password_hash="hash",
        name="Person",
        monthly_credits=10_000,
    )
    session = ChatSession(id="session", user_id=user.id)
    added: list[object] = []
    run_kwargs: dict = {}

    async def run_turn(*_args, **kwargs):
        run_kwargs.update(kwargs)
        yield {
            "type": "delta",
            "text": f"Contact {raw_email}; credential {raw_key}",
        }
        yield {
            "type": "step",
            "id": "h1_0",
            "label": f"Connector {raw_email}",
            "status": "done",
            "detail": f"Used {raw_key}",
        }
        yield {
            "type": "model_route",
            "routedModel": "external/model",
            "actualModel": raw_actual,
        }
        yield {"type": "usage", "inputTokens": 1, "outputTokens": 1}

    class TurnDb:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, _id):
            if model is ChatSession:
                return session
            if model is User:
                return user
            return None

        def add(self, value):
            added.append(value)

        async def commit(self):
            return None

    async def enrich(**_kwargs):
        return None, None

    monkeypatch.setattr(sessions_router.agent_service, "run_turn", run_turn)
    monkeypatch.setattr(sessions_router, "SessionLocal", TurnDb)
    monkeypatch.setattr(sessions_router, "_enrich", enrich)
    routing = {
        "requestedModels": ["external/model"],
        "routedModels": ["external/model"],
        "effectiveModels": ["external/model"],
        "actualModels": [],
        "action": "none",
        "dataBoundary": "external",
        "modelRoutes": [
            {
                "routedModel": "external/model",
                "actualModel": None,
                "dataBoundary": "external",
            }
        ],
    }
    _ = [
        event
        async for event in sessions_router._run_turn(
            user_id=user.id,
            api_key="key",
            auto_memory=False,
            session_id=session.id,
            model=_external_model("external/model"),
            messages=[{"role": "user", "content": "clean request"}],
            tools=[],
            tool_definitions=[],
            first_user_message="clean request",
            is_first_turn=False,
            routing=routing,
            mask_at_rest=True,
            sanitize_tool_output=True,
            protect_enrichment=True,
        )
    ]

    message = next(
        row
        for row in added
        if isinstance(row, sessions_router.Message) and row.role == sessions_router.Role.assistant
    )
    persisted = json.dumps(
        {
            "content": message.content,
            "steps": message.steps,
            "model": message.model,
            "routing": message.routing,
        },
        ensure_ascii=False,
    )
    assert raw_email not in persisted
    assert raw_key not in persisted
    assert raw_actual not in persisted
    assert "[이메일]" in persisted
    assert "[API키]" in persisted
    assert run_kwargs["redact_logging"] is True


@pytest.mark.asyncio
async def test_guard_says_on_the_wire_what_it_took_out_of_the_answer(
    monkeypatch,
) -> None:
    """The stored answer is masked; the streamed one is not. Say so."""
    raw_email = "answer-owner@example.com"
    user = User(
        id="user",
        email="person@example.test",
        password_hash="hash",
        name="Person",
        monthly_credits=10_000,
    )
    session = ChatSession(id="session", user_id=user.id)
    added: list[object] = []

    async def run_turn(*_args, **_kwargs):
        yield {"type": "delta", "text": f"Write to {raw_email} and to {raw_email}"}
        yield {"type": "usage", "inputTokens": 1, "outputTokens": 1}

    class TurnDb:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, _id):
            if model is ChatSession:
                return session
            if model is User:
                return user
            return None

        def add(self, value):
            added.append(value)

        async def commit(self):
            return None

    async def enrich(**_kwargs):
        # `(artifact_id, memory_step)`; neither matters here.
        return None, None

    monkeypatch.setattr(sessions_router.agent_service, "run_turn", run_turn)
    monkeypatch.setattr(sessions_router, "SessionLocal", TurnDb)
    monkeypatch.setattr(sessions_router, "_enrich", enrich)
    routing = {
        "requestedModels": ["external/model"],
        "routedModels": ["external/model"],
        "effectiveModels": ["external/model"],
        "actualModels": [],
        "action": "mask_external",
        "dataBoundary": "external",
        "findingCounts": [{"category": "email", "source": "current_input", "count": 1}],
    }
    events = [
        event
        async for event in sessions_router._run_turn(
            user_id=user.id,
            api_key="key",
            auto_memory=False,
            session_id=session.id,
            model=_external_model("external/model"),
            messages=[{"role": "user", "content": "clean request"}],
            tools=[],
            tool_definitions=[],
            first_user_message="clean request",
            is_first_turn=False,
            routing=routing,
            mask_at_rest=True,
        )
    ]

    announced = [
        json.loads(line.removeprefix("data: ").strip())
        for line in "".join(events).splitlines()
        if line.startswith("data: ")
    ]
    answer = [
        finding
        for event in announced
        if event["type"] == "privacy_route"
        for finding in event.get("findingCounts", [])
        if finding["source"] == "assistant_output"
    ]
    assert answer == [{"category": "email", "source": "assistant_output", "count": 2}]

    message = next(
        row
        for row in added
        if isinstance(row, sessions_router.Message) and row.role == sessions_router.Role.assistant
    )
    # The record is still masked — this test is about the silence, not the mask.
    assert raw_email not in message.content
    assert {"category": "email", "source": "assistant_output", "count": 2} in (
        message.routing["findingCounts"]
    )
    # The prompt's own finding is not overwritten by the answer's.
    assert {"category": "email", "source": "current_input", "count": 1} in (
        message.routing["findingCounts"]
    )


@pytest.mark.asyncio
async def test_clean_answer_under_the_guard_announces_no_answer_findings(
    monkeypatch,
) -> None:
    """No finding, no claim: an untouched answer must not grow a warning."""
    user = User(
        id="user",
        email="person@example.test",
        password_hash="hash",
        name="Person",
        monthly_credits=10_000,
    )
    session = ChatSession(id="session", user_id=user.id)
    added: list[object] = []

    async def run_turn(*_args, **_kwargs):
        yield {"type": "delta", "text": "아무것도 가릴 것이 없는 답."}
        yield {"type": "usage", "inputTokens": 1, "outputTokens": 1}

    class TurnDb:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, model, _id):
            if model is ChatSession:
                return session
            if model is User:
                return user
            return None

        def add(self, value):
            added.append(value)

        async def commit(self):
            return None

    async def enrich(**_kwargs):
        # `(artifact_id, memory_step)`; neither matters here.
        return None, None

    monkeypatch.setattr(sessions_router.agent_service, "run_turn", run_turn)
    monkeypatch.setattr(sessions_router, "SessionLocal", TurnDb)
    monkeypatch.setattr(sessions_router, "_enrich", enrich)
    _ = [
        event
        async for event in sessions_router._run_turn(
            user_id=user.id,
            api_key="key",
            auto_memory=False,
            session_id=session.id,
            model=_external_model("external/model"),
            messages=[{"role": "user", "content": "clean request"}],
            tools=[],
            tool_definitions=[],
            first_user_message="clean request",
            is_first_turn=False,
            routing=None,
            mask_at_rest=True,
        )
    ]

    message = next(
        row
        for row in added
        if isinstance(row, sessions_router.Message) and row.role == sessions_router.Role.assistant
    )
    assert message.routing is None


@pytest.mark.asyncio
async def test_comparison_masks_variants_and_persists_provider_actual_model(
    monkeypatch,
) -> None:
    routed = _external_model("hybrid/alias")
    routed["dataBoundary"] = "hybrid"
    user = User(
        id="user",
        email="person@example.test",
        password_hash="hash",
        name="Person",
        monthly_credits=10_000,
    )
    added: list[object] = []
    raw_key = "sk-abcdefghijklmnopqrstuvwxyz123456"
    raw_actual = "provider/model-owner@example.com"

    async def stream_completion(*_args, **_kwargs):
        yield {
            "type": "model_route",
            "routedModel": routed["id"],
            "actualModel": raw_actual,
        }
        yield {
            "type": "delta",
            "text": f"reply person@example.com credential {raw_key}",
        }
        yield {"type": "usage", "inputTokens": 1, "outputTokens": 1}

    class ComparisonDb:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        def add(self, value):
            added.append(value)

        async def get(self, model, _id):
            return user if model is User else None

        async def commit(self):
            return None

    monkeypatch.setattr(
        sessions_router.chat_service,
        "stream_completion",
        stream_completion,
    )
    monkeypatch.setattr(sessions_router, "SessionLocal", ComparisonDb)
    routing = {
        "requestedModels": [routed["id"]],
        "routedModels": [routed["id"]],
        "effectiveModels": [routed["id"]],
        "actualModels": [],
        "action": "mask_external",
        "dataBoundary": "hybrid",
        "modelRoutes": [
            {
                "routedModel": routed["id"],
                "actualModel": None,
                "dataBoundary": "hybrid",
            }
        ],
    }

    events = "".join(
        [
            event
            async for event in sessions_router._run_comparison(
                user_id=user.id,
                api_key="key",
                session_id="session",
                models=[routed],
                messages=[{"role": "user", "content": "clean request"}],
                skills_event={
                    "type": "skills_applied",
                    "skills": [
                        {
                            "id": "skill",
                            "name": "review owner@example.com",
                            "catalogKey": "review",
                            "estimatedTokens": 12,
                        }
                    ],
                    "estimatedTokens": 12,
                },
                routing=routing,
                mask_at_rest=True,
            )
        ]
    )

    message = next(
        row
        for row in added
        if isinstance(row, sessions_router.Message) and row.role == sessions_router.Role.assistant
    )
    assert "person@example.com" not in message.content
    assert "person@example.com" not in json.dumps(message.variants)
    assert raw_key not in message.content
    assert raw_actual not in json.dumps(message.variants)
    assert raw_actual not in json.dumps(message.routing)
    assert "owner@example.com" not in json.dumps(message.steps)
    assert "review [이메일]" in json.dumps(message.steps, ensure_ascii=False)
    assert message.model == "provider/[이메일]"
    assert message.variants[0]["routedModel"] == routed["id"]
    assert message.variants[0]["actualModel"] == "provider/[이메일]"
    assert message.routing["dataBoundary"] == "hybrid"
    assert message.routing["actualModels"] == ["provider/[이메일]"]
    assert raw_actual in events
