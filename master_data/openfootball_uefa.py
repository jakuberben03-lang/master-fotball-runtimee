from __future__ import annotations
import re, json, hashlib
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from .identity import stable_id, normalize_name
from .ingest import utcnow, ensure_team
from .advanced import ensure_source
from .domains import seed_domain_profiles, assign_fixture_domain, set_knockout_context

MONTHS={m:i for i,m in enumerate(['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'],1)}
DATE_RE=re.compile(r'^\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+([A-Z][a-z]{2})\s+(\d{1,2})(?:\s+(\d{4}))?\s*$')
STAGE_RE=re.compile(r'^\s*▪\s*(.+?)\s*$')
HEADER_RE=re.compile(r'^=\s*(UEFA (?:Champions League|Europa League|Conference League)(?:\s*-\s*Quali)?[^\n]*)\s*$')
MATCH_RE=re.compile(
    r'^\s*(?:(?P<time>\d{1,2}:\d{2})\s+)?'
    r'(?P<home>.+?)\s+v\s+(?P<away>.+?)\s+'
    r'(?:(?P<pen_h>\d+)-(?P<pen_a>\d+)\s+pen\.\s+)?'
    r'(?P<final_h>\d+)-(?P<final_a>\d+)'
    r'(?P<aet>\s+a\.e\.t\.)?'
    r'(?:\s+\((?P<detail>[^)]*)\))?'
    r'(?:\s+\[(?P<note>[^\]]+)\])?\s*$'
)
NONPLAYED_RE=re.compile(r'^\s*(?:(?P<time>\d{1,2}:\d{2})\s+)?(?P<home>.+?)\s+v\s+(?P<away>.+?)\s+\[(?P<note>cancelled|awarded|abandoned|postponed|void)\]\s*$',re.I)
COUNTRY_SUFFIX_RE=re.compile(r'\s+\(([A-Z]{3})\)\s*$')
SCORE_RE=re.compile(r'^\s*(\d+)\s*-\s*(\d+)\s*$')
DECLARED_RE=re.compile(r'^#\s*Matches\s+(\d+)\s*$')

COMPETITIONS={
    'cl': {'source_name':'OpenFootball Champions League','source_code':'UCL','name':'UEFA Champions League'},
    'el': {'source_name':'OpenFootball Europa League','source_code':'UEL','name':'UEFA Europa League'},
    'conf': {'source_name':'OpenFootball Conference League','source_code':'UECL','name':'UEFA Conference League'},
}
ALIASES={'ucl':'cl','champions':'cl','champions_league':'cl','uel':'el','europa':'el','europa_league':'el','uecl':'conf','conference':'conf','conference_league':'conf'}

def _hash(x): return hashlib.sha256(x.encode('utf-8')).hexdigest()
def _strip_country(name):
    m=COUNTRY_SUFFIX_RE.search(name); return (COUNTRY_SUFFIX_RE.sub('',name).strip(),m.group(1) if m else None)

def _competition_key(x):
    x=(x or 'cl').strip().casefold(); x=ALIASES.get(x,x)
    if x not in COMPETITIONS: raise ValueError(f'UNKNOWN_UEFA_COMPETITION:{x}')
    return x

def _infer_competition_from_header(header:str|None):
    h=(header or '').casefold()
    if 'conference league' in h: return 'conf'
    if 'europa league' in h: return 'el'
    return 'cl'

def _score(text):
    if not text: return None
    m=SCORE_RE.match(text.strip())
    return (int(m.group(1)),int(m.group(2))) if m else None

def _regulation_and_ht(final_h:int, final_a:int, detail:str|None, went_aet:bool):
    """Return canonical 90-minute score and half-time score.

    OpenFootball AET notation uses e.g. `2-0 a.e.t. (1-0, 1-0)` where
    the first parenthesised score is the score after 90 minutes and the
    second is half-time. Standard matches use `(HT)` only.
    """
    parts=[p.strip() for p in (detail or '').split(',') if p.strip()]
    parsed=[_score(p) for p in parts]
    parsed=[p for p in parsed if p is not None]
    if went_aet and len(parsed)>=2:
        reg=parsed[0]; ht=parsed[-1]
    elif went_aet and len(parsed)==1:
        # Ambiguous legacy line: keep final score but do not fabricate HT.
        reg=(final_h,final_a); ht=None
    else:
        reg=(final_h,final_a); ht=parsed[-1] if parsed else None
    return reg,ht

def audit_openfootball_file(path):
    """Reconcile source-declared match count without treating non-played rows as matches."""
    lines=Path(path).read_text(encoding='utf-8').splitlines(); declared=None
    played=awarded=cancelled=other_nonplayed=0; nonplayed=[]
    for line in lines:
        dm=DECLARED_RE.match(line)
        if dm: declared=int(dm.group(1))
        mm=MATCH_RE.match(line)
        if mm:
            note=(mm.group('note') or '').casefold()
            if note=='awarded': awarded+=1; nonplayed.append({'status':'AWARDED','raw_line':line}); continue
            played+=1; continue
        nm=NONPLAYED_RE.match(line)
        if nm:
            note=nm.group('note').casefold()
            if note=='awarded': awarded+=1
            elif note=='cancelled': cancelled+=1
            else: other_nonplayed+=1
            nonplayed.append({'status':note.upper(),'raw_line':line})
    accounted=played+awarded+cancelled+other_nonplayed
    return {'declared_matches':declared,'played_matches':played,'awarded_rows':awarded,'cancelled_rows':cancelled,
            'other_nonplayed_rows':other_nonplayed,'accounted_rows':accounted,
            'declared_reconciled': declared is None or declared==accounted,'nonplayed_examples':nonplayed[:10]}

def parse_openfootball_uefa(path, *, qualifying=False, competition_key=None):
    lines=Path(path).read_text(encoding='utf-8').splitlines(); rows=[]
    header=None; start_year=None; current_year=None; current_date=None; current_time=None; stage='UNKNOWN'
    comp=_competition_key(competition_key) if competition_key else None
    for raw in lines:
        h=HEADER_RE.match(raw)
        if h:
            header=h.group(1); m=re.search(r'(\d{4})/(\d{2})',header)
            if m: start_year=int(m.group(1)); current_year=start_year
            if comp is None: comp=_infer_competition_from_header(header)
            if '- quali' in header.casefold(): qualifying=True
            continue
        sm=STAGE_RE.match(raw)
        if sm: stage=sm.group(1).strip(); continue
        dm=DATE_RE.match(raw)
        if dm:
            mon=MONTHS[dm.group(2)]; day=int(dm.group(3)); yr=int(dm.group(4)) if dm.group(4) else current_year
            if dm.group(4): current_year=yr
            elif current_date is not None and mon < current_date.month-6: current_year+=1; yr=current_year
            elif yr is None: yr=start_year
            current_date=datetime(yr,mon,day); current_time=None; continue
        mm=MATCH_RE.match(raw)
        if not mm or current_date is None: continue
        note=(mm.group('note') or '').casefold()
        # Administrative awards are not played football and must not enter performance models.
        if note in {'awarded','cancelled','abandoned','void','postponed'}: continue
        tm=mm.group('time'); precision='UNKNOWN'
        if tm:
            hh,mi=map(int,tm.split(':')); current_time=(hh,mi); precision='UNKNOWN'
        elif current_time:
            hh,mi=current_time; precision='INHERITED_SAME_BLOCK'
        else:
            hh,mi=12,0; precision='DATE_ONLY'
        # OpenFootball displays common European schedule times but does not define a timezone contract.
        # Deterministic Europe/Prague normalization is research-only and explicitly marked non-exact.
        dt=current_date.replace(hour=hh,minute=mi,tzinfo=ZoneInfo('Europe/Prague')).astimezone(timezone.utc)
        home,hcc=_strip_country(mm.group('home').strip()); away,acc=_strip_country(mm.group('away').strip())
        low=stage.casefold()
        if qualifying: domain='UEFA_QUALIFYING'
        elif low.startswith('league') or low.startswith('group') or low.startswith('gruppe'): domain='UEFA_LEAGUE_PHASE'
        else: domain='UEFA_KNOCKOUT'
        final_h,final_a=int(mm.group('final_h')),int(mm.group('final_a'))
        went_aet=bool(mm.group('aet'))
        reg,ht=_regulation_and_ht(final_h,final_a,mm.group('detail'),went_aet)
        rows.append({'kickoff_utc':dt.strftime('%Y-%m-%dT%H:%M:00Z'),'kickoff_precision':precision,'home_team':home,'away_team':away,
                     'home_country_code':hcc,'away_country_code':acc,'home_goals':reg[0],'away_goals':reg[1],
                     'home_ht_goals':None if ht is None else ht[0],'away_ht_goals':None if ht is None else ht[1],
                     'final_after_extra_time_home':final_h if went_aet else None,'final_after_extra_time_away':final_a if went_aet else None,
                     'went_extra_time':went_aet,
                     'pen_home':None if mm.group('pen_h') is None else int(mm.group('pen_h')),'pen_away':None if mm.group('pen_a') is None else int(mm.group('pen_a')),
                     'stage':stage,'domain_key':domain,'header':header,'competition_key':comp or 'cl','raw_line':raw})
    return rows

def _ensure(con, competition_key='cl', qualifying=False):
    competition_key=_competition_key(competition_key); meta=COMPETITIONS[competition_key]
    source_id=ensure_source(con,meta['source_name'],'OPEN_PUBLIC_DATA','https://github.com/openfootball/champions-league',18,
      f"CC0/public-domain {meta['name']} result history. Research domain expansion, not odds or advanced stats.",
      'Schedule timezone is not treated as exact provider-time contract; result/team identity audit required before model use.')
    code=meta['source_code']+('Q' if qualifying else '')
    name=meta['name']+(' Qualification' if qualifying else '')
    cid=stable_id('competition',source_id,code)
    con.execute('''INSERT OR IGNORE INTO competitions(competition_id,source_id,source_code,name,country,tier,competition_type,domain_status,model_domain_notes)
      VALUES(?,?,?,?,?,?,?,?,?)''',(cid,source_id,code,name,'Europe',1,'cup','EXPERIMENTAL','OpenFootball CC0 result-history expansion; separate UEFA validation mandatory.'))
    con.commit(); seed_domain_profiles(con); return source_id,cid,code

def ingest_openfootball_uefa(con,path,season_code,*,qualifying=False,competition_key='cl'):
    competition_key=_competition_key(competition_key)
    source_id,cid,code=_ensure(con,competition_key,qualifying); rows=parse_openfootball_uefa(path,qualifying=qualifying,competition_key=competition_key)
    if not rows: raise ValueError('NO_UEFA_MATCHES_PARSED')
    file_audit=audit_openfootball_file(path)
    if not file_audit['declared_reconciled']:
        raise ValueError(f"UEFA_SOURCE_COUNT_RECONCILIATION_FAIL:{Path(path).name}:{file_audit}")
    # Protect against accidental wrong-file routing.
    parsed={r['competition_key'] for r in rows}
    if parsed != {competition_key}: raise ValueError(f'UEFA_COMPETITION_FILE_MISMATCH expected={competition_key} parsed={parsed}')
    sid=stable_id('season',cid,season_code); label=f'20{season_code[:2]}/20{season_code[2:]}' if len(season_code)==4 else season_code
    con.execute('INSERT OR IGNORE INTO seasons(season_id,competition_id,season_code,label,is_current) VALUES(?,?,?,?,0)',(sid,cid,season_code,label))
    run=stable_id('ingest-run',source_id,str(Path(path).resolve()),utcnow()); con.execute('INSERT INTO ingest_runs(ingest_run_id,source_id,started_at,status,source_locator) VALUES(?,?,?,?,?)',(run,source_id,utcnow(),'RUNNING',str(path)))
    ins=upd=0
    for i,r in enumerate(rows):
        ht=ensure_team(con,source_id,cid,r['home_team'],r['home_country_code']); at=ensure_team(con,source_id,cid,r['away_team'],r['away_country_code'])
        sk=f"{code}|{season_code}|{r['kickoff_utc'][:10]}|{normalize_name(r['home_team'])}|{normalize_name(r['away_team'])}"
        fid=stable_id('fixture',source_id,sk); exists=con.execute('SELECT 1 FROM fixtures WHERE fixture_id=?',(fid,)).fetchone(); now=utcnow(); rh=_hash(r['raw_line'])
        con.execute('''INSERT INTO fixtures(fixture_id,source_id,source_fixture_key,competition_id,season_id,kickoff_utc,home_team_id,away_team_id,status,round_name,stage,source_row_hash,first_ingested_at,last_ingested_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fixture_id) DO UPDATE SET kickoff_utc=excluded.kickoff_utc,round_name=excluded.round_name,stage=excluded.stage,source_row_hash=excluded.source_row_hash,last_ingested_at=excluded.last_ingested_at''',
          (fid,source_id,sk,cid,sid,r['kickoff_utc'],ht,at,'FT',r['stage'],r['stage'],rh,now,now))
        # Canonical goals are 90-minute regulation goals. AET / shootout truth remains in source evidence.
        con.execute('''INSERT INTO team_match_stats(fixture_id,home_goals,away_goals,home_ht_goals,away_ht_goals) VALUES(?,?,?,?,?)
          ON CONFLICT(fixture_id) DO UPDATE SET home_goals=excluded.home_goals,away_goals=excluded.away_goals,
          home_ht_goals=COALESCE(excluded.home_ht_goals,team_match_stats.home_ht_goals),away_ht_goals=COALESCE(excluded.away_ht_goals,team_match_stats.away_ht_goals),
          updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''',(fid,r['home_goals'],r['away_goals'],r['home_ht_goals'],r['away_ht_goals']))
        con.execute('''INSERT OR REPLACE INTO fixture_time_metadata(fixture_id,kickoff_precision,source_timezone,notes) VALUES(?,?,?,?)''',(fid,r['kickoff_precision'],'Europe/Prague','Research normalization of OpenFootball displayed European schedule time; not an exact source timezone contract.'))
        assign_fixture_domain(con,fid,r['domain_key'],'SOURCE_NATIVE',{'competition':code,'stage':r['stage'],'qualifying_file':qualifying,'source':'OpenFootball CC0'})
        if r['domain_key'] in {'UEFA_KNOCKOUT','UEFA_QUALIFYING'}:
            set_knockout_context(con,fid,round_name=r['stage'],phase='QUALIFYING' if qualifying else 'KNOCKOUT',extra_time_possible=True,penalties_possible=True,source_id=source_id,source_record_key=sk,
                evidence={'result_history_only':True,'aggregate_before_unknown':True,'competition':code,'went_extra_time':r['went_extra_time'],
                          'final_after_extra_time':[r['final_after_extra_time_home'],r['final_after_extra_time_away']] if r['went_extra_time'] else None,
                          'penalty_shootout':[r['pen_home'],r['pen_away']] if r['pen_home'] is not None else None})
        srid=stable_id('source-row',source_id,str(path),i,rh)
        con.execute('''INSERT OR IGNORE INTO source_rows(source_row_id,source_id,ingest_run_id,fixture_id,source_locator,row_number,row_hash,raw_json,observed_at) VALUES(?,?,?,?,?,?,?,?,?)''',(srid,source_id,run,fid,str(path),i+1,rh,json.dumps(r,ensure_ascii=False),None))
        ins += 0 if exists else 1; upd += 1 if exists else 0
    con.execute("UPDATE ingest_runs SET finished_at=?,status='SUCCESS',rows_seen=?,rows_inserted=?,rows_updated=?,rows_rejected=? WHERE ingest_run_id=?",
                (utcnow(),file_audit['declared_matches'] or len(rows),ins,upd,file_audit['awarded_rows']+file_audit['cancelled_rows']+file_audit['other_nonplayed_rows'],run)); con.commit()
    return {'competition':code,'season':season_code,'qualifying':qualifying,'seen':len(rows),'seen_played':len(rows),'inserted':ins,'updated':upd,'source_license':'CC0',
            'source_file_audit':file_audit,
            'domain_counts':{k:sum(1 for r in rows if r['domain_key']==k) for k in ['UEFA_LEAGUE_PHASE','UEFA_KNOCKOUT','UEFA_QUALIFYING']},
            'ht_score_rows':sum(1 for r in rows if r['home_ht_goals'] is not None),'aet_rows':sum(1 for r in rows if r['went_extra_time']),
            'shootout_rows':sum(1 for r in rows if r['pen_home'] is not None)}


def seed_bundled_uefa(con, root):
    """Seed bundled CC0 UEFA club result-history files.

    Supported filename suffixes:
      *_cl.txt / *_clq.txt     Champions League
      *_el.txt / *_elq.txt     Europa League
      *_conf.txt / *_confq.txt Conference League
    """
    root=Path(root); out=[]; d=root/'data'/'openfootball_uefa'
    specs=[('cl','cl',False),('clq','cl',True),('el','el',False),('elq','el',True),('conf','conf',False),('confq','conf',True)]
    for suffix,comp,q in specs:
        for p in sorted(d.glob(f'*_{suffix}.txt')):
            m=re.match(r'(\d{4})-(\d{2})_'+re.escape(suffix)+r'$',p.stem)
            code=(m.group(1)[2:]+m.group(2)) if m else p.stem
            out.append(ingest_openfootball_uefa(con,p,code,qualifying=q,competition_key=comp))
    return out
