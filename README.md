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
- 🎵 **Audio Recognition** — Shazam integration for track identification
- 💾 **Smart Caching** — SQLite-based cache with 7-day TTL prevents re-downloading same videos
- 📊 **Rich Metadata** — Likes, comments, reposts, resolution, FPS, file size, upload date
- 🌍 **Geolocation** — Detects country of upload when available
- 🐳 **Docker Ready** — One-command deployment with Docker Compose
- 🔒 **Safe** — Automatic file cleanup, size validation (50MB limit)

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
3. Bot responds instantly with video, then updates caption with full analytics

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
| Downloader | [yt-dlp](https://github.com/yt-dlp/yt-dlp) |
| Audio ID | [Shazamio](https://github.com/dotX12/ShazamIO) |
| Database | SQLite (aiosqlite) |
| Deployment | Docker + Docker Compose |

## Caching

Bot uses SQLite with following logic:
- **Key**: Video URL
- **Value**: Telegram `file_id` + metadata
- **TTL**: 7 days (auto cleanup)
- **LRU**: Access updates timestamp
- **Fallback**: If cached `file_id` invalid (Telegram expiration), auto-re-download

## Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `BOT_TOKEN` | Telegram Bot Token from [@BotFather](https://t.me/botfather) | Yes |
| `DOWNLOAD_DIR` | Download directory (default: `./downloads`) | No |

## Project Structure

```
.
├── core/
│   ├── config.py      # Configuration & constants
│   └── database.py    # SQLite cache layer
├── handlers/
│   └── routes.py      # Bot command handlers
├── services/
│   ├── audio.py       # Shazam recognition
│   └── downloader.py  # TikTok download logic
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