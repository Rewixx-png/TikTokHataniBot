import aiosqlite
import os
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.getcwd(), "cache.db")

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS video_cache (
                url TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                uploader TEXT,
                uploader_id TEXT,
                description TEXT,
                like_count INTEGER DEFAULT 0,
                view_count INTEGER DEFAULT 0,
                comment_count INTEGER DEFAULT 0,
                repost_count INTEGER DEFAULT 0,
                upload_date TEXT,
                detected_country TEXT,
                width INTEGER DEFAULT 0,
                height INTEGER DEFAULT 0,
                fps INTEGER DEFAULT 0,
                duration REAL DEFAULT 0,
                file_size INTEGER DEFAULT 0,
                song_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE INDEX IF NOT EXISTS idx_created_at ON video_cache(created_at)
        """)

        cursor = await db.execute("PRAGMA table_info(video_cache)")
        columns = [row[1] for row in await cursor.fetchall()]
        if 'view_count' not in columns:
            await db.execute("ALTER TABLE video_cache ADD COLUMN view_count INTEGER DEFAULT 0")

        await db.commit()

async def get_cached_video(url: str):
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM video_cache WHERE url = ? AND created_at > ?",
            (url, datetime.now() - timedelta(days=7))
        )
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "UPDATE video_cache SET created_at = CURRENT_TIMESTAMP WHERE url = ?",
                (url,)
            )
            await db.commit()
        return dict(row) if row else None


async def get_cached_video_by_file_id(file_id: str):
    if not file_id:
        return None

    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM video_cache WHERE file_id = ? ORDER BY created_at DESC LIMIT 1",
            (file_id,),
        )
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "UPDATE video_cache SET created_at = CURRENT_TIMESTAMP WHERE url = ?",
                (row['url'],),
            )
            await db.commit()
        return dict(row) if row else None

async def save_video_cache(data: dict):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO video_cache 
            (url, file_id, uploader, uploader_id, description, like_count, view_count, comment_count, 
             repost_count, upload_date, detected_country, width, height, fps, duration, 
             file_size, song_name)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get('url'),
            data.get('file_id'),
            data.get('uploader'),
            data.get('uploader_id'),
            data.get('description'),
            data.get('like_count', 0),
            data.get('view_count', 0),
            data.get('comment_count', 0),
            data.get('repost_count', 0),
            data.get('upload_date'),
            data.get('detected_country'),
            data.get('width', 0),
            data.get('height', 0),
            data.get('fps', 0),
            data.get('duration', 0),
            data.get('file_size', 0),
            data.get('song_name')
        ))
        await db.commit()

async def cleanup_old_cache():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "DELETE FROM video_cache WHERE created_at < ?",
            (datetime.now() - timedelta(days=7),)
        )
        await db.commit()
