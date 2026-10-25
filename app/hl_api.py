"""Separate API representation of reported, sampled positions."""
from datetime import datetime,timezone,timedelta
import numpy as np
from fastapi import HTTPException
from app import hyperliquid
from app.hl_math import METHOD,projection,unpack


def validate(lookback,bucket):
    periods={'24h':1,'3d':3,'7d':7,'14d':14,'30d':30}
    if lookback not in periods or bucket not in (25,50,100,200):
        raise HTTPException(422,'Unsupported lookback or bucket')
    return periods[lookback]


async def latest(p,bucket,positions=None):
    mark=await p.fetchrow("SELECT price,ts FROM live_marks WHERE exchange='hyperliquid'")
    if not mark: return {},[],None,{}
    if positions is None:positions=await hyperliquid.current(p)
    start,a,audit,selected=projection(positions,mark['price'])
    out={}
    for i in np.where(a.sum(axis=0)>0)[0]:
        center=float(np.floor((start+i*25)/bucket)*bucket+bucket/2)
        v=out.setdefault(center,[0.,0.])
        v[0]+=float(a[0,i]*mark['price']); v[1]+=float(a[1,i]*mark['price'])
    prov=[{'exchange':'hyperliquid','snapshot_ts':max((r['observed_at'] for r in selected),default=None),
           'mark_ts':mark['ts'].isoformat(),'mark':mark['price']}]
    return out,prov,mark,audit


async def build(p,lookback,bucket,since=None):
    # One response uses a consistent database snapshot and one account selection.
    async with p.acquire() as connection:
        async with connection.transaction(isolation='repeatable_read',readonly=True):
            return await _build(connection,lookback,bucket,since)


async def _build(p,lookback,bucket,since=None):
    days=validate(lookback,bucket)
    now=datetime.now(timezone.utc); start=now-timedelta(days=days)
    if since is not None:
        try: start=max(start,datetime.fromtimestamp(since/1000,timezone.utc))
        except (ValueError,OverflowError,OSError): raise HTTPException(422,'Invalid since timestamp')
    step=max(300,int(np.ceil(days*86400/288/300))*300)
    rows=await p.fetch('''SELECT DISTINCT ON(time_bucket($1::interval,ts)) ts,mark,grid_start,grid_n,matrix,audit
      FROM hl_snapshots WHERE ts>=$2 ORDER BY time_bucket($1::interval,ts),ts DESC''',timedelta(seconds=step),start)
    states={int(r['ts'].timestamp())//step*step+step:(r['grid_start'],unpack(r['matrix'],r['grid_n']),r['mark']) for r in rows}
    current_positions=await hyperliquid.current(p,now)
    profile,provenance,mark,audit=await latest(p,bucket,current_positions)
    if mark:
        grid,a,live_audit,_=projection(current_positions,mark['price'])
        states[int(now.timestamp())//step*step+step]=(grid,a,mark['price'])
        audit.update(live_audit)
    times=sorted(states); price_set=set(); columns=[]; clld=[]
    for ts in times:
        grid,a,m=states[ts]; col={}
        for i in np.where(a.sum(axis=0)>0)[0]:
            price=float(np.floor((grid+i*25)/bucket)*bucket+bucket/2)
            col[price]=col.get(price,0.)+float(a[:,i].sum()*m)
        columns.append(col); price_set.update(col)
        longs,shorts=a.sum(axis=1)*m
        clld.append({'ts':ts*1000,'long_usd':float(longs),'short_usd':float(shorts),'usd':float(longs-shorts),
                     'normalized':float((longs-shorts)/max(1,longs+shorts))})
    prices=sorted(price_set); pi={v:i for i,v in enumerate(prices)}
    matrix=[[ti,pi[price],round(value,2)] for ti,col in enumerate(columns) for price,value in col.items() if value>=1]
    values=[r[2] for r in matrix]; norm=float(np.percentile(values,98)) if values else 1.
    ps=sorted(profile); cumlong=np.cumsum([profile[x][0] for x in ps[::-1]])[::-1]; cumshort=np.cumsum([profile[x][1] for x in ps])
    profiles=[{'price':x,'long_usd':round(profile[x][0],2),'short_usd':round(profile[x][1],2),
               'cumulative_long_usd':round(float(cumlong[i]),2),'cumulative_short_usd':round(float(cumshort[i]),2)} for i,x in enumerate(ps)]
    candles=await p.fetch('''SELECT time_bucket($1::interval,ts) t,first(o,ts) o,max(h) h,min(l) l,last(c,ts) c
      FROM candles WHERE exchange='hyperliquid' AND kind='trade' AND interval_s=300 AND ts>=$2 GROUP BY t ORDER BY t''',timedelta(seconds=step),start)
    coverage=await hyperliquid.status(p,current_positions)
    return {'source':'hyperliquid','model_version':METHOD,'estimated':False,'sampled':True,'incremental':since is not None,
      'times':[t*1000 for t in times],'prices':prices,'matrix':matrix,
      'candles':[[int((r['t'].timestamp()+step)*1000),r['o'],r['c'],r['l'],r['h']] for r in candles],
      'candle_exchange':'hyperliquid','profile':profiles,'clld':clld,'mark':mark['price'] if mark else None,
      'normalization_p98':norm,'time_resolution_s':step,'bucket':bucket,'provenance':provenance,'sample':coverage,
      'uncertainty':{'status':'reported thresholds, incomplete staggered sample','confidence_interval':False},
      'coverage_note':'Sampled Hyperliquid BTC/USDC positions. Not global top 1000. No extrapolation to other exchanges. History begins with collection; candles before that are native price history only.'}
