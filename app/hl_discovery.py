"""Bounded, durable account discovery. Signals never manufacture positions."""
import asyncio
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import math
import time

from app.hl_math import ADDRESS, number

VERSION = 'expanded-discovery-v2'
REGISTRY_LIMIT = 100_000
PENDING_LIMIT = 4000
TRADE_LIMIT = 200_000
WEIGHT_LIMIT = 600  # Half the documented public IP budget; no extra concurrency.


def address(value):
    value = str(value).lower()
    return value if ADDRESS.fullmatch(value) and value != '0x'+'0'*40 else None


def leaderboard_candidate(row):
    a = address(row.get('ethAddress'))
    value = number(row.get('accountValue'))
    return (a, max(0., value)) if a and value is not None else None


def refresh_seconds(position, mark):
    if position is None:
        return 86400  # A new trade signal can bring an empty account forward.
    liq = number(position.get('liquidation_price'), True)
    if liq and mark and abs(liq/mark-1) <= .02:
        return 120
    return 300 if abs(position['size_btc']) >= 10 else 600


def linked_addresses(kind, data):
    # A schema error must not be interpreted as a successful empty directory.
    if kind in ('subAccounts', 'userVaultEquities'):
        if data is None and kind == 'subAccounts':
            return []
        if not isinstance(data, list) or any(not isinstance(r, dict) for r in data):
            raise ValueError('Unexpected account relationship response')
        key = 'subAccountUser' if kind == 'subAccounts' else 'vaultAddress'
        return sorted({a for r in data if (a := address(r.get(key)))})
    if not isinstance(data, dict):
        raise ValueError('Unexpected vault details response')
    relation = data.get('relationship') or {}
    children = (relation.get('data') or {}).get('childAddresses', []) if relation.get('type') == 'parent' else []
    if not isinstance(children, list):
        raise ValueError('Unexpected vault child addresses')
    return sorted({a for v in children if (a := address(v))})


class TradeBuffer:
    """Both counterparties, including small fills. Durable dedup happens in SQL."""
    def __init__(self, limit=PENDING_LIMIT):
        self.limit = limit
        self.rows = OrderedDict()
        self.dropped = 0

    def add(self, trades, now_ms=None):
        now_ms = int(time.time()*1000) if now_ms is None else now_ms
        for t in trades if isinstance(trades, list) else []:
            if not isinstance(t, dict) or t.get('coin') != 'BTC':
                continue
            px, sz = number(t.get('px'), True), number(t.get('sz'), True)
            ms = number(t.get('time'))
            tid = t.get('tid')
            users = t.get('users')
            if px is None or sz is None or ms is None or int(ms) != ms or not now_ms-7200000 <= ms <= now_ms+60000:
                continue
            if not isinstance(tid, int) or isinstance(tid, bool) or tid < 0 or not isinstance(users, list) or len(users) != 2:
                continue
            buyer, seller = address(users[0]), address(users[1])
            value = px*sz
            if not buyer or not seller or not math.isfinite(value) or not 0 < value <= 1e15:
                continue
            key = (int(ms), str(tid))
            if key in self.rows:
                continue
            if len(self.rows) >= self.limit:
                self.dropped += 1
                continue
            self.rows[key] = (*key, value, buyer, seller)


class WeightBudget:
    def __init__(self, clock=time.monotonic, sleep=asyncio.sleep):
        self.clock, self.sleep = clock, sleep
        self.used = deque()

    async def reserve(self, weight):
        while True:
            now = self.clock()
            while self.used and self.used[0][0] <= now-60:
                self.used.popleft()
            if sum(w for _, w in self.used)+weight <= WEIGHT_LIMIT:
                self.used.append((now, weight))
                return
            await self.sleep(max(.01, self.used[0][0]+60-now))


def request_weight(body):
    if body['type'] == 'clearinghouseState':
        return 2
    if body['type'] == 'candleSnapshot':
        r = body['req']
        rows = min(5000, max(1, math.ceil((r['endTime']-r['startTime'])/300000)+1))
        return 20+math.ceil(rows/60)
    return 21 if body['type'] == 'recentTrades' else 20


@asynccontextmanager
async def connection(p):
    if hasattr(p, 'acquire'):
        async with p.acquire() as c:
            yield c
    else:
        yield p


async def metric(c, source, admitted=0, dropped=0, evicted=0):
    await c.execute('''INSERT INTO hl_discovery_metrics(source,admitted,capacity_dropped,evicted) VALUES($1,$2,$3,$4)
      ON CONFLICT(source) DO UPDATE SET admitted=hl_discovery_metrics.admitted+excluded.admitted,
      capacity_dropped=hl_discovery_metrics.capacity_dropped+excluded.capacity_dropped,
      evicted=hl_discovery_metrics.evicted+excluded.evicted''', source, admitted, dropped, evicted)


async def register(p, candidates, source, limit=REGISTRY_LIMIT):
    """Only normalized addresses/scores enter the registry; no names or balances as exposure."""
    normalized = {}
    for a, value in candidates:
        a, value = address(a), number(value)
        if a and value is not None:
            normalized[a] = max(normalized.get(a, 0.), max(0., value))
    if not normalized:
        return 0
    if len(normalized) > 2000:
        raise ValueError('Discovery write exceeds batch budget')
    async with connection(p) as c:
        async with c.transaction():
            await c.execute('SELECT pg_advisory_xact_lock(732598342)')
            existing = {r['address'] for r in await c.fetch('SELECT address FROM hl_accounts WHERE address=ANY($1::text[])', list(normalized))}
            count = await c.fetchval('SELECT count(*) FROM hl_accounts')
            new = sorted((a for a in normalized if a not in existing), key=lambda a: (-normalized[a], a))
            need = max(0, len(new)-max(0, limit-count))
            evicted = []
            if need:
                # Preserve every account ever observed with BTC and all fresh trade signals.
                evicted = await c.fetch('''DELETE FROM hl_accounts WHERE address IN (
                  SELECT address FROM hl_accounts WHERE position IS NULL AND first_btc_at IS NULL
                  AND observed_at<now()-interval '6 hours' AND NOT tracking
                  AND (last_trade_at IS NULL OR last_trade_at<now()-interval '7 days')
                  AND NOT(address=ANY($2::text[])) ORDER BY observed_at,address LIMIT $1)
                  RETURNING address''', need, list(normalized))
            slots = max(0, limit-count+len(evicted))
            admitted = new[:slots]
            allowed = existing | set(admitted)
            rows = [(a, source, normalized[a]) for a in sorted(allowed)]
            await c.executemany('''INSERT INTO hl_accounts(address,discovery,priority) VALUES($1,$2,$3)
              ON CONFLICT(address) DO UPDATE SET last_seen_at=now(),
              priority=CASE WHEN excluded.discovery='public_leaderboard_full' THEN excluded.priority ELSE hl_accounts.priority END''', rows)
            await metric(c, source, len(admitted), len(new)-len(admitted), len(evicted))
            return len(admitted)


async def cohort(p):
    await p.execute('''WITH chosen AS (SELECT address FROM hl_accounts WHERE position IS NOT NULL
      ORDER BY abs((position->>'size_btc')::double precision) DESC,address LIMIT 1000)
      UPDATE hl_accounts a SET tracking=(c.address IS NOT NULL),
      next_check_at=CASE WHEN c.address IS NOT NULL AND NOT a.tracking THEN least(a.next_check_at,now()) ELSE a.next_check_at END
      FROM (SELECT h.address AS candidate,c.address FROM hl_accounts h LEFT JOIN chosen c USING(address)
       WHERE h.tracking IS DISTINCT FROM (c.address IS NOT NULL)) c WHERE a.address=c.candidate''')


async def pick(p, scan_count):
    queries = []
    if scan_count % 4:
        queries.append('''SELECT * FROM hl_accounts WHERE tracking AND next_check_at<=now()
          ORDER BY next_check_at,address LIMIT 1''')
    if scan_count % 8 == 0:
        # Guaranteed breadth slots: busy wallets cannot monopolize all discovery.
        queries.append('''SELECT * FROM hl_accounts WHERE observed_at IS NULL AND next_check_at<=now()
          ORDER BY priority DESC,address LIMIT 1''')
    else:
        queries.append('''SELECT * FROM hl_accounts WHERE discovery IN ('public_subaccount','public_vault','public_vault_child')
          AND observed_at IS NULL AND next_check_at<=now() ORDER BY priority DESC,address LIMIT 1''')
        queries.append('''SELECT * FROM hl_accounts WHERE signaled_at>coalesce(observed_at,TIMESTAMPTZ '1970-01-01')
          AND signaled_at>now()-interval '1 hour' AND next_check_at<=now()
          ORDER BY activity_usd DESC,signaled_at DESC,address LIMIT 1''')
    queries.append('''SELECT * FROM hl_accounts WHERE next_check_at<=now()
      ORDER BY next_check_at,priority DESC,address LIMIT 1''')
    for q in queries:
        row = await p.fetchrow(q)
        if row:
            return row


async def observed(p, row, position, mark):
    delay = refresh_seconds(position, mark)
    if position is not None and not row['tracking']:
        delay = 1800  # Newly discovered large accounts enter the next cohort refresh.
    await p.execute('''UPDATE hl_accounts SET observed_at=now(),position=$2,error_count=0,
      next_check_at=now()+$3::interval,
      first_btc_at=CASE WHEN $2::jsonb IS NOT NULL THEN coalesce(first_btc_at,now()) ELSE first_btc_at END,
      first_btc_source=CASE WHEN $2::jsonb IS NOT NULL THEN coalesce(first_btc_source,discovery) ELSE first_btc_source END,
      first_btc_size=CASE WHEN $2::jsonb IS NOT NULL THEN coalesce(first_btc_size,abs(($2::jsonb->>'size_btc')::double precision)) ELSE first_btc_size END
      WHERE address=$1''', row['address'], position, timedelta(seconds=delay))


async def failed(p, row):
    await p.execute('''UPDATE hl_accounts SET error_count=error_count+1,next_check_at=now()+$2::interval
      WHERE address=$1''', row['address'], timedelta(seconds=min(3600, 30*2**min(row['error_count'], 7))))


async def flush_trades(p, rows):
    if not rows:
        return 0
    async with connection(p) as c:
        async with c.transaction():
            accepted = await c.fetch('''INSERT INTO hl_discovery_trades(event_ms,tid,notional,buyer,seller)
              SELECT * FROM unnest($1::bigint[],$2::text[],$3::double precision[],$4::text[],$5::text[])
              ON CONFLICT DO NOTHING RETURNING *''', *[list(v) for v in zip(*rows)])
            signals = {}
            for r in accepted:
                window = r['event_ms']//600000
                for a in {r['buyer'], r['seller']}:
                    v = signals.setdefault((a, window), [0., 0., 0])
                    v[0] += r['notional']; v[1] = max(v[1], r['notional']); v[2] = max(v[2], r['event_ms'])
            users = sorted({a for a, _ in signals})
            for i in range(0,len(users),1000):
                await register(c, [(a, 0.) for a in users[i:i+1000]], 'public_btc_trade')
            await c.executemany('''UPDATE hl_accounts SET
              activity_usd=CASE WHEN $2>activity_window THEN $3 WHEN $2=activity_window THEN activity_usd+$3 ELSE activity_usd END,
              activity_window=greatest(activity_window,$2),largest_trade_usd=greatest(largest_trade_usd,$4),
              last_trade_at=greatest(last_trade_at,to_timestamp($5::double precision/1000)),
              signaled_at=greatest(signaled_at,to_timestamp($5::double precision/1000)),last_seen_at=now(),
              next_check_at=least(next_check_at,greatest(now(),coalesce(attempted_at,TIMESTAMPTZ '1970-01-01')+
                CASE WHEN tracking THEN interval '120 seconds' ELSE interval '60 seconds' END))
              WHERE address=$1''', [(a, w, *v) for (a, w), v in sorted(signals.items())])
            return len(accepted)


async def cleanup(p):
    await p.execute('DELETE FROM hl_discovery_trades WHERE event_ms<$1', int((time.time()-7200)*1000))
    await p.execute('''DELETE FROM hl_discovery_trades WHERE (event_ms,tid) IN (
      SELECT event_ms,tid FROM hl_discovery_trades ORDER BY event_ms DESC,tid OFFSET $1)''', TRADE_LIMIT)


async def diagnostics(p):
    q = await p.fetchrow('''SELECT count(*) FILTER(WHERE observed_at IS NULL) never_checked,
      count(*) FILTER(WHERE next_check_at<=now()) due,
      count(*) FILTER(WHERE tracking) refresh_cohort,
      count(*) FILTER(WHERE position IS NOT NULL AND observed_at<now()-interval '15 minutes') stale_positions,
      count(*) FILTER(WHERE error_count>0) accounts_in_backoff,
      percentile_cont(.5) WITHIN GROUP(ORDER BY extract(epoch FROM now()-observed_at))
        FILTER(WHERE tracking AND observed_at IS NOT NULL) median_position_age_s,
      max(extract(epoch FROM now()-observed_at)) FILTER(WHERE tracking AND observed_at IS NOT NULL) oldest_position_age_s
      FROM hl_accounts''')
    sources = await p.fetch('''SELECT discovery source,count(*) candidates,count(observed_at) checked,
      count(*) FILTER(WHERE position IS NOT NULL AND observed_at>=now()-interval '15 minutes') fresh_btc_positions
      FROM hl_accounts GROUP BY discovery ORDER BY discovery''')
    discoveries = await p.fetch('''SELECT first_btc_source source,count(*) accounts,
      sum(first_btc_size) first_observed_gross_btc,
      coalesce(sum(abs((position->>'size_btc')::double precision)) FILTER(WHERE observed_at>=now()-interval '15 minutes'),0) current_fresh_gross_btc
      FROM hl_accounts WHERE first_btc_at IS NOT NULL GROUP BY first_btc_source ORDER BY first_btc_source''')
    metrics = await p.fetch('SELECT * FROM hl_discovery_metrics ORDER BY source')
    return {'version':VERSION,'registry_limit':REGISTRY_LIMIT,'request_weight_limit_per_minute':WEIGHT_LIMIT,
      'pending_trade_limit':PENDING_LIMIT,'trade_dedup_retention_hours':2,**dict(q),
      'sources':[dict(r) for r in sources],'first_btc_discoveries':[dict(r) for r in discoveries],
      'capacity':[dict(r) for r in metrics],
      'attribution':'First discovery path, not causal accuracy or new position opening. Vault equity is never added to position exposure.',
      'activity':'Sum of deduplicated received fills in fixed 10-minute windows; discovery priority only, not inferred position size.'}
