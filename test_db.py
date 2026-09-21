import asyncio
from app.database import db
from passlib.hash import bcrypt

async def main():
    await db.connect()
    async with db.pg_pool.acquire() as conn:
        records = await conn.fetch("SELECT username, role FROM users")
        for r in records:
            print(f"User: {r['username']}, Role: {r['role']}")
    await db.close()

asyncio.run(main())
