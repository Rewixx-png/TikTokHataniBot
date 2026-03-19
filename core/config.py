import os
from dotenv import load_dotenv

load_dotenv()


def _get_bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

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
