import os
import uuid
import glob
import subprocess
import json
import time
from yt_dlp import YoutubeDL
from core.config import DOWNLOAD_DIR

def parse_fps(fps_str: str) -> int:
    if not fps_str or fps_str == '0/0':
        return 0
    try:
        if '/' in fps_str:
            num, den = map(int, fps_str.split('/'))
            if den > 0:
                return round(num / den)
            return 0
        return round(float(fps_str))
    except (ValueError, ZeroDivisionError, TypeError):
        return 0

class TikTokDownloader:
    def __init__(self):
        self.download_path = DOWNLOAD_DIR
        self.cookie_file = os.path.join(os.getcwd(), 'cookies.txt')

    def _get_local_video_info(self, file_path: str) -> dict:
        try:
            cmd = [
                'ffprobe', 
                '-v', 'error', 
                '-select_streams', 'v:0', 
                '-show_entries', 'stream=width,height,r_frame_rate,avg_frame_rate,duration', 
                '-of', 'json', 
                file_path
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
                'duration': float(stream.get('duration', 0))
            }
        except Exception:
            return {}

    async def download_video(self, url: str) -> dict:
        filename_id = str(uuid.uuid4())
        start_time = time.time()

        ydl_opts = {
            'format': 'best',
            'outtmpl': f'{self.download_path}/{filename_id}.%(ext)s',
            'quiet': True,
            'no_warnings': True,
            'nocheckcertificate': True,
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
            },
            'postprocessors': [{
                'key': 'FFmpegVideoConvertor',
                'preferedformat': 'mp4',
            }],
        }

        if os.path.exists(self.cookie_file) and os.path.getsize(self.cookie_file) > 0:
            ydl_opts['cookiefile'] = self.cookie_file

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)

                search_pattern = f"{self.download_path}/{filename_id}.*"
                files = glob.glob(search_pattern)

                if not files:
                    raise FileNotFoundError("Downloaded file not found")

                file_path = files[0]
                download_time = time.time() - start_time
                file_size = os.path.getsize(file_path)

                country = (
                    info.get('location') or 
                    info.get('region') or 
                    info.get('compat_region') or 
                    info.get('geo_bypass_country')
                )

                local_info = self._get_local_video_info(file_path)

                result = {
                    'file_path': file_path,
                    'file_size': file_size,
                    'status': 'success',
                    'download_time': download_time,
                    'uploader': info.get('uploader', 'Unknown'),
                    'uploader_id': info.get('uploader_id', ''),
                    'description': info.get('description', ''),
                    'like_count': info.get('like_count', 0),
                    'comment_count': info.get('comment_count', 0),
                    'repost_count': info.get('repost_count', 0),
                    'upload_date': info.get('upload_date'),
                    'detected_country': country,
                    'width': local_info.get('width') or info.get('width') or 0,
                    'height': local_info.get('height') or info.get('height') or 0,
                    'fps': local_info.get('fps') or info.get('fps') or 0,
                    'duration': local_info.get('duration') or info.get('duration') or 0,
                }

                return result

        except Exception as e:
            return {
                'status': 'error',
                'message': str(e)
            }
