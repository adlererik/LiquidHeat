"""Independent public adapters. REST requests are serialized per venue and paced.
No API keys, alternate region hosts, or access-control workarounds are used.
"""
import asyncio
import hashlib
import json
import logging
import time
import aiohttp
from app import db
from app.feed_audit import FeedAudit

log = logging.getLogger(__name__)


class FeedError(Exception):
    pass


def bybit_one_side(row):
    if 'singleOpenInterest' in row:
        return float(row['singleOpenInterest']), 'singleOpenInterest'
    # Since 2026-06-11 openInterest is both sides. Explicit audited fallback.
    return float(row['openInterest'])/2., 'openInterest/2 (2026-06-11 semantics)'


class Adapter:
    def __init__(self, name, session, pool):
        self.name, self.s, self.p = name, session, pool
        self.base = {'binance':'https://fapi.binance.com','bybit':'https://api.bybit.com','okx':'https://www.okx.com'}[name]
        self.contract_btc = .01 if name == 'okx' else 1.
        self.max_leverage = 100 if name == 'okx' else 125
        self.lock = asyncio.Lock()
        self.next_request = 0.
        self.pending = []

    async def get(self, path, **params):
        async with self.lock:
            for attempt in range(5):
                await asyncio.sleep(max(0, self.next_request-time.monotonic()))
                self.next_request = time.monotonic()+.45
                try:
                    async with self.s.get(self.base+path, params=params,timeout=aiohttp.ClientTimeout(total=20)) as r:
                        if r.status in (418, 429):
                            delay = min(120, max(2**attempt, float(r.headers.get('Retry-After','5'))))
                            self.next_request = time.monotonic()+delay
                            await db.coverage(self.p,self.name,'rest','rate_limited',{'retry_seconds':delay})
                            continue
                        if r.status in (401,403,451):
                            raise FeedError(f'access unavailable: HTTP {r.status} {path}')
                        if r.status >= 500:
                            self.next_request = time.monotonic()+2**attempt
                            continue
                        r.raise_for_status()
                        data = await r.json()
                        if self.name == 'bybit':
                            if data.get('retCode') != 0:
                                raise FeedError(f'Bybit {data.get("retCode")}: {data.get("retMsg")}')
                            return data['result']
                        if self.name == 'okx':
                            if data.get('code') != '0':
                                raise FeedError(f'OKX {data.get("code")}: {data.get("msg")}')
                            return data['data']
                        return data
                except (aiohttp.ClientError, asyncio.TimeoutError):
                    if attempt == 4:
                        raise
                    self.next_request = time.monotonic()+2**attempt
            raise FeedError('rate limit retry budget exhausted')

    async def metadata(self):
        now = db.utc(time.time())
        if self.name == 'binance':
            r = await self.get('/fapi/v1/exchangeInfo')
            spec = next(x for x in r['symbols'] if x['symbol']=='BTCUSDT')
            brackets = []  # signed leverageBracket is not public
        elif self.name == 'bybit':
            r = await self.get('/v5/market/instruments-info',category='linear',symbol='BTCUSDT')
            spec = r['list'][0]
            self.max_leverage = float(spec['leverageFilter']['maxLeverage'])
            risk = await self.get('/v5/market/risk-limit',category='linear',symbol='BTCUSDT')
            brackets = []
            floor = 0.
            for i, b in enumerate(sorted(risk['list'],key=lambda x:float(x['riskLimitValue']))):
                cap = float(b['riskLimitValue'])
                brackets.append((i+1,floor,cap,float(b['maintenanceMargin']),float(b.get('mmDeduction') or 0),b))
                floor = cap
        else:
            r = await self.get('/api/v5/public/instruments',instType='SWAP',instId='BTC-USDT-SWAP')
            spec = r[0]
            self.contract_btc = float(spec['ctVal'])*float(spec.get('ctMult') or 1)
            self.max_leverage = float(spec.get('lever') or 100)
            risk = await self.get('/api/v5/public/position-tiers',instType='SWAP',tdMode='cross',uly='BTC-USDT')
            brackets = []
            # OKX tier boundaries are in contracts. Convert to USD at a documented reference mark.
            mark = (await self.get('/api/v5/public/mark-price',instType='SWAP',instId='BTC-USDT-SWAP'))[0]
            ref = float(mark['markPx'])
            prior_mmr, deduction, prior_cap = 0., 0., 0.
            for b in sorted(risk,key=lambda x:int(x['tier'])):
                floor=float(b['minSz'])*self.contract_btc*ref
                cap=float(b['maxSz'])*self.contract_btc*ref
                mmr=float(b['mmr'])
                deduction += prior_cap*(mmr-prior_mmr)
                raw=dict(b,reference_mark=ref,boundary_unit='contracts; converted USD at metadata time')
                brackets.append((int(b['tier']),floor,cap,mmr,deduction,raw))
                prior_cap,prior_mmr=cap,mmr
        spec['model_max_leverage'] = self.max_leverage
        await self.p.execute('INSERT INTO exchange_metadata(exchange,symbol,coverage) VALUES($1,$2,$3) ON CONFLICT(exchange) DO UPDATE SET coverage=excluded.coverage',self.name,'BTC-USDT-SWAP' if self.name=='okx' else 'BTCUSDT',{'public_only':True,'brackets_public':bool(brackets),'max_leverage':self.max_leverage})
        await self.p.execute('INSERT INTO contract_specs VALUES($1,$2,$3,$4) ON CONFLICT DO NOTHING',self.name,now,self.contract_btc,spec)
        for tier,floor,cap,mmr,deduction,raw in brackets:
            await self.p.execute('INSERT INTO maintenance_margin_brackets VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING',self.name,now,tier,floor,cap,mmr,deduction,raw)
        await db.coverage(self.p,self.name,'specifications','live',{'public_brackets':bool(brackets),'max_leverage':self.max_leverage})

    async def candle_page(self, kind, interval, end_ms, limit=None):
        if self.name == 'binance':
            path = '/fapi/v1/klines' if kind=='trade' else '/fapi/v1/markPriceKlines'
            rows = await self.get(path,symbol='BTCUSDT',interval=f'{interval//60}m',endTime=end_ms,limit=limit or 1000)
            return [(int(r[0]),*[float(x) for x in r[1:5]],float(r[5]) if kind=='trade' else 0.,float(r[9]) if kind=='trade' else None,float(r[7]) if kind=='trade' else None) for r in rows]
        if self.name == 'bybit':
            path='/v5/market/kline' if kind=='trade' else '/v5/market/mark-price-kline'
            rows=(await self.get(path,category='linear',symbol='BTCUSDT',interval=str(interval//60),end=end_ms,limit=limit or 1000))['list']
            return [(int(r[0]),*[float(x) for x in r[1:5]],float(r[5]) if kind=='trade' else 0.,None,float(r[6]) if kind=='trade' else None) for r in rows]
        path='/api/v5/market/history-candles' if kind=='trade' else '/api/v5/market/history-mark-price-candles'
        rows=await self.get(path,instId='BTC-USDT-SWAP',bar=f'{interval//60}m',after=str(end_ms+1),limit=limit or (300 if kind=='trade' else 100))
        return [(int(r[0]),*[float(x) for x in r[1:5]],float(r[5]) if kind=='trade' else 0.,None,float(r[7]) if kind=='trade' else None) for r in rows if r[-1]=='1']

    async def store_candles(self, rows, kind, interval):
        now = time.time()
        rows = [r for r in rows if r[0]/1000+interval <= now]
        if not rows:
            return
        async with self.p.acquire() as c:
            async with c.transaction():
                await c.executemany('''INSERT INTO candles(ts,exchange,kind,interval_s,o,h,l,c,volume,buy_volume,quote_volume) VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11) ON CONFLICT DO NOTHING''',[(db.utc(r[0]/1000),self.name,kind,interval,*r[1:]) for r in rows])
        start, end = min(r[0] for r in rows)/1000,max(r[0] for r in rows)/1000
        gaps=sum(b-a>interval*1000 for a,b in zip(sorted(r[0] for r in rows),sorted(r[0] for r in rows)[1:]))
        await db.coverage(self.p,self.name,f'{kind}_{interval}s','live',{'page_rows':len(rows),'page_internal_gaps':gaps},db.utc(start),db.utc(end))

    async def backfill_candles(self, kind, interval, days):
        feed=f'backfill_{kind}_{interval}'
        cp=await self.p.fetchval('SELECT cursor FROM ingestion_checkpoints WHERE exchange=$1 AND feed=$2',self.name,feed)
        start=(time.time()-days*86400)*1000
        end=int(cp['end_ms']) if cp and not cp.get('done') else int(time.time()*1000)
        if cp and cp.get('done'):
            return
        for _ in range(500):
            rows=await self.candle_page(kind,interval,end)
            if not rows:
                await db.coverage(self.p,self.name,feed,'limited',{'reason':'exchange returned empty historical page','oldest_cursor':end})
                break
            await self.store_candles([r for r in rows if r[0]>=start],kind,interval)
            oldest=min(r[0] for r in rows)
            if oldest<=start:
                await db.checkpoint(self.p,self.name,feed,{'end_ms':oldest-1,'done':True})
                return
            if oldest>=end:
                break
            end=oldest-1
            await db.checkpoint(self.p,self.name,feed,{'end_ms':end,'done':False})
        await db.checkpoint(self.p,self.name,feed,{'end_ms':end,'done':True,'limited':True})

    async def store_oi(self, ts, native, raw, provenance, mark=None):
        await self.p.execute('INSERT INTO open_interest VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING',db.utc(ts/1000),self.name,native,native*self.contract_btc,native*self.contract_btc*mark if mark else None,provenance,raw)
        await db.coverage(self.p,self.name,'open_interest','live',{'one_sided':True,'native_contract_btc':self.contract_btc},db.utc(ts/1000),db.utc(ts/1000))

    async def store_oi_batch(self,rows):
        # One transaction per exchange page, not two commits per sample.
        if not rows:
            return
        async with self.p.acquire() as c:
            async with c.transaction():
                await c.executemany('INSERT INTO open_interest VALUES($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING',
                    [(db.utc(ts/1000),self.name,native,native*self.contract_btc,None,provenance,raw) for ts,native,raw,provenance in rows])
        await db.coverage(self.p,self.name,'open_interest','live',{'one_sided':True,'native_contract_btc':self.contract_btc},
            db.utc(min(r[0] for r in rows)/1000),db.utc(max(r[0] for r in rows)/1000))

    async def backfill_oi(self, days):
        if self.name=='okx':
            end=int(time.time()*1000)
            oldest=end
            for _ in range(16):
                rows=await self.get('/api/v5/rubik/stat/contracts/open-interest-history',instId='BTC-USDT-SWAP',period='5m',end=end,limit=100)
                if not rows:
                    break
                await self.store_oi_batch([(int(r[0]),float(r[1]),{'row':r},'OKX historical native contracts') for r in rows])
                oldest=min(int(r[0]) for r in rows)
                if oldest>=end:
                    break
                end=oldest-1
            await db.coverage(self.p,self.name,'historical_open_interest','limited',{'reason':'latest 1440 5m observations only; older range left blank','oldest_ms':oldest})
            return
        start=(time.time()-days*86400)*1000
        cp=await self.p.fetchval('SELECT cursor FROM ingestion_checkpoints WHERE exchange=$1 AND feed=$2',self.name,'backfill_oi')
        if cp and cp.get('done'):
            model_ts=await self.p.fetchval('SELECT ts FROM model_checkpoints WHERE exchange=$1',self.name)
            if model_ts:
                start=max(start,(model_ts.timestamp()-300)*1000)
            end=int(time.time()*1000)
        else:
            end=int(cp['end_ms']) if cp else int(time.time()*1000)
            if cp and cp.get('target_ms'):
                start=max(start,float(cp['target_ms']))
        for _ in range(100):
            if self.name=='binance':
                rows=await self.get('/futures/data/openInterestHist',symbol='BTCUSDT',period='5m',endTime=end,limit=500)
            else:
                rows=(await self.get('/v5/market/open-interest',category='linear',symbol='BTCUSDT',intervalTime='5min',endTime=end,limit=200))['list']
            if not rows:
                break
            batch=[]
            for r in rows:
                ts=int(r['timestamp'])
                native=float(r['sumOpenInterest']) if self.name=='binance' else bybit_one_side(r)[0]
                provenance='sumOpenInterest' if self.name=='binance' else bybit_one_side(r)[1]
                if ts>=start:
                    batch.append((ts,native,r,provenance))
            await self.store_oi_batch(batch)
            oldest=min(int(r['timestamp']) for r in rows)
            if oldest<=start:
                await db.checkpoint(self.p,self.name,'backfill_oi',{'end_ms':oldest-1,'done':True,'target_ms':start})
                return
            if oldest>=end:
                break
            end=oldest-1
            await db.checkpoint(self.p,self.name,'backfill_oi',{'end_ms':end,'done':False,'target_ms':start})
        await db.checkpoint(self.p,self.name,'backfill_oi',{'end_ms':end,'done':True,'limited':True})

    async def metrics(self):
        now=int(time.time()*1000)
        if self.name=='binance':
            r=await self.get('/fapi/v1/openInterest',symbol='BTCUSDT')
            await self.store_oi(int(r['time']),float(r['openInterest']),r,'openInterest')
            # Recent historical boundaries allow deterministic exact 5m accounting.
            rows=await self.get('/futures/data/openInterestHist',symbol='BTCUSDT',period='5m',limit=3)
            for r in rows:
                await self.store_oi(int(r['timestamp']),float(r['sumOpenInterest']),r,'sumOpenInterest')
            r=await self.get('/fapi/v1/premiumIndex',symbol='BTCUSDT')
            await self.mark(int(r['time']),float(r['markPrice']))
            await self.metric(now,'funding',float(r['lastFundingRate']),r)
            r=await self.get('/futures/data/topLongShortPositionRatio',symbol='BTCUSDT',period='5m',limit=2)
            for v in r:
                await self.metric(int(v['timestamp']),'top_position_long_fraction',float(v['longAccount']),v)
        elif self.name=='bybit':
            r=(await self.get('/v5/market/tickers',category='linear',symbol='BTCUSDT'))['list'][0]
            # ticker single-sided field may not exist on every API rollout; prefer history.
            await self.mark(now,float(r['markPrice']))
            await self.metric(now,'funding',float(r['fundingRate']),r)
            rows=(await self.get('/v5/market/open-interest',category='linear',symbol='BTCUSDT',intervalTime='5min',limit=3))['list']
            for v in rows:
                native,provenance=bybit_one_side(v)
                await self.store_oi(int(v['timestamp']),native,v,provenance,float(r['markPrice']))
            ratios=(await self.get('/v5/market/account-ratio',category='linear',symbol='BTCUSDT',period='5min',limit=2))['list']
            for v in ratios:
                await self.metric(int(v['timestamp']),'account_long_fraction',float(v['buyRatio']),v)
        else:
            rows=await self.get('/api/v5/rubik/stat/contracts/open-interest-history',instId='BTC-USDT-SWAP',period='5m',limit=3)
            for v in rows:
                await self.store_oi(int(v[0]),float(v[1]),{'row':v},'OKX historical native contracts')
            r=(await self.get('/api/v5/public/open-interest',instType='SWAP',instId='BTC-USDT-SWAP'))[0]
            await self.store_oi(int(r['ts']),float(r['oi']),r,'oi contracts')
            r=(await self.get('/api/v5/public/mark-price',instType='SWAP',instId='BTC-USDT-SWAP'))[0]
            await self.mark(int(r['ts']),float(r['markPx']))
            r=(await self.get('/api/v5/public/funding-rate',instId='BTC-USDT-SWAP'))[0]
            if r.get('fundingRate'):
                await self.metric(int(r['ts']),'funding',float(r['fundingRate']),r)
            ratios=await self.get('/api/v5/rubik/stat/contracts/long-short-position-ratio-contract-top-trader',instId='BTC-USDT-SWAP',period='5m',limit=2)
            for v in ratios:
                ratio=float(v[1])
                await self.metric(int(v[0]),'top_position_long_fraction',ratio/(1+ratio),{'row':v})
            flows=await self.get('/api/v5/rubik/stat/taker-volume-contract',instId='BTC-USDT-SWAP',period='5m',unit=0,limit=3)
            for v in flows:
                sell,buy=float(v[1]),float(v[2])
                await self.p.execute("UPDATE candles SET buy_volume=$1 WHERE exchange=$2 AND kind='trade' AND interval_s=300 AND ts=$3",buy/self.contract_btc,self.name,db.utc(int(v[0])/1000))
            observed=await self.get('/api/v5/public/liquidation-orders',instType='SWAP',instFamily='BTC-USDT',mgnMode='cross',state='filled',limit=100)
            for v in observed:
                if v.get('instId')=='BTC-USDT-SWAP':
                    for d in v.get('details',[]):
                        await self.liquidation(int(d['ts']),d.get('posSide') or ('long' if d['side']=='sell' else 'short'),float(d['sz']),float(d['bkPx']),d,'bankruptcy')

    async def metric(self,ts,kind,value,raw):
        await self.p.execute('INSERT INTO funding_ratios VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING',db.utc(ts/1000),self.name,kind,value,raw)

    async def mark(self,ts,price):
        await self.p.execute('INSERT INTO live_marks VALUES($1,$2,$3) ON CONFLICT(exchange) DO UPDATE SET ts=excluded.ts,price=excluded.price WHERE excluded.ts>live_marks.ts',self.name,db.utc(ts/1000),price)
        # Also persist live path extrema, so crashes cannot erase intervening sweeps.
        await self.p.execute('''INSERT INTO candles(ts,exchange,kind,interval_s,o,h,l,c,volume,complete) VALUES($1,$2,'live_mark',60,$3,$3,$3,$3,0,false)
        ON CONFLICT(ts,exchange,kind,interval_s) DO UPDATE SET h=GREATEST(candles.h,excluded.h),l=LEAST(candles.l,excluded.l),c=excluded.c''',db.utc(ts//60000*60),self.name,price)
        await db.event(self.p,'mark',{'exchange':self.name,'ts':ts,'price':price})

    async def liquidation(self,ts,side,qty,price,raw,semantics,event_id=None,transport='rest'):
        btc=qty*self.contract_btc
        eid=event_id or hashlib.sha256(json.dumps([self.name,ts,side,btc,price,semantics],separators=(',',':')).encode()).hexdigest()
        async with self.p.acquire() as c:
            async with c.transaction():
                # Public streams lack account/order IDs. Serialize equivalent reports so
                # REST/WS metadata differences cannot duplicate the same observed print.
                await c.execute('SELECT pg_advisory_xact_lock($1)',837100+('binance','bybit','okx').index(self.name))
                duplicate=False if event_id else await c.fetchval('SELECT EXISTS(SELECT 1 FROM observed_liquidations WHERE ts=$1 AND exchange=$2 AND side=$3 AND quantity_btc=$4 AND price=$5 AND price_semantics=$6)',db.utc(ts/1000),self.name,side,btc,price,semantics)
                inserted=None if duplicate else await c.fetchval('INSERT INTO observed_liquidations VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING RETURNING event_id',db.utc(ts/1000),self.name,eid,side,btc,price,btc*price,semantics,raw)
                if inserted and transport=='websocket':
                    await c.execute('INSERT INTO learning_event_receipts(ts,exchange,event_id) VALUES($1,$2,$3) ON CONFLICT DO NOTHING',db.utc(ts/1000),self.name,eid)
        if inserted:
            await db.event(self.p,'liquidation',{'exchange':self.name,'ts':ts,'side':side,'usd':qty*self.contract_btc*price,'price_semantics':semantics})

    async def flush_trades(self):
        batch,self.pending=self.pending,[]
        if not batch:
            return
        try:
            async with self.p.acquire() as c:
                async with c.transaction():
                    await c.execute('''WITH incoming AS (
                    INSERT INTO aggregate_trades SELECT * FROM unnest($1::timestamptz[],$2::text[],$3::text[],$4::float8[],$5::float8[],$6::boolean[])
                    ON CONFLICT DO NOTHING RETURNING *), grouped AS (
                    SELECT time_bucket('5 minutes',ts) ts,exchange,round(price/25)*25 price,sum(quantity_native) volume_native,
                    sum(CASE WHEN buy THEN quantity_native ELSE 0 END) buy_native FROM incoming GROUP BY 1,2,3)
                    INSERT INTO volume_profiles SELECT * FROM grouped ON CONFLICT(ts,exchange,price)
                    DO UPDATE SET volume_native=volume_profiles.volume_native+excluded.volume_native,
                    buy_native=volume_profiles.buy_native+excluded.buy_native''',*[list(x) for x in zip(*batch)])
            await db.checkpoint(self.p,self.name,'trades',{'last_id':batch[-1][2],'last_ts':batch[-1][0].timestamp()})
        except Exception:
            self.pending=batch[-5000:]+self.pending
            await db.coverage(self.p,self.name,'trades','error',{'reason':'bounded queue; possible trade loss on database failure; profile requires candle-volume reconciliation'})
            raise

    async def repair_candles(self,kind,interval):
        feed=f'repair_{kind}_{interval}'
        cp=await self.p.fetchval('SELECT cursor FROM ingestion_checkpoints WHERE exchange=$1 AND feed=$2',self.name,feed)
        latest=await self.p.fetchval('SELECT max(ts) FROM candles WHERE exchange=$1 AND kind=$2 AND interval_s=$3',self.name,kind,interval)
        if latest is None:
            return
        now=int(time.time()*1000)
        if cp and not cp.get('done'):
            end=int(cp['end_ms']);target=int(cp['target_ms'])
        elif time.time()-latest.timestamp()>interval*12:
            end=now;target=int(latest.timestamp()*1000)
        else:
            return
        target=max(target,int((time.time()-30*86400)*1000))
        await db.checkpoint(self.p,self.name,feed,{'end_ms':end,'target_ms':target,'done':False})
        for _ in range(8):
            rows=await self.candle_page(kind,interval,end)
            if not rows:
                await db.coverage(self.p,self.name,feed,'limited',{'reason':'empty gap-repair page','end_ms':end})
                await db.checkpoint(self.p,self.name,feed,{'end_ms':end,'target_ms':target,'done':True,'limited':True})
                return
            await self.store_candles(rows,kind,interval)
            oldest=min(r[0] for r in rows)
            if oldest>=end:
                return
            end=oldest-1
            done=oldest<=target
            await db.checkpoint(self.p,self.name,feed,{'end_ms':end,'target_ms':target,'done':done})
            if done:
                return

    async def recent_trades(self):
        # Reconnection catch-up is explicitly bounded and reconciled against candle volume.
        if self.name=='binance':
            cp=await self.p.fetchval("SELECT cursor FROM ingestion_checkpoints WHERE exchange=$1 AND feed='trades'",self.name)
            args={'symbol':'BTCUSDT','limit':1000}
            if cp and time.time()-float(cp.get('last_ts',0))<47*3600:
                args['fromId']=int(cp['last_id'])+1
            for _ in range(4):
                rows=await self.get('/fapi/v1/aggTrades',**args)
                for t in rows:
                    self.pending.append((db.utc(int(t['T'])/1000),self.name,str(t['a']),float(t['p']),float(t['q']),not t['m']))
                await self.flush_trades()
                if len(rows)<1000 or 'fromId' not in args:
                    break
                args['fromId']=int(rows[-1]['a'])+1
        elif self.name=='bybit':
            rows=(await self.get('/v5/market/recent-trade',category='linear',symbol='BTCUSDT',limit=1000))['list']
            for t in rows:
                self.pending.append((db.utc(int(t['time'])/1000),self.name,t['execId'],float(t['price']),float(t['size']),t['side']=='Buy'))
            await self.flush_trades()
        else:
            rows=await self.get('/api/v5/market/history-trades',instId='BTC-USDT-SWAP',limit=100)
            for t in rows:
                self.pending.append((db.utc(int(t['ts'])/1000),self.name,t['tradeId'],float(t['px']),float(t['sz']),t['side']=='buy'))
            await self.flush_trades()
        await db.coverage(self.p,self.name,'trade_recovery','limited',{'reason':'bounded catch-up; complete entry profile requires native candle-volume reconciliation'})

    async def stream(self):
        if self.name=='binance':
            url='wss://fstream.binance.com/market/stream?streams=btcusdt@markPrice@1s/btcusdt@aggTrade/btcusdt@forceOrder/btcusdt@kline_1m'
            sub=None
        elif self.name=='bybit':
            url='wss://stream.bybit.com/v5/public/linear'
            sub={'op':'subscribe','args':['publicTrade.BTCUSDT','tickers.BTCUSDT','allLiquidation.BTCUSDT']}
        else:
            url='wss://ws.okx.com:8443/ws/v5/public'
            sub={'op':'subscribe','args':[{'channel':'mark-price','instId':'BTC-USDT-SWAP'},{'channel':'trades','instId':'BTC-USDT-SWAP'},{'channel':'liquidation-orders','instType':'SWAP'}]}
        delay=1
        while True:
            audit=None
            try:
                try:
                    await self.recent_trades()
                except Exception as e:
                    await db.coverage(self.p,self.name,'trade_recovery','error',{'error':str(e)})
                async with self.s.ws_connect(url,heartbeat=20,receive_timeout=50,max_msg_size=2**20) as ws:
                    audit=FeedAudit(self.p,self.name)
                    if sub:
                        await ws.send_json(sub)
                    await db.coverage(self.p,self.name,'websocket','connected',{'url':url,'losses_possible':True,'liquidation_completeness':'sampled' if self.name in ('binance','okx') else 'documented all, connection gaps possible'})
                    lastmark,lastflush,lasthb=0.,time.monotonic(),0.
                    async for msg in ws:
                        if msg.type!=aiohttp.WSMsgType.TEXT:
                            if msg.type in (aiohttp.WSMsgType.CLOSED,aiohttp.WSMsgType.ERROR):
                                break
                            continue
                        if msg.data=='pong':
                            continue
                        r=json.loads(msg.data)
                        await audit.observe(r)
                        if self.name=='okx' and 'data' not in r:
                            if r.get('event')=='error':
                                await db.coverage(self.p,self.name,'websocket_subscription','partial',r)
                            continue
                        if r.get('event')=='error' or (r.get('success') is False):
                            await db.coverage(self.p,self.name,'websocket_subscription','partial',r)
                        now=time.monotonic()
                        if self.name=='binance':
                            d=r.get('data',r)
                            typ=d.get('e')
                            if typ=='markPriceUpdate' and now-lastmark>=5:
                                await self.mark(int(d['E']),float(d['p']));lastmark=now
                            elif typ=='aggTrade':
                                self.pending.append((db.utc(int(d['T'])/1000),self.name,str(d['a']),float(d['p']),float(d['q']),not d['m']))
                            elif typ=='forceOrder':
                                o=d['o'];await self.liquidation(int(o['T']),'long' if o['S']=='SELL' else 'short',float(o['z']),float(o['ap'] or o['p']),o,'average_execution',transport='websocket')
                            elif typ=='kline':
                                k=d['k']
                                await db.event(self.p,'candle',{'exchange':self.name,'ts':k['t'],'o':k['o'],'h':k['h'],'l':k['l'],'c':k['c'],'closed':k['x']})
                        elif self.name=='bybit':
                            topic=r.get('topic','')
                            if topic.startswith('tickers') and r.get('data',{}).get('markPrice') and now-lastmark>=5:
                                await self.mark(int(r['ts']),float(r['data']['markPrice']));lastmark=now
                            elif topic.startswith('publicTrade'):
                                for t in r['data']:
                                    self.pending.append((db.utc(int(t['T'])/1000),self.name,t['i'],float(t['p']),float(t['v']),t['S']=='Buy'))
                            elif topic.startswith('allLiquidation'):
                                for t in r['data']:
                                    await self.liquidation(int(t['T']),'long' if t['S']=='Buy' else 'short',float(t['v']),float(t['p']),t,'bankruptcy',transport='websocket')
                        else:
                            ch=r.get('arg',{}).get('channel')
                            if ch=='mark-price' and now-lastmark>=5:
                                for t in r['data']:
                                    await self.mark(int(t['ts']),float(t['markPx']))
                                lastmark=now
                            elif ch=='trades':
                                for t in r['data']:
                                    self.pending.append((db.utc(int(t['ts'])/1000),self.name,t['tradeId'],float(t['px']),float(t['sz']),t['side']=='buy'))
                            elif ch=='liquidation-orders':
                                for t in r['data']:
                                    if t.get('instId')!='BTC-USDT-SWAP':
                                        continue
                                    for d in t.get('details',[]):
                                        await self.liquidation(int(d['ts']),d.get('posSide') or ('long' if d['side']=='sell' else 'short'),float(d['sz']),float(d['bkPx']),d,'bankruptcy',transport='websocket')
                        if now-lastflush>5 or len(self.pending)>=2000:
                            await self.flush_trades();lastflush=now
                        if now-lasthb>=30:
                            delay=1
                            await db.coverage(self.p,self.name,'websocket','live',{'received_at_ms':int(time.time()*1000),'trade_feed':'partial; gaps not backfilled for Bybit/OKX'},last=db.utc(time.time()))
                            lasthb=now
                            if self.name=='bybit':
                                await ws.send_json({'op':'ping'})
            except asyncio.CancelledError:
                await self.flush_trades()
                raise
            except Exception as e:
                log.warning('%s websocket: %s',self.name,e)
                await db.coverage(self.p,self.name,'websocket','reconnecting',{'error':str(e),'gap_since':time.time()})
            finally:
                if audit:
                    try:await audit.flush()
                    except Exception:log.exception('%s feed-audit flush',self.name)
            await asyncio.sleep(delay)
            delay=min(60,delay*2)
