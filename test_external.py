import asyncio
from services.snaptik import SnapTikService
from services.musicaldown import MusicalDownService
from core.config import MAX_FILE_SIZE_BYTES

async def main():
    s = SnapTikService()
    m = MusicalDownService()
    url = "https://vt.tiktok.com/ZSxdBmGNx/"
    
    print("Testing SnapTik...")
    try:
        res = await asyncio.wait_for(s.download_original(url, MAX_FILE_SIZE_BYTES), timeout=60)
        print("SnapTik:", res)
    except Exception as e:
        print("SnapTik failed:", e)
    
    print("Testing MusicalDown...")
    try:
        res = await asyncio.wait_for(m.download_original(url, MAX_FILE_SIZE_BYTES), timeout=60)
        print("MusicalDown:", res)
    except Exception as e:
        print("MusicalDown failed:", e)

asyncio.run(main())
