from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict
from .advanced import ensure_source

SOURCE_NAME='Wyscout Open Event Dataset'
LICENSE='CC BY 4.0'
RIGHTS='VERIFIED_OPEN_WITH_ATTRIBUTION'
DOWNLOADS={
 'players':'https://ndownloader.figshare.com/files/15073721',
 'teams':'https://ndownloader.figshare.com/files/15073697',
 'matches':'https://ndownloader.figshare.com/files/14464622',
 'events':'https://ndownloader.figshare.com/files/14464685',
}
GITHUB_MIRROR_DOWNLOADS={
 'players':'https://github.com/koenvo/wyscout-soccer-match-event-dataset/raw/refs/heads/main/raw_data/players.json',
 'teams':'https://github.com/koenvo/wyscout-soccer-match-event-dataset/raw/refs/heads/main/raw_data/teams.json',
 'matches':'https://github.com/koenvo/wyscout-soccer-match-event-dataset/raw/refs/heads/main/raw_data/matches.zip',
 'events':'https://github.com/koenvo/wyscout-soccer-match-event-dataset/raw/refs/heads/main/raw_data/events.zip',
}
COMPETITIONS=('England','France','Germany','Italy','Spain')
EXPECTED_MATCHES={'England':380,'France':380,'Germany':306,'Italy':380,'Spain':380}
EXPECTED_MATCHES_TOTAL=sum(EXPECTED_MATCHES.values())
TAG_GOAL=101; TAG_OWN_GOAL=102; TAG_ASSIST=301; TAG_BLOCKED=2101; TAG_RED=1701; TAG_YELLOW=1702; TAG_SECOND_YELLOW=1703; TAG_ACCURATE=1801

def source_id(con):
    return ensure_source(con,SOURCE_NAME,'OPEN_RESEARCH_DATA','https://figshare.com/collections/Soccer_match_event_dataset/4415000',30,
        'CC BY 4.0 Wyscout-derived public event dataset. Attribution to Pappalardo et al. / source required.',
        '2017/18 Big-5 only for domestic club leagues. Actual XI/substitutions are post-match truth, not archived pre-match timestamps.')

def dataset_manifest():
    return {'license':LICENSE,'rights_status':RIGHTS,'competitions':list(COMPETITIONS),
            'expected_matches_by_competition':EXPECTED_MATCHES,'season':'2017/18','downloads':DOWNLOADS,'expected_matches':EXPECTED_MATCHES_TOTAL,
            'prematch_lineup_permission':'POST_MATCH_ONLY','player_history_permission':'RESEARCH_ALLOWED_WITH_ATTRIBUTION',
            'fouls_drawn_support':'NOT_DIRECTLY_IDENTIFIED_BY_CURRENT_EVENT_ADAPTER','github_raw_mirror':GITHUB_MIRROR_DOWNLOADS}

def _load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def _find_comp_file(root:Path,prefix:str,competition:str):
    candidates=[root/f'{prefix}_{competition}.json',root/prefix/f'{prefix}_{competition}.json']
    for p in candidates:
        if p.exists(): return p
    gl=list(root.rglob(f'{prefix}_{competition}.json'))
    if gl: return gl[0]
    raise FileNotFoundError(f'{prefix}_{competition}.json not found under {root}')

def _player_map(players):
    out={}
    for p in players:
        name=p.get('shortName2') or p.get('shortName') or ' '.join(x for x in [p.get('firstName'),p.get('lastName')] if x)
        role=(p.get('role') or {}).get('name') if isinstance(p.get('role'),dict) else p.get('role')
        out[str(p.get('wyId'))]={'name':name,'role':role}
    return out

def _team_map(teams): return {str(t.get('wyId')):t.get('name') or t.get('officialName') for t in teams}

def _tags(ev): return {int(x.get('id')) for x in (ev.get('tags') or []) if x.get('id') is not None}

def _aggregate_events(events):
    agg=defaultdict(lambda: {'shots':0,'sot':0,'goals':0,'fouls':0,'yellow':0,'red':0,'dribbles':0,'dribbles_success':0,'crosses':0,'event_rows':0})
    for ev in events:
        pid=str(ev.get('playerId') or '')
        if not pid or pid=='0': continue
        a=agg[(str(ev.get('matchId')),pid)]; a['event_rows']+=1; tags=_tags(ev); en=str(ev.get('eventName') or '').casefold(); sub=str(ev.get('subEventName') or '').casefold()
        if en=='shot':
            a['shots']+=1; a['goals'] += int(TAG_GOAL in tags)
            # Wyscout public tags support an SOT research proxy. The adapter stores its definition explicitly.
            if TAG_GOAL in tags or (TAG_ACCURATE in tags and TAG_BLOCKED not in tags): a['sot']+=1
        if en=='foul':
            a['fouls']+=1; a['yellow'] += int(TAG_YELLOW in tags or TAG_SECOND_YELLOW in tags); a['red'] += int(TAG_RED in tags)
        if 'attacking duel' in sub:
            a['dribbles']+=1; a['dribbles_success'] += int(TAG_ACCURATE in tags)
        if sub=='cross' or 'cross' in sub: a['crosses']+=1
    return agg

def _minutes_and_lineup(match):
    out={}
    for tid,td in (match.get('teamsData') or {}).items():
        formation=td.get('formation') or {}; subs=formation.get('substitutions') or []
        out_min={str(s.get('playerOut')):float(s.get('minute') or 90) for s in subs if s.get('playerOut')}
        in_min={str(s.get('playerIn')):float(s.get('minute') or 0) for s in subs if s.get('playerIn')}
        for p in formation.get('lineup') or []:
            pid=str(p.get('playerId')); mins=min(90.0,out_min.get(pid,90.0)); red=p.get('redCards')
            if isinstance(red,list) and red: mins=min(mins,float(red[0] or mins))
            elif isinstance(red,(int,float)) and red>0: mins=min(mins,float(red))
            out[(str(match.get('wyId')),pid)]={'team_id':str(tid),'started':1,'minutes':max(0.0,mins),'assists':int(p.get('assists') or 0)}
        for p in formation.get('bench') or []:
            pid=str(p.get('playerId')); start=in_min.get(pid)
            if start is None: mins=0.0
            else: mins=max(0.0,90.0-start)
            red=p.get('redCards')
            if start is not None:
                if isinstance(red,list) and red: mins=max(0.0,min(mins,float(red[0])-start))
                elif isinstance(red,(int,float)) and red>0: mins=max(0.0,min(mins,float(red)-start))
            out[(str(match.get('wyId')),pid)]={'team_id':str(tid),'started':0,'minutes':mins,'assists':int(p.get('assists') or 0)}
    return out

def validate_dataset(root:str|Path, *, strict_counts=True):
    """Validate the local Wyscout Open download before any research ingest.

    This intentionally verifies competition match counts and that event match IDs do not
    point outside the match file. It does not claim pre-match lineup timing: lineups remain
    post-match truth for research/minutes reconstruction only.
    """
    root=Path(root); issues=[]; comps={}
    for global_name in ('players.json','teams.json'):
        if not (root/global_name).exists(): issues.append(f'MISSING:{global_name}')
    if issues:
        return {'ok':False,'issues':issues,'competitions':comps,'expected_total_matches':EXPECTED_MATCHES_TOTAL,'actual_total_matches':0}
    total=0
    for comp in COMPETITIONS:
        try:
            mp=_find_comp_file(root,'matches',comp); ep=_find_comp_file(root,'events',comp)
        except FileNotFoundError as e:
            issues.append(f'MISSING:{e}'); continue
        matches=_load_json(mp); events=_load_json(ep)
        mids={str(m.get('wyId')) for m in matches if m.get('wyId') is not None}
        emids={str(e.get('matchId')) for e in events if e.get('matchId') is not None}
        outside=sorted(emids-mids)
        expected=EXPECTED_MATCHES[comp]; n=len(mids); total+=n
        cissues=[]
        if strict_counts and n!=expected: cissues.append(f'MATCH_COUNT_EXPECTED_{expected}_GOT_{n}')
        if outside: cissues.append(f'EVENT_MATCH_IDS_OUTSIDE_MATCH_FILE:{len(outside)}')
        if n and not emids: cissues.append('NO_EVENT_MATCH_IDS')
        comps[comp]={'matches':n,'expected_matches':expected,'event_rows':len(events),'event_matches':len(emids),'issues':cissues,
                     'matches_file':str(mp),'events_file':str(ep)}
        issues.extend(f'{comp}:{x}' for x in cissues)
    if strict_counts and total!=EXPECTED_MATCHES_TOTAL: issues.append(f'TOTAL_MATCH_COUNT_EXPECTED_{EXPECTED_MATCHES_TOTAL}_GOT_{total}')
    return {'ok':not issues,'issues':issues,'competitions':comps,'expected_total_matches':EXPECTED_MATCHES_TOTAL,'actual_total_matches':total,
            'license':LICENSE,'rights_status':RIGHTS,'prematch_lineup_permission':'POST_MATCH_ONLY'}

def ingest_competition(con,root:str|Path,competition:str):
    if competition not in COMPETITIONS: raise ValueError(f'UNKNOWN_WYSCOUT_COMPETITION:{competition}')
    root=Path(root); sid=source_id(con)
    players=_load_json(root/'players.json'); teams=_load_json(root/'teams.json')
    matches=_load_json(_find_comp_file(root,'matches',competition)); events=_load_json(_find_comp_file(root,'events',competition))
    pmap=_player_map(players); tmap=_team_map(teams); ea=_aggregate_events(events)
    n=0; match_n=0
    for m in matches:
        mid=str(m.get('wyId')); lineup=_minutes_and_lineup(m); ko=str(m.get('dateutc') or m.get('date') or '')
        for (mm,pid),li in lineup.items():
            if mm!=mid: continue
            ev=ea.get((mid,pid),{}); p=pmap.get(pid,{})
            con.execute('''INSERT OR REPLACE INTO research_player_match_stats(source_id,source_fixture_key,source_team_key,source_player_key,
              competition_key,season_key,kickoff_utc,team_name,player_name,started,minutes,minutes_quality,position,role,shots,sot,sot_definition,
              goals,assists,fouls_committed,fouls_drawn,yellow,red,dribbles_attempted,dribbles_success,crosses,event_rows,license_class,rights_status,
              model_use_permission,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
              (sid,mid,li['team_id'],pid,competition,'2017/18',ko,tmap.get(li['team_id']),p.get('name'),li['started'],li['minutes'],'FORMATION_SUBSTITUTION_APPROX',
               p.get('role'),p.get('role'),ev.get('shots',0),ev.get('sot',0),'GOAL_OR_ACCURATE_NOT_BLOCKED_PROXY',ev.get('goals',0),li.get('assists',0),
               ev.get('fouls',0),None,ev.get('yellow',0),ev.get('red',0),ev.get('dribbles',0),ev.get('dribbles_success',0),ev.get('crosses',0),ev.get('event_rows',0),
               LICENSE,RIGHTS,'RESEARCH_ALLOWED_WITH_ATTRIBUTION',json.dumps({'actual_lineup_timing':'POST_MATCH_ONLY','source':'Pappalardo/Wyscout Open','fouls_drawn':'UNAVAILABLE_FROM_CURRENT_ADAPTER'},ensure_ascii=False)))
            n+=1
        match_n+=1
    con.commit(); return {'competition':competition,'matches':match_n,'expected_matches':EXPECTED_MATCHES[competition],'player_match_rows':n,'license':LICENSE,'rights_status':RIGHTS}

def ingest_all_big5(con,root:str|Path, *, strict_counts=True):
    audit=validate_dataset(root,strict_counts=strict_counts)
    if not audit['ok']:
        raise ValueError('WYSCOUT_DATASET_VALIDATION_FAIL:'+json.dumps(audit['issues']))
    results=[ingest_competition(con,root,c) for c in COMPETITIONS]
    r=readiness(con)
    r.update({'dataset_validation':audit,'competition_results':results,'expected_matches':EXPECTED_MATCHES_TOTAL,
              'complete_big5_2017_18':r['matches']==EXPECTED_MATCHES_TOTAL})
    return r

def readiness(con):
    sid=source_id(con); r=con.execute('''SELECT COUNT(*) rows, COUNT(DISTINCT source_fixture_key) matches, COUNT(DISTINCT source_player_key) players,
      AVG(CASE WHEN minutes IS NOT NULL THEN 1.0 ELSE 0 END) minutes_coverage,
      AVG(CASE WHEN shots IS NOT NULL THEN 1.0 ELSE 0 END) shot_coverage,
      AVG(CASE WHEN sot IS NOT NULL THEN 1.0 ELSE 0 END) sot_coverage,
      AVG(CASE WHEN fouls_committed IS NOT NULL THEN 1.0 ELSE 0 END) foul_coverage,
      AVG(CASE WHEN fouls_drawn IS NOT NULL THEN 1.0 ELSE 0 END) fouled_coverage,
      AVG(CASE WHEN yellow IS NOT NULL THEN 1.0 ELSE 0 END) card_coverage,
      AVG(CASE WHEN crosses IS NOT NULL THEN 1.0 ELSE 0 END) cross_coverage
      FROM research_player_match_stats WHERE source_id=?''',(sid,)).fetchone()
    out=dict(r); out['expected_matches']=EXPECTED_MATCHES_TOTAL; out['complete_big5_2017_18']=int(out.get('matches') or 0)==EXPECTED_MATCHES_TOTAL
    out['player_model_permission']='RESEARCH_ONLY_NOT_ACTIVE'; out['prematch_lineup_permission']='POST_MATCH_ONLY'
    return out
