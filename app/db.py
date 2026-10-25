import json
from datetime import datetime, timezone
from pathlib import Path
import asyncpg
from app.config import DATABASE_URL


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc)


async def pool(size=4):
    async def init(c):
        await c.set_type_codec('jsonb', encoder=json.dumps, decoder=json.loads, schema='pg_catalog')
    return await asyncpg.create_pool(DATABASE_URL,min_size=1,max_size=size,command_timeout=90,init=init)


async def migrate(p):
    async with p.acquire() as c:
        async with c.transaction():
            await c.execute('SELECT pg_advisory_xact_lock(732598341)')
            await c.execute('CREATE TABLE IF NOT EXISTS schema_migrations(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())')
            for path in sorted(Path('migrations').glob('*.sql')):
                if not await c.fetchval('SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=$1)',path.name.split('_')[0]):
                    await c.execute(path.read_text())


async def coverage(p, exchange, feed, status, detail=None, first=None, last=None):
    await p.execute('''INSERT INTO feed_coverage(exchange,feed,status,detail,first_ts,last_ts) VALUES($1,$2,$3,$4,$5,$6)
    ON CONFLICT(exchange,feed) DO UPDATE SET status=excluded.status,detail=excluded.detail,
    first_ts=COALESCE(LEAST(feed_coverage.first_ts,excluded.first_ts),feed_coverage.first_ts,excluded.first_ts),
    last_ts=COALESCE(GREATEST(feed_coverage.last_ts,excluded.last_ts),feed_coverage.last_ts,excluded.last_ts),updated_at=now()''',exchange,feed,status,detail or {},first,last)


async def checkpoint(p, exchange, feed, cursor):
    await p.execute('''INSERT INTO ingestion_checkpoints(exchange,feed,cursor) VALUES($1,$2,$3)
    ON CONFLICT(exchange,feed) DO UPDATE SET cursor=excluded.cursor,updated_at=now()''',exchange,feed,cursor)


async def event(p, kind, payload):
    await p.execute('INSERT INTO stream_events(kind,payload) VALUES($1,$2)',kind,payload)
