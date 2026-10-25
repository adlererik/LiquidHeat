"""Run INSIDE the Debian engine container; no services or positions are fabricated."""
import asyncio
from datetime import timedelta
import json
import os
import time
import asyncpg
import aiohttp
import numpy as np
from app import db
from app.config import DATABASE_URL,VERSION
from app.engine import process,load_state
from app.exchanges import Adapter,bybit_one_side
from app.model import decode_array,State,encode_array


async def main():
    report={'tests':[],'started_at':time.time()}
    p=await db.pool(2)
    async def check(name,fn):
        begin=time.monotonic()
        await fn()
        report['tests'].append({'name':name,'passed':True,'seconds':round(time.monotonic()-begin,3)})
        print('PASS',name,flush=True)
    async def migrations():
        admin=await asyncpg.connect(DATABASE_URL)
        name='btc_heatmap_migration_verify'
        if await admin.fetchval('SELECT 1 FROM pg_database WHERE datname=$1',name):
            raise AssertionError('Previous verification DB exists: inspect before reusing')
        await admin.execute('CREATE DATABASE '+name)
        testdsn=DATABASE_URL.rsplit('/',1)[0]+'/'+name
        async def init(c):
            await c.set_type_codec('jsonb',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')
        q=await asyncpg.create_pool(testdsn,min_size=1,max_size=2,init=init)
        try:
            await db.migrate(q);await db.migrate(q)
            from pathlib import Path
            assert await q.fetchval('SELECT count(*) FROM schema_migrations')==len(list(Path('migrations').glob('*.sql')))
            assert await q.fetchval("SHOW timescaledb.license")=='apache'
            assert await q.fetchval("SELECT count(*) FROM timescaledb_information.hypertables")>=9
            await q.execute("INSERT INTO open_interest VALUES(now(),'test',10,10,NULL,'test','{}')")
            await q.execute("SELECT drop_chunks('open_interest',older_than=>interval '35 days')")
        finally:
            await q.close()
            await admin.execute('DROP DATABASE '+name)
            await admin.close()
    async def database_invariants():
        assert await p.fetchval("SHOW timescaledb.license")=='apache'
        assert await p.fetchval("SELECT extversion FROM pg_extension WHERE extname='timescaledb'")=='2.19.3'
        rows=await p.fetch('SELECT * FROM clld_history ORDER BY ts DESC LIMIT 500')
        assert rows,'No actual model states yet'
        for r in rows:
            assert abs(r['clld_usd']-(r['long_usd']-r['short_usd']))<.01
            assert -1<=r['oscillator']<=1
            assert abs(r['oscillator']-(r['long_usd']-r['short_usd'])/max(1.,r['long_usd']+r['short_usd']))<1e-10
        assert not await p.fetchval('SELECT EXISTS(SELECT 1 FROM calibration_history WHERE alpha_after<.01 OR alpha_after>.15)')
        assert await p.fetchval("SELECT count(distinct exchange) FROM candles WHERE exchange IN ('binance','bybit','okx')")==3
        assert await p.fetchval('SELECT count(distinct exchange) FROM open_interest')==3
        assert await p.fetchval('SELECT count(*) FROM observed_liquidations')>0
        for r in await p.fetch('SELECT * FROM model_checkpoints'):
            state=State.restore(r['meta'],r['matrix'])
            assert np.isfinite(state.exposure).all() and state.exposure.min()>=0
            assert np.all(state.exposure.sum(axis=(1,2))<=state.prior_oi*(.01 if r['exchange']=='okx' else 1)+1e-3)
    async with aiohttp.ClientSession(headers={'User-Agent':'btc-liq-heatmap/1.0 verification'}) as session:
        async def public_ingestion():
            for name in ('binance','bybit','okx'):
                a=Adapter(name,session,p)
                candles=await a.candle_page('trade',300,int(time.time()*1000),limit=3)
                marks=await a.candle_page('mark',300,int(time.time()*1000),limit=3)
                assert candles and marks and all(x[2]>=x[3] and x[5]>=0 for x in candles)
                await a.store_candles(candles,'trade',300)
                keys=[db.utc(x[0]/1000) for x in candles if x[0]/1000+300<=time.time()]
                before=await p.fetch("SELECT ts,o,h,l,c,volume FROM candles WHERE exchange=$1 AND kind='trade' AND interval_s=300 AND ts=ANY($2::timestamptz[]) ORDER BY ts",name,keys)
                await a.store_candles(candles,'trade',300)
                after=await p.fetch("SELECT ts,o,h,l,c,volume FROM candles WHERE exchange=$1 AND kind='trade' AND interval_s=300 AND ts=ANY($2::timestamptz[]) ORDER BY ts",name,keys)
                assert before==after
        async def api():
            for lookback,bucket in [('24h',25),('3d',50),('7d',100),('14d',200),('30d',200)]:
                async with session.get(f'http://web:8080/api/v1/heatmap?lookback={lookback}&bucket={bucket}',timeout=aiohttp.ClientTimeout(total=90)) as r:
                    assert r.status==200,await r.text()
                    d=await r.json()
                assert d['estimated'] and d['matrix'] and d['candles'] and d['profile']
                assert all(0<=x<len(d['times']) and 0<=y<len(d['prices']) and np.isfinite(v) and v>=0 for x,y,v in d['matrix'])
                assert d['times']==sorted(set(d['times']))
                # Timestamp of each state and candle belongs to the same interval-end grid.
                assert all(t%(d['time_resolution_s']*1000)==0 for t in d['times'])
                assert all(c[0]%(d['time_resolution_s']*1000)==0 for c in d['candles'])
                await asyncio.sleep(.4)
            async with session.get('http://web:8080/api/v1/heatmap?leverage=125&exchanges=okx&bucket=100') as r:
                d=await r.json();assert r.status==200 and not d['profile']
            async with session.get('http://web:8080/api/v1/heatmap?bucket=17') as r:assert r.status==422
            async with session.get('http://web:8080/api/v1/magnets?limit=5') as r:
                d=await r.json();assert r.status==200 and 0<len(d['zones'])<=5 and all(np.isfinite(z['z_score']) for z in d['zones'])
            async with session.get('http://web:8080/api/v1/diagnostics') as r:
                d=await r.json();assert r.status==200 and len(d['contract_specs'])==3
        async def websocket():
            async with session.ws_connect('http://web:8080/ws/stream',receive_timeout=20) as ws:
                hello=await ws.receive_json();assert hello['type']=='hello'
                types=set()
                until=time.monotonic()+20
                while time.monotonic()<until:
                    msg=await ws.receive_json(timeout=10)
                    types.add(msg['type'])
                    if 'mark' in types and 'profile' in types:break
                assert 'mark' in types,types
                assert 'heartbeat' in types or 'profile' in types,types
                report['ws_types']=sorted(types)
        async def replay():
            # Bounded actual-input replay, compared with the saved historical state.
            a=Adapter('binance',session,p)
            state,n=await process(p,a,None,write=False)
            assert state is not None and n>0
            r=await p.fetchrow('SELECT * FROM heatmap_snapshots WHERE exchange=$1 AND ts=$2 AND model_version=$3','binance',db.utc(state.last_ts),VERSION)
            assert r,'Replay target not persisted yet'
            original=decode_array(r['matrix'],state.exposure.shape)
            np.testing.assert_allclose(state.exposure,original,rtol=2e-5,atol=1e-8)
            restored=State.restore(state.checkpoint(),encode_array(state.exposure))
            np.testing.assert_allclose(restored.exposure,state.exposure,rtol=2e-6,atol=1e-10)
            report['replay_intervals']=state.steps
        for name,fn in [('fresh_and_idempotent_migrations',migrations),('database_and_numerical_invariants',database_invariants),('public_exchange_ingestion_and_deduplication',public_ingestion),('rest_all_lookbacks_and_filters',api),('websocket_real_events',websocket),('historical_replay_actual_inputs',replay)]:
            await check(name,fn)
    await p.close()
    report['finished_at']=time.time()
    print(json.dumps(report,indent=2))

if __name__=='__main__':asyncio.run(main())
