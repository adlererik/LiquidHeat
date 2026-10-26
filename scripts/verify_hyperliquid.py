"""Debian-only integration tests. Synthetic fixtures stay in a disposable DB."""
import asyncio
from datetime import datetime,timezone,timedelta
import json
import os
import uuid
import zlib
import asyncpg
import numpy as np
from app import db,hl_api,hyperliquid
from app.config import DATABASE_URL
from app.hl_math import projection,pack,unpack

async def main():
    name='liquidheat_hl_verify_'+uuid.uuid4().hex[:12]
    admin=await asyncpg.connect(DATABASE_URL)
    await admin.execute('CREATE DATABASE '+name)
    async def init(c):await c.set_type_codec('jsonb',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')
    p=await asyncpg.create_pool(DATABASE_URL.rsplit('/',1)[0]+'/'+name,min_size=1,max_size=1,init=init)
    try:
        await db.migrate(p);await db.migrate(p)
        assert await p.fetchval('SELECT count(*) FROM schema_migrations')==len(list(__import__('pathlib').Path('migrations').glob('*.sql')))
        print('PASS additive migrations and idempotence',flush=True)
        now=datetime.now(timezone.utc); address='0x'+'1'*40
        pos={'size_btc':2.,'entry':100000.,'liquidation_price':90000.,'leverage':7.,'margin_mode':'cross'}
        await p.execute("INSERT INTO hl_accounts(address,discovery,observed_at,position) VALUES($1,'test',$2,$3)",address,now,pos)
        await p.execute("INSERT INTO live_marks VALUES('hyperliquid',$1,100000)",now)
        current=await hyperliquid.current(p); assert len(current)==1
        grid,a,audit,selected=projection(current,100000,10)
        await p.execute('INSERT INTO hl_snapshots(ts,mark,grid_start,grid_n,matrix,constituents,audit) VALUES($1,100000,$2,$3,$4,$5,$6)',now-timedelta(minutes=10),grid,a.shape[1],pack(a),zlib.compress(json.dumps(selected).encode()),audit)
        before=await p.fetchval('SELECT md5(matrix) FROM hl_snapshots')
        for bucket in (25,50,100,200):
            result=await hl_api.build(p,'24h',bucket)
            assert result['source']=='hyperliquid' and not result['estimated'] and result['sampled']
            assert abs(sum(r['long_usd'] for r in result['profile'])-200000)<.01
            assert abs(result['clld'][-1]['long_usd']-sum(r['long_usd'] for r in result['profile']))<.01
            for c in result['clld']:assert abs(c['usd']-(c['long_usd']-c['short_usd']))<.01 and -1<=c['normalized']<=1
            assert address not in json.dumps(result,default=str)
        print('PASS bucket conservation, CLLD, API privacy and separate method',flush=True)
        await p.execute('UPDATE hl_accounts SET observed_at=$1',now-timedelta(minutes=16))
        assert not await hyperliquid.current(p)
        result=await hl_api.build(p,'24h',25);assert not result['profile'] and result['matrix']
        print('PASS stale observations excluded from current profile; history retained',flush=True)
        await p.execute('UPDATE hl_accounts SET observed_at=$1,position=NULL',now)
        assert not await hyperliquid.current(p)
        assert await p.fetchval('SELECT md5(matrix) FROM hl_snapshots')==before
        row=await p.fetchrow('SELECT * FROM hl_snapshots')
        replay=json.loads(zlib.decompress(row['constituents']))
        _,again,_,_=projection(replay,row['mark'],10)
        np.testing.assert_allclose(again,unpack(row['matrix'],row['grid_n']))
        print('PASS closures, immutable historical state and deterministic constituent replay',flush=True)
        from unittest.mock import AsyncMock
        collector=hyperliquid.Collector(p,None)
        ms=int(__import__('time').time()*1000)
        collector.trade_buffer.add([{'coin':'BTC','time':ms,'tid':1,'px':'60000','sz':'1','users':[address,'0x'+'2'*40]}])
        await collector.flush_signals()
        assert await p.fetchval('SELECT count(*) FROM hl_accounts')==2
        assert await p.fetchval('SELECT largest_trade_usd FROM hl_accounts WHERE address=$1',address)==60000
        assert await p.fetchval('SELECT signaled_at IS NOT NULL FROM hl_accounts WHERE address=$1',address)
        collector.trade_buffer.add([{'coin':'BTC','time':ms,'tid':2,'px':'50000','sz':'1','users':[address,'0x'+'2'*40]}])
        await collector.flush_signals()
        assert await p.fetchval('SELECT largest_trade_usd FROM hl_accounts WHERE address=$1',address)==60000
        collector.info=AsyncMock(return_value={'assetPositions':[]})
        await collector.account()
        assert collector.info.await_count==1
        print('PASS durable large-trade discovery, deduplication and normal account queries',flush=True)
        from app import api
        api.p=p
        from fastapi import HTTPException
        try:await api.heatmap(source='hyperliquid',leverage='125')
        except HTTPException as e:assert e.status_code==422
        else:raise AssertionError('CEX filter leaked into reported positions')
        response=await api.heatmap(source='hyperliquid')
        assert response.status_code==200
        print('PASS cached JSON serialization and incompatible filter rejection',flush=True)
    finally:
        await p.close();await admin.execute('DROP DATABASE '+name);await admin.close()

if __name__=='__main__':asyncio.run(main())
