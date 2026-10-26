import json
import unittest
from datetime import datetime,timedelta,timezone
import numpy as np
from app.hyperliquid import Collector
from app.hl_api import display_step
from app.hl_math import LeaderboardRows,btc_position,projection,pack,unpack,seed_row,large_trade_addresses


def position(size=1,liq=90000,address='0x'+'1'*40):
    return {'address':address,'size_btc':size,'entry':100000.,'liquidation_price':liq,'leverage':10.,'margin_mode':'cross'}


class AvailableHistoryResolution(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,26,18,tzinfo=timezone.utc)

    def test_young_history_keeps_native_detail_for_every_lookback(self):
        first=self.now-timedelta(hours=18)
        for days in (1,3,7,14,30):
            self.assertEqual(display_step(self.now,self.now-timedelta(days=days),first),300)

    def test_requested_window_bounds_mature_history(self):
        first=self.now-timedelta(days=35)
        for days,expected in ((1,300),(3,900),(7,2100),(14,4200),(30,9000)):
            self.assertEqual(display_step(self.now,self.now-timedelta(days=days),first),expected)

    def test_growing_history_aggregates_with_bounded_column_count(self):
        start=self.now-timedelta(days=30)
        for hours in (24.01,47,92,180,335,719):
            first=self.now-timedelta(hours=hours)
            step=display_step(self.now,start,first)
            self.assertEqual(step%300,0)
            self.assertLessEqual((self.now-first).total_seconds()/step,288)

    def test_empty_history_is_native_without_fabrication(self):
        self.assertEqual(display_step(self.now,self.now-timedelta(days=14),None),300)


class HyperliquidMath(unittest.TestCase):
    def test_reported_fields_and_signed_units(self):
        s={'assetPositions':[{'position':{'coin':'BTC','szi':'-2','entryPx':'100000','liquidationPx':'110000','leverage':{'type':'cross','value':20},'marginUsed':'5000'}}]}
        p=btc_position(s)
        self.assertEqual(p['size_btc'],-2)
        self.assertEqual(p['liquidation_price'],110000)
        self.assertEqual(p['leverage'],20)
    def test_null_not_inferred(self):
        s={'assetPositions':[{'position':{'coin':'BTC','szi':'1','entryPx':'100000','liquidationPx':None}}]}
        self.assertIsNone(btc_position(s)['liquidation_price'])
        _,a,audit,_=projection([position(liq=None)],100000)
        self.assertEqual(a.sum(),0);self.assertEqual(audit['null_thresholds'],1)
    def test_closed_position(self):
        self.assertIsNone(btc_position({'assetPositions':[]}))
        self.assertIsNone(btc_position({'assetPositions':[{'position':{'coin':'BTC','szi':'0','entryPx':'0'}}]}))
    def test_malformed_is_not_close(self):
        for state in ({},{'assetPositions':None},{'assetPositions':[{'position':{'coin':'BTC','szi':'NaN','entryPx':'1'}}]}):
            with self.assertRaises(ValueError):btc_position(state)
    def test_conservation_sides_gross_oi(self):
        _,a,audit,_=projection([position(2),position(-3,110000,'0x'+'2'*40)],100000,10)
        np.testing.assert_allclose(a.sum(axis=1),[2,3])
        self.assertAlmostEqual(audit['selected_share_of_market_gross'],.25)
    def test_direct_buckets_no_smoothing(self):
        _,a,_,_=projection([position()],100000)
        self.assertEqual(np.count_nonzero(a),1)
        self.assertEqual(a.max(),1)
    def test_outside_recorded_no_clamping(self):
        _,a,audit,_=projection([position(liq=1)],100000)
        self.assertEqual(a.sum(),0);self.assertEqual(audit['outside_grid_positions'],1)
    def test_top_thousand_rank_before_null_exclusion(self):
        items=[position(i+1,90000,'0x'+format(i,'040x')) for i in range(1001)]
        items[-1]['liquidation_price']=None
        _,_,audit,selected=projection(items,100000)
        self.assertEqual(len(selected),1000);self.assertEqual(min(p['size_btc'] for p in selected),2)
        self.assertEqual(audit['null_thresholds'],1)
    def test_compressed_replay(self):
        _,a,_,_=projection([position(1.23456)],100000)
        np.testing.assert_allclose(unpack(pack(a),a.shape[1]),a,rtol=1e-6)
    def test_leaderboard_stream_all_chunk_boundaries(self):
        text=json.dumps({'leaderboardRows':[{'ethAddress':'0x'+'a'*40,'accountValue':'7','displayName':'å'}, {'ethAddress':'0x'+'b'*40,'accountValue':'9'}]})
        for step in (1,2,5,31,128):
            parser=LeaderboardRows(); rows=[]
            for i in range(0,len(text),step):rows+=parser.feed(text[i:i+step])
            self.assertTrue(parser.ended);self.assertEqual(len(rows),2)
    def test_seed_rank_and_invalid(self):
        heap=[]
        for i,v in enumerate(('NaN','4','7','5','1')):seed_row(heap,{'ethAddress':'0x'+format(i,'040x'),'accountValue':v},2)
        self.assertEqual(sorted(v for v,a in heap),[5,7])
    def test_incomplete_stream_never_complete(self):
        parser=LeaderboardRows();parser.feed('{"leaderboardRows": [{"x":')
        self.assertFalse(parser.ended)
    def test_invalid_mark(self):
        for v in (0,float('nan'),-1):
            with self.assertRaises(ValueError):projection([],v)


class LargeTradeDiscovery(unittest.TestCase):
    def test_both_counterparties_at_threshold(self):
        a='0x'+'a'*40;b='0x'+'b'*40
        self.assertEqual(large_trade_addresses([{'coin':'BTC','px':'100000','sz':'.5','users':[a,b]}]),{a:50000.,b:50000.})
    def test_filter_does_not_assume_a_position(self):
        a='0x'+'a'*40
        self.assertEqual(large_trade_addresses([{'coin':'BTC','px':'100000','sz':'.4999','users':[a]}]),{})
        self.assertEqual(large_trade_addresses([{'coin':'ETH','px':'100000','sz':'1','users':[a]}]),{})
    def test_invalid_payloads_and_duplicate_addresses(self):
        a='0x'+'a'*40
        self.assertEqual(large_trade_addresses([{'coin':'BTC','px':'NaN','sz':'2','users':[a]}]),{})
        self.assertEqual(large_trade_addresses([{'coin':'BTC','px':'100000','sz':'1','users':a}]),{})
        self.assertEqual(large_trade_addresses([{'coin':'BTC','px':'100000','sz':'1','users':[a,a,'invalid','0x'+'0'*40]}]),{a:100000.})
