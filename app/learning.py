"""Prospective, resource-bounded learning for recorded Bybit liquidation flow.

This layer does not rewrite heatmaps, infer private leverage, or fit sampled
Binance/OKX totals. Models earn a reversible forecast promotion on future data.
"""
import asyncio
from datetime import timedelta
import hashlib
import json
import logging
import math
import time
import numpy as np
from app import db
from app.config import VERSION
from app.model import decode_array
from app.learning_math import (PROTOCOL,HORIZON_S,SCALE,FEATURES,TRAIN_MIN,TRAIN_DAYS,
    HOLDOUT_DAYS,HOLDOUT_MIN,POSITIVE_MIN,transformed,fit_ridge,predict,metrics,
    paired_day_bounds,promotion_report,audited_window)

log=logging.getLogger(__name__)


class ForecastDeadline(Exception):
    """Retry later when a complete pair cannot be stored before the window starts."""


async def initialize(p):
    await p.execute('INSERT INTO learning_control(protocol) VALUES($1) ON CONFLICT DO NOTHING',PROTOCOL)


async def source_features(p,issued):
    snap=await p.fetchrow("SELECT * FROM heatmap_snapshots WHERE exchange='bybit' AND model_version=$2 AND ts<=$1 ORDER BY ts DESC LIMIT 1",issued,VERSION)
    mark=await p.fetchrow("SELECT * FROM live_marks WHERE exchange='bybit' AND ts<=$1",issued)
    if not snap or not mark or (issued-snap['ts']).total_seconds()>900 or (issued-mark['ts']).total_seconds()>30:
        return None
    # Read only observations available at issuance. Persisted forecasts are never rebuilt.
    path=await p.fetchrow("SELECT min(l) lo,max(h) hi FROM candles WHERE exchange='bybit' AND kind='live_mark' AND ts>=$1 AND ts<=$2",snap['ts'],issued)
    exposure=decode_array(snap['matrix'],(2,5,snap['grid_n'])).sum(axis=1)
    levels=snap['grid_start']+np.arange(snap['grid_n'])*25
    if path['lo'] is not None:
        exposure[0,levels>=path['lo']]=0;exposure[1,levels<=path['hi']]=0
    price=mark['price'];usd=exposure*price
    candles=await p.fetch("SELECT ts,c FROM candles WHERE exchange='bybit' AND kind='mark' AND interval_s=60 AND complete AND ts+interval '1 minute'<=$1 AND ts>=$1::timestamptz-interval '65 minutes' ORDER BY ts",issued)
    closes=np.array([c['c'] for c in candles],dtype=float)
    volatility=float(np.sqrt(np.mean(np.diff(np.log(closes))**2))*np.sqrt(60)) if len(closes)>2 else 0.
    old=[c['c'] for c in candles if c['ts']<=issued-timedelta(minutes=15)]
    change=math.log(price/old[-1]) if old else 0.
    past=await p.fetch("""SELECT l.side,sum(l.usd) usd FROM observed_liquidations l
        JOIN learning_event_receipts r USING(ts,exchange,event_id)
        WHERE l.exchange='bybit' AND l.ts>=$1::timestamptz-interval '1 hour' AND l.ts<$1
        AND r.received_at<=$1 GROUP BY l.side""",issued)
    past={r['side']:r['usd'] for r in past}
    near=[]
    for side in (0,1):
        distance=(price-levels)/price if side==0 else (levels-price)/price
        near.append([float(usd[side,(distance>0)&(distance<=width)].sum()) for width in (.005,.02)])
    feature={}
    for side,name in enumerate(('long','short')):
        persistence=float(past.get(name,0))/4
        skew=(near[side][1]-near[1-side][1])/max(1.,near[side][1]+near[1-side][1])
        feature[name]=[1.,math.log1p(near[side][0]/SCALE),math.log1p(near[side][1]/SCALE),
            min(10.,volatility/.01),min(10.,max(0.,change*(-1 if side==0 else 1))/.01),
            math.log1p(persistence/SCALE),skew,float(snap['audit'].get('entry_method')=='observed_volume_profile')]
    provenance={'snapshot_ts':snap['ts'].isoformat(),'source_model_version':snap['model_version'],'mark_ts':mark['ts'].isoformat(),'mark':price,
        'entry_method':snap['audit'].get('entry_method'),'target':'recorded Bybit liquidation notional, not private liquidation exposure',
        'price_semantics':'reported bankruptcy-price notional','source_feature_names':list(FEATURES),
        'past_mark_candles':len(candles),'past_reported_hour_is_transport_limited':True}
    return feature,past,snap['ts'],provenance


async def issue(p):
    issued=await p.fetchval('SELECT clock_timestamp()')
    last=await p.fetchval("SELECT max(target_end) FROM learning_forecasts WHERE protocol=$1 AND exchange='bybit'",PROTOCOL)
    if last and issued<last-timedelta(seconds=30):return 0
    # An immutable minute boundary strictly in the future, never an already-started label.
    start=db.utc((int(issued.timestamp())//60+1)*60)
    if last and start<last:return 0
    source=await source_features(p,issued)
    if source is None:return 0
    features,past,source_ts,provenance=source
    end=start+timedelta(seconds=HORIZON_S)
    active=await p.fetch("SELECT * FROM learning_models WHERE protocol=$1 AND state IN ('candidate','champion')",PROTOCOL)
    count=0
    async with p.acquire() as c:
        async with c.transaction():
            await c.execute('SELECT pg_advisory_xact_lock(732598343)')
            latest=await c.fetchval("SELECT max(target_end) FROM learning_forecasts WHERE protocol=$1 AND exchange='bybit'",PROTOCOL)
            if latest and start<latest:return 0
            for side in ('long','short'):
                history=await c.fetch("SELECT outcome_usd FROM learning_forecasts WHERE protocol=$1 AND exchange='bybit' AND side=$2 AND eligible AND labelled_at<=$3 AND target_end>$3::timestamptz-interval '28 days' ORDER BY target_end DESC LIMIT 2688",PROTOCOL,side,issued)
                typical=float(SCALE*np.expm1(transformed([r['outcome_usd'] for r in history]).mean())) if history else 0.
                persistence=float(past.get(side,0))/4
                champion=next((r for r in active if r['side']==side and r['state']=='champion'),None)
                candidate=next((r for r in active if r['side']==side and r['state']=='candidate' and start<r['decision_at']),None)
                cp=predict(champion['artifact'],features[side]) if champion else typical
                sp=predict(candidate['artifact'],features[side]) if candidate else None
                detail=dict(provenance,baseline_history_n=len(history),lead_s=(start-issued).total_seconds(),heatmap_calibration='unchanged, uncalibrated')
                result=await c.fetchval('''INSERT INTO learning_forecasts(protocol,exchange,side,issued_at,target_start,target_end,source_ts,features,provenance,
                    baseline_typical_usd,baseline_persistence_usd,champion_id,champion_usd,candidate_id,candidate_usd)
                    SELECT $1,'bybit',$2,GREATEST($3::timestamptz,clock_timestamp()),$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14
                    WHERE clock_timestamp()<$4::timestamptz-interval '2 seconds'
                    ON CONFLICT(protocol,exchange,side,target_start) DO NOTHING RETURNING id''',
                    PROTOCOL,side,issued,start,end,source_ts,features[side],detail,typical,persistence,
                    champion['id'] if champion else None,cp,candidate['id'] if candidate else None,sp)
                count+=int(result is not None)
            if count!=2:
                raise ForecastDeadline('The future-window deadline passed; both sides rolled back')
    if count:await db.event(p,'learning',{'status':'forecast_recorded','target_end':end.isoformat(),'scope':'Bybit reported flow'})
    return count


async def settle(p):
    now=await p.fetchval('SELECT clock_timestamp()')
    rows=await p.fetch("SELECT * FROM learning_forecasts WHERE protocol=$1 AND labelled_at IS NULL AND target_end<=$2::timestamptz-interval '120 seconds' ORDER BY target_end LIMIT 16",PROTOCOL,now)
    for row in rows:
        transport=await p.fetch('SELECT * FROM learning_feed_windows WHERE exchange=$1 AND ts>=$2 AND ts<$3',row['exchange'],row['target_start'],row['target_end'])
        good,audit=audited_window(transport,row['target_start'].timestamp(),row['target_end'].timestamp())
        # Receipts arriving after this irreversible label decision cannot change its score.
        total=await p.fetchval('''SELECT coalesce(sum(l.usd),0) FROM observed_liquidations l
            JOIN learning_event_receipts r USING(ts,exchange,event_id)
            WHERE l.exchange=$1 AND l.side=$2 AND l.ts>=$3 AND l.ts<$4 AND r.received_at<=$5''',
            row['exchange'],row['side'],row['target_start'],row['target_end'],now)
        reason='audited recorded stream; exchange-side omissions remain unknown' if good else 'excluded: missing, lagged or unacknowledged transport minutes'
        await p.execute('''UPDATE learning_forecasts SET outcome_usd=$2,labelled_at=$3,eligible=$4,reason=$5,
            provenance=provenance||jsonb_build_object('label_audit',$6::jsonb)
            WHERE id=$1 AND labelled_at IS NULL''',row['id'],float(total),now,good,reason,audit)
    return len(rows)


async def train_candidate(p,side,now):
    if await p.fetchval("SELECT EXISTS(SELECT 1 FROM learning_models WHERE protocol=$1 AND side=$2 AND state='candidate')",PROTOCOL,side):return
    rows=await p.fetch("""SELECT features,outcome_usd,target_start,target_end FROM learning_forecasts
        WHERE protocol=$1 AND exchange='bybit' AND side=$2 AND eligible AND labelled_at<=$3 AND target_end<=$3
        AND target_end>$3::timestamptz-interval '28 days' ORDER BY target_end DESC LIMIT 2688""",PROTOCOL,side,now)
    if len(rows)<TRAIN_MIN or sum(r['outcome_usd']>0 for r in rows)<POSITIVE_MIN:return
    first=min(r['target_start'] for r in rows);last=max(r['target_end'] for r in rows)
    if (last-first).total_seconds()<TRAIN_DAYS*86400:return
    artifact=await asyncio.to_thread(fit_ridge,[r['features'] for r in rows],[r['outcome_usd'] for r in rows])
    artifact.update(training_first=first.isoformat(),training_last=last.isoformat(),positive_windows=sum(r['outcome_usd']>0 for r in rows))
    identity=hashlib.sha256(json.dumps([PROTOCOL,side,now.isoformat(),artifact],sort_keys=True).encode()).hexdigest()[:24]
    async with p.acquire() as c:
        async with c.transaction():
            inserted=await c.fetchval("INSERT INTO learning_models(id,protocol,exchange,side,created_at,trained_through,decision_at,state,artifact) VALUES($1,$2,'bybit',$3,$4,$5,$6,'candidate',$7) ON CONFLICT DO NOTHING RETURNING id",identity,PROTOCOL,side,now,last,now+timedelta(days=HOLDOUT_DAYS),artifact)
            if inserted:
                await c.execute("INSERT INTO learning_decisions(model_id,action,report) VALUES($1,'candidate_trained',$2)",identity,{'training_n':len(rows),'fixed_future_holdout_days':HOLDOUT_DAYS,'heatmap_parameters_changed':False})


async def decide_candidate(p,model,now):
    rows=await p.fetch("SELECT * FROM learning_forecasts WHERE candidate_id=$1 AND target_start>=$2 AND target_start<$3 AND target_end<=$3 ORDER BY target_start",model['id'],model['created_at'],model['decision_at'])
    if any(r['labelled_at'] is None for r in rows):return
    report=await asyncio.to_thread(promotion_report,rows,model['id'])
    # A fixed decision date prevents repeated peeking at a growing holdout.
    async with p.acquire() as c:
        async with c.transaction():
            await c.execute('SELECT pg_advisory_xact_lock(732598344)')
            if await c.fetchval('SELECT state FROM learning_models WHERE id=$1 FOR UPDATE',model['id'])!='candidate':return
            if report['promote']:
                await c.execute("UPDATE learning_models SET state='retired',decided_at=$3 WHERE protocol=$1 AND side=$2 AND state='champion'",PROTOCOL,model['side'],now)
            state='champion' if report['promote'] else 'rejected'
            await c.execute('UPDATE learning_models SET state=$2,decided_at=$3,report=$4 WHERE id=$1',model['id'],state,now,report)
            await c.execute('INSERT INTO learning_decisions(model_id,action,report) VALUES($1,$2,$3)',model['id'],state,report)
    await db.event(p,'learning',{'status':state,'side':model['side'],'model_id':model['id']})


async def rollback_if_degraded(p,model,now):
    promoted=model['decided_at']
    if not promoted or (now-promoted).total_seconds()<7*86400:return
    rows=await p.fetch("SELECT * FROM learning_forecasts WHERE champion_id=$1 AND eligible AND labelled_at<=$2 AND target_start>=$2::timestamptz-interval '7 days' ORDER BY target_start",model['id'],now)
    if len(rows)<400:return
    days=[r['target_start'].date().isoformat() for r in rows]
    y=[r['outcome_usd'] for r in rows];live=[r['champion_usd'] for r in rows]
    refs={k:[r[k] for r in rows] for k in ('baseline_typical_usd','baseline_persistence_usd')}
    ref=min(refs,key=lambda k:metrics(y,refs[k])['log_mse'])
    loss=metrics(y,live)['log_mse'];base=metrics(y,refs[ref])['log_mse']
    bounds=paired_day_bounds(y,live,refs[ref],days,model['id']+'rollback')
    if loss>base*1.25 and bounds['upper'] is not None and bounds['upper']<0:
        report={'reason':'rolling seven-day reported-flow loss exceeds best recorded baseline by >25%',
                'live':metrics(y,live),'reference':metrics(y,refs[ref]),'bounds':bounds}
        async with p.acquire() as c:
            async with c.transaction():
                await c.execute("UPDATE learning_models SET state='rolled_back',report=report||$2::jsonb WHERE id=$1 AND state='champion'",model['id'],{'rollback':report})
                await c.execute("INSERT INTO learning_decisions(model_id,action,report) VALUES($1,'rolled_back',$2)",model['id'],report)


async def review(p):
    now=await p.fetchval('SELECT clock_timestamp()')
    previous=await p.fetchval('SELECT last_review_at FROM learning_control WHERE protocol=$1',PROTOCOL)
    if previous and (now-previous).total_seconds()<86400:return
    for model in await p.fetch("SELECT * FROM learning_models WHERE protocol=$1 AND state IN ('candidate','champion')",PROTOCOL):
        if model['state']=='candidate' and now>=model['decision_at']+timedelta(minutes=20):await decide_candidate(p,model,now)
        if model['state']=='champion':await rollback_if_degraded(p,model,now)
    for side in ('long','short'):await train_candidate(p,side,now)
    await p.execute('UPDATE learning_control SET last_review_at=$2 WHERE protocol=$1',PROTOCOL,now)


async def summary(p):
    control=await p.fetchrow('SELECT * FROM learning_control WHERE protocol=$1',PROTOCOL)
    worker=await p.fetchrow("SELECT status,updated_at FROM feed_coverage WHERE exchange='system' AND feed='learning'")
    counts=await p.fetch("""SELECT side,count(*) FILTER(WHERE labelled_at IS NOT NULL) scored,
        count(*) FILTER(WHERE eligible) eligible,count(*) FILTER(WHERE labelled_at IS NOT NULL AND NOT eligible) excluded,
        count(*) FILTER(WHERE eligible AND outcome_usd>0) positive,
        min(target_start) FILTER(WHERE eligible) first_eligible,max(target_end) FILTER(WHERE eligible) last_eligible
        FROM learning_forecasts WHERE protocol=$1 AND target_end>now()-interval '28 days' GROUP BY side""",PROTOCOL)
    models=await p.fetch("SELECT id,side,state,created_at,trained_through,decision_at,decided_at,artifact->'training_n' training_n,report FROM learning_models WHERE protocol=$1 ORDER BY (state IN ('candidate','champion')) DESC,created_at DESC LIMIT 8",PROTOCOL)
    forecasts=await p.fetch("SELECT DISTINCT ON(side) side,issued_at,target_start,target_end,source_ts,provenance,baseline_typical_usd,baseline_persistence_usd,champion_id,champion_usd,candidate_id,candidate_usd FROM learning_forecasts WHERE protocol=$1 ORDER BY side,target_start DESC",PROTOCOL)
    outcomes=await p.fetch("SELECT side,target_start,target_end,champion_id,champion_usd,candidate_id,candidate_usd,outcome_usd,eligible,reason,provenance->'label_audit' audit FROM learning_forecasts WHERE protocol=$1 AND labelled_at IS NOT NULL ORDER BY target_end DESC LIMIT 12",PROTOCOL)
    transport=await p.fetch("SELECT exchange,max(last_seen) last_seen,count(*) FILTER(WHERE liquidation_ack) acknowledged_minutes FROM learning_feed_windows WHERE ts>now()-interval '1 hour' GROUP BY exchange")
    champion=any(r['state']=='champion' for r in models)
    return {'protocol':PROTOCOL,'status':'learned reported-flow forecast active' if champion else 'collecting evidence; baseline forecasts active',
        'activated_at':control['activated_at'] if control else None,'worker':dict(worker) if worker else None,'horizon_minutes':15,'training_exchange':'bybit',
        'heatmap_calibration':'unchanged: uncalibrated structural priors',
        'target':'USD notional of recorded Bybit long/short liquidations over the next 15 minutes; not price direction or private exposure',
        'requirements':{'training_windows_per_side':TRAIN_MIN,'minimum_training_span_days':TRAIN_DAYS,'positive_windows_per_side':POSITIVE_MIN,
                        'fixed_future_holdout_days':HOLDOUT_DAYS,'audited_holdout_windows':HOLDOUT_MIN,
                        'minimum_relative_log_loss_improvement':.10,'day_block_lower_bound_above_zero':True},
        'progress':[dict(r) for r in counts],'models':[dict(r) for r in models],'forecasts':[dict(r) for r in forecasts],
        'recent_outcomes':[dict(r) for r in outcomes],
        'transport':[dict(r) for r in transport],
        'limits':['Transport auditing cannot independently prove exchange-side completeness.',
                  'No guaranteed improvement or calibrated confidence interval.',
                  'No fitting of sampled Binance/OKX totals or hidden leverage distributions.',
                  'Promotion changes only reported-flow forecasts; heatmap matrices and prior alpha are untouched.']}


async def loop(p):
    await initialize(p)
    while True:
        try:
            await settle(p)
            await review(p)
            await issue(p)
            await db.coverage(p,'system','learning','live',{'protocol':PROTOCOL,'target':'Bybit reported flow; heatmap priors unchanged'})
        except ForecastDeadline:
            log.info('forecast deadline passed; retrying with a future window')
        except Exception:
            log.exception('reported-flow learning')
            await db.coverage(p,'system','learning','error',{'protocol':PROTOCOL,'training_paused':True,'heatmap_unaffected':True})
        await asyncio.sleep(15)
