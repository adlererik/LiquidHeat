import asyncio
from contextlib import asynccontextmanager
from datetime import datetime,timezone,timedelta
import time
import numpy as np
from fastapi import FastAPI,Query,WebSocket,WebSocketDisconnect,HTTPException
from fastapi.responses import JSONResponse
from fastapi.encoders import jsonable_encoder
from app import db
from app import learning
from app import hyperliquid, hl_api
from app.config import EXCHANGES,LEVERAGES,VERSION
from app.model import decode_array,WEIGHTS

p=None
learning_cache=(0,None)


@asynccontextmanager
async def lifespan(app):
    global p
    p=await db.pool(3)
    yield
    await p.close()


app=FastAPI(title='BTC Liquidation Observatory',version='1.2.1',lifespan=lifespan)


def filters(exchanges,leverage):
    ex=exchanges.split(',') if exchanges else list(EXCHANGES)
    lev=[int(x) for x in leverage.split(',')] if leverage else list(LEVERAGES)
    if not ex or len(ex)!=len(set(ex)) or set(ex)-set(EXCHANGES) or not lev or len(lev)!=len(set(lev)) or set(lev)-set(LEVERAGES):
        raise HTTPException(422,'Unknown exchange or leverage filter')
    return ex,[LEVERAGES.index(x) for x in lev]


@app.get('/healthz')
async def health():
    try:
        await p.fetchval('SELECT 1')
        migrated=await p.fetchval("SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version='007')")
        return JSONResponse({'status':'ok' if migrated else 'starting'},status_code=200 if migrated else 503)
    except Exception:
        return JSONResponse({'status':'database_unavailable'},status_code=503)


async def latest_profiles(ex,ks,bucket):
    rows=await p.fetch('''SELECT DISTINCT ON(exchange) * FROM heatmap_snapshots WHERE exchange=ANY($1::text[]) AND model_version=$2 ORDER BY exchange,ts DESC''',ex,VERSION)
    marks=await p.fetch('SELECT * FROM live_marks WHERE exchange=ANY($1::text[])',ex)
    markmap={r['exchange']:r for r in marks}
    out={}
    provenance=[]
    for r in rows:
        a=decode_array(r['matrix'],(2,5,r['grid_n']))[:,ks,:].sum(axis=1)
        # Same first-passage survival model as engine provisional projections.
        path=await p.fetchrow("SELECT min(l) lo,max(h) hi FROM candles WHERE exchange=$1 AND kind='live_mark' AND ts>=$2",r['exchange'],r['ts'])
        if path['lo'] is not None:
            levels=r['grid_start']+np.arange(r['grid_n'])*25
            a[0,levels>=path['lo']]=0
            a[1,levels<=path['hi']]=0
        live=markmap.get(r['exchange'])
        mark=live['price'] if live else r['mark']
        for i in np.where(a.sum(axis=0)>0)[0]:
            price=float(np.floor((r['grid_start']+i*25)/bucket)*bucket+(bucket-25)/2)
            v=out.setdefault(price,[0.,0.])
            v[0]+=float(a[0,i]*mark);v[1]+=float(a[1,i]*mark)
        provenance.append({'exchange':r['exchange'],'snapshot_ts':r['ts'].isoformat(),'mark_ts':live['ts'].isoformat() if live else None,'mark':mark})
    return out,provenance


async def build_heatmap(lookback:str='24h',bucket:int=25,exchanges:str='',leverage:str='',since:int|None=None):
    periods={'24h':1,'3d':3,'7d':7,'14d':14,'30d':30}
    if lookback not in periods or bucket not in (25,50,100,200):
        raise HTTPException(422,'Supported lookbacks: 24h,3d,7d,14d,30d; buckets:25,50,100,200')
    try:
        ex,ks=filters(exchanges,leverage)
    except ValueError:
        raise HTTPException(422,'Invalid leverage')
    days=periods[lookback]
    start=datetime.now(timezone.utc)-timedelta(days=days)
    if since is not None:
        try:
            start=max(start,datetime.fromtimestamp(since/1000,timezone.utc))
        except (ValueError,OverflowError,OSError):
            raise HTTPException(422,'Invalid since timestamp')
    step=max(300,int(np.ceil(days*86400/288/300))*300)
    rows=await p.fetch('''SELECT DISTINCT ON(exchange,time_bucket($1::interval,ts-interval '1 microsecond',TIMESTAMPTZ '1970-01-01')) * FROM heatmap_snapshots
    WHERE exchange=ANY($2::text[]) AND ts>=$3 AND model_version=$4 ORDER BY exchange,time_bucket($1::interval,ts-interval '1 microsecond',TIMESTAMPTZ '1970-01-01'),ts DESC''',timedelta(seconds=step),ex,start,VERSION)
    times=sorted({(int(r['ts'].timestamp())-1)//step*step for r in rows})
    time_index={t:i for i,t in enumerate(times)}
    side_totals=np.zeros((len(times),2))
    if rows:
        grid_lo=np.floor(min(r['grid_start'] for r in rows)/bucket)*bucket
        grid_hi=np.floor(max(r['grid_start']+(r['grid_n']-1)*25 for r in rows)/bucket)*bucket
        bins=int(round((grid_hi-grid_lo)/bucket))+1
        dense=np.zeros((len(times),bins))
        mapping={}
        for r in rows:
            t=(int(r['ts'].timestamp())-1)//step*step
            ti=time_index[t]
            selected=decode_array(r['matrix'],(2,5,r['grid_n']))[:,ks,:].sum(axis=1)
            side_totals[ti]+=selected.sum(axis=1)*r['mark']
            key=(r['grid_start'],r['grid_n'])
            if key not in mapping:
                mapping[key]=np.floor((r['grid_start']+np.arange(r['grid_n'])*25-grid_lo)/bucket).astype(np.int64)
            dense[ti]+=np.bincount(mapping[key],weights=selected.sum(axis=0)*r['mark'],minlength=bins)
            if ti%25==0:
                await asyncio.sleep(0)
        visible=np.any(dense>.01,axis=0)
        prices=(grid_lo+np.where(visible)[0]*bucket+(bucket-25)/2).tolist()
        dense=dense[:,visible]
        xi,yi=np.nonzero(dense>=1)
        values=np.round(dense[xi,yi],2)
        matrix=list(map(list,zip(xi.tolist(),yi.tolist(),values.tolist())))
        norm=float(np.percentile(values,98)) if len(values) else 1.
    else:
        prices=[];matrix=[];norm=1.
    # Use a single reference venue's OHLC; do not invent cross-venue candles.
    candle_exchange=next((e for e in EXCHANGES if e in ex),ex[0])
    candles=await p.fetch('''SELECT time_bucket($1::interval,ts,TIMESTAMPTZ '1970-01-01') t,first(o,ts) o,max(h) h,min(l) l,last(c,ts) c,sum(volume) volume
    FROM candles WHERE exchange=$2 AND kind='trade' AND interval_s=300 AND ts>=$3 GROUP BY t ORDER BY t''',timedelta(seconds=step),candle_exchange,start)
    cs=[[int((r['t'].timestamp()+step)*1000),r['o'],r['c'],r['l'],r['h']] for r in candles]
    # Heatmap timestamp is end-of-bucket (candle end); align both without index interpolation.
    display_times=[(t+step)*1000 for t in times]
    profile,provenance=await latest_profiles(ex,ks,bucket)
    ps=sorted(profile)
    cumlong=np.cumsum([profile[x][0] for x in reversed(ps)])[::-1] if ps else []
    cumshort=np.cumsum([profile[x][1] for x in ps]) if ps else []
    profiles=[{'price':x,'long_usd':round(profile[x][0],2),'short_usd':round(profile[x][1],2),'cumulative_long_usd':round(float(cumlong[i]),2),'cumulative_short_usd':round(float(cumshort[i]),2)} for i,x in enumerate(ps)]
    clld=[{'ts':(t+step)*1000,'long_usd':float(v[0]),'short_usd':float(v[1]),'usd':float(v[0]-v[1]),'normalized':float((v[0]-v[1])/max(1,v.sum()))} for t,v in zip(times,side_totals)]
    mark=float(np.mean([v['mark'] for v in provenance])) if provenance else await p.fetchval('SELECT avg(price) FROM live_marks WHERE exchange=ANY($1::text[])',ex)
    return {'source':'cex','model_version':VERSION,'estimated':True,'incremental':since is not None,'times':display_times,'prices':prices,'matrix':matrix,
            'candles':cs,'candle_exchange':candle_exchange,'profile':profiles,'clld':clld,'mark':mark,
            'normalization_p98':norm,'time_resolution_s':step,'bucket':bucket,'provenance':provenance,
            'uncertainty':{'status':'uncalibrated','alpha_prior_range':[.01,.15],'interval_type':'unidentified private positions; no calibrated confidence interval'},
            'coverage_note':'No synthetic history. Empty ranges mean missing native OI or feeds. Historical time columns use last state per display interval.'}


_heatmap_lock=asyncio.Lock()
_heatmap_cache={}

@app.get('/api/v1/heatmap')
async def heatmap(lookback:str='24h',bucket:int=25,exchanges:str='',leverage:str='',since:int|None=None,source:str='cex'):
    if source not in ('cex','hyperliquid'): raise HTTPException(422,'Unknown source')
    if source=='hyperliquid' and (exchanges or leverage): raise HTTPException(422,'CEX exchange/leverage assumptions do not apply to reported Hyperliquid positions')
    key=(source,lookback,bucket,exchanges,leverage,since,int(time.monotonic()//15))
    async with _heatmap_lock:
        if _heatmap_cache.get('key')==key:
            return _heatmap_cache['response']
        result=await hl_api.build(p,lookback,bucket,since) if source=='hyperliquid' else await build_heatmap(lookback,bucket,exchanges,leverage,since)
        _heatmap_cache.clear()
        response=JSONResponse(jsonable_encoder(result))
        _heatmap_cache.update(key=key,response=response)
        return response


@app.get('/api/v1/magnets')
async def magnets(limit:int=Query(5,ge=1,le=20),bucket:int=100,exchanges:str='',leverage:str='',source:str='cex'):
    if bucket not in (25,50,100,200):
        raise HTTPException(422,'Unsupported bucket')
    if source not in ('cex','hyperliquid'): raise HTTPException(422,'Unknown source')
    if source=='hyperliquid':
        if exchanges or leverage: raise HTTPException(422,'CEX filters do not apply to Hyperliquid')
        profile,prov,_,_=await hl_api.latest(p,bucket)
    else:
        try:
            ex,ks=filters(exchanges,leverage)
        except ValueError:
            raise HTTPException(422,'Invalid leverage')
        profile,prov=await latest_profiles(ex,ks,bucket)
    if not profile:
        return {'source':source,'estimated':source=='cex','sampled':source=='hyperliquid','zones':[]}
    prices=sorted(profile)
    values=np.array([sum(profile[x]) for x in prices])
    # Z score across all buckets in the modeled span, including zeros.
    n=max(1,(prices[-1]-prices[0])//bucket+1)
    mean=float(values.sum()/n)
    sd=max(1.,float(np.sqrt(max(0.,(values**2).sum()/n-mean**2))))
    mark=float(np.mean([v['mark'] for v in prov]))
    ranked=sorted(zip(prices,values),key=lambda x:x[1],reverse=True)
    zones=[]
    for price,value in ranked:
        if any(abs(price-z['price'])<bucket*2 for z in zones):
            continue
        zones.append({'price':price,'usd':round(float(value),2),'side':'long' if profile[price][0]>=profile[price][1] else 'short','intensity':float(value/max(1,values.max())),'z_score':float((value-mean)/sd),'distance_pct':(price/mark-1)*100})
        if len(zones)==limit:
            break
    return {'source':source,'estimated':source=='cex','sampled':source=='hyperliquid','mark':mark,'zones':zones,'interpretation':'Concentration descriptors; no validated predictive attraction to price.'}


@app.get('/api/v1/diagnostics')
async def diagnostics():
    feeds=await p.fetch('SELECT * FROM feed_coverage ORDER BY exchange,feed')
    cps=await p.fetch('SELECT exchange,ts,meta FROM model_checkpoints')
    specs=await p.fetch('SELECT DISTINCT ON(exchange) exchange,effective_at,contract_btc,spec FROM contract_specs ORDER BY exchange,effective_at DESC')
    calibrations=await p.fetch('SELECT DISTINCT ON(exchange) * FROM calibration_history ORDER BY exchange,ts DESC')
    evaluations=await p.fetch('SELECT DISTINCT ON(exchange) * FROM model_evaluations ORDER BY exchange,window_end DESC')
    oi=await p.fetch('SELECT exchange,count(*) samples,min(ts) first_ts,max(ts) last_ts FROM open_interest GROUP BY exchange')
    gaps=await p.fetch("SELECT exchange,kind,interval_s,count(*) samples,min(ts) first_ts,max(ts) last_ts,GREATEST(0,(extract(epoch FROM (max(ts)-min(ts)))/interval_s)::bigint+1-count(*)) missing_intervals FROM candles WHERE kind IN ('trade','mark') GROUP BY exchange,kind,interval_s ORDER BY exchange,kind,interval_s")
    counts=await p.fetch('SELECT exchange,count(*) events,min(ts) first_ts,max(ts) last_ts FROM observed_liquidations GROUP BY exchange')
    recent=await p.fetch('SELECT ts,exchange,side,usd,price_semantics FROM observed_liquidations ORDER BY ts DESC LIMIT 10')
    health={r['exchange']:{'last_ts':r['ts'],'lag_s':max(0,time.time()-r['ts'].timestamp()),'steps':r['meta'].get('steps',0)} for r in cps}
    return {'model_version':VERSION,'estimated':True,'model_health':health,'calibration':{'alpha':.04,'bounds':[.01,.15],'status':'frozen pending independent out-of-sample validation','history_latest':[dict(x) for x in calibrations]},
            'learning':await learning_status(),'hyperliquid':await hyperliquid.status(p),
            'leverage_weights':{'prior':dict(zip(LEVERAGES,WEIGHTS.tolist())),'okx_125x':'unsupported: weight zero, others renormalized','effective_parameters_table':'leverage_parameters'},
            'coverage':[dict(x) for x in feeds],'candle_gaps':[dict(x) for x in gaps],'open_interest':[dict(x) for x in oi],
            'observed_liquidations':[dict(x) for x in counts],'recent_liquidations':[dict(x) for x in recent],
            'contract_specs':[dict(x) for x in specs],'out_of_sample_evaluation':[dict(x) for x in evaluations],'uncertainty':{'status':'uncalibrated structural prior','alpha_sensitivity_range':[.01,.15],'confidence_interval':False,
            'unknown':['entry cohort composition','leverage','account size','cross collateral','voluntary closes','stream losses'],
            'binance_mmr':'unverified .004 baseline; private brackets unavailable','trade_profile':'observed profile only when >=95% candle native volume, otherwise weighted typical price'},
            'server_time':datetime.now(timezone.utc),'database_bytes':await p.fetchval('SELECT pg_database_size(current_database())')}


@app.get('/api/v1/learning')
async def learning_status():
    global learning_cache
    if learning_cache[1] is not None and time.monotonic()-learning_cache[0]<30:return learning_cache[1]
    value=await learning.summary(p)
    learning_cache=(time.monotonic(),value)
    return value


@app.websocket('/ws/stream')
async def stream(ws:WebSocket):
    await ws.accept()
    cursor=await p.fetchval('SELECT coalesce(max(id),0) FROM stream_events')
    await ws.send_json({'type':'hello','model_version':VERSION,'cursor':cursor,'estimated':True})
    try:
        while True:
            events=await p.fetch('SELECT id,kind,payload FROM stream_events WHERE id>$1 ORDER BY id LIMIT 100',cursor)
            for e in events:
                cursor=e['id']
                await asyncio.wait_for(ws.send_json({'id':cursor,'type':e['kind'],'data':e['payload']}),timeout=5)
            if not events:
                await asyncio.wait_for(ws.send_json({'type':'heartbeat','ts':int(time.time()*1000)}),timeout=5)
            await asyncio.sleep(2)
    except (WebSocketDisconnect,RuntimeError,asyncio.TimeoutError):
        pass
