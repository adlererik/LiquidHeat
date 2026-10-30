import json
import unittest
from dataclasses import asdict
import numpy as np
from app.config import VERSION
from app.model import State,WEIGHTS,integrated_kernel,encode_array
from app.cex_scenarios import SCENARIOS,significant_norm,version
from tests.test_model import interval


class CexScenarios(unittest.TestCase):
    def test_reference_is_bitwise_identical_to_legacy(self):
        a,b=State.empty(80000),State.empty(80000,'reference')
        for t in range(300,3300,300):
            kw=interval(t,profile=[(79950,100),(80050,200)])
            a.step(**kw);b.step(**kw)
            np.testing.assert_array_equal(a.exposure,b.exposure)
        self.assertEqual(VERSION,'conservation-v1.0')
        self.assertNotIn('scenario',json.loads(a.checkpoint()))

    def test_sharp_keeps_weights_decay_and_collateral(self):
        s=SCENARIOS['sharp'];np.testing.assert_array_equal(s.weights,WEIGHTS)
        self.assertEqual(s.decay,.9996)
        self.assertEqual(s.margins,((0.,.65),(.01,.25),(.04,.10)))

    def test_alternatives_change_one_economic_assumption(self):
        base=asdict(SCENARIOS['sharp'])
        for name,key in [('no_extra','margins'),('high_leverage','weights'),('fast_decay','decay')]:
            other=asdict(SCENARIOS[name])
            changed={k for k in base if base[k]!=other[k]}-{'label','snapshot_seconds'}
            self.assertEqual(changed,{key})

    def test_narrow_profile_reduces_peak_width(self):
        out=[]
        for name in ('reference','sharp'):
            s=State.empty(80000,name)
            s.step(**interval(profile=[(80000,100)],weights=[1,0,0,0,0]))
            p=s.exposure[0,0];i=int(p.argmax());lo=hi=i
            while lo>0 and p[lo-1]>=p[i]/2:lo-=1
            while hi+1<len(p) and p[hi+1]>=p[i]/2:hi+=1
            out.append(hi-lo+1)
        self.assertLess(out[1],out[0]/4)

    def test_four_sigma_mass_bound_without_edge_renormalization(self):
        b=np.arange(40000.,120000.,25.);mu=80311.;sigma=28.
        local=b[(b>=mu-4*sigma-12.5)&(b<=mu+4*sigma+12.5)]
        mass=integrated_kernel(local,mu,sigma).sum()
        self.assertGreaterEqual(mass,1-6.335e-5);self.assertLessEqual(mass,1.)
        self.assertLess(integrated_kernel(b,20000.,sigma).sum(),1e-12)

    def test_all_scenarios_conserve_units_caps_and_recover(self):
        for name in SCENARIOS:
            a,b=State.empty(80000,name),State.empty(80000,name)
            for t in range(300,1800,300):
                a.step(**interval(t));b.step(**interval(t,oi_native=101000,previous_oi_native=100000,volume_native=10000,contract_btc=.01))
            np.testing.assert_allclose(a.exposure,b.exposure,rtol=1e-10,atol=1e-12)
            self.assertTrue(np.all(a.exposure.sum(axis=(1,2))<=1010))
            before=a.exposure.copy();self.assertTrue(a.step(**interval(1500))['replayed'])
            np.testing.assert_array_equal(before,a.exposure)
            restored=State.restore(a.checkpoint(),encode_array(a.exposure))
            self.assertEqual(restored.scenario,name)
            a.step(**interval(1800));restored.step(**interval(1800))
            np.testing.assert_allclose(a.exposure,restored.exposure,rtol=2e-6,atol=1e-10)
            self.assertEqual(version(name),'cex-research-v1.1-'+name)

    def test_significant_normalization_ignores_dust_and_handles_empty(self):
        strong=[100000.,1000000.]
        self.assertAlmostEqual(significant_norm(strong),986500.)
        self.assertEqual(significant_norm(strong+[1.]*10000),significant_norm(strong))
        self.assertEqual(significant_norm([]),1.)
        self.assertEqual(significant_norm([0,float('nan'),float('inf'),-1]),1.)

    def test_invalid_scenario_rejected(self):
        with self.assertRaises(ValueError):State.empty(80000,'not-a-model')

    def test_exact_anchor_reconstructs_checkpoint_grid(self):
        s=State.empty(80000,'sharp')
        for t in range(300,1800,300):s.step(**interval(t))
        full=State.restore(s.checkpoint(),encode_array(s.exposure))
        compact=json.loads(s.checkpoint());compact.pop('buckets')
        compact['buckets']=(s.buckets[0]+np.arange(len(s.buckets))*25).tolist()
        anchor=State.restore(compact,encode_array(s.exposure))
        np.testing.assert_array_equal(anchor.exposure,full.exposure)
        self.assertEqual(anchor.checkpoint(),full.checkpoint())
