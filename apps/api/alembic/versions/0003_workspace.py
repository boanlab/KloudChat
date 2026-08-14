"""Workspace: projects, files, artifacts, skills, memories, agents, connectors.

Revision ID: 0003
Revises: 0002
Create Date: 2026-08-07 12:34:10.443812
"""

from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('agents',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('owner_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('model', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('system_prompt', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('tools', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('skill_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('kinds', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('temperature', sa.Float(), nullable=False),
    sa.Column('color', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('visibility', sa.Enum('private', 'org', name='agentvisibility'), nullable=False),
    sa.Column('installs', sa.Integer(), nullable=False),
    sa.Column('runs', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_agents_owner_id'), 'agents', ['owner_id'], unique=False)
    op.create_table('artifacts',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('session_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('project_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('kind', sa.Enum('report', 'deck', 'chart', 'image', 'audio', 'video', 'code', 'html', name='artifactkind'), nullable=False),
    sa.Column('title', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('storage_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_artifacts_user_id'), 'artifacts', ['user_id'], unique=False)
    op.create_index('ix_artifacts_user_updated', 'artifacts', ['user_id', 'updated_at'], unique=False)
    op.create_table('connectors',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('owner_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('category', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('transport', sa.Enum('stdio', 'http', 'sse', name='transport'), nullable=False),
    sa.Column('endpoint', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('env', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('auth_type', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('kinds', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('official', sa.Boolean(), nullable=False),
    sa.Column('installed', sa.Boolean(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('status', sa.Enum('connected', 'disconnected', 'needs_auth', 'error', name='connectorstatus'), nullable=False),
    sa.Column('last_sync_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_connectors_owner_id'), 'connectors', ['owner_id'], unique=False)
    op.create_table('files',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('project_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('session_id', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('mime', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('storage_key', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('text', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('tokens', sa.Integer(), nullable=False),
    sa.Column('error', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_files_user_id'), 'files', ['user_id'], unique=False)
    op.create_index('ix_files_user_project', 'files', ['user_id', 'project_id'], unique=False)
    op.create_table('memories',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('type', sa.Enum('user', 'feedback', 'project', 'reference', name='memorytype'), nullable=False),
    sa.Column('body', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('scope', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('links', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('pinned', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_memories_user_id'), 'memories', ['user_id'], unique=False)
    op.create_table('projects',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('user_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('emoji', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('instructions', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('skill_ids', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_projects_user_id'), 'projects', ['user_id'], unique=False)
    op.create_table('skills',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('owner_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('slug', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('when_to_use', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('body', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('source', sa.Enum('built_in', 'workspace', 'personal', name='skillsource'), nullable=False),
    sa.Column('kinds', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('version', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_skills_owner_id'), 'skills', ['owner_id'], unique=False)
    op.create_table('artifact_versions',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('artifact_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('data', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('storage_key', sqlmodel.sql.sqltypes.AutoString(), nullable=True),
    sa.Column('summary', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['artifact_id'], ['artifacts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_artifact_versions_artifact_id'), 'artifact_versions', ['artifact_id'], unique=False)
    op.create_table('connector_credentials',
    sa.Column('connector_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.PrimaryKeyConstraint('connector_id')
    )
    op.create_table('connector_tools',
    sa.Column('id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('connector_id', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('name', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('description', sqlmodel.sql.sqltypes.AutoString(), nullable=False),
    sa.Column('parameters', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('read_only', sa.Boolean(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['connector_id'], ['connectors.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_connector_tools_connector', 'connector_tools', ['connector_id', 'name'], unique=False)
    op.create_index(op.f('ix_connector_tools_connector_id'), 'connector_tools', ['connector_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_connector_tools_connector_id'), table_name='connector_tools')
    op.drop_index('ix_connector_tools_connector', table_name='connector_tools')
    op.drop_table('connector_tools')
    op.drop_table('connector_credentials')
    op.drop_index(op.f('ix_artifact_versions_artifact_id'), table_name='artifact_versions')
    op.drop_table('artifact_versions')
    op.drop_index(op.f('ix_skills_owner_id'), table_name='skills')
    op.drop_table('skills')
    op.drop_index(op.f('ix_projects_user_id'), table_name='projects')
    op.drop_table('projects')
    op.drop_index(op.f('ix_memories_user_id'), table_name='memories')
    op.drop_table('memories')
    op.drop_index('ix_files_user_project', table_name='files')
    op.drop_index(op.f('ix_files_user_id'), table_name='files')
    op.drop_table('files')
    op.drop_index(op.f('ix_connectors_owner_id'), table_name='connectors')
    op.drop_table('connectors')
    op.drop_index('ix_artifacts_user_updated', table_name='artifacts')
    op.drop_index(op.f('ix_artifacts_user_id'), table_name='artifacts')
    op.drop_table('artifacts')
    op.drop_index(op.f('ix_agents_owner_id'), table_name='agents')
    op.drop_table('agents')
