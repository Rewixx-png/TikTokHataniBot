import os
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
TIKTOK_WATCH_POLL_SECONDS = max(30, _get_int_env("TIKTOK_WATCH_POLL_SECONDS", default=120))
TIKTOK_WATCH_TARGET_CHAT_ID = _get_chat_id_env("TIKTOK_WATCH_TARGET_CHAT_ID", default=BOT_OWNER_ID)
TIKTOK_WATCH_TARGET_THREAD_ID = max(0, _get_int_env("TIKTOK_WATCH_TARGET_THREAD_ID", default=0))
TIKTOK_WATCH_ENABLED = _get_bool_env(
    "TIKTOK_WATCH_ENABLED",
    default=bool(TIKTOK_WATCH_TARGET_CHAT_ID and TIKTOK_WATCH_PROFILE_URL),
)
