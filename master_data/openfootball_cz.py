from __future__ import annotations
import re, json, hashlib
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .identity import stable_id, normalize_name
from .ingest import utcnow, ensure_team

OPENFOOTBALL_SOURCE_ID=stable_id('source','openfootball-europe')
CZ_COMP_CODE='CZ1'

MONTHS={m:i for i,m in enumerate(['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],1)}
DATE_RE=re.compile(r'^\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$')
MATCH_RE=re.compile(r'^\s*(?:(\d{1,2}:\d{2})\s+)?(.+?)\s+v\s+(.+?)\s+(\d+)-(\d+)(?:\s+\((\d+)-(\d+)\))?\s*$')
HEADER_RE=re.compile(r'^= Czech Republic First League\s+(.+?)\s*$')
STAGE_RE=re.compile(r'^\s*▪\s*(.+?)\s*$')


def _hash_text(x:str)->str: return hashlib.sha256(x.encode('utf-8')).hexdigest()

def ensure_source_and_competition(con):
    con.execute('''INSERT OR IGNORE INTO sources(source_id,name,source_type,base_url,authority_rank,usage_notes,reliability_notes)
                   VALUES(?,?,?,?,?,?,?)''',
                (OPENFOOTBALL_SOURCE_ID,'OpenFootball Europe','OPEN_PUBLIC_DATA','https://github.com/openfootball/europe',20,
                 'CC0/public-domain match schedules and results. Suitable for research result-history expansion, not advanced stats or market truth.',
                 'Coverage is discontinuous for Czech First League; do not infer missing seasons or odds.'))
    cid=stable_id('competition',OPENFOOTBALL_SOURCE_ID,CZ_COMP_CODE)
    con.execute('''INSERT OR IGNORE INTO competitions(competition_id,source_id,source_code,name,country,tier,competition_type,domain_status,model_domain_notes)
                   VALUES(?,?,?,?,?,?,?,?,?)''',
                (cid,OPENFOOTBALL_SOURCE_ID,CZ_COMP_CODE,'Czech First League / Chance Liga','Czech Republic',1,'league','EXPERIMENTAL',
                 'Research/result-history domain. Current MASTER models are not validated for Czech league betting.'))
    con.commit(); return cid


def parse_openfootball_cz(path):
    lines=Path(path).read_text(encoding='utf-8').splitlines()
    season_label=None; start_year=None; current_year=None; current_date=None; current_time=None; stage='Regular Season'; round_name=None
    rows=[]
    for raw in lines:
        h=HEADER_RE.match(raw)
        if h:
            season_label=h.group(1).strip()
            m=re.search(r'(\d{4})/(\d{2})',season_label)
            if m: start_year=int(m.group(1)); current_year=start_year
            continue
        sm=STAGE_RE.match(raw)
        if sm:
            title=sm.group(1).strip(); round_name=title
            low=title.lower()
            if low.startswith('matchday'): stage='Regular Season'
            elif 'championship' in low: stage='Championship'
            elif 'relegation' in low: stage='Relegation'
            elif 'middle playoffs' in low: stage='Middle Playoffs'
            else: stage=title.split(',')[0]
            continue
        dm=DATE_RE.match(raw)
        if dm:
            mon=MONTHS[dm.group(2)]; day=int(dm.group(3)); yr=int(dm.group(4)) if dm.group(4) else current_year
            if dm.group(4): current_year=yr
            elif current_date is not None:
                prev_mon=current_date.month
                # Domestic season crosses New Year; month wraps Dec -> Jan.
                if mon < prev_mon-6: current_year += 1
                yr=current_year
            elif start_year is not None:
                yr=start_year
            current_date=datetime(yr,mon,day)
            current_time=None
            continue
        mm=MATCH_RE.match(raw)
        if mm and current_date is not None:
            tm=mm.group(1)
            precision='EXACT'
            if tm:
                hh,mi=map(int,tm.split(':')); current_time=(hh,mi)
            elif current_time:
                hh,mi=current_time; precision='INHERITED_SAME_BLOCK'
            else:
                hh,mi=12,0; precision='DATE_ONLY'
            dt=current_date.replace(hour=hh,minute=mi,tzinfo=ZoneInfo('Europe/Prague'))
            dt_utc=dt.astimezone(timezone.utc)
            rows.append({
                'kickoff_local':dt.isoformat(timespec='minutes'), 'kickoff_utc':dt_utc.strftime('%Y-%m-%dT%H:%M:00Z'),
                'kickoff_precision':precision, 'home_team':mm.group(2).strip(),'away_team':mm.group(3).strip(),
                'home_goals':int(mm.group(4)),'away_goals':int(mm.group(5)),
                'home_ht_goals':None if mm.group(6) is None else int(mm.group(6)),
                'away_ht_goals':None if mm.group(7) is None else int(mm.group(7)),
                'stage':stage,'round_name':round_name,'season_label':season_label,'raw_line':raw
            })
    return rows


def ingest_openfootball_cz(con,path,season_code=None):
    cid=ensure_source_and_competition(con); rows=parse_openfootball_cz(path)
    if not rows: raise ValueError('No Czech First League matches parsed')
    label=rows[0].get('season_label') or season_code or Path(path).stem
    if season_code is None:
        m=re.search(r'(\d{4})/(\d{2})',label)
        season_code=(m.group(1)[2:]+m.group(2)) if m else label.replace('/','')
    sid=stable_id('season',cid,season_code)
    con.execute('''INSERT OR IGNORE INTO seasons(season_id,competition_id,season_code,label,is_current) VALUES(?,?,?,?,0)''',(sid,cid,season_code,label))
    run_id=stable_id('ingest-run',OPENFOOTBALL_SOURCE_ID,str(Path(path).resolve()),utcnow())
    con.execute('''INSERT INTO ingest_runs(ingest_run_id,source_id,started_at,status,source_locator) VALUES(?,?,?,?,?)''',(run_id,OPENFOOTBALL_SOURCE_ID,utcnow(),'RUNNING',str(path)))
    inserted=updated=0
    for i,r in enumerate(rows):
        ht=ensure_team(con,OPENFOOTBALL_SOURCE_ID,cid,r['home_team'],'Czech Republic')
        at=ensure_team(con,OPENFOOTBALL_SOURCE_ID,cid,r['away_team'],'Czech Republic')
        skey=f"CZ1|{season_code}|{r['kickoff_utc'][:10]}|{normalize_name(r['home_team'])}|{normalize_name(r['away_team'])}"
        fid=stable_id('fixture',OPENFOOTBALL_SOURCE_ID,skey)
        rh=_hash_text(r['raw_line'])
        exists=con.execute('SELECT 1 FROM fixtures WHERE fixture_id=?',(fid,)).fetchone()
        now=utcnow()
        con.execute('''INSERT INTO fixtures(fixture_id,source_id,source_fixture_key,competition_id,season_id,kickoff_utc,home_team_id,away_team_id,status,round_name,stage,source_row_hash,first_ingested_at,last_ingested_at)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fixture_id) DO UPDATE SET kickoff_utc=excluded.kickoff_utc,round_name=excluded.round_name,stage=excluded.stage,
                       source_row_hash=excluded.source_row_hash,last_ingested_at=excluded.last_ingested_at''',
                    (fid,OPENFOOTBALL_SOURCE_ID,skey,cid,sid,r['kickoff_utc'],ht,at,'FT',r['round_name'],r['stage'],rh,now,now))
        con.execute('''INSERT INTO team_match_stats(fixture_id,home_goals,away_goals,home_ht_goals,away_ht_goals) VALUES(?,?,?,?,?)
                       ON CONFLICT(fixture_id) DO UPDATE SET home_goals=excluded.home_goals,away_goals=excluded.away_goals,home_ht_goals=excluded.home_ht_goals,away_ht_goals=excluded.away_ht_goals,
                       updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''',
                    (fid,r['home_goals'],r['away_goals'],r['home_ht_goals'],r['away_ht_goals']))
        con.execute('''INSERT OR REPLACE INTO fixture_time_metadata(fixture_id,kickoff_precision,source_timezone,notes) VALUES(?,?,?,?)''',
                    (fid,r['kickoff_precision'],'Europe/Prague','OpenFootball local schedule time converted with Europe/Prague timezone; DATE_ONLY rows use noon local and remain explicitly low precision.'))
        srid=stable_id('source-row',OPENFOOTBALL_SOURCE_ID,str(path),i,rh)
        con.execute('''INSERT OR IGNORE INTO source_rows(source_row_id,source_id,ingest_run_id,fixture_id,source_locator,row_number,row_hash,raw_json,observed_at)
                       VALUES(?,?,?,?,?,?,?,?,?)''',
                    (srid,OPENFOOTBALL_SOURCE_ID,run_id,fid,str(path),i+1,rh,json.dumps(r,ensure_ascii=False),None))
        if exists: updated+=1
        else: inserted+=1
    con.execute('''UPDATE ingest_runs SET finished_at=?,status='SUCCESS',rows_seen=?,rows_inserted=?,rows_updated=?,rows_rejected=0 WHERE ingest_run_id=?''',
                (utcnow(),len(rows),inserted,updated,run_id))
    con.commit()
    return {'source':'OpenFootball Europe','competition':'CZ1','season':season_code,'seen':len(rows),'inserted':inserted,'updated':updated,'domain_status':'EXPERIMENTAL'}


def seed_bundled_cz(con, root):
    root=Path(root); out=[]
    for p in sorted((root/'data'/'openfootball_cz').glob('*_cz1.txt')):
        # filename e.g. 2024-25_cz1 -> 2425
        m=re.match(r'(\d{4})-(\d{2})_cz1',p.stem)
        code=m.group(1)[2:]+m.group(2) if m else None
        out.append(ingest_openfootball_cz(con,p,code))
    from .domains import seed_domain_profiles
    seed_domain_profiles(con)
    return out
