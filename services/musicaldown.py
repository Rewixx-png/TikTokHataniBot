import asyncio
import os
import re
import time
import uuid
from html import unescape
from urllib.parse import urlparse

import cloudscraper

from core.config import DOWNLOAD_DIR
from services.downloader import TikTokDownloader


class MusicalDownService:
    def __init__(self):
        self.base_url = 'https://musicaldown.com/en'
        self.download_url = 'https://musicaldown.com/download'
        self.download_path = DOWNLOAD_DIR
        self.downloader = TikTokDownloader()

    def _create_scraper(self):
        return cloudscraper.create_scraper(
            browser={
                'browser': 'chrome',
                'platform': 'linux',
                'mobile': False,
            }
        )

    def _clean_text(self, value: str) -> str:
        no_tags = re.sub(r'<[^>]+>', ' ', value or '')
        return ' '.join(unescape(no_tags).split())

    def _extract_size(self, headers) -> int:
        content_range = headers.get('Content-Range', '')
        if content_range:
            match = re.search(r'/([0-9]+)$', content_range)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    pass

        content_length = headers.get('Content-Length')
        if content_length:
            try:
                return int(content_length)
            except ValueError:
                return 0

        return 0

    def _build_file_path(self, source_url: str) -> str:
        extension = os.path.splitext(urlparse(source_url).path)[1].lower()
        if not extension or len(extension) > 6:
            extension = '.mp4'
        return os.path.join(self.download_path, f'{uuid.uuid4()}{extension}')

    def _download_to_file(self, scraper, source_url: str, max_file_size_bytes: int) -> dict:
        file_path = self._build_file_path(source_url)
        downloaded_size = 0

        headers = {
            'Referer': self.base_url,
            'Origin': 'https://musicaldown.com',
        }

        try:
            with scraper.get(source_url, headers=headers, allow_redirects=True, stream=True, timeout=120) as response:
                if response.status_code >= 400:
                    return {
                        'status': 'error',
                        'message': f'Сервис MusicalDown вернул ошибку загрузки: HTTP {response.status_code}',
                    }

                header_size = self._extract_size(response.headers)
                if header_size > max_file_size_bytes > 0:
                    return {
                        'status': 'error',
                        'message': (
                            f'File too large: {header_size / (1024 * 1024):.1f} MB. '
                            f'Maximum allowed: {max_file_size_bytes / (1024 * 1024):.0f} MB'
                        ),
                    }

                with open(file_path, 'wb') as file_obj:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if not chunk:
                            continue

                        downloaded_size += len(chunk)
                        if max_file_size_bytes > 0 and downloaded_size > max_file_size_bytes:
                            raise ValueError(
                                f'File too large: {downloaded_size / (1024 * 1024):.1f} MB. '
                                f'Maximum allowed: {max_file_size_bytes / (1024 * 1024):.0f} MB'
                            )

                        file_obj.write(chunk)

            if downloaded_size <= 0:
                return {
                    'status': 'error',
                    'message': 'Downloaded file is empty',
                }

            return {
                'status': 'success',
                'file_path': file_path,
                'file_size': downloaded_size,
            }
        except Exception as e:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            return {
                'status': 'error',
                'message': str(e),
            }

    def _extract_form_fields(self, html_body: str) -> dict:
        form_match = re.search(
            r'<form[^>]*id=["\']submit-form["\'][^>]*>(.*?)</form>',
            html_body or '',
            re.IGNORECASE | re.DOTALL,
        )
        if not form_match:
            return {'status': 'error', 'message': 'Не удалось найти форму загрузки MusicalDown'}

        form_html = form_match.group(1)
        input_tags = re.findall(r'<input[^>]*>', form_html, re.IGNORECASE)

        url_field_name = ''
        token_field_name = ''
        token_field_value = ''

        for input_tag in input_tags:
            name_match = re.search(r'name=["\']([^"\']+)["\']', input_tag, re.IGNORECASE)
            type_match = re.search(r'type=["\']([^"\']+)["\']', input_tag, re.IGNORECASE)
            value_match = re.search(r'value=["\']([^"\']*)["\']', input_tag, re.IGNORECASE)

            if not name_match:
                continue

            field_name = name_match.group(1)
            field_type = (type_match.group(1).lower() if type_match else '').strip()
            field_value = value_match.group(1) if value_match else ''

            if field_type == 'text' and not url_field_name:
                url_field_name = field_name
            elif field_type == 'hidden' and field_name != 'verify' and not token_field_name:
                token_field_name = field_name
                token_field_value = field_value

        if not url_field_name or not token_field_name:
            return {'status': 'error', 'message': 'Не удалось извлечь параметры формы MusicalDown'}

        return {
            'status': 'success',
            'url_field_name': url_field_name,
            'token_field_name': token_field_name,
            'token_field_value': token_field_value,
        }

    def _extract_video_candidates(self, html_body: str) -> list[dict]:
        candidates = []
        seen = set()

        anchor_pattern = re.compile(
            r'<a[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            re.IGNORECASE | re.DOTALL,
        )

        for match in anchor_pattern.finditer(html_body or ''):
            href = unescape(match.group(1).strip())
            label = self._clean_text(match.group(2) or '')
            lower_label = label.lower()
            lower_href = href.lower()

            if not href.startswith(('http://', 'https://')):
                continue

            if 'download mp4' not in lower_label:
                continue

            is_hd = '[hd]' in lower_label or ' hd' in lower_label
            is_watermark = 'watermark' in lower_label and not is_hd

            if href in seen:
                continue

            seen.add(href)
            candidates.append(
                {
                    'url': href,
                    'label': label,
                    'is_hd': is_hd,
                    'is_watermark': is_watermark,
                    'from_fastdl': 'fastdl.muscdn.app' in lower_href,
                }
            )

        return candidates

    def _choose_best_candidate(self, candidates: list[dict]) -> dict:
        if not candidates:
            return {}

        hd_candidates = [item for item in candidates if item.get('is_hd')]
        if hd_candidates:
            return hd_candidates[0]

        no_wm_candidates = [item for item in candidates if not item.get('is_watermark')]
        if no_wm_candidates:
            return no_wm_candidates[0]

        return candidates[0]

    def _search_candidate(self, scraper, tiktok_url: str) -> dict:
        try:
            home_response = scraper.get(self.base_url, timeout=60)
        except Exception as e:
            return {
                'status': 'error',
                'message': f'Не удалось инициализировать MusicalDown: {e}',
            }

        if home_response.status_code >= 400:
            return {
                'status': 'error',
                'message': f'MusicalDown вернул HTTP {home_response.status_code} при инициализации',
            }

        form_data = self._extract_form_fields(home_response.text or '')
        if form_data.get('status') == 'error':
            return form_data

        payload = {
            form_data['url_field_name']: tiktok_url,
            form_data['token_field_name']: form_data['token_field_value'],
            'verify': '1',
        }

        headers = {
            'Origin': 'https://musicaldown.com',
            'Referer': self.base_url,
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        }

        try:
            response = scraper.post(self.download_url, data=payload, headers=headers, timeout=90)
        except Exception as e:
            return {
                'status': 'error',
                'message': f'Запрос к MusicalDown не выполнен: {e}',
            }

        if response.status_code >= 400:
            return {
                'status': 'error',
                'message': f'MusicalDown вернул HTTP {response.status_code}',
            }

        body = response.text or ''
        if 'Just a moment...' in body:
            return {
                'status': 'error',
                'message': 'MusicalDown временно требует Cloudflare challenge',
            }

        candidates = self._extract_video_candidates(body)
        if not candidates:
            return {
                'status': 'error',
                'message': 'MusicalDown не вернул ссылку на MP4',
            }

        selected = self._choose_best_candidate(candidates)
        if not selected:
            return {
                'status': 'error',
                'message': 'MusicalDown не смог выбрать подходящую ссылку',
            }

        return {
            'status': 'success',
            'candidate': selected,
        }

    def _probe_video_sync(self, tiktok_url: str) -> dict:
        scraper = self._create_scraper()
        result = self._search_candidate(scraper, tiktok_url)
        if result.get('status') == 'error':
            return result

        return {
            'status': 'success',
            'title': 'TikTok video',
            'duration': 0,
        }

    def _download_original_sync(self, tiktok_url: str, max_file_size_bytes: int) -> dict:
        start_time = time.time()
        scraper = self._create_scraper()

        search_result = self._search_candidate(scraper, tiktok_url)
        if search_result.get('status') == 'error':
            return search_result

        selected = search_result['candidate']
        download_result = self._download_to_file(scraper, selected['url'], max_file_size_bytes)
        if download_result.get('status') == 'error':
            return download_result

        file_path = download_result['file_path']
        local_info = self.downloader.get_local_video_info(file_path)

        return {
            'status': 'success',
            'file_path': file_path,
            'file_size': download_result['file_size'],
            'download_time': time.time() - start_time,
            'uploader': 'Unknown',
            'uploader_id': '',
            'description': '',
            'like_count': 0,
            'comment_count': 0,
            'repost_count': 0,
            'upload_date': None,
            'detected_country': None,
            'width': local_info.get('width') or 0,
            'height': local_info.get('height') or 0,
            'fps': local_info.get('fps') or 0,
            'duration': local_info.get('duration') or 0,
            'source_url': selected['url'],
            'source_label': selected.get('label', ''),
        }

    async def download_original(self, tiktok_url: str, max_file_size_bytes: int) -> dict:
        return await asyncio.to_thread(self._download_original_sync, tiktok_url, max_file_size_bytes)

    async def probe_video(self, tiktok_url: str) -> dict:
        return await asyncio.to_thread(self._probe_video_sync, tiktok_url)
