"""Persist actual receipt continuity; silence/disconnections never become zero labels."""
import time
import uuid
from app import db


class FeedAudit:
    def __init__(self,pool,exchange):
        self.p,self.exchange=pool,exchange
        self.session=uuid.uuid4().hex
        self.ack=exchange=='binance'  # combined-URL subscription, sampled and never fitted
        self.bin=None;self.row=None;self.previous=None;self.saved=0.

    async def observe(self,payload):
        wall=time.time();minute=int(wall//60)*60
        if self.bin is not None and self.bin!=minute:
            await self.flush()
            self.row=None
        self.bin=minute
        if self.exchange=='bybit' and payload.get('op')=='subscribe':
            self.ack=payload.get('success') is True
        if self.exchange=='okx' and payload.get('arg',{}).get('channel')=='liquidation-orders':
            if payload.get('event')=='subscribe':self.ack=True
            if payload.get('event')=='error':self.ack=False
        if payload.get('success') is False:self.ack=False
        stamp=payload.get('ts')
        if self.exchange=='binance':stamp=payload.get('data',payload).get('E')
        if self.exchange=='okx' and payload.get('data'):
            stamp=payload['data'][0].get('ts')
        delay=abs(wall-float(stamp)/1000) if stamp is not None else 0.
        gap=wall-self.previous if self.previous is not None else 0.
        if self.row is None:
            self.row={'first':wall,'last':wall,'messages':0,'gap':0.,'delay':0.,'ack':self.ack}
        r=self.row;r['last']=wall;r['messages']+=1
        r['gap']=max(r['gap'],gap);r['delay']=max(r['delay'],delay);r['ack']=r['ack'] and self.ack
        self.previous=wall
        if wall-self.saved>=10:await self.flush()

    async def flush(self):
        if not self.row:return
        r=self.row
        await self.p.execute('''INSERT INTO learning_feed_windows VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)
        ON CONFLICT(ts,exchange,session_id) DO UPDATE SET last_seen=GREATEST(learning_feed_windows.last_seen,excluded.last_seen),
        messages=GREATEST(learning_feed_windows.messages,excluded.messages),
        max_gap_s=GREATEST(learning_feed_windows.max_gap_s,excluded.max_gap_s),
        max_delay_s=GREATEST(learning_feed_windows.max_delay_s,excluded.max_delay_s),
        liquidation_ack=learning_feed_windows.liquidation_ack AND excluded.liquidation_ack''',
        db.utc(self.bin),self.exchange,self.session,db.utc(r['first']),db.utc(r['last']),r['messages'],r['gap'],r['delay'],r['ack'])
        self.saved=time.time()
