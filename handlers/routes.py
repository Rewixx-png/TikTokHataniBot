import asyncio
import datetime
import html
import logging
import os
import re
import time
import uuid

from aiogram import F, Router, types
from aiogram.filters import Command, CommandStart
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

try:
    from babel import Locale
except Exception:
    Locale = None

from core.config import BOT_OWNER_ID, MAX_FILE_SIZE_BYTES
from core.database import cleanup_old_cache, get_cached_video, get_cached_video_by_file_id, save_video_cache
from services.audio import ShazamService
from services.bonus_tracker import bonus_tracker_service
from services.downloader import TikTokDownloader
from services.musicaldown import MusicalDownService
from services.nim_commentary import NimCommentaryService
from services.profile_watcher import TikTokProfileWatcher
from services.snaptik import SnapTikService

router = Router()

downloader = TikTokDownloader()
shazam_service = ShazamService()
snaptik_service = SnapTikService()
musicaldown_service = MusicalDownService()
nim_commentary_service = NimCommentaryService()
profile_watcher = TikTokProfileWatcher()

URL_PATTERN = r'(https?://(?:www\.|vm\.|vt\.)?tiktok\.com/[^\s]+)'

QUALITY_NORMAL = 'normal'
QUALITY_HIGH = 'high'
QUALITY_ORIGINAL = 'original'

QUALITY_LABELS = {
    QUALITY_NORMAL: 'Обычное',
    QUALITY_HIGH: 'Высокое',
    QUALITY_ORIGINAL: 'Оригинальное',
}

REQUEST_TTL_SECONDS = 10 * 60
pending_requests: dict[str, dict] = {}
logger = logging.getLogger(__name__)
PREMIUM_RENDER_ATTEMPTS = 3
PREMIUM_RETRY_STEP_SECONDS = 0.35
REFRESH_META_CALLBACK = 'meta:update'

RU_LOCALE = Locale.parse('ru') if Locale else None

CUSTOM_EMOJI = {
    'bell': ('6039486778597970865', '🔔'),
    'user': ('5904630315946611415', '👤'),
    'success': ('5774022692642492953', '✅'),
    'video': ('5884252508603289902', '🎥'),
    'info': ('6028435952299413210', 'ℹ️'),
    'exclamation': ('6030563507299160824', '❗️'),
    'likes': ('5116368680279606270', '♥️'),
    'views': ('6037397706505195857', '👁'),
    'comments': ('5886436057091673541', '💬'),
    'reposts': ('6005843436479975944', '🔁'),
    'file': ('5877680341057015789', '📁'),
    'date': ('5967412305338568701', '📅'),
    'region': ('5985479497586053461', '🗺️'),
    'song': ('5282852032263233269', '🎵'),
    'via': ('5877465816030515018', '🔗'),
    'speed': ('5116093437300442328', '⚡'),
    'speed_song': ('5271627010681108586', '🎵'),
}

TG_EMOJI_TAG_RE = re.compile(r'<tg-emoji\s+emoji-id="[^"]+">([^<]*)</tg-emoji>')
USERNAME_RE = re.compile(r'^[A-Za-z0-9_]{5,32}$')
HATANI_HASHTAG_RE = re.compile(r'(?i)(?:^|\s)#hatanisquad\b')


def custom_emoji(name: str) -> str:
    emoji_id, fallback = CUSTOM_EMOJI[name]
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


def strip_custom_emoji_tags(text: str) -> str:
    return TG_EMOJI_TAG_RE.sub(r'\1', text)


def contains_hatani_hashtag(description: str | None) -> bool:
    return bool(HATANI_HASHTAG_RE.search(str(description or '')))


def format_requester_label(requester: str) -> str:
    raw = str(requester or '').strip()
    if not raw:
        return '@owner'

    if raw.startswith('@'):
        raw = raw[1:].strip()

    escaped = html.escape(raw)
    if USERNAME_RE.fullmatch(raw):
        return f'@{escaped}'

    return escaped


def _is_owner_private_message(message: types.Message) -> bool:
    if not message.from_user:
        return False

    if int(BOT_OWNER_ID or 0) <= 0:
        return False

    chat_type = getattr(message.chat.type, 'value', message.chat.type)
    return str(chat_type).lower() == 'private' and message.from_user.id == BOT_OWNER_ID

def _cleanup_pending_requests() -> None:
    now = time.time()
    expired_ids = [
        request_id
        for request_id, data in pending_requests.items()
        if now - data.get('created_at', 0) > REQUEST_TTL_SECONDS
    ]
    for request_id in expired_ids:
        pending_requests.pop(request_id, None)


def _build_cache_key(url: str, quality: str) -> str:
    return f'{quality}|{url}'


def _main_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text='ℹ️ Форматы',
                    callback_data=f'qsel:{request_id}:formats',
                ),
            ],
            [
                InlineKeyboardButton(
                    text='⚡ Высокое',
                    callback_data=f'qsel:{request_id}:{QUALITY_HIGH}',
                ),
                InlineKeyboardButton(
                    text='🎥 Оригинальное',
                    callback_data=f'qsel:{request_id}:{QUALITY_ORIGINAL}',
                ),
            ],
        ]
    )

def _build_user_formats_keyboard(formats: list, request_id: str) -> InlineKeyboardMarkup:
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
            cb_data = f"udl:{request_id}:{format_id}"
            if len(cb_data.encode('utf-8')) > 64:
                continue
            buttons.append([InlineKeyboardButton(text=text, callback_data=cb_data)])
            
        if len(buttons) >= 8:
            break
            
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def _metadata_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text='🔄 Обновить данные TT',
                    callback_data=REFRESH_META_CALLBACK,
                )
            ]
        ]
    )


def _split_cache_key(cache_key: str) -> tuple[str, str]:
    raw = str(cache_key or '')
    if '|' not in raw:
        if raw.startswith('http://') or raw.startswith('https://'):
            return QUALITY_HIGH, raw
        return QUALITY_HIGH, ''

    quality, source_url = raw.split('|', 1)
    if quality not in QUALITY_LABELS:
        quality = QUALITY_HIGH

    return quality, source_url


def _extract_times_from_caption(caption: str | None) -> dict:
    default_times = {
        'download': 0.0,
        'upload': 0.0,
        'recognize': 0.0,
        'total': 0.0,
    }

    if not caption:
        return default_times

    text = strip_custom_emoji_tags(caption)
    text = re.sub(r'<[^>]+>', '', text)

    line_with_total = ''
    for line in text.splitlines():
        if 'Σ' in line and 's' in line:
            line_with_total = line
            break

    if not line_with_total:
        return default_times

    matches = re.findall(r'([0-9]+(?:\.[0-9]+)?)s', line_with_total)
    if len(matches) < 4:
        return default_times

    try:
        return {
            'download': float(matches[0]),
            'upload': float(matches[1]),
            'recognize': float(matches[2]),
            'total': float(matches[3]),
        }
    except (TypeError, ValueError):
        return default_times


def _extract_requester_from_caption(caption: str | None) -> str:
    if not caption:
        return ''

    text = strip_custom_emoji_tags(caption)
    text = re.sub(r'<[^>]+>', '', text)

    for line in text.splitlines():
        match = re.search(r'\bvia\s+(.+)$', line.strip(), flags=re.IGNORECASE)
        if not match:
            continue

        requester = match.group(1).strip()
        if requester:
            return requester

    return ''


def _refresh_info_from_probe(cached_info: dict, probe_data: dict) -> dict:
    info = dict(cached_info)

    if not probe_data or probe_data.get('status') != 'success':
        return info

    for field in ('like_count', 'view_count', 'comment_count', 'repost_count'):
        value = probe_data.get(field)
        if value is None:
            continue

        try:
            numeric_value = int(float(value))
        except (TypeError, ValueError):
            continue

        if numeric_value < 0:
            continue

        info[field] = numeric_value

    return info


def format_number(num):
    if not num:
        return '0'
    if num >= 1_000_000:
        return f'{num/1_000_000:.1f}M'
    if num >= 1_000:
        return f'{num/1_000:.1f}K'
    return str(num)


def format_date(date_str):
    try:
        if not date_str:
            return datetime.datetime.now().strftime('%d.%m.%Y')
        date_obj = datetime.datetime.strptime(str(date_str), '%Y%m%d')
        return date_obj.strftime('%d.%m.%Y')
    except Exception:
        return datetime.datetime.now().strftime('%d.%m.%Y')


def country_flag_emoji(country_code: str) -> str:
    code = (country_code or '').strip().upper()
    if len(code) != 2 or not code.isalpha():
        return ''
    return ''.join(chr(127397 + ord(char)) for char in code)


def country_name_ru(country_code: str) -> str:
    code = (country_code or '').strip().upper()
    if len(code) != 2 or not code.isalpha():
        return country_code

    if RU_LOCALE:
        try:
            localized = RU_LOCALE.territories.get(code)
            if localized:
                return localized
        except Exception:
            pass

    return code


def format_region(country_value) -> str:
    raw = str(country_value or '').strip()
    if raw.lower() in {'', 'unknown', 'none', 'null', 'n/a'}:
        return ''

    code = raw.upper()
    if len(code) == 2 and code.isalpha():
        flag = country_flag_emoji(code)
        name = country_name_ru(code)
        if flag:
            return f'{flag} {name}'
        return name

    return raw


def normalize_duration_seconds(value) -> float:
    try:
        duration = float(value or 0)
    except (TypeError, ValueError):
        return 0.0

    if duration > 1000:
        return duration / 1000
    return duration


def merge_probe_metadata(info: dict, probe_data: dict | None) -> dict:
    if not probe_data or probe_data.get('status') != 'success':
        return info

    def is_missing(value) -> bool:
        if value in (None, ''):
            return True
        if isinstance(value, str):
            return value.strip().lower() in {'unknown', 'none', 'null', 'n/a'}
        if isinstance(value, (int, float)):
            return value == 0
        return False

    fields = (
        'uploader',
        'uploader_id',
        'description',
        'like_count',
        'view_count',
        'comment_count',
        'repost_count',
        'upload_date',
        'detected_country',
        'width',
        'height',
        'fps',
        'duration',
    )

    for field in fields:
        value = info.get(field)
        if is_missing(value):
            probe_value = probe_data.get(field)
            if not is_missing(probe_value):
                info[field] = probe_value

    probe_country = probe_data.get('detected_country')
    if not is_missing(probe_country):
        info['detected_country'] = probe_country

    if not info.get('description') and probe_data.get('title'):
        info['description'] = probe_data['title']

    if is_missing(info.get('uploader')) and not is_missing(info.get('uploader_id')):
        info['uploader'] = str(info.get('uploader_id'))

    return info


def build_caption(info: dict, requester: str, times: dict, is_watcher: bool = False, original_url: str = '') -> str:
    raw_uploader_name = str(info.get('uploader') or '').strip()
    raw_uploader_id = str(info.get('uploader_id') or '').strip()

    if raw_uploader_name.lower() in {'', 'unknown', 'none', 'null', 'n/a'}:
        if raw_uploader_id and raw_uploader_id.lower() not in {'unknown', 'none', 'null', 'n/a'}:
            raw_uploader_name = raw_uploader_id
        else:
            raw_uploader_name = 'Автор'

    if raw_uploader_id.lower() in {'unknown', 'none', 'null', 'n/a'}:
        raw_uploader_id = ''

    uploader_name = html.escape(raw_uploader_name)
    uploader_id = html.escape(raw_uploader_id)

    raw_description = str(info.get('description', '') or '').strip()
    has_hatani_hashtag = contains_hatani_hashtag(raw_description)

    description = html.escape(raw_description)
    if not description:
        description = 'Без описания'
    if len(description) > 150:
        description = description[:147] + '...'

    thanks_line = ''
    if has_hatani_hashtag:
        thanks_line = '<b>Спасибо за ваш контент!</b>\n\n'

    likes = format_number(info.get('like_count', 0))
    views = format_number(info.get('view_count', 0))
    comments = format_number(info.get('comment_count', 0))
    reposts = format_number(info.get('repost_count', 0))
    upload_date = format_date(info.get('upload_date'))
    region_text = format_region(info.get('detected_country'))
    file_size_mb = info.get('file_size', 0) / (1024 * 1024)
    duration = int(normalize_duration_seconds(info.get('duration', 0)))
    width = int(info.get('width', 0) or 0)
    height = int(info.get('height', 0) or 0)
    fps = int(info.get('fps', 0) or 0)
    song_name = html.escape(str(info.get('song_name', 'Original Sound')))
    ai_comment = html.escape(str(info.get('ai_comment', '') or '').strip())
    quality_label = html.escape(info.get('quality_label', QUALITY_LABELS[QUALITY_HIGH]))
    requester_label = format_requester_label(requester)

    location_line = f'{custom_emoji("date")} {upload_date}'
    if region_text:
        location_line += f'\n{custom_emoji("region")} Регион: {html.escape(region_text)}'

    cached_mark = ' ♻️' if info.get('cached') else ''
    fps_text = f'{fps}fps' if fps and fps > 0 else 'N/A'

    uploader_suffix = ''
    if uploader_id and uploader_id != uploader_name:
        uploader_suffix = f' (@{uploader_id})'

    ai_block = ''
    if has_hatani_hashtag and ai_comment:
        if len(ai_comment) > 260:
            ai_comment = ai_comment[:257] + '...'
        ai_block = f'Ai:\n<blockquote>{ai_comment}</blockquote>\n\n'

    video_url = original_url or info.get('source_url') or info.get('webpage_url')
    if not video_url:
        raw_url = str(info.get('url', ''))
        if '|' in raw_url:
            video_url = raw_url.split('|', 1)[1]
        else:
            video_url = raw_url

    if is_watcher:
        region_line = ''
        if region_text:
            region_line = f'{custom_emoji("region")} Регион: {html.escape(region_text)} | '

        return (
            f'{custom_emoji("bell")} <a href="https://www.tiktok.com/@{uploader_id}">{uploader_name}</a> • Via — <a href="{video_url}">CLICK</a>\n\n'
            f'<blockquote>{custom_emoji("info")} {description}</blockquote>\n'
            f'{thanks_line}'
            f'🎚️ {quality_label}\n'
            f'{custom_emoji("file")} {duration}s | {width}×{height} | {fps_text} | {file_size_mb:.1f}MB\n'
            f'{custom_emoji("date")} {upload_date}\n'
            f'{region_line}{custom_emoji("song")} <i>{song_name}</i>'
        )
    else:
        uploader_str = f'{custom_emoji("user")} {uploader_name}{cached_mark}'
        if uploader_id and uploader_id != uploader_name:
            uploader_str += f'\n   •   (@{uploader_id})'

        region_line_full = ''
        if region_text:
            region_line_full = f'   |   Регион: {html.escape(region_text)}'

        return (
            f'{uploader_str}\n\n'
            f'<blockquote>{custom_emoji("exclamation")} {description}</blockquote>\n\n'
            f'{thanks_line}'
            f'{custom_emoji("likes")} {likes}  '
            f'{custom_emoji("views")} {views}  '
            f'{custom_emoji("comments")} {comments}  '
            f'{custom_emoji("reposts")} {reposts}\n\n'
            f'🎚️ <b>{quality_label}</b>\n'
            f'{custom_emoji("file")} {duration}s | {width}×{height} | {fps_text} | {file_size_mb:.1f}MB\n\n'
            f'{custom_emoji("date")} {upload_date}{region_line_full}\n\n'
            f'{custom_emoji("song")} <i>{song_name}</i>\n\n'
            f'{ai_block}'
            f'{custom_emoji("via")} via {requester_label}\n\n'
            f'{custom_emoji("speed")} ↓{times.get("download", 0):.1f}s | '
            f'↑{times.get("upload", 0):.1f}s | '
            f'{custom_emoji("speed_song")} {times.get("recognize", 0):.1f}s | '
            f'<b>Σ{times.get("total", 0):.1f}s</b>'
        )


async def _send_media(
    target: types.Message,
    quality: str,
    media,
    caption: str,
    width: int = 0,
    height: int = 0,
    duration: int = 0,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> types.Message:
    if quality == QUALITY_ORIGINAL:
        return await target.answer_document(
            document=media,
            caption=caption,
            parse_mode='HTML',
            reply_markup=reply_markup,
        )

    return await target.answer_video(
        video=media,
        caption=caption,
        parse_mode='HTML',
        width=width,
        height=height,
        duration=duration,
        reply_markup=reply_markup,
    )


async def _send_media_with_premium_retry(
    target: types.Message,
    quality: str,
    media,
    caption: str,
    width: int = 0,
    height: int = 0,
    duration: int = 0,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> types.Message:
    fallback_caption = strip_custom_emoji_tags(caption)
    last_error = None

    for attempt in range(1, PREMIUM_RENDER_ATTEMPTS + 1):
        try:
            return await _send_media(
                target=target,
                quality=quality,
                media=media,
                caption=caption,
                width=width,
                height=height,
                duration=duration,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            last_error = exc
            if attempt < PREMIUM_RENDER_ATTEMPTS:
                await asyncio.sleep(PREMIUM_RETRY_STEP_SECONDS * attempt)

    logger.warning(
        'Premium caption send failed after %s attempts, using fallback: %s',
        PREMIUM_RENDER_ATTEMPTS,
        last_error,
    )
    return await _send_media(
        target=target,
        quality=quality,
        media=media,
        caption=fallback_caption,
        width=width,
        height=height,
        duration=duration,
        reply_markup=reply_markup,
    )


async def _edit_caption_with_premium_retry(
    message: types.Message,
    caption: str,
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    fallback_caption = strip_custom_emoji_tags(caption)
    last_error = None

    for attempt in range(1, PREMIUM_RENDER_ATTEMPTS + 1):
        try:
            await message.edit_caption(caption=caption, parse_mode='HTML', reply_markup=reply_markup)
            return
        except Exception as exc:
            last_error = exc
            if attempt < PREMIUM_RENDER_ATTEMPTS:
                await asyncio.sleep(PREMIUM_RETRY_STEP_SECONDS * attempt)

    logger.warning(
        'Premium caption edit failed after %s attempts, using fallback: %s',
        PREMIUM_RENDER_ATTEMPTS,
        last_error,
    )
    await message.edit_caption(caption=fallback_caption, parse_mode='HTML', reply_markup=reply_markup)


async def _download_through_external_services(url: str) -> tuple[dict | None, list[str]]:
    providers = (
        ('SnapTik', snaptik_service),
        ('MusicalDown', musicaldown_service),
    )
    errors: list[str] = []

    for provider_name, provider_service in providers:
        result = await provider_service.download_original(url, MAX_FILE_SIZE_BYTES)
        if result.get('status') == 'success':
            result['external_provider'] = provider_name
            return result, errors

        provider_message = result.get('message', 'Unknown error')
        errors.append(f'{provider_name}: {provider_message}')
        logger.warning('%s download failed: %s', provider_name, provider_message)

    return None, errors


async def _process_download(
    target_message: types.Message,
    status_message: types.Message,
    url: str,
    quality: str,
    requester: str,
    probe_data: dict | None = None,
    format_id: str | None = None,
    is_watcher: bool = False,
):
    total_start = time.time()
    
    if format_id:
        cache_key = _build_cache_key(url, f"{quality}_{format_id}")
    else:
        cache_key = _build_cache_key(url, quality)
    file_path = None
    is_tiktok_album = bool((probe_data or {}).get('is_tiktok_album'))

    cached = await get_cached_video(cache_key)
    if cached:
        await status_message.edit_text('📤 <b>Отправляю из кэша...</b>', parse_mode='HTML')

        cached['cached'] = True
        cached['quality_label'] = QUALITY_LABELS.get(quality, QUALITY_LABELS[QUALITY_HIGH])
        cached = merge_probe_metadata(cached, probe_data)

        cached_hashtag = contains_hatani_hashtag(cached.get('description'))
        if not cached_hashtag:
            cached['ai_comment'] = ''

        if not is_watcher and cached_hashtag and nim_commentary_service.enabled and not str(cached.get('ai_comment', '') or '').strip():
            generated_comment = await nim_commentary_service.generate_comment(cached)
            if generated_comment:
                cached['ai_comment'] = generated_comment
                cached['url'] = cache_key
                await save_video_cache(cached)

        times = {
            'download': 0,
            'upload': 0,
            'recognize': 0,
            'total': 0,
        }

        caption = build_caption(cached, requester, times, is_watcher=is_watcher)

        try:
            await _send_media_with_premium_retry(
                target=target_message,
                quality=quality,
                media=cached['file_id'],
                caption=caption,
                width=cached.get('width', 0),
                height=cached.get('height', 0),
                duration=int(cached.get('duration', 0)),
                reply_markup=None if is_watcher else _metadata_keyboard(),
            )
            await status_message.delete()
            await bonus_tracker_service.register_video_if_eligible(
                cached,
                source_url=str(cached.get('source_url') or url),
            )
            await cleanup_old_cache()
            return
        except Exception:
            await status_message.edit_text('⚠️ <b>Кэш невалиден, скачиваю заново...</b>', parse_mode='HTML')

    try:
        if quality in {QUALITY_HIGH, QUALITY_ORIGINAL} and not is_tiktok_album:
            if quality == QUALITY_ORIGINAL:
                await status_message.edit_text('🧬 <b>Ищу оригинальное качество через сторонние сервисы...</b>', parse_mode='HTML')
            else:
                await status_message.edit_text('⚡ <b>Ищу высокое качество через сторонние сервисы...</b>', parse_mode='HTML')

            info, external_errors = await _download_through_external_services(url)

            if info is None:
                await status_message.edit_text(
                    '⏳ <b>Сторонние сервисы недоступны, пробую прямую загрузку через TikTok...</b>',
                    parse_mode='HTML',
                )

                fallback_info = await downloader.download_video(url, quality=quality, format_id=format_id)
                if fallback_info.get('status') == 'error':
                    external_details = '\n'.join(f'- {item}' for item in external_errors) or '- Unknown error'
                    info = {
                        'status': 'error',
                        'message': (
                            f'Сторонние сервисы:\n{external_details}\n'
                            f'Прямая загрузка: {fallback_info.get("message", "Unknown error")}'
                        ),
                    }
                else:
                    info = fallback_info
        else:
            if is_tiktok_album:
                await status_message.edit_text(
                    '🖼️ <b>Обнаружен TikTok альбом, собираю его в видео...</b>',
                    parse_mode='HTML',
                )
            else:
                await status_message.edit_text(
                    '⏳ <b>Скачиваю видео с TikTok в лучшем доступном качестве...</b>',
                    parse_mode='HTML',
                )
            info = await downloader.download_video(url, quality=quality)

        if info.get('status') == 'error':
            await status_message.edit_text(
                f'❌ <b>Скачивание не удалось:</b>\n<code>{html.escape(info.get("message", "Unknown error"))}</code>',
                parse_mode='HTML',
            )
            return

        file_path = info['file_path']
        is_tiktok_album = bool(info.get('is_tiktok_album') or is_tiktok_album)
        info = merge_probe_metadata(info, probe_data)

        if info['file_size'] > MAX_FILE_SIZE_BYTES:
            await status_message.edit_text(
                f'❌ <b>Файл слишком большой:</b> {info["file_size"]/(1024*1024):.1f} MB\n'
                f'Максимум: {MAX_FILE_SIZE_BYTES/(1024*1024):.0f} MB',
                parse_mode='HTML',
            )
            return

        await status_message.edit_text('📤 <b>Загружаю в Telegram...</b>', parse_mode='HTML')

        upload_start = time.time()

        duration = int(normalize_duration_seconds(info.get('duration', 0)))
        width = info.get('width', 0)
        height = info.get('height', 0)
        description = html.escape(info.get('description', '') or '')
        if len(description) > 150:
            description = description[:147] + '...'

        uploader_name = html.escape(str(info.get('uploader', 'Unknown')))
        uploader_id = html.escape(str(info.get('uploader_id', 'unknown')))
        quality_label = QUALITY_LABELS.get(quality, QUALITY_LABELS[QUALITY_HIGH])

        temp_caption = (
            f'👤 <b>{uploader_name}</b> (@{uploader_id})\n'
            f'🎚️ <b>{quality_label}</b>\n'
            f'📝 {description[:100]}{"..." if len(description) > 100 else ""}\n\n'
            f'⏳ <i>Анализирую аудио и финализирую...</i>'
        )

        input_file = FSInputFile(file_path)

        sent_message = await _send_media(
            target=target_message,
            quality=quality,
            media=input_file,
            caption=temp_caption,
            width=width,
            height=height,
            duration=duration,
        )

        upload_time = time.time() - upload_start

        await status_message.edit_text('🎵 <b>Распознаю аудио...</b>', parse_mode='HTML')

        recognize_start = time.time()
        song_name = str(info.get('song_name') or '').strip()
        if is_tiktok_album and song_name and song_name.lower() not in {'unknown', 'original sound'}:
            recognize_time = 0.0
        else:
            song_name = await shazam_service.recognize(file_path)
            recognize_time = time.time() - recognize_start

        ai_comment = ''
        has_hatani_tag = contains_hatani_hashtag(info.get('description'))
        if nim_commentary_service.enabled and has_hatani_tag and not is_watcher:
            await status_message.edit_text('🤖 <b>Генерирую AI-комментарий...</b>', parse_mode='HTML')
            ai_comment = await nim_commentary_service.generate_comment(
                {
                    **info,
                    'song_name': song_name,
                    'quality_label': quality_label,
                }
            )

        total_time = time.time() - total_start

        info.update(
            {
                'song_name': song_name,
                'ai_comment': ai_comment,
                'cached': False,
                'quality_label': quality_label,
            }
        )

        times = {
            'download': info.get('download_time', 0),
            'upload': upload_time,
            'recognize': recognize_time,
            'total': total_time,
        }

        final_caption = build_caption(info, requester, times, is_watcher=is_watcher)

        await _edit_caption_with_premium_retry(sent_message, final_caption, reply_markup=None if is_watcher else _metadata_keyboard())

        file_id = None
        if quality == QUALITY_ORIGINAL:
            if sent_message.document and sent_message.document.file_id:
                file_id = sent_message.document.file_id
        else:
            if sent_message.video and sent_message.video.file_id:
                file_id = sent_message.video.file_id

        if file_id:
            info['file_id'] = file_id
            info['url'] = cache_key
            await save_video_cache(info)

        await bonus_tracker_service.register_video_if_eligible(
            info,
            source_url=str(info.get('source_url') or url),
        )

        await status_message.delete()
        await cleanup_old_cache()

    except Exception as e:
        await status_message.edit_text(
            f'❌ <b>Ошибка:</b> <code>{html.escape(str(e))}</code>',
            parse_mode='HTML',
        )
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception:
                pass


@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        '📱 <b>TikTok Downloader</b>\n\n'
        'Отправь ссылку на TikTok, бот проверит ролик и предложит выбор качества: '
        'Обычное / Высокое / Оригинальное.\n\n'
        '<i>Видео кэшируются на 7 дней для быстрого повтора.</i>',
        parse_mode='HTML',
    )


@router.message(Command('test'))
async def cmd_test_profile_watch_notification(message: types.Message):
    if not _is_owner_private_message(message):
        await message.reply('⛔ Команда доступна только владельцу бота в ЛС.')
        return

    if not profile_watcher.profiles:
        await message.reply('⚠️ Не задан TIKTOK_WATCH_PROFILES или TIKTOK_WATCH_PROFILE_URL в окружении.')
        return

    parts = (message.text or '').split(maxsplit=1)
    requested_profile = parts[1].strip() if len(parts) > 1 else ''

    status = await message.reply('🧪 <b>Готовлю тестовое уведомление...</b>', parse_mode='HTML')
    result = await profile_watcher.send_latest_preview(
        message.bot,
        chat_id=message.chat.id,
        profile_key=requested_profile or None,
    )

    if result.get('status') == 'success':
        profile_name = html.escape(str(result.get('profile') or requested_profile or 'default'))
        await status.edit_text(
            f'✅ <b>Тестовое уведомление отправлено в этот чат:</b> '
            f'<code>{message.chat.id}</code>\n'
            f'Профиль: <code>{profile_name}</code>',
            parse_mode='HTML',
        )
        return

    available_profiles = ', '.join(profile_watcher.profile_keys()[:10])
    await status.edit_text(
        f'❌ <b>Тест не удался:</b>\n'
        f'<code>{html.escape(result.get("message", "Unknown error"))}</code>\n'
        f'<i>Доступные профили: {html.escape(available_profiles)}</i>',
        parse_mode='HTML',
    )


@router.message(Command('bonus'))
async def cmd_bonus(message: types.Message):
    await bonus_tracker_service.process_due_videos(limit=50)

    parts = (message.text or '').split(maxsplit=1)
    participant = parts[1].strip() if len(parts) > 1 else ''
    if not participant and message.from_user and message.from_user.username:
        participant = message.from_user.username

    if not participant:
        await message.reply(
            'ℹ️ Укажи TikTok username: <code>/bonus tpebop.fx</code>\n'
            'Или используй <code>/tb</code>, чтобы посмотреть общий топ.',
            parse_mode='HTML',
        )
        return

    profile = await bonus_tracker_service.bonus_for_participant(participant)
    if (
        profile.get('awarded_videos', 0) == 0
        and profile.get('pending_videos', 0) == 0
        and profile.get('processed_videos', 0) == 0
    ):
        await message.reply(
            f'📭 По участнику <code>{html.escape(participant)}</code> бонусов пока нет.',
            parse_mode='HTML',
        )
        return

    display_name = html.escape(str(profile.get('display_name') or participant))
    participant_key = html.escape(str(profile.get('participant') or participant))
    bonus_points = float(profile.get('bonus_points', 0.0) or 0.0)
    awarded_videos = int(profile.get('awarded_videos', 0) or 0)
    pending_videos = int(profile.get('pending_videos', 0) or 0)

    await message.reply(
        f'🏅 <b>Бонусы участника</b>\n\n'
        f'👤 <b>{display_name}</b> (@{participant_key})\n'
        f'⭐ <b>{bonus_points:.2f}</b> бонусов\n'
        f'✅ Начислено по видео: <b>{awarded_videos}</b>\n'
        f'⏳ На проверке: <b>{pending_videos}</b>',
        parse_mode='HTML',
    )


@router.message(Command('tb'))
async def cmd_bonus_top(message: types.Message):
    await bonus_tracker_service.process_due_videos(limit=50)

    top = await bonus_tracker_service.top_bonus(limit=15)
    if not top:
        await message.reply('📭 Пока нет начисленных бонусов.')
        return

    lines = ['🏆 <b>Топ по бонусам</b>', '']
    for index, item in enumerate(top, start=1):
        display_name = html.escape(str(item.get('display_name') or item.get('participant') or 'unknown'))
        participant_key = html.escape(str(item.get('participant') or 'unknown'))
        points = float(item.get('bonus_points', 0.0) or 0.0)
        videos = int(item.get('awarded_videos', 0) or 0)
        lines.append(f'{index}. <b>{display_name}</b> (@{participant_key}) — <b>{points:.2f}</b> ⭐ ({videos} видео)')

    await message.reply('\n'.join(lines), parse_mode='HTML')


@router.message(F.text.regexp(URL_PATTERN))
async def handle_tiktok_link(message: types.Message):
    match = re.search(URL_PATTERN, message.text or '')
    if not match:
        await message.reply('❌ Не удалось извлечь валидную ссылку.')
        return

    url = match.group(0)

    status_msg = await message.answer('🔍 <b>Проверяю ссылку...</b>', parse_mode='HTML')

    probe = await downloader.probe_video(url)
    if probe.get('status') == 'error':
        snaptik_probe = await snaptik_service.probe_video(url)
        if snaptik_probe.get('status') == 'error':
            musicaldown_probe = await musicaldown_service.probe_video(url)
            if musicaldown_probe.get('status') == 'error':
                external_message = snaptik_probe.get('message') or musicaldown_probe.get('message')
                details = probe.get('message', 'Unknown error')
                if external_message:
                    details = f'{details}\nСторонний сервис: {external_message}'

                await status_msg.edit_text(
                    f'❌ <b>Видео недоступно:</b>\n<code>{html.escape(details)}</code>',
                    parse_mode='HTML',
                )
                return

        probe = {
            'status': 'success',
            'title': 'TikTok video',
            'duration': 0,
            'uploader': 'Unknown',
            'uploader_id': '',
            'description': '',
            'like_count': 0,
            'view_count': 0,
            'comment_count': 0,
            'repost_count': 0,
            'upload_date': None,
            'detected_country': None,
            'width': 0,
            'height': 0,
            'fps': 0,
            'is_tiktok_album': False,
        }

    if probe.get('is_tiktok_album'):
        requester = message.from_user.username or message.from_user.first_name or 'owner'
        await status_msg.edit_text(
            '🖼️ <b>Альбом найден.</b>\n'
            '⏳ Загружаю в обычном качестве и собираю плавное слайд-шоу...',
            parse_mode='HTML',
        )
        await _process_download(
            target_message=message,
            status_message=status_msg,
            url=url,
            quality=QUALITY_NORMAL,
            requester=requester,
            probe_data=probe,
        )
        return

    _cleanup_pending_requests()

    request_id = uuid.uuid4().hex[:12]
    pending_requests[request_id] = {
        'url': url,
        'probe': probe,
        'user_id': message.from_user.id if message.from_user else 0,
        'chat_id': message.chat.id,
        'created_at': time.time(),
    }

    content_label = 'Альбом найден' if probe.get('is_tiktok_album') else 'Видео найдено'

    await status_msg.edit_text(
        f'{custom_emoji("success")} <b>{content_label}</b>\n'
        'Выбери качество:\n\n'
        f'{custom_emoji("info")} <b>Форматы</b> - Сам сможешь посмотреть все допустимые форматы твоего видео в тт\n'
        f'{custom_emoji("speed")} <b>Высокое</b> - Скачаем без водяного знака в высоком качестве\n'
        f'{custom_emoji("video")} <b>Оригинальное</b> - Скачаем через сторонний сервис и отправим файлом с оригинальным качеством',
        reply_markup=_main_keyboard(request_id),
        parse_mode='HTML',
    )


@router.callback_query(F.data == REFRESH_META_CALLBACK)
async def handle_refresh_metadata(
    callback: types.CallbackQuery,
    session: AsyncSession,
):
    message = callback.message
    if not message:
        await callback.answer('Сообщение не найдено', show_alert=True)
        return

    file_id = ''
    if message.video and message.video.file_id:
        file_id = message.video.file_id
    elif message.document and message.document.file_id:
        file_id = message.document.file_id

    if not file_id:
        await callback.answer('Не удалось определить файл сообщения', show_alert=True)
        return

    cached = await get_cached_video_by_file_id(file_id, session=session)
    if not cached:
        await callback.answer('Кэш не найден, отправь ссылку заново', show_alert=True)
        return

    cache_key = str(cached.get('url') or '')
    quality, source_url = _split_cache_key(cache_key)
    if not source_url:
        await callback.answer('Источник видео не найден', show_alert=True)
        return

    await callback.answer('Обновляю данные TikTok...')

    probe = await downloader.probe_video(source_url)
    if probe.get('status') == 'error':
        await callback.answer('Не удалось обновить данные TikTok', show_alert=True)
        return

    refreshed = _refresh_info_from_probe(cached, probe)
    refreshed['cached'] = True
    refreshed['quality_label'] = QUALITY_LABELS.get(quality, QUALITY_LABELS[QUALITY_HIGH])
    refreshed['file_id'] = file_id
    refreshed['url'] = cache_key

    original_requester = _extract_requester_from_caption(message.caption or '')
    requester = original_requester or callback.from_user.username or callback.from_user.first_name or 'owner'
    times = _extract_times_from_caption(message.caption or '')
    new_caption = build_caption(refreshed, requester, times)

    try:
        await _edit_caption_with_premium_retry(message, new_caption, reply_markup=_metadata_keyboard())
    except Exception:
        await callback.answer('Не удалось обновить сообщение', show_alert=True)
        return

    await save_video_cache(refreshed, session=session)
    await callback.answer('Данные TikTok обновлены ✅')


@router.callback_query(F.data.startswith('pdl:'))
async def handle_profile_download_callback(callback: types.CallbackQuery):
    if not callback.message:
        await callback.answer('Сообщение не найдено', show_alert=True)
        return

    parts = (callback.data or '').split(':', 2)
    if len(parts) != 3:
        await callback.answer('Некорректный формат запроса', show_alert=True)
        return

    _, video_id, format_id = parts

    url = f"https://www.tiktok.com/@_/video/{video_id}"

    await callback.answer('Начинаю загрузку...')
    status_message = await callback.message.reply('⏳ <b>Подготавливаю загрузку выбранного формата...</b>', parse_mode='HTML')

    try:
        await callback.message.delete()
    except Exception as e:
        logger.warning(f"Failed to delete callback message: {e}")

    requester = callback.from_user.username or callback.from_user.first_name or 'owner'

    try:
        await _process_download(
            target_message=callback.message,
            status_message=status_message,
            url=url,
            quality='normal',
            requester=requester,
            probe_data=None,
            format_id=format_id,
            is_watcher=True
        )
    except Exception as e:
        logger.exception("Error in handle_profile_download_callback")
        await status_message.edit_text(f"❌ <b>Произошла системная ошибка при загрузке:</b>\n<code>{html.escape(str(e))}</code>", parse_mode='HTML')

@router.callback_query(F.data.startswith('udl:'))
async def handle_user_download_callback(callback: types.CallbackQuery):
    if not callback.message:
        await callback.answer('Сообщение не найдено', show_alert=True)
        return

    parts = (callback.data or '').split(':', 2)
    if len(parts) != 3:
        await callback.answer('Некорректный формат запроса', show_alert=True)
        return

    _, request_id, format_id = parts

    _cleanup_pending_requests()

    request_data = pending_requests.get(request_id)
    if not request_data:
        await callback.answer('Запрос устарел. Отправь ссылку заново.', show_alert=True)
        return

    if (
        request_data.get('user_id') != callback.from_user.id
        or request_data.get('chat_id') != callback.message.chat.id
    ):
        await callback.answer('Этот запрос принадлежит другому пользователю', show_alert=True)
        return

    pending_requests.pop(request_id, None)

    await callback.answer('Начинаю загрузку...')
    
    await callback.message.edit_text(
        '⏳ <b>Подготавливаю загрузку выбранного формата...</b>',
        parse_mode='HTML',
        reply_markup=None,
    )

    requester = callback.from_user.username or callback.from_user.first_name or 'owner'

    try:
        await _process_download(
            target_message=callback.message,
            status_message=callback.message,
            url=request_data['url'],
            quality='normal',
            requester=requester,
            probe_data=request_data.get('probe'),
            format_id=format_id,
            is_watcher=False
        )
    except Exception as e:
        logger.exception("Error in handle_user_download_callback")
        await callback.message.edit_text(f"❌ <b>Произошла системная ошибка при загрузке:</b>\n<code>{html.escape(str(e))}</code>", parse_mode='HTML')

@router.callback_query(F.data.startswith('qsel:'))
async def handle_quality_callback(callback: types.CallbackQuery):
    if not callback.message:
        await callback.answer('Сообщение не найдено', show_alert=True)
        return

    parts = (callback.data or '').split(':', 2)
    if len(parts) != 3:
        await callback.answer('Некорректный формат запроса', show_alert=True)
        return

    _, request_id, quality = parts
    
    if quality == 'formats':
        request_data = pending_requests.get(request_id)
        if not request_data:
            await callback.answer('Запрос устарел. Отправь ссылку заново.', show_alert=True)
            return
            
        if (
            request_data.get('user_id') != callback.from_user.id
            or request_data.get('chat_id') != callback.message.chat.id
        ):
            await callback.answer('Этот запрос принадлежит другому пользователю', show_alert=True)
            return

        probe_data = request_data.get('probe', {})
        formats = probe_data.get('formats', [])
        
        if not formats:
            await callback.answer('Форматы недоступны для этого видео', show_alert=True)
            return
            
        keyboard = _build_user_formats_keyboard(formats, request_id)
        
        await callback.message.edit_text(
            '<b>Допустимые форматы:</b>\n\n'
            '<i>Ниже представлены все доступные варианты загрузки для этого видео.\n'
            'Выберите подходящий, чтобы начать скачивание.</i>',
            parse_mode='HTML',
            reply_markup=keyboard
        )
        return

    if quality not in QUALITY_LABELS:
        await callback.answer('Неизвестное качество', show_alert=True)
        return

    _cleanup_pending_requests()

    request_data = pending_requests.get(request_id)
    if not request_data:
        await callback.answer('Запрос устарел. Отправь ссылку заново.', show_alert=True)
        return

    if (
        request_data.get('user_id') != callback.from_user.id
        or request_data.get('chat_id') != callback.message.chat.id
    ):
        await callback.answer('Этот запрос принадлежит другому пользователю', show_alert=True)
        return

    pending_requests.pop(request_id, None)

    quality_label = QUALITY_LABELS[quality]
    await callback.answer(f'Выбрано: {quality_label}')

    await callback.message.edit_text(
        f'🎚️ <b>Качество:</b> {quality_label}\n\n⏳ Подготавливаю загрузку...',
        parse_mode='HTML',
        reply_markup=None,
    )

    requester = callback.from_user.username or callback.from_user.first_name or 'owner'
    await _process_download(
        target_message=callback.message,
        status_message=callback.message,
        url=request_data['url'],
        quality=quality,
        requester=requester,
        probe_data=request_data.get('probe'),
    )
