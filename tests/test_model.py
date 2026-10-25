import unittest
import numpy as np
from app.model import State,WEIGHTS,openings,integrated_kernel,liquidation_price,calibration_proposal,encode_array
from app.exchanges import bybit_one_side


def interval(ts=300, **extra):
    kw=dict(ts=ts,entry=80000.,high=80200.,low=79900.,close=80100.,mark_low=79900.,mark_high=80200.,mark_close=80100.,oi_native=1010.,previous_oi_native=1000.,volume_native=100.)
    kw.update(extra)
    return kw


class NumericalTests(unittest.TestCase):
    def test_conservation_bounds_many_intervals(self):
        for v in [0,1,20,500,3000]:
            for d in np.linspace(-v,v,15):
                for alpha in [.01,.04,.15]:
                    o=openings(d,v,alpha);c=o-d
                    self.assertGreaterEqual(o,-1e-10);self.assertGreaterEqual(c,-1e-10)
                    self.assertLessEqual(o+c,v+1e-8)
                    self.assertAlmostEqual(o-c,d)
    def test_bybit_current_semantics(self):
        self.assertEqual(bybit_one_side({'openInterest':'200','singleOpenInterest':'100'})[0],100)
        self.assertEqual(bybit_one_side({'openInterest':'200'})[0],100)
    def test_integrated_gaussian_mass(self):
        b=np.arange(0.,200000.,25.)
        for sigma in [12.5,100,2000]:
            self.assertAlmostEqual(integrated_kernel(b,80000,sigma).sum(),1.,places=12)
    def test_offgrid_tails_not_piled_at_edge(self):
        k=integrated_kernel(np.arange(0.,10000.,25.),11000.,100.)
        self.assertLess(k.sum(),1e-10)
    def test_linear_equity_equation(self):
        for side in [0,1]:
            for leverage in [10,25,50,100,125]:
                e,n,m,d,f,extra=80000.,50000.,.004,35.,.0006,.01
                p=liquidation_price(e,leverage,side,m,d,n,f,extra)
                q=n/e
                equity=n/leverage+n*extra+(1 if side==0 else -1)*q*(p-e)
                self.assertAlmostEqual(equity,q*p*(m+f)-d,places=8)
    def test_high_leverage_infeasible_bracket(self):
        self.assertIsNone(liquidation_price(80000,125,0,.04))
    def test_replay_is_deterministic_and_idempotent(self):
        a,b=State.empty(80000),State.empty(80000)
        for t in range(300,3300,300):
            a.step(**interval(t));b.step(**interval(t))
        np.testing.assert_array_equal(a.exposure,b.exposure)
        before=a.exposure.copy();self.assertTrue(a.step(**interval(3000))['replayed'])
        np.testing.assert_array_equal(a.exposure,before)
    def test_checkpoint_recovery_float32_tolerance(self):
        a=State.empty(80000)
        for t in range(300,1800,300):a.step(**interval(t))
        b=State.restore(a.checkpoint(),encode_array(a.exposure))
        for t in range(1800,3000,300):a.step(**interval(t));b.step(**interval(t))
        np.testing.assert_allclose(a.exposure,b.exposure,rtol=2e-6,atol=1e-10)
    def test_sweeps_are_one_pass_and_use_mark(self):
        a=State.empty(80000);a.step(**interval())
        before=a.exposure.sum();s=a.sweep(72000,88000)
        self.assertGreater(s.sum(),0);self.assertLess(a.exposure.sum(),before)
        np.testing.assert_array_equal(a.sweep(72000,88000),[0,0])
    def test_oi_cap_each_side_and_native_contract_normalization(self):
        a,b=State.empty(80000),State.empty(80000)
        a.step(**interval())
        b.step(**interval(oi_native=101000,previous_oi_native=100000,volume_native=10000,contract_btc=.01))
        np.testing.assert_allclose(a.exposure,b.exposure,rtol=1e-10,atol=1e-12)
        self.assertTrue(np.all(a.exposure.sum(axis=(1,2))<=1010))
    def test_sampling_inconsistency_is_not_fabricated(self):
        a=State.empty(80000);r=a.step(**interval(oi_native=5000,volume_native=10))
        self.assertFalse(r['valid_sampling']);self.assertEqual(a.exposure.sum(),0)
    def test_future_observation_cannot_change_past_state(self):
        a=State.empty(80000);a.step(**interval());past=a.exposure.copy()
        a.step(**interval(600,entry=90000,mark_close=90000,mark_low=89000,mark_high=90100))
        self.assertFalse(np.array_equal(past,a.exposure))
        fresh=State.empty(80000);fresh.step(**interval());np.testing.assert_array_equal(past,fresh.exposure)
    def test_side_specific_weight_attribution(self):
        a=State.empty(80000);weights=[[1,0,0,0,0],[0,1,0,0,0]]
        a.step(**interval(weights=weights))
        self.assertEqual(a.exposure[0,1:].sum(),0);self.assertEqual(a.exposure[1,[0,2,3,4]].sum(),0)
    def test_invalid_weights_rejected(self):
        with self.assertRaises(ValueError):State.empty(80000).step(**interval(weights=[.2]*4))
    def test_nonfinite_rejected(self):
        with self.assertRaises(ValueError):State.empty(80000).step(**interval(entry=float('nan')))
    def test_observed_profile_cannot_inflate_mass(self):
        a=State.empty(80000);r=a.step(**interval(profile=[(79950,100),(80050,200)]))
        self.assertEqual(r['entry_method'],'observed_volume_profile')
        self.assertLess(a.exposure.sum(),2*r['new_contracts_each_side'])
    def test_calibration_bounded_and_censored_frozen(self):
        self.assertEqual(calibration_proposal(.04,1e9,1,False),.04)
        self.assertEqual(calibration_proposal(.04,1e9,1,True,True),.04)
        for alpha in [.01,.04,.15]:
            for real,pred in [(0,10000),(1e10,1),(10,1e10)]:
                x=calibration_proposal(alpha,real,pred,True)
                self.assertGreaterEqual(x,.01);self.assertLessEqual(x,.15)
    def test_clld_identity(self):
        a=State.empty(80000);a.step(**interval(buy_ratio=.9))
        l,s=a.exposure.sum(axis=(1,2))*a.last_mark
        self.assertGreater(l,s);self.assertLessEqual(abs((l-s)/(l+s)),1)
    def test_leverage_prior_sum(self):self.assertAlmostEqual(WEIGHTS.sum(),1)

if __name__=='__main__':unittest.main()
