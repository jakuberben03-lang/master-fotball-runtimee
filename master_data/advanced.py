from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path
from .identity import stable_id, normalize_name


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def ensure_source(con, name:str, source_type:str='DATA_PROVIDER', base_url:str|None=None,
                  authority_rank:int=50, usage_notes:str|None=None, reliability_notes:str|None=None):
    sid=stable_id('source',name)
    con.execute('''INSERT INTO sources(source_id,name,source_type,base_url,authority_rank,usage_notes,reliability_notes)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET
                   source_type=excluded.source_type,base_url=COALESCE(excluded.base_url,sources.base_url),
                   authority_rank=excluded.authority_rank,usage_notes=COALESCE(excluded.usage_notes,sources.usage_notes),
                   reliability_notes=COALESCE(excluded.reliability_notes,sources.reliability_notes)''',
                (sid,name,source_type,base_url,authority_rank,usage_notes,reliability_notes))
    con.commit()
    # Some legacy MASTER sources were created before source IDs were standardized on stable_id('source', name).
    # ON CONFLICT(name) updates that existing row, so always return the actual persisted ID rather than the candidate ID.
    row=con.execute('SELECT source_id FROM sources WHERE name=?',(name,)).fetchone()
    return row['source_id'] if row else sid


def register_provider_capability(con, source_id:str, capability_key:str, coverage_scope='UNKNOWN',
                                 competitions=None, seasons=None, timing_granularity='UNKNOWN',
                                 license_class='UNKNOWN', notes=None):
    con.execute('''INSERT INTO data_provider_capabilities(source_id,capability_key,coverage_scope,supported_competitions_json,
                   supported_seasons_json,timing_granularity,license_class,notes)
                   VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(source_id,capability_key) DO UPDATE SET
                   coverage_scope=excluded.coverage_scope,supported_competitions_json=excluded.supported_competitions_json,
                   supported_seasons_json=excluded.supported_seasons_json,timing_granularity=excluded.timing_granularity,
                   license_class=excluded.license_class,notes=excluded.notes,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''',
                (source_id,capability_key,coverage_scope,json.dumps(competitions or []),json.dumps(seasons or []),
                 timing_granularity,license_class,notes))
    con.commit()


def link_external_fixture(con, source_id:str, source_fixture_key:str, fixture_id:str,
                          link_method='MANUAL_VERIFIED', evidence=None):
    if link_method not in {'EXPLICIT_ID','MANUAL_VERIFIED','OFFICIAL_MAPPING'}:
        raise ValueError('invalid link_method')
    if not con.execute('SELECT 1 FROM fixtures WHERE fixture_id=?',(fixture_id,)).fetchone():
        raise KeyError(f'unknown canonical fixture {fixture_id}')
    con.execute('''INSERT INTO fixture_source_links(source_id,source_fixture_key,fixture_id,link_method,evidence_json)
                   VALUES(?,?,?,?,?) ON CONFLICT(source_id,source_fixture_key) DO UPDATE SET
                   fixture_id=excluded.fixture_id,link_method=excluded.link_method,evidence_json=excluded.evidence_json,
                   linked_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''',
                (source_id,str(source_fixture_key),fixture_id,link_method,json.dumps(evidence or {},ensure_ascii=False)))
    con.commit()


def resolve_linked_fixture(con, source_id:str, source_fixture_key:str):
    r=con.execute('SELECT fixture_id FROM fixture_source_links WHERE source_id=? AND source_fixture_key=?',
                  (source_id,str(source_fixture_key))).fetchone()
    if not r: raise KeyError(f'NO EXPLICIT FIXTURE LINK for source={source_id} key={source_fixture_key}')
    return r['fixture_id']


def ensure_player(con, source_id:str, source_player_key:str|None, name:str, birth_date=None, country=None, primary_position=None):
    if source_player_key:
        alias_norm=f'id:{source_player_key}'
        r=con.execute('SELECT player_id FROM player_aliases WHERE source_id=? AND alias_normalized=?',(source_id,alias_norm)).fetchone()
        if r: return r['player_id']
    else:
        alias_norm=normalize_name(name)
        r=con.execute('SELECT player_id FROM player_aliases WHERE source_id=? AND alias_normalized=?',(source_id,alias_norm)).fetchone()
        if r: return r['player_id']
    pid=stable_id('player',source_id,source_player_key or normalize_name(name),birth_date or '')
    con.execute('''INSERT OR IGNORE INTO players(player_id,canonical_name,birth_date,country,primary_position) VALUES(?,?,?,?,?)''',
                (pid,name,birth_date,country,primary_position))
    alias=str(source_player_key) if source_player_key else name
    con.execute('''INSERT OR IGNORE INTO player_aliases(source_id,alias,alias_normalized,player_id) VALUES(?,?,?,?)''',
                (source_id,alias,alias_norm,pid))
    return pid


def ingest_metric_observation(con, fixture_id:str, source_id:str, entity_type:str, entity_id:str,
                              metric_name:str, metric_value, team_side=None, unit=None, observed_at=None,
                              availability_class='UNKNOWN', source_locator=None, source_record_key=None, evidence=None):
    oid=stable_id('metric-observation',fixture_id,source_id,entity_type,entity_id,metric_name,source_record_key or '',observed_at or '')
    con.execute('''INSERT OR REPLACE INTO fixture_metric_provenance(metric_observation_id,fixture_id,source_id,entity_type,
                   entity_id,team_side,metric_name,metric_value,unit,observed_at,availability_class,source_locator,
                   source_record_key,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (oid,fixture_id,source_id,entity_type,entity_id,team_side,metric_name,metric_value,unit,observed_at,
                 availability_class,source_locator,source_record_key,json.dumps(evidence or {},ensure_ascii=False)))
    return oid


def upsert_team_advanced_stats(con, fixture_id:str, source_id:str, home:dict|None=None, away:dict|None=None,
                               observed_at=None, availability_class='POST_MATCH_SOURCE', source_locator=None,
                               source_record_key=None):
    allowed={'xg':'xg','blocked_shots':'blocked_shots','crosses':'crosses','box_touches':'box_touches','possession':'possession'}
    fx=con.execute('SELECT home_team_id,away_team_id FROM fixtures WHERE fixture_id=?',(fixture_id,)).fetchone()
    if not fx: raise KeyError(fixture_id)
    sets=[]; vals=[]
    for side,payload,team_id in [('home',home or {},fx['home_team_id']),('away',away or {},fx['away_team_id'])]:
        for key,col in allowed.items():
            if payload.get(key) is None: continue
            sets.append(f'{side}_{col}=?'); vals.append(float(payload[key]))
            ingest_metric_observation(con,fixture_id,source_id,'TEAM',team_id,key,float(payload[key]),side.upper(),
                                      None,observed_at,availability_class,source_locator,
                                      f'{source_record_key or fixture_id}:{side}:{key}')
    if sets:
        vals.append(fixture_id)
        con.execute('UPDATE team_match_stats SET '+','.join(sets)+",updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE fixture_id=?",vals)
    con.commit(); return len(sets)


def ingest_lineup_snapshot(con, fixture_id:str, team_id:str, source_id:str, lineup_status:str, observed_at:str,
                           members:list[dict], formation=None, confidence='UNKNOWN', source_locator=None,
                           source_record_key=None, evidence=None):
    if not observed_at: raise ValueError('observed_at required for lineup snapshot')
    if lineup_status not in {'EXPECTED','PREDICTED','CONFIRMED','CORRECTED'}: raise ValueError('invalid lineup_status')
    kickoff=con.execute('SELECT kickoff_utc FROM fixtures WHERE fixture_id=?',(fixture_id,)).fetchone()
    if not kickoff: raise KeyError(fixture_id)
    sid=stable_id('lineup-snapshot',fixture_id,team_id,source_id,lineup_status,observed_at,source_record_key or '')
    con.execute('''INSERT OR REPLACE INTO lineup_snapshots(lineup_snapshot_id,fixture_id,team_id,source_id,lineup_status,
                   observed_at,source_locator,source_record_key,confidence,formation,evidence_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(sid,fixture_id,team_id,source_id,lineup_status,observed_at,
                   source_locator,source_record_key,confidence,formation,json.dumps(evidence or {},ensure_ascii=False)))
    con.execute('DELETE FROM lineup_snapshot_members WHERE lineup_snapshot_id=?',(sid,))
    for m in members:
        pid=m.get('player_id')
        if not pid: raise ValueError('lineup member requires canonical player_id')
        con.execute('''INSERT INTO lineup_snapshot_members(lineup_snapshot_id,player_id,is_starting,shirt_number,position,role,side,captain)
                       VALUES(?,?,?,?,?,?,?,?)''',(sid,pid,int(bool(m.get('is_starting',True))),m.get('shirt_number'),
                       m.get('position'),m.get('role'),m.get('side'),None if m.get('captain') is None else int(bool(m.get('captain')))))
    con.commit(); return sid


def ingest_availability_snapshot(con, player_id:str, source_id:str, status:str, observed_at:str, team_id=None,
                                 fixture_id=None, reason=None, expected_return=None, effective_from=None, effective_to=None,
                                 confidence='UNKNOWN', source_locator=None, source_record_key=None, evidence=None):
    if not observed_at: raise ValueError('observed_at required')
    aid=stable_id('availability',player_id,source_id,fixture_id or '',observed_at,source_record_key or '')
    con.execute('''INSERT OR REPLACE INTO player_availability_snapshots(availability_snapshot_id,player_id,team_id,fixture_id,
                   source_id,status,reason,expected_return,observed_at,effective_from,effective_to,confidence,source_locator,
                   source_record_key,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                (aid,player_id,team_id,fixture_id,source_id,status,reason,expected_return,observed_at,effective_from,effective_to,
                 confidence,source_locator,source_record_key,json.dumps(evidence or {},ensure_ascii=False)))
    con.commit(); return aid


def ingest_transfer_event(con, player_id:str, source_id:str, event_type:str, effective_date:str, observed_at:str,
                          from_team_id=None,to_team_id=None,source_locator=None,source_record_key=None,evidence=None):
    if observed_at > effective_date+'T23:59:59Z' and str(effective_date).endswith('Z'):
        pass
    eid=stable_id('transfer',player_id,source_id,event_type,effective_date,source_record_key or '')
    con.execute('''INSERT OR REPLACE INTO transfer_events(transfer_event_id,player_id,from_team_id,to_team_id,source_id,
                   event_type,effective_date,observed_at,source_locator,source_record_key,evidence_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(eid,player_id,from_team_id,to_team_id,source_id,event_type,effective_date,
                   observed_at,source_locator,source_record_key,json.dumps(evidence or {},ensure_ascii=False)))
    con.commit(); return eid


def ingest_normalized_provider_json(con, path:str|Path):
    """Ingest a provider-neutral JSON package. Cross-source fixtures MUST already be explicitly linked.

    Format: {source:{name,...}, capabilities:[...], fixture_packages:[{source_fixture_key, metrics, lineups, availability, transfers}]}
    This function deliberately refuses fuzzy fixture/team matching.
    """
    doc=json.loads(Path(path).read_text(encoding='utf-8'))
    src=doc['source']; source_id=ensure_source(con,src['name'],src.get('source_type','DATA_PROVIDER'),src.get('base_url'),
                                              int(src.get('authority_rank',50)),src.get('usage_notes'),src.get('reliability_notes'))
    for c in doc.get('capabilities',[]):
        register_provider_capability(con,source_id,c['capability_key'],c.get('coverage_scope','UNKNOWN'),
                                     c.get('competitions'),c.get('seasons'),c.get('timing_granularity','UNKNOWN'),
                                     c.get('license_class','UNKNOWN'),c.get('notes'))
    counts={'metrics':0,'lineups':0,'availability':0,'transfers':0}
    for pkg in doc.get('fixture_packages',[]):
        fixture_id=resolve_linked_fixture(con,source_id,str(pkg['source_fixture_key']))
        fx=con.execute('SELECT home_team_id,away_team_id FROM fixtures WHERE fixture_id=?',(fixture_id,)).fetchone()
        advanced_by_side={'HOME':{},'AWAY':{}}
        advanced_names={'xg','blocked_shots','crosses','box_touches','possession'}
        for mm in pkg.get('team_metrics',[]):
            side=mm['side'].upper(); team_id=fx['home_team_id'] if side=='HOME' else fx['away_team_id']
            ingest_metric_observation(con,fixture_id,source_id,'TEAM',team_id,mm['metric_name'],mm.get('value'),side,
                                      mm.get('unit'),mm.get('observed_at'),mm.get('availability_class','UNKNOWN'),
                                      mm.get('source_locator'),mm.get('source_record_key'),mm.get('evidence')); counts['metrics']+=1
            if mm['metric_name'] in advanced_names and mm.get('value') is not None:
                advanced_by_side[side][mm['metric_name']]=mm.get('value')
        # Materialize canonical advanced columns only for explicitly supported metric names; provenance remains authoritative.
        if advanced_by_side['HOME'] or advanced_by_side['AWAY']:
            # Avoid duplicate provenance here: values were already inserted above.
            sets=[]; vals=[]
            colmap={'xg':'xg','blocked_shots':'blocked_shots','crosses':'crosses','box_touches':'box_touches','possession':'possession'}
            for side in ['HOME','AWAY']:
                for key,val in advanced_by_side[side].items():
                    sets.append(f"{side.lower()}_{colmap[key]}=?"); vals.append(float(val))
            vals.append(fixture_id)
            con.execute('UPDATE team_match_stats SET '+','.join(sets)+",updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE fixture_id=?",vals)
        for lu in pkg.get('lineups',[]):
            team_id=fx['home_team_id'] if lu['side'].upper()=='HOME' else fx['away_team_id']; members=[]
            for m in lu.get('members',[]):
                pid=ensure_player(con,source_id,str(m.get('source_player_key') or ''),m['name'],m.get('birth_date'),m.get('country'),m.get('position'))
                members.append({**m,'player_id':pid})
            ingest_lineup_snapshot(con,fixture_id,team_id,source_id,lu['lineup_status'],lu['observed_at'],members,
                                   lu.get('formation'),lu.get('confidence','UNKNOWN'),lu.get('source_locator'),
                                   lu.get('source_record_key'),lu.get('evidence')); counts['lineups']+=1
        for av in pkg.get('availability',[]):
            pid=ensure_player(con,source_id,str(av.get('source_player_key') or ''),av['name'],av.get('birth_date'),av.get('country'),av.get('position'))
            team_id=fx['home_team_id'] if av.get('side','HOME').upper()=='HOME' else fx['away_team_id']
            ingest_availability_snapshot(con,pid,source_id,av['status'],av['observed_at'],team_id,fixture_id,av.get('reason'),
                                         av.get('expected_return'),av.get('effective_from'),av.get('effective_to'),
                                         av.get('confidence','UNKNOWN'),av.get('source_locator'),av.get('source_record_key'),av.get('evidence'))
            counts['availability']+=1
    return {'source_id':source_id,**counts}
