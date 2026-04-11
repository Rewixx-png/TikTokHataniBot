"""initial schema

Revision ID: 20260408_0001
Revises:
Create Date: 2026-04-08 00:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '20260408_0001'
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'app_state',
        sa.Column('key', sa.Text(), nullable=False),
        sa.Column('value', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('key'),
    )

    op.create_table(
        'users',
        sa.Column('id', sa.BigInteger(), nullable=False),
        sa.Column('username', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('settings_json', sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_users_username', 'users', ['username'], unique=False)

    op.create_table(
        'video_cache',
        sa.Column('url', sa.Text(), nullable=False),
        sa.Column('file_id', sa.Text(), nullable=False),
        sa.Column('uploader', sa.Text(), nullable=True),
        sa.Column('uploader_id', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('like_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('view_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('comment_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('repost_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('upload_date', sa.Text(), nullable=True),
        sa.Column('detected_country', sa.Text(), nullable=True),
        sa.Column('width', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('height', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('fps', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('duration', sa.Float(), nullable=False, server_default='0'),
        sa.Column('file_size', sa.BigInteger(), nullable=False, server_default='0'),
        sa.Column('song_name', sa.Text(), nullable=True),
        sa.Column('ai_comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('url'),
    )
    op.create_index('ix_video_cache_created_at', 'video_cache', ['created_at'], unique=False)

    op.create_table(
        'bonus_videos',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('video_url', sa.Text(), nullable=False),
        sa.Column('video_id', sa.Text(), nullable=True),
        sa.Column('uploader', sa.Text(), nullable=True),
        sa.Column('uploader_id', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('initial_view_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('checked_view_count', sa.Integer(), nullable=True),
        sa.Column('bonus_points', sa.Float(), nullable=False, server_default='0'),
        sa.Column('status', sa.Text(), nullable=False, server_default='pending'),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('check_after', sa.DateTime(), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('processed_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('video_url'),
    )
    op.create_index('ix_bonus_videos_video_url', 'bonus_videos', ['video_url'], unique=False)
    op.create_index('ix_bonus_videos_video_id', 'bonus_videos', ['video_id'], unique=False)
    op.create_index('ix_bonus_videos_uploader_id', 'bonus_videos', ['uploader_id'], unique=False)
    op.create_index('ix_bonus_videos_status', 'bonus_videos', ['status'], unique=False)
    op.create_index('ix_bonus_videos_check_after', 'bonus_videos', ['check_after'], unique=False)
    op.create_index('ix_bonus_videos_created_at', 'bonus_videos', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_bonus_videos_created_at', table_name='bonus_videos')
    op.drop_index('ix_bonus_videos_check_after', table_name='bonus_videos')
    op.drop_index('ix_bonus_videos_status', table_name='bonus_videos')
    op.drop_index('ix_bonus_videos_uploader_id', table_name='bonus_videos')
    op.drop_index('ix_bonus_videos_video_id', table_name='bonus_videos')
    op.drop_index('ix_bonus_videos_video_url', table_name='bonus_videos')
    op.drop_table('bonus_videos')

    op.drop_index('ix_video_cache_created_at', table_name='video_cache')
    op.drop_table('video_cache')

    op.drop_index('ix_users_username', table_name='users')
    op.drop_table('users')

    op.drop_table('app_state')
