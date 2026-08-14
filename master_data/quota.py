from __future__ import annotations
from datetime import datetime, timezone
from .identity import stable_id

DEFAULT_FREE_LIMITS={
    'API_FOOTBALL': {'period':'DAY','limit':100,'reserve':15},
    'THE_ODDS_API': {'period':'MONTH','limit':500,'reserve':50},
}

def _now(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')

def period_key(provider_key:str, when=None):
    d=when or datetime.now(timezone.utc)
    spec=DEFAULT_FREE_LIMITS[provider_key]
    return d.strftime('%Y-%m-%d') if spec['period']=='DAY' else d.strftime('%Y-%m')

def used_in_period(con, provider_key:str, pkey=None):
    pkey=pkey or period_key(provider_key)
    r=con.execute('SELECT COALESCE(SUM(request_cost),0) s FROM free_quota_ledger WHERE provider_key=? AND period_key=?',(provider_key,pkey)).fetchone()
    return int(r['s'] or 0)

def quota_state(con, provider_key:str, pkey=None):
    if provider_key not in DEFAULT_FREE_LIMITS: raise KeyError(provider_key)
    pkey=pkey or period_key(provider_key); spec=DEFAULT_FREE_LIMITS[provider_key]
    used=used_in_period(con,provider_key,pkey); remaining=max(0,spec['limit']-used)
    return {'provider_key':provider_key,'period_key':pkey,'limit':spec['limit'],'reserve':spec['reserve'],
            'used':used,'remaining':remaining,'spendable':max(0,remaining-spec['reserve'])}

def can_spend(con, provider_key:str, cost:int=1):
    return quota_state(con,provider_key)['spendable'] >= int(cost)

def record_cost(con, provider_key:str, cost:int, *, requests_remaining=None, source='LOCAL_ESTIMATE', notes=None, observed_at=None):
    observed_at=observed_at or _now(); pkey=period_key(provider_key)
    lid=stable_id('free-quota',provider_key,pkey,observed_at,source,cost,notes or '')
    con.execute('''INSERT OR IGNORE INTO free_quota_ledger(ledger_id,provider_key,period_key,observed_at,requests_used,requests_remaining,request_cost,source,notes)
                   VALUES(?,?,?,?,?,?,?,?,?)''',(lid,provider_key,pkey,observed_at,cost,requests_remaining,cost,source,notes))
    con.commit(); return quota_state(con,provider_key,pkey)

def plan_free_budget(con, shortlist_fixtures:int=5):
    af=quota_state(con,'API_FOOTBALL'); toa=quota_state(con,'THE_ODDS_API')
    # Conservative plan: API-Football 4 calls per shortlisted fixture (fixture/lineup/player/injury);
    # The Odds API current featured call cost depends on regions x markets. Assume 2 credits for h2h+totals in one region.
    af_possible=min(shortlist_fixtures,af['spendable']//4)
    toa_calls=toa['spendable']//2
    return {'api_football':af,'the_odds_api':toa,'recommended_context_fixtures':int(af_possible),
            'recommended_featured_odds_calls_remaining':int(toa_calls),
            'policy':'BROAD_SCAN_PUBLIC_FILES__PAID_STYLE_FREE_APIS_ONLY_FOR_SHORTLIST'}
