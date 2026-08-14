from __future__ import annotations
import json, os, urllib.parse, urllib.request
from datetime import datetime
from pathlib import Path
from .free_sources import seed_free_source_catalog
from .identity import stable_id
from .provider_fetch import utcnow

BASE='https://api.the-odds-api.com/v4'
SOURCE_NAME='The Odds API Free Tier'

def _token(explicit=None):
    tok=explicit or os.getenv('THE_ODDS_API_KEY')
    if not tok:
        raise RuntimeError('THE_ODDS_API_KEY is required; MASTER never stores provider secrets in files or DB')
    return tok

def fetch_current_odds(sport_key:str, *, regions='eu', markets='h2h,totals', odds_format='decimal', api_key=None, timeout=45):
    """Fetch current featured odds from the free/current endpoint.

    Returns (document, metadata). The API key is never included in returned metadata.
    Header quota values are captured when the provider supplies them.
    """
    params={'apiKey':_token(api_key),'regions':regions,'markets':markets,'oddsFormat':odds_format,'dateFormat':'iso'}
    url=f"{BASE}/sports/{urllib.parse.quote(sport_key)}/odds?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,headers={'User-Agent':'MASTER-Football-Free-Collector/2.0'})
    fetched_at=utcnow()
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(); status=getattr(r,'status',200); headers={str(k).lower():str(v) for k,v in r.headers.items()}
    doc=json.loads(raw.decode())
    meta={'fetched_at':fetched_at,'http_status':status,'bytes':len(raw),
          'requests_remaining':_int_or_none(headers.get('x-requests-remaining')),
          'requests_used':_int_or_none(headers.get('x-requests-used')),
          'requests_last':_int_or_none(headers.get('x-requests-last'))}
    return doc,meta,raw

def _int_or_none(v):
    try: return int(v)
    except Exception: return None


def _temporal_relation(observed_at, kickoff):
    try:
        o=datetime.fromisoformat(str(observed_at).replace('Z','+00:00'))
        k=datetime.fromisoformat(str(kickoff).replace('Z','+00:00'))
        return 'PRE_EVENT_DATE' if o<k else ('POST_EVENT_DATE' if o>k else 'SAME_DATE_UNKNOWN')
    except Exception:
        return 'UNKNOWN'

def _market_descriptor(key):
    if key in {'h2h','h2h_3_way'}: return ('MAIN','1X2','THREE_WAY')
    if key=='totals': return ('GOALS','GOALS_TOTAL','TOTAL')
    if key=='spreads': return ('MAIN','ASIAN_HANDICAP','SPREAD')
    return None

def _selection(kind,outcome,home,away):
    name=str(outcome.get('name') or '')
    if kind in {'THREE_WAY','SPREAD'}:
        if name==home: return 'HOME'
        if name==away: return 'AWAY'
        if name.casefold()=='draw': return 'DRAW'
    if kind=='TOTAL':
        n=name.casefold()
        if n=='over': return 'OVER'
        if n=='under': return 'UNDER'
    return None

def stage_current_odds(con, sport_key:str, doc, *, fetched_at=None, raw_locator=None):
    """Stage current The Odds API observations without fuzzy-linking canonical fixtures.

    Bookmaker last_update is treated as provider-time for that bookmaker snapshot when present.
    If absent, MASTER fetch time is used and timing_quality is FETCH_TIME_ONLY.
    """
    src=seed_free_source_catalog(con); sid=src['the_odds_api_free']; fetched_at=fetched_at or utcnow()
    events=doc if isinstance(doc,list) else (doc.get('data',[]) if isinstance(doc,dict) else [])
    fc=oc=0
    for event in events or []:
        sk=str(event.get('id') or '')
        home=str(event.get('home_team') or ''); away=str(event.get('away_team') or '')
        if not sk or not home or not away: continue
        kickoff=event.get('commence_time')
        fid=stable_id('external-fixture',sid,sk,fetched_at)
        rel_fetch=_temporal_relation(fetched_at,kickoff)
        con.execute('''INSERT OR IGNORE INTO external_fixture_observations(external_fixture_observation_id,source_id,source_fixture_key,competition_hint,
          kickoff_utc,event_temporal_relation,home_name,away_name,observed_at,timing_quality,raw_locator,raw_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',(fid,sid,sk,sport_key,kickoff,rel_fetch,home,away,fetched_at,'FETCH_TIME_ONLY',raw_locator,json.dumps(event,ensure_ascii=False)))
        fc += con.execute('SELECT changes()').fetchone()[0]
        # Preserve explicit links only if they already exist; never fuzzy-auto-link.
        link=con.execute('SELECT fixture_id FROM fixture_source_links WHERE source_id=? AND source_fixture_key=?',(sid,sk)).fetchone()
        linked=link['fixture_id'] if link else None
        if linked:
            con.execute('UPDATE external_fixture_observations SET linked_fixture_id=? WHERE source_id=? AND source_fixture_key=?',(linked,sid,sk))
        for book in event.get('bookmakers',[]) or []:
            bookmaker=str(book.get('key') or book.get('title') or '')
            if not bookmaker: continue
            provider_ts=book.get('last_update')
            obs_at=str(provider_ts or fetched_at); quality='EXACT_SOURCE' if provider_ts else 'FETCH_TIME_ONLY'
            for market in book.get('markets',[]) or []:
                desc=_market_descriptor(str(market.get('key') or ''))
                if not desc: continue
                family,mkey,kind=desc
                for outcome in market.get('outcomes',[]) or []:
                    sel=_selection(kind,outcome,home,away)
                    if not sel: continue
                    try: price=float(outcome.get('price'))
                    except Exception: continue
                    if price<=1: continue
                    line=outcome.get('point')
                    try: line=None if line is None else float(line)
                    except Exception: line=None
                    line_key='' if line is None else format(abs(line),'.6g')
                    oid=stable_id('external-odds',sid,sk,bookmaker,mkey,sel,line_key,obs_at)
                    rel=_temporal_relation(obs_at,kickoff)
                    con.execute('''INSERT OR IGNORE INTO external_odds_observations(external_odds_observation_id,source_id,source_fixture_key,bookmaker,
                      market_family,market_key,selection_key,line,line_key,decimal_odds,observed_at,timing_quality,event_temporal_relation,snapshot_type,raw_column,source_record_key,linked_fixture_id)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(oid,sid,sk,bookmaker,family,mkey,sel,line,line_key,price,obs_at,quality,rel,'UNKNOWN',str(market.get('key') or ''),sk,linked))
                    oc += con.execute('SELECT changes()').fetchone()[0]
    con.commit()
    return {'source_id':sid,'sport_key':sport_key,'fixture_observations':fc,'odds_observations':oc,'fetched_at':fetched_at}

def collect_current_odds(con, sport_key:str, *, regions='eu', markets='h2h,totals', raw_dir=None, api_key=None):
    doc,meta,raw=fetch_current_odds(sport_key,regions=regions,markets=markets,api_key=api_key)
    raw_path=None
    if raw_dir:
        d=Path(raw_dir); d.mkdir(parents=True,exist_ok=True)
        raw_path=d/f"the_odds_api_current_{sport_key.replace('/','_')}_{meta['fetched_at'].replace(':','')}.json"; raw_path.write_bytes(raw)
    staged=stage_current_odds(con,sport_key,doc,fetched_at=meta['fetched_at'],raw_locator=str(raw_path) if raw_path else None)
    return {**meta,**staged,'raw_path':str(raw_path) if raw_path else None}
