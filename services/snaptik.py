import asyncio
import json
import os
import re
import time
import uuid
from html import unescape
from urllib.parse import urljoin, urlparse

import cloudscraper

from core.config import DOWNLOAD_DIR
from services.downloader import TikTokDownloader


class SnapTikService:
    def __init__(self):
        self.base_url = 'https://snaptik.net/en'
        self.search_url = 'https://snaptik.net/api/ajaxSearch'
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
        without_tags = re.sub(r'<[^>]+>', ' ', value)
        return ' '.join(unescape(without_tags).split())

    def _extract_video_candidates(self, html_fragment: str) -> list[dict]:
        pattern = re.compile(r'<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
        candidates = []
        seen = set()

        for match in pattern.finditer(html_fragment or ''):
            href = match.group(1).strip()
            text = self._clean_text(match.group(2) or '')
            full_url = urljoin(self.base_url, href)
            lower_text = text.lower()
            lower_url = full_url.lower()

            if not full_url.startswith(('http://', 'https://')):
                continue
            if 'snaptikpro.net' in lower_url:
                continue
            if lower_url.startswith('https://snaptik.net/'):
                continue

            is_mp3 = 'mp3' in lower_text or '.mp3' in lower_url
            is_mp4 = 'mp4' in lower_text or '.mp4' in lower_url or 'dl.snapcdn.app/get?' in lower_url
            if is_mp3 or not is_mp4:
                continue

            if full_url in seen:
                continue

            seen.add(full_url)
            candidates.append(
                {
                    'url': full_url,
                    'text': text,
                    'is_hd': 'hd' in lower_text,
                }
            )

        return candidates

    def _extract_video_candidates_from_text(self, raw_text: str) -> list[dict]:
        candidates = self._extract_video_candidates(raw_text or '')
        if candidates:
            return candidates

        text = raw_text or ''
        if not text:
            return []

        url_pattern = re.compile(r'https?://[^\s"\'<>]+', re.IGNORECASE)
        seen = set()
        fallback_candidates = []

        for match in url_pattern.finditer(text):
            url = match.group(0).strip()
            lower_url = url.lower()

            if 'snaptikpro.net' in lower_url:
                continue
            if lower_url.startswith('https://snaptik.net/'):
                continue

            if '.mp3' in lower_url:
                continue

            if '.mp4' not in lower_url and 'dl.snapcdn.app/get?' not in lower_url:
                continue

            if url in seen:
                continue

            seen.add(url)
            fallback_candidates.append(
                {
                    'url': url,
                    'text': 'direct_link',
                    'is_hd': 'hd' in lower_url,
                }
            )

        return fallback_candidates

    def _parse_json_payload(self, raw_text: str):
        text = (raw_text or '').strip()
        if not text:
            return None

        candidates = [text]

        if text.startswith(")]}'"):
            lines = text.splitlines()
            if len(lines) > 1:
                candidates.append('\n'.join(lines[1:]).strip())

        start = text.find('{')
        end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            candidates.append(text[start : end + 1])

        for payload_text in candidates:
            if not payload_text:
                continue
            try:
                return json.loads(payload_text)
            except Exception:
                continue

        return None

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

    def _probe_size(self, scraper, url: str) -> int:
        headers = {
            'Referer': self.base_url,
            'Origin': 'https://snaptik.net',
        }

        try:
            response = scraper.head(url, headers=headers, allow_redirects=True, timeout=20)
            if response.status_code < 400:
                size = self._extract_size(response.headers)
                if size > 0:
                    return size
        except Exception:
            pass

        try:
            range_headers = dict(headers)
            range_headers['Range'] = 'bytes=0-1'
            with scraper.get(url, headers=range_headers, allow_redirects=True, timeout=20, stream=True) as response:
                if response.status_code < 400:
                    return self._extract_size(response.headers)
        except Exception:
            pass

        return 0

    def _choose_best_candidate(self, scraper, candidates: list[dict]) -> dict:
        hd_candidates = [item for item in candidates if item.get('is_hd')]
        if hd_candidates:
            return hd_candidates[0]

        best_candidate = candidates[0]
        best_size = -1

        for candidate in candidates[:6]:
            size = self._probe_size(scraper, candidate['url'])
            if size > best_size:
                best_size = size
                best_candidate = candidate

        return best_candidate

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
            'Origin': 'https://snaptik.net',
        }

        try:
            with scraper.get(source_url, headers=headers, allow_redirects=True, stream=True, timeout=120) as response:
                if response.status_code >= 400:
                    return {
                        'status': 'error',
                        'message': f'Сторонний сервис вернул ошибку загрузки: HTTP {response.status_code}',
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

    def _search_candidates(self, scraper, tiktok_url: str) -> dict:
        headers = {
            'x-requested-with': 'XMLHttpRequest',
            'origin': 'https://snaptik.net',
            'referer': self.base_url,
            'content-type': 'application/x-www-form-urlencoded; charset=UTF-8',
        }

        try:
            response = scraper.post(
                self.search_url,
                data={
                    'q': tiktok_url,
                    'lang': 'en',
                },
                headers=headers,
                timeout=90,
            )
        except Exception as e:
            return {
                'status': 'error',
                'message': f'Запрос к стороннему сервису не выполнен: {e}',
            }

        if response.status_code >= 400:
            return {
                'status': 'error',
                'message': f'Сторонний сервис вернул HTTP {response.status_code}',
            }

        raw_body = response.text or ''

        payload = None
        try:
            payload = response.json()
        except Exception:
            payload = self._parse_json_payload(raw_body)

        candidates = []
        service_message = ''

        if isinstance(payload, dict):
            raw_status = payload.get('status')
            status_ok = raw_status is True or str(raw_status).strip().lower() in {'ok', 'success', 'true', '1'}
            service_message = str(payload.get('msg') or payload.get('message') or '')

            fragments = []
            data_field = payload.get('data')

            if isinstance(data_field, str):
                fragments.append(data_field)
            elif isinstance(data_field, dict):
                fragments.extend(value for value in data_field.values() if isinstance(value, str))

            for key in ('html', 'result', 'download'):
                value = payload.get(key)
                if isinstance(value, str):
                    fragments.append(value)

            for fragment in fragments:
                candidates.extend(self._extract_video_candidates_from_text(fragment))

            if not candidates and not status_ok and service_message:
                return {
                    'status': 'error',
                    'message': service_message,
                }

        if not candidates:
            candidates = self._extract_video_candidates_from_text(raw_body)

        if not candidates:
            fallback_message = service_message or 'Сторонний сервис не вернул ссылку на MP4'
            return {
                'status': 'error',
                'message': fallback_message,
            }

        return {
            'status': 'success',
            'candidates': candidates,
        }

    def _probe_video_sync(self, tiktok_url: str) -> dict:
        scraper = self._create_scraper()

        try:
            scraper.get(self.base_url, timeout=60)
        except Exception as e:
            return {
                'status': 'error',
                'message': f'Не удалось инициализировать сторонний сервис: {e}',
            }

        search_result = self._search_candidates(scraper, tiktok_url)
        if search_result.get('status') == 'error':
            return search_result

        return {
            'status': 'success',
            'title': 'TikTok video',
            'duration': 0,
        }

    def _download_original_sync(self, tiktok_url: str, max_file_size_bytes: int) -> dict:
        start_time = time.time()
        scraper = self._create_scraper()

        try:
            scraper.get(self.base_url, timeout=60)
        except Exception as e:
            return {
                'status': 'error',
                'message': f'Не удалось инициализировать сторонний сервис: {e}',
            }

        search_result = self._search_candidates(scraper, tiktok_url)
        if search_result.get('status') == 'error':
            return search_result

        candidates = search_result['candidates']

        selected = self._choose_best_candidate(scraper, candidates)
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
            'source_label': selected.get('text', ''),
        }

    async def download_original(self, tiktok_url: str, max_file_size_bytes: int) -> dict:
        return await asyncio.to_thread(self._download_original_sync, tiktok_url, max_file_size_bytes)

    async def probe_video(self, tiktok_url: str) -> dict:
        return await asyncio.to_thread(self._probe_video_sync, tiktok_url)
