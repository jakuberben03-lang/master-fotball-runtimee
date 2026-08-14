from __future__ import annotations
import json, os, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .advanced import (ensure_source, register_provider_capability, resolve_linked_fixture,
                       ensure_player, ingest_lineup_snapshot, ingest_availability_snapshot)
from .provider_fetch import record_provider_fetch, utcnow
from .identity import normalize_name

BASE='https://v3.football.api-sports.io'
SOURCE_NAME='API-Football'


def source_id_and_capabilities(con):
    sid=ensure_source(con,SOURCE_NAME,'COMMERCIAL_DATA_API','https://www.api-football.com',18,
        'Fixture-centred provider for competitions, lineups, fixture player statistics, injuries/suspensions and current odds. API key stays in environment.',
        'Coverage varies by league-season and must be probed from the provider coverage object. Historical backfills do not prove pre-match observation time.')
    caps={
      'fixtures':('PRODUCTION','EXACT','RESTRICTED','Several years available subject to plan/league-season coverage.'),
      'lineups':('PARTIAL','EXACT','RESTRICTED','Current collection can be timestamped at fetch time. Historical backfilled actual XI is guarded post-kickoff.'),
      'fixture_statistics':('PARTIAL','POST_MATCH','RESTRICTED','Fixture team statistics where coverage.fixtures.statistics_fixtures is true.'),
      'player_match_stats':('PARTIAL','POST_MATCH','RESTRICTED','Fixture player performance history where coverage.fixtures.players is true.'),
      'injuries':('PARTIAL','EXACT','RESTRICTED','Use coverage flag. Historical backfill has no proven pre-match publication timestamp and is guarded.'),
      'transfers':('PARTIAL','DATE_ONLY','RESTRICTED','Provider supports player transfers; exact availability depends on source coverage.'),
      'prematch_odds_recent':('PARTIAL','EXACT','RESTRICTED','Provider documentation states pre-match odds endpoint retrieval is limited to recent data; do not use as historical archive.'),
    }
    for key,(scope,timing,lic,notes) in caps.items():
        register_provider_capability(con,sid,key,scope,timing_granularity=timing,license_class=lic,notes=notes)
    return sid


def _key(explicit=None):
    k=explicit or os.getenv('API_FOOTBALL_KEY')
    if not k: raise RuntimeError('API_FOOTBALL_KEY is required; MASTER never persists provider API keys')
    return k


def _safe_params(params):
    return {k:v for k,v in (params or {}).items() if v is not None}


def _request(con, endpoint:str, params:dict|None=None, *, api_key=None, raw_dir:str|Path|None=None, timeout=45):
    sid=source_id_and_capabilities(con); params=_safe_params(params)
    url=BASE+'/'+endpoint.lstrip('/')
    if params: url += '?'+urllib.parse.urlencode(params,doseq=True)
    req=urllib.request.Request(url,headers={'x-apisports-key':_key(api_key),'User-Agent':'MASTER-Football/1.0'})
    requested=utcnow()
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            raw=r.read(); status=getattr(r,'status',200)
        doc=json.loads(raw.decode())
        raw_path=None
        if raw_dir:
            d=Path(raw_dir); d.mkdir(parents=True,exist_ok=True)
            stamp=requested.replace(':','').replace('/','-')
            raw_path=d/f"api_football_{endpoint.replace('/','_')}_{stamp}.json"; raw_path.write_bytes(raw)
        record_provider_fetch(con,sid,endpoint,params,requested_at=requested,http_status=status,response_bytes=raw,raw_path=raw_path,success=True)
        return doc, requested
    except Exception as e:
        record_provider_fetch(con,sid,endpoint,params,requested_at=requested,success=False,notes=type(e).__name__+': '+str(e)); raise


def fetch_leagues(con, *, country=None, name=None, league_id=None, season=None, current=None, api_key=None, raw_dir=None):
    params={'country':country,'name':name,'id':league_id,'season':season,'current':'true' if current else None}
    return _request(con,'leagues',params,api_key=api_key,raw_dir=raw_dir)[0]


def coverage_catalog(doc:dict):
    """Flatten league-season coverage. Do not assume one season's flags apply to another."""
    out=[]
    for item in doc.get('response',[]):
        lg=item.get('league') or {}; country=(item.get('country') or {}).get('name')
        for s in item.get('seasons') or []:
            cov=s.get('coverage') or {}; fix=cov.get('fixtures') or {}
            out.append({'league_id':lg.get('id'),'league_name':lg.get('name'),'country':country,'season':s.get('year'),
                        'current':bool(s.get('current')),'events':bool(fix.get('events')),'lineups':bool(fix.get('lineups')),
                        'fixture_statistics':bool(fix.get('statistics_fixtures')),'player_statistics':bool(fix.get('statistics_players')),
                        'injuries':bool(cov.get('injuries')),'odds':bool(cov.get('odds')),'players':bool(cov.get('players'))})
    return out


def target_discovery_queries():
    return [
      {'domain':'CZ_FIRST_LEAGUE','params':{'country':'Czech-Republic','name':'Czech Liga'}},
      {'domain':'UEFA_CHAMPIONS','params':{'name':'UEFA Champions League'}},
      {'domain':'UEFA_EUROPA','params':{'name':'UEFA Europa League'}},
      {'domain':'UEFA_CONFERENCE','params':{'name':'UEFA Europa Conference League'}},
    ]


def fetch_fixtures(con, league_id:int, season:int, *, api_key=None, raw_dir=None):
    return _request(con,'fixtures',{'league':league_id,'season':season},api_key=api_key,raw_dir=raw_dir)[0]


def fetch_fixture_bundle(con, provider_fixture_id:int|str, *, api_key=None, raw_dir=None, include_injuries=True):
    """Fetch fixture, actual lineup, player-match stats and associated injuries.
    Historical actual lineups/injuries are NOT retroactively treated as known pre-match.
    """
    out={}
    for key,endpoint,params in [
        ('fixture','fixtures',{'id':provider_fixture_id}),
        ('lineups','fixtures/lineups',{'fixture':provider_fixture_id}),
        ('players','fixtures/players',{'fixture':provider_fixture_id}),
    ]:
        try: out[key],out[key+'_fetched_at']=_request(con,endpoint,params,api_key=api_key,raw_dir=raw_dir)
        except Exception as e: out[key]={'response':[],'errors':{'master':str(e)}}; out[key+'_fetched_at']=utcnow()
    if include_injuries:
        try: out['injuries'],out['injuries_fetched_at']=_request(con,'injuries',{'fixture':provider_fixture_id},api_key=api_key,raw_dir=raw_dir)
        except Exception as e: out['injuries']={'response':[],'errors':{'master':str(e)}}; out['injuries_fetched_at']=utcnow()
    return out


def _guard_observed_at(kickoff:str, fetched_at:str, *, historical_backfill:bool):
    if not historical_backfill: return fetched_at
    k=datetime.fromisoformat(kickoff.replace('Z','+00:00'))
    return (k+timedelta(seconds=1)).astimezone(timezone.utc).isoformat().replace('+00:00','Z')


def _canonical_side_map(con, fixture_id:str, provider_teams:list[dict]):
    fx=con.execute('SELECT home_team_id,away_team_id FROM fixtures WHERE fixture_id=?',(fixture_id,)).fetchone()
    canon={fx['home_team_id']:con.execute('SELECT canonical_name FROM teams WHERE team_id=?',(fx['home_team_id'],)).fetchone()[0],
           fx['away_team_id']:con.execute('SELECT canonical_name FROM teams WHERE team_id=?',(fx['away_team_id'],)).fetchone()[0]}
    out={}
    for t in provider_teams:
        pid=t.get('id'); name=t.get('name')
        if pid is None or not name: continue
        matches=[tid for tid,n in canon.items() if normalize_name(n)==normalize_name(name)]
        if len(matches)==1: out[str(pid)]=matches[0]
    if len(set(out.values()))<2: raise ValueError('API_FOOTBALL_TEAM_IDENTITY_CHECK_FAILED: fixture must have exact normalized home/away names or explicit future team mapping')
    return out


def _fixture_doc(bundle):
    rows=(bundle.get('fixture') or {}).get('response') or []
    return rows[0] if rows else None


def _injury_status(reason):
    s=(reason or '').casefold()
    if 'suspend' in s: return 'SUSPENDED'
    if 'ill' in s or 'sick' in s: return 'ILL'
    if 'doubt' in s or 'question' in s: return 'DOUBTFUL'
    if 'injur' in s or 'knock' in s or 'muscle' in s or 'knee' in s or 'ankle' in s: return 'INJURED'
    return 'UNKNOWN'


def ingest_fixture_bundle(con, provider_fixture_id:int|str, bundle:dict, *, historical_backfill=True):
    sid=source_id_and_capabilities(con); fixture_id=resolve_linked_fixture(con,sid,str(provider_fixture_id))
    fx=con.execute('SELECT kickoff_utc,home_team_id,away_team_id FROM fixtures WHERE fixture_id=?',(fixture_id,)).fetchone()
    fdoc=_fixture_doc(bundle)
    provider_teams=[]
    if fdoc:
        for k in ('home','away'):
            t=(fdoc.get('teams') or {}).get(k) or {}; provider_teams.append(t)
    for block in ((bundle.get('lineups') or {}).get('response') or []): provider_teams.append(block.get('team') or {})
    teammap=_canonical_side_map(con,fixture_id,provider_teams)
    counts={'lineup_snapshots':0,'player_match_stats':0,'availability_snapshots':0}

    lineup_fetch=bundle.get('lineups_fetched_at') or utcnow(); lineup_obs=_guard_observed_at(fx['kickoff_utc'],lineup_fetch,historical_backfill=historical_backfill)
    for block in (bundle.get('lineups') or {}).get('response') or []:
        pt=block.get('team') or {}; ctid=teammap.get(str(pt.get('id')))
        if not ctid: continue
        members=[]
        for grp,starter in [('startXI',True),('substitutes',False)]:
            for item in block.get(grp) or []:
                p=item.get('player') or {}; name=p.get('name') or str(p.get('id'))
                pid=ensure_player(con,sid,str(p.get('id') or ''),name,primary_position=p.get('pos'))
                members.append({'player_id':pid,'is_starting':starter,'shirt_number':p.get('number'),'position':p.get('pos'),'role':grp})
        ingest_lineup_snapshot(con,fixture_id,ctid,sid,'CONFIRMED',lineup_obs,members,block.get('formation'),'B',
                               f'api-football://fixtures/lineups?fixture={provider_fixture_id}',f'{provider_fixture_id}:{pt.get("id")}:lineup',
                               {'historical_backfill':historical_backfill,'timestamp_semantics':'POST_KICKOFF_GUARD' if historical_backfill else 'FETCH_TIME'})
        counts['lineup_snapshots']+=1

    # Player-match stats are post-match features for future fixtures; no pre-match timestamp claim is made.
    for teamblock in (bundle.get('players') or {}).get('response') or []:
        pt=teamblock.get('team') or {}; ctid=teammap.get(str(pt.get('id')))
        if not ctid: continue
        for pr in teamblock.get('players') or []:
            p=pr.get('player') or {}; pid=ensure_player(con,sid,str(p.get('id') or ''),p.get('name') or str(p.get('id')))
            stats=(pr.get('statistics') or [{}])[0] or {}; games=stats.get('games') or {}; shots=stats.get('shots') or {}; goals=stats.get('goals') or {}
            fouls=stats.get('fouls') or {}; cards=stats.get('cards') or {}; drib=stats.get('dribbles') or {}
            con.execute('''INSERT INTO player_match_stats(fixture_id,team_id,player_id,minutes,started,position,role,shots,sot,goals,assists,
                           fouls_committed,fouls_drawn,yellow,red,dribbles_attempted,dribbles_success)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fixture_id,player_id) DO UPDATE SET
                           team_id=excluded.team_id,minutes=excluded.minutes,started=excluded.started,position=excluded.position,
                           shots=excluded.shots,sot=excluded.sot,goals=excluded.goals,assists=excluded.assists,
                           fouls_committed=excluded.fouls_committed,fouls_drawn=excluded.fouls_drawn,yellow=excluded.yellow,red=excluded.red,
                           dribbles_attempted=excluded.dribbles_attempted,dribbles_success=excluded.dribbles_success''',
                        (fixture_id,ctid,pid,games.get('minutes'),1 if games.get('substitute') is False else 0,games.get('position'),None,
                         shots.get('total'),shots.get('on'),goals.get('total'),goals.get('assists'),fouls.get('committed'),fouls.get('drawn'),
                         cards.get('yellow'),cards.get('red'),drib.get('attempts'),drib.get('success')))
            counts['player_match_stats']+=1

    injury_fetch=bundle.get('injuries_fetched_at') or utcnow(); injury_obs=_guard_observed_at(fx['kickoff_utc'],injury_fetch,historical_backfill=historical_backfill)
    for item in (bundle.get('injuries') or {}).get('response') or []:
        p=item.get('player') or {}; team=item.get('team') or {}; ctid=teammap.get(str(team.get('id')))
        if not ctid: continue
        pid=ensure_player(con,sid,str(p.get('id') or ''),p.get('name') or str(p.get('id')))
        reason=p.get('reason') or item.get('reason') or p.get('type') or item.get('type')
        ingest_availability_snapshot(con,pid,sid,_injury_status(reason),injury_obs,ctid,fixture_id,reason=reason,confidence='B',
                                     source_locator=f'api-football://injuries?fixture={provider_fixture_id}',source_record_key=f'{provider_fixture_id}:{p.get("id")}:injury',
                                     evidence={'historical_backfill':historical_backfill,'timestamp_semantics':'POST_KICKOFF_GUARD' if historical_backfill else 'FETCH_TIME'})
        counts['availability_snapshots']+=1
    con.commit(); return {'fixture_id':fixture_id,**counts,'historical_backfill':historical_backfill}


def backfill_request_plan(coverage_rows:list[dict], fixtures_per_season:dict[tuple[int,int],int]|None=None):
    """Estimate API request volume before spending quota. One fixture bundle ~= 3-4 calls."""
    fixtures_per_season=fixtures_per_season or {}; rows=[]; total=0
    for r in coverage_rows:
        n=int(fixtures_per_season.get((int(r['league_id']),int(r['season'])),0))
        # One season fixture-list call + one fixture-detail call per match in the current bundle contract.
        # Lineups / player statistics / injuries each add at most one call per fixture when coverage allows it.
        per=1+n
        if r.get('lineups'): per += n
        if r.get('player_statistics'): per += n
        if r.get('injuries'): per += n
        total += per
        rows.append({**r,'estimated_fixture_count':n,'estimated_requests':per})
    return {'rows':rows,'estimated_total_requests':total,
            'note':'Conservative operational estimate for the current bundle contract (season fixture list + fixture detail + covered sub-endpoints). Actual pagination/batching/provider behavior can change.'}


def fixture_rows_for_linking(doc:dict):
    rows=[]
    for x in doc.get('response',[]):
        f=x.get('fixture') or {}; teams=x.get('teams') or {}
        h=teams.get('home') or {}; a=teams.get('away') or {}
        if f.get('id') is None or not f.get('date') or not h.get('name') or not a.get('name'): continue
        rows.append({'source_fixture_key':str(f['id']),'kickoff_utc':f['date'],'home_team':h['name'],'away_team':a['name']})
    return rows

# ---------------------------------------------------------------------------
# Current fixture catalog support for MASTER Stats Monitor v1.0
# ---------------------------------------------------------------------------
def _catalog_existing_competition(con, league_name:str, country_name:str|None=None):
    """Use only deterministic exact/declared-alias competition matching.
    If an old zero-fixture placeholder and a populated canonical competition share the same name,
    prefer the populated one. No fuzzy similarity is used.
    """
    from .identity import normalize_name
    aliases={
      'czech liga':'czech first league chance liga',
      'uefa europa conference league':'uefa conference league',
    }
    wanted=aliases.get(normalize_name(league_name),normalize_name(league_name))
    rows=con.execute('''SELECT c.competition_id,c.name,c.country,COUNT(f.fixture_id) fixture_count
                        FROM competitions c LEFT JOIN fixtures f ON f.competition_id=c.competition_id
                        GROUP BY c.competition_id''').fetchall()
    exact=[r for r in rows if normalize_name(r['name'])==wanted]
    if len(exact)==1: return exact[0]['competition_id']
    if country_name:
        narrowed=[r for r in exact if normalize_name(r['country'] or '')==normalize_name(country_name)]
        if len(narrowed)==1: return narrowed[0]['competition_id']
        if narrowed: exact=narrowed
    populated=[r for r in exact if int(r['fixture_count'] or 0)>0]
    if len(populated)==1: return populated[0]['competition_id']
    return None


def _provider_season_code(con, competition_id:str, provider_year:int|str):
    """Prefer MASTER's European-style YYZZ season code when the competition already uses it.
    Otherwise keep the provider year literally. This avoids inventing calendar semantics for unknown leagues.
    """
    import re
    y=int(provider_year)
    rows=[str(r['season_code']) for r in con.execute('SELECT season_code FROM seasons WHERE competition_id=?',(competition_id,)).fetchall()]
    if any(re.fullmatch(r'\d{4}',x or '') and int(x[2:])==(int(x[:2])+1)%100 for x in rows):
        return f'{y%100:02d}{(y+1)%100:02d}'
    return str(y)


def _ensure_catalog_competition(con, sid:str, league:dict):
    from .identity import stable_id
    lname=str(league.get('name') or f"API league {league.get('id')}")
    country=str(league.get('country') or '')
    cid=_catalog_existing_competition(con,lname,country)
    if cid: return cid,'EXACT_CANONICAL_NAME'
    cid=stable_id('competition',sid,str(league.get('id')))
    con.execute('''INSERT OR IGNORE INTO competitions(competition_id,source_id,source_code,name,country,tier,competition_type,domain_status,model_domain_notes)
                   VALUES(?,?,?,?,?,?,?,?,?)''',(cid,sid,str(league.get('id')),lname,country or None,None,'league','EXPERIMENTAL',
                   'Provider-native current fixture catalog. No cross-source model/domain promotion is implied.'))
    return cid,'PROVIDER_NATIVE_EXPERIMENTAL'


def _ensure_catalog_team(con, sid:str, competition_id:str, name:str, country:str|None=None):
    """Reuse only a globally unambiguous exact-normalized team name; otherwise create a source alias/team."""
    from .ingest import ensure_team
    from .identity import normalize_name
    rows=con.execute('SELECT team_id,canonical_name FROM teams').fetchall()
    exact=[r for r in rows if normalize_name(r['canonical_name'])==normalize_name(name)]
    if len(exact)==1:
        tid=exact[0]['team_id']
        con.execute("INSERT OR IGNORE INTO team_aliases(source_id,competition_id,alias,alias_normalized,team_id) VALUES(?,?,?,?,?)",
                    (sid,competition_id,name,normalize_name(name),tid))
        return tid,'EXACT_GLOBAL_NAME'
    safe_country=None if normalize_name(country or '') in {'world','europe'} else country
    return ensure_team(con,sid,competition_id,name,safe_country),'PROVIDER_NATIVE'


def ingest_fixture_catalog(con, doc:dict):
    """Ingest API-Football fixture list as current canonical fixtures.

    Provider IDs are stored as explicit fixture links. Existing competitions are reused only on an
    unambiguous exact-normalized name match; otherwise a provider-native EXPERIMENTAL competition is created.
    No fuzzy team/fixture merge is performed.
    """
    import hashlib
    from .ingest import ensure_team, ensure_referee
    from .identity import stable_id
    sid=source_id_and_capabilities(con); counts={'seen':0,'inserted':0,'updated':0,'links':0,'provider_native_competitions':0}
    comp_cache={}; season_cache={}
    for x in doc.get('response') or []:
        counts['seen']+=1
        f=x.get('fixture') or {}; league=x.get('league') or {}; teams=x.get('teams') or {}
        if f.get('id') is None or not f.get('date') or not (teams.get('home') or {}).get('name') or not (teams.get('away') or {}).get('name'):
            continue
        lkey=str(league.get('id') or normalize_name(league.get('name') or 'unknown'))
        if lkey not in comp_cache:
            comp_cache[lkey]=_ensure_catalog_competition(con,sid,league)
            if comp_cache[lkey][1]=='PROVIDER_NATIVE_EXPERIMENTAL': counts['provider_native_competitions']+=1
        cid,map_method=comp_cache[lkey]
        year=int(league.get('season') or datetime.fromisoformat(str(f['date']).replace('Z','+00:00')).year)
        skey=(cid,year)
        if skey not in season_cache:
            scode=_provider_season_code(con,cid,year); season_id=stable_id('season',cid,scode)
            con.execute('''INSERT OR IGNORE INTO seasons(season_id,competition_id,season_code,label,is_current) VALUES(?,?,?,?,1)''',
                        (season_id,cid,scode,str(year)))
            season_cache[skey]=season_id
        season_id=season_cache[skey]
        country=league.get('country')
        h=teams.get('home') or {}; a=teams.get('away') or {}
        ht,_hm=_ensure_catalog_team(con,sid,cid,h['name'],country); at,_am=_ensure_catalog_team(con,sid,cid,a['name'],country)
        ref=ensure_referee(con,sid,f.get('referee'))
        provider_key=str(f['id']); fid=stable_id('fixture',sid,provider_key)
        raw=json.dumps(x,sort_keys=True,ensure_ascii=False,default=str); rh=hashlib.sha256(raw.encode()).hexdigest(); now=utcnow()
        status=str(((f.get('status') or {}).get('short') or 'NS'))
        venue=((f.get('venue') or {}).get('name'))
        prev=con.execute('SELECT source_row_hash FROM fixtures WHERE fixture_id=?',(fid,)).fetchone()
        if prev is None:
            con.execute('''INSERT INTO fixtures(fixture_id,source_id,source_fixture_key,competition_id,season_id,kickoff_utc,home_team_id,away_team_id,
                           referee_id,status,venue,data_freshness,source_row_hash,first_ingested_at,last_ingested_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (fid,sid,provider_key,cid,season_id,f['date'],ht,at,ref,status,venue,'FRESH',rh,now,now)); counts['inserted']+=1
        else:
            con.execute('''UPDATE fixtures SET competition_id=?,season_id=?,kickoff_utc=?,home_team_id=?,away_team_id=?,referee_id=?,status=?,venue=?,
                           data_freshness='FRESH',source_row_hash=?,last_ingested_at=? WHERE fixture_id=?''',
                        (cid,season_id,f['date'],ht,at,ref,status,venue,rh,now,fid)); counts['updated']+=1
        con.execute('''INSERT INTO fixture_source_links(source_id,source_fixture_key,fixture_id,link_method,evidence_json)
                       VALUES(?,?,?,?,?) ON CONFLICT(source_id,source_fixture_key) DO UPDATE SET fixture_id=excluded.fixture_id,
                       link_method=excluded.link_method,evidence_json=excluded.evidence_json,linked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''',
                    (sid,provider_key,fid,'EXPLICIT_ID',json.dumps({'provider_native_fixture':True,'competition_mapping':map_method},ensure_ascii=False)))
        counts['links']+=1
    con.commit(); return counts


def refresh_fixture_catalog(con, league_id:int, season:int, *, api_key=None, raw_dir=None):
    doc=fetch_fixtures(con,league_id,season,api_key=api_key,raw_dir=raw_dir)
    out=ingest_fixture_catalog(con,doc); out.update({'league_id':int(league_id),'season':int(season)}); return out


def bootstrap_default_fixture_catalog(con, *, api_key=None, raw_dir=None, include_uefa=True):
    """Discover current API-Football IDs in one /leagues?current=true call, then ingest current fixture catalogs.

    The default target set is Big-5 + Czech top flight + optional UEFA club competitions. Exact normalized
    provider names are required; missing/ambiguous targets are reported rather than guessed.
    """
    from .identity import normalize_name
    targets=[
      {'name':'Premier League','country':'England','aliases':[]},
      {'name':'La Liga','country':'Spain','aliases':[]},
      {'name':'Serie A','country':'Italy','aliases':[]},
      {'name':'Bundesliga','country':'Germany','aliases':[]},
      {'name':'Ligue 1','country':'France','aliases':[]},
      {'name':'Czech Liga','country':'Czech Republic','aliases':['Czech Liga']},
    ]
    if include_uefa:
        targets += [
          {'name':'UEFA Champions League','country':None,'aliases':['UEFA Champions League']},
          {'name':'UEFA Europa League','country':None,'aliases':['UEFA Europa League']},
          {'name':'UEFA Conference League','country':None,'aliases':['UEFA Europa Conference League','UEFA Conference League']},
        ]
    doc,_=_request(con,'leagues',{'current':'true'},api_key=api_key,raw_dir=raw_dir)
    rows=doc.get('response') or []; selected=[]; missing=[]
    for t in targets:
        names={normalize_name(t['name'])}|{normalize_name(a) for a in t.get('aliases',[])}
        cands=[]
        for r in rows:
            lg=r.get('league') or {}; country=(r.get('country') or {}).get('name')
            if normalize_name(lg.get('name') or '') not in names: continue
            if t.get('country') and normalize_name(country or '') != normalize_name(t['country']): continue
            current=[s for s in (r.get('seasons') or []) if s.get('current')]
            if len(current)==1: cands.append((lg,current[0],country))
        if len(cands)!=1:
            missing.append({'target':t['name'],'matches':len(cands)}); continue
        lg,season,country=cands[0]; selected.append({'target':t['name'],'league_id':int(lg['id']),'season':int(season['year']),'provider_name':lg.get('name'),'country':country})
    results=[]
    for s in selected:
        results.append({**s,**refresh_fixture_catalog(con,s['league_id'],s['season'],api_key=api_key,raw_dir=raw_dir)})
    return {'selected':selected,'missing_or_ambiguous':missing,'catalogs':results,'requests_estimate':1+len(selected)}
