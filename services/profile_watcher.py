import asyncio
import datetime
import html
import json
import logging
import os
import re
from dataclasses import dataclass
from http.cookiejar import MozillaCookieJar
from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from babel import Locale
import requests
from yt_dlp import YoutubeDL

from core.config import (
    BOT_OWNER_ID,
    TIKTOK_WATCH_ENABLED,
    TIKTOK_WATCH_POLL_SECONDS,
    TIKTOK_WATCH_PROFILES,
    TIKTOK_WATCH_TARGET_CHAT_ID,
    TIKTOK_WATCH_TARGET_THREAD_ID,
)
from core.database import get_app_state, set_app_state
from services.bonus_tracker import bonus_tracker_service
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
HATANI_HASHTAG_RE = re.compile(r'(?i)(?:^|\s)#hatanisquad\b')


@dataclass(frozen=True)
class WatchProfile:
    key: str
    url: str
    label: str
    username: str
    state_key: str


class TikTokProfileWatcher:
    def __init__(self):
        self.owner_id = BOT_OWNER_ID
        self.target_chat_id = TIKTOK_WATCH_TARGET_CHAT_ID
        self.target_thread_id = TIKTOK_WATCH_TARGET_THREAD_ID
        self.poll_seconds = TIKTOK_WATCH_POLL_SECONDS
        self.profiles = self._normalize_profiles(TIKTOK_WATCH_PROFILES)
        self.profile_url = self.profiles[0].url if self.profiles else ''
        self.profile_username = self.profiles[0].username if self.profiles else ''
        self.enabled = bool(TIKTOK_WATCH_ENABLED and self.target_chat_id and self.profiles)
        self.cookie_file = os.path.join(os.getcwd(), 'cookies.txt')
        self.downloader = TikTokDownloader()
        self.milestone_rules = {}

    def _extract_username(self, profile_url: str) -> str:
        match = re.search(r'tiktok\.com/@([^/?]+)', profile_url or '', re.IGNORECASE)
        if not match:
            return ''
        return match.group(1).strip().lower()

    def _normalize_profiles(self, raw_profiles: list[dict]) -> list[WatchProfile]:
        normalized: list[WatchProfile] = []
        for item in raw_profiles or []:
            url = str((item or {}).get('url') or '').strip()
            if not url:
                continue

            username = self._extract_username(url)
            key = str((item or {}).get('key') or username or url).strip().lower()
            label = str((item or {}).get('label') or '').strip()
            state_suffix = key or username or url
            state_key = f'tiktok_watch_last_video:{state_suffix}'

            normalized.append(
                WatchProfile(
                    key=key,
                    url=url,
                    label=label,
                    username=username,
                    state_key=state_key,
                )
            )

        return normalized

    def profile_keys(self) -> list[str]:
        keys = []
        for profile in self.profiles:
            if profile.label:
                keys.append(profile.label)
            elif profile.username:
                keys.append(profile.username)
            else:
                keys.append(profile.key)
        return keys

    def _resolve_profile(self, profile_key: str | None) -> WatchProfile | None:
        if not self.profiles:
            return None

        if not profile_key:
            return self.profiles[0]

        normalized_key = str(profile_key).strip().lower()
        if normalized_key.startswith('@'):
            normalized_key = normalized_key[1:]

        for profile in self.profiles:
            if normalized_key in {
                profile.key,
                profile.username,
                profile.label.strip().lower(),
            }:
                return profile

        return None

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

    def _milestone_state_key(self, profile_key: str, threshold: int) -> str:
        return f'tiktok_milestone_sent:{profile_key}:{threshold}'

    def _create_profile_session(self) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/122.0.0.0 Safari/537.36'
                ),
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            }
        )

        cookie_path = Path(self.cookie_file)
        if cookie_path.exists() and cookie_path.stat().st_size > 0:
            try:
                cookie_jar = MozillaCookieJar()
                cookie_jar.load(str(cookie_path), ignore_discard=True, ignore_expires=True)
                session.cookies.update(cookie_jar)
            except Exception as e:
                logger.warning('Failed to load watcher cookies: %s', e)

        return session

    def _extract_universal_data(self, html_text: str):
        match = re.search(
            r'<script[^>]+id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
            html_text or '',
            re.IGNORECASE | re.DOTALL,
        )
        if not match:
            return None

        try:
            return json.loads(match.group(1))
        except Exception:
            return None

    def _find_user_info(self, payload: dict, username: str) -> dict | None:
        lower_username = str(username or '').strip().lower()

        direct_user_info = (
            (payload.get('__DEFAULT_SCOPE__') or {})
            .get('webapp.user-detail', {})
            .get('userInfo', {})
        )

        direct_unique_id = str((direct_user_info.get('user') or {}).get('uniqueId') or '').strip().lower()
        if direct_unique_id and (not lower_username or direct_unique_id == lower_username):
            return direct_user_info

        found = []

        def walk(node):
            if isinstance(node, dict):
                user_block = node.get('user') if isinstance(node.get('user'), dict) else None
                if user_block and (node.get('statsV2') or node.get('stats')):
                    candidate_id = str(user_block.get('uniqueId') or '').strip().lower()
                    if candidate_id and (not lower_username or candidate_id == lower_username):
                        found.append(node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(payload)
        return found[0] if found else None

    def _parse_follower_count(self, user_info: dict) -> int:
        stats_v2 = user_info.get('statsV2') or {}
        stats = user_info.get('stats') or {}

        raw_count = stats_v2.get('followerCount')
        if raw_count in (None, ''):
            raw_count = stats.get('followerCount')

        try:
            return int(float(raw_count))
        except (TypeError, ValueError):
            return 0

    def fetch_follower_count(self, profile: WatchProfile) -> dict:
        try:
            session = self._create_profile_session()
            response = session.get(profile.url, timeout=40)
        except Exception as e:
            return {
                'status': 'error',
                'message': f'Profile request failed: {e}',
            }

        if response.status_code >= 400:
            return {
                'status': 'error',
                'message': f'Profile request returned HTTP {response.status_code}',
            }

        payload = self._extract_universal_data(response.text or '')
        if not isinstance(payload, dict):
            return {
                'status': 'error',
                'message': 'Failed to parse profile payload',
            }

        user_info = self._find_user_info(payload, profile.username)
        if not user_info:
            return {
                'status': 'error',
                'message': 'Profile user info not found in payload',
            }

        follower_count = self._parse_follower_count(user_info)
        if follower_count <= 0:
            return {
                'status': 'error',
                'message': 'Follower count missing in profile payload',
            }

        return {
            'status': 'success',
            'follower_count': follower_count,
        }

    def _format_milestone_number(self, value: int) -> str:
        return f'{int(value):,}'.replace(',', '.')

    def _build_milestone_text(
        self,
        profile: WatchProfile,
        follower_count: int,
        threshold: int,
        mention: str,
        display_name: str,
    ) -> str:
        pretty_threshold = self._format_milestone_number(threshold)
        safe_display_name = html.escape(display_name or profile.label or profile.username or profile.key)
        safe_mention = html.escape(mention or '')

        return (
            f'{self._custom_emoji("bell")} <b>{safe_display_name}</b> '
            f'поздравляем тебя ({safe_mention}) с <b>{pretty_threshold}</b> сабов! '
            'Надеемся, что ты дальше продолжишь радовать нас своим контентом!'
        )

    async def _notify_milestone(
        self,
        bot: Bot,
        profile: WatchProfile,
        follower_count: int,
        threshold: int,
        mention: str,
        display_name: str,
        chat_id: int | None = None,
    ) -> bool:
        target_chat_id = int(chat_id or self.target_chat_id)
        target_thread_id = self.target_thread_id if chat_id is None else 0

        try:
            await bot.send_message(
                chat_id=target_chat_id,
                text=self._build_milestone_text(
                    profile=profile,
                    follower_count=follower_count,
                    threshold=threshold,
                    mention=mention,
                    display_name=display_name,
                ),
                message_thread_id=target_thread_id or None,
            )
            return True
        except Exception as e:
            logger.warning('Failed to send milestone notification (%s): %s', profile.key, e)
            return False

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

    def _build_caption(self, latest: dict, info: dict, profile: WatchProfile) -> str:
        profile_url = html.escape(profile.url, quote=True)
        display_name = (
            profile.label
            or latest.get('channel_name')
            or latest.get('uploader_name')
            or profile.username
            or latest.get('uploader_id')
            or 'Автор'
        )
        safe_display_name = html.escape(str(display_name))
        video_url = html.escape(str(latest.get('video_url') or ''), quote=True)

        raw_description = str(info.get('description') or '').strip()
        has_hatani_hashtag = bool(HATANI_HASHTAG_RE.search(raw_description))

        description = html.escape(raw_description or 'Без описания')
        if len(description) > 300:
            description = description[:297] + '...'

        thanks_line = ''
        if has_hatani_hashtag:
            thanks_line = '<b>Спасибо за ваш контент!</b>\n\n'

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
            f'{thanks_line}'
            f'🎚️ Обычное\n'
            f'{self._custom_emoji("file")} {duration}s | {width}×{height} | {fps}fps | {file_size_mb:.1f}MB\n'
            f'{self._custom_emoji("date")} {upload_date}\n'
            f'{region_line}'
            f'{self._custom_emoji("song")} <i>Original Sound</i>'
        )

    async def _register_bonus_candidate(self, latest: dict, info: dict | None) -> None:
        payload = dict(info or {})
        source_url = str(payload.get('source_url') or latest.get('video_url') or '').strip()
        if not source_url:
            return

        if not str(payload.get('description') or '').strip():
            payload['description'] = str(latest.get('title') or '').strip()
        if not str(payload.get('uploader') or '').strip():
            payload['uploader'] = str(latest.get('uploader_name') or latest.get('channel_name') or '').strip()
        if not str(payload.get('uploader_id') or '').strip():
            payload['uploader_id'] = str(latest.get('uploader_id') or '').strip()

        try:
            await bonus_tracker_service.register_video_if_eligible(payload, source_url=source_url)
        except Exception as e:
            logger.debug('Bonus candidate registration skipped (%s): %s', source_url, e)

    def fetch_latest_video(self, profile: WatchProfile) -> dict:
        try:
            with YoutubeDL(self._ydl_opts()) as ydl:
                info = ydl.extract_info(profile.url, download=False)
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
        uploader_name = str(latest.get('uploader') or profile.username or '').strip()
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
            'formats': latest.get('formats', []),
        }

    def _build_formats_keyboard(self, formats: list, video_id: str) -> InlineKeyboardMarkup:
        buttons = []
        video_formats = [f for f in formats if f.get('vcodec') != 'none']
        
        def sort_key(f):
            res = f.get('height') or 0
            size = f.get('filesize') or 0
            return (res, size)
            
        video_formats.sort(key=sort_key, reverse=True)
        
        seen_labels = set()
        
        for f in video_formats:
            res = f.get('resolution') or f.get('format_id') or 'unknown'
            vcodec = f.get('vcodec', '')
            size = f.get('filesize')
            if size:
                size_mb = f'{size / (1024 * 1024):.1f} MB'
            else:
                size_mb = '?'
            fps = f.get('fps')
            fps_str = f'p{fps}' if fps else ''
            text = f"📥 {res}{fps_str} • {vcodec} • {size_mb}"
            
            if text in seen_labels:
                continue
                
            format_id = f.get('format_id')
            if format_id:
                seen_labels.add(text)
                cb_data = f"pdl:{video_id}:{format_id}"
                if len(cb_data.encode('utf-8')) > 64:
                    continue
                buttons.append([InlineKeyboardButton(text=text, callback_data=cb_data)])
                
            if len(buttons) >= 8:
                break
                
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    async def _notify_owner(
        self,
        bot: Bot,
        latest: dict,
        profile: WatchProfile,
        chat_id: int | None = None,
    ) -> bool:
        target_chat_id = chat_id or self.owner_id
        if not target_chat_id or target_chat_id <= 0:
            logger.warning('Target/Owner ID not set, cannot send private notification.')
            return False

        try:
            display_name = html.escape(profile.label or latest.get('channel_name') or profile.username or 'Автор')
            video_url = html.escape(str(latest.get("video_url") or ""), quote=True)
            
            text = (
                f'{self._custom_emoji("bell")} <a href="{video_url}">{display_name}</a> выложил новое видео\n\n'
                f'Выжимка из тик тока:'
            )
            
            formats = latest.get('formats', [])
            keyboard = self._build_formats_keyboard(formats, latest.get('video_id', ''))
            
            await bot.send_message(
                chat_id=target_chat_id,
                text=text,
                reply_markup=keyboard,
                disable_web_page_preview=True,
                parse_mode='HTML'
            )
            return True
        except Exception as e:
            logger.warning('Failed to send profile watcher notification (%s): %s', profile.key, e)
            return False

    async def send_latest_preview(
        self,
        bot: Bot,
        chat_id: int | None = None,
        profile_key: str | None = None,
    ) -> dict:
        profile = self._resolve_profile(profile_key)
        if not profile:
            return {
                'status': 'error',
                'message': 'Профиль для теста не найден',
            }

        latest = await asyncio.to_thread(self.fetch_latest_video, profile)
        if latest.get('status') != 'success':
            return latest

        notified = await self._notify_owner(bot, latest, profile, chat_id=chat_id)
        if not notified:
            return {
                'status': 'error',
                'message': 'Не удалось отправить тестовое уведомление',
            }

        return {
            'status': 'success',
            'profile': profile.key,
            'video_id': latest.get('video_id', ''),
            'video_url': latest.get('video_url', ''),
        }

    async def send_milestone_preview(
        self,
        bot: Bot,
        profile_key: str | None = None,
        chat_id: int | None = None,
        force: bool = True,
    ) -> dict:
        profile = self._resolve_profile(profile_key)
        if not profile:
            return {
                'status': 'error',
                'message': 'Профиль для milestone-теста не найден',
            }

        rule = self.milestone_rules.get(profile.key)
        if not rule:
            return {
                'status': 'error',
                'message': f'Для профиля {profile.key} нет настроек milestone',
            }

        stats = await asyncio.to_thread(self.fetch_follower_count, profile)
        if stats.get('status') != 'success':
            return stats

        follower_count = int(stats.get('follower_count') or 0)
        threshold = int(rule.get('threshold') or 0)
        if not force and follower_count < threshold:
            return {
                'status': 'not_reached',
                'follower_count': follower_count,
                'threshold': threshold,
            }

        sent = await self._notify_milestone(
            bot=bot,
            profile=profile,
            follower_count=follower_count,
            threshold=threshold,
            mention=str(rule.get('mention') or ''),
            display_name=str(rule.get('display_name') or profile.label or profile.username),
            chat_id=chat_id,
        )
        if not sent:
            return {
                'status': 'error',
                'message': 'Не удалось отправить milestone-уведомление',
            }

        return {
            'status': 'success',
            'profile': profile.key,
            'follower_count': follower_count,
            'threshold': threshold,
        }

    async def run(self, bot: Bot):
        if not self.enabled:
            logger.info('TikTok profile watcher disabled')
            return

        logger.info(
            'TikTok profile watcher enabled: profiles=%s interval=%ss target_chat_id=%s thread_id=%s',
            len(self.profiles),
            self.poll_seconds,
            self.target_chat_id,
            self.target_thread_id,
        )

        while True:
            try:
                for profile in self.profiles:
                    latest = await asyncio.to_thread(self.fetch_latest_video, profile)
                    if latest.get('status') != 'success':
                        logger.warning(
                            'TikTok profile watcher probe failed (%s): %s',
                            profile.key,
                            latest.get('message', 'Unknown error'),
                        )
                        continue

                    latest_video_id = latest['video_id']
                    last_known_video_id = await get_app_state(profile.state_key)

                    if not last_known_video_id:
                        await set_app_state(profile.state_key, latest_video_id)
                        logger.info('TikTok watcher initialized %s at video id: %s', profile.key, latest_video_id)
                        continue

                    if latest_video_id != last_known_video_id:
                        notified = await self._notify_owner(bot, latest, profile)
                        if notified:
                            await set_app_state(profile.state_key, latest_video_id)
                            logger.info('TikTok watcher new video notified %s: %s', profile.key, latest_video_id)

                    milestone_rule = self.milestone_rules.get(profile.key)
                    if milestone_rule:
                        threshold = int(milestone_rule.get('threshold') or 0)
                        if threshold > 0:
                            milestone_state_key = self._milestone_state_key(profile.key, threshold)
                            already_sent = await get_app_state(milestone_state_key)
                            if not already_sent:
                                stats = await asyncio.to_thread(self.fetch_follower_count, profile)
                                if stats.get('status') == 'success':
                                    follower_count = int(stats.get('follower_count') or 0)
                                    if follower_count >= threshold:
                                        milestone_sent = await self._notify_milestone(
                                            bot=bot,
                                            profile=profile,
                                            follower_count=follower_count,
                                            threshold=threshold,
                                            mention=str(milestone_rule.get('mention') or ''),
                                            display_name=str(
                                                milestone_rule.get('display_name')
                                                or profile.label
                                                or profile.username
                                                or profile.key
                                            ),
                                        )
                                        if milestone_sent:
                                            await set_app_state(milestone_state_key, '1')
                                            logger.info(
                                                'Milestone notified %s threshold=%s followers=%s',
                                                profile.key,
                                                threshold,
                                                follower_count,
                                            )
                                else:
                                    logger.warning(
                                        'Milestone stats check failed (%s): %s',
                                        profile.key,
                                        stats.get('message', 'Unknown error'),
                                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception('TikTok profile watcher loop error')

            await asyncio.sleep(self.poll_seconds)
