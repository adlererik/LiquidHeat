"""Debian-only checks. Real CEX samples are copied into a disposable database."""
import asyncio
import json
import time
import uuid
from datetime import datetime,timezone,timedelta
import asyncpg
import numpy as np
from app import db,cex_worker,api
from app.config import DATABASE_URL,EXCHANGES,VERSION
from app.cex_scenarios import SCENARIOS,significant_norm
from app.model import State,decode_array,encode_array
from app.engine import process
from app.exchanges import Adapter


async def codec(c):await c.set_type_codec('jsonb',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')


async def main():
    source=await db.pool(1);admin=await asyncpg.connect(DATABASE_URL)
    name='liquidheat_cex_verify_'+uuid.uuid4().hex[:12]
    await admin.execute('CREATE DATABASE '+name)
    target=DATABASE_URL.rsplit('/',1)[0]+'/'+name
    p=await asyncpg.create_pool(target,min_size=1,max_size=1,init=codec)
    restricted=None
    try:
        await db.migrate(p);await db.migrate(p)
        print('PASS additive/repeated CEX migration',flush=True)
        start=datetime.now(timezone.utc)-timedelta(minutes=95)
        queries={
          'candles':("SELECT * FROM candles WHERE exchange=ANY($1::text[]) AND kind IN ('trade','mark') AND interval_s=300 AND ts>=$2",[list(EXCHANGES),start]),
          'open_interest':("SELECT * FROM open_interest WHERE exchange=ANY($1::text[]) AND ts>=$2",[list(EXCHANGES),start-timedelta(minutes=2)]),
          'funding_ratios':("SELECT * FROM funding_ratios WHERE exchange=ANY($1::text[]) AND ts>=$2",[list(EXCHANGES),start-timedelta(hours=24)]),
          'volume_profiles':("SELECT * FROM volume_profiles WHERE exchange=ANY($1::text[]) AND ts>=$2",[list(EXCHANGES),start]),
          'contract_specs':("SELECT DISTINCT ON(exchange) * FROM contract_specs ORDER BY exchange,effective_at DESC",[]),
          'maintenance_margin_brackets':("SELECT b.* FROM maintenance_margin_brackets b WHERE effective_at=(SELECT max(effective_at) FROM maintenance_margin_brackets x WHERE x.exchange=b.exchange)",[]),
          'leverage_parameters':("SELECT * FROM leverage_parameters",[]),
          'ingestion_checkpoints':("SELECT * FROM ingestion_checkpoints WHERE exchange=ANY($1::text[])",[list(EXCHANGES)]),
        }
        async with source.acquire() as src:
            async with src.transaction(readonly=True,isolation='repeatable_read'):
                for table,(sql,args) in queries.items():
                    rows=await src.fetch(sql,*args)
                    if rows:
                        columns=','.join(rows[0].keys());parameters=','.join('$'+str(i+1) for i in range(len(rows[0])))
                        await p.executemany(f'INSERT INTO {table}({columns}) VALUES({parameters}) ON CONFLICT DO NOTHING',[tuple(r.values()) for r in rows])
        print('PASS real CEX sample copied; no input table writes on production',flush=True)
        restricted=await asyncpg.connect(target);await codec(restricted)
        await restricted.execute('SET ROLE btc_liq_cex_worker')
        for table in ('hl_accounts','hl_snapshots','learning_control','learning_forecasts','candles','open_interest','observed_liquidations','model_checkpoints'):
            try:await restricted.execute('DELETE FROM '+table+' WHERE false')
            except asyncpg.InsufficientPrivilegeError:pass
            else:raise AssertionError('Worker can write protected table '+table)
        print('PASS restricted worker cannot write Hyperliquid, learning, legacy states or raw inputs',flush=True)
        for exchange in EXCHANGES:
            n=await cex_worker.advance(restricted,exchange);assert n>0,(exchange,n)
        assert await p.fetchval('SELECT count(*) FROM cex_checkpoints')==len(EXCHANGES)*len(SCENARIOS)
        frozen=await p.fetch('SELECT exchange,scenario,ts,md5(matrix) digest FROM cex_checkpoints ORDER BY exchange,scenario')
        for exchange in EXCHANGES:assert await cex_worker.advance(restricted,exchange)==0
        assert frozen==await p.fetch('SELECT exchange,scenario,ts,md5(matrix) digest FROM cex_checkpoints ORDER BY exchange,scenario')
        print('PASS all five scenarios, native observations, deduplication and restart cursors',flush=True)
        for r in await p.fetch('SELECT * FROM cex_checkpoints'):
            state=State.restore(dict(r['meta']),r['matrix'])
            assert np.isfinite(state.exposure).all() and np.all(state.exposure>=0)
            unit=.01 if r['exchange']=='okx' else 1.
            assert np.all(state.exposure.sum(axis=(1,2))<=state.prior_oi*unit+1e-6)
            if r['exchange']=='okx':assert state.exposure[:,4].sum()==0
        api.p=p;api._heatmap_cache.clear()
        for scenario in ('baseline',*SCENARIOS):
            if scenario=='baseline':continue
            for lookback,bucket in [('24h',25),('3d',50),('7d',100),('14d',200),('30d',200)]:
                d=await api.build_heatmap(lookback,bucket,cex_model=scenario)
                assert d['matrix'] and d['profile'] and d['cex_model']==scenario
                assert d['normalization_p98']==significant_norm([r[2] for r in d['matrix']])
                assert all(t%(d['time_resolution_s']*1000)==0 for t in d['times'])
                assert all(t[0]%(d['time_resolution_s']*1000)==0 for t in d['candles'])
            d=await api.build_heatmap(exchanges='okx',leverage='125',cex_model=scenario)
            assert not d['profile'] and not d['matrix']
        try:await api.heatmap(source='hyperliquid',cex_model='sharp')
        except Exception as e:assert getattr(e,'status_code',None)==422
        else:raise AssertionError('CEX scenario leaked into Hyperliquid')
        print('PASS scenario filters, all lookbacks/buckets, native caps, CEX normalization and source isolation',flush=True)
        # Reproduce the original engine's first 150 real intervals without writes.
        async with source.acquire() as c:
            async with c.transaction(readonly=True):
                a=Adapter('binance',None,c);state,n=await process(c,a,None,write=False)
                row=await c.fetchrow('SELECT * FROM heatmap_snapshots WHERE exchange=$1 AND ts=$2 AND model_version=$3','binance',db.utc(state.last_ts),VERSION)
                assert row is not None
                np.testing.assert_allclose(state.exposure,decode_array(row['matrix'],state.exposure.shape),rtol=2e-5,atol=1e-8)
                reference=State.empty(state.buckets[0]*2,'reference');reference.buckets=state.buckets.copy();reference.exposure=np.zeros_like(state.exposure)
                last=0;count=0
                while count<state.steps:
                    batch=await cex_worker.inputs(c,'binance',last,state.last_ts)
                    assert batch
                    for item in batch:
                        if count>=state.steps:break
                        cex_worker.apply(reference,item);last=reference.last_ts;count+=1
                np.testing.assert_allclose(reference.exposure,state.exposure,rtol=2e-12,atol=1e-12)
        print('PASS original 150-interval real replay and independent captured-input reference parity',flush=True)
        # Synthetic retention-only fixtures stay inside this disposable database.
        anchor_time=datetime.now(timezone.utc)-timedelta(days=37)
        matrix=encode_array(np.zeros((2,5,1)))
        for scenario in SCENARIOS:
            await p.execute('INSERT INTO cex_snapshots VALUES($1,$2,$3,40000,1,80000,$4,$5)',
              anchor_time,'binance',scenario,matrix,{'checkpoint_meta':{'steps':0},'chain_digest':''})
        await p.execute("INSERT INTO cex_inputs VALUES($1,'binance',$2,'fixture')",anchor_time-timedelta(minutes=5),b'fixture')
        await cex_worker.retain(restricted)
        assert await p.fetchval('SELECT count(*) FROM cex_snapshots WHERE ts=$1',anchor_time)==len(SCENARIOS)
        assert not await p.fetchval('SELECT EXISTS(SELECT 1 FROM cex_inputs WHERE ts<$1)',anchor_time)
        print('PASS bounded research retention preserves exact replay anchors',flush=True)
        # Simulate single-interval live batches, where every interval is an
        # encoded checkpoint. Cadence thinning must preserve complete anchors.
        for age in (1,8):
            base=datetime.fromtimestamp((int(time.time())-age*86400)//21600*21600,timezone.utc)
            for minute in (5,10,15,60):
                for scenario in SCENARIOS:
                    await p.execute('INSERT INTO cex_snapshots VALUES($1,$2,$3,40000,1,80000,$4,$5)',
                      base+timedelta(minutes=minute),'binance',scenario,matrix,
                      {'checkpoint_meta':{'steps':0},'chain_digest':''})
            await cex_worker.retain(restricted)
            for minute,expected in ((5,5),(10,1 if age==1 else 0),(15,1),(60,5)):
                assert await p.fetchval('SELECT count(*) FROM cex_snapshots WHERE ts=$1 AND exchange=$2',
                  base+timedelta(minutes=minute),'binance')==expected,(age,minute)
        print('PASS live-batch anchor thinning preserves complete six-hour anchors and native/hourly cadence',flush=True)
        print('ALL CEX RESEARCH CHECKS PASSED',flush=True)
    finally:
        if restricted:await restricted.close()
        await p.close();await source.close()
        await admin.execute('DROP DATABASE '+name);await admin.close()


if __name__=='__main__':asyncio.run(main())
