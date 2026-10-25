"""Small deterministic learners for *reported flow*, never hidden-position labels.

One fixed ridge specification, training-only standardization, precommitted
forecasts, fixed future holdout, and day-block paired uncertainty. No paid SDK.
"""
import hashlib
import numpy as np

PROTOCOL = 'reported-flow-v1'
HORIZON_S = 900
SCALE = 10000.
FEATURES = ('intercept','near_half_pct','near_two_pct','past_hour_volatility',
            'past_directional_move','past_reported_flow','side_concentration','trade_profile_observed')
TRAIN_MIN = 600
TRAIN_DAYS = 7
HOLDOUT_DAYS = 14
HOLDOUT_MIN = 1000
POSITIVE_MIN = 30


def transformed(values):
    a=np.asarray(values,dtype=float)
    if not np.isfinite(a).all() or np.any(a<0):
        raise ValueError('nonfinite or negative reported notional')
    return np.log1p(a/SCALE)


def fit_ridge(features, outcomes):
    x=np.asarray(features,dtype=float);y=transformed(outcomes)
    if not len(y) or x.ndim!=2 or x.shape!=(len(y),len(FEATURES)) or not np.isfinite(x).all():
        raise ValueError('invalid feature matrix')
    center=x.mean(axis=0);spread=x.std(axis=0)
    center[0]=0.;spread[0]=1.;spread[spread<1e-8]=1.
    z=(x-center)/spread
    penalty=np.eye(x.shape[1])*10.;penalty[0,0]=0.
    coef=np.linalg.solve(z.T@z+penalty,z.T@y)
    return {'features':list(FEATURES),'center':center.tolist(),'spread':spread.tolist(),
            'coefficients':coef.tolist(),'ridge':10.,'target':'log1p(Bybit reported USD / 10000)',
            'training_n':len(y)}


def predict(artifact, features):
    if artifact['features']!=list(FEATURES):
        raise ValueError('feature version mismatch')
    x=np.asarray(features,dtype=float);c=np.asarray(artifact['center']);s=np.asarray(artifact['spread']);b=np.asarray(artifact['coefficients'])
    if any(a.shape!=(len(FEATURES),) for a in (x,c,s,b)) or not all(np.isfinite(a).all() for a in (x,c,s,b)) or np.any(s<=0):
        raise ValueError('invalid learner artifact/input')
    value=float(((x-c)/s)@b)
    return float(SCALE*np.expm1(np.clip(value,0,np.log1p(1e10/SCALE))))


def metrics(actual, predicted):
    y=transformed(actual);p=transformed(predicted)
    if not len(y) or y.shape!=p.shape:
        raise ValueError('empty/mismatched evaluation')
    return {'log_mse':float(np.mean((y-p)**2)),
            'usd_mae':float(np.mean(np.abs(np.asarray(actual)-np.asarray(predicted))))}


def relative_gain(candidate_loss,reference_loss):
    if reference_loss<=1e-12 and candidate_loss<=1e-12:return 0.
    return float(1-candidate_loss/max(reference_loss,1e-12))


def paired_day_bounds(actual, candidate, reference, days, seed):
    """Paired day blocks retain intraday dependence; exploratory, not a guarantee."""
    y=transformed(actual);c=(y-transformed(candidate))**2;r=(y-transformed(reference))**2
    unique=sorted(set(days));blocks=[np.where(np.asarray(days)==d)[0] for d in unique]
    if len(blocks)<7:
        return {'lower':None,'upper':None,'days':len(blocks)}
    rng=np.random.default_rng(int(hashlib.sha256(seed.encode()).hexdigest()[:8],16))
    improvements=[]
    for _ in range(400):
        idx=np.concatenate([blocks[i] for i in rng.integers(0,len(blocks),len(blocks))])
        base=float(r[idx].mean())
        improvements.append(relative_gain(float(c[idx].mean()),base))
    return {'lower':float(np.quantile(improvements,.05)),
            'upper':float(np.quantile(improvements,.95)),'days':len(blocks)}


def promotion_report(rows, model_id):
    """Called only once after a candidate's fixed future holdout has elapsed."""
    good=[r for r in rows if r['eligible'] and r['candidate_usd'] is not None]
    reasons=[]
    days=[r['target_start'].date().isoformat() for r in good]
    positive=sum(r['outcome_usd']>0 for r in good)
    if len(good)<HOLDOUT_MIN:reasons.append(f'need {HOLDOUT_MIN} audited holdout windows')
    if len(set(days))<12:reasons.append('need 12 distinct UTC holdout days')
    if len(good)/max(1,len(rows))<.8:reasons.append('less than 80% of issued holdout windows are auditable')
    if positive<POSITIVE_MIN:reasons.append(f'need {POSITIVE_MIN} positive holdout windows')
    report={'eligible_windows':len(good),'issued_windows':len(rows),'positive_windows':positive,
            'promote':False,'reasons':reasons,'scope':'Bybit reported 15m flow only; hidden exposure remains uncalibrated'}
    if not good:return report
    actual=[r['outcome_usd'] for r in good];candidate=[r['candidate_usd'] for r in good]
    baselines={k:[r[k] for r in good] for k in ('baseline_typical_usd','baseline_persistence_usd','champion_usd')}
    scores={k:metrics(actual,v) for k,v in baselines.items()}
    best=min(scores,key=lambda k:scores[k]['log_mse']);cm=metrics(actual,candidate)
    improvement=relative_gain(cm['log_mse'],scores[best]['log_mse'])
    bounds=paired_day_bounds(actual,candidate,baselines[best],days,model_id)
    if improvement<.10:reasons.append('less than 10% log-loss improvement over the best recorded reference')
    if bounds['lower'] is None or bounds['lower']<=0:reasons.append('day-block lower improvement bound is not above zero')
    if cm['usd_mae']>min(v['usd_mae'] for v in scores.values())*1.05:reasons.append('USD absolute error degrades by more than 5% against the best reference')
    report.update(candidate=cm,baselines=scores,best_reference=best,relative_improvement=improvement,
                  day_block_improvement=bounds,promote=not reasons)
    return report


def minute_eligible(row, start):
    return (row['liquidation_ack'] and row['first_seen'].timestamp()<=start+10
            and row['last_seen'].timestamp()>=start+50 and row['max_gap_s']<=15
            and row['max_delay_s']<=10 and row['messages']>=2)


def audited_window(rows, start, end):
    """Every minute needs one acknowledged, low-lag uninterrupted session."""
    available={int(r['ts'].timestamp()) for r in rows if minute_eligible(r,int(r['ts'].timestamp()))}
    expected=set(range(int(start),int(end),60))
    missing=sorted(expected-available)
    return not missing,{'expected_minutes':len(expected),'audited_minutes':len(expected&available),
                        'missing_minutes':missing[:30],
                        'scope':'transport audit; exchange-side completeness is not independently proven'}
