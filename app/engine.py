import asyncio
import logging
import signal
import time
import numpy as np
import aiohttp
from app import db
from app import learning
from app import hyperliquid
from app.config import BACKFILL_DAYS, EXCHANGES, LEVERAGES, VERSION
from app.exchanges import Adapter
from app.model import State, WEIGHTS, encode_array, calibration_proposal

log=logging.getLogger(__name__)


async def history(a):
    try:
        await a.metadata()
    except Exception as e:
        log.warning('%s metadata %s',a.name,e)
        await db.coverage(a.p,a.name,'specifications','error',{'error':str(e)})
    # OI first determines the usable replay horizon, independently of candle availability.
    try:
        await a.backfill_oi(BACKFILL_DAYS)
    except Exception as e:
        await db.coverage(a.p,a.name,'historical_open_interest','error',{'error':str(e)})
    for kind,interval in [('trade',300),('mark',300)]:
        try:
            await a.backfill_candles(kind,interval,BACKFILL_DAYS)
        except Exception as e:
            log.warning('%s backfill %s %s %s',a.name,kind,interval,e)
            await db.coverage(a.p,a.name,f'backfill_{kind}_{interval}','error',{'error':str(e)})
    await a.p.execute("UPDATE open_interest o SET usd=o.btc*m.c FROM candles m WHERE o.exchange=$1 AND m.exchange=o.exchange AND m.kind='mark' AND m.interval_s=300 AND m.ts+interval '300 seconds'=o.ts AND o.usd IS NULL",a.name)
    # Historical funding is actually reported settlements; do not manufacture ratios.
    try:
        start=int((time.time()-BACKFILL_DAYS*86400)*1000)
        if a.name=='binance':
            rows=await a.get('/fapi/v1/fundingRate',symbol='BTCUSDT',startTime=start,limit=1000)
            for r in rows:
                await a.metric(int(r['fundingTime']),'funding',float(r['fundingRate']),r)
        elif a.name=='bybit':
            rows=(await a.get('/v5/market/funding/history',category='linear',symbol='BTCUSDT',startTime=start,endTime=int(time.time()*1000),limit=200))['list']
            for r in rows:
                await a.metric(int(r['fundingRateTimestamp']),'funding',float(r['fundingRate']),r)
        else:
            rows=await a.get('/api/v5/public/funding-rate-history',instId='BTC-USDT-SWAP',limit=100)
            for r in rows:
                await a.metric(int(r['fundingTime']),'funding',float(r.get('realizedRate') or r['fundingRate']),r)
        await db.coverage(a.p,a.name,'funding_history','available',{'rows':len(rows),'not_extrapolated':True})
    except Exception as e:
        await db.coverage(a.p,a.name,'funding_history','error',{'error':str(e)})
    await db.coverage(a.p,a.name,'historical_trades','limited',{'reason':'Only live trades collected; historical 5m entry distribution uses explicitly flagged weighted typical-price approximation. No synthetic aggregate trades.'})


async def minute_history(a,core):
    await core
    for kind in ('trade','mark'):
        try:
            await a.backfill_candles(kind,60,BACKFILL_DAYS)
        except Exception as e:
            await db.coverage(a.p,a.name,f'backfill_{kind}_60','error',{'error':str(e)})


async def poll(a):
    n=0
    while True:
        try:
            await a.metrics()
            for kind,interval in [('trade',300),('mark',300),('trade',60),('mark',60)]:
                # Inspect the old high-water mark before adding fresh candles;
                # otherwise a long outage would be hidden by the newest page.
                await a.repair_candles(kind,interval)
                rows=await a.candle_page(kind,interval,int(time.time()*1000),limit=10)
                await a.store_candles(rows,kind,interval)
            if n>0 and n%20==0:
                cp=await a.p.fetchval('SELECT ts FROM model_checkpoints WHERE exchange=$1',a.name)
                if cp and time.time()-cp.timestamp()>900:
                    await a.backfill_oi(BACKFILL_DAYS)
            if n%240==0:
                await a.metadata()
            await db.coverage(a.p,a.name,'rest','live',{'last_poll':time.time()})
        except Exception as e:
            log.warning('%s poll %s',a.name,e)
            await db.coverage(a.p,a.name,'rest','error',{'error':str(e)})
        n+=1
        await asyncio.sleep(45)


async def load_state(p,exchange):
    r=await p.fetchrow('SELECT * FROM model_checkpoints WHERE exchange=$1',exchange)
    if r:
        if r['model_version']!=VERSION:
            raise RuntimeError('model version changed; explicit replay/migration required')
        return State.restore(r['meta'],r['matrix'])
    return None


async def process(p,a,state=None,write=True):
    last=state.last_ts if state else 0
    # Each OI boundary must be observed, within 70s, and bracket this candle.
    rows=await p.fetch('''SELECT c.*,m.l ml,m.h mh,m.c mc,p.native previous_oi,q.native current_oi,
    extract(epoch FROM p.ts) p_ts,extract(epoch FROM q.ts) q_ts
    FROM candles c JOIN candles m ON m.exchange=c.exchange AND m.ts=c.ts AND m.kind='mark' AND m.interval_s=300
    JOIN LATERAL (SELECT * FROM open_interest o WHERE o.exchange=c.exchange AND o.ts<=c.ts+interval '5 seconds' AND o.ts>=c.ts-interval '70 seconds' ORDER BY o.ts DESC LIMIT 1) p ON true
    JOIN LATERAL (SELECT * FROM open_interest o WHERE o.exchange=c.exchange AND o.ts<=c.ts+interval '305 seconds' AND o.ts>=c.ts+interval '230 seconds' ORDER BY o.ts DESC LIMIT 1) q ON true
    WHERE c.exchange=$1 AND c.kind='trade' AND c.interval_s=300 AND c.complete=true AND c.ts>to_timestamp($2)-interval '300 seconds'
    AND c.ts+interval '300 seconds'<=now()
    AND NOT EXISTS(SELECT 1 FROM ingestion_checkpoints ic WHERE ic.exchange=c.exchange AND (ic.feed LIKE 'repair_%' OR ic.feed='backfill_oi') AND ic.cursor->>'done'='false')
    ORDER BY c.ts LIMIT 150''',a.name,float(last))
    pending=[]
    if not rows:
        return state,0
    begin=rows[0]['ts'];finish=rows[-1]['ts']+__import__('datetime').timedelta(seconds=300)
    metrics=await p.fetch("SELECT ts,kind,value FROM funding_ratios WHERE exchange=$1 AND ts>=$2::timestamptz-interval '24 hours' AND ts<=$3 ORDER BY ts",a.name,begin,finish)
    leverage_rows=await p.fetch('SELECT side,effective_at,weights FROM leverage_parameters WHERE exchange=$1 AND effective_at<=$2 ORDER BY effective_at',a.name,finish)
    volume_rows=await p.fetch('SELECT ts,price,volume_native v,buy_native buy FROM volume_profiles WHERE exchange=$1 AND ts>=$2 AND ts<$3',a.name,begin,finish)
    volume_map={}
    for v in volume_rows:
        volume_map.setdefault(v['ts'],[]).append(v)
    if not hasattr(a,'model_brackets') or time.monotonic()-a.model_brackets[0]>3600:
        bracket_rows=await p.fetch('SELECT floor,cap,mmr,deduction FROM maintenance_margin_brackets WHERE exchange=$1 AND effective_at=(SELECT max(effective_at) FROM maintenance_margin_brackets WHERE exchange=$1) ORDER BY tier',a.name)
        a.model_brackets=(time.monotonic(),[dict(b) for b in bracket_rows])
    brackets=a.model_brackets[1]
    for r in rows:
        ts=int(r['ts'].timestamp())+300
        if ts<=last or float(r['q_ts']-r['p_ts'])<230:
            continue
        if state is None:
            state=State.empty(r['mc'])
        known=[x for x in metrics if x['ts'].timestamp()<=ts]
        funds=[x['value'] for x in known if x['kind']=='funding']
        funding=funds[-1] if funds else None
        ratios=[x['value'] for x in known if x['kind']=='top_position_long_fraction' and x['ts'].timestamp()>=ts-900]
        change=ratios[-1]-ratios[-2] if len(ratios)>=2 else None
        weights=[]
        for side in (0,1):
            effective=[x['weights'] for x in leverage_rows if x['side']==side and x['effective_at'].timestamp()<=ts]
            w=np.array(effective[-1] if effective else WEIGHTS.copy())
            w[np.array(LEVERAGES)>a.max_leverage]=0
            weights.append(w/w.sum())
        # Require observed trade volume to cover at least 95% of the candle volume.
        trades=volume_map.get(r['ts'],[])
        traded=sum(t['v'] for t in trades)
        profile=[(t['price'],t['v']) for t in trades] if r['volume']>0 and .95<=traded/r['volume']<=1.05 else None
        buy_ratio=r['buy_volume']/r['volume'] if r['buy_volume'] is not None and r['volume'] else None
        if profile and traded:
            buy_ratio=sum(t['buy'] for t in trades)/traded
        entry=(r['h']+r['l']+2*r['c'])/4
        audit=state.step(ts,entry,r['h'],r['l'],r['c'],r['ml'],r['mh'],r['mc'],
            r['current_oi'],r['previous_oi'],r['volume'],a.contract_btc,buy_ratio,change,funding,profile,brackets,weights,
            fee=0. if a.name=='binance' else .0006)
        audit['input_provenance']={'oi_start_ts':float(r['p_ts']),'oi_end_ts':float(r['q_ts']),'contract_btc':a.contract_btc,'mark_price_trigger':True}
        audit['gross_opening_admissible_native']=[max(0.,r['current_oi']-r['previous_oi']),max(0.,r['current_oi']-r['previous_oi'])+max(0.,(r['volume']-abs(r['current_oi']-r['previous_oi']))/2)]
        audit['risk_bracket_time_assumption']='latest public brackets applied as scenario to historical cohorts; not historically verified'
        audit['missing_signals']={'taker':buy_ratio is None,'position_ratio':change is None,'funding':funding is None}
        if write:
            sides=state.exposure.sum(axis=(1,2))*state.last_mark
            pending.append({'ts':state.last_ts,'matrix':encode_array(state.exposure),'audit':audit,
                            'long':float(sides[0]),'short':float(sides[1]),'mark':state.last_mark,
                            'tiers':state.exposure.sum(axis=2).copy()})
        last=state.last_ts
        await asyncio.sleep(0)  # Yield to live feeds between historical cohorts.
    if write and pending:
        await save_batch(p,a.name,state,pending)
    return state,len(rows)


async def save_batch(p,exchange,state,records):
    # All five-minute histories and the final cursor commit atomically per bounded batch.
    snapshots=[];cllds=[];tiers=[];events=[]
    for r in records:
        ts=db.utc(r['ts']);long,short=r['long'],r['short']
        snapshots.append((ts,exchange,VERSION,float(state.buckets[0]),len(state.buckets),r['mark'],r['matrix'],r['audit']))
        cllds.append((ts,exchange,long,short,long-short,(long-short)/max(1.,long+short)))
        tiers.extend((ts,exchange,side,lev,float(r['tiers'][side,k]),float(r['tiers'][side,k]*r['mark'])) for side in (0,1) for k,lev in enumerate(LEVERAGES))
        if time.time()-r['ts']<900:
            events.append(('heatmap',{'exchange':exchange,'ts':r['ts']*1000,'clld_usd':long-short,'predicted_swept_usd':sum(r['audit']['swept_btc'])*r['mark']}))
    async with p.acquire() as c:
        async with c.transaction():
            await c.executemany('INSERT INTO heatmap_snapshots VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING',snapshots)
            await c.executemany('INSERT INTO clld_history VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING',cllds)
            await c.executemany('INSERT INTO leverage_tier_exposure VALUES($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING',tiers)
            await c.execute('INSERT INTO model_checkpoints VALUES($1,$2,$3,$4,$5) ON CONFLICT(exchange) DO UPDATE SET model_version=excluded.model_version,ts=excluded.ts,meta=excluded.meta,matrix=excluded.matrix',exchange,VERSION,db.utc(state.last_ts),__import__('json').loads(state.checkpoint()),records[-1]['matrix'])
            if events:
                await c.executemany('INSERT INTO stream_events(kind,payload) VALUES($1,$2)',events)
    for r in records:
        if r['ts']%3600==0:
            window=db.utc(r['ts'])
            observed=await p.fetchval("SELECT coalesce(sum(usd),0) FROM observed_liquidations WHERE exchange=$1 AND ts>=$2::timestamptz-interval '1 hour' AND ts<$2",exchange,window)
            predicted=await p.fetchval("SELECT coalesce(sum(((audit->'swept_btc'->>0)::float+(audit->'swept_btc'->>1)::float)*mark),0) FROM heatmap_snapshots WHERE exchange=$1 AND ts>$2::timestamptz-interval '1 hour' AND ts<=$2",exchange,window)
            await p.execute('INSERT INTO calibration_history VALUES(now(),$1,$2,$2,$3,$4,false,$5,$6) ON CONFLICT DO NOTHING',exchange,state.alpha,float(observed),float(predicted),'frozen: censored observations, unknown account sizes; insufficient independent validation',window)
            await p.execute("INSERT INTO model_evaluations SELECT $1,$2,$3,CASE WHEN $1::timestamptz-interval '1 hour'>=created_at THEN 'prospective_holdout' ELSE 'historical_replay_lower_bound' END,$4::float8,$5::float8,abs($4::float8-$5::float8),false,'Reported events are censored; error is descriptive and ineligible for fitting' FROM model_versions WHERE version=$3 ON CONFLICT DO NOTHING",window,exchange,VERSION,float(observed),float(predicted))


async def model_loop(p,a,history_task):
    # Replay only after historical pages arrive; never initialize past states from future OI.
    await history_task
    state=await load_state(p,a.name)
    while True:
        try:
            state,n=await process(p,a,state)
            if state:
                await db.coverage(p,a.name,'model','live' if time.time()-state.last_ts<900 else 'warming',{'steps':state.steps,'last_ts':state.last_ts,'alpha':state.alpha,'uncertainty':'uncalibrated structural priors'},last=db.utc(state.last_ts))
            else:
                await db.coverage(p,a.name,'model','warming',{'reason':'Waiting for paired native OI, closed trade and mark candles'})
            # Project *a copy* through live mark extrema. Committed historical checkpoints
            # only advance with closed intervals; provisional state is recomputed, not swept twice.
            if state and n<150:
                path=await p.fetchrow("SELECT min(l) lo,max(h) hi FROM candles WHERE exchange=$1 AND kind='live_mark' AND ts>=$2",a.name,db.utc(state.last_ts))
                live=await p.fetchrow('SELECT * FROM live_marks WHERE exchange=$1',a.name)
                if live and path['lo'] is not None:
                    copy=State.restore(state.checkpoint(),encode_array(state.exposure))
                    copy.sweep(path['lo'],path['hi'])
                    profile=copy.exposure.sum(axis=1)*live['price']
                    indices=np.where(profile.sum(axis=0)>1)[0]
                    await db.event(p,'profile',{'exchange':a.name,'ts':int(live['ts'].timestamp()*1000),'base_ts':state.last_ts*1000,'mark':live['price'],'grid_start':float(state.buckets[0]),'width':25,'buckets':[[int(i),round(float(profile[0,i]),2),round(float(profile[1,i]),2)] for i in indices]})
            await asyncio.sleep(.05 if n==150 else 10)
        except Exception:
            log.exception('%s model',a.name)
            try:
                state=await load_state(p,a.name)
            except Exception:
                pass
            await asyncio.sleep(15)


async def maintenance(p):
    while True:
        await asyncio.sleep(300)
        # Apache-only core supports drop_chunks; proprietary policies/compression are absent.
        for table,age in [('candles','35 days'),('open_interest','35 days'),('funding_ratios','35 days'),
                          ('aggregate_trades','6 hours'),('volume_profiles','35 days'),('observed_liquidations','35 days'),
                          ('hl_snapshots','35 days'),('heatmap_snapshots','35 days'),('leverage_tier_exposure','35 days'),('clld_history','35 days')]:
            await p.execute('SELECT drop_chunks($1::regclass,older_than=>$2::interval)',table,__import__('datetime').timedelta(hours=6) if age=='6 hours' else __import__('datetime').timedelta(days=35))
        await p.execute("DELETE FROM stream_events WHERE ts<now()-interval '10 minutes'")
        await p.execute("DELETE FROM calibration_history WHERE ts<now()-interval '35 days'")
        await p.execute("DELETE FROM learning_feed_windows WHERE ts<now()-interval '35 days'")
        await p.execute("DELETE FROM learning_event_receipts WHERE ts<now()-interval '35 days'")
        await p.execute("DELETE FROM learning_forecasts WHERE target_end<now()-interval '90 days'")
        await p.execute("DELETE FROM learning_models WHERE created_at<now()-interval '365 days' AND state NOT IN ('candidate','champion')")
        await p.execute("DELETE FROM learning_decisions WHERE ts<now()-interval '365 days'")
        # Soft disk budget: stop ingestion through supervisor restart instead of filling host.
        size=await p.fetchval('SELECT pg_database_size(current_database())')
        if size>3*1024**3 or __import__('shutil').disk_usage('/srv').free<4*1024**3:
            raise RuntimeError('Project DB exceeded 3GiB soft budget; operator action required')
        await db.coverage(p,'system','database','healthy',{'bytes':size,'soft_budget_bytes':3*1024**3})


async def main():
    p=await db.pool()
    await db.migrate(p)
    # Pause ingestion at startup if the project/host storage guard is exceeded.
    import shutil
    while await p.fetchval('SELECT pg_database_size(current_database())')>3*1024**3 or shutil.disk_usage('/srv').free<4*1024**3:
        await db.coverage(p,'system','storage_budget','paused',{'reason':'DB >3GiB or filesystem free <4GiB; ingestion paused without deleting data'})
        await asyncio.sleep(60)
    await p.execute('INSERT INTO model_versions(version,parameters) VALUES($1,$2) ON CONFLICT DO NOTHING',VERSION,{'alpha':.04,'leverage_prior':WEIGHTS.tolist(),'calibration':'frozen pending OOS evidence','OI':'one-sided native'})
    tasks=[]
    current=asyncio.current_task()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM,current.cancel)
    async with aiohttp.ClientSession(headers={'User-Agent':'btc-liq-heatmap/1.0 public-research'},connector=aiohttp.TCPConnector(limit=8)) as session:
        for name in EXCHANGES:
            a=Adapter(name,session,p)
            h=asyncio.create_task(history(a))
            tasks.extend([h,asyncio.create_task(minute_history(a,h)),asyncio.create_task(poll(a)),asyncio.create_task(a.stream()),asyncio.create_task(model_loop(p,a,h))])
        tasks.append(asyncio.create_task(maintenance(p)))
        tasks.append(asyncio.create_task(learning.loop(p)))
        tasks.append(asyncio.create_task(hyperliquid.loop(p)))
        try:
            await asyncio.gather(*tasks)
        finally:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
            await p.close()

if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    import uvloop
    uvloop.run(main())
