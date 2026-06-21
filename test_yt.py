import asyncio
from yt_dlp import YoutubeDL
import json

async def main():
    ydl_opts = {'quiet': True, 'skip_download': True, 'extract_flat': False}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info('https://www.tiktok.com/@tiktok/video/7106594312292453675', download=False)
        formats = info.get('formats', [])
        for f in formats:
            if f.get('vcodec') != 'none':
                print({
                    'format_id': f.get('format_id'),
                    'resolution': f.get('resolution'),
                    'vcodec': f.get('vcodec'),
                    'filesize': f.get('filesize'),
                    'tbr': f.get('tbr'),
                    'fps': f.get('fps'),
                })
                print("url:", f.get('url')[:50])

if __name__ == '__main__':
    asyncio.run(main())
