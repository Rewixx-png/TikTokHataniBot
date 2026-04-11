from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import os
from pathlib import Path
from typing import Any

from sqlalchemy import delete, desc, func, select, text, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.models import AppState, Base, BonusVideo, VideoCache


CACHE_TTL_DAYS = 7
BONUS_STATUS_PENDING = 'pending'
BONUS_STATUS_AWARDED = 'awarded'
BONUS_STATUS_MISSING = 'missing'
BONUS_STATUS_ZERO = 'zero'
BONUS_STATUS_ERROR = 'error'

DEFAULT_DB_PATH = Path(os.getcwd(), 'cache.db').resolve()
DATABASE_URL = os.getenv('DATABASE_URL', f'sqlite+aiosqlite:///{DEFAULT_DB_PATH.as_posix()}').strip()

engine = create_async_engine(
    DATABASE_URL,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    autoflush=False,
    expire_on_commit=False,
)


async def init_db() -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await _ensure_sqlite_schema_compatibility(connection)


async def close_db() -> None:
    await engine.dispose()


async def _ensure_sqlite_schema_compatibility(connection) -> None:
    if connection.dialect.name != 'sqlite':
        return

    result = await connection.execute(text('PRAGMA table_info(video_cache)'))
    video_cache_columns = {row[1] for row in result.fetchall()}

    alter_statements: list[str] = []

    column_sql = {
        'uploader_id': 'ALTER TABLE video_cache ADD COLUMN uploader_id TEXT',
        'view_count': 'ALTER TABLE video_cache ADD COLUMN view_count INTEGER DEFAULT 0',
        'comment_count': 'ALTER TABLE video_cache ADD COLUMN comment_count INTEGER DEFAULT 0',
        'repost_count': 'ALTER TABLE video_cache ADD COLUMN repost_count INTEGER DEFAULT 0',
        'detected_country': 'ALTER TABLE video_cache ADD COLUMN detected_country TEXT',
        'width': 'ALTER TABLE video_cache ADD COLUMN width INTEGER DEFAULT 0',
        'height': 'ALTER TABLE video_cache ADD COLUMN height INTEGER DEFAULT 0',
        'fps': 'ALTER TABLE video_cache ADD COLUMN fps INTEGER DEFAULT 0',
        'duration': 'ALTER TABLE video_cache ADD COLUMN duration REAL DEFAULT 0',
        'ai_comment': 'ALTER TABLE video_cache ADD COLUMN ai_comment TEXT',
    }

    for column_name, statement in column_sql.items():
        if column_name not in video_cache_columns:
            alter_statements.append(statement)

    for statement in alter_statements:
        await connection.execute(text(statement))

    app_state_result = await connection.execute(text('PRAGMA table_info(app_state)'))
    app_state_columns = {row[1] for row in app_state_result.fetchall()}
    if 'updated_at' not in app_state_columns:
        await connection.execute(
            text('ALTER TABLE app_state ADD COLUMN updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP')
        )


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session


@asynccontextmanager
async def _session_scope(session: AsyncSession | None) -> AsyncGenerator[AsyncSession, None]:
    if session is not None:
        yield session
        return

    async with async_session_factory() as new_session:
        yield new_session


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _cache_cutoff() -> datetime:
    return datetime.utcnow() - timedelta(days=CACHE_TTL_DAYS)


def _serialize_video_cache(row: VideoCache) -> dict[str, Any]:
    return {
        'url': row.url,
        'file_id': row.file_id,
        'uploader': row.uploader,
        'uploader_id': row.uploader_id,
        'description': row.description,
        'like_count': row.like_count,
        'view_count': row.view_count,
        'comment_count': row.comment_count,
        'repost_count': row.repost_count,
        'upload_date': row.upload_date,
        'detected_country': row.detected_country,
        'width': row.width,
        'height': row.height,
        'fps': row.fps,
        'duration': row.duration,
        'file_size': row.file_size,
        'song_name': row.song_name,
        'ai_comment': row.ai_comment,
        'created_at': row.created_at,
    }


def _build_video_cache_payload(data: dict[str, Any]) -> dict[str, Any]:
    return {
        'url': str(data.get('url') or ''),
        'file_id': str(data.get('file_id') or ''),
        'uploader': data.get('uploader'),
        'uploader_id': data.get('uploader_id'),
        'description': data.get('description'),
        'like_count': _safe_int(data.get('like_count', 0)),
        'view_count': _safe_int(data.get('view_count', 0)),
        'comment_count': _safe_int(data.get('comment_count', 0)),
        'repost_count': _safe_int(data.get('repost_count', 0)),
        'upload_date': data.get('upload_date'),
        'detected_country': data.get('detected_country'),
        'width': _safe_int(data.get('width', 0)),
        'height': _safe_int(data.get('height', 0)),
        'fps': _safe_int(data.get('fps', 0)),
        'duration': _safe_float(data.get('duration', 0.0)),
        'file_size': _safe_int(data.get('file_size', 0)),
        'song_name': data.get('song_name'),
        'ai_comment': data.get('ai_comment', ''),
    }


def _normalize_participant_key(value: str | None) -> str:
    raw = str(value or '').strip()
    if raw.startswith('@'):
        raw = raw[1:]
    return raw.strip().lower()


def _serialize_bonus_video(row: BonusVideo) -> dict[str, Any]:
    return {
        'id': row.id,
        'video_url': row.video_url,
        'video_id': row.video_id,
        'uploader': row.uploader,
        'uploader_id': row.uploader_id,
        'description': row.description,
        'initial_view_count': row.initial_view_count,
        'checked_view_count': row.checked_view_count,
        'bonus_points': row.bonus_points,
        'status': row.status,
        'error_message': row.error_message,
        'check_after': row.check_after,
        'created_at': row.created_at,
        'processed_at': row.processed_at,
    }


async def get_cached_video(
    url: str,
    session: AsyncSession | None = None,
) -> dict[str, Any] | None:
    if not url:
        return None

    async with _session_scope(session) as db:
        stmt = select(VideoCache).where(
            VideoCache.url == url,
            VideoCache.created_at > _cache_cutoff(),
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None

        row.created_at = datetime.utcnow()
        await db.commit()
        return _serialize_video_cache(row)


async def get_cached_video_by_file_id(
    file_id: str,
    session: AsyncSession | None = None,
) -> dict[str, Any] | None:
    if not file_id:
        return None

    async with _session_scope(session) as db:
        stmt = (
            select(VideoCache)
            .where(VideoCache.file_id == file_id)
            .order_by(VideoCache.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        row = result.scalar_one_or_none()
        if row is None:
            return None

        row.created_at = datetime.utcnow()
        await db.commit()
        return _serialize_video_cache(row)


async def save_video_cache(
    data: dict[str, Any],
    session: AsyncSession | None = None,
) -> None:
    payload = _build_video_cache_payload(data)
    if not payload['url'] or not payload['file_id']:
        return

    payload['created_at'] = datetime.utcnow()

    stmt = sqlite_insert(VideoCache).values(**payload)
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=[VideoCache.url],
        set_=payload,
    )

    async with _session_scope(session) as db:
        await db.execute(upsert_stmt)
        await db.commit()


async def cleanup_old_cache(session: AsyncSession | None = None) -> None:
    stmt = delete(VideoCache).where(VideoCache.created_at < _cache_cutoff())
    async with _session_scope(session) as db:
        await db.execute(stmt)
        await db.commit()


async def get_app_state(
    key: str,
    session: AsyncSession | None = None,
) -> str | None:
    if not key:
        return None

    async with _session_scope(session) as db:
        stmt = select(AppState.value).where(AppState.key == key)
        result = await db.execute(stmt)
        return result.scalar_one_or_none()


async def set_app_state(
    key: str,
    value: str,
    session: AsyncSession | None = None,
) -> None:
    if not key:
        return

    now = datetime.utcnow()
    stmt = sqlite_insert(AppState).values(
        key=key,
        value=value,
        updated_at=now,
    )
    upsert_stmt = stmt.on_conflict_do_update(
        index_elements=[AppState.key],
        set_={
            'value': value,
            'updated_at': now,
        },
    )

    async with _session_scope(session) as db:
        await db.execute(upsert_stmt)
        await db.commit()


async def save_bonus_video_candidate(
    data: dict[str, Any],
    session: AsyncSession | None = None,
) -> bool:
    video_url = str(data.get('video_url') or '').strip()
    if not video_url:
        return False

    uploader = str(data.get('uploader') or '').strip() or 'Unknown'
    uploader_id = str(data.get('uploader_id') or '').strip()
    video_id = str(data.get('video_id') or '').strip()
    description = str(data.get('description') or '').strip()
    initial_view_count = _safe_int(data.get('initial_view_count', 0))

    check_after = data.get('check_after')
    if not isinstance(check_after, datetime):
        check_after = datetime.utcnow() + timedelta(days=7)

    payload = {
        'video_url': video_url,
        'video_id': video_id or None,
        'uploader': uploader,
        'uploader_id': uploader_id,
        'description': description,
        'initial_view_count': initial_view_count,
        'check_after': check_after,
        'status': BONUS_STATUS_PENDING,
    }

    stmt = sqlite_insert(BonusVideo).values(**payload)
    upsert_stmt = stmt.on_conflict_do_nothing(index_elements=[BonusVideo.video_url])

    async with _session_scope(session) as db:
        result = await db.execute(upsert_stmt)
        await db.commit()
        return (result.rowcount or 0) > 0


async def get_due_bonus_videos(
    limit: int = 50,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    now = datetime.utcnow()
    safe_limit = max(1, min(int(limit or 50), 300))

    stmt = (
        select(BonusVideo)
        .where(BonusVideo.status == BONUS_STATUS_PENDING)
        .where(BonusVideo.check_after <= now)
        .order_by(BonusVideo.check_after.asc(), BonusVideo.id.asc())
        .limit(safe_limit)
    )

    async with _session_scope(session) as db:
        result = await db.execute(stmt)
        rows = result.scalars().all()
        return [_serialize_bonus_video(row) for row in rows]


async def mark_bonus_video_processed(
    bonus_video_id: int,
    status: str,
    checked_view_count: int = 0,
    bonus_points: float = 0.0,
    error_message: str = '',
    session: AsyncSession | None = None,
) -> None:
    now = datetime.utcnow()
    safe_status = str(status or BONUS_STATUS_ERROR).strip().lower()
    if safe_status not in {
        BONUS_STATUS_AWARDED,
        BONUS_STATUS_MISSING,
        BONUS_STATUS_ZERO,
        BONUS_STATUS_ERROR,
        BONUS_STATUS_PENDING,
    }:
        safe_status = BONUS_STATUS_ERROR

    stmt = (
        update(BonusVideo)
        .where(BonusVideo.id == int(bonus_video_id))
        .where(BonusVideo.status == BONUS_STATUS_PENDING)
        .values(
            checked_view_count=max(0, _safe_int(checked_view_count, 0)),
            bonus_points=max(0.0, _safe_float(bonus_points, 0.0)),
            status=safe_status,
            error_message=str(error_message or '').strip() or None,
            processed_at=now,
        )
    )

    async with _session_scope(session) as db:
        await db.execute(stmt)
        await db.commit()


async def get_bonus_profile(
    participant: str,
    session: AsyncSession | None = None,
) -> dict[str, Any]:
    key = _normalize_participant_key(participant)
    if not key:
        return {
            'participant': '',
            'display_name': '',
            'bonus_points': 0.0,
            'awarded_videos': 0,
            'pending_videos': 0,
            'processed_videos': 0,
        }

    participant_expr = func.lower(func.coalesce(func.nullif(BonusVideo.uploader, ''), BonusVideo.uploader_id, ''))

    awarded_stmt = select(
        func.coalesce(func.sum(BonusVideo.bonus_points), 0.0),
        func.count(BonusVideo.id),
        func.max(BonusVideo.uploader),
    ).where(
        participant_expr == key,
        BonusVideo.status == BONUS_STATUS_AWARDED,
    )

    pending_stmt = select(func.count(BonusVideo.id)).where(
        participant_expr == key,
        BonusVideo.status == BONUS_STATUS_PENDING,
    )

    processed_stmt = select(func.count(BonusVideo.id)).where(
        participant_expr == key,
        BonusVideo.status != BONUS_STATUS_PENDING,
    )

    async with _session_scope(session) as db:
        awarded_result = await db.execute(awarded_stmt)
        bonus_points, awarded_videos, display_name = awarded_result.one()

        pending_result = await db.execute(pending_stmt)
        pending_videos = int(pending_result.scalar() or 0)

        processed_result = await db.execute(processed_stmt)
        processed_videos = int(processed_result.scalar() or 0)

        display = str(display_name or '').strip() or key
        return {
            'participant': key,
            'display_name': display,
            'bonus_points': max(0.0, float(bonus_points or 0.0)),
            'awarded_videos': int(awarded_videos or 0),
            'pending_videos': pending_videos,
            'processed_videos': processed_videos,
        }


async def get_bonus_top(
    limit: int = 10,
    session: AsyncSession | None = None,
) -> list[dict[str, Any]]:
    participant_expr = func.lower(func.coalesce(func.nullif(BonusVideo.uploader, ''), BonusVideo.uploader_id, ''))
    total_expr = func.sum(BonusVideo.bonus_points)

    stmt = (
        select(
            participant_expr.label('participant'),
            func.max(BonusVideo.uploader).label('display_name'),
            total_expr.label('bonus_points'),
            func.count(BonusVideo.id).label('awarded_videos'),
        )
        .where(BonusVideo.status == BONUS_STATUS_AWARDED)
        .group_by(participant_expr)
        .having(total_expr > 0)
        .order_by(desc(total_expr))
        .limit(max(1, min(int(limit or 10), 50)))
    )

    async with _session_scope(session) as db:
        result = await db.execute(stmt)
        rows = result.fetchall()

    leaderboard: list[dict[str, Any]] = []
    for row in rows:
        participant = str(row.participant or '').strip()
        display_name = str(row.display_name or '').strip() or participant
        leaderboard.append(
            {
                'participant': participant,
                'display_name': display_name,
                'bonus_points': max(0.0, float(row.bonus_points or 0.0)),
                'awarded_videos': int(row.awarded_videos or 0),
            }
        )

    return leaderboard
