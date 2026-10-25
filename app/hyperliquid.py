"""Bounded public Hyperliquid collector. Account identifiers stay server-side."""
import asyncio
import codecs
from datetime import datetime,timezone,timedelta
import json
import logging
import time
import zlib
import aiohttp
from app import db
from app.hl_math import ADDRESS, FRESH_SECONDS, METHOD, LeaderboardRows, btc_position, number, pack, projection, seed_row, large_trade_addresses

log=logging.getLogger(__name__)
INFO='https://api.hyperliquid.xyz/info'
LEADERBOARD='https://stats-data.hyperliquid.xyz/Mainnet/leaderboard'
WEBSOCKET='wss://api.hyperliquid.xyz/ws'


async def current(p, now=None):
    now=now or datetime.now(timezone.utc)
    rows=await p.fetch('''SELECT address,observed_at,position FROM hl_accounts WHERE position IS NOT NULL
      AND observed_at>=$1 ORDER BY abs((position->>'size_btc')::double precision) DESC,address LIMIT 1000''',now-timedelta(seconds=FRESH_SECONDS))
    return [dict(r['position'],address=r['address'],observed_at=r['observed_at'].isoformat()) for r in rows]


async def status(p,positions=None):
    row=await p.fetchrow('''SELECT count(*) discovered,count(observed_at) checked,
      count(*) FILTER(WHERE observed_at>now()-interval '15 minutes') fresh_accounts,
      count(*) FILTER(WHERE position IS NOT NULL) known_btc_positions,
      min(discovered_at) discovery_started FROM hl_accounts''')
    last=await p.fetchrow('SELECT ts,audit FROM hl_snapshots ORDER BY ts DESC LIMIT 1')
    feeds=await p.fetch("SELECT feed,status,updated_at,detail FROM feed_coverage WHERE exchange='hyperliquid'")
    mark=await p.fetchval("SELECT price FROM live_marks WHERE exchange='hyperliquid'")
    market=next((r['detail'] for r in feeds if r['feed']=='market'),{})
    if positions is None:positions=await current(p)
    _,_,live_sample,_=projection(positions,mark,market.get('one_sided_oi_btc')) if mark else (None,None,None,None)
    return {'current_sample':live_sample,'method_version':METHOD,**dict(row),'last_snapshot':last['ts'] if last else None,
            'snapshot_age_s':max(0,time.time()-last['ts'].timestamp()) if last else None,
            'sample':last['audit'] if last else None,'feeds':[dict(r) for r in feeds],
            'limitations':['Account discovery is incomplete and biased toward large/active accounts.',
             'This is not a verified global top-1000 census or Glassnode dataset.',
             'Account queries are staggered; freshness is capped at 15 minutes.',
             'No historic positions are fabricated; live closed positions disappear after successful re-query.',
             'Reported thresholds can change with collateral and other positions; null values are excluded.']}


class Collector:
    def __init__(self,p,session):
        self.p=p; self.session=session; self.next_request=0.; self.mark=None; self.oi=None
        self.last_discovery=0.; self.last_candles=0.; self.last_seed=0.; self.last_snapshot=0.; self.scan_count=0
        self.pending={};self.last_flush=0.;self.ws_received=0.;self.ws_connected=False
        self.ws_trades=0;self.ws_large_signals=0;self.ws_dropped=0;self.ws_reconnects=0

    async def info(self,body):
        # Single caller: <=150 ordinary requests/min, ~300 weight/min for accounts.
        await asyncio.sleep(max(0,self.next_request-time.monotonic()))
        self.next_request=time.monotonic()+.4
        async with self.session.post(INFO,json=body,timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status in (429,503):
                self.next_request=time.monotonic()+max(30,min(120,float(r.headers.get('Retry-After','30'))))
            r.raise_for_status()
            return await r.json()

    async def seed(self):
        parser=LeaderboardRows(); heap=[]; size=0; decode=codecs.getincrementaldecoder('utf-8')()
        async with self.session.get(LEADERBOARD,timeout=aiohttp.ClientTimeout(total=90)) as r:
            r.raise_for_status()
            async for chunk in r.content.iter_chunked(65536):
                size+=len(chunk)
                if size>64_000_000: raise ValueError('Leaderboard exceeds download budget')
                for row in parser.feed(decode.decode(chunk)):
                    seed_row(heap,row)
                await asyncio.sleep(0)
        for row in parser.feed(decode.decode(b'',final=True)): seed_row(heap,row)
        if not parser.ended or not heap: raise ValueError('Incomplete leaderboard JSON')
        existing={r['address'] for r in await self.p.fetch('SELECT address FROM hl_accounts')}
        available=max(0,10000-len(existing)); seeds=[]
        for value,address in sorted(heap,reverse=True):
            if address not in existing:
                if not available: continue
                available-=1
            seeds.append((address,value))
        await self.p.executemany('''INSERT INTO hl_accounts(address,discovery,priority) VALUES($1,'public_leaderboard',$2)
          ON CONFLICT(address) DO UPDATE SET priority=excluded.priority''',seeds)
        await db.coverage(self.p,'hyperliquid','account_discovery','available',{'seed_candidates':len(heap),'bytes':size,
            'source':LEADERBOARD,'warning':'Undocumented official-host endpoint; account value is not BTC position rank.'})
        self.last_seed=time.monotonic()

    async def market(self):
        meta,ctxs=await self.info({'type':'metaAndAssetCtxs'})
        index=next(i for i,v in enumerate(meta['universe']) if v['name']=='BTC')
        ctx=ctxs[index]; mark=number(ctx['markPx'],True); oi=number(ctx['openInterest'],True)
        if mark is None: raise ValueError('Invalid Hyperliquid mark')
        self.mark=mark; self.oi=oi; now=datetime.now(timezone.utc)
        await self.p.execute('''INSERT INTO live_marks(exchange,ts,price) VALUES('hyperliquid',$1,$2)
          ON CONFLICT(exchange) DO UPDATE SET ts=excluded.ts,price=excluded.price''',now,mark)
        await db.coverage(self.p,'hyperliquid','market','live',{'mark':mark,'one_sided_oi_btc':oi,'funding':ctx.get('funding'),
            'contract':meta['universe'][index],'collateral':'USDC'},last=now)
        await db.event(self.p,'mark',{'exchange':'hyperliquid','ts':now.isoformat(),'price':mark})

    async def discover(self):
        trades=await self.info({'type':'recentTrades','coin':'BTC'})
        addresses={str(a).lower() for t in trades for a in t.get('users',[]) if ADDRESS.fullmatch(str(a).lower())}
        # Bound registry: new candidates cannot make a shared server scan unbounded.
        count=await self.p.fetchval('SELECT count(*) FROM hl_accounts')
        if count<10000:
            await self.p.executemany('''INSERT INTO hl_accounts(address,discovery) VALUES($1,'public_btc_trade') ON CONFLICT(address) DO UPDATE SET discovery='public_btc_trade' ''',[(a,) for a in sorted(addresses)[:10000-count]])
        self.last_discovery=time.monotonic()

    async def flush_signals(self):
        # Poll loop is the only registry writer for signals; bound SQL work per batch.
        batch=self.pending;self.pending={}
        existing={r['address'] for r in await self.p.fetch('SELECT address FROM hl_accounts')}
        available=max(0,10000-len(existing));records=[]
        for address,value in sorted(batch.items(),key=lambda r:r[1],reverse=True):
            if address not in existing:
                if not available:
                    self.ws_dropped+=1;continue
                available-=1
            records.append((address,value))
        if records:
            try:
                await self.p.executemany("""INSERT INTO hl_accounts(address,discovery,signaled_at,largest_trade_usd)
                  VALUES($1,'public_large_btc_trade',now(),$2) ON CONFLICT(address) DO UPDATE
                  SET signaled_at=excluded.signaled_at,largest_trade_usd=greatest(hl_accounts.largest_trade_usd,excluded.largest_trade_usd)""",records)
            except Exception:
                # Retain bounded outstanding discovery signals if the database is unavailable.
                for address,value in batch.items():
                    if len(self.pending)<1000 or address in self.pending:
                        self.pending[address]=max(self.pending.get(address,0.),value)
                raise
        await db.coverage(self.p,'hyperliquid','whale_trade_discovery',
          'live' if self.ws_connected and time.time()-self.ws_received<60 else 'reconnecting',
          {'threshold_usd':50000,'trades_received':self.ws_trades,'address_signals':self.ws_large_signals,
           'pending_addresses':len(self.pending),'dropped_at_capacity':self.ws_dropped,'reconnects':self.ws_reconnects,
           'last_trade_message_at':db.utc(self.ws_received).isoformat() if self.ws_received else None,
           'scope':'Public BTC trade addresses only; fills do not prove new/open whale positions.'})
        self.last_flush=time.monotonic()

    async def trade_stream(self):
        backoff=1
        while True:
            try:
                async with self.session.ws_connect(WEBSOCKET,heartbeat=20,receive_timeout=45,max_msg_size=2**20) as ws:
                    await ws.send_json({'method':'subscribe','subscription':{'type':'trades','coin':'BTC'}})
                    async for msg in ws:
                        if msg.type==aiohttp.WSMsgType.TEXT:
                            message=json.loads(msg.data)
                            if message.get('channel')=='subscriptionResponse':
                                self.ws_connected=True;backoff=1
                            if message.get('channel')!='trades':continue
                            trades=message.get('data',[])
                            if not isinstance(trades,list):continue
                            self.ws_received=time.time();self.ws_trades+=len(trades)
                            candidates=large_trade_addresses(trades)
                            self.ws_large_signals+=len(candidates)
                            for address,value in candidates.items():
                                if address not in self.pending and len(self.pending)>=1000:
                                    self.ws_dropped+=1;continue
                                self.pending[address]=max(self.pending.get(address,0.),value)
                        elif msg.type in (aiohttp.WSMsgType.CLOSED,aiohttp.WSMsgType.ERROR):break
            except asyncio.CancelledError:raise
            except Exception as e:
                log.warning('Hyperliquid public trade stream %s; reconnecting',type(e).__name__)
            finally:self.ws_connected=False
            self.ws_reconnects+=1
            await asyncio.sleep(backoff);backoff=min(60,backoff*2)

    async def candles(self):
        last=await self.p.fetchval("SELECT max(ts) FROM candles WHERE exchange='hyperliquid' AND kind='trade' AND interval_s=300")
        now=datetime.now(timezone.utc)
        start=max(last-timedelta(minutes=5) if last else now-timedelta(days=1),now-timedelta(days=3))
        data=await self.info({'type':'candleSnapshot','req':{'coin':'BTC','interval':'5m','startTime':int(start.timestamp()*1000),'endTime':int(now.timestamp()*1000)}})
        records=[]
        for c in data:
            vals=[number(c[k]) for k in ('o','h','l','c','v')]
            if any(v is None for v in vals) or min(vals[:4])<=0 or vals[1]<vals[2] or vals[4]<0:
                raise ValueError('Invalid Hyperliquid candle')
            records.append((db.utc(c['t']/1000),'hyperliquid','trade',300,*vals,c['T']<now.timestamp()*1000))
        await self.p.executemany('''INSERT INTO candles(ts,exchange,kind,interval_s,o,h,l,c,volume,complete)
          VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10) ON CONFLICT(ts,exchange,kind,interval_s)
          DO UPDATE SET h=excluded.h,l=excluded.l,c=excluded.c,volume=excluded.volume,complete=excluded.complete''',records)
        await db.coverage(self.p,'hyperliquid','candles','live',{'interval_s':300,'backfill':'native candles only, NOT past account positions'},last=now)
        self.last_candles=time.monotonic()

    async def account(self):
        # Reserve regular discovery scans while refreshing known BTC positions first.
        self.scan_count+=1
        priority=self.scan_count%4!=0
        address=await self.p.fetchval('''SELECT address FROM hl_accounts
          WHERE attempted_at IS NULL OR attempted_at<now()-interval '4 minutes'
          OR (signaled_at>coalesce(observed_at,TIMESTAMPTZ '1970-01-01') AND attempted_at<now()-interval '60 seconds')
          ORDER BY CASE WHEN $1 AND position IS NOT NULL THEN 0 WHEN signaled_at>coalesce(observed_at,TIMESTAMPTZ '1970-01-01') THEN 1 WHEN discovery='public_btc_trade' AND attempted_at IS NULL THEN 1 ELSE 2 END,
          attempted_at NULLS FIRST,priority DESC,address LIMIT 1''',priority)
        if not address: return
        await self.p.execute('UPDATE hl_accounts SET attempted_at=now() WHERE address=$1',address)
        state=await self.info({'type':'clearinghouseState','user':address})
        position=btc_position(state)
        # Successful no-position response closes prior observation. Errors never do.
        await self.p.execute('UPDATE hl_accounts SET observed_at=now(),position=$2,error_count=0 WHERE address=$1',address,position)

    async def snapshot(self):
        positions=await current(self.p)
        if not self.mark: return
        now=datetime.now(timezone.utc)
        start,matrix,audit,selected=projection(positions,self.mark,self.oi)
        ages=[(now-datetime.fromisoformat(r['observed_at'])).total_seconds() for r in selected]
        audit.update(max_position_age_s=max(ages,default=None),oldest_position_observed_at=min((r['observed_at'] for r in selected),default=None))
        await self.p.execute('''INSERT INTO hl_snapshots(ts,mark,grid_start,grid_n,matrix,constituents,audit,method_version)
          VALUES($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING''',now,self.mark,start,matrix.shape[1],pack(matrix),
          zlib.compress(json.dumps(selected,separators=(',',':')).encode(),6),audit,METHOD)
        await db.coverage(self.p,'hyperliquid','positions','sampled',audit,first=now,last=now)
        await db.coverage(self.p,'hyperliquid','collector','live',{'interval_s':300,'account_request_floor_s':.4},last=now)
        await db.checkpoint(self.p,'hyperliquid','snapshots',{'last_snapshot':now.isoformat(),'method_version':METHOD})
        await db.event(self.p,'hyperliquid',{'ts':now.isoformat(),'positions':len(selected),'method_version':METHOD})
        self.last_snapshot=time.monotonic()

    async def run(self):
        last=await self.p.fetchval('SELECT max(ts) FROM hl_snapshots')
        self.last_snapshot=time.monotonic()-min(300,max(0,time.time()-last.timestamp())) if last else 0
        seed=await self.p.fetchval("SELECT updated_at FROM feed_coverage WHERE exchange='hyperliquid' AND feed='account_discovery'")
        self.last_seed=time.monotonic()-min(21600,max(0,time.time()-seed.timestamp())) if seed else 0
        last_market=0.; failures=0
        while True:
            try:
                now=time.monotonic()
                if now-self.last_seed>=21600:
                    try: await self.seed()
                    except Exception as e:
                        self.last_seed=time.monotonic()-21000
                        await db.coverage(self.p,'hyperliquid','account_discovery','degraded',{'error':type(e).__name__,'fallback':'public BTC trades'})
                if now-self.last_flush>=10: await self.flush_signals()
                if now-last_market>=30:
                    await self.market(); last_market=time.monotonic()
                if now-self.last_discovery>=60: await self.discover()
                if now-self.last_candles>=60: await self.candles()
                await self.account()
                if now-self.last_snapshot>=300: await self.snapshot()
                failures=0
                await asyncio.sleep(.05)
            except asyncio.CancelledError: raise
            except Exception as e:
                failures+=1
                # Do not log request URL/user address or replace valid data on errors.
                log.warning('Hyperliquid collection %s; retry %s',type(e).__name__,failures)
                await db.coverage(self.p,'hyperliquid','collector','retrying',{'error':type(e).__name__,'failures':failures})
                await asyncio.sleep(min(60,2**min(failures,6)))


async def loop(p):
    async with aiohttp.ClientSession(headers={'User-Agent':'LiquidHeat/1.1 public-position-research'},connector=aiohttp.TCPConnector(limit=2)) as session:
        collector=Collector(p,session)
        tasks=[asyncio.create_task(collector.run()),asyncio.create_task(collector.trade_stream())]
        try:await asyncio.gather(*tasks)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
