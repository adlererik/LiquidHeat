"""Read-only streaming verification of every frozen CEX input and scenario."""
import asyncio
from datetime import datetime,timedelta,timezone
import hashlib
import json
import sys
import time
import zlib
import numpy as np
from app import cex_worker
from app.model import State,decode_array
from app.cex_scenarios import SCENARIOS

async def main(recent=False):
    c=await cex_worker.connect();work={};report=[];begin=time.monotonic();announced={}
    try:
        while len(report)<3:
            runs=await c.fetch('SELECT * FROM cex_runs ORDER BY exchange');processed=0
            for run in runs:
                ex=run['exchange'];fresh=ex not in work
                w=work.setdefault(ex,dict(last=0,count=0,replayed=0,seed=0,chain='',states={},target=None,done=False))
                if fresh:
                    seed_before=datetime.now(timezone.utc)-timedelta(hours=6) if recent else datetime.now(timezone.utc)-timedelta(days=35)
                    anchor=await c.fetchval("SELECT max(ts) FROM cex_snapshots WHERE exchange=$1 AND scenario='reference' AND ts<=$2 AND audit ? 'checkpoint_meta'",ex,seed_before)
                    if anchor:
                        seeds=await c.fetch('SELECT * FROM cex_snapshots WHERE exchange=$1 AND ts=$2',ex,anchor)
                        assert len(seeds)==len(SCENARIOS)
                        for r in seeds:
                            meta=dict(r['audit']['checkpoint_meta'])
                            meta['buckets']=(r['grid_start']+np.arange(r['grid_n'])*25).tolist()
                            w['states'][r['scenario']]=State.restore(meta,r['matrix'])
                        seed=seeds[0];w.update(last=int(anchor.timestamp()),seed=int(anchor.timestamp()),count=seed['audit']['checkpoint_meta']['steps'],chain=seed['audit']['chain_digest'])
                if w['done']:continue
                if w['target'] is None and run['status']=='live':
                    async with c.transaction(readonly=True,isolation='repeatable_read'):
                        fixed=await c.fetchrow('SELECT * FROM cex_runs WHERE exchange=$1',ex)
                        checkpoints=await c.fetch('SELECT * FROM cex_checkpoints WHERE exchange=$1',ex)
                    assert len(checkpoints)==len(SCENARIOS)
                    w['target']=(dict(fixed),{r['scenario']:r for r in checkpoints})
                limit=w['target'][0]['last_ts'] if w['target'] else run['last_ts']
                rows=await c.fetch('SELECT * FROM cex_inputs WHERE exchange=$1 AND ts>to_timestamp($2) AND ts<=to_timestamp($3) ORDER BY ts LIMIT 100',ex,float(w['last']),float(limit))
                for row in rows:
                    payload=zlib.decompress(row['payload']);digest=hashlib.sha256(payload).hexdigest()
                    assert digest==row['digest'];w['chain']=hashlib.sha256((w['chain']+digest).encode()).hexdigest()
                    item=json.loads(payload);w['last']=item['kwargs']['ts'];assert w['last']==int(row['ts'].timestamp())
                    if not w['states']:w['states']={name:State.empty(item['kwargs']['mark_close'],name) for name in SCENARIOS}
                    for state in w['states'].values():
                        cex_worker.apply(state,item)
                        if item['checkpoint_boundary']:
                            # Same float32 quantization as encode_array/restore, without compression work.
                            state.exposure=np.asarray(state.exposure,dtype='<f4').astype(np.float64)
                    w['count']+=1;w['replayed']+=1;processed+=1
                    await asyncio.sleep(0)
                if w['count']//1000>announced.get(ex,0):
                    announced[ex]=w['count']//1000;print(ex,w['count'],'inputs verified',flush=True)
                if w['target'] and w['last']==limit:
                    fixed,saved=w['target'];assert w['count']==fixed['inputs'] and w['chain']==fixed['input_digest']
                    for name,state in w['states'].items():
                        np.testing.assert_array_equal(state.exposure,decode_array(saved[name]['matrix'],state.exposure.shape))
                        assert state.last_ts==int(saved[name]['ts'].timestamp())
                    w['done']=True;result=dict(exchange=ex,inputs=w['count'],replayed_inputs=w['replayed'],seed_ts=w['seed'],scenarios=len(w['states']),exact_checkpoint_match=True)
                    report.append(result);print('PASS full frozen-input replay',result,flush=True)
            if not processed:await asyncio.sleep(5)
        print(json.dumps(dict(passed=True,venues=report,seconds=round(time.monotonic()-begin,2))),flush=True)
    finally:await c.close()

if __name__=='__main__':
    import uvloop
    uvloop.run(main('--recent' in sys.argv))
