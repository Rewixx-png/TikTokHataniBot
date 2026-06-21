import asyncio
from core.database import get_session
from sqlalchemy import text

async def main():
    async for session in get_session():
        result = await session.execute(text("SELECT count(*), status FROM bonus_videos GROUP BY status"))
        for row in result:
            print(f"Status: {row[1]}, Count: {row[0]}")
        break

if __name__ == "__main__":
    asyncio.run(main())
