import datetime
import html
import os
import re
import time
import uuid

from aiogram import F, Router, types
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from core.config import MAX_FILE_SIZE_BYTES
from core.database import cleanup_old_cache, get_cached_video, save_video_cache
from services.audio import ShazamService
from services.downloader import TikTokDownloader
from services.snaptik import SnapTikService

router = Router()

downloader = TikTokDownloader()
shazam_service = ShazamService()
snaptik_service = SnapTikService()

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


def _quality_keyboard(request_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text='📉 Обычное',
                    callback_data=f'qsel:{request_id}:{QUALITY_NORMAL}',
                ),
                InlineKeyboardButton(
                    text='⚡ Высокое',
                    callback_data=f'qsel:{request_id}:{QUALITY_HIGH}',
                ),
            ],
            [
                InlineKeyboardButton(
                    text='🧬 Оригинальное',
                    callback_data=f'qsel:{request_id}:{QUALITY_ORIGINAL}',
                ),
            ],
        ]
    )


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

    if not info.get('description') and probe_data.get('title'):
        info['description'] = probe_data['title']

    if is_missing(info.get('uploader')) and not is_missing(info.get('uploader_id')):
        info['uploader'] = str(info.get('uploader_id'))

    return info


def build_caption(info: dict, requester: str, times: dict) -> str:
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

    description = html.escape(info.get('description', '') or '')
    if len(description) > 150:
        description = description[:147] + '...'

    likes = format_number(info.get('like_count', 0))
    comments = format_number(info.get('comment_count', 0))
    reposts = format_number(info.get('repost_count', 0))
    upload_date = format_date(info.get('upload_date'))
    country_code = info.get('detected_country')
    file_size_mb = info.get('file_size', 0) / (1024 * 1024)
    duration = int(normalize_duration_seconds(info.get('duration', 0)))
    width = info.get('width', 0)
    height = info.get('height', 0)
    fps = info.get('fps', 0)
    song_name = html.escape(str(info.get('song_name', 'Original Sound')))
    quality_label = html.escape(info.get('quality_label', QUALITY_LABELS[QUALITY_HIGH]))

    location_line = f'📅 {upload_date}'
    if country_code and country_code != 'Unknown':
        location_line += f'  🌍 {country_code}'

    cached_mark = ' ♻️' if info.get('cached') else ''
    fps_text = f'{fps}fps | ' if fps and fps > 0 else 'N/A | '

    uploader_suffix = ''
    if uploader_id and uploader_id != uploader_name:
        uploader_suffix = f' (@{uploader_id})'

    return (
        f'👤 <b>{uploader_name}</b>{uploader_suffix}{cached_mark}\n\n'
        f'📝 <blockquote expandable>{description}</blockquote>\n\n'
        f'❤️ {likes}  💬 {comments}  🔄 {reposts}\n\n'
        f'🎚️ <b>{quality_label}</b>\n'
        f'💾 <code>{duration}s | {width}×{height} | {fps_text}{file_size_mb:.1f}MB</code>\n'
        f'{location_line}\n\n'
        f'🎵 <i>{song_name}</i>\n\n'
        f'🔗 via @{requester}\n\n'
        f'⚡ <code>↓{times.get("download", 0):.1f}s</code> | '
        f'<code>↑{times.get("upload", 0):.1f}s</code> | '
        f'<code>🎵{times.get("recognize", 0):.1f}s</code> | '
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
) -> types.Message:
    if quality == QUALITY_ORIGINAL:
        return await target.answer_document(
            document=media,
            caption=caption,
            parse_mode='HTML',
        )

    return await target.answer_video(
        video=media,
        caption=caption,
        parse_mode='HTML',
        width=width,
        height=height,
        duration=duration,
    )


async def _process_download(
    target_message: types.Message,
    status_message: types.Message,
    url: str,
    quality: str,
    requester: str,
    probe_data: dict | None = None,
):
    total_start = time.time()
    cache_key = _build_cache_key(url, quality)
    file_path = None

    cached = await get_cached_video(cache_key)
    if cached:
        await status_message.edit_text('📤 <b>Отправляю из кэша...</b>', parse_mode='HTML')

        cached['cached'] = True
        cached['quality_label'] = QUALITY_LABELS.get(quality, QUALITY_LABELS[QUALITY_HIGH])

        times = {
            'download': 0,
            'upload': 0,
            'recognize': 0,
            'total': 0,
        }

        caption = build_caption(cached, requester, times)

        try:
            await _send_media(
                target=target_message,
                quality=quality,
                media=cached['file_id'],
                caption=caption,
                width=cached.get('width', 0),
                height=cached.get('height', 0),
                duration=int(cached.get('duration', 0)),
            )
            await status_message.delete()
            await cleanup_old_cache()
            return
        except Exception:
            await status_message.edit_text('⚠️ <b>Кэш невалиден, скачиваю заново...</b>', parse_mode='HTML')

    try:
        if quality in {QUALITY_HIGH, QUALITY_ORIGINAL}:
            if quality == QUALITY_ORIGINAL:
                await status_message.edit_text('🧬 <b>Ищу оригинальное качество через сторонний сервис...</b>', parse_mode='HTML')
            else:
                await status_message.edit_text('⚡ <b>Ищу высокое качество через сторонний сервис...</b>', parse_mode='HTML')
            info = await snaptik_service.download_original(url, MAX_FILE_SIZE_BYTES)
        else:
            await status_message.edit_text('⏳ <b>Скачиваю видео с TikTok...</b>', parse_mode='HTML')
            info = await downloader.download_video(url, quality=quality)

        if info.get('status') == 'error':
            await status_message.edit_text(
                f'❌ <b>Скачивание не удалось:</b>\n<code>{html.escape(info.get("message", "Unknown error"))}</code>',
                parse_mode='HTML',
            )
            return

        file_path = info['file_path']
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
        song_name = await shazam_service.recognize(file_path)
        recognize_time = time.time() - recognize_start

        total_time = time.time() - total_start

        info.update(
            {
                'song_name': song_name,
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

        final_caption = build_caption(info, requester, times)

        await sent_message.edit_caption(caption=final_caption, parse_mode='HTML')

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


@router.message(F.text.regexp(URL_PATTERN))
async def handle_tiktok_link(message: types.Message):
    match = re.search(URL_PATTERN, message.text or '')
    if not match:
        await message.reply('❌ Не удалось извлечь валидную ссылку.')
        return

    url = match.group(0)

    status_msg = await message.reply('🔍 <b>Проверяю ссылку...</b>', parse_mode='HTML')

    probe = await downloader.probe_video(url)
    if probe.get('status') == 'error':
        snaptik_probe = await snaptik_service.probe_video(url)
        if snaptik_probe.get('status') == 'error':
            await status_msg.edit_text(
                f'❌ <b>Видео недоступно:</b>\n<code>{html.escape(probe.get("message", "Unknown error"))}</code>',
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
            'comment_count': 0,
            'repost_count': 0,
            'upload_date': None,
            'detected_country': None,
            'width': 0,
            'height': 0,
            'fps': 0,
        }

    _cleanup_pending_requests()

    request_id = uuid.uuid4().hex[:12]
    pending_requests[request_id] = {
        'url': url,
        'probe': probe,
        'user_id': message.from_user.id if message.from_user else 0,
        'chat_id': message.chat.id,
        'created_at': time.time(),
    }

    await status_msg.edit_text(
        '✅ <b>Видео найдено</b>\n'
        'Выбери качество:\n\n'
        '📉 <b>Обычное</b> - Скачаем по обычному через yt-dlp\n'
        '⚡ <b>Высокое</b> - Скачаем через сторонний сервис с максимальным качеством\n'
        '🧬 <b>Оригинальное</b> - Скачаем через сторонний сервис и отправим файлом с оригинальным качеством',
        reply_markup=_quality_keyboard(request_id),
        parse_mode='HTML',
    )


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
