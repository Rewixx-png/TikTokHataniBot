import html
import json
import logging
import re

import aiohttp

from core.config import (
    NIM_API_KEY,
    NIM_BASE_URL,
    NIM_COMMENTARY_MODE,
    NIM_ENABLED,
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
        self.model = NIM_MODEL
        self.timeout_seconds = float(NIM_TIMEOUT_SECONDS)
        self.max_chars = int(NIM_MAX_COMMENT_CHARS)
        self.mode = NIM_COMMENTARY_MODE if NIM_COMMENTARY_MODE in {'neutral', 'critical'} else 'neutral'

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

    def _build_messages(self, info: dict) -> list[dict]:
        description = self._sanitize_text(info.get('description', ''), 380)
        song_name = self._sanitize_text(info.get('song_name', 'Original Sound'), 120)
        uploader = self._sanitize_text(info.get('uploader', ''), 64)
        quality_label = self._sanitize_text(info.get('quality_label', ''), 24)
        region = self._sanitize_text(info.get('detected_country', ''), 24)

        payload = {
            'uploader': uploader,
            'description': description,
            'song_name': song_name,
            'quality': quality_label,
            'duration_seconds': int(float(info.get('duration', 0) or 0)),
            'resolution': f"{int(info.get('width', 0) or 0)}x{int(info.get('height', 0) or 0)}",
            'fps': int(info.get('fps', 0) or 0),
            'likes': int(float(info.get('like_count', 0) or 0)),
            'views': int(float(info.get('view_count', 0) or 0)),
            'comments': int(float(info.get('comment_count', 0) or 0)),
            'reposts': int(float(info.get('repost_count', 0) or 0)),
            'region': region,
        }

        if self.mode == 'critical':
            system_prompt = (
                'Ты пишешь краткий критический AI-комментарий к TikTok-видео на русском языке. '
                'Опирайся только на переданные данные. Подчеркивай слабые стороны ролика: динамика, монтаж, идея, подача. '
                'Не переходи на личности и не оскорбляй автора. Без травли и унижений. '
                'Ответ 1-2 предложения, максимум 220 символов, без эмодзи и markdown.'
            )
        else:
            system_prompt = (
                'Ты пишешь краткий AI-комментарий к TikTok-видео на русском языке. '
                'Опирайся только на переданные данные. Не выдумывай фактов, имён аниме и событий, если их нет. '
                'Тон дружелюбный, живой, без токсичности. Ответ 1-2 предложения, максимум 220 символов, без эмодзи и без markdown.'
            )

        user_prompt = (
            'Сформируй комментарий для блока <blockquote>Ai:</blockquote> по этим данным:\n'
            f'{json.dumps(payload, ensure_ascii=False)}\n'
            'Верни только текст комментария.'
        )

        return [
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt},
        ]

    def _fallback_comment(self, info: dict) -> str:
        description = self._sanitize_text(info.get('description', ''), 120)
        song_name = self._sanitize_text(info.get('song_name', 'Original Sound'), 90)
        quality = self._sanitize_text(info.get('quality_label', ''), 20)
        uploader = self._sanitize_text(info.get('uploader', ''), 36)

        likes = self._format_number(info.get('like_count', 0))
        views = self._format_number(info.get('view_count', 0))

        if self.mode == 'critical':
            first = 'Ролик больше похож на слайды: темп слабый и мало визуальной динамики.'
            if quality:
                first = f'Даже в качестве {quality.lower()} ролик выглядит статично, как набор слайдов.'

            if description and description.lower() not in {'без описания', 'unknown'}:
                first = f'Идея из описания «{description}» читается слабо, подача кажется сырой.'

            second = 'Стоит усилить монтаж, ритм и синхронизацию с музыкой.'
            if song_name and song_name.lower() not in {'original sound', 'unknown'}:
                second = f'Трек «{song_name}» мощный, но визуал пока не дотягивает до его энергии.'
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

        headers = {
            'Authorization': f'Bearer {self.api_key}',
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

        payload = {
            'model': self.model,
            'messages': self._build_messages(info),
            'temperature': 0.35,
            'top_p': 0.9,
            'max_tokens': 140,
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

            choices = data.get('choices') or []
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
