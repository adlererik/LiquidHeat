"""Run inside the Debian app image; all fixtures use a disposable database."""
import asyncio
from datetime import timedelta
import json
from pathlib import Path
import uuid
from unittest.mock import AsyncMock
import asyncpg
from app import db,hl_discovery as d,hyperliquid
from app.config import DATABASE_URL


def addr(n):return '0x'+format(n,'040x')


async def main():
    name='liquidheat_discovery_verify_'+uuid.uuid4().hex[:12]
    admin=await asyncpg.connect(DATABASE_URL)
    await admin.execute('CREATE DATABASE '+name)
    async def init(c):await c.set_type_codec('jsonb',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')
    p=await asyncpg.create_pool(DATABASE_URL.rsplit('/',1)[0]+'/'+name,min_size=1,max_size=2,init=init)
    try:
        await db.migrate(p);await db.migrate(p)
        assert await p.fetchval('SELECT count(*) FROM schema_migrations')==len(list(Path('migrations').glob('*.sql')))
        print('PASS migration 006 and repeated full migration',flush=True)
        # More than the former 2,000-candidate cutoff; empty accounts sleep a day.
        for i in range(1,3002,1000):
            await d.register(p,[(addr(j),float(4000-j)) for j in range(i,min(i+1000,3002))],'public_leaderboard_full')
        assert await p.fetchval('SELECT count(*) FROM hl_accounts')==3001
        seen=[]
        for _ in range(3):
            row=await d.pick(p,8);seen.append(row['address']);await d.observed(p,row,None,100000)
        assert len(set(seen))==3
        assert await p.fetchval("SELECT count(*) FROM hl_accounts WHERE next_check_at>now()+interval '23 hours'")==3
        print('PASS full candidate queue, durable progress and inactive-account backoff',flush=True)
        # Same fill from REST, WS and a restarted collector must be one signal.
        ms=int(__import__('time').time()*1000);a,b=addr(6000),addr(6001)
        q=d.TradeBuffer();q.add([{'coin':'BTC','px':'100000','sz':'.01','time':ms,'tid':1,'users':[a,b]},
                               {'coin':'BTC','px':'100000','sz':'.02','time':ms,'tid':2,'users':[a,b]}])
        rows=list(q.rows.values());assert await d.flush_trades(p,rows)==2
        assert await d.flush_trades(p,rows)==0
        assert await p.fetchval('SELECT activity_usd FROM hl_accounts WHERE address=$1',a)==3000.
        assert await p.fetchval('SELECT position IS NULL FROM hl_accounts WHERE address=$1',a)
        print('PASS small-fill accumulation, durable deduplication and no synthetic positions',flush=True)
        pos={'size_btc':20.,'entry':100000.,'liquidation_price':90000.,'leverage':5.,'margin_mode':'cross'}
        row=await p.fetchrow('SELECT * FROM hl_accounts WHERE address=$1',a)
        await d.observed(p,row,pos,100000);await d.cohort(p)
        assert await p.fetchval('SELECT tracking FROM hl_accounts WHERE address=$1',a)
        await p.execute('UPDATE hl_accounts SET next_check_at=now(),attempted_at=now() WHERE address=$1',a)
        assert (await d.pick(p,1))['address']==a
        assert (await d.pick(p,8))['address']!=a
        # Query errors preserve last success and exposure; retry timestamp persists.
        row=await p.fetchrow('SELECT * FROM hl_accounts WHERE address=$1',a)
        before=row['observed_at'];await d.failed(p,row)
        row=await p.fetchrow('SELECT * FROM hl_accounts WHERE address=$1',a)
        assert row['observed_at']==before and row['position']==pos and row['error_count']==1
        assert row['next_check_at']>before
        print('PASS refresh/discovery fairness, error preservation and per-account retry',flush=True)
        collector=hyperliquid.Collector(p,None);collector.mark=100000
        collector.info=AsyncMock(return_value=[{'subAccountUser':addr(7000)}])
        await collector.relations()
        collector.info=AsyncMock(return_value=[{'vaultAddress':addr(7001),'equity':'999999999999'}])
        await collector.relations()
        collector.info=AsyncMock(return_value={'relationship':{'type':'parent','data':{'childAddresses':[addr(7002),addr(7002)]}}})
        await collector.relations()
        assert await p.fetchval("SELECT count(*) FROM hl_accounts WHERE discovery IN ('public_subaccount','public_vault','public_vault_child')")==3
        assert len(await hyperliquid.current(p))==1
        assert (await d.pick(p,4))['address'] in {addr(7000),addr(7001),addr(7002)},'Linked accounts must not wait behind the entire leaderboard'
        print('PASS linked subaccounts/vaults/children; investor equity excluded',flush=True)
        # Optional endpoint failure does not stop the independently scheduled account job.
        await collector.job('relations',AsyncMock(side_effect=RuntimeError('fixture')),30)
        assert await p.fetchval("SELECT status FROM feed_coverage WHERE exchange='hyperliquid' AND feed='job_relations'")=='retrying'
        done=AsyncMock();await collector.job('unrelated',done,1);done.assert_awaited_once()
        print('PASS optional-job failure isolation',flush=True)
        before=await p.fetchval('SELECT count(*) FROM hl_accounts')
        assert await d.register(p,[(addr(8000),1.)],'public_btc_trade',limit=before)==0
        await p.execute("UPDATE hl_accounts SET observed_at=now()-interval '8 hours',next_check_at=now()+interval '1 day' WHERE address=$1",seen[0])
        assert await d.register(p,[(addr(8000),1.)],'public_btc_trade',limit=before)==1
        assert await p.fetchval('SELECT count(*) FROM hl_accounts')==before
        assert await p.fetchval('SELECT EXISTS(SELECT 1 FROM hl_accounts WHERE address=$1)',a)
        assert not await p.fetchval('SELECT EXISTS(SELECT 1 FROM hl_accounts WHERE address=$1)',seen[0])
        print('PASS bounded capacity, inactive rotation and protection of BTC history',flush=True)
        report=await d.diagnostics(p)
        assert report['first_btc_discoveries'][0]['accounts']==1
        assert a not in json.dumps(report,default=str)
        assert report['first_btc_discoveries'][0]['current_fresh_gross_btc']==20.
        print('PASS discovery attribution, fresh BTC units and public diagnostics privacy',flush=True)
    finally:
        await p.close();await admin.execute('DROP DATABASE '+name);await admin.close()


if __name__=='__main__':asyncio.run(main())
