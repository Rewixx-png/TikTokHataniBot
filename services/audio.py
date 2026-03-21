import asyncio
import os
import tempfile

from shazamio import Shazam


class ShazamService:
    def __init__(self):
        self.shazam = Shazam()

    async def _extract_audio_sample(self, source_path: str, sample_path: str, timeout: int) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                'ffmpeg',
                '-hide_banner',
                '-loglevel',
                'error',
                '-y',
                '-ss',
                '3',
                '-t',
                '12',
                '-i',
                source_path,
                '-vn',
                '-ac',
                '1',
                '-ar',
                '16000',
                '-c:a',
                'mp3',
                sample_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception:
            return False

        try:
            await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return False

        return process.returncode == 0 and os.path.exists(sample_path) and os.path.getsize(sample_path) > 0

    async def recognize(self, file_path: str, timeout: int = 30) -> str:
        if not file_path or not os.path.exists(file_path):
            return 'Original Sound'

        sample_path = ''
        extract_timeout = max(8, min(20, timeout // 2))
        recognize_timeout = max(8, timeout - extract_timeout)

        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
                sample_path = tmp_file.name

            sample_ready = await self._extract_audio_sample(file_path, sample_path, timeout=extract_timeout)
            if not sample_ready:
                return 'Original Sound'

            out = await asyncio.wait_for(
                self.shazam.recognize(sample_path),
                timeout=recognize_timeout,
            )

            track = out.get('track', {})
            if not track:
                return 'Original Sound'

            title = track.get('title', 'Unknown Track')
            subtitle = track.get('subtitle', 'Unknown Artist')

            return f'{title} — {subtitle}'

        except Exception:
            return 'Original Sound'
        finally:
            if sample_path and os.path.exists(sample_path):
                try:
                    os.remove(sample_path)
                except OSError:
                    pass
