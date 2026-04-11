<h1 align="center">
  <br>
  <img src="https://upload.wikimedia.org/wikipedia/en/a/a9/TikTok_logo.svg" alt="TikTok" width="200">
  <br>
  TikTok Downloader Bot
  <br>
</h1>

<h4 align="center">High-performance Telegram bot for downloading TikTok videos with metadata extraction and audio recognition.</h4>

<p align="center">
  <a href="#features">Features</a> •
  <a href="#installation">Installation</a> •
  <a href="#usage">Usage</a> •
  <a href="#tech-stack">Tech Stack</a> •
  <a href="#caching">Caching</a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11-blue?style=flat-square&logo=python" alt="Python">
  <img src="https://img.shields.io/badge/aiogram-3.x-green?style=flat-square&logo=telegram" alt="Aiogram">
  <img src="https://img.shields.io/badge/Docker-ready-blue?style=flat-square&logo=docker" alt="Docker">
  <img src="https://img.shields.io/badge/License-MIT-yellow?style=flat-square" alt="License">
</p>

---

## Features

- ⚡ **Instant Delivery** — Videos sent immediately after download, metadata added asynchronously
- 🎚️ **Quality Picker** — Choose `Normal`, `High`, or `Original` quality before download
- 🧬 **Original/High Source** — `High` and `Original` use a third-party source; `Original` is sent as file
- 🎵 **Audio Recognition** — Shazam integration for track identification
- 🔔 **Profile Watcher** — Optional alerts in configured chat/topic when watched TikTok profile posts a new video
- 💾 **Smart Caching** — SQLite-based cache with 7-day TTL prevents re-downloading same videos
- 🧱 **DB Layer** — SQLAlchemy 2.0 Async + repository pattern + Alembic migrations
- 📊 **Rich Metadata** — Likes, comments, reposts, resolution, FPS, file size, upload date
- 🌍 **Geolocation** — Detects country of upload when available
- 🐳 **Docker Ready** — Includes local Telegram Bot API server in Docker Compose
- 🔒 **Safe** — Automatic file cleanup, configurable max file size

## Installation

### Via Docker (Recommended)

```bash
git clone https://github.com/YOUR_USERNAME/tiktok-downloader-bot.git
cd tiktok-downloader-bot

cp .env.example .env
# Edit .env and add your BOT_TOKEN

docker compose up -d
```

### Manual

```bash
# Install dependencies
pip install -r requirements.txt

# Setup environment
cp .env.example .env
# Edit .env with your token

# Run
python main.py
```

## Usage

1. Start the bot: `/start`
2. Send any TikTok link (supporting `tiktok.com`, `vm.tiktok.com`, `vt.tiktok.com`)
3. Bot checks availability and asks for quality (`Normal`, `High`, `Original`)
4. `Normal` downloads via TikTok extractor, `High`/`Original` via third-party source
5. Bot sends media, then updates caption with full analytics

### Performance Metrics

Every message includes timing breakdown:
- `↓` — Download time from TikTok
- `↑` — Upload time to Telegram  
- `🎵` — Audio recognition time
- `Σ` — Total processing time

Cached videos marked with `♻️` and served instantly.

## Tech Stack

| Component | Technology |
|-----------|------------|
| Framework | [aiogram 3.x](https://docs.aiogram.dev/) |
| Downloader | [yt-dlp](https://github.com/yt-dlp/yt-dlp) (`Normal` + probe/metadata) |
| High/Original mode | Third-party service flow (cloudscraper session) |
| Audio ID | [Shazamio](https://github.com/dotX12/ShazamIO) |
| Database | SQLite + SQLAlchemy 2.0 Async |
| Migrations | Alembic |
| Deployment | Docker + Docker Compose |

## Caching

Bot uses SQLite with following logic:
- **Key**: `quality|video_url`
- **Value**: Telegram `file_id` + metadata
- **TTL**: 7 days (auto cleanup)
- **LRU**: Access updates timestamp
- **Fallback**: If cached `file_id` invalid (Telegram expiration), auto-re-download

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `BOT_TOKEN` | Telegram Bot Token from [@BotFather](https://t.me/botfather) | Yes |
| `DATABASE_URL` | SQLAlchemy async URL (default: `sqlite+aiosqlite:///.../cache.db`) | No |
| `BOT_OWNER_ID` | Telegram user ID allowed to run `/test` watcher command in private chat | No |
| `DOWNLOAD_DIR` | Download directory (default: `./downloads`) | No |
| `MAX_FILE_SIZE_MB` | Max upload size in MB (auto defaults to 50 or 2000 in local Bot API mode) | No |
| `TIKTOK_WATCH_ENABLED` | Enables watched-profile notifications | No |
| `TIKTOK_WATCH_PROFILES` | Semicolon-separated watch list `Label|URL;Label|URL` | No |
| `TIKTOK_WATCH_PROFILE_URL` | Single-profile fallback (legacy mode) | No |
| `TIKTOK_WATCH_PROFILE_LABEL` | Optional label for legacy single-profile mode | No |
| `TIKTOK_WATCH_POLL_SECONDS` | Poll interval in seconds (minimum 30) | No |
| `TIKTOK_WATCH_TARGET_CHAT_ID` | Target chat ID for watcher alerts (supports supergroup numeric id) | No |
| `TIKTOK_WATCH_TARGET_THREAD_ID` | Optional forum topic `message_thread_id` (`0` for General topic) | No |
| `NIM_ENABLED` | Enables NVIDIA NIM AI caption commentary | No |
| `NIM_API_KEY` | NVIDIA NIM API key (`nvapi-...`) | No |
| `NIM_MODEL` | NIM model id (recommended: `meta/llama-3.3-70b-instruct`) | No |
| `NIM_BASE_URL` | NIM OpenAI-compatible chat completions URL | No |
| `NIM_TIMEOUT_SECONDS` | Timeout for AI request in seconds | No |
| `NIM_MAX_COMMENT_CHARS` | Maximum length of generated AI comment | No |
| `NIM_COMMENTARY_MODE` | `neutral` или `critical` (жесткая критика без оскорблений) | No |
| `NIM_MAX_FRAMES` | Максимум кадров для VLM-анализа (рекомендуется 6-10) | No |
| `TELEGRAM_API_ID` | Telegram API ID for local Bot API container | For Docker local Bot API |
| `TELEGRAM_API_HASH` | Telegram API hash for local Bot API container | For Docker local Bot API |
| `TELEGRAM_BOT_API_BASE_URL` | Custom Bot API base URL (e.g. `http://127.0.0.1:18081`) | No |
| `TELEGRAM_BOT_API_IS_LOCAL` | Set `1` when using Telegram Bot API in local mode | No |

## Migrations (Alembic)

```bash
# 1) install dependencies
pip install -r requirements.txt

# 2) create first migration automatically from models
alembic revision --autogenerate -m "initial schema"

# 3) apply migrations
alembic upgrade head
```

For an existing database that was created before Alembic, mark current schema as baseline first:

```bash
alembic stamp head
```

## Project Structure

```
.
├── core/
│   ├── config.py      # Configuration & constants
│   ├── database.py    # SQLAlchemy async engine + repositories
│   ├── middleware.py  # Aiogram middleware with AsyncSession injection
│   └── models.py      # SQLAlchemy ORM models
├── alembic/           # Alembic migrations
│   ├── env.py
│   └── versions/
├── handlers/
│   └── routes.py      # Bot command handlers
├── services/
│   ├── audio.py       # Shazam recognition
│   ├── downloader.py  # TikTok downloader (normal + metadata probe)
│   ├── nim_commentary.py # NVIDIA NIM AI caption commentary
│   ├── profile_watcher.py # Alerts for new videos on watched profiles
│   ├── snaptik.py     # Third-party download flow (high/original)
│   └── __init__.py
├── downloads/         # Temporary download directory
├── cookies.txt        # TikTok auth cookies (optional)
├── main.py           # Entry point
└── docker-compose.yml
```

## License

MIT License — see [LICENSE](LICENSE) for details.

---

<p align="center">
  Made with ❤️ and ☕
</p>
