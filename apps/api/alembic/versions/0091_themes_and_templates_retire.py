"""Templates and themes retire into 디자인 지침 and the agent's own fields (2026-11-15).

Data only; nothing is deleted, and the template and theme columns stay (old conversations and
artifacts still read their template).

- `projects.design_brief`, `agents.design_brief`: a project or agent with a theme
  (`design_system_id`) and no 지침 gets the theme written as one (`design_brief.render`, frozen
  here): its colours, face, look, voice rules, craft rules and picture style.
- `agents.doc_format`: an agent bound to a shipped document template and with no format of its
  own takes the template's format (the pattern now brings its questions and checks).
- An agent bound to a person's template (`ut-…`) takes that template's questions, process
  settings and check skills where it has none of its own.

Downgrade empties the 지침 this upgrade wrote (marked 「(테마에서 옮김)」); the formats and fields
it gave agents stay, as they are what those agents already did.

Revision ID: 0091
Revises: 0090
Create Date: 2026-11-15
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0091"
down_revision: str | None = "0090"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: shipped document template → its format, as the templates said on 2026-11-15
TEMPLATE_FORMATS = {
    "doc-agenda": "agenda", "doc-career-history": "career_history",
    "doc-client-proposal": "client_proposal", "doc-comparison": "comparison",
    "doc-event-plan": "event_plan", "doc-gaejo-report": "gaejo",
    "doc-internal-draft": "internal_draft", "doc-minutes": "minutes",
    "doc-result-report": "result_report",
}
LOOK_WORDS = {
    "poster": "포스터처럼 강렬하게", "minimal": "미니멀하게", "dark": "다크 배경으로",
    "split": "색면 분할로", "warm": "따뜻한 색감으로", "mono": "흑백으로",
    "pastel": "파스텔 톤으로", "forest": "숲 느낌으로", "slate": "슬레이트 톤으로",
    "paper": "종이 서식처럼",
}
CRAFT = {
    "restraint": (
        "- 이모지를 쓰지 않는다.\n"
        "- '혁신적', '차별화된', '최적의' 같은 채움말 대신 확인할 수 있는 사실을 쓴다.\n"
        "- 쓸 내용이 없으면 분량을 채우지 말고 그 항목을 줄인다."
    ),
    "typography": (
        "- 강조는 한 가지 방법으로만 한다. 굵게·따옴표·밑줄을 겹쳐 쓰지 않는다.\n"
        "- 제목 단계는 두 단계까지만 쓴다.\n"
        "- 한 절은 문단이거나 목록이다. 둘을 번갈아 쓰지 않는다."
    ),
}
#: what marks a 지침 this migration wrote, for the downgrade
MARK = "\n\n(테마에서 옮김)"


def _json(value):
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return None
    return value


def render(tokens, body, image_style, craft) -> str:
    raw = _json(tokens) or {}
    lines: list[str] = []
    if raw.get("accent"):
        lines.append(f"강조색 {str(raw['accent']).upper()}.")
    if raw.get("ink"):
        lines.append(f"글자색 {str(raw['ink']).upper()}.")
    if raw.get("font") in ("serif", "gothic"):
        lines.append("본문은 명조." if raw["font"] == "serif" else "본문은 고딕.")
    if (look := LOOK_WORDS.get(str(raw.get("visualStyle") or ""))):
        lines.append(f"모양은 {look}.")
    if (body or "").strip():
        lines.append(body.strip())
    for key in _json(craft) or []:
        if key in CRAFT:
            lines.append(CRAFT[key])
    if (image_style or "").strip():
        lines.append(f"그림 스타일: {image_style.strip()}")
    return "\n".join(lines)[:1900] + MARK if lines else ""


def upgrade() -> None:
    conn = op.get_bind()
    themes = {
        row[0]: render(row[1], row[2], row[3], row[4])
        for row in conn.execute(sa.text(
            "SELECT id, tokens, body, image_style, craft FROM design_systems")).all()
    }
    for table in ("projects", "agents"):
        rows = conn.execute(sa.text(
            f"SELECT id, design_system_id FROM {table} "
            "WHERE design_system_id IS NOT NULL AND (design_brief IS NULL OR design_brief = '')"
        )).all()
        for row_id, theme_id in rows:
            if themes.get(theme_id):
                conn.execute(sa.text(f"UPDATE {table} SET design_brief = :brief WHERE id = :id"),
                             {"brief": themes[theme_id], "id": row_id})
    agents = conn.execute(sa.text(
        "SELECT id, render_template_id, doc_format, gap_questions, process_settings, "
        "check_skills FROM agents WHERE render_template_id IS NOT NULL")).all()
    for agent_id, template_id, doc_format, questions, process, checks in agents:
        if template_id in TEMPLATE_FORMATS and not doc_format:
            conn.execute(sa.text("UPDATE agents SET doc_format = :f WHERE id = :id"),
                         {"f": TEMPLATE_FORMATS[template_id], "id": agent_id})
            continue
        own = str(template_id).removeprefix("ut-")
        template = conn.execute(sa.text(
            "SELECT questions, process, check_skills FROM user_templates WHERE id = :id"),
            {"id": own}).first()
        if template is None:
            continue
        changes: dict[str, str] = {}
        if _json(questions) is None and _json(template[0]):
            changes["gap_questions"] = json.dumps(_json(template[0]), ensure_ascii=False)
        if _json(process) is None and _json(template[1]):
            changes["process_settings"] = json.dumps(_json(template[1]), ensure_ascii=False)
        if _json(checks) is None and _json(template[2]):
            changes["check_skills"] = json.dumps(_json(template[2]), ensure_ascii=False)
        if changes:
            sets = ", ".join(f"{key} = CAST(:{key} AS {_json_type(conn)})" for key in changes)
            conn.execute(sa.text(f"UPDATE agents SET {sets} WHERE id = :id"),
                         {**changes, "id": agent_id})


def _json_type(conn) -> str:
    return "JSONB" if conn.dialect.name == "postgresql" else "TEXT"


def downgrade() -> None:
    conn = op.get_bind()
    for table in ("projects", "agents"):
        conn.execute(sa.text(
            f"UPDATE {table} SET design_brief = '' WHERE design_brief LIKE :mark"),
            {"mark": f"%{MARK.strip()}"})
