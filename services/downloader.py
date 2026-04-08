import glob
import json
import os
import re
import subprocess
import time
import urllib.request
import uuid

from yt_dlp import YoutubeDL

from core.config import DOWNLOAD_DIR


def parse_fps(fps_str) -> int:
    if not fps_str or str(fps_str) == '0/0':
        return 0
    try:
        val = str(fps_str)
        if '/' in val:
            num, den = map(float, val.split('/'))
            if den > 0:
                return int(round(num / den))
            return 0
        return int(round(float(val)))
    except (ValueError, ZeroDivisionError, TypeError):
        return 0


class TikTokDownloader:
    def __init__(self):
        self.download_path = DOWNLOAD_DIR
        self.cookie_file = os.path.join(os.getcwd(), 'cookies.txt')

    def _build_format_selector(self, quality: str) -> str:
        quality = (quality or 'high').lower()

        if quality == 'normal':
            return (
                'bv*[ext=mp4]+ba[ext=m4a]/'
                'bv*[ext=mp4]+ba/'
                'b[ext=mp4]/'
                'b/'
                'best'
            )

        return 'bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/best'

    def _base_ydl_opts(self) -> dict:
        return {
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'http_headers': {
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/121.0.0.0 Safari/537.36'
                ),
                'Accept': (
                    'text/html,application/xhtml+xml,application/xml;q=0.9,'
                    'image/avif,image/webp,image/apng,*/*;q=0.8'
                ),
                'Accept-Language': 'en-US,en;q=0.9',
            },
        }

    def _apply_cookie_file(self, ydl_opts: dict) -> None:
        if os.path.exists(self.cookie_file) and os.path.getsize(self.cookie_file) > 0:
            ydl_opts['cookiefile'] = self.cookie_file

    def _clean_country_value(self, value) -> str:
        if value is None:
            return ''

        text = str(value).strip()
        if not text:
            return ''
        if text.lower() in {'unknown', 'none', 'null', 'n/a'}:
            return ''

        if len(text) == 2 and text.isalpha():
            return text.upper()

        return text

    def _extract_country_from_webpage(self, page_url: str) -> str:
        if not page_url:
            return ''

        try:
            request = urllib.request.Request(
                page_url,
                headers={
                    'User-Agent': (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/121.0.0.0 Safari/537.36'
                    ),
                    'Accept-Language': 'en-US,en;q=0.9',
                },
            )

            html = urllib.request.urlopen(request, timeout=20).read().decode('utf-8', 'ignore')

            patterns = (
                r'"locationCreated"\s*:\s*"([A-Z]{2})"',
                r'"location_created"\s*:\s*"([A-Z]{2})"',
                r'"createAddress"\s*:\s*"([A-Z]{2})"',
                r'"create_address"\s*:\s*"([A-Z]{2})"',
            )

            for pattern in patterns:
                match = re.search(pattern, html)
                if match:
                    return self._clean_country_value(match.group(1))

            return ''
        except Exception:
            return ''

    def _extract_country(self, info: dict, source_url: str = '') -> str:
        direct_fields = (
            'locationCreated',
            'location_created',
            'createAddress',
            'create_address',
            'location',
            'country',
            'country_code',
        )

        for field in direct_fields:
            value = self._clean_country_value(info.get(field))
            if value:
                return value

        page_url = info.get('webpage_url') or source_url
        return self._extract_country_from_webpage(page_url)

    def _get_local_video_info(self, file_path: str) -> dict:
        try:
            cmd = [
                'ffprobe',
                '-v', 'error',
                '-select_streams', 'v:0',
                '-show_entries', 'stream=width,height,r_frame_rate,avg_frame_rate,duration',
                '-of', 'json',
                file_path,
            ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if not result.stdout:
                return {}

            data = json.loads(result.stdout)
            if 'streams' not in data or not data['streams']:
                return {}

            stream = data['streams'][0]

            fps = parse_fps(stream.get('r_frame_rate'))
            if fps == 0:
                fps = parse_fps(stream.get('avg_frame_rate'))

            return {
                'width': int(stream.get('width', 0)),
                'height': int(stream.get('height', 0)),
                'fps': fps,
                'duration': float(stream.get('duration', 0)),
            }
        except Exception:
            return {}

    def get_local_video_info(self, file_path: str) -> dict:
        return self._get_local_video_info(file_path)

    async def probe_video(self, url: str) -> dict:
        ydl_opts = self._base_ydl_opts()
        ydl_opts.update({
            'skip_download': True,
            'extract_flat': False,
        })

        self._apply_cookie_file(ydl_opts)

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

            country = self._extract_country(info, url)

            return {
                'status': 'success',
                'title': info.get('title') or info.get('description') or 'TikTok video',
                'duration': info.get('duration') or 0,
                'uploader': info.get('uploader', 'Unknown'),
                'uploader_id': info.get('uploader_id', ''),
                'description': info.get('description', ''),
                'like_count': info.get('like_count', 0),
                'view_count': info.get('view_count', 0),
                'comment_count': info.get('comment_count', 0),
                'repost_count': info.get('repost_count', 0),
                'upload_date': info.get('upload_date'),
                'detected_country': country,
                'width': info.get('width') or 0,
                'height': info.get('height') or 0,
                'fps': info.get('fps') or 0,
            }
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
            }

    async def download_video(self, url: str, quality: str = 'high') -> dict:
        filename_id = str(uuid.uuid4())
        start_time = time.time()
        quality = (quality or 'high').lower()

        ydl_opts = self._base_ydl_opts()
        ydl_opts.update({
            'format': self._build_format_selector(quality),
            'outtmpl': f'{self.download_path}/{filename_id}.%(ext)s',
            'format_sort': ['res', 'fps', 'br', 'size'],
            'merge_output_format': 'mp4',
        })

        self._apply_cookie_file(ydl_opts)

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                search_pattern = f'{self.download_path}/{filename_id}.*'
                files = glob.glob(search_pattern)

                if not files:
                    raise FileNotFoundError('Downloaded file not found')

                file_path = files[0]
                download_time = time.time() - start_time
                file_size = os.path.getsize(file_path)

                country = self._extract_country(info, url)

                local_info = self._get_local_video_info(file_path)

                fps_val = local_info.get('fps')
                if not fps_val:
                    fps_val = info.get('fps')
                if not fps_val:
                    for f in info.get('formats', []):
                        if f.get('vcodec') != 'none' and f.get('fps'):
                            fps_val = f.get('fps')
                            break
                if not fps_val:
                    fmt_note = info.get('format_note', '')
                    if '60' in str(fmt_note):
                        fps_val = 60
                    else:
                        fps_val = 30

                try:
                    final_fps = int(round(float(fps_val))) if fps_val else 30
                except (ValueError, TypeError):
                    final_fps = 30

                return {
                    'file_path': file_path,
                    'file_size': file_size,
                    'status': 'success',
                    'download_time': download_time,
                    'uploader': info.get('uploader', 'Unknown'),
                    'uploader_id': info.get('uploader_id', ''),
                    'description': info.get('description', ''),
                    'like_count': info.get('like_count', 0),
                    'view_count': info.get('view_count', 0),
                    'comment_count': info.get('comment_count', 0),
                    'repost_count': info.get('repost_count', 0),
                    'upload_date': info.get('upload_date'),
                    'detected_country': country,
                    'width': local_info.get('width') or info.get('width') or 0,
                    'height': local_info.get('height') or info.get('height') or 0,
                    'fps': final_fps,
                    'duration': local_info.get('duration') or info.get('duration') or 0,
                }

        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
            }
