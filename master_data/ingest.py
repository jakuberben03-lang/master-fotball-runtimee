
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import csv, hashlib, json, math, sqlite3, urllib.request, urllib.error, time
import pandas as pd
from .db import connect, init_db
from .identity import stable_id, normalize_name
from .competitions import FOOTBALL_DATA_COMPETITIONS, LEGACY_LEAGUE_TO_CODE, UEFA_PLACEHOLDERS
from .domains import seed_domain_profiles
from .domain_sources import seed_domain_source_catalog

FD_SOURCE_ID=stable_id('source','football-data.co.uk')
MODEL_SOURCE_ID=stable_id('source','master-model-v1-seed')

def utcnow(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
def sha256_bytes(b: bytes): return hashlib.sha256(b).hexdigest()
def row_hash(d: dict): return sha256_bytes(json.dumps(d,sort_keys=True,default=str,ensure_ascii=False).encode())
def num(v, integer=True):
    if v is None or (isinstance(v,float) and math.isnan(v)) or str(v).strip()=='': return None
    try: return int(float(v)) if integer else float(v)
    except: return None

def season_label(code:str):
    if len(code)==4 and code.isdigit():
        return f"20{code[:2]}/{code[2:]}" if int(code[:2])<90 else f"19{code[:2]}/{code[2:]}"
    return code

def bootstrap_reference_data(con):
    con.execute("INSERT OR IGNORE INTO sources(source_id,name,source_type,base_url,authority_rank,usage_notes,reliability_notes) VALUES(?,?,?,?,?,?,?)",
                (FD_SOURCE_ID,'Football-Data.co.uk','historical_csv','https://www.football-data.co.uk',3,
                 'Historical/current domestic results, match statistics and bookmaker odds where present.',
                 'Pinnacle columns are not treated as automatic sharp truth; source-specific caveats must be preserved.'))
    con.execute("INSERT OR IGNORE INTO sources(source_id,name,source_type,authority_rank,usage_notes) VALUES(?,?,?,?,?)",
                (MODEL_SOURCE_ID,'MASTER Model Engine v1 seed','local_seed',1,'Seed files used to reproduce the existing v1.0.1 research backend.'))
    # Historical Football-Data aggregates are consensus references, never automatic sharp truth.
    for book in ('Avg','AvgC','Max','MaxC'):
        con.execute("INSERT OR IGNORE INTO market_source_registry(bookmaker,role,reference_confidence,notes) VALUES(?,?,?,?)",
                    (book,'CONSENSUS','B' if book.startswith('Avg') else 'C','Historical aggregate column; market observation timestamp unavailable in CSV.'))

    for code,(name,country,tier,status) in FOOTBALL_DATA_COMPETITIONS.items():
        cid=stable_id('competition',FD_SOURCE_ID,code)
        con.execute("INSERT OR IGNORE INTO competitions(competition_id,source_id,source_code,name,country,tier,competition_type,domain_status,model_domain_notes) VALUES(?,?,?,?,?,?,?,?,?)",
                    (cid,FD_SOURCE_ID,code,name,country,tier,'league',status,
                     'SUPPORTED means current v1 model domain only for Big-5 top divisions; EXPERIMENTAL means data can be stored but betting model is not automatically valid.'))
    for code,name,country,ctype in UEFA_PLACEHOLDERS:
        cid=stable_id('competition','uefa-placeholder',code)
        con.execute("INSERT OR IGNORE INTO competitions(competition_id,source_id,source_code,name,country,tier,competition_type,domain_status,model_domain_notes) VALUES(?,?,?,?,?,?,?,?,?)",
                    (cid,None,code,name,country,None,ctype,'EXPERIMENTAL','Placeholder domain for future event/API ingestion; current v1 model is not validated here.'))
    con.commit()
    seed_domain_profiles(con)
    seed_domain_source_catalog(con)

def ensure_season(con, code, season_code):
    cid=stable_id('competition',FD_SOURCE_ID,code)
    sid=stable_id('season',cid,season_code)
    con.execute("INSERT OR IGNORE INTO seasons(season_id,competition_id,season_code,label,is_current) VALUES(?,?,?,?,?)",
                (sid,cid,season_code,season_label(season_code),1 if season_code=='2627' else 0))
    return cid,sid

def ensure_team(con, source_id, competition_id, name, country=None):
    norm=normalize_name(name)
    r=con.execute("SELECT team_id FROM team_aliases WHERE source_id=? AND competition_id=? AND alias_normalized=?",(source_id,competition_id,norm)).fetchone()
    if r: return r['team_id']
    # Source+country deterministic identity. Cross-source merge remains explicit via aliases, never fuzzy.
    tid=stable_id('team',country or '',norm)
    con.execute("INSERT OR IGNORE INTO teams(team_id,canonical_name,country) VALUES(?,?,?)",(tid,str(name).strip(),country))
    con.execute("INSERT OR IGNORE INTO team_aliases(source_id,competition_id,alias,alias_normalized,team_id) VALUES(?,?,?,?,?)",
                (source_id,competition_id,str(name).strip(),norm,tid))
    return tid

def ensure_referee(con, source_id, name):
    if not name or str(name).strip()=='' or str(name).lower()=='nan': return None
    norm=normalize_name(name)
    r=con.execute("SELECT referee_id FROM referee_aliases WHERE source_id=? AND alias_normalized=?",(source_id,norm)).fetchone()
    if r: return r['referee_id']
    rid=stable_id('referee',norm)
    con.execute("INSERT OR IGNORE INTO referees(referee_id,canonical_name) VALUES(?,?)",(rid,str(name).strip()))
    con.execute("INSERT OR IGNORE INTO referee_aliases(source_id,alias,alias_normalized,referee_id) VALUES(?,?,?,?)",(source_id,str(name).strip(),norm,rid))
    return rid

def parse_date(v):
    s=str(v).strip()
    if not s or s.lower()=='nan': return None
    # Existing MASTER seed uses ISO YYYY-MM-DD; Football-Data raw files use DD/MM/YYYY.
    if len(s)>=10 and s[4:5]=='-' and s[7:8]=='-':
        dt=pd.to_datetime(s,format='%Y-%m-%d',errors='coerce')
    else:
        dt=pd.to_datetime(s,dayfirst=True,errors='coerce')
    if pd.isna(dt): return None
    return dt.strftime('%Y-%m-%dT00:00:00Z')

def _insert_odds(con, fixture_id, row, source_id, source_hash, market_observed_at=None, timestamp_quality='UNKNOWN'):
    # Football-Data normalized subset. Missing columns are simply ignored.
    specs=[]
    # 1X2 bookmaker triples. Prefix C means closing variant in Football-Data naming.
    triples={
      'B365':('B365H','B365D','B365A','PRECLOSE'), 'B365C':('B365CH','B365CD','B365CA','CLOSING'),
      'BW':('BWH','BWD','BWA','PRECLOSE'), 'BWC':('BWCH','BWCD','BWCA','CLOSING'),
      'IW':('IWH','IWD','IWA','PRECLOSE'), 'IWC':('IWCH','IWCD','IWCA','CLOSING'),
      'PS':('PSH','PSD','PSA','PRECLOSE'), 'PSC':('PSCH','PSCD','PSCA','CLOSING'),
      'Avg':('AvgH','AvgD','AvgA','PRECLOSE'), 'AvgC':('AvgCH','AvgCD','AvgCA','CLOSING'),
      'Max':('MaxH','MaxD','MaxA','PRECLOSE'), 'MaxC':('MaxCH','MaxCD','MaxCA','CLOSING'),
    }
    for book,(h,d,a,stype) in triples.items():
        for sel,col in [('HOME',h),('DRAW',d),('AWAY',a)]:
            o=num(row.get(col),False)
            if o and o>1: specs.append((book,'MAIN','1X2',sel,None,o,stype,col))
    # Totals 2.5, common historical columns.
    twos={
      'B365':('B365>2.5','B365<2.5','PRECLOSE'), 'B365C':('B365C>2.5','B365C<2.5','CLOSING'),
      'P':('P>2.5','P<2.5','PRECLOSE'), 'PC':('PC>2.5','PC<2.5','CLOSING'),
      'Avg':('Avg>2.5','Avg<2.5','PRECLOSE'), 'AvgC':('AvgC>2.5','AvgC<2.5','CLOSING'),
      'Max':('Max>2.5','Max<2.5','PRECLOSE'), 'MaxC':('MaxC>2.5','MaxC<2.5','CLOSING'),
    }
    for book,(oc,uc,stype) in twos.items():
        for sel,col in [('OVER',oc),('UNDER',uc)]:
            o=num(row.get(col),False)
            if o and o>1: specs.append((book,'GOALS','OU_2.5',sel,2.5,o,stype,col))
    # Asian handicap, home line from AHh/AHCh if present.
    ah_sets={
      'B365':('AHh','B365AHH','B365AHA','PRECLOSE'), 'B365C':('AHCh','B365CAHH','B365CAHA','CLOSING'),
      'P':('AHh','PAHH','PAHA','PRECLOSE'), 'PC':('AHCh','PCAHH','PCAHA','CLOSING'),
      'Avg':('AHh','AvgAHH','AvgAHA','PRECLOSE'), 'AvgC':('AHCh','AvgCAHH','AvgCAHA','CLOSING'),
    }
    for book,(lc,hc,ac,stype) in ah_sets.items():
        line=num(row.get(lc),False)
        for sel,col in [('HOME',hc),('AWAY',ac)]:
            o=num(row.get(col),False)
            if line is not None and o and o>1: specs.append((book,'MAIN','AH',sel,line,o,stype,col))
    for book,fam,mkey,sel,line,o,stype,col in specs:
        oid=stable_id('odds',fixture_id,source_id,book,mkey,sel,line,stype,col,source_hash)
        try:
            con.execute("INSERT OR IGNORE INTO odds_snapshots(odds_snapshot_id,fixture_id,source_id,bookmaker,market_family,market_key,selection_key,line,line_key,decimal_odds,observed_at,ingested_at,timestamp_quality,snapshot_type,is_sharp,is_execution,reference_confidence,raw_column,source_row_hash) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (oid,fixture_id,source_id,book,fam,mkey,sel,line,'' if line is None else format(float(line),'.6g'),o,market_observed_at,utcnow(),timestamp_quality,stype,0,0,'B' if book.startswith('Avg') else 'C',col,source_hash))
        except sqlite3.IntegrityError:
            pass

def ingest_dataframe(con, df, league_code, season_code, source_locator, source_id=FD_SOURCE_ID):
    if league_code not in FOOTBALL_DATA_COMPETITIONS: raise ValueError(f'Unknown football-data league code {league_code}')
    name,country,tier,status=FOOTBALL_DATA_COMPETITIONS[league_code]
    cid,sid=ensure_season(con,league_code,season_code)
    run_id=stable_id('ingest-run',source_id,source_locator,utcnow())
    started=utcnow()
    con.execute("INSERT INTO ingest_runs(ingest_run_id,source_id,started_at,status,source_locator) VALUES(?,?,?,?,?)",(run_id,source_id,started,'RUNNING',source_locator))
    inserted=updated=rejected=0
    for i,ser in df.iterrows():
        row={str(k): (None if pd.isna(v) else v) for k,v in ser.items()}
        ko=parse_date(row.get('Date'))
        home=row.get('HomeTeam'); away=row.get('AwayTeam')
        if not ko or not home or not away:
            rejected+=1; continue
        ht=ensure_team(con,source_id,cid,home,country); at=ensure_team(con,source_id,cid,away,country)
        ref=ensure_referee(con,source_id,row.get('Referee'))
        skey=f"{league_code}|{season_code}|{ko[:10]}|{normalize_name(home)}|{normalize_name(away)}"
        fid=stable_id('fixture',source_id,skey)
        rh=row_hash(row); now=utcnow()
        prev=con.execute("SELECT source_row_hash FROM fixtures WHERE fixture_id=?",(fid,)).fetchone()
        vals=(fid,source_id,skey,cid,sid,ko,ht,at,ref,'FT',status if False else 'FRESH',rh,now,now)
        if prev is None:
            con.execute("INSERT INTO fixtures(fixture_id,source_id,source_fixture_key,competition_id,season_id,kickoff_utc,home_team_id,away_team_id,referee_id,status,data_freshness,source_row_hash,first_ingested_at,last_ingested_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",vals)
            inserted+=1
        elif prev['source_row_hash'] != rh:
            con.execute("UPDATE fixtures SET referee_id=?,source_row_hash=?,last_ingested_at=?,data_freshness='FRESH' WHERE fixture_id=?",(ref,rh,now,fid)); updated+=1
        stats=(num(row.get('FTHG')),num(row.get('FTAG')),num(row.get('HTHG')),num(row.get('HTAG')),
               num(row.get('HS')),num(row.get('AS')),num(row.get('HST')),num(row.get('AST')),
               num(row.get('HF')),num(row.get('AF')),num(row.get('HC')),num(row.get('AC')),
               num(row.get('HY')),num(row.get('AY')),num(row.get('HR')),num(row.get('AR')))
        con.execute("""INSERT INTO team_match_stats(fixture_id,home_goals,away_goals,home_ht_goals,away_ht_goals,home_shots,away_shots,home_sot,away_sot,home_fouls,away_fouls,home_corners,away_corners,home_yellow,away_yellow,home_red,away_red)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fixture_id) DO UPDATE SET
          home_goals=excluded.home_goals,away_goals=excluded.away_goals,home_ht_goals=excluded.home_ht_goals,away_ht_goals=excluded.away_ht_goals,
          home_shots=excluded.home_shots,away_shots=excluded.away_shots,home_sot=excluded.home_sot,away_sot=excluded.away_sot,
          home_fouls=excluded.home_fouls,away_fouls=excluded.away_fouls,home_corners=excluded.home_corners,away_corners=excluded.away_corners,
          home_yellow=excluded.home_yellow,away_yellow=excluded.away_yellow,home_red=excluded.home_red,away_red=excluded.away_red,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",(fid,)+stats)
        srid=stable_id('source-row',source_id,source_locator,i,rh)
        con.execute("INSERT OR IGNORE INTO source_rows(source_row_id,source_id,ingest_run_id,fixture_id,source_locator,row_number,row_hash,raw_json,observed_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (srid,source_id,run_id,fid,source_locator,int(i)+2,rh,json.dumps(row,ensure_ascii=False,default=str),now))
        _insert_odds(con,fid,row,source_id,rh,market_observed_at=None,timestamp_quality='UNKNOWN')
    con.execute("UPDATE ingest_runs SET finished_at=?,status='SUCCESS',rows_seen=?,rows_inserted=?,rows_updated=?,rows_rejected=? WHERE ingest_run_id=?",
                (utcnow(),len(df),inserted,updated,rejected,run_id))
    con.commit()
    return {'seen':len(df),'inserted':inserted,'updated':updated,'rejected':rejected,'run_id':run_id}

def ingest_csv(con, path, league_code, season_code):
    p=Path(path); df=pd.read_csv(p)
    return ingest_dataframe(con,df,league_code,season_code,str(p.resolve()))

def seed_from_v1(con, engine_root):
    root=Path(engine_root); bootstrap_reference_data(con)
    out=[]
    for p in sorted((root/'data').glob('model_data_*_*.csv')):
        stem=p.stem.split('_'); league=stem[2]; season=stem[3]
        code=LEGACY_LEAGUE_TO_CODE.get(league)
        if not code: continue
        out.append((p.name,ingest_csv(con,p,code,season)))
    # Enrich EPL 2025/26 with raw odds if bundled.
    op=root/'data'/'football_data_E0_2526_with_odds.csv'
    if op.exists(): out.append((op.name,ingest_csv(con,op,'E0','2526')))
    return out

def download_csv(url, target, retries=3, timeout=30):
    target=Path(target); target.parent.mkdir(parents=True,exist_ok=True)
    last=None
    for n in range(retries):
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'MASTER-Football-Data-Engine/1.3'})
            with urllib.request.urlopen(req,timeout=timeout) as r: data=r.read()
            if len(data)<20: raise RuntimeError('download too small')
            target.write_bytes(data); return {'path':str(target),'sha256':sha256_bytes(data),'bytes':len(data)}
        except Exception as e:
            last=e; time.sleep(1.5*(n+1))
    raise RuntimeError(f'download failed: {url}: {last}')

def refresh_football_data(con, season_code, codes, raw_dir, base_url):
    results=[]
    for code in codes:
        if code not in FOOTBALL_DATA_COMPETITIONS: continue
        url=base_url.format(season=season_code,code=code)
        target=Path(raw_dir)/season_code/f'{code}.csv'
        try:
            dl=download_csv(url,target)
            ing=ingest_csv(con,target,code,season_code)
            results.append({'code':code,'download':dl,'ingest':ing,'status':'SUCCESS'})
        except Exception as e:
            results.append({'code':code,'status':'FAILED','error':str(e)})
    return results
