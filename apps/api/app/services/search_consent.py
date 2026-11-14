"""Asking before a strict-local turn searches the web.

A strict-local turn (internal material present, or a strict-local model chosen) sends nothing
outside the data boundary. Some questions still need the outside world (other papers, recent
facts). Instead of dropping the search silently, the turn shows what would leave — short search
queries only, never the material — and asks:

- chat: the model calls `request_web_search(queries)`, a local tool. In 「매번 묻기」 the turn stops
  there and the answer row carries a pending card (`routing.searchConsent`); the person's answer
  reruns the question (`retry_of`) with the decision, and that turn runs the approved queries
  through the ordinary web search, read by the same local model. In 「항상 허용(검색어만)」, or
  once the person allowed it for the conversation, the tool runs the queries at once.
- report/slides: research runs before the outline, so the queries are planned up front by the
  local planner and asked about before the pipeline starts (`plan_document_queries`).

Every query is vetted before it is shown or sent (`vet`): personal identifiers are masked,
secrets and passages copied from the material are withheld with the reason. What was sent, by
whose decision and when, is kept on the answer's routing and in the audit log.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from app.models.user import utcnow
from app.models.workspace import Project
from app.services import governance, query_terms, server_notes, settings_store
from app.services.tools.base import SearchEvidence, Tool, ToolContext, ToolResult

#: Admin and project settings, least to most strict.
MODES = ("allow", "ask", "off")
Mode = Literal["allow", "ask", "off"]
_RANK = {mode: rank for rank, mode in enumerate(MODES)}
DEFAULT_MODE: Mode = "ask"

#: Queries one card may carry, and how long each may be.
MAX_QUERIES = 5
MAX_CHARS = 100
#: Characters a query may share verbatim with the material before it reads as an excerpt.
_EXCERPT_CHARS = 40

TOOL_NAME = "request_web_search"

#: What the paused answer says; the card under it lists the queries.
PAUSE_TEXT = (
    "이 질문에 답하려면 외부 검색이 필요합니다. 아래 검색어만 밖으로 나갑니다. "
    "프로젝트 자료 내용은 보내지 않습니다."
)
#: Said under an answer written without the outside search it would have used (taken back
#: off before the answer is sent as history, like the server's other notes).
NO_SEARCH_NOTE_KO = server_notes.NO_SEARCH_KO
NO_SEARCH_NOTE_EN = server_notes.NO_SEARCH_EN

#: Withholding reasons, as the card says them.
REASONS = {
    "secret": "비밀 값(주민번호·카드번호·API 키 등)이 들어 있어 보내지 않습니다.",
    "material": "자료의 문장을 그대로 담고 있어 보내지 않습니다.",
    "category": "관리자가 막은 주제가 들어 있어 보내지 않습니다.",
}


def strictest(*modes: str | None) -> Mode:
    """The strictest of the given settings; unknown or unset ones don't count."""
    known = [mode for mode in modes if mode in _RANK]
    return max(known, key=_RANK.__getitem__) if known else DEFAULT_MODE  # type: ignore[return-value]


def policy_mode(admin: str | None, project: str | None = None) -> Mode:
    """The admin's setting, made stricter (never looser) by the project's."""
    admin_mode = admin if admin in _RANK else DEFAULT_MODE
    return strictest(admin_mode, project)


def project_choices(admin: str | None) -> list[str]:
    """Project settings that are as strict as the admin's or stricter."""
    floor = _RANK[admin if admin in _RANK else DEFAULT_MODE]
    return [mode for mode in MODES if _RANK[mode] >= floor]


def record_of(row: Any) -> dict[str, Any] | None:
    """A message's search-consent record, or None."""
    routing = getattr(row, "routing", None)
    found = routing.get("searchConsent") if isinstance(routing, dict) else None
    return found if isinstance(found, dict) else None


def paused(row: Any) -> bool:
    """Whether this answer row is a card still waiting for the person."""
    found = record_of(row)
    return bool(found and found.get("status") == "pending")


def remembered(history: list[Any]) -> bool:
    """Whether the person allowed searching for the rest of this conversation."""
    return any(
        (found := record_of(row)) is not None
        and found.get("remember") is True
        and found.get("status") in ("allowed", "auto")
        for row in history
    )


# ── vetting ──────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Vetted:
    """One proposed query: what would be sent (masked), and why it is withheld, if it is."""

    text: str
    masked: int = 0
    blocked: str = ""

    def wire(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "masked": self.masked,
            **({"blocked": self.blocked, "reason": REASONS.get(self.blocked.split(":")[0], "")}
               if self.blocked else {}),
        }


_SPACE = re.compile(r"\s+")


def _flat(text: str) -> str:
    return _SPACE.sub(" ", text or "").strip().lower()


def _copies_material(query: str, material: str) -> bool:
    """Whether the query carries a long verbatim span of the material."""
    flat = _flat(query)
    if len(flat) < _EXCERPT_CHARS or not material:
        return False
    return any(
        flat[at : at + _EXCERPT_CHARS] in material
        for at in range(0, len(flat) - _EXCERPT_CHARS + 1)
    )


def clean(queries: Any) -> list[str]:
    """Distinct non-empty queries, each cut to `MAX_CHARS`, at most `MAX_QUERIES`."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in query_terms.query_list(queries):
        text = _SPACE.sub(" ", str(raw)).strip()[:MAX_CHARS].strip()
        key = text.lower()
        if text and key not in seen:
            seen.add(key)
            out.append(text)
        if len(out) >= MAX_QUERIES:
            break
    return out


def vet(
    queries: Any,
    *,
    material: list[str] | None = None,
    legacy: bool = False,
    blocked_categories: list[str] | None = None,
) -> list[Vetted]:
    """Each query masked as the egress detectors would; withheld when it carries a secret, a
    passage of the material or a blocked category."""
    corpus = _flat("\n".join(material or []))
    masker = governance.mask_legacy if legacy else governance.mask
    out: list[Vetted] = []
    for text in clean(queries):
        found = governance.findings({"search_query": text}, legacy=legacy)
        if any(row.category in governance.SECRETS for row in found):
            # the raw value is never shown back, even masked
            out.append(Vetted(masker(text)[0], sum(r.count for r in found), "secret"))
            continue
        masked, count = masker(text)
        if _copies_material(text, corpus):
            out.append(Vetted(masked, count, "material"))
            continue
        if blocked_categories and (hit := governance.blocked_by(text, blocked_categories)):
            out.append(Vetted(masked, count, f"category:{hit}"))
            continue
        out.append(Vetted(masked, count))
    return out


def sendable(vetted: list[Vetted]) -> list[str]:
    return [item.text for item in vetted if not item.blocked]


# ── one turn ─────────────────────────────────────────────────────────────


@dataclass(slots=True)
class Decision:
    """The person's answer to a card, as the server settled it."""

    status: Literal["allowed", "declined", "blocked", "unavailable"]
    queries: list[str]
    withheld: list[Vetted]
    by: str
    at: datetime
    remember: bool = False
    #: the paused answer this decides
    message_id: str = ""


@dataclass(slots=True)
class TurnConsent:
    """What this strict-local turn may do about the web, and what it did."""

    mode: Mode
    #: why `mode` is `allow`: `policy` (admin or project) or `conversation` (remembered)
    source: str = "policy"
    #: chat: material whose passages no query may carry
    material: list[str] = field(default_factory=list)
    legacy: bool = False
    blocked_categories: list[str] = field(default_factory=list)
    #: the question obviously needs outside information (freshness, literature)
    needs_outside: bool = False
    #: a search backend is configured
    backend: bool = True
    #: the person's answer this turn carries out
    decision: Decision | None = None
    #: filled while the turn runs
    card: dict[str, Any] | None = None
    sent: list[str] = field(default_factory=list)
    withheld: list[Vetted] = field(default_factory=list)
    searches: int = 0
    offered: bool = False

    @property
    def usable(self) -> bool:
        return self.mode != "off" and self.backend

    @property
    def offers(self) -> bool:
        """Whether `request_web_search` joins this turn's tools."""
        if self.decision is not None:
            return self.decision.status == "allowed" and bool(self.decision.queries)
        return self.usable

    def tool(self) -> Tool:
        """`request_web_search` for this turn (local: it calls nothing until allowed)."""
        self.offered = True
        return Tool(
            name=TOOL_NAME,
            description=TOOL_DESCRIPTION,
            parameters=_PARAMETERS,
            run=self._run,
            label="외부 검색 확인 중",
            title="외부 검색 요청",
            read_only=True,
            source="builtin",
            wants_context=True,
        )

    async def _run(self, args: dict[str, Any], ctx: ToolContext) -> ToolResult:
        if self.decision is not None:
            if self.searches or self.decision.status != "allowed":
                return ToolResult(content=(
                    "외부 검색은 이번 턴에 이미 정해졌습니다. 받은 결과와 자료로 답하세요."
                    if self.searches else
                    "이번에는 외부 검색을 하지 않기로 했습니다. 자료만으로 답하세요."))
            return await self._search(self.decision.queries, ctx)
        vetted = vet(args.get("queries") or args.get("query"), material=self.material,
                     legacy=self.legacy, blocked_categories=self.blocked_categories)
        queries = sendable(vetted)
        self.withheld = [item for item in vetted if item.blocked]
        if not queries:
            return ToolResult(
                content=(
                    "보낼 수 있는 검색어가 없어 외부 검색을 하지 않았습니다"
                    + (f" ({self.withheld[0].wire()['reason']})" if self.withheld else "")
                    + ". 자료만으로 답하고, 외부 검색 없이 답했다고 밝히세요."
                ),
                empty=True,
            )
        if self.mode == "allow":
            if self.searches >= MAX_QUERIES:
                return ToolResult(content=(
                    "이번 턴의 외부 검색은 이미 충분히 했습니다. 받은 결과와 자료로 답하세요."))
            return await self._search(queries[: MAX_QUERIES - self.searches], ctx)
        if self.card is not None:
            # a second call in the same hop: one card, its queries joined
            known = [q["text"] for q in self.card["queries"]]
            self.card["queries"] += [
                item.wire() for item in vetted
                if item.text not in known and len(known) < MAX_QUERIES
            ][: MAX_QUERIES - len(known)]
        else:
            self.card = {
                "status": "pending",
                "queries": [item.wire() for item in vetted],
                "askedAt": utcnow().isoformat(),
            }
        return ToolResult(content=PAUSE_TEXT, final_text=PAUSE_TEXT)

    async def _search(self, queries: list[str], ctx: ToolContext) -> ToolResult:
        """The approved queries through the ordinary web search; results stay with the local
        model."""
        from app.services.tools.builtin import web_search

        self.sent += queries
        results = await asyncio.gather(*(web_search({"query": q}) for q in queries))
        self.searches += len(queries)
        ctx.tool_calls["web_search"] = ctx.tool_calls.get("web_search", 0) + len(queries)
        usable = [r for r in results if not r.failed]
        urls = tuple(
            url for r in usable if isinstance(r.search_evidence, SearchEvidence)
            for url in r.search_evidence.source_urls
        )
        for result in usable:
            if not result.empty:
                ctx.searched.append(result.content)
        content = "\n\n".join(r.content for r in results)
        return ToolResult(
            content=content,
            detail="검색어만 전송 · " + " · ".join(queries),
            failed=not usable,
            empty=not urls,
            search_evidence=SearchEvidence(urls) if urls else None,
        )

    # ── what is stored ───────────────────────────────────────────────────

    def record(self, *, by: str) -> dict[str, Any] | None:
        """`routing.searchConsent` for this turn's answer; None when the turn had no part in it."""
        if self.card is not None:
            return dict(self.card)
        withheld = [item.wire() for item in self.withheld]
        if self.decision is not None:
            return {
                "status": self.decision.status,
                "source": "card",
                "queries": self.sent if self.searches else [],
                "searches": self.searches,
                "withheld": [item.wire() for item in self.decision.withheld] or withheld,
                "decidedBy": self.decision.by,
                "decidedAt": self.decision.at.isoformat(),
                "remember": self.decision.remember,
            }
        if self.searches:
            return {
                "status": "auto",
                "source": self.source,
                "queries": self.sent,
                "searches": self.searches,
                "withheld": withheld,
                "decidedBy": by,
                "decidedAt": utcnow().isoformat(),
                # a remembered conversation stays remembered
                "remember": self.source == "conversation",
            }
        if withheld:
            return {"status": "blocked", "queries": [], "searches": 0, "withheld": withheld}
        if not self.usable and self.needs_outside:
            return {"status": "unavailable", "queries": [], "searches": 0,
                    "reason": "policy_off" if self.mode == "off" else "no_backend"}
        if self.offered:
            return {"status": "none", "queries": [], "searches": 0}
        return None

    def note(self, *, korean: bool = True) -> str:
        """The line under an answer written without the outside search it would have used."""
        if self.card is not None or self.searches:
            return ""
        declined = self.decision is not None
        if declined or self.withheld or (self.needs_outside and (not self.usable or self.offered)):
            return NO_SEARCH_NOTE_KO if korean else NO_SEARCH_NOTE_EN
        return ""

    def instruction(self) -> str:
        """System block for the writer while the tool is offered or a decision stands."""
        if self.decision is not None:
            if self.decision.status == "allowed" and self.decision.queries:
                return ALLOWED_RULE
            if self.decision.status == "declined":
                return DECLINED_RULE + DECLINED_WHY
            return DECLINED_RULE + BLOCKED_WHY
        return OFFER_RULE if self.offered else ""


def decide(
    answer: Any,
    *,
    card: dict[str, Any],
    mode: Mode,
    backend: bool,
    user_id: str,
    message_id: str,
    material: list[str] | None = None,
    legacy: bool = False,
    blocked_categories: list[str] | None = None,
) -> Decision:
    """Settles the person's answer to `card` against today's policy; edited queries are vetted
    again (the browser's copy is never trusted)."""
    now = utcnow()
    if answer.decision != "allow":
        return Decision("declined", [], [], user_id, now, message_id=message_id)
    if mode == "off" or not backend:
        return Decision("unavailable", [], [], user_id, now, message_id=message_id)
    proposed = (
        answer.queries if answer.queries is not None
        else [q.get("text") for q in card.get("queries") or [] if not q.get("blocked")]
    )
    vetted = vet(proposed, material=material, legacy=legacy,
                 blocked_categories=blocked_categories)
    queries = sendable(vetted)
    withheld = [item for item in vetted if item.blocked]
    return Decision(
        "allowed" if queries else "blocked", queries, withheld, user_id, now,
        remember=bool(answer.remember) and bool(queries), message_id=message_id,
    )


def audit_metadata(record: dict[str, Any], *, session_id: str, message_id: str | None,
                   paused_id: str | None = None) -> dict[str, Any]:
    """What the audit log keeps of a decision: the queries that left, who, when."""
    return {
        "sessionId": session_id,
        **({"messageId": message_id} if message_id else {}),
        **({"pausedMessageId": paused_id} if paused_id else {}),
        "status": record.get("status"),
        "source": record.get("source"),
        # a card's proposals are records; a decision's, the texts sent
        "queries": [q.get("text") if isinstance(q, dict) else q
                    for q in record.get("queries") or []],
        "withheld": [
            {"blocked": item.get("blocked"), "text": item.get("text")}
            for item in record.get("withheld") or []
        ],
        "decidedBy": record.get("decidedBy"),
        "decidedAt": record.get("decidedAt"),
        "remember": bool(record.get("remember")),
    }


AUDIT_ACTIONS = {
    "pending": "search.consent_asked",
    "allowed": "search.consent_allowed",
    "auto": "search.consent_auto",
    "declined": "search.consent_declined",
    "blocked": "search.consent_blocked",
    "unavailable": "search.consent_unavailable",
}


#: Signals that a question reaches outside the material: other or earlier work, the latest.
_OUTSIDE = re.compile(
    r"(?:기존|선행|관련|다른|타|최근|최신|해외|국내)\s{0,3}(?:연구|논문|문헌|사례|동향|기술|제품)"
    r"|(?:연구|논문|사례)\s{0,3}(?:동향|현황)|최신\s{0,3}(?:정보|소식|자료|버전)"
    r"|\b(?:prior|related|recent|existing)\s{1,3}(?:work|research|papers?|studies)\b",
    re.I,
)


def needs_outside(request: str) -> bool:
    """Whether the question plainly asks about the world outside the material."""
    from app.services.context import instruction_part, literature_request, needs_web_search

    asked = instruction_part(request or "")
    return bool(_OUTSIDE.search(asked) or literature_request(asked) or needs_web_search(asked))


async def mode_for(db: Any, *, policy: Any, session: Any, history: list[Any]) -> tuple[Mode, str]:
    """This conversation's setting and why: the admin's, tightened by the project's, loosened
    to `allow` only by the person's 「이 대화에서는 계속 허용」."""
    project_mode = None
    if getattr(session, "project_id", None):
        project = await db.get(Project, session.project_id)
        project_mode = getattr(project, "strict_local_web_search", None)
    mode = policy_mode(getattr(policy, "strict_local_web_search", None), project_mode)
    if mode == "ask" and remembered(history):
        return "allow", "conversation"
    return mode, "policy"


async def for_turn(
    db: Any,
    *,
    policy: Any,
    session: Any,
    history: list[Any],
    user_id: str,
    material: list[str],
    needs_outside: bool,
    tools: bool,
    paused: Any = None,
    answer: Any = None,
) -> TurnConsent:
    """The turn's consent state; `paused`/`answer`: the card this turn answers, and how."""
    mode, source = await mode_for(db, policy=policy, session=session, history=history)
    backend = tools and bool((await settings_store.tools_config()).search)
    consent = TurnConsent(
        mode=mode, source=source, material=list(material), legacy=bool(policy.pii_masking),
        blocked_categories=(list(policy.blocked_categories or [])
                            if getattr(policy, "intent_filter", False) else []),
        needs_outside=needs_outside, backend=backend,
    )
    if paused is not None and answer is not None:
        consent.decision = decide(
            answer, card=record_of(paused) or {}, mode=mode, backend=backend, user_id=user_id,
            message_id=paused.id, material=consent.material, legacy=consent.legacy,
            blocked_categories=consent.blocked_categories,
        )
    return consent


# ── model-facing words ───────────────────────────────────────────────────

TOOL_DESCRIPTION = (
    "Ask the person for permission to search the web. This conversation is strict-local: "
    "nothing leaves the server unless the person allows it, and only the search queries would "
    "leave — never the material. Call it when the answer needs information outside the "
    "provided material and conversation: other or earlier papers and studies (「기존 연구」, "
    "「다른 논문」), recent events, current figures, prices or rules. Do not call it when the "
    "material answers the question. After calling it, stop and write nothing more: the server "
    "shows the queries to the person and asks."
)

_PARAMETERS = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": MAX_QUERIES,
            "description": (
                f"1–{MAX_QUERIES} short keyword queries (under 80 characters each) naming only "
                "public topics. Never copy sentences, figures, names, codes or other details from "
                "the person's files or this conversation into a query. "
                "예: 「우리 병원 A 과제 예산 3억 연합학습」(X) → 「연합학습 의료영상 선행연구」(O)."
            ),
        },
    },
    "required": ["queries"],
}

OFFER_RULE = (
    "# 외부 검색 (strict-local)\n"
    "This conversation is strict-local, so there is no web search tool. When the answer needs "
    "information outside the material and this conversation (other papers or prior work, "
    "recent facts), do not answer from memory as if you had checked: call request_web_search "
    "with short public keyword queries — no sentences, figures or names from the material — "
    "and stop. The person decides whether those queries may leave. When the material answers "
    "the question, answer from it without calling the tool."
)

ALLOWED_RULE = (
    "# 외부 검색 (strict-local, 허용됨)\n"
    "The person allowed this turn's search queries; their results are in the request_web_search "
    "result. Answer from the material and those results, citing a result's number like [2] "
    "where you use it. Do not call request_web_search again."
)

DECLINED_RULE = (
    "# 외부 검색 (strict-local, 하지 않음)\n"
    "No outside search runs this turn. Answer from the material and this conversation only. Do "
    "not write that you searched, and say plainly which parts the material does not cover "
    "(예: 「자료에는 다른 연구와의 비교가 없습니다」) instead of filling them from memory."
)
#: why no search ran, said as it was: never as a tool failure
DECLINED_WHY = (
    "\nThe person chose 「이번엔 자료만으로」 on the search card: if you mention the missing "
    "search, say it was not run at their choice "
    "(예: 「이번에는 검색하지 않고 자료로만 답합니다」), never that a tool failed or errored."
)
BLOCKED_WHY = (
    "\nThe search was not allowed here (policy, or nothing safe to send): if you mention it, "
    "say it was not allowed, never that a tool failed or errored."
)


async def for_document(
    db: Any,
    *,
    policy: Any,
    session: Any,
    history: list[Any],
    user_id: str,
    request: str,
    material: list[str],
    needs_outside: bool,
    planner: str,
    api_key: str,
    scholarly: bool = False,
    paused: Any = None,
    answer: Any = None,
) -> tuple[TurnConsent, tuple[str, ...], dict[str, int]]:
    """A strict-local report's or deck's research, settled before the pipeline starts (it
    cannot pause mid-way): `(consent, queries research may send, planner spend)`. The local
    planner proposes the queries; `consent.card` set means ask before anything runs."""
    from app.services import research

    mode, source = await mode_for(db, policy=policy, session=session, history=history)
    backend = await research.available()
    consent = TurnConsent(
        mode=mode, source=source, material=list(material), legacy=bool(policy.pii_masking),
        blocked_categories=(list(policy.blocked_categories or [])
                            if getattr(policy, "intent_filter", False) else []),
        needs_outside=needs_outside, backend=backend,
    )
    spent = {"inputTokens": 0, "outputTokens": 0}
    if paused is not None and answer is not None:
        consent.decision = decide(
            answer, card=record_of(paused) or {}, mode=mode, backend=backend, user_id=user_id,
            message_id=paused.id, material=consent.material, legacy=consent.legacy,
            blocked_categories=consent.blocked_categories,
        )
        consent.sent = list(consent.decision.queries)
        consent.searches = len(consent.sent)
        return consent, tuple(consent.sent), spent
    if not consent.usable:
        return consent, (), spent
    consent.offered = True
    planned, spent = await research.plan(request, planner, api_key, scholarly=scholarly)
    vetted = vet(planned, material=consent.material, legacy=consent.legacy,
                 blocked_categories=consent.blocked_categories)
    queries = sendable(vetted)
    consent.withheld = [item for item in vetted if item.blocked]
    if not queries:
        return consent, (), spent
    if mode == "allow":
        consent.sent, consent.searches = list(queries), len(queries)
        return consent, tuple(queries), spent
    consent.card = {
        "status": "pending",
        "scope": "document",
        "queries": [item.wire() for item in vetted],
        "askedAt": utcnow().isoformat(),
    }
    return consent, (), spent
