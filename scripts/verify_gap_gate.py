"""Actual-input replay must wait for a durable gap-repair checkpoint."""
import asyncio,json
import asyncpg,aiohttp
from app import db
from app.config import DATABASE_URL
from app.engine import process
from app.exchanges import Adapter

async def main():
    p=await db.pool(1)
    admin=await asyncpg.connect(DATABASE_URL)
    name='btc_heatmap_gap_verify'
    assert not await admin.fetchval('SELECT 1 FROM pg_database WHERE datname=$1',name)
    await admin.execute('CREATE DATABASE '+name)
    async def init(c):await c.set_type_codec('jsonb',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')
    q=await asyncpg.create_pool(DATABASE_URL.rsplit('/',1)[0]+'/'+name,min_size=1,max_size=2,init=init)
    try:
        await db.migrate(q)
        start=await p.fetchval("SELECT min(c.ts) FROM candles c JOIN open_interest o ON o.exchange=c.exchange AND o.ts=c.ts JOIN open_interest n ON n.exchange=c.exchange AND n.ts=c.ts+interval '5 minutes' WHERE c.exchange='binance' AND c.kind='trade' AND c.interval_s=300")
        assert start
        candles=await p.fetch("SELECT * FROM candles WHERE exchange='binance' AND interval_s=300 AND kind IN ('trade','mark') AND ts=$1",start)
        oi=await p.fetch("SELECT * FROM open_interest WHERE exchange='binance' AND ts IN ($1,$1::timestamptz+interval '5 minutes')",start)
        async with q.acquire() as c:
            await c.executemany('INSERT INTO candles VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)',[tuple(r) for r in candles])
            await c.executemany('INSERT INTO open_interest VALUES($1,$2,$3,$4,$5,$6,$7)',[tuple(r) for r in oi])
        async with aiohttp.ClientSession() as s:
            a=Adapter('binance',s,q)
            for feed in ('repair_trade_300','backfill_oi'):
                await db.checkpoint(q,'binance',feed,{'done':False})
                state,n=await process(q,a,None,write=False)
                assert state is None and n==0,'Model jumped across an unfinished public-data repair'
                await db.checkpoint(q,'binance',feed,{'done':True})
                state,n=await process(q,a,None,write=False)
                assert state is not None and n==1
        # Replay one actual reported liquidation with REST/WS metadata differences.
        observed=await p.fetchrow('SELECT * FROM observed_liquidations ORDER BY ts LIMIT 1')
        assert observed
        await q.execute('INSERT INTO observed_liquidations VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)',*tuple(observed))
        async with aiohttp.ClientSession() as session:
            a=Adapter(observed['exchange'],session,q)
            raw=observed['raw']
            native=float(raw['sz']) if 'sz' in raw else observed['quantity_btc']/a.contract_btc
            stamp=int(raw['ts']) if 'ts' in raw else round(observed['ts'].timestamp()*1000)
            args=(stamp,observed['side'],native,observed['price'])
            await asyncio.gather(a.liquidation(*args,dict(raw,transport='rest'),observed['price_semantics']),a.liquidation(*args,dict(raw,transport='websocket'),observed['price_semantics']))
        assert await q.fetchval('SELECT count(*) FROM observed_liquidations')==1
        print(json.dumps({'passed':True,'actual_exchange':'binance','actual_candle_ts':start.isoformat(),'candle_gap_gate':True,'OI_gap_gate':True,'replay_resumes':True,'cross_transport_liquidation_deduplication':True}))
    finally:
        await q.close();await admin.execute('DROP DATABASE '+name);await admin.close();await p.close()

asyncio.run(main())
