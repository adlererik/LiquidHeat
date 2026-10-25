import asyncio
import sys
from app import db

async def main():
    p=await db.pool(1)
    try:
        n=await p.fetchval("SELECT count(*) FROM feed_coverage WHERE feed='rest' AND status='live' AND updated_at>now()-interval '3 minutes'")
        return 0 if n else 1
    finally:
        await p.close()

sys.exit(asyncio.run(main()))
