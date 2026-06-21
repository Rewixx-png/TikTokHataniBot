import asyncio
import html as html_mod
import json
import logging
import os
import re
import shutil
import tempfile
import uuid

import aiohttp

logger = logging.getLogger(__name__)

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)

_INSTAGRAM_COOKIES_CANDIDATES = [
    os.path.join(_PROJECT_ROOT, 'instagram_cookies.txt'),
    '/app/instagram_cookies.txt',
]
_YOUTUBE_COOKIES_CANDIDATES = [
    os.path.join(_PROJECT_ROOT, 'youtube_cookies.txt'),
    '/app/youtube_cookies.txt',
]

_YT_RE = re.compile(
    r'https?://(?:(?:www\.|m\.)?(?:youtube\.com|youtu\.be)|music\.youtube\.com)',
    re.IGNORECASE,
)
_IG_RE = re.compile(r'https?://(?:www\.)?instagram\.com/', re.IGNORECASE)
_PI_RE = re.compile(r'https?://(?:(?:[a-z]+\.)?pinterest\.|pin\.it)', re.IGNORECASE)

_ANSI_ESCAPE_RE = re.compile(r'\x1b\[[0-9;]*m')

_OG_IMAGE_RE = re.compile(
    r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']'
    r'|<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']',
    re.IGNORECASE,
)

_PINTEREST_HEADERS = {
    'User-Agent': (
        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
        'AppleWebKit/537.36 (KHTML, like Gecko) '
        'Chrome/124.0.0.0 Safari/537.36'
    ),
    'Accept-Language': 'en-US,en;q=0.9',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

_VIDEO_EXTS = frozenset({
    '.mp4', '.webm', '.mov', '.mkv', '.avi', '.m4v', '.flv', '.ts', '.3gp',
})
_PHOTO_EXTS = frozenset({
    '.jpg', '.jpeg', '.png', '.webp', '.gif',
})
_AUDIO_EXTS = frozenset({
    '.m4a', '.mp3', '.opus', '.ogg', '.flac', '.aac', '.wav',
})


def _find_cookies(candidates: list[str]) -> str:
    for path in candidates:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path
    return ''


def _find_instagram_cookies() -> str:
    return _find_cookies(_INSTAGRAM_COOKIES_CANDIDATES)


def _find_youtube_cookies() -> str:
    return _find_cookies(_YOUTUBE_COOKIES_CANDIDATES)


def detect_platform(url: str) -> str | None:
    if _YT_RE.search(url):
        return 'youtube'
    if _IG_RE.search(url):
        return 'instagram'
    if _PI_RE.search(url):
        return 'pinterest'
    return None


def _media_type(file_path: str) -> str:
    ext = os.path.splitext(file_path)[1].lower()
    if ext in _VIDEO_EXTS:
        return 'video'
    if ext in _PHOTO_EXTS:
        return 'photo'
    if ext in _AUDIO_EXTS:
        return 'audio'
    return 'document'


async def probe_youtube_formats(url: str) -> list[int]:
    cmd = [
        'yt-dlp', '--no-playlist', '--no-warnings',
        '-j', url,
    ]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return []
        if proc.returncode != 0:
            return []
        info = json.loads(stdout.decode('utf-8', errors='replace'))
        heights: set[int] = set()
        for fmt in info.get('formats', []):
            h = fmt.get('height')
            if h and fmt.get('vcodec', 'none') != 'none':
                heights.add(int(h))
        return sorted(heights, reverse=True)
    except Exception:
        logger.exception('probe_youtube_formats failed for %s', url)
        return []


async def _pinterest_image_fallback(url: str) -> dict:
    tmp_dir = tempfile.mkdtemp(prefix='tgbot_social_')
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(headers=_PINTEREST_HEADERS) as session:
            async with session.get(url, allow_redirects=True, timeout=timeout) as resp:
                page_html = await resp.text(encoding='utf-8', errors='replace')

            m = _OG_IMAGE_RE.search(page_html)
            if not m:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return {'status': 'error', 'message': 'Изображение не найдено на странице Pinterest'}

            image_url = html_mod.unescape(m.group(1) or m.group(2))

            url_path = image_url.split('?')[0]
            ext = os.path.splitext(url_path)[1].lower()
            if ext not in _PHOTO_EXTS:
                ext = '.jpg'

            file_path = os.path.join(tmp_dir, f'{uuid.uuid4().hex}{ext}')

            dl_timeout = aiohttp.ClientTimeout(total=120)
            async with session.get(image_url, timeout=dl_timeout) as img_resp:
                with open(file_path, 'wb') as fh:
                    async for chunk in img_resp.content.iter_chunked(65536):
                        fh.write(chunk)

        return {
            'status': 'success',
            'file_path': file_path,
            'media_type': 'photo',
            'tmp_dir': tmp_dir,
        }
    except Exception as exc:
        logger.exception('Pinterest image fallback failed for %s', url)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {'status': 'error', 'message': f'Не удалось скачать изображение: {exc}'}


async def _exec_yt_dlp(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=600)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, 'timeout'
    error_msg = _ANSI_ESCAPE_RE.sub('', stderr_bytes.decode('utf-8', errors='replace').strip())
    return proc.returncode, error_msg


def _build_yt_cmd(output_template: str, fs: str, audio_only: bool,
                  cookies: str, url: str) -> list[str]:
    cmd: list[str] = [
        'yt-dlp', '--no-playlist', '--no-warnings',
        '-f', fs, '-o', output_template,
    ]
    if not audio_only:
        cmd += ['--merge-output-format', 'mp4', '-S', 'vcodec:av01,vcodec:vp9']
    if cookies:
        cmd += ['--cookies', cookies]
    cmd += ['--concurrent-fragments', '4', url]
    return cmd


async def _collect_result(tmp_dir: str) -> dict:
    files = [f for f in os.listdir(tmp_dir) if not f.endswith(('.part', '.ytdl', '.json'))]
    if not files:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {'status': 'error', 'message': 'yt-dlp не скачал ни одного файла'}
    best = max(files, key=lambda f: os.path.getsize(os.path.join(tmp_dir, f)))
    return {
        'status': 'success',
        'file_path': os.path.join(tmp_dir, best),
        'media_type': _media_type(os.path.join(tmp_dir, best)),
        'tmp_dir': tmp_dir,
    }


async def download_media(url: str, platform: str, format_selector: str | None = None) -> dict:
    tmp_dir = tempfile.mkdtemp(prefix='tgbot_social_')
    output_template = os.path.join(tmp_dir, f'{uuid.uuid4().hex}.%(ext)s')

    try:
        if platform == 'youtube':
            fs = format_selector or 'bestvideo+bestaudio/best'
            audio_only = 'bestvideo' not in fs
            yt_cookies = _find_youtube_cookies()

            cmd = _build_yt_cmd(output_template, fs, audio_only, yt_cookies, url)
            rc, error_msg = await _exec_yt_dlp(cmd)

            if rc == -1:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                return {'status': 'error', 'message': 'Скачивание прервано: превышен лимит 10 минут'}

            if rc != 0:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                if yt_cookies and 'requested format is not available' in error_msg.lower():
                    logger.warning('YouTube cookies triggered geo-restriction, retrying without cookies')
                    tmp_dir2 = tempfile.mkdtemp(prefix='tgbot_social_')
                    out2 = os.path.join(tmp_dir2, f'{uuid.uuid4().hex}.%(ext)s')
                    cmd2 = _build_yt_cmd(out2, fs, audio_only, '', url)
                    rc2, err2 = await _exec_yt_dlp(cmd2)
                    if rc2 == -1:
                        shutil.rmtree(tmp_dir2, ignore_errors=True)
                        return {'status': 'error', 'message': 'Скачивание прервано: превышен лимит 10 минут'}
                    if rc2 != 0:
                        shutil.rmtree(tmp_dir2, ignore_errors=True)
                        lines = [ln for ln in err2.splitlines() if ln.strip()]
                        return {'status': 'error', 'message': lines[-1] if lines else err2}
                    return await _collect_result(tmp_dir2)
                lines = [ln for ln in error_msg.splitlines() if ln.strip()]
                return {'status': 'error', 'message': lines[-1] if lines else error_msg}

            return await _collect_result(tmp_dir)

        cmd: list[str] = [
            'yt-dlp', '--no-playlist', '--no-warnings',
            '-o', output_template,
        ]
        if platform == 'instagram':
            cookies = _find_instagram_cookies()
            if cookies:
                cmd += ['--cookies', cookies]
            else:
                logger.warning('Instagram cookies file not found; attempting unauthenticated download')
        cmd.append(url)

        rc, error_msg = await _exec_yt_dlp(cmd)

        if rc == -1:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return {'status': 'error', 'message': 'Скачивание прервано: превышен лимит 10 минут'}

        if rc != 0:
            lines = [ln for ln in error_msg.splitlines() if ln.strip()]
            short_error = lines[-1] if lines else f'yt-dlp завершился с кодом {rc}'
            shutil.rmtree(tmp_dir, ignore_errors=True)
            if platform == 'pinterest' and 'no video formats found' in error_msg.lower():
                return await _pinterest_image_fallback(url)
            return {'status': 'error', 'message': short_error}

        return await _collect_result(tmp_dir)

    except Exception as exc:
        logger.exception('Unexpected error while downloading %s', url)
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return {'status': 'error', 'message': str(exc)}
