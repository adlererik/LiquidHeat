"""Deterministic conditional exposure model. All inputs are public observations.

Native one-sided OI conservation is enforced before USD conversion. Model output
is NOT account liquidation data. The unobserved leverage/collateral law is a prior.
"""
from dataclasses import dataclass
import json
import zlib
import numpy as np
from scipy.special import ndtr
from app.config import LEVERAGES, VERSION
from app.cex_scenarios import SCENARIOS,version as scenario_version

WEIGHTS = np.array([.20, .35, .25, .15, .05])


def openings(delta: float, volume: float, alpha: float = .04) -> float:
    """V = opens + closes + transfers; dOI = opens - closes.

    The unidentifiable matched open/close component is at most (V-|dOI|)/2.
    Sampling-inconsistent intervals are rejected by the caller, never repaired.
    """
    if volume < 0 or not .01 <= alpha <= .15:
        raise ValueError('invalid volume/alpha')
    return max(0., delta) + alpha * max(0., (volume - abs(delta)) / 2)


def vulnerability(delta, volume, buy_ratio=None, ratio_change=None, funding=None):
    # Missing data contributes zero signal; not an invented observation.
    s = .55 * (2 * buy_ratio - 1) if buy_ratio is not None else 0.
    s += .30 * np.tanh(ratio_change / .02) if ratio_change is not None else 0.
    s += .15 * np.tanh((funding - .0001) / .0003) if funding is not None else 0.
    s = np.clip(s, -.85, .85)
    oidv = abs(delta) / max(volume, 1e-12)
    return np.clip(.45 * np.array([1 + .6*s, 1 - .6*s]) * (1 + .5*oidv), .1, .9)


def liquidation_price(entry, leverage, side, mmr, deduction=0., notional=50000., fee=.0006, extra_margin=0.):
    """Linear isolated approximation solving equity = maintenance + fee reserve.

    q*entry/L + extra*q*entry + side*q*(P-entry) = q*P*(mmr+fee)-deduction.
    A cohort's assumed account size selects its bracket; unknown cross collateral
    is integrated as explicit extra-margin scenarios, not inferred private data.
    """
    q = notional / entry
    m = mmr + fee
    if leverage <= 0 or m >= 1/leverage:
        return None  # leverage is infeasible under this assumed risk bracket
    if side == 0:
        return max(0., (entry*(1-1/leverage-extra_margin)-deduction/q)/(1-m))
    return (entry*(1+1/leverage+extra_margin)+deduction/q)/(1+m)


def integrated_kernel(buckets, mu, sigma, width=25.):
    p = ndtr((buckets + width/2-mu)/sigma)-ndtr((buckets-width/2-mu)/sigma)
    # Do not renormalize an off-grid tail into an artificial boundary spike.
    return np.maximum(p, 0.)


def encode_array(a):
    return zlib.compress(np.asarray(a, dtype='<f4').tobytes(), 6)


def decode_array(b, shape):
    return np.frombuffer(zlib.decompress(b), dtype='<f4').reshape(shape).astype(np.float64)


def scenario_projection(exposure,buckets,pts,total,options,new,unit,phi,weights,high,low,mark,brackets,fee,profile):
    """Batch experimental cohorts in original order; cap temporary work at 128 cohorts.

    The legacy path below remains separate. Scatter-add retains each cohort's
    bucket amounts, without a dense price-by-price projection or renormalization.
    """
    margins=options.margins;n=len(margins)
    entry=np.repeat(np.asarray([p for p,v in pts],dtype=float),3*n)
    volume=np.repeat(np.asarray([v for p,v in pts],dtype=float),3*n)
    sizes=np.tile(np.repeat([10000.,50000.,250000.],n),len(pts))
    size_weights=np.tile(np.repeat([.35,.45,.20],n),len(pts))
    extra=np.tile([x for x,w in margins],len(pts)*3)
    collateral_weights=np.tile([w for x,w in margins],len(pts)*3)
    rates=[];deductions=[]
    for size in (10000.,50000.,250000.):
        b=next((b for b in (brackets or []) if b['floor']<=size<b['cap']),None)
        rates.append(b['mmr'] if b else .004);deductions.append(b.get('deduction',0.) if b else 0.)
    base=len(entry)
    entry=np.tile(entry,10);volume=np.tile(volume,10);sizes=np.tile(sizes,10)
    size_weights=np.tile(size_weights,10);extra=np.tile(extra,10);collateral_weights=np.tile(collateral_weights,10)
    side=np.repeat(np.repeat([0,1],5),base);tier=np.repeat(np.tile(np.arange(5),2),base)
    leverage=np.repeat(np.tile(np.array(LEVERAGES,dtype=float),2),base)
    m=np.tile(np.repeat(rates,n),len(pts)*10)+fee
    deduction=np.tile(np.repeat(deductions,n),len(pts)*10);q=sizes/entry
    valid=m<1/leverage
    mu=np.where(side==0,np.maximum(0.,(entry*(1-1/leverage-extra)-deduction/q)/np.where(valid,1-m,1.)),
                (entry*(1+1/leverage+extra)+deduction/q)/(1+m))
    sigma=np.maximum(np.maximum((high-low)/2.2,entry*.07/leverage),12.5)
    if options.conditional:
        sigma=np.maximum(entry*.00035,12.5) if profile else np.maximum(np.minimum((high-low)/3.5,entry*.0012)/(1+1/leverage),12.5)
    amount=new*unit*phi[side]*weights[side,tier]*volume/total*size_weights*collateral_weights
    allocated=np.zeros(2);flat=exposure.reshape(-1)
    for begin in range(0,len(entry),128):
        sl=slice(begin,begin+128);muv=mu[sl];sig=sigma[sl]
        start=np.searchsorted(buckets,muv-options.support*sig-12.5)
        end=np.searchsorted(buckets,muv+options.support*sig+12.5,side='right')
        count=np.where(valid[sl],end-start,0)
        if not count.sum():continue
        cohort=np.repeat(np.arange(len(count)),count)
        offsets=np.arange(int(count.sum()))-np.repeat(np.cumsum(count)-count,count)
        index=np.repeat(start,count)+offsets;local=buckets[index]
        kernel=np.maximum(0.,ndtr((local+12.5-muv[cohort])/sig[cohort])-ndtr((local-12.5-muv[cohort])/sig[cohort]))
        kernel*=np.where(side[sl][cohort]==0,local<mark,local>mark)
        values=amount[sl][cohort]*kernel
        target=(side[sl][cohort]*5+tier[sl][cohort])*len(buckets)+index
        np.add.at(flat,target,values)
        allocated+=np.bincount(side[sl][cohort],weights=values,minlength=2)
    return allocated


@dataclass
class State:
    buckets: np.ndarray
    exposure: np.ndarray  # [side, leverage, price] in BTC, not USD
    last_ts: int = 0
    last_mark: float = 0.
    alpha: float = .04
    prior_oi: float = 0.
    steps: int = 0
    scenario: str = 'baseline'

    @classmethod
    def empty(cls, mark, scenario='baseline'):
        # Fixed grid per replay epoch. Tail losses are measured, never rebinned silently.
        b = np.arange(np.floor(mark*.5/25)*25, np.ceil(mark*1.5/25)*25+25, 25.)
        if scenario!='baseline' and scenario not in SCENARIOS:raise ValueError('Unknown CEX scenario')
        return cls(b, np.zeros((2, 5, len(b))),scenario=scenario)

    def sweep(self, low, high):
        """First passage on the dispersed conditional threshold distribution.

        Margin/partial-liquidation uncertainty already broadens each cohort. A
        crossed slice is removed once. Refill is allowed only from later cohorts.
        """
        before = self.exposure.sum(axis=(1, 2))
        self.exposure[0, :, self.buckets >= low] = 0
        self.exposure[1, :, self.buckets <= high] = 0
        return np.maximum(0., before-self.exposure.sum(axis=(1, 2)))

    def step(self, ts, entry, high, low, close, mark_low, mark_high, mark_close,
             oi_native, previous_oi_native, volume_native, contract_btc=1.,
             buy_ratio=None, ratio_change=None, funding=None, profile=None,
             brackets=None, weights=None, fee=.0006):
        if ts <= self.last_ts:
            return {'replayed': True}
        vals = (entry, high, low, close, mark_low, mark_high, mark_close,
                oi_native, previous_oi_native, volume_native, contract_btc)
        if not all(np.isfinite(vals)) or min(entry, mark_close, contract_btc) <= 0:
            raise ValueError('nonfinite or nonpositive market input')
        if min(oi_native, previous_oi_native, volume_native) < 0:
            raise ValueError('negative OI/volume')
        dt = 300 if self.last_ts == 0 else max(0, ts-self.last_ts)
        # Decay occurs exactly once per elapsed five-minute interval.
        options=SCENARIOS.get(self.scenario)
        self.exposure *= (options.decay if options else .9996) ** (dt/300)
        swept = self.sweep(mark_low, mark_high)
        delta = oi_native-previous_oi_native
        valid = abs(delta) <= volume_native * 1.05 + 1e-8
        new = openings(delta, volume_native, self.alpha) if valid else 0.
        closed = max(0., new-delta) if valid else max(0., -delta)
        swept_contracts = float(max(swept))/contract_btc
        residual = max(0., closed-swept_contracts)
        survival = max(0., 1-residual/max(previous_oi_native, 1e-12))
        self.exposure *= survival
        phi = vulnerability(delta, volume_native, buy_ratio, ratio_change, funding)
        default_weights=options.weights if options else WEIGHTS
        w = np.array(weights if weights is not None else [default_weights, default_weights], dtype=float)
        if w.shape == (5,):
            w = np.tile(w, (2, 1))
        if w.shape != (2, 5) or np.any(w < 0) or not np.allclose(w.sum(axis=1), 1):
            raise ValueError('side-specific leverage weights must sum to one')
        # Actual observed price-volume profile if complete; fallback explicitly flagged.
        pts = profile if profile else [(entry, 1.)]
        total = sum(v for _, v in pts)
        if total <= 0:
            raise ValueError('empty volume profile')
        allocated = np.zeros(2)
        if options:
            allocated=scenario_projection(self.exposure,self.buckets,pts,total,options,new,contract_btc,phi,w,high,low,mark_close,brackets,fee,profile)
        else:
            for side in (0, 1):
                for k, lev in enumerate(LEVERAGES):
                    for p, v in pts:
                        # Size/collateral are scenario priors, NOT known account facts.
                        for size, size_weight in ((10000., .35), (50000., .45), (250000., .20)):
                            bracket = next((b for b in (brackets or []) if b['floor'] <= size < b['cap']), None)
                            mmr = bracket['mmr'] if bracket else .004
                            deduction = bracket.get('deduction', 0.) if bracket else 0.
                            for extra, cw in (options.margins if options else ((0., .65), (.01, .25), (.04, .10))):
                                mu = liquidation_price(p, lev, side, mmr, deduction, size, fee, extra)
                                if mu is None:
                                    continue
                                sigma = max((high-low)/2.2, p*.07/lev, 12.5)
                                if options and options.conditional:
                                    sigma=max(p*.00035,12.5) if profile else max(min((high-low)/3.5,p*.0012)/(1+1/lev),12.5)
                                support=options.support if options else 8.5
                                # At 4 sigma, omitted normal mass is at most 6.335e-5;
                                # bucket padding reduces it further. Never pile it at an edge.
                                start=int(np.searchsorted(self.buckets,mu-support*sigma-12.5))
                                end=int(np.searchsorted(self.buckets,mu+support*sigma+12.5,side='right'))
                                local=self.buckets[start:end]
                                kernel=integrated_kernel(local,mu,sigma)
                                kernel *= (local < mark_close if side == 0 else local > mark_close)
                                amount = new*contract_btc*phi[side]*w[side,k]*v/total*size_weight*cw
                                self.exposure[side,k,start:end] += amount*kernel
                                allocated[side] += amount*kernel.sum()
        # The vulnerable subset cannot exceed gross OI on either side.
        cap = oi_native*contract_btc
        for side in (0, 1):
            gross = self.exposure[side].sum()
            if gross > cap and gross > 0:
                self.exposure[side] *= cap/gross
        self.last_ts, self.last_mark, self.prior_oi = int(ts), float(mark_close), float(oi_native)
        self.steps += 1
        return {'valid_sampling': bool(valid), 'new_contracts_each_side': float(new),
                'closed_contracts': float(closed), 'swept_btc': swept.tolist(),
                'allocated_btc': allocated.tolist(), 'phi': phi.tolist(),
                'entry_method': 'observed_volume_profile' if profile else 'weighted_typical_price',
                'brackets': 'public_exchange' if brackets else 'unverified_mmr_prior',
                'weights': w.tolist(), 'model_version': scenario_version(self.scenario) if options else VERSION}

    def checkpoint(self):
        meta={'buckets': self.buckets.tolist(), 'last_ts': self.last_ts,
                           'last_mark': self.last_mark, 'alpha': self.alpha,
                           'prior_oi': self.prior_oi, 'steps': self.steps}
        if self.scenario!='baseline':meta['scenario']=self.scenario
        return json.dumps(meta)

    @classmethod
    def restore(cls, meta, data):
        m = json.loads(meta) if isinstance(meta, str) else meta
        b = np.array(m.pop('buckets'))
        return cls(b, decode_array(data, (2,5,len(b))), **m)


def calibration_proposal(alpha, observed, predicted, coverage_complete, evaluation_only=False):
    """No fitting to censored feeds or same-window evaluation. Production defaults frozen."""
    if not coverage_complete or evaluation_only or predicted <= 0:
        return float(alpha)
    change = .01*np.tanh((observed-predicted)/(predicted+10000.))
    return float(np.clip(alpha*(1+change), .01, .15))
