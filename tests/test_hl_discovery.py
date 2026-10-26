import json
import unittest
from unittest.mock import AsyncMock,patch
from app import hl_discovery as d
from app.hyperliquid import Collector

A='0x'+'1'*40
B='0x'+'2'*40


def trade(tid=1, size=.01, when=1000000):
    return {'coin':'BTC','px':'100000','sz':str(size),'time':when,'tid':tid,'users':[A,B]}


class DiscoveryMath(unittest.TestCase):
    def test_small_fills_and_both_counterparties(self):
        q=d.TradeBuffer();q.add([trade()],1000000)
        self.assertEqual(list(q.rows.values()),[(1000000,'1',1000.,A,B)])

    def test_replayed_fill_not_counted_twice_in_buffer(self):
        q=d.TradeBuffer();q.add([trade(),trade()],1000000)
        self.assertEqual(len(q.rows),1)

    def test_tid_reuse_at_different_block_times_is_distinct(self):
        q=d.TradeBuffer();q.add([trade(),trade(when=1000001)],1000001)
        self.assertEqual(len(q.rows),2)

    def test_queue_is_bounded_and_reports_loss(self):
        q=d.TradeBuffer(limit=2);q.add([trade(i) for i in range(3)],1000000)
        self.assertEqual(len(q.rows),2);self.assertEqual(q.dropped,1)

    def test_invalid_and_wrong_chain_fields_rejected(self):
        q=d.TradeBuffer()
        variants=[dict(trade(),coin='ETH'),dict(trade(),px='NaN'),dict(trade(),sz='-1'),
                  dict(trade(),tid=True),dict(trade(),users=[A,'bc1test']),
                  dict(trade(),time=-10000000),dict(trade(),time=999999999),dict(trade(),users=[A,'0x'+'0'*40])]
        q.add(variants,1000000);self.assertFalse(q.rows)

    def test_all_leaderboard_candidates_include_zero_equity(self):
        self.assertEqual(d.leaderboard_candidate({'ethAddress':A,'accountValue':'0'}),(A,0.))
        self.assertIsNone(d.leaderboard_candidate({'ethAddress':A,'accountValue':'NaN'}))

    def test_subaccounts_and_vaults_are_addresses_only(self):
        self.assertEqual(d.linked_addresses('subAccounts',[{'subAccountUser':A,'clearinghouseState':{'fake':'ignored'}},{'subAccountUser':A}]),[A])
        self.assertEqual(d.linked_addresses('userVaultEquities',[{'vaultAddress':B,'equity':'999999999999'}]),[B])
        self.assertEqual(d.linked_addresses('subAccounts',None),[])
        with self.assertRaises(ValueError):d.linked_addresses('userVaultEquities',{'error':'bad'})

    def test_vault_children_not_follower_wallets(self):
        self.assertEqual(d.linked_addresses('vaultDetails',{'followers':[{'user':A}],
            'relationship':{'type':'parent','data':{'childAddresses':[B,B]}}}),[B])

    def test_adaptive_refresh_without_guessed_null_thresholds(self):
        self.assertEqual(d.refresh_seconds(None,100000),86400)
        self.assertEqual(d.refresh_seconds({'size_btc':100,'liquidation_price':None},100000),300)
        self.assertEqual(d.refresh_seconds({'size_btc':1,'liquidation_price':90000},100000),600)
        self.assertEqual(d.refresh_seconds({'size_btc':1,'liquidation_price':99000},100000),120)

    def test_weight_reservation_for_initial_candle_backfill(self):
        self.assertEqual(d.request_weight({'type':'clearinghouseState'}),2)
        self.assertGreaterEqual(d.request_weight({'type':'candleSnapshot','req':{'startTime':0,'endTime':86400000}}),25)


class WeightTests(unittest.IsolatedAsyncioTestCase):
    async def test_ip_budget_waits_for_sliding_window(self):
        now=[0.];waits=[]
        async def sleep(seconds):waits.append(seconds);now[0]+=seconds
        b=d.WeightBudget(lambda:now[0],sleep)
        for _ in range(30):await b.reserve(20)
        self.assertEqual(waits,[])
        await b.reserve(2)
        self.assertEqual(waits,[60.])
        self.assertEqual(sum(v for _,v in b.used),2)


class SeedTests(unittest.IsolatedAsyncioTestCase):
    class Response:
        def __init__(self,data):self.data=data;self.content=self
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def raise_for_status(self):pass
        async def iter_chunked(self,n):
            for i in range(0,len(self.data),997):yield self.data[i:i+997]
    class Session:
        def __init__(self,data):self.data=data
        def get(self,*args,**kwargs):return SeedTests.Response(self.data)

    async def test_streamed_full_leaderboard_admits_beyond_previous_cutoff(self):
        rows=[{'ethAddress':'0x'+format(i,'040x'),'accountValue':str(i),'displayName':'not retained'} for i in range(1,3002)]
        payload=json.dumps({'leaderboardRows':rows}).encode()
        c=Collector(object(),self.Session(payload))
        with patch('app.hl_discovery.register',new_callable=AsyncMock,return_value=0) as register,patch('app.db.coverage',new_callable=AsyncMock):
            await c.seed()
            admitted=[r for call in register.await_args_list for r in call.args[1]]
            self.assertEqual(len(admitted),3001)
            self.assertEqual(len({a for a,_ in admitted}),3001)
            self.assertNotIn('not retained',str(admitted))

    async def test_truncated_download_does_not_admit_or_checkpoint(self):
        c=Collector(object(),self.Session(b'{"leaderboardRows":[{"ethAddress":"'+A.encode()+b'","accountValue":"1"}'))
        with patch('app.hl_discovery.register',new_callable=AsyncMock) as register,patch('app.db.coverage',new_callable=AsyncMock) as coverage:
            with self.assertRaises(ValueError):await c.seed()
            register.assert_not_awaited();coverage.assert_not_awaited()


if __name__=='__main__':unittest.main()
