"""Bounded CEX-only replay. Never starts feeds, Hyperliquid or the learner."""
import asyncio
from datetime import timedelta
import hashlib
import json
import logging
import shutil
import signal
import sys
import time
import zlib
import asyncpg
import numpy as np
from app import db
from app.config import DATABASE_URL,EXCHANGES,LEVERAGES
from app.model import State,encode_array
from app.cex_scenarios import SCENARIOS,specification,significant_norm

log=logging.getLogger(__name__)
ROLE='btc_liq_cex_worker'
TABLES=('cex_runs','cex_inputs','cex_checkpoints','cex_snapshots')


async def connect():
    c=await asyncpg.connect(DATABASE_URL,command_timeout=60)
    await c.set_type_codec('jsonb',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')
    await c.execute('SET ROLE '+ROLE)
    return c


async def inputs(c,exchange,last,cutoff):
    # Same native-OI pairing and entry-profile eligibility as the legacy engine.
    rows=await c.fetch('''SELECT c.*,m.l ml,m.h mh,m.c mc,p.native previous_oi,q.native current_oi,
      extract(epoch FROM p.ts) p_ts,extract(epoch FROM q.ts) q_ts
      FROM candles c JOIN candles m ON m.exchange=c.exchange AND m.ts=c.ts AND m.kind='mark' AND m.interval_s=300
      JOIN LATERAL (SELECT * FROM open_interest o WHERE o.exchange=c.exchange AND o.ts<=c.ts+interval '5 seconds'
       AND o.ts>=c.ts-interval '70 seconds' ORDER BY o.ts DESC LIMIT 1) p ON true
      JOIN LATERAL (SELECT * FROM open_interest o WHERE o.exchange=c.exchange AND o.ts<=c.ts+interval '305 seconds'
       AND o.ts>=c.ts+interval '230 seconds' ORDER BY o.ts DESC LIMIT 1) q ON true
      WHERE c.exchange=$1 AND c.kind='trade' AND c.interval_s=300 AND c.complete
       AND c.ts>to_timestamp($2)-interval '300 seconds' AND c.ts+interval '300 seconds'<=to_timestamp($3)
       AND NOT EXISTS(SELECT 1 FROM ingestion_checkpoints ic WHERE ic.exchange=c.exchange
        AND (ic.feed LIKE 'repair_%' OR ic.feed='backfill_oi') AND ic.cursor->>'done'='false')
      ORDER BY c.ts LIMIT 60''',exchange,float(last),float(cutoff))
    if not rows:return []
    begin=rows[0]['ts'];finish=rows[-1]['ts']+timedelta(seconds=300)
    metrics=await c.fetch("SELECT ts,kind,value FROM funding_ratios WHERE exchange=$1 AND ts>=$2::timestamptz-interval '24 hours' AND ts<=$3 ORDER BY ts",exchange,begin,finish)
    profiles=await c.fetch('SELECT ts,price,volume_native v,buy_native buy FROM volume_profiles WHERE exchange=$1 AND ts>=$2 AND ts<$3 ORDER BY ts,price',exchange,begin,finish)
    params=await c.fetch('SELECT side,effective_at,weights FROM leverage_parameters WHERE exchange=$1 AND effective_at<=$2 ORDER BY effective_at',exchange,finish)
    spec=await c.fetchrow('SELECT * FROM contract_specs WHERE exchange=$1 ORDER BY effective_at DESC LIMIT 1',exchange)
    unit=spec['contract_btc'] if spec else (.01 if exchange=='okx' else 1.)
    max_lev=float(spec['spec'].get('model_max_leverage',100 if exchange=='okx' else 125)) if spec else (100 if exchange=='okx' else 125)
    brackets=[dict(r) for r in await c.fetch('''SELECT floor,cap,mmr,deduction FROM maintenance_margin_brackets WHERE exchange=$1
      AND effective_at=(SELECT max(effective_at) FROM maintenance_margin_brackets WHERE exchange=$1) ORDER BY tier''',exchange)]
    by_time={}
    for v in profiles:by_time.setdefault(v['ts'],[]).append(v)
    out=[]
    for r in rows:
        ts=int(r['ts'].timestamp())+300
        if ts<=last or float(r['q_ts']-r['p_ts'])<230:continue
        known=[x for x in metrics if x['ts'].timestamp()<=ts]
        funds=[x['value'] for x in known if x['kind']=='funding']
        ratios=[x['value'] for x in known if x['kind']=='top_position_long_fraction' and x['ts'].timestamp()>=ts-900]
        trades=by_time.get(r['ts'],[]);traded=sum(t['v'] for t in trades)
        profile=[(t['price'],t['v']) for t in trades] if r['volume']>0 and .95<=traded/r['volume']<=1.05 else None
        buy=r['buy_volume']/r['volume'] if r['buy_volume'] is not None and r['volume'] else None
        if profile and traded:buy=sum(t['buy'] for t in trades)/traded
        overrides=[]
        for side in (0,1):
            effective=[x['weights'] for x in params if x['side']==side and x['effective_at'].timestamp()<=ts]
            overrides.append(effective[-1] if effective else None)
        kw=dict(ts=ts,entry=(r['h']+r['l']+2*r['c'])/4,high=r['h'],low=r['l'],close=r['c'],
          mark_low=r['ml'],mark_high=r['mh'],mark_close=r['mc'],oi_native=r['current_oi'],
          previous_oi_native=r['previous_oi'],volume_native=r['volume'],contract_btc=unit,
          buy_ratio=buy,ratio_change=ratios[-1]-ratios[-2] if len(ratios)>=2 else None,
          funding=funds[-1] if funds else None,profile=profile,brackets=brackets,fee=0. if exchange=='binance' else .0006)
        out.append(dict(kwargs=kw,max_leverage=max_lev,weight_overrides=overrides,
          provenance={'oi_start_ts':float(r['p_ts']),'oi_end_ts':float(r['q_ts']),
            'risk_brackets':'latest public schedule at input capture; retrospective assumption',
            'metadata_observed_at':spec['effective_at'].isoformat() if spec else None}))
    return out


def apply(state,item):
    kwargs=dict(item['kwargs']);options=SCENARIOS[state.scenario];weights=[]
    for override in item['weight_overrides']:
        w=np.array(override if override is not None else options.weights,dtype=float)
        w[np.array(LEVERAGES)>item['max_leverage']]=0
        if w.sum()<=0:raise ValueError('No feasible leverage prior')
        weights.append((w/w.sum()).tolist())
    audit=state.step(**kwargs,weights=weights)
    audit['input_provenance']=item['provenance']
    return audit


def summarize(state,metrics):
    a=state.exposure
    widths=[]
    for side in a:
        for tier in side:
            if tier.max()<=0:continue
            at=int(tier.argmax());half=tier[at]/2;lo=hi=at
            while lo>0 and tier[lo-1]>=half:lo-=1
            while hi+1<len(tier) and tier[hi+1]>=half:hi+=1
            widths.append((hi-lo+1)*25)
    v=a.sum(axis=(0,1))*state.last_mark;positive=v[v>=1]
    metrics['samples']=metrics.get('samples',0)+1
    metrics['fwhm_sum']=metrics.get('fwhm_sum',0.)+float(np.mean(widths) if widths else 0.)
    metrics['active_buckets_sum']=metrics.get('active_buckets_sum',0.)+float(np.count_nonzero(v>=max(1.,v.max()*.01)))
    metrics['saturation_sum']=metrics.get('saturation_sum',0.)+float(np.mean(positive>=significant_norm(positive)) if len(positive) else 0.)
    metrics['latest_long_btc']=float(a[0].sum());metrics['latest_short_btc']=float(a[1].sum())


async def budget(c):
    size=await c.fetchval('SELECT pg_database_size(current_database())')
    own=await c.fetchval('SELECT sum(pg_total_relation_size(x::regclass)) FROM unnest($1::text[]) x',list(TABLES))
    if size>int(2.5*1024**3) or own>512*1024**2 or shutil.disk_usage('/srv').free<5*1024**3:
        raise RuntimeError('CEX storage guard: DB >2.5GiB, experiment >512MiB or host free <5GiB; legacy feeds untouched')


async def advance(c,exchange):
    run=await c.fetchrow('SELECT * FROM cex_runs WHERE exchange=$1',exchange)
    if not run:
        cutoff=int(time.time())//300*300;start=cutoff-30*86400
        await c.execute('INSERT INTO cex_runs(exchange,start_ts,initial_cutoff,last_ts) VALUES($1,$2,$3,$2)',exchange,start,cutoff)
        run=await c.fetchrow('SELECT * FROM cex_runs WHERE exchange=$1',exchange)
    batch=await inputs(c,exchange,run['last_ts'],int(time.time())//300*300)
    if not batch:
        await c.execute("UPDATE cex_runs SET status=CASE WHEN last_ts>=initial_cutoff-900 THEN 'live' ELSE 'waiting_for_inputs' END,updated_at=now() WHERE exchange=$1",exchange)
        return 0
    states={};stats={}
    for name in SCENARIOS:
        cp=await c.fetchrow('SELECT * FROM cex_checkpoints WHERE exchange=$1 AND scenario=$2',exchange,name)
        states[name]=State.restore(dict(cp['meta']),cp['matrix']) if cp else State.empty(batch[0]['kwargs']['mark_close'],name)
        stats[name]=dict(cp['metrics']) if cp else {}
    frozen=[];snapshots=[];chain=run['input_digest']
    for index,item in enumerate(batch):
        item['checkpoint_boundary']=index==len(batch)-1
        payload=json.dumps(item,sort_keys=True,separators=(',',':'),allow_nan=False).encode();digest=hashlib.sha256(payload).hexdigest()
        chain=hashlib.sha256((chain+digest).encode()).hexdigest()
        ts=item['kwargs']['ts'];frozen.append((db.utc(ts),exchange,zlib.compress(payload,1),digest))
        for name,state in states.items():
            audit=apply(state,item)
            if item['checkpoint_boundary']:
                meta=json.loads(state.checkpoint());meta.pop('buckets')
                audit.update(checkpoint_meta=meta,chain_digest=chain)
            if ts%3600==0:
                summarize(state,stats[name])
            if ts%SCENARIOS[name].snapshot_seconds==0 or item['checkpoint_boundary']:
                snapshots.append((db.utc(ts),exchange,name,float(state.buckets[0]),len(state.buckets),
                  state.last_mark,encode_array(state.exposure),dict(audit,input_digest=digest)))
        await asyncio.sleep(0)
    async with c.transaction():
        await c.executemany('INSERT INTO cex_inputs VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING',frozen)
        await c.executemany('INSERT INTO cex_snapshots VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING',snapshots)
        for name,state in states.items():
            stats[name].update(latest_long_btc=float(state.exposure[0].sum()),latest_short_btc=float(state.exposure[1].sum()))
            await c.execute('''INSERT INTO cex_checkpoints VALUES($1,$2,$3,$4,$5,$6)
              ON CONFLICT(exchange,scenario) DO UPDATE SET ts=excluded.ts,meta=excluded.meta,matrix=excluded.matrix,metrics=excluded.metrics''',
              exchange,name,db.utc(state.last_ts),json.loads(state.checkpoint()),encode_array(state.exposure),stats[name])
        await c.execute('''UPDATE cex_runs SET last_ts=$2,inputs=inputs+$3,profiles=profiles+$4,input_digest=$5,
          updated_at=now(),status=CASE WHEN $2>=initial_cutoff-900 THEN 'live' ELSE 'replaying' END,detail='' WHERE exchange=$1''',
          exchange,batch[-1]['kwargs']['ts'],len(batch),sum(bool(i['kwargs']['profile']) for i in batch),chain)
    return len(batch)


async def retain(c):
    """Retain real coarse history and an exact replay anchor before ledger expiry."""
    # Older lookbacks already render at >=35m. Preserve native recent detail;
    # keep checkpoint anchors even where the rest of the history is decimated.
    await c.execute("""DELETE FROM cex_snapshots WHERE ctid IN (
      SELECT ctid FROM cex_snapshots WHERE scenario='sharp' AND ts<now()-interval '7 days'
      AND mod(extract(epoch FROM ts)::bigint,900)<>0 AND NOT(audit ? 'checkpoint_meta') LIMIT 3000)""")
    for ex in EXCHANGES:
        anchor=await c.fetchval("""SELECT max(ts) FROM cex_snapshots WHERE exchange=$1 AND scenario='reference'
          AND ts<=now()-interval '36 days' AND audit ? 'checkpoint_meta'""",ex)
        if anchor:
            # One extra day keeps a read-only rolling verifier ahead of deletion.
            await c.execute('DELETE FROM cex_inputs WHERE ctid IN (SELECT ctid FROM cex_inputs WHERE exchange=$1 AND ts<=$2 LIMIT 1000)',ex,anchor)
            await c.execute('DELETE FROM cex_snapshots WHERE ctid IN (SELECT ctid FROM cex_snapshots WHERE exchange=$1 AND ts<$2 LIMIT 1000)',ex,anchor)
        else:
            # During the initial bootstrap retain the original input chain until
            # a sufficiently old exact checkpoint anchor has been recorded.
            await c.execute("""DELETE FROM cex_snapshots WHERE ctid IN (SELECT ctid FROM cex_snapshots
              WHERE exchange=$1 AND ts<now()-interval '35 days' AND NOT(audit ? 'checkpoint_meta') LIMIT 1000)""",ex)


async def status(p):
    runs=[dict(r) for r in await p.fetch('SELECT * FROM cex_runs ORDER BY exchange')]
    actual={r['exchange']:r for r in await p.fetch('SELECT exchange,min(ts) first_ts,max(ts) last_ts FROM cex_inputs GROUP BY exchange')}
    for r in runs:
        a=actual.get(r['exchange'])
        r.update(available_input_start=a['first_ts'] if a else None,available_input_end=a['last_ts'] if a else None)
    checkpoints=await p.fetch('SELECT exchange,scenario,ts,metrics FROM cex_checkpoints ORDER BY exchange,scenario')
    reports=[]
    for r in checkpoints:
        m=r['metrics'];n=max(1,m.get('samples',0))
        reports.append(dict(r,mean_peak_fwhm_usd=m.get('fwhm_sum',0)/n,
          mean_active_buckets=m.get('active_buckets_sum',0)/n,mean_saturated_cell_fraction=m.get('saturation_sum',0)/n))
    return {'scenarios':{k:specification(k) for k in SCENARIOS},'runs':runs,'reports':reports,
      'storage_budget_bytes':512*1024**2,'legacy_and_learning_version':'conservation-v1.0',
      'interpretation':'Shape sensitivity, not predictive validation. Narrow history: 5m for seven days, older 15m; alternatives hourly. Exact batch anchors preserve rolling replay.'}


async def main(health=False):
    if not health:
        admin=await db.pool(1)
        try:await db.migrate(admin)
        finally:await admin.close()
    c=await connect()
    try:
        if health:
            return 0 if await c.fetchval("SELECT count(*) FROM cex_runs WHERE status<>'paused' AND updated_at>now()-interval '5 minutes'") else 1
        if not await c.fetchval('SELECT pg_try_advisory_lock(732598344)'):raise RuntimeError('Another CEX worker is active')
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,asyncio.current_task().cancel)
        last_cleanup=0
        while True:
            try:
                if time.monotonic()-last_cleanup>300:
                    await retain(c);last_cleanup=time.monotonic()
                await budget(c);processed=0
                for exchange in EXCHANGES:processed+=await advance(c,exchange)
                await asyncio.sleep(.1 if processed else 15)
            except Exception as e:
                log.exception('CEX experiment worker')
                await c.execute("UPDATE cex_runs SET status='paused',detail=$1,updated_at=now()",str(e)[:500])
                await asyncio.sleep(60)
    except asyncio.CancelledError:return 0
    finally:await c.close()


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO)
    import uvloop
    sys.exit(uvloop.run(main('--health' in sys.argv)))
