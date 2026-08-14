from __future__ import annotations
import json, os, urllib.parse, urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .advanced import ensure_source, register_provider_capability, resolve_linked_fixture
from .identity import stable_id
from .market import normalize_snapshot_group
from .provider_fetch import record_provider_fetch, utcnow

BASE='https://api.the-odds-api.com/v4'
SOURCE_NAME='The Odds API'
ADDITIONAL_MARKETS_START='2023-05-03T05:30:00Z'
SECONDARY_MARKETS=(
 'alternate_spreads_corners','alternate_totals_corners','alternate_team_totals_corners','corners_1x2',
 'alternate_spreads_cards','alternate_totals_cards',
 'player_goal_scorer_anytime','player_first_goal_scorer','player_last_goal_scorer','player_to_receive_card',
 'player_to_receive_red_card','player_shots_on_target','player_shots','player_assists'
)


def source_id_and_capabilities(con):
    sid=ensure_source(con,SOURCE_NAME,'COMMERCIAL_MARKET_API','https://the-odds-api.com',15,
        'Historical/current bookmaker snapshots. API key must come from environment; secrets are never stored in MASTER DB.',
        'Historical bookmaker availability varies by sport/date/market; additional market history is event-level and partial.')
    register_provider_capability(con,sid,'historical_odds','PRODUCTION',timing_granularity='EXACT',license_class='COMMERCIAL',
        notes='Provider documents historical featured-market snapshots from 2020-06-06; 10-minute cadence initially, 5-minute cadence from Sep 2022. Paid plan required.')
    register_provider_capability(con,sid,'historical_additional_markets','PARTIAL',timing_granularity='EXACT',license_class='COMMERCIAL',
        notes='Provider documents historical event-level additional markets from 2023-05-03T05:30Z at 5-minute snapshots; coverage depends on bookmaker/sport/market.')
    return sid


def _token(explicit=None):
    tok=explicit or os.getenv('THE_ODDS_API_KEY')
    if not tok: raise RuntimeError('THE_ODDS_API_KEY is required; MASTER never stores provider secrets in files or DB')
    return tok


def _get_json(url, timeout=45):
    req=urllib.request.Request(url,headers={'User-Agent':'MASTER-Football/1.0'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(); status=getattr(r,'status',200)
    return json.loads(raw.decode()),raw,status


def _fetch(con, endpoint_key, url, params, *, raw_dir=None):
    sid=source_id_and_capabilities(con); requested=utcnow()
    try:
        doc,raw,status=_get_json(url); raw_path=None
        if raw_dir:
            d=Path(raw_dir); d.mkdir(parents=True,exist_ok=True)
            raw_path=d/f"the_odds_api_{endpoint_key}_{requested.replace(':','')}.json"; raw_path.write_bytes(raw)
        record_provider_fetch(con,sid,endpoint_key,params,requested_at=requested,provider_snapshot_at=doc.get('timestamp') if isinstance(doc,dict) else None,
                              http_status=status,response_bytes=raw,raw_path=raw_path,success=True)
        return doc
    except Exception as e:
        record_provider_fetch(con,sid,endpoint_key,params,requested_at=requested,success=False,notes=type(e).__name__+': '+str(e)); raise


def fetch_historical_snapshot(con, sport_key:str, date_iso:str, *, regions='eu', markets='h2h,totals', api_key=None, odds_format='decimal', raw_dir=None):
    params={'apiKey':_token(api_key),'regions':regions,'markets':markets,'date':date_iso,'oddsFormat':odds_format,'dateFormat':'iso'}
    url=f"{BASE}/historical/sports/{urllib.parse.quote(sport_key)}/odds?"+urllib.parse.urlencode(params)
    return _fetch(con,'historical_odds',url,params,raw_dir=raw_dir)


def fetch_historical_events(con, sport_key:str, date_iso:str, *, api_key=None, raw_dir=None):
    params={'apiKey':_token(api_key),'date':date_iso}
    url=f"{BASE}/historical/sports/{urllib.parse.quote(sport_key)}/events?"+urllib.parse.urlencode(params)
    return _fetch(con,'historical_events',url,params,raw_dir=raw_dir)


def fetch_historical_event_odds(con, sport_key:str, event_id:str, date_iso:str, *, regions='eu', markets=None, api_key=None, odds_format='decimal', raw_dir=None):
    markets=markets or ','.join(SECONDARY_MARKETS)
    if datetime.fromisoformat(date_iso.replace('Z','+00:00')) < datetime.fromisoformat(ADDITIONAL_MARKETS_START.replace('Z','+00:00')):
        raise ValueError('ADDITIONAL_MARKET_HISTORY_UNAVAILABLE_BEFORE_'+ADDITIONAL_MARKETS_START)
    params={'apiKey':_token(api_key),'regions':regions,'markets':markets,'date':date_iso,'oddsFormat':odds_format,'dateFormat':'iso'}
    url=f"{BASE}/historical/sports/{urllib.parse.quote(sport_key)}/events/{urllib.parse.quote(str(event_id))}/odds?"+urllib.parse.urlencode(params)
    return _fetch(con,'historical_event_odds',url,params,raw_dir=raw_dir)


def historical_snapshot_plan(kickoff_iso:str):
    k=datetime.fromisoformat(kickoff_iso.replace('Z','+00:00'))
    points=[('OPENING',timedelta(hours=24),'T_MINUS_24H'),('PRECLOSE',timedelta(hours=6),'T_MINUS_6H'),
            ('ENTRY',timedelta(hours=1),'T_MINUS_60M'),('CLOSING',timedelta(minutes=5),'T_MINUS_5M_CLOSING_PROXY')]
    return [{'snapshot_type':st,'requested_at':(k-d).astimezone(timezone.utc).isoformat().replace('+00:00','Z'),'snapshot_basis':basis} for st,d,basis in points]


def secondary_snapshot_plan(kickoff_iso:str):
    plan=historical_snapshot_plan(kickoff_iso)
    start=datetime.fromisoformat(ADDITIONAL_MARKETS_START.replace('Z','+00:00'))
    return [p for p in plan if datetime.fromisoformat(p['requested_at'].replace('Z','+00:00'))>=start]


def _participant(outcome):
    desc=outcome.get('description')
    if desc: return ('PLAYER_OR_TEAM',str(desc),normalize_participant(str(desc)))
    return (None,None,'')


def normalize_participant(s): return ' '.join(str(s).casefold().replace('.','').split())


def _descriptor(market, home, away):
    key=market.get('key')
    if key in {'h2h','h2h_3_way'}: return {'family':'MAIN','key':'1X2','kind':'THREE_WAY'}
    if key=='totals': return {'family':'GOALS','key':'GOALS_TOTAL','kind':'TOTAL'}
    if key=='btts': return {'family':'GOALS','key':'BTTS','kind':'YES_NO'}
    if key=='draw_no_bet': return {'family':'MAIN','key':'DNB','kind':'TWO_WAY_TEAM'}
    if key=='alternate_totals_corners': return {'family':'CORNERS','key':'CORNERS_TOTAL','kind':'TOTAL'}
    if key=='alternate_team_totals_corners': return {'family':'CORNERS','key':'TEAM_CORNERS_TOTAL','kind':'PARTICIPANT_TOTAL'}
    if key=='alternate_spreads_corners': return {'family':'CORNERS','key':'CORNERS_HANDICAP','kind':'SPREAD'}
    if key=='corners_1x2': return {'family':'CORNERS','key':'CORNERS_1X2','kind':'THREE_WAY'}
    if key=='alternate_totals_cards': return {'family':'CARDS','key':'CARDS_TOTAL','kind':'TOTAL'}
    if key=='alternate_spreads_cards': return {'family':'CARDS','key':'CARDS_HANDICAP','kind':'SPREAD'}
    pmap={
      'player_goal_scorer_anytime':('PLAYER_GOALS','ANYTIME_GOAL','PLAYER_BINARY'),
      'player_first_goal_scorer':('PLAYER_GOALS','FIRST_GOAL','PLAYER_BINARY'),
      'player_last_goal_scorer':('PLAYER_GOALS','LAST_GOAL','PLAYER_BINARY'),
      'player_to_receive_card':('PLAYER_CARDS','PLAYER_CARD','PLAYER_BINARY'),
      'player_to_receive_red_card':('PLAYER_CARDS','PLAYER_RED_CARD','PLAYER_BINARY'),
      'player_shots_on_target':('PLAYER_SOT','PLAYER_SOT','PLAYER_TOTAL'),
      'player_shots':('PLAYER_SHOTS','PLAYER_SHOTS','PLAYER_TOTAL'),
      'player_assists':('PLAYER_ASSISTS','PLAYER_ASSISTS','PLAYER_TOTAL'),
    }
    if key in pmap:
        f,k,kind=pmap[key]; return {'family':f,'key':k,'kind':kind}
    return None


def _selection(desc, outcome, home, away):
    name=str(outcome.get('name','')); n=name.casefold(); kind=desc['kind']
    if kind in {'THREE_WAY','TWO_WAY_TEAM','SPREAD'}:
        if name==home: return 'HOME'
        if name==away: return 'AWAY'
        if n=='draw': return 'DRAW'
    if kind in {'TOTAL','PARTICIPANT_TOTAL','PLAYER_TOTAL'}:
        if n=='over': return 'OVER'
        if n=='under': return 'UNDER'
    if kind in {'YES_NO','PLAYER_BINARY'}:
        if n in {'yes','no'}: return n.upper()
        # Some books expose one-sided named-player yes prices. Keep execution/reference observation but do not invent NO.
        return 'YES'
    return name.upper()[:80] if name else None


def _line_group(desc, outcome):
    p=outcome.get('point')
    if p is None: return None,''
    p=float(p)
    if desc['kind']=='SPREAD': return p,format(abs(p),'.6g')
    return p,format(p,'.6g')


def _participant_fields(desc,outcome,home,away):
    kind=desc['kind']; descname=outcome.get('description')
    if kind in {'PLAYER_TOTAL','PLAYER_BINARY'}:
        pname=str(descname or outcome.get('name') or '')
        return 'PLAYER',pname,normalize_participant(pname),str(outcome.get('description') or '')
    if kind=='PARTICIPANT_TOTAL':
        pname=str(descname or '')
        if not pname and outcome.get('name') not in {'Over','Under'}: pname=str(outcome.get('name'))
        return 'TEAM',pname,normalize_participant(pname),''
    return None,None,'',''


def _event_list(doc):
    d=doc.get('data',[]) if isinstance(doc,dict) else []
    if isinstance(d,dict): return [d]
    return d or []


def ingest_historical_snapshot(con, doc:dict, *, snapshot_type:str, requested_snapshot_at:str|None=None, snapshot_basis='PROVIDER_HISTORICAL_SNAPSHOT', strict_links=True):
    if snapshot_type not in {'OPENING','PRECLOSE','ENTRY','CLOSING','LIVE','UNKNOWN'}: raise ValueError(snapshot_type)
    sid=source_id_and_capabilities(con); snapshot_ts=doc.get('timestamp') if isinstance(doc,dict) else None
    inserted=events=groups=0; skipped=[]
    for event in _event_list(doc):
        ext=str(event.get('id'))
        try: fixture_id=resolve_linked_fixture(con,sid,ext)
        except KeyError:
            if strict_links: skipped.append(ext)
            continue
        events+=1; home=event.get('home_team'); away=event.get('away_team')
        for book in event.get('bookmakers',[]):
            bookmaker=book.get('key') or book.get('title'); last_update=book.get('last_update') or snapshot_ts
            for market in book.get('markets',[]):
                md=_descriptor(market,home,away)
                if not md: continue
                group_participants=set()
                for o in market.get('outcomes',[]):
                    sel=_selection(md,o,home,away); price=o.get('price')
                    if not sel or not price or float(price)<=1: continue
                    line,line_key=_line_group(md,o); ptype,pname,pkey,ppkey=_participant_fields(md,o,home,away)
                    group_participants.add((pkey,line_key))
                    raw_hash=stable_id('toa-row',ext,bookmaker,market.get('key'),pkey,sel,line_key,last_update,price)
                    oid=stable_id('odds',fixture_id,sid,bookmaker,md['key'],pkey,sel,line_key,snapshot_type,raw_hash)
                    con.execute('''INSERT OR IGNORE INTO odds_snapshots(odds_snapshot_id,fixture_id,source_id,bookmaker,market_family,market_key,
                      selection_key,line,line_key,decimal_odds,observed_at,ingested_at,timestamp_quality,snapshot_type,is_sharp,is_execution,
                      reference_confidence,raw_column,source_row_hash,provider_event_id,requested_snapshot_at,snapshot_basis,bookmaker_last_update,
                      participant_type,participant_name,participant_key,provider_participant_key)
                      VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                      (oid,fixture_id,sid,bookmaker,md['family'],md['key'],sel,line,line_key,float(price),last_update,utcnow(),'EXACT',snapshot_type,
                       0,0,'UNAVAILABLE',market.get('key'),raw_hash,ext,requested_snapshot_at,snapshot_basis,last_update,ptype,pname,pkey,ppkey))
                    inserted += con.execute('SELECT changes()').fetchone()[0]
                for pkey,line_key in group_participants:
                    try:
                        if normalize_snapshot_group(con,fixture_id,bookmaker,md['key'],snapshot_type,source_id=sid,line_key=line_key,
                                                    requested_snapshot_at=requested_snapshot_at,participant_key=pkey): groups+=1
                    except Exception: pass
    con.commit(); return {'events_linked':events,'odds_inserted':inserted,'groups_normalized_attempted':groups,'unlinked_event_ids':skipped}
