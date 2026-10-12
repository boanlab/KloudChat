"""What a document does besides writing becomes settings; people make templates; the
connector catalogue moves into the database.

- `agents.process_settings`: an agent's own 진행 과정 설정 (`services/process_settings.py`):
  pre-pass calculations, 서식 보정 stages, checks, 검토 규칙, structure rules and options, over
  its 서식's. Null for every existing row: an agent follows its 서식's settings, which the
  shipped 서식 now declare in their `template.toml` exactly as their per-format pipeline files
  did.
- `user_templates`: a person's document templates (`services/user_templates.py`).
- `connector_catalog`: the installable MCP servers administrators keep in 관리 › 커넥터
  카탈로그, seeded with the three entries the code listed (time, youtube, deep-research) with
  the same commands, addresses, placeholders and gateway-key binding. Installed connectors
  keep matching their entry by slug.

Data change: the three catalogue rows only. Downgrade drops the tables and the column.

Revision ID: 0062
Revises: 0061
Create Date: 2026-10-10
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MCP_SDK = "'mcp<2'"
#: What `services/tools/catalog.py` listed, as of this revision.
SEED = [
    {
        "slug": "time",
        "name": "시간",
        "description": "현재 시각과 타임존 변환. '오늘'을 모델이 지어내지 않게 합니다.",
        "category": "기본",
        "transport": "stdio",
        "endpoint": f"uvx --with {_MCP_SDK} mcp-server-time --local-timezone=Asia/Seoul",
        "env": None,
        "tools_key_var": "",
        "tools_key_feature": "",
        "kinds": ["chat", "report", "slides"],
    },
    {
        "slug": "youtube",
        "name": "YouTube 전사",
        "description": "영상의 자막을 가져오고, 없으면 음성을 전사합니다.",
        "category": "미디어",
        "transport": "stdio",
        "endpoint": "uv run --script /srv/mcp/youtube.py",
        "env": {"WHISPER_URL": "${TOOLS_STT_URL}"},
        "tools_key_var": "WHISPER_URL",
        "tools_key_feature": "stt",
        "kinds": ["chat", "report"],
    },
    {
        "slug": "deep-research",
        "name": "심층 리서치",
        "description": "arXiv·웹을 반복 탐색하는 ReAct 에이전트. 수 분에서 수십 분 걸립니다.",
        "category": "연구",
        "transport": "http",
        "endpoint": "${TOOLS_RESEARCH_URL}/mcp",
        "env": None,
        "tools_key_var": "",
        "tools_key_feature": "",
        "kinds": ["chat", "report"],
    },
]


def upgrade() -> None:
    json = JSONB().with_variant(sa.JSON(), "sqlite")
    stamp = sa.DateTime(timezone=True)
    op.add_column("agents", sa.Column("process_settings", json, nullable=True))
    op.create_table(
        "user_templates",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("owner_id", sa.String(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=20), nullable=False, server_default=""),
        sa.Column("fills", json, nullable=True),
        sa.Column("example_prompt", sa.String(), nullable=False, server_default=""),
        sa.Column("instructions", sa.String(), nullable=False, server_default=""),
        sa.Column("checklist", sa.String(), nullable=False, server_default=""),
        sa.Column("genre", sa.String(), nullable=False, server_default=""),
        sa.Column("doc_format", json, nullable=True),
        sa.Column("cover_page", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("body_face", sa.String(length=10), nullable=False, server_default=""),
        sa.Column("check_skills", json, nullable=True),
        sa.Column("check_options", json, nullable=True),
        sa.Column("questions", json, nullable=True),
        sa.Column("process", json, nullable=True),
        sa.Column("form_name", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("form_text", sa.String(), nullable=False, server_default=""),
        sa.Column("visibility", sa.String(length=10), nullable=False, server_default="private"),
        sa.Column("created_at", stamp, nullable=False),
        sa.Column("updated_at", stamp, nullable=False),
    )
    op.create_index("ix_user_templates_owner_id", "user_templates", ["owner_id"])
    catalog = op.create_table(
        "connector_catalog",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("slug", sa.String(length=60), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=False, server_default=""),
        sa.Column("category", sa.String(length=30), nullable=False, server_default=""),
        sa.Column("transport", sa.String(length=10), nullable=False, server_default="http"),
        sa.Column("endpoint", sa.String(length=500), nullable=False, server_default=""),
        sa.Column("env", json, nullable=True),
        sa.Column("required_env", json, nullable=True),
        sa.Column("auth", sa.String(length=20), nullable=False, server_default="none"),
        sa.Column("tools_key_var", sa.String(length=60), nullable=False, server_default=""),
        sa.Column("tools_key_feature", sa.String(length=20), nullable=False,
                  server_default=""),
        sa.Column("kinds", json, nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("updated_by", sa.String(), nullable=True),
        sa.Column("created_at", stamp, nullable=False),
        sa.Column("updated_at", stamp, nullable=False),
    )
    op.create_index("ix_connector_catalog_slug", "connector_catalog", ["slug"], unique=True)
    now = datetime.now(UTC)
    op.bulk_insert(catalog, [
        {**row, "id": str(uuid.uuid4()), "required_env": [], "auth": "none", "enabled": True,
         "position": n, "updated_by": None, "created_at": now, "updated_at": now}
        for n, row in enumerate(SEED)
    ])


def downgrade() -> None:
    op.drop_index("ix_connector_catalog_slug", table_name="connector_catalog")
    op.drop_table("connector_catalog")
    op.drop_index("ix_user_templates_owner_id", table_name="user_templates")
    op.drop_table("user_templates")
    op.drop_column("agents", "process_settings")
