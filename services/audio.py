import asyncio
import logging
import os
import tempfile

from shazamio import Shazam


logger = logging.getLogger(__name__)


class ShazamService:
    def __init__(self):
        self.shazam = Shazam()

    async def _probe_duration(self, source_path: str, timeout: int) -> float:
        try:
            process = await asyncio.create_subprocess_exec(
                'ffprobe',
                '-v',
                'error',
                '-show_entries',
                'format=duration',
                '-of',
                'default=noprint_wrappers=1:nokey=1',
                source_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
        except Exception:
            return 0.0

        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return 0.0

        if process.returncode != 0:
            return 0.0

        try:
            return max(0.0, float((stdout or b'').decode('utf-8', errors='ignore').strip() or 0.0))
        except (TypeError, ValueError):
            return 0.0

    def _build_sample_starts(self, duration: float) -> list[float]:
        if duration <= 0:
            return [0.8, 4.2, 8.5]

        latest_start = max(0.0, duration - 9.5)
        candidates = [
            0.8,
            min(3.0, latest_start),
            duration * 0.28,
            duration * 0.52,
            duration * 0.74,
        ]

        starts = []
        seen = set()
        for value in candidates:
            clamped = max(0.0, min(latest_start, float(value)))
            normalized = round(clamped, 2)
            if normalized in seen:
                continue
            seen.add(normalized)
            starts.append(normalized)

        return starts or [0.0]

    async def _extract_audio_sample(
        self,
        source_path: str,
        sample_path: str,
        sample_start: float,
        sample_length: float,
        timeout: int,
    ) -> bool:
        try:
            process = await asyncio.create_subprocess_exec(
                'ffmpeg',
                '-hide_banner',
                '-loglevel',
                'error',
                '-y',
                '-ss',
                f'{max(0.0, sample_start):.2f}',
                '-t',
                f'{max(2.0, sample_length):.2f}',
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

    async def _recognize_sample(self, sample_path: str, timeout: int) -> str:
        try:
            recognize_fn = getattr(self.shazam.recognize_song, '__wrapped__', None)
            if callable(recognize_fn):
                task = recognize_fn(self.shazam, sample_path)
            else:
                task = self.shazam.recognize_song(sample_path)

            out = await asyncio.wait_for(
                task,
                timeout=timeout,
            )
        except Exception:
            return ''

        track = out.get('track', {}) if isinstance(out, dict) else {}
        if not track:
            return ''

        title = str(track.get('title', 'Unknown Track')).strip()
        subtitle = str(track.get('subtitle', 'Unknown Artist')).strip()
        if not title:
            return ''

        if subtitle:
            return f'{title} — {subtitle}'
        return title

    async def recognize(self, file_path: str, timeout: int = 30) -> str:
        if not file_path or not os.path.exists(file_path):
            return 'Original Sound'

        extract_timeout = max(5, min(12, timeout // 3))
        recognize_timeout = max(5, min(14, timeout // 2))
        sample_length = 10.0

        deadline = asyncio.get_running_loop().time() + max(8, timeout)
        duration = await self._probe_duration(file_path, timeout=min(6, extract_timeout))
        sample_starts = self._build_sample_starts(duration)

        for sample_start in sample_starts[:5]:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining < 3:
                break

            sample_path = ''
            current_extract_timeout = int(max(3, min(extract_timeout, remaining - 1)))
            current_recognize_timeout = int(max(3, min(recognize_timeout, remaining - 0.5)))

            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_file:
                    sample_path = tmp_file.name

                sample_ready = await self._extract_audio_sample(
                    file_path,
                    sample_path,
                    sample_start=sample_start,
                    sample_length=sample_length,
                    timeout=current_extract_timeout,
                )
                if not sample_ready:
                    continue

                recognized = await self._recognize_sample(sample_path, timeout=current_recognize_timeout)
                if recognized:
                    return recognized

            except Exception:
                continue
            finally:
                if sample_path and os.path.exists(sample_path):
                    try:
                        os.remove(sample_path)
                    except OSError:
                        pass

        logger.info('Shazam did not identify track for file: %s', os.path.basename(file_path))
        return 'Original Sound'
