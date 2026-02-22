import os
import re
import time
import datetime
import asyncio
import html
from aiogram import Router, F, types
from aiogram.filters import CommandStart
from aiogram.types import FSInputFile
from services.downloader import TikTokDownloader
from services.audio import ShazamService
from core.config import MAX_FILE_SIZE_BYTES
from core.database import get_cached_video, save_video_cache, cleanup_old_cache

router = Router()

downloader = TikTokDownloader()
shazam_service = ShazamService()

URL_PATTERN = r'(https?://(?:www\.|vm\.|vt\.)?tiktok\.com/[^\s]+)'

def format_number(num):
    if not num:
        return "0"
    if num >= 1_000_000:
        return f"{num/1_000_000:.1f}M"
    if num >= 1_000:
        return f"{num/1_000:.1f}K"
    return str(num)

def format_date(date_str):
    try:
        if not date_str:
            return datetime.datetime.now().strftime("%d.%m.%Y")
        date_obj = datetime.datetime.strptime(date_str, "%Y%m%d")
        return date_obj.strftime("%d.%m.%Y")
    except:
        return datetime.datetime.now().strftime("%d.%m.%Y")

def build_caption(info: dict, requester: str, times: dict) -> str:
    uploader_name = info.get('uploader', 'Unknown')
    uploader_id = info.get('uploader_id', 'unknown')
    description = info.get('description', '')
    description = html.escape(description)
    if len(description) > 150:
        description = description[:147] + "..."
    
    likes = format_number(info.get('like_count', 0))
    comments = format_number(info.get('comment_count', 0))
    reposts = format_number(info.get('repost_count', 0))
    upload_date = format_date(info.get('upload_date'))
    country_code = info.get('detected_country')
    file_size_mb = info.get('file_size', 0) / (1024 * 1024)
    duration = int(float(info.get('duration', 0)))
    width = info.get('width', 0)
    height = info.get('height', 0)
    fps = info.get('fps', 0)
    song_name = info.get('song_name', 'Original Sound')
    song_name = html.escape(song_name)
    
    location_line = f"📅 {upload_date}"
    if country_code and country_code != "Unknown":
        location_line += f"  🌍 {country_code}"
    
    cached_mark = " ♻️" if info.get('cached') else ""
    fps_text = f"{fps}fps | " if fps and fps > 0 else "N/A | "
    
    return (
        f"👤 <b>{uploader_name}</b> (@{uploader_id}){cached_mark}\n\n"
        f"📝 <blockquote expandable>{description}</blockquote>\n\n"
        f"❤️ {likes}  💬 {comments}  🔄 {reposts}\n\n"
        f"💾 <code>{duration}s | {width}×{height} | {fps_text}{file_size_mb:.1f}MB</code>\n"
        f"{location_line}\n\n"
        f"🎵 <i>{song_name}</i>\n\n"
        f"🔗 via @{requester}\n\n"
        f"⚡ <code>↓{times.get('download', 0):.1f}s</code> | "
        f"<code>↑{times.get('upload', 0):.1f}s</code> | "
        f"<code>🎵{times.get('recognize', 0):.1f}s</code> | "
        f"<b>Σ{times.get('total', 0):.1f}s</b>"
    )

@router.message(CommandStart())
async def cmd_start(message: types.Message):
    await message.answer(
        "📱 <b>TikTok Downloader</b>\n\n"
        "Send me a TikTok link and I'll instantly download the video, "
        "then analyze metadata and audio track.\n\n"
        "<i>Videos are cached for 7 days for instant reuse.</i>",
        parse_mode="HTML"
    )

@router.message(F.text.regexp(URL_PATTERN))
async def handle_tiktok_link(message: types.Message):
    total_start = time.time()
    
    match = re.search(URL_PATTERN, message.text)
    if not match:
        await message.reply("❌ Could not extract a valid URL.")
        return
    
    url = match.group(0)
    requester = message.from_user.username or message.from_user.first_name
    
    status_msg = await message.reply("🔍 <b>Checking cache...</b>", parse_mode="HTML")
    
    cached = await get_cached_video(url)
    
    if cached:
        await status_msg.edit_text("📤 <b>Sending from cache...</b>", parse_mode="HTML")
        
        cached['cached'] = True
        cached['url'] = url
        
        times = {
            'download': 0,
            'upload': 0,
            'recognize': 0,
            'total': 0
        }
        
        caption = build_caption(cached, requester, times)
        
        try:
            await message.answer_video(
                video=cached['file_id'],
                caption=caption,
                parse_mode="HTML",
                width=cached.get('width', 0),
                height=cached.get('height', 0),
                duration=int(cached.get('duration', 0))
            )
            await status_msg.delete()
            await cleanup_old_cache()
            return
        except Exception:
            await status_msg.edit_text("⚠️ <b>Cache invalid, re-downloading...</b>", parse_mode="HTML")
    
    file_path = None
    
    try:
        await status_msg.edit_text("⏳ <b>Downloading from TikTok...</b>", parse_mode="HTML")
        
        info = await downloader.download_video(url)
        
        if info.get('status') == 'error':
            await status_msg.edit_text(f"❌ <b>Download failed:</b>\n<code>{info.get('message')}</code>")
            return
        
        file_path = info['file_path']
        
        if info['file_size'] > MAX_FILE_SIZE_BYTES:
            await status_msg.edit_text(
                f"❌ <b>File too large:</b> {info['file_size']/(1024*1024):.1f} MB\n"
                f"Maximum allowed: {MAX_FILE_SIZE_BYTES/(1024*1024):.0f} MB"
            )
            return
        
        await status_msg.edit_text("📤 <b>Uploading to Telegram...</b>", parse_mode="HTML")
        
        upload_start = time.time()
        
        duration = int(float(info.get('duration', 0)))
        width = info.get('width', 0)
        height = info.get('height', 0)
        description = info.get('description', '')
        if len(description) > 150:
            description = description[:147] + "..."
        
        uploader_name = info.get('uploader', 'Unknown')
        uploader_id = info.get('uploader_id', 'unknown')
        
        temp_caption = (
            f"👤 <b>{uploader_name}</b> (@{uploader_id})\n"
            f"📝 {description[:100]}{'...' if len(description) > 100 else ''}\n\n"
            f"⏳ <i>Analyzing audio and finalizing...</i>"
        )
        
        video_file = FSInputFile(file_path)
        
        sent_message = await message.answer_video(
            video=video_file,
            caption=temp_caption,
            parse_mode="HTML",
            width=width,
            height=height,
            duration=duration
        )
        
        upload_time = time.time() - upload_start
        
        await status_msg.edit_text("🎵 <b>Recognizing audio...</b>", parse_mode="HTML")
        
        recognize_start = time.time()
        song_name = await shazam_service.recognize(file_path)
        recognize_time = time.time() - recognize_start
        
        total_time = time.time() - total_start
        
        info.update({
            'url': url,
            'song_name': song_name,
            'cached': False
        })
        
        times = {
            'download': info.get('download_time', 0),
            'upload': upload_time,
            'recognize': recognize_time,
            'total': total_time
        }
        
        final_caption = build_caption(info, requester, times)
        
        await sent_message.edit_caption(
            caption=final_caption,
            parse_mode="HTML"
        )
        
        if sent_message.video and sent_message.video.file_id:
            info['file_id'] = sent_message.video.file_id
            await save_video_cache(info)
        
        await status_msg.delete()
        await cleanup_old_cache()
        
    except Exception as e:
        await status_msg.edit_text(f"❌ <b>Error:</b> <code>{str(e)}</code>")
    
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except:
                pass
