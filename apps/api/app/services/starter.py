"""Shipped catalogue of agents, skills and designs (owned by the oldest admin, shared)."""

from __future__ import annotations

import re
import tomllib
from math import ceil
from pathlib import Path

from sqlalchemy import text
from sqlmodel import col, func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.models.user import User, UserRole
from app.models.workspace import (
    Agent,
    DesignSystem,
    Skill,
    SkillSource,
    Visibility,
)
from app.services import skill_packages

#: agents reference skills by key (seeder maps to ids); every shipped skill is a package
_SKILLS: list[dict] = list(skill_packages.catalog_specs())

#: earlier shipped bodies by (key, version); untouched ones upgraded in place
_LEGACY_CATALOG_BODIES = {
    # formerly calculation-unit-check
    ("calc-check", "1.0.0"): """수치를 설명하기 전에 `execute_code` 로 실제 계산을 재현한다.

1. 입력값마다 출처·단위·기준 시점을 적는다. 주어진 값과 가정한 값을 구분한다.
2. 분모와 기준선이 무엇인지 먼저 확인한다. 0 또는 결측이면 계산을 중단하고 알린다.
3. 변환식을 코드로 실행하고 중간값과 최종값을 출력한다.
4. 퍼센트와 퍼센트포인트, 평균과 중앙값, 명목값과 실질값을 섞지 않는다.
5. 답에는 입력값 → 식 → 결과 → 반올림 규칙 순서로 provenance 를 짧게 남긴다.

도구를 실행하지 못했으면 검산했다고 쓰지 말고, 필요한 계산과 미검증 상태를 밝힌다.""",
}

#: shipped agents (app/agents/<key>.toml); optional surface/template/doc_format/design
_AGENT_ROOT = Path(__file__).resolve().parent.parent / "agents"


def _compile_bundles(spec: dict) -> None:
    """Check skills, options and steps an agent's 묶음 compile to (as PUT /agents/{id}/process)."""
    from app.services import bundles

    surface = str(spec.get("surface") or (spec.get("kinds") or ["chat"])[0])
    made = bundles.compile(surface, list(spec.get("bundles") or []), bundles.shipped(),
                           doc_format=spec.get("doc_format"))
    spec["bundles"] = bundles.clean(spec.get("bundles"))
    spec["checks"] = list(made.check_skills)
    spec["check_options"] = dict(made.check_options)
    spec["steps"] = made.steps


def _agent_specs() -> list[dict]:
    specs = []
    for path in sorted(_AGENT_ROOT.glob("*.toml")):
        with path.open("rb") as fh:
            spec = tomllib.load(fh)
        for name in ("guide", "system_prompt"):
            spec[name] = str(spec.get(name) or "").strip()
        _compile_bundles(spec)
        if "questions" in spec:
            # 빠진 정보 질문, validated like the agent form
            from app.services import gap_questions

            spec["questions"] = gap_questions.clean(spec["questions"])
        if "process" in spec:
            # 진행 과정 설정, validated and stored like the agent form
            from app.services import process_settings

            spec["process"] = process_settings.stored_form(
                process_settings.clean(spec["process"])) or None
        specs.append(spec)
    return sorted(specs, key=lambda spec: (spec.get("order", 99), spec["key"]))


_AGENTS: list[dict] = _agent_specs()


def retired_agents() -> frozenset[str]:
    """Retired catalogue keys: rows kept, hidden and unstartable."""
    return frozenset(spec["key"] for spec in _AGENTS if spec.get("retired"))


def draft_agents() -> frozenset[str]:
    """Draft catalogue keys: admin-only until evaluated."""
    return frozenset(spec["key"] for spec in _AGENTS if spec.get("draft"))


def offered_agents(*, drafts: bool = False) -> list[dict]:
    """Pickable shipped agents in catalogue order; drafts on request."""
    return [spec for spec in _AGENTS
            if not spec.get("retired") and (drafts or not spec.get("draft"))]


def agent_spec(key: str | None) -> dict | None:
    return next((s for s in _AGENTS if s["key"] == key), None) if key else None


def _questions(spec: dict) -> list[dict] | None:
    """Shipped agent's own 빠진 정보 질문, or None (서식's apply)."""
    return [dict(q) for q in spec["questions"]] if spec.get("questions") else None


def _process(spec: dict) -> dict | None:
    """Shipped agent's own 진행 과정 설정, or None (서식's apply)."""
    return dict(spec["process"]) if spec.get("process") else None


def _hidden_design(name: str | None) -> bool:
    return any(d["name"] == name and d.get("listed") is False for d in _DESIGNS)


def design_listed(name: str | None, owner_id: str | None = None,
                  catalog_owner: str | None = None) -> bool:
    """Whether a theme shows in pickers; False only for the owner's unlisted shipped row."""
    return not (_hidden_design(name) and owner_id is not None and owner_id == catalog_owner)


def agent_checks(key: str | None) -> tuple[str, ...]:
    """Check skills a shipped agent attaches, by catalogue key."""
    return tuple(str(k) for k in (agent_spec(key) or {}).get("checks") or ())

_DESIGNS: list[dict] = [
    {
        "name": "기본",
        "description": "보라 강조색과 고딕 서체만 정하고, 문체 규칙은 두지 않는 수수한 테마입니다.",
        "tokens": {"accent": "#5b5bd6", "ink": "#1a1a1a", "muted": "#666666", "font": "gothic"},
        "body": "",
        "image_style": "clean uncluttered composition, generous whitespace",
        "craft": ["restraint"],
    },
    {
        "name": "문서용 명조",
        "description": "보고서와 공문에 맞춘 먹빛 명조. 인쇄해도 읽힙니다.",
        "tokens": {"accent": "#334155", "ink": "#111827", "muted": "#6b7280", "font": "serif"},
        "body": "제목은 명사구로 쓴다. 한 문장에 한 사실만 담고, 수식어를 덜어낸다.",
        "image_style": "muted documentary photography, low saturation, natural light",
        "craft": ["restraint", "typography"],
    },
    {
        "name": "발표용 청록",
        # off the pickers; existing users keep it
        "listed": False,
        "description": "슬라이드와 표지 이미지를 같은 청록으로 묶습니다.",
        "tokens": {"accent": "#0f766e", "ink": "#0f172a", "muted": "#64748b", "font": "gothic"},
        "body": "청중이 소리 내어 읽을 문장으로 쓴다. 한 장에 주장 하나.",
        "image_style": "bold high-contrast graphic, large negative space, teal accent",
        "craft": ["restraint"],
    },
]


def _server_fields(key: str) -> dict | None:
    """Shipped package server fields as stored, or None."""
    from app.services import skill_specs

    package = skill_packages.package_for(key)
    if package is None:
        return None
    fields = skill_specs.package_fields(package)
    return None if skill_specs.empty(fields) else fields


def skill_name(key: str) -> str:
    """Shipped name for a catalogue key, or ""."""
    return next((spec["name"] for spec in _SKILLS if spec["key"] == key), "")


def _slug(name: str) -> str:
    base = re.sub(r"[^\w가-힣]+", "-", name.strip().lower()).strip("-")
    return base[:60] or "item"


def _estimated_tokens(spec: dict) -> int:
    """Cheap stable token estimate for the selector."""
    text = f"{spec.get('when_to_use', '')}\n{spec.get('body', '')}".strip()
    return max(1, ceil(len(text) / 3.5)) if text else 0


def estimate_tokens(when_to_use: str, body: str, description: str = "") -> int:
    return _estimated_tokens({"when_to_use": when_to_use, "body": body or description})


#: slug → key, to adopt rows seeded without catalog_key
_LEGACY_CATALOG_KEYS = {_slug(spec["name"]): spec["key"] for spec in _SKILLS}


async def seed_designs(db: AsyncSession, user_id: str) -> int:
    """Seed starting designs into an account with none; caller commits."""
    existing = (
        await db.exec(
            select(func.count()).select_from(DesignSystem).where(DesignSystem.owner_id == user_id)
        )
    ).one()
    if existing:
        return 0
    # unlisted themes only to the catalogue owner (official agents still use them)
    owner = await catalog_owner_id(db)
    specs = [spec for spec in _DESIGNS
             if spec.get("listed") is not False or owner is None or owner == user_id]
    for spec in specs:
        db.add(DesignSystem(owner_id=user_id, **{k: v for k, v in spec.items() if k != "listed"}))
    return len(specs)


async def catalog_owner_id(db: AsyncSession) -> str | None:
    """Catalogue owner: the oldest administrator."""
    return (
        await db.exec(
            select(User.id)
            .where(User.role == UserRole.admin)
            .order_by(col(User.created_at), col(User.id))
            .limit(1)
        )
    ).first()


async def seed_catalog(db: AsyncSession, owner_id: str) -> int:
    """Sync shipped agents/skills into the owner account; idempotent, caller commits."""
    # row lock: concurrent logins race on the unique key index
    await db.exec(select(User.id).where(User.id == owner_id).with_for_update())
    existing_agents = list(
        (await db.exec(select(Agent).where(col(Agent.owner_id) == owner_id))).all()
    )
    existing_skills = list(
        (await db.exec(select(Skill).where(col(Skill.owner_id) == owner_id))).all()
    )
    # first run ⇔ nothing shared or taken yet (keys alone can't tell: old per-account seeds)
    shipped_skills = {spec["key"] for spec in _SKILLS}
    established = any(agent.catalog_key for agent in existing_agents) or any(
        skill.catalog_key in shipped_skills
        and (skill.visibility == Visibility.org or skill.installs > 0)
        for skill in existing_skills
    )
    by_catalog = {s.catalog_key: s for s in existing_skills if s.catalog_key}
    for old_key, new_key in skill_packages.RENAMED.items():
        if old_key in by_catalog and new_key not in by_catalog:
            renamed = by_catalog.pop(old_key)
            renamed.catalog_key = new_key
            by_catalog[new_key] = renamed
    legacy_builtins = {
        _LEGACY_CATALOG_KEYS[s.slug]: s
        for s in existing_skills
        if s.source == SkillSource.built_in and s.slug in _LEGACY_CATALOG_KEYS
    }

    ids: dict[str, str] = {}
    made = 0
    for spec in _SKILLS:
        key = spec["key"]
        skill = by_catalog.get(key)
        if skill is None and (adopted := legacy_builtins.get(key)) is not None:
            skill = adopted
            skill.catalog_key = key
        if skill is not None:
            # drafts stay admin-private (never shared)
            if spec.get("draft"):
                skill.visibility = Visibility.private
            elif not established:
                skill.visibility = Visibility.org
            # only untouched earlier bodies upgraded
            previous = _LEGACY_CATALOG_BODIES.get((key, skill.version))
            upgraded = bool(previous and skill.body.strip() == previous.strip())
            if skill.required_tools is None:
                skill.required_tools = list(spec.get("required_tools", []))
            if upgraded:
                skill.body = spec["body"]
                skill.version = spec.get("version", "1.0.0")
                skill.description = spec["description"]
                skill.when_to_use = spec["when_to_use"]
                skill.required_tools = list(spec.get("required_tools", []))
            if upgraded or not skill.estimated_tokens:
                skill.estimated_tokens = estimate_tokens(
                    skill.when_to_use, skill.body, skill.description
                )
            # server fields follow the package
            skill.server_fields = _server_fields(key)
            db.add(skill)
            ids[key] = skill.id
            continue
        skill = Skill(
            owner_id=owner_id,
            name=spec["name"],
            slug=_slug(spec["name"]),
            description=spec["description"],
            when_to_use=spec["when_to_use"],
            body=spec["body"],
            catalog_key=key,
            kinds=spec["kinds"],
            required_tools=spec.get("required_tools", []),
            estimated_tokens=_estimated_tokens(spec),
            version=spec.get("version", "1.0.0"),
            source=SkillSource.built_in,
            visibility=Visibility.private if spec.get("draft") else Visibility.org,
            server_fields=_server_fields(key),
        )
        db.add(skill)
        ids[key] = skill.id
        made += 1

    agents_by_catalog = {a.catalog_key: a for a in existing_agents if a.catalog_key}
    designs: dict[str, str] = {}
    if any(spec.get("design") for spec in _AGENTS):
        rows = await db.exec(select(DesignSystem).where(col(DesignSystem.owner_id) == owner_id))
        designs = {row.name: row.id for row in rows.all()}
    slugs = {a.slug for a in existing_agents}
    for spec in _AGENTS:
        key = spec["key"]
        agent = agents_by_catalog.get(key)
        if spec.get("retired"):
            # retired: rows and copies kept (conversations open) but hidden; never reseeded
            if agent is not None:
                agent.retired = True
                agent.visibility = Visibility.private
                db.add(agent)
                copies = await db.exec(select(Agent).where(
                    (col(Agent.origin_id) == agent.id) | (col(Agent.catalog_key) == key),
                    col(Agent.retired).is_(False)))
                for copy in copies.all():
                    copy.retired = True
                    db.add(copy)
            continue
        if agent is not None:
            # renamed agent: copies with an old shipped name take the new one
            if former := [str(n) for n in spec.get("former_names") or ()]:
                renamed = await db.exec(select(Agent).where(
                    col(Agent.origin_id) == agent.id, col(Agent.name).in_(former)))
                for copy in renamed.all():
                    copy.name = spec["name"]
                    db.add(copy)
            # prompt follows shipped; copies with the original prompt refreshed, edited ones kept
            if agent.system_prompt != spec["system_prompt"]:
                untouched = await db.exec(
                    select(Agent).where(
                        col(Agent.origin_id) == agent.id,
                        col(Agent.system_prompt) == agent.system_prompt,
                    )
                )
                for copy in untouched.all():
                    copy.system_prompt = spec["system_prompt"]
                    copy.guide = spec.get("guide", "")
                    copy.starters = list(spec.get("starters", []))
                    db.add(copy)
            # same for 진행 과정 설정
            if agent.process_settings != _process(spec):
                kept = await db.exec(select(Agent).where(col(Agent.origin_id) == agent.id))
                for copy in kept.all():
                    if copy.process_settings == agent.process_settings:
                        copy.process_settings = _process(spec)
                        db.add(copy)
                agent.process_settings = _process(spec)
            agent.name = spec["name"]
            agent.description = spec["description"]
            agent.system_prompt = spec["system_prompt"]
            agent.kinds = spec["kinds"]
            agent.surface = spec.get("surface")
            agent.render_template_id = spec.get("template")
            agent.doc_format = spec.get("doc_format")
            agent.guide = spec.get("guide", "")
            agent.starters = list(spec.get("starters", []))
            agent.skill_ids = [ids[k] for k in spec.get("skills", []) if k in ids]
            agent.check_skills = list(spec.get("checks") or [])
            agent.check_options = dict(spec.get("check_options") or {}) or None
            agent.bundles = list(spec.get("bundles") or [])
            agent.gap_questions = _questions(spec)
            agent.temperature = spec.get("temperature", 0.5)
            agent.color = spec.get("color", "#5b53e8")
            agent.retired = False
            # owner's later design pick kept
            if agent.design_system_id is None:
                agent.design_system_id = designs.get(spec.get("design", ""))
            db.add(agent)
            continue
        # same-name owner agent: shipped one steps aside
        slug = base = _slug(spec["name"])
        n = 2
        while slug in slugs:
            slug, n = f"{base}-{n}", n + 1
        slugs.add(slug)
        db.add(
            Agent(
                owner_id=owner_id,
                name=spec["name"],
                slug=slug,
                description=spec["description"],
                system_prompt=spec["system_prompt"],
                kinds=spec["kinds"],
                surface=spec.get("surface"),
                render_template_id=spec.get("template"),
                doc_format=spec.get("doc_format"),
                design_system_id=designs.get(spec.get("design", "")),
                guide=spec.get("guide", ""),
                starters=list(spec.get("starters", [])),
                skill_ids=[ids[k] for k in spec.get("skills", []) if k in ids],
                # checks and strictness on the row
                check_skills=list(spec.get("checks") or []),
                check_options=dict(spec.get("check_options") or {}) or None,
                bundles=list(spec.get("bundles") or []),
                gap_questions=_questions(spec),
                process_settings=_process(spec),
                # never inherit: missing/[] denies all tools; list = hard allowlist
                tools=list(spec.get("tools", [])),
                temperature=spec.get("temperature", 0.5),
                color=spec.get("color", "#5b53e8"),
                catalog_key=key,
                # drafts shared but admin-only (draft_agents); dropping the flag publishes
                visibility=Visibility.org,
            )
        )
        made += 1

    # retired keys removed from owner rows; copies carry none
    shipped_agents = {spec["key"] for spec in _AGENTS}
    for skill in existing_skills:
        if skill.catalog_key and skill.catalog_key not in shipped_skills:
            await db.delete(skill)
    for agent in existing_agents:
        if agent.catalog_key and agent.catalog_key not in shipped_agents:
            await db.delete(agent)
    return made


#: pg_advisory_xact_lock key ("kccat")
CATALOG_LOCK_KEY = 0x6B_6363_6174


async def _catalog_lock(db: AsyncSession) -> None:
    """Serialise catalogue syncs across replicas for the transaction; no-op on SQLite."""
    conn = await db.connection()
    if conn.dialect.name == "postgresql":
        await conn.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                           {"key": CATALOG_LOCK_KEY})


async def sync_catalog(db: AsyncSession, user: User) -> int:
    """Sync the catalogue on sign-in, for the owner only."""
    if user.role is not UserRole.admin:
        return 0
    if await catalog_owner_id(db) != user.id:
        return 0
    await _catalog_lock(db)
    return await seed_catalog(db, user.id)


async def sync_catalog_at_startup(session_factory) -> int:
    """Startup catalogue sync under the advisory lock (idempotent); no-op without an owner."""
    async with session_factory() as db:
        await _catalog_lock(db)
        owner = await catalog_owner_id(db)
        if owner is None:
            await db.rollback()
            return 0
        made = await seed_catalog(db, owner)
        await db.commit()
        return made


def runtime_metadata(skill: Skill) -> dict:
    """Catalogue key, required tools, token estimate for a skill row."""
    return {
        "catalog_key": skill.catalog_key,
        "required_tools": list(skill.required_tools or []),
        "estimated_tokens": skill.estimated_tokens
        or estimate_tokens(skill.when_to_use, skill.body, skill.description),
    }


__all__ = [
    "agent_spec",
    "catalog_owner_id",
    "draft_agents",
    "estimate_tokens",
    "offered_agents",
    "retired_agents",
    "runtime_metadata",
    "seed_catalog",
    "seed_designs",
    "skill_name",
    "sync_catalog",
    "sync_catalog_at_startup",
]
