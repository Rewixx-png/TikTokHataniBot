from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import math
import re
from urllib.parse import urlparse, urlunparse

from core.config import BONUS_CHECK_DELAY_DAYS, BONUS_CHECK_INTERVAL_SECONDS, BONUS_ENABLED
from core.database import (
    BONUS_STATUS_AWARDED,
    BONUS_STATUS_ERROR,
    BONUS_STATUS_MISSING,
    BONUS_STATUS_ZERO,
    get_bonus_profile,
    get_bonus_top,
    get_due_bonus_videos,
    mark_bonus_video_processed,
    save_bonus_video_candidate,
)
from services.downloader import TikTokDownloader


logger = logging.getLogger(__name__)
HATANI_HASHTAG_RE = re.compile(r'(?i)(?:^|\s)#hatanisquad\b')
VIDEO_ID_RE = re.compile(r'/video/(\d+)', re.IGNORECASE)


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


class BonusTrackerService:
    def __init__(self) -> None:
        self.enabled = BONUS_ENABLED
        self.check_delay_days = BONUS_CHECK_DELAY_DAYS
        self.check_interval_seconds = BONUS_CHECK_INTERVAL_SECONDS
        self.downloader = TikTokDownloader()
        self._process_lock = asyncio.Lock()

    def _canonical_video_url(self, value: str) -> str:
        raw = str(value or '').strip()
        if not raw:
            return ''

        parsed = urlparse(raw)
        scheme = parsed.scheme or 'https'
        host = (parsed.netloc or 'www.tiktok.com').lower()
        path = parsed.path or ''
        path = re.sub(r'/photo/(\d+)', r'/video/\1', path, flags=re.IGNORECASE)
        return urlunparse((scheme, host, path, '', '', ''))

    def _extract_video_id(self, value: str) -> str:
        match = VIDEO_ID_RE.search(str(value or ''))
        if not match:
            return ''
        return match.group(1)

    def _has_hatani_hashtag(self, description: str) -> bool:
        return bool(HATANI_HASHTAG_RE.search(str(description or '')))

    def _calculate_bonus_points(self, view_count: int) -> float:
        safe_views = max(0, int(view_count or 0))
        raw_points = safe_views / 10_000
        return math.floor(raw_points * 100) / 100

    async def register_video_if_eligible(self, info: dict, source_url: str) -> bool:
        description = str(info.get('description') or '').strip()
        if not self._has_hatani_hashtag(description):
            return False

        primary_url = str(info.get('source_url') or source_url or '').strip()
        canonical_url = self._canonical_video_url(primary_url)
        if not canonical_url:
            return False

        uploader = str(info.get('uploader') or '').strip() or 'Unknown'
        uploader_id = str(info.get('uploader_id') or '').strip().lstrip('@')
        initial_view_count = _safe_int(info.get('view_count'), 0)

        created = await save_bonus_video_candidate(
            {
                'video_url': canonical_url,
                'video_id': self._extract_video_id(canonical_url),
                'uploader': uploader,
                'uploader_id': uploader_id,
                'description': description,
                'initial_view_count': initial_view_count,
                'check_after': datetime.utcnow() + timedelta(days=self.check_delay_days),
            }
        )
        if created:
            logger.info('Bonus candidate saved: %s (%s)', canonical_url, uploader_id or uploader)
        return created

    async def process_due_videos(self, limit: int = 40) -> dict[str, int]:
        if not self.enabled:
            return {'processed': 0, 'awarded': 0, 'missing': 0, 'zero': 0, 'error': 0}

        async with self._process_lock:
            due_entries = await get_due_bonus_videos(limit=limit)
            if not due_entries:
                return {'processed': 0, 'awarded': 0, 'missing': 0, 'zero': 0, 'error': 0}

            stats = {'processed': 0, 'awarded': 0, 'missing': 0, 'zero': 0, 'error': 0}

            for entry in due_entries:
                entry_id = int(entry.get('id') or 0)
                video_url = str(entry.get('video_url') or '').strip()

                if not entry_id or not video_url:
                    continue

                try:
                    probe = await self.downloader.probe_video(video_url)
                    if probe.get('status') != 'success':
                        await mark_bonus_video_processed(
                            bonus_video_id=entry_id,
                            status=BONUS_STATUS_MISSING,
                            checked_view_count=0,
                            bonus_points=0.0,
                            error_message=str(probe.get('message') or 'Video unavailable after week'),
                        )
                        stats['processed'] += 1
                        stats['missing'] += 1
                        continue

                    checked_view_count = max(0, _safe_int(probe.get('view_count'), 0))
                    points = self._calculate_bonus_points(checked_view_count)

                    if points > 0:
                        status = BONUS_STATUS_AWARDED
                        stats['awarded'] += 1
                    else:
                        status = BONUS_STATUS_ZERO
                        stats['zero'] += 1

                    await mark_bonus_video_processed(
                        bonus_video_id=entry_id,
                        status=status,
                        checked_view_count=checked_view_count,
                        bonus_points=points,
                        error_message='',
                    )
                    stats['processed'] += 1
                except Exception as error:
                    await mark_bonus_video_processed(
                        bonus_video_id=entry_id,
                        status=BONUS_STATUS_ERROR,
                        checked_view_count=0,
                        bonus_points=0.0,
                        error_message=str(error),
                    )
                    stats['processed'] += 1
                    stats['error'] += 1

            return stats

    async def run(self) -> None:
        if not self.enabled:
            logger.info('Bonus tracker disabled')
            return

        logger.info(
            'Bonus tracker started: delay=%sd interval=%ss',
            self.check_delay_days,
            self.check_interval_seconds,
        )

        while True:
            try:
                stats = await self.process_due_videos()
                if stats['processed'] > 0:
                    logger.info(
                        'Bonus tracker processed=%s awarded=%s missing=%s zero=%s error=%s',
                        stats['processed'],
                        stats['awarded'],
                        stats['missing'],
                        stats['zero'],
                        stats['error'],
                    )
            except Exception as error:
                logger.exception('Bonus tracker cycle failed: %s', error)

            await asyncio.sleep(self.check_interval_seconds)

    async def bonus_for_participant(self, participant: str) -> dict:
        return await get_bonus_profile(participant)

    async def top_bonus(self, limit: int = 10) -> list[dict]:
        return await get_bonus_top(limit=limit)


bonus_tracker_service = BonusTrackerService()
