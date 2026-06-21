import asyncio
from core.database import get_session
from sqlalchemy import text
from core.config import DATABASE_URL

async def main():
    print(f"DATABASE_URL is: {DATABASE_URL}")
    try:
        async for session in get_session():
            result = await session.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))
            tables = [row[0] for row in result]
            print(f"Tables in DB: {tables}")
            
            if 'bonus_videos' in tables:
                res = await session.execute(text("SELECT status, count(*) FROM bonus_videos GROUP BY status"))
                for row in res:
                    print(f"Status: {row[0]}, Count: {row[1]}")
            else:
                print("Table bonus_videos NOT FOUND in this database.")
            break
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
