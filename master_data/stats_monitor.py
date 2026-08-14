from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .advanced import ensure_player, ingest_metric_observation
from .identity import stable_id, normalize_name
from .provider_fetch import utcnow
from .quota import can_spend, quota_state, record_cost

FINAL_STATUSES = {'FT', 'AET', 'PEN'}
LIVE_STATUSES = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'INT', 'LIVE'}
PRE_STATUSES = {'NS', 'TBD'}

DEFAULT_COMPETITIONS = [
    {'name': 'Premier League', 'country': 'England', 'aliases': []},
    {'name': 'La Liga', 'country': 'Spain', 'aliases': []},
    {'name': 'Serie A', 'country': 'Italy', 'aliases': []},
    {'name': 'Bundesliga', 'country': 'Germany', 'aliases': []},
    {'name': 'Ligue 1', 'country': 'France', 'aliases': []},
    {'name': 'Czech Liga', 'country': 'Czech Republic', 'aliases': ['Czech Liga']},
    {'name': 'UEFA Champions League', 'country': 'World', 'aliases': ['UEFA Champions League']},
    {'name': 'UEFA Europa League', 'country': 'World', 'aliases': ['UEFA Europa League']},
    {'name': 'UEFA Conference League', 'country': 'World', 'aliases': ['UEFA Europa Conference League', 'UEFA Conference League']},
]

STAT_ALIASES = {
    'shots': {'total shots'},
    'sot': {'shots on goal'},
    'blocked_shots': {'blocked shots'},
    'fouls': {'fouls'},
    'corners': {'corner kicks', 'corners'},
    'yellow': {'yellow cards'},
    'red': {'red cards'},
    'xg': {'expected goals', 'expected goals (xg)', 'expected_goals'},
    'possession': {'ball possession', 'possession'},
    'offsides': {'offsides'},
    'saves': {'goalkeeper saves'},
    'passes': {'total passes'},
    'passes_accurate': {'passes accurate'},
}


def _dt(v: str | datetime) -> datetime:
    if isinstance(v, datetime):
        d = v
    else:
        d = datetime.fromisoformat(str(v).replace('Z', '+00:00'))
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _number(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace('%', '')
    if not s or s.casefold() in {'none', 'null', 'nan'}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def _int(v):
    n = _number(v)
    return None if n is None else int(round(n))


def _phase(status: str | None) -> str:
    s = (status or '').upper()
    if s in FINAL_STATUSES:
        return 'POST_MATCH_FINAL'
    if s in LIVE_STATUSES:
        return 'LIVE'
    if s in PRE_STATUSES:
        return 'PRE_MATCH'
    if s in {'PST', 'CANC', 'ABD', 'AWD', 'WO'}:
        return 'NON_STANDARD'
    return 'UNKNOWN'


def _fixture_doc(bundle: dict) -> dict | None:
    rows = ((bundle.get('fixture') or {}).get('response') or [])
    return rows[0] if rows else None


def _provider_status(fdoc: dict | None) -> tuple[str, int | None]:
    if not fdoc:
        return 'UNKNOWN', None
    st = ((fdoc.get('fixture') or {}).get('status') or {})
    return str(st.get('short') or 'UNKNOWN'), _int(st.get('elapsed'))


def _stats_payload(doc: dict) -> list[dict]:
    return (doc or {}).get('response') or []


def _stats_dict(team_block: dict) -> dict:
    out = {}
    for x in team_block.get('statistics') or []:
        typ = normalize_name(x.get('type') or '')
        val = x.get('value')
        for key, aliases in STAT_ALIASES.items():
            if typ in {normalize_name(a) for a in aliases}:
                out[key] = _number(val)
                break
    # Count-like metrics should remain integers when present.
    for k in ('shots', 'sot', 'blocked_shots', 'fouls', 'corners', 'yellow', 'red', 'offsides', 'saves', 'passes', 'passes_accurate'):
        if out.get(k) is not None:
            out[k] = int(round(out[k]))
    return out


def _team_map(con, fixture_id: str, provider_teams: list[dict]) -> dict[str, str]:
    fx = con.execute('SELECT home_team_id,away_team_id FROM fixtures WHERE fixture_id=?', (fixture_id,)).fetchone()
    if not fx:
        raise KeyError(fixture_id)
    canon = {}
    for tid in (fx['home_team_id'], fx['away_team_id']):
        canon[tid] = con.execute('SELECT canonical_name FROM teams WHERE team_id=?', (tid,)).fetchone()[0]
    out = {}
    for t in provider_teams:
        if not t:
            continue
        pid, name = t.get('id'), t.get('name')
        if pid is None or not name:
            continue
        matches = [tid for tid, n in canon.items() if normalize_name(n) == normalize_name(name)]
        if len(matches) == 1:
            out[str(pid)] = matches[0]
    if len(set(out.values())) < 2:
        raise ValueError('STATS_MONITOR_TEAM_IDENTITY_CHECK_FAILED')
    return out


def _source_and_fixture(con, provider_fixture_id: int | str):
    from .api_football import source_id_and_capabilities
    sid = source_id_and_capabilities(con)
    row = con.execute('SELECT fixture_id FROM fixture_source_links WHERE source_id=? AND source_fixture_key=?', (sid, str(provider_fixture_id))).fetchone()
    if not row:
        raise KeyError(f'NO_EXPLICIT_FIXTURE_LINK:{provider_fixture_id}')
    return sid, row['fixture_id']


def _insert_team_snapshot(con, fixture_id, team_id, source_id, observed_at, phase, status, elapsed, stats, raw):
    sid = stable_id('fixture-stat-snapshot', fixture_id, team_id, source_id, observed_at, phase)
    con.execute('''INSERT OR IGNORE INTO fixture_stat_snapshots(
        stat_snapshot_id,fixture_id,team_id,source_id,observed_at,phase,provider_status,elapsed,
        shots,sot,blocked_shots,fouls,corners,yellow,red,xg,possession,offsides,saves,passes,passes_accurate,raw_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (sid, fixture_id, team_id, source_id, observed_at, phase, status, elapsed,
         stats.get('shots'), stats.get('sot'), stats.get('blocked_shots'), stats.get('fouls'), stats.get('corners'),
         stats.get('yellow'), stats.get('red'), stats.get('xg'), stats.get('possession'), stats.get('offsides'),
         stats.get('saves'), stats.get('passes'), stats.get('passes_accurate'), json.dumps(raw, ensure_ascii=False, default=str)))
    return con.execute('SELECT changes()').fetchone()[0]


def _player_values(pr: dict) -> dict:
    stats = (pr.get('statistics') or [{}])[0] or {}
    games = stats.get('games') or {}; shots = stats.get('shots') or {}; goals = stats.get('goals') or {}
    fouls = stats.get('fouls') or {}; cards = stats.get('cards') or {}; drib = stats.get('dribbles') or {}
    return {
        'minutes': _number(games.get('minutes')),
        'started': 1 if games.get('substitute') is False else (0 if games.get('substitute') is True else None),
        'position': games.get('position'),
        'shots': _int(shots.get('total')), 'sot': _int(shots.get('on')),
        'goals': _int(goals.get('total')), 'assists': _int(goals.get('assists')),
        'fouls_committed': _int(fouls.get('committed')), 'fouls_drawn': _int(fouls.get('drawn')),
        'yellow': _int(cards.get('yellow')), 'red': _int(cards.get('red')),
        'dribbles_attempted': _int(drib.get('attempts')), 'dribbles_success': _int(drib.get('success')),
    }


def _insert_player_snapshot(con, fixture_id, team_id, player_id, source_id, observed_at, phase, status, vals, raw):
    sid = stable_id('player-stat-snapshot', fixture_id, player_id, source_id, observed_at, phase)
    con.execute('''INSERT OR IGNORE INTO player_stat_snapshots(
        player_stat_snapshot_id,fixture_id,team_id,player_id,source_id,observed_at,phase,provider_status,
        minutes,started,position,shots,sot,goals,assists,fouls_committed,fouls_drawn,yellow,red,dribbles_attempted,dribbles_success,raw_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (sid, fixture_id, team_id, player_id, source_id, observed_at, phase, status,
         vals.get('minutes'), vals.get('started'), vals.get('position'), vals.get('shots'), vals.get('sot'), vals.get('goals'),
         vals.get('assists'), vals.get('fouls_committed'), vals.get('fouls_drawn'), vals.get('yellow'), vals.get('red'),
         vals.get('dribbles_attempted'), vals.get('dribbles_success'), json.dumps(raw, ensure_ascii=False, default=str)))
    return con.execute('SELECT changes()').fetchone()[0]


def _materialize_team_final(con, fixture_id, source_id, observed_at, status, fdoc, side_stats: dict[str, dict]):
    fx = con.execute('SELECT home_team_id,away_team_id FROM fixtures WHERE fixture_id=?', (fixture_id,)).fetchone()
    if not fx:
        raise KeyError(fixture_id)
    con.execute('INSERT OR IGNORE INTO team_match_stats(fixture_id) VALUES(?)', (fixture_id,))
    update = {}
    for side, tid in [('home', fx['home_team_id']), ('away', fx['away_team_id'])]:
        s = side_stats.get(tid, {})
        for key, col in [('shots','shots'),('sot','sot'),('blocked_shots','blocked_shots'),('fouls','fouls'),('corners','corners'),
                         ('yellow','yellow'),('red','red'),('xg','xg'),('possession','possession')]:
            if s.get(key) is not None:
                update[f'{side}_{col}'] = s[key]
                ingest_metric_observation(con, fixture_id, source_id, 'TEAM', tid, key, s[key], side.upper(), observed_at=observed_at,
                                          availability_class='POST_MATCH_SOURCE', source_locator='api-football://fixtures/statistics',
                                          source_record_key=f'{fixture_id}:{observed_at}:{side}:{key}', evidence={'monitor_phase':'POST_MATCH_FINAL','provider_status':status})
    if fdoc:
        score = fdoc.get('score') or {}; goals = fdoc.get('goals') or {}
        ht = score.get('halftime') or {}
        # FT is unambiguous. AET/PEN scores are intentionally not materialized as regulation goals here.
        if status == 'FT':
            if goals.get('home') is not None: update['home_goals'] = _int(goals.get('home'))
            if goals.get('away') is not None: update['away_goals'] = _int(goals.get('away'))
        if ht.get('home') is not None: update['home_ht_goals'] = _int(ht.get('home'))
        if ht.get('away') is not None: update['away_ht_goals'] = _int(ht.get('away'))
    if update:
        sets = ','.join(f'{k}=?' for k in update)
        con.execute(f"UPDATE team_match_stats SET {sets},updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE fixture_id=?", tuple(update.values()) + (fixture_id,))
    con.execute("UPDATE fixtures SET status=?,data_freshness='FRESH',last_ingested_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE fixture_id=?", (status, fixture_id))
    return len(update)


def _materialize_players_final(con, fixture_id, player_rows: list[tuple[str, str, dict]]):
    n = 0
    for team_id, player_id, vals in player_rows:
        con.execute('''INSERT INTO player_match_stats(fixture_id,team_id,player_id,minutes,started,position,role,shots,sot,goals,assists,
                       fouls_committed,fouls_drawn,yellow,red,dribbles_attempted,dribbles_success)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fixture_id,player_id) DO UPDATE SET
                       team_id=excluded.team_id,minutes=excluded.minutes,started=excluded.started,position=excluded.position,
                       shots=excluded.shots,sot=excluded.sot,goals=excluded.goals,assists=excluded.assists,
                       fouls_committed=excluded.fouls_committed,fouls_drawn=excluded.fouls_drawn,yellow=excluded.yellow,red=excluded.red,
                       dribbles_attempted=excluded.dribbles_attempted,dribbles_success=excluded.dribbles_success''',
                    (fixture_id,team_id,player_id,vals.get('minutes'),vals.get('started'),vals.get('position'),None,vals.get('shots'),vals.get('sot'),
                     vals.get('goals'),vals.get('assists'),vals.get('fouls_committed'),vals.get('fouls_drawn'),vals.get('yellow'),vals.get('red'),
                     vals.get('dribbles_attempted'),vals.get('dribbles_success')))
        n += 1
    return n


def ingest_monitor_bundle(con, provider_fixture_id: int | str, bundle: dict, *, observed_at: str | None = None, materialize_final: bool = True):
    source_id, fixture_id = _source_and_fixture(con, provider_fixture_id)
    fdoc = _fixture_doc(bundle)
    status, elapsed = _provider_status(fdoc)
    phase = _phase(status)
    observed_at = observed_at or bundle.get('observed_at') or bundle.get('statistics_fetched_at') or bundle.get('fixture_fetched_at') or utcnow()

    provider_teams = []
    if fdoc:
        for side in ('home','away'):
            provider_teams.append(((fdoc.get('teams') or {}).get(side) or {}))
    for b in _stats_payload(bundle.get('statistics') or {}): provider_teams.append(b.get('team') or {})
    for b in _stats_payload(bundle.get('players') or {}): provider_teams.append(b.get('team') or {})
    teammap = _team_map(con, fixture_id, provider_teams)

    counts = {'fixture_stat_snapshots':0, 'player_stat_snapshots':0, 'final_team_fields':0, 'final_player_rows':0}
    side_stats = {}
    for block in _stats_payload(bundle.get('statistics') or {}):
        pt = block.get('team') or {}; ctid = teammap.get(str(pt.get('id')))
        if not ctid: continue
        vals = _stats_dict(block); side_stats[ctid] = vals
        counts['fixture_stat_snapshots'] += _insert_team_snapshot(con, fixture_id, ctid, source_id, observed_at, phase, status, elapsed, vals, block)

    player_rows = []
    for teamblock in _stats_payload(bundle.get('players') or {}):
        pt = teamblock.get('team') or {}; ctid = teammap.get(str(pt.get('id')))
        if not ctid: continue
        for pr in teamblock.get('players') or []:
            p = pr.get('player') or {}
            if p.get('id') is None: continue
            pid = ensure_player(con, source_id, str(p.get('id')), p.get('name') or str(p.get('id')))
            vals = _player_values(pr)
            player_rows.append((ctid, pid, vals))
            counts['player_stat_snapshots'] += _insert_player_snapshot(con, fixture_id, ctid, pid, source_id, observed_at, phase, status, vals, pr)

    if materialize_final and status in FINAL_STATUSES:
        counts['final_team_fields'] = _materialize_team_final(con, fixture_id, source_id, observed_at, status, fdoc, side_stats)
        counts['final_player_rows'] = _materialize_players_final(con, fixture_id, player_rows)

    # Existing current-observation lineup/injury adapter is reused, preserving its timing contract.
    if bundle.get('lineups') is not None or bundle.get('injuries') is not None:
        from .api_football import ingest_fixture_bundle
        context_bundle = {k:v for k,v in bundle.items() if k in {'fixture','lineups','players','injuries','lineups_fetched_at','players_fetched_at','injuries_fetched_at','fixture_fetched_at'}}
        ctx = ingest_fixture_bundle(con, provider_fixture_id, context_bundle, historical_backfill=False)
        counts['lineup_snapshots'] = ctx.get('lineup_snapshots',0)
        counts['availability_snapshots'] = ctx.get('availability_snapshots',0)
    else:
        counts['lineup_snapshots'] = 0; counts['availability_snapshots'] = 0

    now = utcnow()
    con.execute('''INSERT INTO stats_monitor_state(fixture_id,source_id,provider_fixture_key,last_observed_at,last_phase,last_provider_status,
                   prematch_observed_at,prematch_lineup_count,live_observed_at,postmatch_observed_at,finalized_at,last_error,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fixture_id,source_id) DO UPDATE SET
                   provider_fixture_key=excluded.provider_fixture_key,last_observed_at=excluded.last_observed_at,last_phase=excluded.last_phase,
                   last_provider_status=excluded.last_provider_status,
                   prematch_observed_at=CASE WHEN excluded.last_phase='PRE_MATCH' THEN excluded.last_observed_at ELSE stats_monitor_state.prematch_observed_at END,
                   prematch_lineup_count=CASE WHEN excluded.last_phase='PRE_MATCH' THEN MAX(stats_monitor_state.prematch_lineup_count,excluded.prematch_lineup_count) ELSE stats_monitor_state.prematch_lineup_count END,
                   live_observed_at=CASE WHEN excluded.last_phase='LIVE' THEN excluded.last_observed_at ELSE stats_monitor_state.live_observed_at END,
                   postmatch_observed_at=CASE WHEN excluded.last_phase='POST_MATCH_FINAL' THEN excluded.last_observed_at ELSE stats_monitor_state.postmatch_observed_at END,
                   finalized_at=CASE WHEN excluded.last_phase='POST_MATCH_FINAL' THEN COALESCE(stats_monitor_state.finalized_at,excluded.last_observed_at) ELSE stats_monitor_state.finalized_at END,
                   last_error=NULL,updated_at=excluded.updated_at''',
                (fixture_id, source_id, str(provider_fixture_id), observed_at, phase, status,
                 observed_at if phase=='PRE_MATCH' else None, counts.get('lineup_snapshots',0), observed_at if phase=='LIVE' else None,
                 observed_at if phase=='POST_MATCH_FINAL' else None, observed_at if phase=='POST_MATCH_FINAL' else None, None, now))
    con.commit()
    return {'fixture_id':fixture_id,'provider_fixture_id':str(provider_fixture_id),'phase':phase,'provider_status':status,'observed_at':observed_at,**counts}


def fetch_monitor_bundle(con, provider_fixture_id: int | str, *, raw_dir: str | Path | None = None,
                         include_statistics=True, include_players=True, include_lineups=False, include_injuries=False):
    from .api_football import _request
    out = {}; request_count = 0
    endpoints = [('fixture','fixtures',{'id':provider_fixture_id})]
    if include_statistics: endpoints.append(('statistics','fixtures/statistics',{'fixture':provider_fixture_id}))
    if include_players: endpoints.append(('players','fixtures/players',{'fixture':provider_fixture_id}))
    if include_lineups: endpoints.append(('lineups','fixtures/lineups',{'fixture':provider_fixture_id}))
    if include_injuries: endpoints.append(('injuries','injuries',{'fixture':provider_fixture_id}))
    for key, endpoint, params in endpoints:
        doc, fetched = _request(con, endpoint, params, raw_dir=raw_dir)
        out[key] = doc; out[key+'_fetched_at'] = fetched; request_count += 1
    out['observed_at'] = utcnow(); out['request_count'] = request_count
    return out


def add_watch(con, fixture_id: str, *, priority=50, collect_players=True, collect_lineups=True, collect_injuries=True, collect_live=False):
    con.execute('''INSERT INTO stats_monitor_watchlist(fixture_id,priority,collect_players,collect_lineups,collect_injuries,collect_live,active)
                   VALUES(?,?,?,?,?,?,1) ON CONFLICT(fixture_id) DO UPDATE SET priority=excluded.priority,collect_players=excluded.collect_players,
                   collect_lineups=excluded.collect_lineups,collect_injuries=excluded.collect_injuries,collect_live=excluded.collect_live,active=1,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''',
                (fixture_id,int(priority),int(bool(collect_players)),int(bool(collect_lineups)),int(bool(collect_injuries)),int(bool(collect_live))))
    con.commit(); return {'fixture_id':fixture_id,'status':'WATCHING'}


def remove_watch(con, fixture_id: str):
    con.execute("UPDATE stats_monitor_watchlist SET active=0,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE fixture_id=?", (fixture_id,))
    con.commit(); return {'fixture_id':fixture_id,'status':'DISABLED'}


def select_targets(con, *, now: str | datetime | None = None, prematch_window_minutes=150, postmatch_delay_minutes=105,
                   postmatch_lookback_hours=18, include_live=False, watchlist_only=False, live_min_interval_minutes=20, max_fixtures=12):
    from .api_football import source_id_and_capabilities
    sid = source_id_and_capabilities(con)
    nowdt = _dt(now or datetime.now(timezone.utc)); rows = con.execute('''
        SELECT f.fixture_id,f.kickoff_utc,l.source_fixture_key,w.priority,w.collect_players,w.collect_lineups,w.collect_injuries,w.collect_live,
               s.prematch_observed_at,s.prematch_lineup_count,s.live_observed_at,s.finalized_at,s.last_provider_status
        FROM fixtures f JOIN fixture_source_links l ON l.fixture_id=f.fixture_id AND l.source_id=?
        LEFT JOIN stats_monitor_watchlist w ON w.fixture_id=f.fixture_id AND w.active=1
        LEFT JOIN stats_monitor_state s ON s.fixture_id=f.fixture_id AND s.source_id=?
        WHERE (?=0 OR w.fixture_id IS NOT NULL)
    ''', (sid,sid,1 if watchlist_only else 0)).fetchall()
    out=[]
    for r in rows:
        ko=_dt(r['kickoff_utc']); mins=(ko-nowdt).total_seconds()/60.0
        base={'fixture_id':r['fixture_id'],'provider_fixture_id':r['source_fixture_key'],'kickoff_utc':r['kickoff_utc'],
              'priority':int(r['priority'] if r['priority'] is not None else 50),
              'collect_players':bool(r['collect_players']) if r['collect_players'] is not None else True,
              'collect_lineups':bool(r['collect_lineups']) if r['collect_lineups'] is not None else True,
              'collect_injuries':bool(r['collect_injuries']) if r['collect_injuries'] is not None else True,
              'collect_live':bool(r['collect_live']) if r['collect_live'] is not None else False}
        if 0 <= mins <= prematch_window_minutes:
            # Retry until a lineup is actually captured; no-lineup fetch is not considered complete.
            if not r['prematch_observed_at'] or int(r['prematch_lineup_count'] or 0) == 0:
                out.append({**base,'phase':'PRE_MATCH','estimated_cost':1+int(base['collect_lineups'])+int(base['collect_injuries'])})
        elif -postmatch_delay_minutes < mins < 0 and include_live and base['collect_live'] and not r['finalized_at']:
            due=True
            if r['live_observed_at']:
                due=(nowdt-_dt(r['live_observed_at'])).total_seconds() >= live_min_interval_minutes*60
            if due: out.append({**base,'phase':'LIVE','estimated_cost':2+int(base['collect_players'])})
        elif -(postmatch_lookback_hours*60) <= mins <= -postmatch_delay_minutes and not r['finalized_at']:
            out.append({**base,'phase':'POST_MATCH','estimated_cost':2+int(base['collect_players'])+int(base['collect_lineups'])})
    order={'POST_MATCH':0,'PRE_MATCH':1,'LIVE':2}
    out.sort(key=lambda x:(order.get(x['phase'],9),-x['priority'],x['kickoff_utc']))
    return out[:int(max_fixtures)]


def monitor_cycle(con, *, raw_dir='data/raw/stats_monitor', now=None, prematch_window_minutes=150, postmatch_delay_minutes=105,
                  postmatch_lookback_hours=18, include_live=False, watchlist_only=False, max_fixtures=12):
    if not os.getenv('API_FOOTBALL_KEY'):
        return {'status':'SKIPPED','reason':'API_FOOTBALL_KEY_NOT_SET','quota':quota_state(con,'API_FOOTBALL'),'targets':[]}
    targets=select_targets(con,now=now,prematch_window_minutes=prematch_window_minutes,postmatch_delay_minutes=postmatch_delay_minutes,
                           postmatch_lookback_hours=postmatch_lookback_hours,include_live=include_live,watchlist_only=watchlist_only,max_fixtures=max_fixtures)
    results=[]; used=0
    for t in targets:
        cost=int(t['estimated_cost'])
        if not can_spend(con,'API_FOOTBALL',cost):
            results.append({**t,'status':'QUOTA_RESERVE'}); break
        try:
            if t['phase']=='PRE_MATCH':
                bundle=fetch_monitor_bundle(con,t['provider_fixture_id'],raw_dir=raw_dir,include_statistics=False,include_players=False,
                                             include_lineups=t['collect_lineups'],include_injuries=t['collect_injuries'])
            elif t['phase']=='LIVE':
                bundle=fetch_monitor_bundle(con,t['provider_fixture_id'],raw_dir=raw_dir,include_statistics=True,include_players=t['collect_players'],
                                             include_lineups=False,include_injuries=False)
            else:
                bundle=fetch_monitor_bundle(con,t['provider_fixture_id'],raw_dir=raw_dir,include_statistics=True,include_players=t['collect_players'],
                                             include_lineups=t['collect_lineups'],include_injuries=False)
            actual=int(bundle.get('request_count',cost)); record_cost(con,'API_FOOTBALL',actual,notes=f"stats_monitor:{t['phase']}:{t['fixture_id']}")
            used += actual; r=ingest_monitor_bundle(con,t['provider_fixture_id'],bundle,observed_at=bundle.get('observed_at'),materialize_final=True)
            results.append({**t,'status':'OK','result':r,'requests':actual})
        except Exception as e:
            results.append({**t,'status':'FAILED','error':type(e).__name__+': '+str(e)})
            from .api_football import source_id_and_capabilities
            sid=source_id_and_capabilities(con)
            con.execute('''INSERT INTO stats_monitor_state(fixture_id,source_id,provider_fixture_key,last_error,updated_at)
                           VALUES(?,?,?,?,?) ON CONFLICT(fixture_id,source_id) DO UPDATE SET last_error=excluded.last_error,updated_at=excluded.updated_at''',
                        (t['fixture_id'],sid,str(t['provider_fixture_id']),results[-1]['error'],utcnow())); con.commit()
    return {'status':'SUCCESS' if any(x.get('status')=='OK' for x in results) else ('IDLE' if not results else 'PARTIAL'),
            'requests_used':used,'targets':results,'quota':quota_state(con,'API_FOOTBALL')}


def status_report(con):
    from .api_football import source_id_and_capabilities
    sid=source_id_and_capabilities(con)
    scalar=lambda q,p=(): con.execute(q,p).fetchone()[0]
    return {
        'api_football_linked_fixtures': scalar('SELECT COUNT(*) FROM fixture_source_links WHERE source_id=?',(sid,)),
        'future_or_recent_api_fixtures': scalar("SELECT COUNT(*) FROM fixtures f JOIN fixture_source_links l ON l.fixture_id=f.fixture_id AND l.source_id=? WHERE datetime(f.kickoff_utc)>=datetime('now','-1 day')",(sid,)),
        'team_stat_snapshots': scalar('SELECT COUNT(*) FROM fixture_stat_snapshots WHERE source_id=?',(sid,)),
        'player_stat_snapshots': scalar('SELECT COUNT(*) FROM player_stat_snapshots WHERE source_id=?',(sid,)),
        'finalized_fixtures': scalar('SELECT COUNT(*) FROM stats_monitor_state WHERE source_id=? AND finalized_at IS NOT NULL',(sid,)),
        'active_watchlist': scalar('SELECT COUNT(*) FROM stats_monitor_watchlist WHERE active=1'),
        'canonical_player_match_rows': scalar('SELECT COUNT(*) FROM player_match_stats'),
        'quota': quota_state(con,'API_FOOTBALL'),
    }
