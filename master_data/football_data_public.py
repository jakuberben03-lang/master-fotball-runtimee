from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
import pandas as pd
from .advanced import ensure_source
from .identity import stable_id, normalize_name
from .market import normalize_snapshot_group
from .provider_fetch import utcnow

SOURCE_NAME='Football-Data.co.uk Public'
MAIN_FIXTURES_URL='https://www.football-data.co.uk/fixtures.csv'
EXTRA_FIXTURES_URL='https://www.football-data.co.uk/new_league_fixtures.csv'

BOOK_PREFIXES=['B365','BFD','BV','BW','PP','SKB','Max','Avg','BFE']

def source_id(con):
    return ensure_source(con,SOURCE_NAME,'OPEN_PUBLIC_DATA','https://www.football-data.co.uk',20,
        'Free historical and upcoming fixture/odds CSVs. MASTER timestamps its own fetches and does not fabricate provider observation time.',
        'Fixture-file prices are broad scanner/reference observations; exact collection time of each row is not supplied.')

def _parse_date(v):
    try: return pd.to_datetime(v,dayfirst=True,errors='raise').date().isoformat()
    except Exception: return str(v)

def _date_relation(source_date, observed_at):
    try:
        ed=pd.to_datetime(source_date,dayfirst=True,errors='raise').date()
        od=datetime.fromisoformat(str(observed_at).replace('Z','+00:00')).date()
        return 'PRE_EVENT_DATE' if ed>od else ('POST_EVENT_DATE' if ed<od else 'SAME_DATE_UNKNOWN')
    except Exception:
        return 'UNKNOWN'


def _fixture_key(row, extra=False):
    if extra:
        comp=f"{row.get('Country','')}|{row.get('League','')}"
        h=row.get('Home',''); a=row.get('Away','')
    else:
        comp=str(row.get('Div','')); h=row.get('HomeTeam',''); a=row.get('AwayTeam','')
    return stable_id('fd-public-fixture',comp,_parse_date(row.get('Date')),h,a)

def _competition_hint(row,extra=False):
    return f"{row.get('Country','')}::{row.get('League','')}" if extra else str(row.get('Div',''))

def _names(row,extra=False):
    return (str(row.get('Home','')),str(row.get('Away',''))) if extra else (str(row.get('HomeTeam','')),str(row.get('AwayTeam','')))

def _add_odds(obs, bookmaker, family,key,selection,price,line=None,raw_column=None):
    try: p=float(price)
    except Exception: return
    if not (p>1): return
    obs.append({'bookmaker':bookmaker,'market_family':family,'market_key':key,'selection_key':selection,
                'line':None if line is None or pd.isna(line) else float(line),'price':p,'raw_column':raw_column})

def _extract_main_row(row,extra=False):
    obs=[]
    if extra:
        prefixes=['PS','Max','Avg','BFE','B365']
        for pre in prefixes:
            for suff,sel in [('H','HOME'),('D','DRAW'),('A','AWAY')]: _add_odds(obs,pre,'MAIN','1X2',sel,row.get(pre+suff),raw_column=pre+suff)
        return obs
    for pre in BOOK_PREFIXES:
        for suff,sel in [('H','HOME'),('D','DRAW'),('A','AWAY')]: _add_odds(obs,pre,'MAIN','1X2',sel,row.get(pre+suff),raw_column=pre+suff)
        for suff,sel in [('>2.5','OVER'),('<2.5','UNDER')]: _add_odds(obs,pre,'GOALS','GOALS_TOTAL',sel,row.get(pre+suff),2.5,pre+suff)
        # Current/closing-labeled columns are stored as raw observations only; snapshot_type remains UNKNOWN.
        for suff,sel in [('CH','HOME'),('CD','DRAW'),('CA','AWAY')]: _add_odds(obs,pre,'MAIN','1X2',sel,row.get(pre+suff),raw_column=pre+suff)
        for suff,sel in [('C>2.5','OVER'),('C<2.5','UNDER')]: _add_odds(obs,pre,'GOALS','GOALS_TOTAL',sel,row.get(pre+suff),2.5,pre+suff)
    ah=row.get('AHh')
    for pre in ['B365','Max','Avg','BFE']:
        _add_odds(obs,pre,'MAIN','ASIAN_HANDICAP','HOME',row.get(pre+'AHH'),ah,pre+'AHH')
        _add_odds(obs,pre,'MAIN','ASIAN_HANDICAP','AWAY',row.get(pre+'AHA'),ah,pre+'AHA')
        cah=row.get('AHCh')
        _add_odds(obs,pre,'MAIN','ASIAN_HANDICAP','HOME',row.get(pre+'CAHH'),cah,pre+'CAHH')
        _add_odds(obs,pre,'MAIN','ASIAN_HANDICAP','AWAY',row.get(pre+'CAHA'),cah,pre+'CAHA')
    return obs

def ingest_public_fixture_csv(con,path:str|Path,*,observed_at=None,extra=False,raw_locator=None):
    sid=source_id(con); observed_at=observed_at or utcnow(); df=pd.read_csv(path)
    fcount=ocount=0
    for _,row in df.iterrows():
        d=row.to_dict(); h,a=_names(d,extra)
        if not h or not a or h=='nan' or a=='nan': continue
        event_date=_parse_date(d.get('Date')); event_time=None if pd.isna(d.get('Time')) else str(d.get('Time')); relation=_date_relation(d.get('Date'),observed_at)
        sk=_fixture_key(d,extra); oid=stable_id('external-fixture',sid,sk,observed_at)
        con.execute('''INSERT OR IGNORE INTO external_fixture_observations(external_fixture_observation_id,source_id,source_fixture_key,
          competition_hint,kickoff_utc,source_event_date,source_event_time,event_temporal_relation,home_name,away_name,observed_at,timing_quality,raw_locator,raw_json)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(oid,sid,sk,_competition_hint(d,extra),None,event_date,event_time,relation,h,a,observed_at,'FETCH_TIME_ONLY',raw_locator,json.dumps(d,default=str,ensure_ascii=False)))
        fcount += con.execute('SELECT changes()').fetchone()[0]
        for o in _extract_main_row(d,extra):
            lk='' if o['line'] is None else format(abs(float(o['line'])),'.6g')
            ok=stable_id('external-odds',sid,sk,o['bookmaker'],o['market_key'],o['selection_key'],lk,observed_at,o['raw_column'] or '')
            con.execute('''INSERT OR IGNORE INTO external_odds_observations(external_odds_observation_id,source_id,source_fixture_key,bookmaker,
              market_family,market_key,selection_key,line,line_key,decimal_odds,observed_at,timing_quality,event_temporal_relation,snapshot_type,raw_column,source_record_key)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(ok,sid,sk,o['bookmaker'],o['market_family'],o['market_key'],o['selection_key'],o['line'],lk,
                  o['price'],observed_at,'FETCH_TIME_ONLY',relation,'UNKNOWN',o['raw_column'],sk))
            ocount += con.execute('SELECT changes()').fetchone()[0]
    con.commit(); return {'source_id':sid,'fixture_observations':fcount,'odds_observations':ocount,'rows':len(df),'observed_at':observed_at}

def link_external_observation(con, source_fixture_key:str, fixture_id:str):
    sid=source_id(con)
    con.execute('UPDATE external_fixture_observations SET linked_fixture_id=? WHERE source_id=? AND source_fixture_key=?',(fixture_id,sid,source_fixture_key))
    con.execute('UPDATE external_odds_observations SET linked_fixture_id=? WHERE source_id=? AND source_fixture_key=?',(fixture_id,sid,source_fixture_key))
    con.commit(); return {'linked_fixture_id':fixture_id,'source_fixture_key':source_fixture_key}

def promote_linked_odds(con):
    sid=source_id(con); rows=con.execute('''SELECT * FROM external_odds_observations WHERE source_id=? AND linked_fixture_id IS NOT NULL''',(sid,)).fetchall()
    n=0; groups=set()
    for r in rows:
        raw_hash=stable_id('fd-public-row',r['external_odds_observation_id'])
        oid=stable_id('odds',r['linked_fixture_id'],sid,r['bookmaker'],r['market_key'],r['selection_key'],r['line_key'],r['observed_at'],raw_hash)
        con.execute('''INSERT OR IGNORE INTO odds_snapshots(odds_snapshot_id,fixture_id,source_id,bookmaker,market_family,market_key,selection_key,line,line_key,
          decimal_odds,observed_at,ingested_at,timestamp_quality,snapshot_type,is_sharp,is_execution,reference_confidence,raw_column,source_row_hash,snapshot_basis,
          participant_key) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (oid,r['linked_fixture_id'],sid,r['bookmaker'],r['market_family'],r['market_key'],r['selection_key'],r['line'],r['line_key'],r['decimal_odds'],
           r['observed_at'],utcnow(),'APPROXIMATE','UNKNOWN',0,0,'C',r['raw_column'],raw_hash,'MASTER_FETCH_OF_PUBLIC_FIXTURE_FILE',''))
        n += con.execute('SELECT changes()').fetchone()[0]
        groups.add((r['linked_fixture_id'],r['bookmaker'],r['market_key'],r['line_key'],r['observed_at']))
    # No-vig only when complete two/three-sided group exists. UNKNOWN snapshot is acceptable for recorder history but not ACTIVE promotion proof.
    for fixture_id,book,market,line_key,obs in groups:
        try: normalize_snapshot_group(con,fixture_id,book,market,'UNKNOWN',source_id=sid,line_key=line_key,requested_snapshot_at=None,participant_key='')
        except Exception: pass
    con.commit(); return {'promoted_rows':n,'groups_seen':len(groups)}

def snapshot_summary(con):
    sid=source_id(con)
    return dict(con.execute('''SELECT COUNT(DISTINCT source_fixture_key) fixtures, COUNT(*) odds_rows, MIN(observed_at) first_obs, MAX(observed_at) last_obs
                              FROM external_odds_observations WHERE source_id=?''',(sid,)).fetchone())
