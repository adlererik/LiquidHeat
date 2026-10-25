"""Synthetic *unit-test* inputs only; never inserted into live market history."""
from datetime import datetime,timedelta,timezone
import copy
import unittest
from unittest.mock import patch
import numpy as np
from app.learning_math import (FEATURES,fit_ridge,predict,transformed,metrics,
    promotion_report,paired_day_bounds,audited_window)
from app.feed_audit import FeedAudit
from app import learning
from unittest.mock import AsyncMock


def minute(stamp,**extra):
    d={'ts':datetime.fromtimestamp(stamp,timezone.utc),'first_seen':datetime.fromtimestamp(stamp+1,timezone.utc),
       'last_seen':datetime.fromtimestamp(stamp+59,timezone.utc),'messages':100,'max_gap_s':2.,'max_delay_s':1.,'liquidation_ack':True}
    d.update(extra);return d


def holdout():
    first=datetime(2026,1,1,tzinfo=timezone.utc);rows=[]
    for i in range(14*96):
        value=10000.+(i%20)*1000
        rows.append({'target_start':first+timedelta(minutes=15*i),'outcome_usd':value,
            'candidate_usd':value,'baseline_typical_usd':value*2,
            'baseline_persistence_usd':value*.5,'champion_usd':value*1.5,'eligible':True})
    return rows


class LearningMathTests(unittest.TestCase):
    def test_ridge_generalizes_simple_signal_and_is_deterministic(self):
        rng=np.random.default_rng(4);x=rng.normal(size=(800,len(FEATURES)));x[:,0]=1
        y=10000*np.expm1(np.clip(2+.4*x[:,1],0,10))
        artifact=fit_ridge(x[:600],y[:600]);other=fit_ridge(x[:600],y[:600])
        self.assertEqual(artifact,other)
        pred=[predict(artifact,r) for r in x[600:]]
        self.assertLess(metrics(y[600:],pred)['log_mse'],.002)
        self.assertLess(metrics(y[600:],pred)['log_mse'],metrics(y[600:],np.repeat(y[:600].mean(),200))['log_mse'])
    def test_prediction_does_not_modify_training_artifact(self):
        x=np.ones((50,len(FEATURES)));a=fit_ridge(x,np.arange(50)*1000);before=copy.deepcopy(a)
        self.assertTrue(np.isfinite(predict(a,np.ones(len(FEATURES))*1e6)))
        self.assertEqual(a,before)
    def test_invalid_targets_and_features_are_rejected(self):
        for value in ([np.nan],[-1],[np.inf]):
            with self.assertRaises(ValueError):transformed(value)
        with self.assertRaises(ValueError):fit_ridge([],[])
        with self.assertRaises(ValueError):fit_ridge([[1,2]],[1])
    def test_malformed_artifact_is_rejected(self):
        a=fit_ridge(np.ones((30,len(FEATURES))),np.arange(30))
        a['spread'][0]=0
        with self.assertRaises(ValueError):predict(a,np.ones(len(FEATURES)))
    def test_constant_features_and_zero_outcomes_are_finite(self):
        a=fit_ridge(np.ones((40,len(FEATURES))),np.zeros(40))
        self.assertEqual(predict(a,np.ones(len(FEATURES))),0)
    def test_audited_zero_event_window_can_be_evaluated(self):
        okay,audit=audited_window([minute(i) for i in range(0,900,60)],0,900)
        self.assertTrue(okay);self.assertEqual(audit['audited_minutes'],15)
    def test_silent_or_missing_minute_is_not_a_zero_label(self):
        rows=[minute(i) for i in range(0,840,60)]
        self.assertFalse(audited_window(rows,0,900)[0])
    def test_lag_gap_unacknowledged_and_partial_sessions_are_excluded(self):
        for bad in [dict(max_gap_s=31),dict(max_delay_s=11),dict(liquidation_ack=False),
                    dict(first_seen=datetime.fromtimestamp(20,timezone.utc)),dict(last_seen=datetime.fromtimestamp(40,timezone.utc))]:
            rows=[minute(i) for i in range(0,900,60)];rows[0].update(bad)
            self.assertFalse(audited_window(rows,0,900)[0])
    def test_strong_model_can_pass_fixed_holdout_gate(self):
        report=promotion_report(holdout(),'test-model')
        self.assertTrue(report['promote'],report)
        self.assertGreater(report['day_block_improvement']['lower'],0)
    def test_too_little_evidence_cannot_promote(self):
        self.assertFalse(promotion_report(holdout()[:100],'small')['promote'])
    def test_equal_or_worse_models_cannot_promote(self):
        rows=holdout()
        for r in rows:r['candidate_usd']=r['champion_usd']
        self.assertFalse(promotion_report(rows,'equal')['promote'])
        for r in rows:r['candidate_usd']=r['outcome_usd']*4
        self.assertFalse(promotion_report(rows,'bad')['promote'])
    def test_ineligible_coverage_cannot_train_a_promotion(self):
        rows=holdout()
        for r in rows[::2]:r['eligible']=False
        report=promotion_report(rows,'gaps')
        self.assertFalse(report['promote']);self.assertEqual(report['eligible_windows'],672)
    def test_zero_only_holdout_cannot_establish_skill(self):
        rows=holdout()
        for r in rows:r.update(outcome_usd=0,candidate_usd=0,baseline_typical_usd=0,baseline_persistence_usd=0,champion_usd=0)
        self.assertFalse(promotion_report(rows,'quiet')['promote'])
    def test_tying_a_perfect_reference_is_not_an_improvement(self):
        rows=holdout()
        for r in rows:r['champion_usd']=r['outcome_usd']
        report=promotion_report(rows,'perfect-tie')
        self.assertFalse(report['promote']);self.assertEqual(report['relative_improvement'],0)
    def test_day_block_uncertainty_is_reproducible(self):
        rows=holdout();y=[r['outcome_usd'] for r in rows];days=[r['target_start'].date().isoformat() for r in rows]
        a=paired_day_bounds(y,y,[r['champion_usd'] for r in rows],days,'fixed')
        self.assertEqual(a,paired_day_bounds(y,y,[r['champion_usd'] for r in rows],days,'fixed'))
        self.assertIsNone(paired_day_bounds([1],[1],[2],['one'],'fixed')['lower'])


class FeedAuditTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_forecast_pair_rolls_back_at_deadline(self):
        class Transaction:
            rolled_back=False
            async def __aenter__(self):return self
            async def __aexit__(self,typ,value,tb):self.rolled_back=typ is not None
        class Connection:
            def __init__(self):self.tx=Transaction();self.inserts=0
            def transaction(self):return self.tx
            async def execute(self,*args):pass
            async def fetch(self,*args):return []
            async def fetchval(self,sql,*args):
                if 'INSERT INTO learning_forecasts' in sql:
                    self.inserts+=1
                    return 1 if self.inserts==1 else None
                return None
        class Acquisition:
            def __init__(self,c):self.c=c
            async def __aenter__(self):return self.c
            async def __aexit__(self,*args):pass
        class Pool:
            def __init__(self):self.c=Connection()
            def acquire(self):return Acquisition(self.c)
            async def fetchval(self,sql,*args):
                return datetime.now(timezone.utc) if sql=='SELECT clock_timestamp()' else None
            async def fetch(self,*args):return []
        p=Pool();source=({'long':[1.]*len(FEATURES),'short':[1.]*len(FEATURES)}, {},datetime.now(timezone.utc)-timedelta(minutes=5),{})
        with patch('app.learning.source_features',new=AsyncMock(return_value=source)):
            with self.assertRaises(learning.ForecastDeadline):await learning.issue(p)
        self.assertTrue(p.c.tx.rolled_back)

    async def test_ack_and_actual_receipt_gap_are_persisted(self):
        class Pool:
            def __init__(self):self.calls=[]
            async def execute(self,*args):self.calls.append(args)
        p=Pool();a=FeedAudit(p,'bybit')
        with patch('app.feed_audit.time.time',return_value=121.):
            await a.observe({'op':'subscribe','success':True})
        with patch('app.feed_audit.time.time',return_value=125.):
            await a.observe({'ts':125000})
        with patch('app.feed_audit.time.time',return_value=157.):
            await a.observe({'ts':157000})
        self.assertTrue(a.ack);self.assertTrue(p.calls)
        last=p.calls[-1]
        self.assertEqual(last[-3],32.);self.assertTrue(last[-1])


if __name__=='__main__':unittest.main()
