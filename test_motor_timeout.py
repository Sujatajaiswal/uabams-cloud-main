import asyncio
import pymongo
from pymongo.errors import ServerSelectionTimeoutError

async def main():
    try:
        raise ServerSelectionTimeoutError("test")
    except Exception as e:
        print("Caught exception:", type(e))

asyncio.run(main())
