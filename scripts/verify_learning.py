"""Debian-only integration. Actual input snapshots plus explicit disposable fixtures.

Synthetic control/clock fixtures test transitions in the isolated verification DB;
they are never copied into live data, fitted live, or presented as market evidence.
"""
import asyncio
from datetime import timedelta
import json
from unittest.mock import patch
import asyncpg
import numpy as np
from app import db,learning
from app.config import DATABASE_URL
from app.learning_math import PROTOCOL,FEATURES,fit_ridge


async def main():
    p=await db.pool(1);admin=await asyncpg.connect(DATABASE_URL)
    name='btc_heatmap_learning_verify'
    assert not await admin.fetchval('SELECT 1 FROM pg_database WHERE datname=$1',name),'Refusing to reuse an existing database'
    await admin.execute('CREATE DATABASE '+name)
    async def init(c):await c.set_type_codec('jsonb',encoder=json.dumps,decoder=json.loads,schema='pg_catalog')
    q=await asyncpg.create_pool(DATABASE_URL.rsplit('/',1)[0]+'/'+name,min_size=1,max_size=2,init=init)
    report={'tests':[],'fixture_scope':'real public snapshots; synthetic transition fixtures exist only in disposable test DB'}
    try:
        await db.migrate(q);await db.migrate(q)
        assert await q.fetchval('SELECT count(*) FROM schema_migrations')==3
        report['tests'].append('fresh and repeated additive migrations')
        snap=await p.fetchrow("SELECT * FROM heatmap_snapshots WHERE exchange='bybit' ORDER BY ts DESC LIMIT 1")
        mark=await p.fetchrow("SELECT * FROM live_marks WHERE exchange='bybit'")
        assert snap and mark
        await q.execute('INSERT INTO heatmap_snapshots VALUES($1,$2,$3,$4,$5,$6,$7,$8)',*tuple(snap))
        await q.execute('INSERT INTO live_marks VALUES($1,$2,$3)',*tuple(mark))
        candles=await p.fetch("SELECT * FROM candles WHERE exchange='bybit' AND ts>now()-interval '70 minutes' AND (kind='live_mark' OR (kind='mark' AND interval_s=60))")
        await q.executemany('INSERT INTO candles VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12)',[tuple(r) for r in candles])
        await learning.initialize(q);activated=await q.fetchval('SELECT activated_at FROM learning_control')
        await learning.initialize(q)
        assert await q.fetchval('SELECT activated_at FROM learning_control')==activated
        for attempt in range(3):
            try:
                if await learning.issue(q)==2:break
            except learning.ForecastDeadline:pass
            await asyncio.sleep(3)
        else:raise AssertionError('Could not issue a complete future forecast pair from fresh real inputs')
        assert await learning.issue(q)==0
        future=await q.fetch('SELECT * FROM learning_forecasts ORDER BY side')
        assert len(future)==2
        for r in future:
            assert r['issued_at']<r['target_start']<r['target_end'] and r['source_ts']<=r['issued_at']
            assert len(r['features'])==len(FEATURES) and r['champion_id'] is None
        try:
            await q.execute('UPDATE learning_forecasts SET issued_at=target_start WHERE id=$1',future[0]['id'])
            raise AssertionError('DB permitted issuance after the target begins')
        except asyncpg.CheckViolationError:pass
        assert await learning.settle(q)==0
        await learning.review(q)
        assert await q.fetchval('SELECT count(*) FROM learning_models')==0
        report['tests'].append('actual-input prospective issuance, cutoff constraint, restart initialization and idempotence')
        observed=await p.fetchrow("SELECT * FROM observed_liquidations WHERE exchange='bybit' AND ts < to_timestamp(floor(extract(epoch from (clock_timestamp()-interval '120 seconds'))/900)*900) ORDER BY ts DESC LIMIT 1")
        assert observed,'Need an actual Bybit event in a fully ended 15-minute window plus two minutes; leave collection running and retry'
        await q.execute('INSERT INTO observed_liquidations VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9)',*tuple(observed))
        now=await q.fetchval('SELECT clock_timestamp()')
        begin=db.utc(int(observed['ts'].timestamp()//900)*900);end=begin+timedelta(seconds=900)
        assert end<now-timedelta(seconds=120),'Wait for an actual matured report window'
        await q.execute('INSERT INTO learning_event_receipts VALUES($1,$2,$3,$4)',observed['ts'],'bybit',observed['event_id'],now+timedelta(hours=1))
        fixture=await q.fetchval('''INSERT INTO learning_forecasts(protocol,exchange,side,issued_at,target_start,target_end,source_ts,features,provenance,
            baseline_typical_usd,baseline_persistence_usd,champion_usd) VALUES($1,'bybit',$2,$3,$4,$5,$6,$7,$8,0,0,0) RETURNING id''',
            PROTOCOL,observed['side'],begin-timedelta(seconds=10),begin,end,begin-timedelta(minutes=5),[1.]*len(FEATURES),{'integration_fixture':True})
        assert await learning.settle(q)==1
        frozen=await q.fetchrow('SELECT * FROM learning_forecasts WHERE id=$1',fixture)
        assert frozen['outcome_usd']==0 and not frozen['eligible'] and 'excluded' in frozen['reason']
        await q.execute('UPDATE learning_event_receipts SET received_at=$1',observed['ts'])
        assert await learning.settle(q)==0
        assert await q.fetchval('SELECT outcome_usd FROM learning_forecasts WHERE id=$1',fixture)==0
        report['tests'].append('actual event with late receipt excluded; missing coverage never becomes a training zero; settled score immutable')
        # Verify registry transactions independently of the statistical tests.
        artifact=fit_ridge(np.ones((40,len(FEATURES))),np.arange(40)*1000)
        for i in (1,2):
            identity=f'integration-transition-{i}'
            await q.execute("INSERT INTO learning_models(id,protocol,exchange,side,created_at,trained_through,decision_at,state,artifact) VALUES($1,$2,'bybit','long',$3,$4,$5,'candidate',$6)",
                identity,PROTOCOL,now-timedelta(days=15),now-timedelta(days=16),now-timedelta(days=1),artifact)
            model=await q.fetchrow('SELECT * FROM learning_models WHERE id=$1',identity)
            with patch('app.learning.promotion_report',return_value={'promote':True,'synthetic_transition_fixture':True}):
                await learning.decide_candidate(q,model,now)
            assert await q.fetchval("SELECT count(*) FROM learning_models WHERE state='champion'")==1
        assert await q.fetchval("SELECT state FROM learning_models WHERE id='integration-transition-1'")=='retired'
        # No duplicate decisions when the same candidate is revisited after a restart.
        before=await q.fetchval('SELECT count(*) FROM learning_decisions')
        await learning.decide_candidate(q,model,now)
        assert await q.fetchval('SELECT count(*) FROM learning_decisions')==before
        report['tests'].append('atomic candidate promotion, single champion, previous-model retirement and replay-safe decision')
        # Explicit synthetic forecast fixtures exercise the degradation rollback;
        # this is software verification, not an empirical accuracy result.
        await q.execute('UPDATE learning_models SET decided_at=$2 WHERE id=$1',model['id'],now-timedelta(days=8))
        rows=[]
        first=db.utc(int(now.timestamp()//60)*60)-timedelta(days=7)
        for i in range(500):
            start=first+timedelta(minutes=20*i)
            rows.append((PROTOCOL,start-timedelta(seconds=10),start,start+timedelta(minutes=15),start-timedelta(minutes=5),
                         [1.]*len(FEATURES),{'synthetic_transition_fixture':True},model['id'],now))
        await q.executemany('''INSERT INTO learning_forecasts(protocol,exchange,side,issued_at,target_start,target_end,source_ts,features,provenance,
            baseline_typical_usd,baseline_persistence_usd,champion_id,champion_usd,outcome_usd,labelled_at,eligible)
            VALUES($1,'bybit','long',$2,$3,$4,$5,$6,$7,10000,11000,$8,100000,10000,$9,true)''',rows)
        live_model=await q.fetchrow('SELECT * FROM learning_models WHERE id=$1',model['id'])
        await learning.rollback_if_degraded(q,live_model,now)
        assert await q.fetchval('SELECT state FROM learning_models WHERE id=$1',model['id'])=='rolled_back'
        assert await q.fetchval("SELECT count(*) FROM learning_models WHERE state='champion'")==0
        report['tests'].append('degraded promoted forecast rolls back to baseline (disposable synthetic fixtures)')
        older=[]
        for i in range(100):
            start=first-timedelta(days=2)+timedelta(minutes=20*i)
            older.append((PROTOCOL,start-timedelta(seconds=10),start,start+timedelta(minutes=15),start-timedelta(minutes=5),
                          [1.]*len(FEATURES),{'synthetic_training_fixture':True},now))
        await q.executemany('''INSERT INTO learning_forecasts(protocol,exchange,side,issued_at,target_start,target_end,source_ts,features,provenance,
            baseline_typical_usd,baseline_persistence_usd,champion_usd,outcome_usd,labelled_at,eligible)
            VALUES($1,'bybit','long',$2,$3,$4,$5,$6,$7,10000,11000,10000,10000,$8,true)''',older)
        await learning.train_candidate(q,'long',now)
        candidate=await q.fetchrow("SELECT * FROM learning_models WHERE state='candidate'")
        assert candidate and candidate['artifact']['training_n']==600 and candidate['trained_through']<=candidate['created_at']
        assert await q.fetchval("SELECT count(*) FROM learning_decisions WHERE model_id=$1 AND action='candidate_trained'",candidate['id'])==1
        await learning.train_candidate(q,'long',now)
        assert await q.fetchval("SELECT count(*) FROM learning_models WHERE state='candidate'")==1
        # A failed audit insert must not leave an unaudited candidate behind.
        await q.execute("UPDATE learning_models SET state='rejected' WHERE state='candidate'")
        await q.execute("""CREATE FUNCTION fail_learning_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN IF NEW.action='candidate_trained' THEN RAISE EXCEPTION 'intentional disposable audit failure'; END IF; RETURN NEW; END $$;
            CREATE TRIGGER fail_learning_audit BEFORE INSERT ON learning_decisions FOR EACH ROW EXECUTE FUNCTION fail_learning_audit();""")
        model_count=await q.fetchval('SELECT count(*) FROM learning_models')
        try:
            await learning.train_candidate(q,'long',now+timedelta(seconds=1))
            raise AssertionError('Audit failure was not propagated')
        except asyncpg.PostgresError:pass
        assert await q.fetchval('SELECT count(*) FROM learning_models')==model_count
        assert await q.fetchval("SELECT count(*) FROM learning_models WHERE state='candidate'")==0
        report['tests'].append('eligible numerical fixtures train once; candidate and training audit commit atomically')
        if await p.fetchval("SELECT to_regclass('learning_models')"):
            assert await p.fetchval("SELECT count(*) FROM learning_models WHERE id LIKE 'integration-%'")==0
        assert await p.fetchval('SELECT md5(matrix) FROM heatmap_snapshots WHERE exchange=$1 AND ts=$2',snap['exchange'],snap['ts'])==await q.fetchval('SELECT md5(matrix) FROM heatmap_snapshots WHERE exchange=$1 AND ts=$2',snap['exchange'],snap['ts'])
        report.update(passed=True,real_source_snapshot=snap['ts'].isoformat(),forecast_rows=len(future),live_heatmap_unchanged=True)
        print(json.dumps(report,indent=2),flush=True)
    finally:
        await q.close();await admin.execute('DROP DATABASE '+name);await admin.close();await p.close()


if __name__=='__main__':asyncio.run(main())
