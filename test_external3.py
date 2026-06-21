import asyncio
from services.snaptik import SnapTikService
from services.musicaldown import MusicalDownService
from core.config import MAX_FILE_SIZE_BYTES

async def test():
    s = SnapTikService()
    url = "https://vt.tiktok.com/ZSxdBmGNx/"
    res = await s.download_original(url, MAX_FILE_SIZE_BYTES)
    print(res)

asyncio.run(test())
