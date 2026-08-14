from __future__ import annotations
import json, os, urllib.parse, urllib.request
from pathlib import Path
from .advanced import (ensure_source, register_provider_capability, resolve_linked_fixture,
                       upsert_team_advanced_stats, ensure_player, ingest_lineup_snapshot)
from .provider_fetch import record_provider_fetch, utcnow

BASE='https://api.sportmonks.com/v3/football'
SOURCE_NAME='Sportmonks Football API'
DEFAULT_INCLUDES='participants;scores;lineups.player;statistics;xgfixture;referees'


def source_id_and_capabilities(con):
    sid=ensure_source(con,SOURCE_NAME,'COMMERCIAL_DATA_API','https://www.sportmonks.com/football-api/',10,
        'Licensed production candidate for fixture/stats/lineup/xG ingestion. API token must come from environment.',
        'Coverage and xG availability depend on subscription/league. Historical availability must be audited before feature admission.')
    for cap in ['fixtures','match_statistics','lineups','referees']:
        register_provider_capability(con,sid,cap,'PRODUCTION',timing_granularity='EXACT',license_class='COMMERCIAL',
                                     notes='Provider coverage must be checked per subscribed league/season.')
    register_provider_capability(con,sid,'xg','PRODUCTION',timing_granularity='POST_MATCH',license_class='COMMERCIAL',
                                 notes='xG availability depends on package/league; post-match xG can only affect future fixtures via shifted as-of features.')
    return sid


def _token(explicit=None):
    tok=explicit or os.getenv('SPORTMONKS_TOKEN')
    if not tok: raise RuntimeError('SPORTMONKS_TOKEN is required; MASTER never stores provider secrets in files or DB')
    return tok


def _get_json(url, timeout=30):
    req=urllib.request.Request(url,headers={'User-Agent':'MASTER-Football/1.0'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        raw=r.read(); status=getattr(r,'status',200)
    return json.loads(raw.decode()),raw,status


def fetch_fixture(con, fixture_provider_id:str|int, *, include=DEFAULT_INCLUDES, api_token=None, raw_dir:str|Path|None=None):
    sid=source_id_and_capabilities(con)
    params={'api_token':_token(api_token),'include':include}
    url=f"{BASE}/fixtures/{fixture_provider_id}?"+urllib.parse.urlencode(params,safe=';')
    requested=utcnow()
    try:
        doc,raw,status=_get_json(url)
        raw_path=None
        if raw_dir:
            d=Path(raw_dir); d.mkdir(parents=True,exist_ok=True); raw_path=d/f'sportmonks_fixture_{fixture_provider_id}.json'; raw_path.write_bytes(raw)
        record_provider_fetch(con,sid,'fixture_with_advanced',params,requested_at=requested,http_status=status,
                              response_bytes=raw,raw_path=raw_path,success=True)
        return doc
    except Exception as e:
        record_provider_fetch(con,sid,'fixture_with_advanced',params,requested_at=requested,success=False,notes=type(e).__name__+': '+str(e))
        raise


def _loc_side(location):
    z=str(location or '').casefold()
    return 'home' if z in {'home','local'} else ('away' if z in {'away','visitor'} else None)


def _extract_xg(data):
    out={'home':None,'away':None}
    vals=data.get('xgfixture') or data.get('xg') or []
    if isinstance(vals,dict): vals=vals.get('data',[]) or vals.get('values',[])
    for x in vals or []:
        side=_loc_side(x.get('location') or x.get('side'))
        val=x.get('value')
        if side and val is not None: out[side]=float(val)
    return out


def _participants(data):
    vals=data.get('participants') or []
    if isinstance(vals,dict): vals=vals.get('data',[])
    out={}
    for p in vals or []:
        side=_loc_side((p.get('meta') or {}).get('location') or p.get('location'))
        if side: out[side]=p
    return out


def ingest_fixture_payload(con, doc:dict, *, observed_at:str|None=None, ingest_actual_lineup=True):
    sid=source_id_and_capabilities(con)
    data=doc.get('data',doc)
    ext=str(data.get('id'))
    fixture_id=resolve_linked_fixture(con,sid,ext)
    fx=con.execute('SELECT kickoff_utc,home_team_id,away_team_id FROM fixtures WHERE fixture_id=?',(fixture_id,)).fetchone()
    if not fx: raise KeyError(fixture_id)
    obs=observed_at or utcnow()
    xg=_extract_xg(data)
    nx=upsert_team_advanced_stats(con,fixture_id,sid,
        home={'xg':xg['home']} if xg['home'] is not None else {},
        away={'xg':xg['away']} if xg['away'] is not None else {},
        observed_at=obs,availability_class='POST_MATCH_SOURCE',source_locator=f'sportmonks:fixture:{ext}',source_record_key=ext)
    nline=0
    # Actual lineup included in a post-match/historical fixture payload is NOT assumed pre-match-known.
    # If a caller has a genuine archived pre-match observation, it must pass its real observed_at and ingest separately.
    if ingest_actual_lineup:
        lines=data.get('lineups') or []
        if isinstance(lines,dict): lines=lines.get('data',[])
        grouped={'home':[],'away':[]}; parts=_participants(data)
        home_pid=(parts.get('home') or {}).get('id'); away_pid=(parts.get('away') or {}).get('id')
        for item in lines or []:
            tid=item.get('team_id') or item.get('participant_id')
            side='home' if tid==home_pid else ('away' if tid==away_pid else _loc_side(item.get('location')))
            if side not in grouped: continue
            pl=item.get('player') or {}
            pkey=str(pl.get('id') or item.get('player_id') or '')
            name=pl.get('display_name') or pl.get('name') or item.get('player_name')
            if not name: continue
            pid=ensure_player(con,sid,pkey,name,primary_position=(item.get('position') or {}).get('name') if isinstance(item.get('position'),dict) else item.get('position'))
            grouped[side].append({'player_id':pid,'is_starting':bool(item.get('type_id') in {11,'11'} or item.get('starter',True)),
                                  'shirt_number':item.get('jersey_number'),'position':(item.get('position') or {}).get('name') if isinstance(item.get('position'),dict) else item.get('position')})
        # Post-match guard: if obs is not demonstrably before kickoff, actual XI cannot leak into pre-match backtests.
        for side,members in grouped.items():
            if not members: continue
            team_id=fx['home_team_id'] if side=='home' else fx['away_team_id']
            safe_obs=obs
            if safe_obs <= fx['kickoff_utc']:
                safe_obs=fx['kickoff_utc'][:-1]+'001Z' if fx['kickoff_utc'].endswith('Z') else fx['kickoff_utc']
            ingest_lineup_snapshot(con,fixture_id,team_id,sid,'CONFIRMED',safe_obs,members,confidence='A',
                                   source_locator=f'sportmonks:fixture:{ext}',source_record_key=f'{ext}:{side}:actual',
                                   evidence={'availability_semantics':'ACTUAL_XI_POSTMATCH_GUARD','not_valid_as_prematch_without_archived_timestamp':True})
            nline+=1
    return {'fixture_id':fixture_id,'xg_values_written':nx,'lineup_snapshots_written':nline,'source_fixture_key':ext}
