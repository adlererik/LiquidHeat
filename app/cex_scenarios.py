"""Explicit CEX experiments; the production/learning model VERSION is unchanged."""
from dataclasses import dataclass,asdict
import numpy as np

PRIOR=(.20,.35,.25,.15,.05)


@dataclass(frozen=True)
class Scenario:
    label:str
    conditional:bool=False
    support:float=8.5
    decay:float=.9996
    weights:tuple=PRIOR
    margins:tuple=((0.,.65),(.01,.25),(.04,.10))
    snapshot_seconds:int=3600


SCENARIOS={
 'reference':Scenario('Replay reference'),
 'sharp':Scenario('Narrow kernels',conditional=True,support=4.,snapshot_seconds=300),
 'no_extra':Scenario('Narrow · no extra collateral',conditional=True,support=4.,margins=((0.,1.),)),
 'high_leverage':Scenario('Narrow · higher leverage prior',conditional=True,support=4.,weights=(.10,.22,.30,.23,.15)),
 'fast_decay':Scenario('Narrow · faster decay',conditional=True,support=4.,decay=.9988),
}


def version(scenario):
    if scenario not in SCENARIOS:raise ValueError('Unknown CEX scenario')
    return 'cex-research-v1.1-'+scenario


def specification(scenario):
    return dict(asdict(SCENARIOS[scenario]),version=version(scenario),
      scientific_status='Uncalibrated scenario; narrower bands do not establish predictive accuracy')


def significant_norm(values):
    a=np.asarray(values,dtype=float)
    a=a[np.isfinite(a)&(a>0)]
    if not len(a):return 1.
    significant=a[a>=a.max()*.01]
    return max(1.,float(np.percentile(significant if len(significant) else a,98.5)))


NORMALIZATION={'method':'significant-p98.5','minimum_peak_fraction':.01,'percentile':98.5}
