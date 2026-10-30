"""Read-only deployed CEX and project-proxy checks; run in a Debian one-off container."""
import asyncio
from contextlib import AsyncExitStack
import json
import math
import time
import aiohttp


async def main():
    report={'checks':[], 'started_at':time.time()}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
        async def get(path, expected=200):
            async with session.get('http://web:8080'+path) as response:
                body=await response.text()
                assert response.status==expected,(path,response.status,body[:150])
                return json.loads(body)
        for lookback,bucket in [('24h',25),('3d',50),('7d',100),('14d',200),('30d',200)]:
            start=time.monotonic()
            d=await get(f'/api/v1/heatmap?source=cex&cex_model=sharp&lookback={lookback}&bucket={bucket}')
            assert d['matrix'] and d['candles'] and d['profile'] and d['clld']
            assert d['cex_model']=='sharp' and d['normalization']['method']=='significant-p98.5'
            assert d['times']==sorted(set(d['times']))
            assert all(t%(d['time_resolution_s']*1000)==0 for t in d['times'])
            assert all(c[0]%(d['time_resolution_s']*1000)==0 for c in d['candles'])
            assert all(0<=x<len(d['times']) and 0<=y<len(d['prices']) and math.isfinite(v) and v>=0 for x,y,v in d['matrix'])
            for r in d['clld']:
                assert abs(r['usd']-r['long_usd']+r['short_usd'])<.01
                assert abs(r['normalized']-r['usd']/max(1.,r['long_usd']+r['short_usd']))<1e-10
            assert all(r['status']=='live' for r in d['cex_health']),d['cex_health']
            result=dict(lookback=lookback,bucket=bucket,cells=len(d['matrix']),seconds=round(time.monotonic()-start,3))
            report['checks'].append(result);print('PASS CEX live',result,flush=True)
            await asyncio.sleep(.4)
        for model in ('reference','no_extra','high_leverage','fast_decay'):
            d=await get(f'/api/v1/heatmap?source=cex&cex_model={model}&lookback=24h&bucket=100&exchanges=bybit&leverage=50,100,125')
            assert d['cex_model']==model and d['matrix'] and d['profile']
            assert d['time_resolution_s']==3600
            report['checks'].append(dict(model=model,filtered=True));print('PASS filtered alternative',model,flush=True)
            await asyncio.sleep(.4)
        d=await get('/api/v1/heatmap?source=cex&cex_model=sharp&exchanges=okx&leverage=125&bucket=100')
        assert not d['matrix'] and not d['profile']
        await get('/api/v1/heatmap?source=hyperliquid&cex_model=sharp',422)
        d=await get('/api/v1/magnets?source=cex&cex_model=sharp&limit=5')
        assert 0<len(d['zones'])<=5 and all(math.isfinite(z['z_score']) for z in d['zones'])
        print('PASS caps, source isolation and magnets',flush=True)
        # Four admitted WebSockets must not consume the six HTTP slots. Use a
        # separate one-off container IP so user browser slots are not borrowed.
        async with AsyncExitStack() as stack:
            for _ in range(4):
                ws=await stack.enter_async_context(session.ws_connect('http://web:8080/ws/stream',heartbeat=15))
                assert (await ws.receive_json(timeout=60))['type']=='hello'
            assert (await get('/api/v1/diagnostics'))['cex_experiments']
            assert (await get('/healthz'))['status']=='ok'
            types=set();until=time.monotonic()+30
            while time.monotonic()<until:
                event=await ws.receive_json(timeout=30);types.add(event['type'])
                if 'mark' in types and ('profile' in types or 'heartbeat' in types):break
            assert 'mark' in types,types
            report['ws_types']=sorted(types)
        print('PASS four WebSockets plus HTTP; streams closed',flush=True)
    report['passed']=True
    print(json.dumps(report),flush=True)


if __name__=='__main__':
    asyncio.run(main())
