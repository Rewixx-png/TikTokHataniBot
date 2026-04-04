import asyncio
import datetime
import html
import logging
import os
import re

from aiogram import Bot
from aiogram.types import FSInputFile
from babel import Locale
from yt_dlp import YoutubeDL

from core.config import (
    BOT_OWNER_ID,
    TIKTOK_WATCH_ENABLED,
    TIKTOK_WATCH_POLL_SECONDS,
    TIKTOK_WATCH_PROFILE_URL,
    TIKTOK_WATCH_TARGET_CHAT_ID,
    TIKTOK_WATCH_TARGET_THREAD_ID,
)
from core.database import get_app_state, set_app_state
from services.downloader import TikTokDownloader


logger = logging.getLogger(__name__)
RU_LOCALE = Locale.parse('ru')

CUSTOM_EMOJI = {
    'bell': ('6039486778597970865', '🔔'),
    'info': ('6028435952299413210', 'ℹ️'),
    'file': ('5877680341057015789', '📁'),
    'date': ('5967412305338568701', '📅'),
    'region': ('5985479497586053461', '🗺️'),
    'song': ('5282852032263233269', '🎵'),
}

TG_EMOJI_TAG_RE = re.compile(r'<tg-emoji\s+emoji-id="[^"]+">([^<]*)</tg-emoji>')


class TikTokProfileWatcher:
    def __init__(self):
        self.owner_id = BOT_OWNER_ID
        self.target_chat_id = TIKTOK_WATCH_TARGET_CHAT_ID
        self.target_thread_id = TIKTOK_WATCH_TARGET_THREAD_ID
        self.profile_url = TIKTOK_WATCH_PROFILE_URL
        self.poll_seconds = TIKTOK_WATCH_POLL_SECONDS
        self.enabled = bool(TIKTOK_WATCH_ENABLED and self.target_chat_id and self.profile_url)
        self.profile_username = self._extract_username(self.profile_url)
        state_suffix = self.profile_username or self.profile_url
        self.state_key = f'tiktok_watch_last_video:{state_suffix}'
        self.cookie_file = os.path.join(os.getcwd(), 'cookies.txt')
        self.downloader = TikTokDownloader()

    def _extract_username(self, profile_url: str) -> str:
        match = re.search(r'tiktok\.com/@([^/?]+)', profile_url or '', re.IGNORECASE)
        if not match:
            return ''
        return match.group(1).strip()

    def _ydl_opts(self) -> dict:
        options = {
            'quiet': True,
            'no_warnings': True,
            'skip_download': True,
            'extract_flat': False,
            'playlistend': 1,
            'nocheckcertificate': True,
            'http_headers': {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/121.0.0.0 Safari/537.36'
                ),
                'Accept-Language': 'en-US,en;q=0.9',
            },
        }

        if os.path.exists(self.cookie_file) and os.path.getsize(self.cookie_file) > 0:
            options['cookiefile'] = self.cookie_file

        return options

    def _custom_emoji(self, name: str) -> str:
        emoji_id, fallback = CUSTOM_EMOJI[name]
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

    def _strip_custom_emoji_tags(self, text: str) -> str:
        return TG_EMOJI_TAG_RE.sub(r'\1', text or '')

    def _format_date(self, raw_date: str | None) -> str:
        try:
            if not raw_date:
                return datetime.datetime.now().strftime('%d.%m.%Y')
            dt = datetime.datetime.strptime(str(raw_date), '%Y%m%d')
            return dt.strftime('%d.%m.%Y')
        except Exception:
            return datetime.datetime.now().strftime('%d.%m.%Y')

    def _country_flag_emoji(self, country_code: str) -> str:
        code = (country_code or '').strip().upper()
        if len(code) != 2 or not code.isalpha():
            return ''
        return ''.join(chr(127397 + ord(char)) for char in code)

    def _country_name_ru(self, country_code: str) -> str:
        code = (country_code or '').strip().upper()
        if len(code) != 2 or not code.isalpha():
            return country_code
        return RU_LOCALE.territories.get(code) or code

    def _format_region(self, country_value) -> str:
        raw = str(country_value or '').strip()
        if raw.lower() in {'', 'unknown', 'none', 'null', 'n/a'}:
            return ''

        code = raw.upper()
        if len(code) == 2 and code.isalpha():
            flag = self._country_flag_emoji(code)
            name = self._country_name_ru(code)
            if flag:
                return f'{flag} {name}'
            return name

        return raw

    def _normalize_duration_seconds(self, value) -> float:
        try:
            duration = float(value or 0)
        except (TypeError, ValueError):
            return 0.0

        if duration > 1000:
            return duration / 1000
        return duration

    def _build_caption(self, latest: dict, info: dict) -> str:
        profile_url = html.escape(self.profile_url, quote=True)
        display_name = (
            latest.get('channel_name')
            or latest.get('uploader_name')
            or self.profile_username
            or latest.get('uploader_id')
            or 'Автор'
        )
        safe_display_name = html.escape(str(display_name))
        video_url = html.escape(str(latest.get('video_url') or ''), quote=True)

        description = html.escape(str(info.get('description') or '').strip() or 'Без описания')
        if len(description) > 300:
            description = description[:297] + '...'

        duration = int(self._normalize_duration_seconds(info.get('duration', 0)))
        width = int(info.get('width', 0) or 0)
        height = int(info.get('height', 0) or 0)
        fps = int(info.get('fps', 0) or 0)
        file_size_mb = float(info.get('file_size', 0) or 0) / (1024 * 1024)
        upload_date = self._format_date(info.get('upload_date'))

        region_text = self._format_region(info.get('detected_country'))
        region_line = ''
        if region_text:
            region_line = f'{self._custom_emoji("region")} Регион: {html.escape(region_text)}\n\n'

        return (
            f'{self._custom_emoji("bell")} '
            f'<a href="{profile_url}">{safe_display_name}</a> выложил новое видео:\n'
            f'{video_url}\n'
            f'{self._custom_emoji("info")} \n'
            f'<blockquote>{description}</blockquote>\n'
            f'🎚️ Обычное\n'
            f'{self._custom_emoji("file")} {duration}s | {width}×{height} | {fps}fps | {file_size_mb:.1f}MB\n'
            f'{self._custom_emoji("date")} {upload_date}\n'
            f'{region_line}'
            f'{self._custom_emoji("song")} <i>Original Sound</i>'
        )

    def fetch_latest_video(self) -> dict:
        try:
            with YoutubeDL(self._ydl_opts()) as ydl:
                info = ydl.extract_info(self.profile_url, download=False)
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
            }

        entries = info.get('entries') or []
        if not entries:
            return {
                'status': 'error',
                'message': 'No videos found on watched profile',
            }

        latest = entries[0]
        video_id = str(latest.get('id') or '').strip()
        uploader_id = str(latest.get('uploader_id') or '').strip()
        uploader_name = str(latest.get('uploader') or self.profile_username or '').strip()
        channel_name = str(latest.get('channel') or '').strip()
        title = str(latest.get('title') or '').strip()
        video_url = str(latest.get('webpage_url') or latest.get('url') or '').strip()

        if not video_url and video_id and uploader_name:
            video_url = f'https://www.tiktok.com/@{uploader_name}/video/{video_id}'

        if not video_id or not video_url:
            return {
                'status': 'error',
                'message': 'Failed to parse latest TikTok video data',
            }

        return {
            'status': 'success',
            'video_id': video_id,
            'video_url': video_url,
            'uploader_id': uploader_id,
            'uploader_name': uploader_name,
            'channel_name': channel_name,
            'title': title,
        }

    async def _notify_owner(self, bot: Bot, latest: dict, chat_id: int | None = None) -> bool:
        file_path = ''
        caption = ''
        download_info = {}
        target_chat_id = int(chat_id or self.target_chat_id)
        target_thread_id = self.target_thread_id if chat_id is None else 0

        try:
            download_info = await self.downloader.download_video(
                latest['video_url'],
                quality='normal',
            )
            if download_info.get('status') == 'error':
                logger.warning(
                    'Profile watcher failed to download new video, fallback to text message: %s',
                    download_info.get('message', 'Unknown error'),
                )
                await bot.send_message(
                    chat_id=target_chat_id,
                    text=(
                        f'{self._custom_emoji("bell")} '
                        f'Новое видео: <a href="{html.escape(str(latest.get("video_url") or ""), quote=True)}">Ссылка</a>'
                    ),
                    disable_web_page_preview=True,
                    message_thread_id=target_thread_id or None,
                )
                return True

            file_path = download_info['file_path']
            caption = self._build_caption(latest, download_info)

            input_file = FSInputFile(file_path)
            await bot.send_video(
                chat_id=target_chat_id,
                video=input_file,
                caption=caption,
                parse_mode='HTML',
                width=int(download_info.get('width') or 0),
                height=int(download_info.get('height') or 0),
                duration=int(self._normalize_duration_seconds(download_info.get('duration') or 0)),
                message_thread_id=target_thread_id or None,
            )
            return True
        except Exception as e:
            try:
                if not file_path:
                    raise RuntimeError('No local video for fallback send')
                fallback_caption = self._strip_custom_emoji_tags(caption)
                input_file = FSInputFile(file_path)
                await bot.send_video(
                    chat_id=target_chat_id,
                    video=input_file,
                    caption=fallback_caption,
                    parse_mode='HTML',
                    width=int(download_info.get('width') or 0),
                    height=int(download_info.get('height') or 0),
                    duration=int(self._normalize_duration_seconds(download_info.get('duration') or 0)),
                    message_thread_id=target_thread_id or None,
                )
                return True
            except Exception:
                logger.warning('Failed to send profile watcher notification: %s', e)
            return False
        finally:
            if file_path and os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass

    async def send_latest_preview(self, bot: Bot, chat_id: int | None = None) -> dict:
        latest = await asyncio.to_thread(self.fetch_latest_video)
        if latest.get('status') != 'success':
            return latest

        notified = await self._notify_owner(bot, latest, chat_id=chat_id)
        if not notified:
            return {
                'status': 'error',
                'message': 'Не удалось отправить тестовое уведомление',
            }

        return {
            'status': 'success',
            'video_id': latest.get('video_id', ''),
            'video_url': latest.get('video_url', ''),
        }

    async def run(self, bot: Bot):
        if not self.enabled:
            logger.info('TikTok profile watcher disabled')
            return

        logger.info(
            'TikTok profile watcher enabled: profile=%s interval=%ss target_chat_id=%s thread_id=%s',
            self.profile_url,
            self.poll_seconds,
            self.target_chat_id,
            self.target_thread_id,
        )

        while True:
            try:
                latest = await asyncio.to_thread(self.fetch_latest_video)
                if latest.get('status') != 'success':
                    logger.warning('TikTok profile watcher probe failed: %s', latest.get('message', 'Unknown error'))
                else:
                    latest_video_id = latest['video_id']
                    last_known_video_id = await get_app_state(self.state_key)

                    if not last_known_video_id:
                        await set_app_state(self.state_key, latest_video_id)
                        logger.info('TikTok watcher initialized at video id: %s', latest_video_id)
                    elif latest_video_id != last_known_video_id:
                        notified = await self._notify_owner(bot, latest)
                        if notified:
                            await set_app_state(self.state_key, latest_video_id)
                            logger.info('TikTok watcher new video notified: %s', latest_video_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('TikTok profile watcher loop error')

            await asyncio.sleep(self.poll_seconds)
