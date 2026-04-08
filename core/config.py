import os
import re
from dotenv import load_dotenv

load_dotenv()


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int_env(name: str, default: int = 0) -> int:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


def _get_chat_id_env(name: str, default: int = 0) -> int:
    raw_value = os.getenv(name, '').strip()
    if not raw_value:
        return default

    try:
        numeric = int(raw_value)
    except (TypeError, ValueError):
        return default

    if numeric > 0 and len(raw_value) >= 10:
        return int(f'-100{raw_value}')

    return numeric


def _get_float_env(name: str, default: float = 0.0) -> float:
    value = os.getenv(name)
    if value is None:
        return default

    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return default

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in .env file")

DOWNLOAD_DIR = os.path.join(os.getcwd(), os.getenv("DOWNLOAD_DIR", "downloads"))
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

TELEGRAM_BOT_API_BASE_URL = os.getenv("TELEGRAM_BOT_API_BASE_URL", "").strip()
TELEGRAM_BOT_API_IS_LOCAL = _get_bool_env(
    "TELEGRAM_BOT_API_IS_LOCAL",
    default=bool(TELEGRAM_BOT_API_BASE_URL),
)

default_max_file_size_mb = 2000 if TELEGRAM_BOT_API_BASE_URL and TELEGRAM_BOT_API_IS_LOCAL else 50

MAX_FILE_SIZE_MB = int(os.getenv("MAX_FILE_SIZE_MB", str(default_max_file_size_mb)))
MAX_FILE_SIZE_BYTES = MAX_FILE_SIZE_MB * 1024 * 1024

BOT_OWNER_ID = _get_int_env("BOT_OWNER_ID", default=0)

TIKTOK_WATCH_PROFILE_URL = os.getenv("TIKTOK_WATCH_PROFILE_URL", "").strip()
TIKTOK_WATCH_PROFILE_LABEL = os.getenv("TIKTOK_WATCH_PROFILE_LABEL", "").strip()
TIKTOK_WATCH_PROFILES_RAW = os.getenv("TIKTOK_WATCH_PROFILES", "").strip()
TIKTOK_WATCH_POLL_SECONDS = max(30, _get_int_env("TIKTOK_WATCH_POLL_SECONDS", default=120))
TIKTOK_WATCH_TARGET_CHAT_ID = _get_chat_id_env("TIKTOK_WATCH_TARGET_CHAT_ID", default=BOT_OWNER_ID)
TIKTOK_WATCH_TARGET_THREAD_ID = max(0, _get_int_env("TIKTOK_WATCH_TARGET_THREAD_ID", default=0))


def _extract_username(profile_url: str) -> str:
    match = re.search(r'tiktok\.com/@([^/?]+)', profile_url or '', re.IGNORECASE)
    if not match:
        return ''
    return match.group(1).strip().lower()


def _build_watch_profiles() -> list[dict[str, str]]:
    profiles: list[dict[str, str]] = []
    seen_keys: set[str] = set()

    def push(label: str, url: str):
        cleaned_url = str(url or '').strip()
        if not cleaned_url:
            return
        if not cleaned_url.startswith(('http://', 'https://')):
            return

        username = _extract_username(cleaned_url)
        key = username or cleaned_url.lower()
        if key in seen_keys:
            return

        seen_keys.add(key)
        profiles.append(
            {
                'key': key,
                'label': str(label or '').strip(),
                'url': cleaned_url,
            }
        )

    for chunk in re.split(r'[;\n]+', TIKTOK_WATCH_PROFILES_RAW):
        piece = chunk.strip()
        if not piece:
            continue

        if '|' in piece:
            label, url = piece.split('|', 1)
            push(label, url)
        else:
            push('', piece)

    if not profiles and TIKTOK_WATCH_PROFILE_URL:
        push(TIKTOK_WATCH_PROFILE_LABEL, TIKTOK_WATCH_PROFILE_URL)

    return profiles


TIKTOK_WATCH_PROFILES = _build_watch_profiles()

TIKTOK_WATCH_ENABLED = _get_bool_env(
    "TIKTOK_WATCH_ENABLED",
    default=bool(TIKTOK_WATCH_TARGET_CHAT_ID and TIKTOK_WATCH_PROFILES),
)

NIM_API_KEY = os.getenv("NIM_API_KEY", "").strip()
NIM_ENABLED = _get_bool_env("NIM_ENABLED", default=bool(NIM_API_KEY))
NIM_BASE_URL = os.getenv("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1/chat/completions").strip()
NIM_MODEL = os.getenv("NIM_MODEL", "meta/llama-3.3-70b-instruct").strip()
NIM_TIMEOUT_SECONDS = max(5, _get_float_env("NIM_TIMEOUT_SECONDS", default=12.0))
NIM_MAX_COMMENT_CHARS = max(120, min(350, _get_int_env("NIM_MAX_COMMENT_CHARS", default=220)))
NIM_COMMENTARY_MODE = os.getenv("NIM_COMMENTARY_MODE", "neutral").strip().lower()
NIM_MAX_FRAMES = max(3, min(16, _get_int_env("NIM_MAX_FRAMES", default=8)))
