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
from app import hl_discovery as discovery
from app.hl_math import FRESH_SECONDS, METHOD, LeaderboardRows, btc_position, number, pack, projection, large_trade_addresses

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
            'discovery':await discovery.diagnostics(p),
            'limitations':['Account discovery is incomplete and biased toward large/active accounts.',
             'This is not a verified global top-1000 census or Glassnode dataset.',
             'Account queries are staggered; freshness is capped at 15 minutes.',
             'No historic positions are fabricated; live closed positions disappear after successful re-query.',
             'Reported thresholds can change with collateral and other positions; null values are excluded.']}


class Collector:
    def __init__(self,p,session):
        self.p=p; self.session=session; self.next_request=0.; self.mark=None; self.oi=None
        self.last_discovery=0.; self.last_candles=0.; self.last_seed=0.; self.last_snapshot=0.; self.scan_count=0
        self.last_flush=0.;self.ws_received=0.;self.ws_connected=False
        self.ws_trades=0;self.ws_large_signals=0;self.ws_dropped=0;self.ws_reconnects=0
        self.trade_buffer=discovery.TradeBuffer();self.weight_budget=discovery.WeightBudget()
        self.jobs={};self.relation_count=0
        self.last_market_success=0.

    async def info(self,body):
        # Single caller: <=150 ordinary requests/min, ~300 weight/min for accounts.
        await asyncio.sleep(max(0,self.next_request-time.monotonic()))
        await self.weight_budget.reserve(discovery.request_weight(body))
        self.next_request=time.monotonic()+.4
        async with self.session.post(INFO,json=body,timeout=aiohttp.ClientTimeout(total=20)) as r:
            if r.status in (429,503):
                retry=number(r.headers.get('Retry-After','30')) or 30
                self.next_request=time.monotonic()+max(30,min(120,retry))
            r.raise_for_status()
            return await r.json()

    async def seed(self):
        parser=LeaderboardRows(); candidates={}; size=0; rows_seen=0; omitted=0; decode=codecs.getincrementaldecoder('utf-8')()
        async with self.session.get(LEADERBOARD,timeout=aiohttp.ClientTimeout(total=90)) as r:
            r.raise_for_status()
            async for chunk in r.content.iter_chunked(65536):
                size+=len(chunk)
                if size>64_000_000: raise ValueError('Leaderboard exceeds download budget')
                for row in parser.feed(decode.decode(chunk)):
                    rows_seen+=1
                    value=discovery.leaderboard_candidate(row)
                    if value:
                        a,v=value
                        if a in candidates or len(candidates)<discovery.REGISTRY_LIMIT:candidates[a]=v
                        else:omitted+=1
                await asyncio.sleep(0)
        for row in parser.feed(decode.decode(b'',final=True)):
            rows_seen+=1;value=discovery.leaderboard_candidate(row)
            if value:
                a,v=value
                if a in candidates or len(candidates)<discovery.REGISTRY_LIMIT:candidates[a]=v
                else:omitted+=1
        if not parser.ended or not candidates: raise ValueError('Incomplete leaderboard JSON')
        ordered=sorted(candidates.items(),key=lambda v:(-v[1],v[0])); admitted=0
        for i in range(0,len(ordered),1000):
            admitted+=await discovery.register(self.p,ordered[i:i+1000],'public_leaderboard_full')
            await asyncio.sleep(0)
        await db.coverage(self.p,'hyperliquid','account_discovery','available',{'seed_candidates':len(candidates),'bytes':size,
            'rows_seen':rows_seen,'omitted_at_parser_capacity':omitted,'new_candidates':admitted,'discovery_version':discovery.VERSION,
            'source':LEADERBOARD,'warning':'Undocumented official-host endpoint; account value is not BTC position rank.'})
        self.last_seed=time.monotonic()

    async def market(self):
        meta,ctxs=await self.info({'type':'metaAndAssetCtxs'})
        index=next(i for i,v in enumerate(meta['universe']) if v['name']=='BTC')
        ctx=ctxs[index]; mark=number(ctx['markPx'],True); oi=number(ctx['openInterest'],True)
        if mark is None: raise ValueError('Invalid Hyperliquid mark')
        self.mark=mark; self.oi=oi; now=datetime.now(timezone.utc)
        self.last_market_success=time.time()
        await self.p.execute('''INSERT INTO live_marks(exchange,ts,price) VALUES('hyperliquid',$1,$2)
          ON CONFLICT(exchange) DO UPDATE SET ts=excluded.ts,price=excluded.price''',now,mark)
        await db.coverage(self.p,'hyperliquid','market','live',{'mark':mark,'one_sided_oi_btc':oi,'funding':ctx.get('funding'),
            'contract':meta['universe'][index],'collateral':'USDC'},last=now)
        await db.event(self.p,'mark',{'exchange':'hyperliquid','ts':now.isoformat(),'price':mark})

    async def discover(self):
        trades=await self.info({'type':'recentTrades','coin':'BTC'})
        self.trade_buffer.add(trades)
        self.last_discovery=time.monotonic()

    async def flush_signals(self):
        batch=list(self.trade_buffer.rows.items())
        accepted=await discovery.flush_trades(self.p,[v for _,v in batch])
        for k,_ in batch:self.trade_buffer.rows.pop(k,None)
        await db.coverage(self.p,'hyperliquid','whale_trade_discovery',
          'live' if self.ws_connected and time.time()-self.ws_received<60 else 'reconnecting',
          {'minimum_fill_usd':0,'trades_received':self.ws_trades,'large_fill_address_signals':self.ws_large_signals,
           'accepted_unique_trades_last_flush':accepted,'pending_trades':len(self.trade_buffer.rows),
           'dropped_at_capacity':self.trade_buffer.dropped,'reconnects':self.ws_reconnects,
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
                            self.trade_buffer.add(trades)
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
        self.scan_count+=1
        row=await discovery.pick(self.p,self.scan_count)
        if not row:return False
        await self.p.execute('UPDATE hl_accounts SET attempted_at=now() WHERE address=$1',row['address'])
        try:
            state=await self.info({'type':'clearinghouseState','user':row['address']})
            await discovery.observed(self.p,row,btc_position(state),self.mark)
        except asyncio.CancelledError:raise
        except Exception as e:
            await discovery.failed(self.p,row)
            log.warning('Hyperliquid account query %s; individual retry scheduled',type(e).__name__)
            return True
        return True

    async def relations(self):
        kinds=[('subAccounts','subaccounts_next_at','public_subaccount'),
               ('userVaultEquities','vaults_next_at','public_vault'),
               ('vaultDetails','vault_details_next_at','public_vault_child')]
        kind,column,source=kinds[self.relation_count%3];self.relation_count+=1
        condition="discovery IN ('public_vault','public_vault_child')" if kind=='vaultDetails' else '(tracking OR priority>=100000)'
        row=await self.p.fetchrow(f'''SELECT address FROM hl_accounts WHERE {condition} AND {column}<=now()
          ORDER BY {column},priority DESC,address LIMIT 1''')
        if not row:return
        # Back off an individual failing relationship lookup without blocking others.
        await self.p.execute(f"UPDATE hl_accounts SET {column}=now()+interval '30 minutes' WHERE address=$1",row['address'])
        data=await self.info({'type':kind,'vaultAddress' if kind=='vaultDetails' else 'user':row['address']})
        addresses=discovery.linked_addresses(kind,data)
        for i in range(0,len(addresses),1000):
            await discovery.register(self.p,[(a,0.) for a in addresses[i:i+1000]],source)
        await self.p.execute(f"UPDATE hl_accounts SET {column}=now()+interval '12 hours' WHERE address=$1",row['address'])
        await db.coverage(self.p,'hyperliquid','account_relationships','live',{'last_kind':kind,'linked_candidates':len(addresses),
            'scope':'Addresses only; vault investor equity and embedded account summaries are not added to exposure.'})

    async def snapshot(self):
        positions=await current(self.p)
        if not self.mark: return
        if self.last_market_success and time.time()-self.last_market_success>120:
            await db.coverage(self.p,'hyperliquid','positions','degraded',{'reason':'mark observation older than 120 seconds; snapshot skipped'})
            return
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
        seed=await self.p.fetchrow("SELECT updated_at,detail FROM feed_coverage WHERE exchange='hyperliquid' AND feed='account_discovery'")
        if seed and seed['detail'].get('discovery_version')==discovery.VERSION:
            self.jobs['seed']=(time.monotonic()+max(0,21600-(time.time()-seed['updated_at'].timestamp())),0)
        self.jobs['snapshot']=(self.last_snapshot+300,0)
        while True:
            await self.job('market',self.market,30)
            await self.job('signals',self.flush_signals,10)
            await self.job('cohort',lambda:discovery.cohort(self.p),30)
            await self.job('account',self.account,.05)
            await self.job('snapshot',self.snapshot,300)
            await self.job('relations',self.relations,30)
            await self.job('recent_trades',self.discover,60)
            await self.job('candles',self.candles,60)
            await self.job('cleanup',lambda:discovery.cleanup(self.p),60)
            await self.job('seed',self.seed,21600)
            await asyncio.sleep(.1)

    async def job(self,key,fn,interval):
        due,failures=self.jobs.get(key,(0,0))
        if time.monotonic()<due:return
        try:
            await fn()
            self.jobs[key]=(time.monotonic()+interval,0)
            if failures:await db.coverage(self.p,'hyperliquid','job_'+key,'live',{'recovered':True})
        except asyncio.CancelledError:raise
        except Exception as e:
            failures+=1
            self.jobs[key]=(time.monotonic()+min(600,2**min(failures+1,9)),failures)
            log.warning('Hyperliquid %s %s; retry %s',key,type(e).__name__,failures)
            await db.coverage(self.p,'hyperliquid','job_'+key,'retrying',{'error':type(e).__name__,'failures':failures})


async def loop(p):
    async with aiohttp.ClientSession(headers={'User-Agent':'LiquidHeat/1.2 public-position-research'},connector=aiohttp.TCPConnector(limit=2)) as session:
        collector=Collector(p,session)
        tasks=[asyncio.create_task(collector.run()),asyncio.create_task(collector.trade_stream())]
        try:await asyncio.gather(*tasks)
        finally:
            for task in tasks:task.cancel()
            await asyncio.gather(*tasks,return_exceptions=True)
