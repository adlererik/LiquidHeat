"""Reported positions, without inferred entry/leverage/survival assumptions."""
import heapq
import json
import math
import re
import zlib
import numpy as np

METHOD = 'hyperliquid-reported-v1'
FRESH_SECONDS = 900
TOP_N = 1000
ADDRESS = re.compile(r'^0x[0-9a-f]{40}$')


def number(v, positive=False):
    try:
        x = float(v)
    except (ValueError, TypeError, OverflowError):
        return None
    return x if math.isfinite(x) and (not positive or x > 0) else None


def btc_position(state):
    """None means successfully observed no open BTC position, NOT request failure."""
    if not isinstance(state, dict) or not isinstance(state.get('assetPositions'), list):
        raise ValueError('Unexpected clearinghouseState schema')
    for item in state['assetPositions']:
        p = item.get('position', {})
        if p.get('coin') != 'BTC':
            continue
        size = number(p.get('szi'))
        entry = number(p.get('entryPx'), True)
        if size == 0:
            return None
        if size is None or entry is None:
            raise ValueError('Invalid BTC position size/entry')
        if not size:
            return None
        lev = p.get('leverage') or {}
        return {'size_btc':size,'entry':entry,'liquidation_price':number(p.get('liquidationPx'),True),
                'leverage':number(lev.get('value'),True),'margin_mode':str(lev.get('type','unknown')),
                'margin_used':number(p.get('marginUsed'))}
    return None


class LeaderboardRows:
    """Incremental JSON rows; never decode the entire multi-MB leaderboard."""
    def __init__(self):
        self.buffer = ''; self.started = False; self.ended = False
        self.decoder = json.JSONDecoder()

    def feed(self, chunk):
        self.buffer += chunk
        if len(self.buffer) > 2_000_000:
            raise ValueError('Leaderboard row/header exceeds bounded parser buffer')
        if not self.started:
            at = self.buffer.find('"leaderboardRows"')
            bracket = self.buffer.find('[', at) if at >= 0 else -1
            if bracket < 0:
                return []
            self.buffer = self.buffer[bracket+1:]; self.started = True
        rows = []
        while not self.ended:
            self.buffer = self.buffer.lstrip(' \n\r\t,')
            if self.buffer.startswith(']'):
                self.ended = True; self.buffer = ''; break
            try:
                row, at = self.decoder.raw_decode(self.buffer)
            except json.JSONDecodeError:
                break
            if not isinstance(row, dict):
                raise ValueError('Unexpected leaderboard row')
            rows.append(row); self.buffer = self.buffer[at:]
        return rows


def seed_row(heap, row, limit=2000):
    address = str(row.get('ethAddress','')).lower()
    value = number(row.get('accountValue'),True)
    if not ADDRESS.fullmatch(address) or value is None:
        return
    pair = (value, address)
    if len(heap) < limit:
        heapq.heappush(heap,pair)
    elif pair > heap[0]:
        heapq.heapreplace(heap,pair)


def projection(positions, mark, oi_btc=None):
    """One current observation per account. Rank before excluding null thresholds.

    Input must already be fresh. Discrete bin assignment preserves included BTC;
    no Gaussian smoothing, hypothetical tiers, extrapolation, sweeps, or decay.
    """
    if number(mark,True) is None:
        raise ValueError('Invalid mark')
    selected = sorted(positions,key=lambda x:(-abs(x['size_btc']),x['address']))[:TOP_N]
    start = math.floor(mark*.5/25)*25
    n = math.ceil((mark*1.5-start)/25)+1
    matrix = np.zeros((2,n),dtype=np.float64)
    missing=outside=0; gross=eligible=0.
    for p in selected:
        qty=abs(p['size_btc']); gross+=qty
        liq=p['liquidation_price']
        if liq is None:
            missing+=1; continue
        i=math.floor((liq-start)/25)
        if not 0<=i<n:
            outside+=1; continue
        matrix[0 if p['size_btc']>0 else 1,i]+=qty; eligible+=qty
    audit={'selected_positions':len(selected),'selection_limit':TOP_N,'null_thresholds':missing,
           'outside_grid_positions':outside,'selected_gross_btc':gross,'included_gross_btc':eligible,
           'market_one_sided_oi_btc':oi_btc,
           'selected_share_of_market_gross':gross/(2*oi_btc) if oi_btc and oi_btc>0 else None,
           'universe':'largest BTC positions among discovered, fresh accounts; global rank unknown',
           'freshness_limit_s':FRESH_SECONDS,'thresholds':'reported at account query time; mutable, sometimes null',
           'smoothing':'none; direct $25 bins','history':'prospectively recorded observations only'}
    return start,matrix,audit,selected


def pack(matrix):
    return zlib.compress(np.asarray(matrix,dtype='<f4').tobytes(),6)


def unpack(blob,n):
    return np.frombuffer(zlib.decompress(blob),dtype='<f4').reshape(2,n).astype(np.float64)


def large_trade_addresses(trades,minimum=50000.):
    """Discovery only. A fill does not prove opening, whale size, or position side."""
    addresses={}
    if not isinstance(trades,list):return addresses
    for trade in trades:
        if not isinstance(trade,dict) or trade.get('coin')!='BTC':continue
        price=number(trade.get('px'),True);size=number(trade.get('sz'),True)
        if price is None or size is None:continue
        notional=price*size
        if not math.isfinite(notional) or notional<minimum:continue
        users=trade.get('users')
        if not isinstance(users,list):continue
        for user in users:
            address=str(user).lower()
            if ADDRESS.fullmatch(address) and address!='0x'+'0'*40:
                addresses[address]=max(addresses.get(address,0.),notional)
    return addresses
