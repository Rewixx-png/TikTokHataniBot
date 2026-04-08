import asyncio
import base64
import html
import json
import logging
import os
import re
import tempfile
import zlib

import aiohttp

from core.config import (
    NIM_API_KEY,
    NIM_BASE_URL,
    NIM_COMMENTARY_MODE,
    NIM_ENABLED,
    NIM_MAX_FRAMES,
    NIM_MAX_COMMENT_CHARS,
    NIM_MODEL,
    NIM_TIMEOUT_SECONDS,
)


logger = logging.getLogger(__name__)


class NimCommentaryService:
    def __init__(self):
        self.enabled = bool(NIM_ENABLED and NIM_API_KEY and NIM_MODEL and NIM_BASE_URL)
        self.api_key = NIM_API_KEY
        self.base_url = NIM_BASE_URL
        self.timeout_seconds = float(NIM_TIMEOUT_SECONDS)
        self.max_chars = int(NIM_MAX_COMMENT_CHARS)
        self.max_frames = int(NIM_MAX_FRAMES)
        self.mode = NIM_COMMENTARY_MODE if NIM_COMMENTARY_MODE in {'neutral', 'critical'} else 'neutral'
        
        if self.mode == 'critical':
            self.model = 'meta/llama-3.2-90b-vision-instruct'
        else:
            self.model = NIM_MODEL

    def _format_number(self, value) -> str:
        try:
            num = float(value or 0)
        except (TypeError, ValueError):
            return '0'

        if num >= 1_000_000:
            return f'{num/1_000_000:.1f}M'
        if num >= 1_000:
            return f'{num/1_000:.1f}K'
        return str(int(num))

    def _sanitize_text(self, value: str, max_length: int) -> str:
        text = str(value or '').strip()
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'<[^>]+>', ' ', text)
        text = html.unescape(text)
        text = text.strip().strip('"').strip("'")
        if len(text) > max_length:
            text = text[: max_length - 1].rstrip() + '…'
        return text

    async def _extract_frames(self, file_path: str, duration: float) -> list[str]:
        if not file_path or not os.path.exists(file_path) or duration <= 0:
            return []

        base64_frames = []

        with tempfile.TemporaryDirectory() as tmp_dir:
            try:
                target_frames = self.max_frames
                fps = min(6.0, max(0.45, target_frames / max(duration, 1.0)))
                out_pattern = os.path.join(tmp_dir, 'frame_%04d.jpg')

                process = await asyncio.create_subprocess_exec(
                    'ffmpeg',
                    '-hide_banner',
                    '-loglevel', 'error',
                    '-y',
                    '-i', file_path,
                    '-vf', f'fps={fps},scale=384:-1',
                    '-q:v', '2',
                    out_pattern,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(process.communicate(), timeout=25.0)

                if process.returncode != 0:
                    return []

                frames = sorted([os.path.join(tmp_dir, f) for f in os.listdir(tmp_dir) if f.endswith('.jpg')])

                for f in frames[:target_frames]:
                    with open(f, 'rb') as img_file:
                        b64_data = base64.b64encode(img_file.read()).decode('utf-8')
                        base64_frames.append(b64_data)
            except Exception:
                pass

        return base64_frames

    def _build_messages(self, info: dict, frames: list[str]) -> list[dict]:
        description = self._sanitize_text(info.get('description', ''), 380)
        song_name = self._sanitize_text(info.get('song_name', 'Original Sound'), 120)
        uploader = self._sanitize_text(info.get('uploader', ''), 64)
        quality_label = self._sanitize_text(info.get('quality_label', ''), 24)
        likes = self._format_number(info.get('like_count', 0))
        views = self._format_number(info.get('view_count', 0))

        payload_text = {
            'uploader': uploader,
            'description': description,
            'song_name': song_name,
            'quality': quality_label,
            'likes': likes,
            'views': views,
            'frame_count': len(frames),
        }

        if self.mode == 'critical':
            system_prompt = (
                'Ты — элитный, токсичный и агрессивный критик видеомонтажа (эдитов). '
                f'Тебе на вход передана плотная раскадровка видео (максимум {self.max_frames} кадров). '
                'Твоя задача — проанализировать динамику, смену сцен и визуал, как если бы ты посмотрел ролик. '
                'Цепляйся за то, что реально видишь на таймлайне: отсутствие плавности, рваные переходы, ублюдская цветокоррекция (ЦК), '
                'пересветы, мыльное качество, отсутствие синхронизации. Если кадры вообще не меняются — хуесось за статичность и слайдшоу. '
                'Используй сленг эдиторов и маты. '
                'Ответ 2-3 предложения, максимум 250 символов. Без эмодзи и markdown.'
            )
        else:
            system_prompt = (
                'Ты пишешь краткий AI-комментарий к TikTok-видео на русском языке. '
                'Опирайся на переданную раскадровку и описание. '
                'Тон дружелюбный, живой, без токсичности. Ответ 1-2 предложения, максимум 220 символов, без эмодзи.'
            )

        user_content_text = (
            'Сформируй комментарий для блока <blockquote>Ai:</blockquote> по этим данным и раскадровке:\n'
            f'{json.dumps(payload_text, ensure_ascii=False)}\n'
            'Верни только текст комментария.'
        )

        user_content = [{'type': 'text', 'text': user_content_text}]

        selected_frames = []
        if frames:
            selected_frames = [frames[len(frames) // 2]]

        for frame_b64 in selected_frames:
            user_content.append({
                'type': 'image_url',
                'image_url': {
                    'url': f'data:image/jpeg;base64,{frame_b64}',
                    'detail': 'low'
                }
            })

        return[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_content},
        ]

    def _fallback_comment(self, info: dict) -> str:
        description = self._sanitize_text(info.get('description', ''), 120)
        song_name = self._sanitize_text(info.get('song_name', 'Original Sound'), 90)
        quality = self._sanitize_text(info.get('quality_label', ''), 20)
        uploader = self._sanitize_text(info.get('uploader', ''), 36)

        likes = self._format_number(info.get('like_count', 0))
        views = self._format_number(info.get('view_count', 0))

        seed_base = f"{uploader}|{description}|{song_name}|{quality}|{likes}|{views}|{info.get('duration', 0)}"
        variant = zlib.crc32(seed_base.encode('utf-8')) % 5

        if self.mode == 'critical':
            if variant == 0:
                first = 'Визуал выглядит как кусок мыла, выкрученный контраст не спасет это убожество.'
                second = 'Опять налепил дефолтных эффектов поверх всратых исходников. Удаляй.'
            elif variant == 1:
                first = 'ЦК настолько вырвиглазная, что хочется выколоть себе глаза. Идеи ноль, реализация в минусе.'
                second = 'Ты бы хоть попытался сделать нормально, а не рендерить этот кал.'
            elif variant == 2:
                first = 'Скучная, статичная хуйня. Кадры сменяются как презентация в 2007 году.'
                second = 'Даже если исходники были норм, ты умудрился превратить их в говно.'
            elif variant == 3:
                first = 'Шейки на бит будто отключили: клип едет мимо музыки и разваливается по ритму.'
                second = 'Такое ощущение, что монтаж делался на автопилоте без просмотра результата.'
            else:
                first = 'Синхра дохлая, переходы рвут глаз, а кадры ощущаются случайным набором.'
                second = 'Нужно заново собирать таймлайн и чистить ЦК, иначе это просто сырой черновик.'

            if song_name and song_name.lower() not in {'original sound', 'unknown'} and variant % 2 == 0:
                second = f'И под трек «{song_name}» это смотрится максимально уебански. Синхра мертва.'
        else:
            first = 'Ролик выглядит динамично и аккуратно смонтирован.'
            if quality:
                first = f'Ролик в качестве {quality.lower()} выглядит динамично и аккуратно смонтирован.'

            if description and description.lower() not in {'без описания', 'unknown'}:
                first = f'По описанию видно, что ролик сделан в стиле «{description}». '

            second = ''
            if song_name and song_name.lower() not in {'original sound', 'unknown'}:
                second = f'Трек «{song_name}» хорошо поддерживает атмосферу.'
            elif int(float(info.get('view_count', 0) or 0)) > 0 or int(float(info.get('like_count', 0) or 0)) > 0:
                second = f'У видео уже {views} просмотров и {likes} лайков — аудитории явно заходит.'
            elif uploader:
                second = f'У @{uploader} получился приятный клип с ровным вайбом.'

        comment = f'{first} {second}'.strip()
        return self._sanitize_text(comment, self.max_chars)

    async def generate_comment(self, info: dict) -> str:
        if not self.enabled:
            return ''

        file_path = info.get('file_path')
        duration = float(info.get('duration', 0) or 0)
        frames = await self._extract_frames(file_path, duration)

        if not frames and self.mode == 'critical':
            logger.warning('Failed to extract frames for VLM, using text-only prompt')

        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        payload = {
            'model': self.model,
            'messages': self._build_messages(info, frames),
            'temperature': 0.92 if self.mode == 'critical' else 0.55,
            'top_p': 0.9,
            'max_tokens': 160,
            'stream': False,
        }

        for attempt in range(2):
            timeout = aiohttp.ClientTimeout(total=self.timeout_seconds * (1 + attempt * 0.5))
            try:
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.post(self.base_url, headers=headers, json=payload) as response:
                        raw_text = await response.text()
                        if response.status >= 400:
                            logger.warning(
                                'NIM API error status=%s attempt=%s body=%s',
                                response.status,
                                attempt + 1,
                                raw_text[:300],
                            )
                            continue

                        data = json.loads(raw_text)
            except Exception as e:
                logger.warning('NIM API request failed attempt=%s error=%s', attempt + 1, e)
                continue

            choices = data.get('choices') or[]
            if not choices:
                continue

            message = (choices[0] or {}).get('message') or {}
            content = message.get('content', '')
            comment = self._sanitize_text(content, self.max_chars)
            if comment.lower().startswith('ai:'):
                comment = comment[3:].strip()

            if comment:
                return comment

        fallback = self._fallback_comment(info)
        if fallback:
            logger.info('NIM fallback comment used')
        return fallback
