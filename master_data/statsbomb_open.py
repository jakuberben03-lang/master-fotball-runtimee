from __future__ import annotations
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .advanced import ensure_source, register_provider_capability, resolve_linked_fixture, ensure_player, ingest_metric_observation, ingest_lineup_snapshot

SOURCE_NAME='StatsBomb Open Data'

def source_id_and_capabilities(con):
    sid=ensure_source(con,SOURCE_NAME,'OPEN_RESEARCH_DATA','https://github.com/statsbomb/open-data',35,
                      'Research adapter only. Coverage is partial; never infer full Big-5 or current-season coverage.',
                      'Event/lineup data are useful for research; historical publication timestamps are not a pre-match odds/lineup timestamp.')
    for cap in ['events','lineups','xg','shots','player_events']:
        register_provider_capability(con,sid,cap,'RESEARCH_ONLY',timing_granularity='POST_MATCH',license_class='OPEN_RESEARCH',
                                     notes='Use only where the open-data repository actually contains the competition/season.')
    return sid

def _iso_after_kickoff(kickoff:str):
    dt=datetime.fromisoformat(kickoff.replace('Z','+00:00'))+timedelta(seconds=1)
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')

def ingest_match(con, root:str|Path, statsbomb_match_id:str|int):
    """Research-only ingestion for an explicitly linked StatsBomb match.
    Actual lineup is timestamped just AFTER kickoff by design because open historical files do not prove when the XI became observable.
    It can therefore train post-match/player-history features but cannot leak actual XI into a pre-match backtest.
    """
    root=Path(root); sid=source_id_and_capabilities(con); key=str(statsbomb_match_id)
    fixture_id=resolve_linked_fixture(con,sid,key)
    fx=con.execute('SELECT kickoff_utc,home_team_id,away_team_id FROM fixtures WHERE fixture_id=?',(fixture_id,)).fetchone()
    ep=root/'data'/'events'/f'{key}.json'
    if not ep.exists(): raise FileNotFoundError(ep)
    events=json.loads(ep.read_text(encoding='utf-8'))
    team_xg={}; team_shots={}; player_xg={}; starters={}; formations={}
    for ev in events:
        t=(ev.get('team') or {}).get('id'); typ=(ev.get('type') or {}).get('name')
        if typ=='Shot':
            xg=float((ev.get('shot') or {}).get('statsbomb_xg') or 0.0); team_xg[t]=team_xg.get(t,0.0)+xg; team_shots[t]=team_shots.get(t,0)+1
            p=(ev.get('player') or {}); pid=p.get('id')
            if pid is not None: player_xg[pid]=player_xg.get(pid,0.0)+xg
        elif typ=='Starting XI':
            tactics=ev.get('tactics') or {}; formations[t]=tactics.get('formation'); starters[t]=tactics.get('lineup') or []
    # Map StatsBomb teams by the Starting XI event ordering to canonical home/away only if explicit team-side evidence exists in event names.
    # We require name match as a safety CHECK, not as an identity merge.
    canon={fx['home_team_id']:con.execute('SELECT canonical_name FROM teams WHERE team_id=?',(fx['home_team_id'],)).fetchone()[0],
           fx['away_team_id']:con.execute('SELECT canonical_name FROM teams WHERE team_id=?',(fx['away_team_id'],)).fetchone()[0]}
    team_id_map={}
    from .identity import normalize_name
    for ev in events:
        sbt=ev.get('team') or {}; sbid=sbt.get('id'); name=sbt.get('name')
        if sbid in team_id_map or not name: continue
        candidates=[tid for tid,n in canon.items() if normalize_name(n)==normalize_name(name)]
        if len(candidates)==1: team_id_map[sbid]=candidates[0]
    if len(team_id_map)<2:
        raise ValueError('StatsBomb team names do not exactly normalize to both explicitly linked fixture teams; add a verified team mapping before ingest')
    obs=_iso_after_kickoff(fx['kickoff_utc'])
    for sbtid,ctid in team_id_map.items():
        side='HOME' if ctid==fx['home_team_id'] else 'AWAY'
        if sbtid in team_xg:
            ingest_metric_observation(con,fixture_id,sid,'TEAM',ctid,'xg',team_xg[sbtid],side,'xG',None,'POST_MATCH_SOURCE',str(ep),f'{key}:{sbtid}:xg',{'research_only':True})
            con.execute(f"UPDATE team_match_stats SET {side.lower()}_xg=? WHERE fixture_id=?",(team_xg[sbtid],fixture_id))
        if sbtid in starters:
            members=[]
            for item in starters[sbtid]:
                p=item.get('player') or {}; pos=(item.get('position') or {}).get('name')
                pid=ensure_player(con,sid,str(p.get('id') or ''),p.get('name') or str(p.get('id')),primary_position=pos)
                members.append({'player_id':pid,'is_starting':True,'position':pos})
            ingest_lineup_snapshot(con,fixture_id,ctid,sid,'CONFIRMED',obs,members,str(formations.get(sbtid) or ''),'B',str(ep),f'{key}:{sbtid}:startingxi',
                                   {'research_only':True,'timestamp_semantics':'POST_KICKOFF_GUARD'})
    con.commit()
    return {'fixture_id':fixture_id,'statsbomb_match_id':key,'team_xg':team_xg,'lineup_teams':len(starters),'research_only':True}
