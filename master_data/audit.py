
from __future__ import annotations
from datetime import datetime, timezone
import json

def audit_database(con):
    out={}
    for name,table in [('fixtures','fixtures'),('team_stats','team_match_stats'),('teams','teams'),('competitions','competitions'),('odds','odds_snapshots'),('features','feature_snapshots'),('predictions','prediction_locks'),('lineup_snapshots','lineup_snapshots'),('availability_snapshots','player_availability_snapshots'),('advanced_metric_observations','fixture_metric_provenance'),('player_match_stats','player_match_stats'),('fixture_link_proposals','fixture_link_proposals')]:
        out[name]=con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    out['date_min']=con.execute('SELECT MIN(kickoff_utc) FROM fixtures').fetchone()[0]
    out['date_max']=con.execute('SELECT MAX(kickoff_utc) FROM fixtures').fetchone()[0]
    out['duplicate_source_fixture_keys']=con.execute('SELECT COUNT(*) FROM (SELECT source_id,source_fixture_key,COUNT(*) n FROM fixtures GROUP BY source_id,source_fixture_key HAVING n>1)').fetchone()[0]
    out['fixtures_without_stats']=con.execute('SELECT COUNT(*) FROM fixtures f LEFT JOIN team_match_stats s USING(fixture_id) WHERE s.fixture_id IS NULL').fetchone()[0]
    out['bad_home_away']=con.execute('SELECT COUNT(*) FROM fixtures WHERE home_team_id=away_team_id').fetchone()[0]
    out['prediction_status_violations']=con.execute("SELECT COUNT(*) FROM prediction_locks WHERE model_status<>'ACTIVE' AND verdict IN ('BET','CORE','SECONDARY','HIGH-ODDS')").fetchone()[0]
    out['computation_provenance_violations']=con.execute("SELECT COUNT(*) FROM prediction_locks WHERE model_probability IS NOT NULL AND computation_status<>'REAL'").fetchone()[0]
    out['odds_unknown_market_timestamp']=con.execute("SELECT COUNT(*) FROM odds_snapshots WHERE observed_at IS NULL OR timestamp_quality='UNKNOWN'").fetchone()[0]
    out['odds_exact_market_timestamp']=con.execute("SELECT COUNT(*) FROM odds_snapshots WHERE observed_at IS NOT NULL AND timestamp_quality='EXACT'").fetchone()[0]
    out['odds_with_no_vig']=con.execute("SELECT COUNT(*) FROM odds_snapshots WHERE no_vig_probability IS NOT NULL").fetchone()[0]
    out['future_lineup_snapshot_violations']=con.execute("SELECT COUNT(*) FROM lineup_snapshots ls JOIN fixtures f USING(fixture_id) WHERE ls.lineup_status IN ('EXPECTED','PREDICTED') AND ls.observed_at>f.kickoff_utc").fetchone()[0]
    out['post_kickoff_availability_snapshots']=con.execute("SELECT COUNT(*) FROM player_availability_snapshots a JOIN fixtures f USING(fixture_id) WHERE a.fixture_id IS NOT NULL AND a.observed_at>=f.kickoff_utc").fetchone()[0]
    out['prematch_availability_snapshots']=con.execute("SELECT COUNT(*) FROM player_availability_snapshots a JOIN fixtures f USING(fixture_id) WHERE a.fixture_id IS NOT NULL AND a.observed_at<f.kickoff_utc").fetchone()[0]
    out['prematch_confirmed_lineup_snapshots']=con.execute("SELECT COUNT(*) FROM lineup_snapshots l JOIN fixtures f USING(fixture_id) WHERE l.lineup_status='CONFIRMED' AND l.observed_at<f.kickoff_utc").fetchone()[0]
    out['secondary_market_odds_rows']=con.execute("SELECT COUNT(*) FROM odds_snapshots WHERE market_family NOT IN ('MAIN','GOALS')").fetchone()[0]
    out['secondary_market_exact_rows']=con.execute("SELECT COUNT(*) FROM odds_snapshots WHERE market_family NOT IN ('MAIN','GOALS') AND timestamp_quality='EXACT' AND observed_at IS NOT NULL").fetchone()[0]
    out['pending_fixture_link_proposals']=con.execute("SELECT COUNT(*) FROM fixture_link_proposals WHERE status='PENDING'").fetchone()[0]
    out['pre_fixture_feature_snapshots']=con.execute("SELECT COUNT(*) FROM feature_snapshots WHERE snapshot_scope='PRE_FIXTURE'").fetchone()[0]
    out['domain_counts']={r[0]:r[1] for r in con.execute('SELECT domain_status,COUNT(*) FROM competitions GROUP BY domain_status')}
    out['model_status_counts']={r[0]:r[1] for r in con.execute('SELECT status,COUNT(*) FROM model_registry GROUP BY status')}
    out['domain_profile_counts']={r[0]:r[1] for r in con.execute('SELECT status,COUNT(*) FROM domain_profiles GROUP BY status')}
    out['competition_domain_assignments']=con.execute('SELECT COUNT(*) FROM competition_domain_assignments').fetchone()[0]
    out['fixture_domain_assignments']=con.execute('SELECT COUNT(*) FROM fixture_domain_assignments').fetchone()[0]
    out['knockout_context_rows']=con.execute('SELECT COUNT(*) FROM knockout_context').fetchone()[0]
    out['cz_first_league_fixtures']=con.execute("SELECT COUNT(*) FROM fixtures f JOIN competitions c USING(competition_id) WHERE c.source_code='CZ1'").fetchone()[0]
    out['cz_first_league_seasons']=con.execute("SELECT COUNT(*) FROM seasons s JOIN competitions c USING(competition_id) WHERE c.source_code='CZ1'").fetchone()[0]
    out['cz_advanced_stats_rows']=con.execute("SELECT COUNT(*) FROM team_match_stats s JOIN fixtures f USING(fixture_id) JOIN competitions c USING(competition_id) WHERE c.source_code='CZ1' AND (s.home_xg IS NOT NULL OR s.home_shots IS NOT NULL OR s.home_corners IS NOT NULL)").fetchone()[0]
    out['cz_odds_rows']=con.execute("SELECT COUNT(*) FROM odds_snapshots o JOIN fixtures f USING(fixture_id) JOIN competitions c USING(competition_id) WHERE c.source_code='CZ1'").fetchone()[0]
    out['uefa_league_phase_fixtures']=con.execute("SELECT COUNT(*) FROM fixture_domain_assignments WHERE domain_key='UEFA_LEAGUE_PHASE'").fetchone()[0]
    out['uefa_knockout_fixtures']=con.execute("SELECT COUNT(*) FROM fixture_domain_assignments WHERE domain_key='UEFA_KNOCKOUT'").fetchone()[0]
    out['uefa_qualifying_fixtures']=con.execute("SELECT COUNT(*) FROM fixture_domain_assignments WHERE domain_key='UEFA_QUALIFYING'").fetchone()[0]
    out['free_external_fixture_observations']=con.execute("SELECT COUNT(*) FROM external_fixture_observations").fetchone()[0]
    out['free_external_odds_observations']=con.execute("SELECT COUNT(*) FROM external_odds_observations").fetchone()[0]
    cols={r[1] for r in con.execute('PRAGMA table_info(external_odds_observations)').fetchall()}
    if 'event_temporal_relation' in cols:
        out['free_external_prematch_odds_rows']=con.execute("SELECT COUNT(*) FROM external_odds_observations WHERE event_temporal_relation='PRE_EVENT_DATE'").fetchone()[0]
        out['free_external_post_event_odds_rows']=con.execute("SELECT COUNT(*) FROM external_odds_observations WHERE event_temporal_relation='POST_EVENT_DATE'").fetchone()[0]
    else:
        out['free_external_prematch_odds_rows']=0; out['free_external_post_event_odds_rows']=0
    out['free_research_player_rows']=con.execute("SELECT COUNT(*) FROM research_player_match_stats").fetchone()[0]
    out['ok']=all(out[k]==0 for k in ['duplicate_source_fixture_keys','fixtures_without_stats','bad_home_away','prediction_status_violations','computation_provenance_violations','future_lineup_snapshot_violations'])
    out['audited_at']=datetime.now(timezone.utc).isoformat().replace('+00:00','Z')
    return out
