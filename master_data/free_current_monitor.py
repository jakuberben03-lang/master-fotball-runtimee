from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .advanced import ensure_player, ensure_source, ingest_lineup_snapshot, link_external_fixture
from .identity import normalize_name, stable_id
from .ingest import bootstrap_reference_data, ensure_team
from .provider_fetch import utcnow
from .football_data_public import ingest_public_fixture_csv, MAIN_FIXTURES_URL, EXTRA_FIXTURES_URL


@dataclass(frozen=True)
class OpenFootballSpec:
    key: str
    competition_code: str
    competition_name: str
    country: str
    repo: str
    path: str
    timezone: str


OPENFOOTBALL_CURRENT_SPECS = (
    OpenFootballSpec('EPL','E0','Premier League','England','openfootball/england','2026-27/1-premierleague.txt','Europe/London'),
    OpenFootballSpec('BUNDESLIGA','D1','Bundesliga','Germany','openfootball/deutschland','2026-27/1-bundesliga.txt','Europe/Berlin'),
    OpenFootballSpec('LALIGA','SP1','La Liga','Spain','openfootball/espana','2026-27/1-liga.txt','Europe/Madrid'),
    OpenFootballSpec('SERIEA','I1','Serie A','Italy','openfootball/italy','2026-27/1-seriea.txt','Europe/Rome'),
)

OPENFOOTBALL_SOURCE_NAME='OpenFootball Current Schedules'
THESPORTSDB_SOURCE_NAME='TheSportsDB Free v1'

_DATE_FULL_RE=re.compile(r'^\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})\s+(\d{4})\s*$')
_DATE_SHORT_RE=re.compile(r'^\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})\s*$')
_MATCHDAY_RE=re.compile(r'^\s*[▪#]?\s*Matchday\s+(.+?)\s*$',re.I)
_FUTURE_RE=re.compile(r'^\s*(?:(\d{1,2}:\d{2})\s+)?(.+?)\s+v\s+(.+?)\s*$')
# OpenFootball result rows are commonly "home 2-1 away" after the match is played.
_RESULT_RE=re.compile(r'^\s*(?:(\d{1,2}:\d{2})\s+)?(.+?)\s+(\d+)\s*[-–]\s*(\d+)\s+(.+?)\s*$')
_MONTHS={m:i for i,m in enumerate(('Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'),1)}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def _source(con):
    return ensure_source(con,OPENFOOTBALL_SOURCE_NAME,'OPEN_PUBLIC_DATA','https://github.com/openfootball',20,
        'CC0 current fixture schedule/result files. Fetch time is recorded by MASTER. Provider-native fixture identity only; no fuzzy cross-source merge.',
        'Schedule times can be exact, inherited inside a date block, or DATE_ONLY; precision is stored explicitly.')


def _tsdb_source(con):
    return ensure_source(con,THESPORTSDB_SOURCE_NAME,'OPEN_PUBLIC_API','https://www.thesportsdb.com',35,
        'Free v1 API key 123. Used only as an enrichment source after exact date/home/away verification.',
        'Community data; fixture link is rejected unless date and declared-alias-normalized teams match uniquely.')


def _download(url: str, path: Path, *, timeout=30) -> tuple[bytes,str]:
    req=urllib.request.Request(url,headers={'User-Agent':'MASTER-Football-Free-Monitor/2.4.3'})
    with urllib.request.urlopen(req,timeout=timeout) as r:
        data=r.read()
    path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
    return data, utcnow()


def parse_openfootball_schedule(text: str, timezone_name: str) -> list[dict]:
    """Parse schedule/result text without inventing an exact time.

    Rows with no explicit time inherit the previous time only inside the same date block,
    mirroring OpenFootball schedule formatting. If no time exists, noon is a storage
    sentinel and kickoff_precision=DATE_ONLY makes it ineligible for exact timing claims.
    """
    tz=ZoneInfo(timezone_name)
    year=None; current_date=None; current_time=None; round_name=None; rows=[]
    for raw in text.splitlines():
        line=raw.rstrip()
        if not line.strip() or line.lstrip().startswith('=') or line.lstrip().startswith('#'): continue
        md=_MATCHDAY_RE.match(line)
        if md:
            round_name='Matchday '+md.group(1).strip(); continue
        d=_DATE_FULL_RE.match(line)
        if d:
            mon=_MONTHS[d.group(2)]; day=int(d.group(3)); year=int(d.group(4))
            current_date=datetime(year,mon,day); current_time=None; continue
        d=_DATE_SHORT_RE.match(line)
        if d and year is not None:
            mon=_MONTHS[d.group(2)]; day=int(d.group(3))
            # schedule spans calendar year; Jan after Aug-Dec means next year.
            y=year
            if current_date is not None and mon < current_date.month-6: y=current_date.year+1
            current_date=datetime(y,mon,day); year=y; current_time=None; continue
        if current_date is None: continue
        fm=_FUTURE_RE.match(line)
        rm=None if fm else _RESULT_RE.match(line)
        if not fm and not rm: continue
        if fm:
            t,home,away=fm.groups(); hg=ag=None; status='NS'
        else:
            t,home,hg,ag,away=rm.groups(); hg=int(hg); ag=int(ag); status='FT'
        if t:
            hh,mi=map(int,t.split(':')); current_time=(hh,mi); precision='EXACT'
        elif current_time:
            hh,mi=current_time; precision='INHERITED_SAME_BLOCK'
        else:
            hh,mi=12,0; precision='DATE_ONLY'
        local=current_date.replace(hour=hh,minute=mi,tzinfo=tz)
        utc=local.astimezone(timezone.utc)
        rows.append({'kickoff_utc':utc.strftime('%Y-%m-%dT%H:%M:00Z'),'kickoff_precision':precision,
                     'home_team':home.strip(),'away_team':away.strip(),'home_goals':hg,'away_goals':ag,
                     'status':status,'round_name':round_name,'raw_line':line})
    return rows


def _competition(con, spec: OpenFootballSpec):
    # Reuse canonical domestic competition seeded by Football-Data. This is a competition identity,
    # not a claim that Football-Data supplied this current fixture.
    row=con.execute('SELECT competition_id FROM competitions WHERE source_code=? AND name=? ORDER BY CASE WHEN source_id IS NULL THEN 1 ELSE 0 END LIMIT 1',
                    (spec.competition_code,spec.competition_name)).fetchone()
    if not row: raise KeyError(f'competition not seeded: {spec.competition_code}')
    return row['competition_id']


def ingest_openfootball_current_text(con, spec: OpenFootballSpec, text: str, *, observed_at=None, raw_locator=None, season_code='2627'):
    observed_at=observed_at or utcnow(); source_id=_source(con); rows=parse_openfootball_schedule(text,spec.timezone)
    if not rows: return {'source':OPENFOOTBALL_SOURCE_NAME,'competition':spec.key,'status':'EMPTY','seen':0,'inserted':0,'updated':0}
    cid=_competition(con,spec); season_id=stable_id('season',cid,season_code)
    con.execute('''INSERT INTO seasons(season_id,competition_id,season_code,label,is_current) VALUES(?,?,?,?,1)
                   ON CONFLICT(competition_id,season_code) DO UPDATE SET is_current=1''',(season_id,cid,season_code,'2026/27'))
    # If a legacy row exists with another stable id, use it.
    season_id=con.execute('SELECT season_id FROM seasons WHERE competition_id=? AND season_code=?',(cid,season_code)).fetchone()['season_id']
    inserted=updated=0
    for idx,r in enumerate(rows):
        ht=ensure_team(con,source_id,cid,r['home_team'],spec.country); at=ensure_team(con,source_id,cid,r['away_team'],spec.country)
        sk=f"{spec.key}|{season_code}|{r['kickoff_utc'][:10]}|{normalize_name(r['home_team'])}|{normalize_name(r['away_team'])}"
        fid=stable_id('fixture',source_id,sk); exists=con.execute('SELECT 1 FROM fixtures WHERE fixture_id=?',(fid,)).fetchone(); now=utcnow(); rh=_sha(r['raw_line'])
        con.execute('''INSERT INTO fixtures(fixture_id,source_id,source_fixture_key,competition_id,season_id,kickoff_utc,home_team_id,away_team_id,status,round_name,stage,data_freshness,source_row_hash,first_ingested_at,last_ingested_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(fixture_id) DO UPDATE SET kickoff_utc=excluded.kickoff_utc,status=excluded.status,round_name=excluded.round_name,
                         data_freshness='FRESH',source_row_hash=excluded.source_row_hash,last_ingested_at=excluded.last_ingested_at''',
                    (fid,source_id,sk,cid,season_id,r['kickoff_utc'],ht,at,r['status'],r['round_name'],'DOMESTIC_LEAGUE','FRESH',rh,now,now))
        con.execute('''INSERT INTO fixture_time_metadata(fixture_id,kickoff_precision,source_timezone,notes) VALUES(?,?,?,?)
                       ON CONFLICT(fixture_id) DO UPDATE SET kickoff_precision=excluded.kickoff_precision,source_timezone=excluded.source_timezone,notes=excluded.notes,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''',
                    (fid,r['kickoff_precision'],spec.timezone,'OpenFootball current schedule; DATE_ONLY uses noon storage sentinel and is not exact timing evidence.'))
        con.execute('''INSERT INTO team_match_stats(fixture_id,home_goals,away_goals) VALUES(?,?,?)
                       ON CONFLICT(fixture_id) DO UPDATE SET home_goals=COALESCE(excluded.home_goals,team_match_stats.home_goals),away_goals=COALESCE(excluded.away_goals,team_match_stats.away_goals),updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''',
                    (fid,r['home_goals'],r['away_goals']))
        link_external_fixture(con,source_id,sk,fid,'EXPLICIT_ID',{'source_native':True,'observed_at':observed_at,'raw_locator':raw_locator})
        if exists: updated+=1
        else: inserted+=1
    con.commit()
    return {'source':OPENFOOTBALL_SOURCE_NAME,'competition':spec.key,'status':'SUCCESS','seen':len(rows),'inserted':inserted,'updated':updated,'observed_at':observed_at}


def collect_openfootball_current(con, raw_dir: str|Path, *, specs=OPENFOOTBALL_CURRENT_SPECS):
    raw=Path(raw_dir); out=[]
    for spec in specs:
        url=f"https://raw.githubusercontent.com/{spec.repo}/master/{spec.path}"
        target=raw/'openfootball'/spec.repo.split('/')[-1]/spec.path.replace('/','__')
        try:
            data,obs=_download(url,target); text=data.decode('utf-8-sig')
            out.append(ingest_openfootball_current_text(con,spec,text,observed_at=obs,raw_locator=url))
        except Exception as e:
            out.append({'source':OPENFOOTBALL_SOURCE_NAME,'competition':spec.key,'status':'GAP_OR_FETCH_FAILED','error':type(e).__name__+': '+str(e)})
    return out


def collect_football_data_public(con, raw_dir: str|Path):
    raw=Path(raw_dir); obs=utcnow(); out=[]
    for key,url,extra in [('main',MAIN_FIXTURES_URL,False),('extra',EXTRA_FIXTURES_URL,True)]:
        p=raw/'football_data'/f'{key}.csv'
        try:
            data,_=_download(url,p); r=ingest_public_fixture_csv(con,p,observed_at=obs,extra=extra,raw_locator=url)
            out.append({'key':key,'status':'SUCCESS','bytes':len(data),**r})
        except Exception as e:
            out.append({'key':key,'status':'GAP_OR_FETCH_FAILED','error':type(e).__name__+': '+str(e)})
    return out


def _declared_club_key(name: str) -> str:
    # Deterministic declared alias normalization only. This is intentionally NOT edit-distance/fuzzy matching.
    toks=normalize_name(name).split()
    removable={'fc','afc','cf','calcio','football','club'}
    while toks and toks[-1] in removable: toks.pop()
    while toks and toks[0] in removable: toks.pop(0)
    return ' '.join(toks)


def _json_fetch(url: str, path: Path, timeout=25):
    req=urllib.request.Request(url,headers={'User-Agent':'MASTER-Football-Free-Monitor/2.4.3'})
    with urllib.request.urlopen(req,timeout=timeout) as r: data=r.read()
    path.parent.mkdir(parents=True,exist_ok=True); path.write_bytes(data)
    return json.loads(data.decode('utf-8-sig')), utcnow()


def _candidate_exact(event: dict, fx: dict) -> bool:
    if str(event.get('dateEvent') or '') != str(fx['kickoff_utc'])[:10]: return False
    if str(event.get('strSport') or '').casefold() not in {'soccer','football',''}: return False
    return (_declared_club_key(event.get('strHomeTeam') or '') == _declared_club_key(fx['home_name']) and
            _declared_club_key(event.get('strAwayTeam') or '') == _declared_club_key(fx['away_name']))


STAT_MAP={
    'shots on goal':'sot','total shots':'shots','blocked shots':'blocked_shots','fouls':'fouls','corner kicks':'corners','corners':'corners',
    'yellow cards':'yellow','red cards':'red','ball possession':'possession','offsides':'offsides','goalkeeper saves':'saves','total passes':'passes','passes accurate':'passes_accurate',
}


def _num(v):
    if v is None: return None
    s=str(v).strip().replace('%','')
    try: return float(s) if '.' in s else int(s)
    except Exception: return None


def _insert_tsdb_stats(con, source_id, fixture_id, event_id, payload, observed_at):
    fx=con.execute('SELECT home_team_id,away_team_id,status FROM fixtures WHERE fixture_id=?',(fixture_id,)).fetchone(); rows=payload.get('eventstats') or []
    home={}; away={}
    for r in rows:
        key=STAT_MAP.get(normalize_name(r.get('strStat') or ''))
        if not key: continue
        home[key]=_num(r.get('intHome')); away[key]=_num(r.get('intAway'))
    phase='POST_MATCH_FINAL' if fx['status'] in {'FT','AET','PEN'} else 'LIVE'
    n=0
    for side,tid,stats in [('HOME',fx['home_team_id'],home),('AWAY',fx['away_team_id'],away)]:
        if not stats: continue
        sid=stable_id('fixture-stat-snapshot',fixture_id,tid,source_id,observed_at,phase,event_id)
        vals={k:stats.get(k) for k in ('shots','sot','blocked_shots','fouls','corners','yellow','red','xg','possession','offsides','saves','passes','passes_accurate')}
        con.execute('''INSERT OR IGNORE INTO fixture_stat_snapshots(stat_snapshot_id,fixture_id,team_id,source_id,observed_at,phase,provider_status,shots,sot,blocked_shots,fouls,corners,yellow,red,xg,possession,offsides,saves,passes,passes_accurate,raw_json)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (sid,fixture_id,tid,source_id,observed_at,phase,None,vals['shots'],vals['sot'],vals['blocked_shots'],vals['fouls'],vals['corners'],vals['yellow'],vals['red'],vals['xg'],vals['possession'],vals['offsides'],vals['saves'],vals['passes'],vals['passes_accurate'],json.dumps(stats,ensure_ascii=False)))
        n += con.execute('SELECT changes()').fetchone()[0]
    con.commit(); return n


def _insert_tsdb_lineups(con, source_id, fixture_id, event_id, payload, observed_at):
    fx=con.execute('SELECT home_team_id,away_team_id,kickoff_utc FROM fixtures WHERE fixture_id=?',(fixture_id,)).fetchone(); lines=payload.get('lineup') or []
    by_side={'HOME':[],'AWAY':[]}
    for r in lines:
        side='HOME' if str(r.get('strHome') or '').casefold()=='yes' else 'AWAY'
        pid=ensure_player(con,source_id,str(r.get('idPlayer') or '') or None,r.get('strPlayer') or 'UNKNOWN',primary_position=r.get('strPosition'))
        by_side[side].append({'player_id':pid,'is_starting':str(r.get('strSubstitute') or '').casefold()!='yes','shirt_number':_num(r.get('intSquadNumber')),'position':r.get('strPosition')})
    # If captured before kickoff, this is genuine pre-match knowledge; after kickoff it stays CORRECTED/post-known and must not backfill pre-match.
    before=datetime.fromisoformat(observed_at.replace('Z','+00:00')) < datetime.fromisoformat(fx['kickoff_utc'].replace('Z','+00:00'))
    status='CONFIRMED' if before else 'CORRECTED'; n=0
    for side,tid in [('HOME',fx['home_team_id']),('AWAY',fx['away_team_id'])]:
        if not by_side[side]: continue
        ingest_lineup_snapshot(con,fixture_id,tid,source_id,status,observed_at,by_side[side],confidence='B',source_locator='TheSportsDB lookuplineup',source_record_key=f'{event_id}:{side}',evidence={'pre_kickoff_observation':before})
        n += 1
    con.commit(); return n


def collect_thesportsdb_enrichment(con, raw_dir: str|Path, *, now=None, max_fixtures=6, prematch_hours=6, postmatch_hours=24):
    """Enrich a small exact fixture window with free TheSportsDB data.

    It never creates a fixture link from fuzzy similarity. Search results must pass exact date + declared alias verification.
    Player shots/fouls are NOT inferred: TheSportsDB eventstats is team-level, while timeline/lineup are separate evidence.
    """
    source_id=_tsdb_source(con); raw=Path(raw_dir)/'thesportsdb'; now=now or datetime.now(timezone.utc)
    lo=(now-timedelta(hours=postmatch_hours)).isoformat().replace('+00:00','Z'); hi=(now+timedelta(hours=prematch_hours)).isoformat().replace('+00:00','Z')
    rows=con.execute('''SELECT f.fixture_id,f.kickoff_utc,f.status,ht.canonical_name home_name,at.canonical_name away_name
                        FROM fixtures f JOIN teams ht ON ht.team_id=f.home_team_id JOIN teams at ON at.team_id=f.away_team_id
                        WHERE datetime(f.kickoff_utc) BETWEEN datetime(?) AND datetime(?) ORDER BY ABS(julianday(f.kickoff_utc)-julianday(?)) LIMIT ?''',(lo,hi,now.isoformat(),max_fixtures)).fetchall()
    out=[]
    for fxr in rows:
        fx=dict(fxr); date=fx['kickoff_utc'][:10]; q=(fx['home_name']+'_vs_'+fx['away_name']).replace(' ','_')
        url='https://www.thesportsdb.com/api/v1/json/123/searchevents.php?'+urllib.parse.urlencode({'e':q,'d':date})
        try:
            search,obs=_json_fetch(url,raw/f"{date}_{stable_id('q',q)[:8]}_search.json")
            candidates=[e for e in (search.get('event') or search.get('events') or []) if _candidate_exact(e,fx)]
            if len(candidates)!=1:
                out.append({'fixture_id':fx['fixture_id'],'status':'NO_EXACT_EVENT_LINK','candidates':len(candidates)}); continue
            ev=candidates[0]; event_id=str(ev.get('idEvent')); link_external_fixture(con,source_id,event_id,fx['fixture_id'],'OFFICIAL_MAPPING',{'verification':'date+declared_alias_normalization','observed_at':obs})
            item={'fixture_id':fx['fixture_id'],'event_id':event_id,'status':'LINKED','team_stat_snapshots':0,'lineup_snapshots':0}
            # Lineups are useful before and after kickoff; stats only after kickoff to avoid needless calls.
            lp,lpobs=_json_fetch(f'https://www.thesportsdb.com/api/v1/json/123/lookuplineup.php?id={event_id}',raw/f'{event_id}_lineup.json')
            item['lineup_snapshots']=_insert_tsdb_lineups(con,source_id,fx['fixture_id'],event_id,lp,lpobs)
            if datetime.fromisoformat(fx['kickoff_utc'].replace('Z','+00:00')) <= now:
                st,stobs=_json_fetch(f'https://www.thesportsdb.com/api/v1/json/123/lookupeventstats.php?id={event_id}',raw/f'{event_id}_stats.json')
                item['team_stat_snapshots']=_insert_tsdb_stats(con,source_id,fx['fixture_id'],event_id,st,stobs)
            out.append(item)
        except Exception as e:
            out.append({'fixture_id':fx['fixture_id'],'status':'ENRICH_FAILED','error':type(e).__name__+': '+str(e)})
    return out


def free_status_report(con):
    of=_source(con); ts=_tsdb_source(con)
    scalar=lambda q,p=(): con.execute(q,p).fetchone()[0]
    fd=ensure_source(con,'Football-Data.co.uk Public','OPEN_PUBLIC_DATA','https://www.football-data.co.uk',20)
    by_comp=[dict(r) for r in con.execute('''SELECT c.name,COUNT(*) fixtures,SUM(CASE WHEN datetime(f.kickoff_utc)>=datetime('now','-1 day') THEN 1 ELSE 0 END) current_or_future
                                            FROM fixtures f JOIN competitions c ON c.competition_id=f.competition_id WHERE f.source_id=? GROUP BY c.name ORDER BY c.name''',(of,)).fetchall()]
    return {
      'monitor_mode':'FREE_MULTI_PROVIDER_V1',
      'openfootball_current_fixtures':scalar('SELECT COUNT(*) FROM fixtures WHERE source_id=?',(of,)),
      'openfootball_by_competition':by_comp,
      'football_data_fixture_observations':scalar('SELECT COUNT(*) FROM external_fixture_observations WHERE source_id=?',(fd,)),
      'football_data_odds_observations':scalar('SELECT COUNT(*) FROM external_odds_observations WHERE source_id=?',(fd,)),
      'thesportsdb_linked_fixtures':scalar('SELECT COUNT(*) FROM fixture_source_links WHERE source_id=?',(ts,)),
      'thesportsdb_team_stat_snapshots':scalar('SELECT COUNT(*) FROM fixture_stat_snapshots WHERE source_id=?',(ts,)),
      'thesportsdb_lineup_snapshots':scalar('SELECT COUNT(*) FROM lineup_snapshots WHERE source_id=?',(ts,)),
      'canonical_player_match_rows':scalar('SELECT COUNT(*) FROM player_match_stats'),
      'coverage_gaps':[
        'LIGUE_1_FULL_CURRENT_CATALOG_NOT_VERIFIED_IN_OPENFOOTBALL_2026_27',
        'CZ_FULL_CURRENT_CATALOG_NEEDS_OFFICIAL_OR_OTHER_VERIFIED_FREE_INGEST',
        'UEFA_2026_27_FULL_CURRENT_CATALOG_NOT_AVAILABLE_IN_OPENFOOTBALL_AT_LAST_VERIFICATION',
        'PLAYER_MATCH_SHOTS_SOT_FOULS_NOT_PROVIDED_BY_CURRENT_FREE_ENRICHMENT_LAYER',
      ],
      'api_football_role':'OPTIONAL_FALLBACK_ONLY',
      'model_registry_unchanged':'8 PROVISIONAL / 12 NO MODEL / 0 ACTIVE',
    }


def run_free_cycle(con, raw_dir: str|Path, *, refresh_catalog=False, max_enrich=6):
    bootstrap_reference_data(con); out={'mode':'FREE_MULTI_PROVIDER_V1','observed_at':utcnow()}
    if refresh_catalog:
        out['openfootball_catalog']=collect_openfootball_current(con,raw_dir)
    out['football_data_public']=collect_football_data_public(con,raw_dir)
    out['thesportsdb']=collect_thesportsdb_enrichment(con,raw_dir,max_fixtures=max_enrich)
    out['status_report']=free_status_report(con)
    # SUCCESS means at least one real public observation/catalog fixture exists; gaps stay explicit.
    s=out['status_report']; usable=(s['openfootball_current_fixtures']>0 or s['football_data_fixture_observations']>0)
    out['status']='SUCCESS_WITH_GAPS' if usable else 'BLOCKED_NO_CURRENT_FREE_SOURCE'
    return out
