from __future__ import annotations
import json


def _latest_lineups(con, fixture_id, cutoff):
    rows=con.execute('''SELECT ls.* FROM lineup_snapshots ls
      JOIN (SELECT team_id,MAX(observed_at) mx FROM lineup_snapshots WHERE fixture_id=? AND observed_at<=? GROUP BY team_id) z
        ON z.team_id=ls.team_id AND z.mx=ls.observed_at
      WHERE ls.fixture_id=? ORDER BY ls.team_id''',(fixture_id,cutoff,fixture_id)).fetchall()
    out=[]
    for r in rows:
        mem=con.execute('''SELECT p.player_id,p.canonical_name,m.is_starting,m.shirt_number,m.position,m.role,m.side,m.captain
          FROM lineup_snapshot_members m JOIN players p USING(player_id) WHERE lineup_snapshot_id=? ORDER BY m.is_starting DESC,p.canonical_name''',(r['lineup_snapshot_id'],)).fetchall()
        out.append({**dict(r),'members':[dict(x) for x in mem]})
    return out


def _availability(con, fixture_id, home_id, away_id, cutoff):
    # Latest observation per player available at cutoff. Fixture-specific rows beat generic team rows through ordering.
    rows=con.execute('''SELECT a.*,p.canonical_name FROM player_availability_snapshots a JOIN players p USING(player_id)
      WHERE a.observed_at<=? AND (a.fixture_id=? OR (a.fixture_id IS NULL AND a.team_id IN (?,?)))
      ORDER BY a.player_id,a.observed_at DESC,CASE WHEN a.fixture_id=? THEN 0 ELSE 1 END''',(cutoff,fixture_id,home_id,away_id,fixture_id)).fetchall()
    seen=set(); out=[]
    for r in rows:
        if r['player_id'] in seen: continue
        seen.add(r['player_id']); out.append(dict(r))
    return out


def _advanced_history_coverage(con, team_id, cutoff, n=10):
    rows=con.execute('''SELECT f.fixture_id,f.kickoff_utc,
      CASE WHEN f.home_team_id=? THEN s.home_xg ELSE s.away_xg END xg,
      CASE WHEN f.home_team_id=? THEN s.home_blocked_shots ELSE s.away_blocked_shots END blocked_shots,
      CASE WHEN f.home_team_id=? THEN s.home_crosses ELSE s.away_crosses END crosses,
      CASE WHEN f.home_team_id=? THEN s.home_box_touches ELSE s.away_box_touches END box_touches,
      CASE WHEN f.home_team_id=? THEN s.home_possession ELSE s.away_possession END possession
      FROM fixtures f JOIN team_match_stats s USING(fixture_id)
      WHERE f.kickoff_utc<? AND (f.home_team_id=? OR f.away_team_id=?)
      ORDER BY f.kickoff_utc DESC LIMIT ?''',(team_id,team_id,team_id,team_id,team_id,cutoff,team_id,team_id,n)).fetchall()
    d={'sample_matches':len(rows)}
    for k in ['xg','blocked_shots','crosses','box_touches','possession']:
        d[k+'_n']=sum(r[k] is not None for r in rows)
        d[k+'_coverage']=0.0 if not rows else d[k+'_n']/len(rows)
    d['source_max_kickoff_utc']=rows[0]['kickoff_utc'] if rows else None
    return d


def build_fixture_context(con, fixture_id: str, asof_utc: str|None=None):
    fx=con.execute('''SELECT f.*,c.name competition,c.domain_status,ht.canonical_name home_team,at.canonical_name away_team,r.canonical_name referee
      FROM fixtures f JOIN competitions c USING(competition_id) JOIN teams ht ON ht.team_id=f.home_team_id JOIN teams at ON at.team_id=f.away_team_id LEFT JOIN referees r USING(referee_id) WHERE fixture_id=?''',(fixture_id,)).fetchone()
    if not fx: raise KeyError(fixture_id)
    cutoff=asof_utc or fx['kickoff_utc']
    feats=con.execute('''SELECT entity_id,features_json,sample_size,source_max_kickoff_utc FROM feature_snapshots
      WHERE fixture_id=? AND snapshot_scope='PRE_FIXTURE' ORDER BY entity_id''',(fixture_id,)).fetchall()
    market=con.execute('''SELECT bookmaker,market_family,market_key,selection_key,line,decimal_odds,observed_at,ingested_at,timestamp_quality,snapshot_type,is_sharp,is_execution,reference_confidence,no_vig_probability,no_vig_method,overround
      FROM odds_snapshots WHERE fixture_id=? AND (observed_at IS NULL OR observed_at<=?) ORDER BY market_key,bookmaker,selection_key''',(fixture_id,cutoff)).fetchall()
    legacy_lineup_n=con.execute('SELECT COUNT(*) FROM lineups WHERE fixture_id=? AND observed_at<=?',(fixture_id,cutoff)).fetchone()[0]
    lineups=_latest_lineups(con,fixture_id,cutoff)
    availability=_availability(con,fixture_id,fx['home_team_id'],fx['away_team_id'],cutoff)
    providers=[dict(r) for r in con.execute('''SELECT s.name,c.* FROM data_provider_capabilities c JOIN sources s USING(source_id) ORDER BY s.name,c.capability_key''').fetchall()]
    advanced_history={
      'home':_advanced_history_coverage(con,fx['home_team_id'],cutoff),
      'away':_advanced_history_coverage(con,fx['away_team_id'],cutoff),
    }
    confirmed_pre=all(any(l['team_id']==tid and l['lineup_status'] in ('CONFIRMED','CORRECTED') for l in lineups)
                      for tid in [fx['home_team_id'],fx['away_team_id']])
    availability_material=[a for a in availability if a['status'] in ('DOUBTFUL','INJURED','SUSPENDED','ILL','RESTED')]
    coverage={
      'pre_fixture_features': 'AVAILABLE' if len(feats)>=2 else 'MISSING',
      'market_prices': 'AVAILABLE' if market else 'MISSING',
      'exact_market_timestamps': 'AVAILABLE' if any(m['timestamp_quality']=='EXACT' for m in market) else 'MISSING',
      'sharp_reference': 'AVAILABLE' if any(m['is_sharp'] for m in market) else 'MISSING',
      'lineups': 'AVAILABLE' if (lineups or legacy_lineup_n) else 'MISSING',
      'confirmed_lineups_pre_cutoff': 'AVAILABLE' if confirmed_pre else 'MISSING',
      'availability_data': 'AVAILABLE' if availability else 'MISSING',
      'referee': 'AVAILABLE' if fx['referee_id'] else 'MISSING',
      'player_data': 'AVAILABLE' if con.execute('SELECT 1 FROM player_match_stats WHERE fixture_id=? LIMIT 1',(fixture_id,)).fetchone() else 'MISSING',
      'historical_xg_both_teams': 'AVAILABLE' if min(advanced_history['home']['xg_n'],advanced_history['away']['xg_n'])>0 else 'MISSING',
      'wide_play_history_both_teams': 'AVAILABLE' if min(advanced_history['home']['crosses_n'],advanced_history['away']['crosses_n'])>0 else 'MISSING',
    }
    blockers=[]
    if fx['domain_status']!='SUPPORTED': blockers.append('OUTSIDE_SUPPORTED_MODEL_DOMAIN')
    if coverage['pre_fixture_features']=='MISSING': blockers.append('NO_PRE_FIXTURE_FEATURE_SNAPSHOT')
    if coverage['sharp_reference']=='MISSING': blockers.append('NO_SHARP_REFERENCE')
    if coverage['exact_market_timestamps']=='MISSING': blockers.append('NO_EXACT_MARKET_TIMESTAMP_FOR_CLV')
    advanced_gates={
      'xg_feature_group_eligible': coverage['historical_xg_both_teams']=='AVAILABLE',
      'lineup_adjustment_eligible': coverage['confirmed_lineups_pre_cutoff']=='AVAILABLE',
      'availability_context_eligible': coverage['availability_data']=='AVAILABLE',
      'player_model_eligible': coverage['player_data']=='AVAILABLE' and coverage['confirmed_lineups_pre_cutoff']=='AVAILABLE',
      'note':'Eligibility means data are present as-of cutoff, NOT that a feature group is validated for model use.'
    }
    return {
      'fixture':{k:fx[k] for k in ['fixture_id','kickoff_utc','competition','domain_status','home_team','away_team','referee','data_freshness','stage','leg','aggregate_home_before','aggregate_away_before']},
      'asof_utc':cutoff,
      'coverage':coverage,
      'hard_blockers':blockers,
      'advanced_feature_gates':advanced_gates,
      'advanced_history_coverage':advanced_history,
      'lineup_snapshots':lineups,
      'material_availability_flags':availability_material,
      'provider_capabilities':providers,
      'team_feature_snapshots':[{'team_id':r['entity_id'],'sample_size':r['sample_size'],'source_max_kickoff_utc':r['source_max_kickoff_utc'],'features':json.loads(r['features_json'])} for r in feats],
      'market_snapshots':[dict(r) for r in market],
      'llm_contract':{
        'may_do':['verify context','explain mechanism','bull/bear','identify conflicts','kill conditions','qualitative flags'],
        'must_not_do':['invent model weights','invent model probability','invent numeric interval','promote PROVISIONAL to BET','treat MARKET_ANCHORED as MODEL_PROBABILITY','turn unvalidated xG/lineup/player context into manual probability adjustments'],
        'advanced_feature_rule':'A feature may enter MODEL PROBABILITY only after timestamp-safe ingestion + feature-group OOS A/B validation + model recomputation.'
      }
    }
