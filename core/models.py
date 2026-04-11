from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Float, Integer, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class VideoCache(Base):
    __tablename__ = 'video_cache'

    url: Mapped[str] = mapped_column(Text, primary_key=True)
    file_id: Mapped[str] = mapped_column(Text, nullable=False)
    uploader: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploader_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    like_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    comment_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    repost_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    upload_date: Mapped[str | None] = mapped_column(Text, nullable=True)
    detected_country: Mapped[str | None] = mapped_column(Text, nullable=True)

    width: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    height: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    fps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    duration: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)

    file_size: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    song_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    ai_comment: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        index=True,
        server_default=func.current_timestamp(),
    )


class AppState(Base):
    __tablename__ = 'app_state'

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
    )


class User(Base):
    __tablename__ = 'users'

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    username: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    )
    settings_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class BonusVideo(Base):
    __tablename__ = 'bonus_videos'

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_url: Mapped[str] = mapped_column(Text, nullable=False, unique=True, index=True)
    video_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)

    uploader: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploader_id: Mapped[str | None] = mapped_column(Text, nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    initial_view_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    checked_view_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    bonus_points: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    status: Mapped[str] = mapped_column(Text, default='pending', nullable=False, index=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    check_after: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
        index=True,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
