import asyncio
import glob
import datetime
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.parse
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

    def _replace_photo_with_video_path(self, url: str) -> str:
        if not url:
            return ''

        return re.sub(
            r'(https?://(?:www\.)?tiktok\.com/@[^/?#]+/)photo/(\d+)',
            r'\1video/\2',
            str(url),
            flags=re.IGNORECASE,
        )

    def _resolve_short_tiktok_url(self, url: str) -> str:
        raw_url = str(url or '').strip()
        if not raw_url:
            return ''

        try:
            parsed = urllib.parse.urlparse(raw_url)
            host = (parsed.netloc or '').lower()
            path = parsed.path or ''
            is_short = host in {'vm.tiktok.com', 'vt.tiktok.com'} or path.startswith('/t/')
            if not is_short:
                return raw_url

            request = urllib.request.Request(
                raw_url,
                headers={
                    'User-Agent': (
                        'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                        'AppleWebKit/537.36 (KHTML, like Gecko) '
                        'Chrome/121.0.0.0 Safari/537.36'
                    ),
                    'Accept-Language': 'en-US,en;q=0.9',
                },
            )
            with urllib.request.urlopen(request, timeout=20) as response:
                resolved = response.geturl() or raw_url
                return str(resolved)
        except Exception:
            return raw_url

    def _normalize_tiktok_url(self, url: str) -> tuple[str, bool]:
        raw_url = str(url or '').strip()
        if not raw_url:
            return '', False

        resolved_url = self._resolve_short_tiktok_url(raw_url)
        is_tiktok_album = '/photo/' in resolved_url.lower()
        normalized_url = self._replace_photo_with_video_path(resolved_url)
        return normalized_url, is_tiktok_album

    def _download_binary_file(self, source_url: str, output_path: str, referer: str = '') -> None:
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/121.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'en-US,en;q=0.9',
        }
        if referer:
            headers['Referer'] = referer

        request = urllib.request.Request(source_url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read()

        with open(output_path, 'wb') as output_file:
            output_file.write(payload)

    def _fetch_tikwm_album_data(self, source_url: str) -> dict:
        payload = urllib.parse.urlencode({'url': source_url, 'hd': '1'}).encode('utf-8')
        request = urllib.request.Request(
            'https://www.tikwm.com/api/',
            data=payload,
            method='POST',
            headers={
                'User-Agent': (
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/121.0.0.0 Safari/537.36'
                ),
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Accept': 'application/json, text/plain, */*',
                'Origin': 'https://www.tikwm.com',
                'Referer': 'https://www.tikwm.com/',
            },
        )

        with urllib.request.urlopen(request, timeout=40) as response:
            raw = response.read().decode('utf-8', 'ignore')

        parsed = json.loads(raw)
        if int(parsed.get('code', -1)) != 0:
            raise RuntimeError(parsed.get('msg', 'TikWM API returned error'))

        data = parsed.get('data') or {}
        images = [
            str(item).strip()
            for item in (data.get('images') or [])
            if str(item).strip().startswith(('http://', 'https://'))
        ]
        if not images:
            raise RuntimeError('TikWM did not return album images')

        author = data.get('author') or {}
        music_info = data.get('music_info') or {}

        upload_date = None
        raw_create_time = data.get('create_time') or data.get('createTime')
        try:
            if raw_create_time not in (None, ''):
                dt = datetime.datetime.utcfromtimestamp(int(float(raw_create_time)))
                upload_date = dt.strftime('%Y%m%d')
        except Exception:
            upload_date = None

        return {
            'images': images,
            'music_url': str(data.get('music') or '').strip(),
            'song_name': str(music_info.get('title') or '').strip(),
            'uploader': str(author.get('nickname') or 'Unknown').strip() or 'Unknown',
            'uploader_id': str(author.get('unique_id') or '').strip(),
            'description': str(data.get('title') or data.get('content_desc') or '').strip(),
            'like_count': int(float(data.get('digg_count') or 0)),
            'view_count': int(float(data.get('play_count') or 0)),
            'comment_count': int(float(data.get('comment_count') or 0)),
            'repost_count': int(float(data.get('share_count') or 0)),
            'detected_country': self._clean_country_value(data.get('region')),
            'upload_date': upload_date,
        }

    def _build_album_slideshow_video(
        self,
        image_paths: list[str],
        output_path: str,
        music_path: str = '',
        width: int = 1080,
        height: int = 1920,
        fps: int = 60,
        frame_hold_seconds: float = 2.0,
        transition_seconds: float = 0.75,
        transition_name: str = 'smoothleft',
    ) -> float:
        if not image_paths:
            raise RuntimeError('No images for slideshow generation')

        if len(image_paths) == 1:
            total_duration = 3.0
        else:
            total_duration = (
                len(image_paths) * frame_hold_seconds
                - (len(image_paths) - 1) * transition_seconds
            )

        command = ['ffmpeg', '-y', '-hide_banner', '-loglevel', 'error']
        for image_path in image_paths:
            command.extend(['-loop', '1', '-t', f'{frame_hold_seconds:.3f}', '-i', image_path])

        if music_path:
            command.extend(['-i', music_path])

        filter_parts: list[str] = []
        for idx in range(len(image_paths)):
            filter_parts.append(
                f'[{idx}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,'
                f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,format=yuv420p[v{idx}]'
            )

        if len(image_paths) == 1:
            current_label = 'v0'
        else:
            current_label = 'v0'
            for idx in range(1, len(image_paths)):
                next_label = f'v{idx}'
                output_label = f'x{idx}'
                offset = idx * (frame_hold_seconds - transition_seconds)
                filter_parts.append(
                    f'[{current_label}][{next_label}]'
                    f'xfade=transition={transition_name}:duration={transition_seconds:.3f}:offset={offset:.3f}'
                    f'[{output_label}]'
                )
                current_label = output_label

        filter_parts.append(f'[{current_label}]fps={fps},format=yuv420p[vout]')

        command.extend(['-filter_complex', ';'.join(filter_parts), '-map', '[vout]'])
        if music_path:
            audio_input_index = len(image_paths)
            command.extend(['-map', f'{audio_input_index}:a:0', '-c:a', 'aac', '-b:a', '192k'])
        else:
            command.extend(['-an'])

        command.extend(
            [
                '-t',
                f'{total_duration:.3f}',
                '-c:v',
                'libx264',
                '-preset',
                'veryfast',
                '-crf',
                '22',
                '-movflags',
                '+faststart',
                '-pix_fmt',
                'yuv420p',
                output_path,
            ]
        )

        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0:
            stderr_text = (result.stderr or '').lower()
            if transition_name != 'slideleft' and 'no such transition' in stderr_text:
                return self._build_album_slideshow_video(
                    image_paths=image_paths,
                    output_path=output_path,
                    music_path=music_path,
                    width=width,
                    height=height,
                    fps=fps,
                    frame_hold_seconds=frame_hold_seconds,
                    transition_seconds=transition_seconds,
                    transition_name='slideleft',
                )
            raise RuntimeError(result.stderr.strip() or 'ffmpeg slideshow render failed')

        return total_duration

    def _download_album_as_video(
        self,
        original_url: str,
        source_url: str,
        filename_id: str,
        start_time: float,
    ) -> dict:
        album_data = self._fetch_tikwm_album_data(original_url)
        image_urls = album_data.get('images') or []
        if not image_urls:
            raise RuntimeError('Album contains no images')

        output_path = os.path.join(self.download_path, f'{filename_id}.mp4')
        with tempfile.TemporaryDirectory(prefix='tt_album_') as temp_dir:
            image_paths: list[str] = []
            for index, image_url in enumerate(image_urls, start=1):
                image_path = os.path.join(temp_dir, f'image_{index:03d}.jpg')
                self._download_binary_file(image_url, image_path, referer=source_url)
                image_paths.append(image_path)

            music_path = ''
            music_url = str(album_data.get('music_url') or '').strip()
            if music_url.startswith(('http://', 'https://')):
                try:
                    candidate_music_path = os.path.join(temp_dir, 'music.mp3')
                    self._download_binary_file(music_url, candidate_music_path, referer=source_url)
                    if os.path.getsize(candidate_music_path) > 0:
                        music_path = candidate_music_path
                except Exception:
                    music_path = ''

            final_duration = self._build_album_slideshow_video(
                image_paths=image_paths,
                output_path=output_path,
                music_path=music_path,
            )

        file_size = os.path.getsize(output_path)
        download_time = time.time() - start_time

        return {
            'file_path': output_path,
            'file_size': file_size,
            'status': 'success',
            'download_time': download_time,
            'uploader': album_data.get('uploader', 'Unknown'),
            'uploader_id': album_data.get('uploader_id', ''),
            'description': album_data.get('description', ''),
            'like_count': album_data.get('like_count', 0),
            'view_count': album_data.get('view_count', 0),
            'comment_count': album_data.get('comment_count', 0),
            'repost_count': album_data.get('repost_count', 0),
            'upload_date': album_data.get('upload_date'),
            'detected_country': album_data.get('detected_country', ''),
            'song_name': album_data.get('song_name') or 'Original Sound',
            'width': 1080,
            'height': 1920,
            'fps': 60,
            'duration': final_duration,
            'source_url': source_url,
            'is_tiktok_album': True,
        }

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

    def _probe_video_sync(self, url: str) -> dict:
        source_url, is_tiktok_album = self._normalize_tiktok_url(url)
        if not source_url:
            return {
                'status': 'error',
                'message': 'Empty URL',
            }

        if is_tiktok_album:
            try:
                album_data = self._fetch_tikwm_album_data(url)
                return {
                    'status': 'success',
                    'title': album_data.get('description') or 'TikTok album',
                    'duration': max(1, len(album_data.get('images') or []) * 2),
                    'uploader': album_data.get('uploader', 'Unknown'),
                    'uploader_id': album_data.get('uploader_id', ''),
                    'description': album_data.get('description', ''),
                    'like_count': album_data.get('like_count', 0),
                    'view_count': album_data.get('view_count', 0),
                    'comment_count': album_data.get('comment_count', 0),
                    'repost_count': album_data.get('repost_count', 0),
                    'upload_date': album_data.get('upload_date'),
                    'detected_country': album_data.get('detected_country', ''),
                    'width': 1080,
                    'height': 1920,
                    'fps': 60,
                    'source_url': source_url,
                    'is_tiktok_album': True,
                    'formats': [],
                }
            except Exception:
                pass

        ydl_opts = self._base_ydl_opts()
        ydl_opts.update({
            'skip_download': True,
            'extract_flat': False,
        })

        self._apply_cookie_file(ydl_opts)

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(source_url, download=False)

            country = self._extract_country(info, source_url)

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
                'source_url': source_url,
                'is_tiktok_album': is_tiktok_album,
                'formats': info.get('formats', []),
            }
        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
            }

    async def probe_video(self, url: str) -> dict:
        return await asyncio.to_thread(self._probe_video_sync, url)

    async def download_video(self, url: str, quality: str = 'high', format_id: str | None = None) -> dict:
        return await asyncio.to_thread(self._download_video_sync, url, quality, format_id)

    def _download_video_sync(self, url: str, quality: str = 'high', format_id: str | None = None) -> dict:
        source_url, is_tiktok_album = self._normalize_tiktok_url(url)
        if not source_url:
            return {
                'status': 'error',
                'message': 'Empty URL',
            }

        filename_id = str(uuid.uuid4())
        start_time = time.time()
        quality = (quality or 'high').lower()

        if is_tiktok_album:
            try:
                return self._download_album_as_video(
                    original_url=url,
                    source_url=source_url,
                    filename_id=filename_id,
                    start_time=start_time,
                )
            except Exception as e:
                return {
                    'status': 'error',
                    'message': f'Album render failed: {e}',
                    'is_tiktok_album': True,
                    'source_url': source_url,
                }

        format_selector = format_id if format_id else self._build_format_selector(quality)

        ydl_opts = self._base_ydl_opts()
        ydl_opts.update({
            'format': format_selector,
            'outtmpl': f'{self.download_path}/{filename_id}.%(ext)s',
            'format_sort': ['res', 'fps', 'br', 'size'],
            'merge_output_format': 'mp4',
        })

        self._apply_cookie_file(ydl_opts)

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(source_url, download=True)

                search_pattern = f'{self.download_path}/{filename_id}.*'
                files = glob.glob(search_pattern)

                if not files:
                    raise FileNotFoundError('Downloaded file not found')

                file_path = files[0]
                download_time = time.time() - start_time
                file_size = os.path.getsize(file_path)

                country = self._extract_country(info, source_url)

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
                    'source_url': source_url,
                    'is_tiktok_album': is_tiktok_album,
                }

        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
            }
