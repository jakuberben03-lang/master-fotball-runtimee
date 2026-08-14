
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    source_type TEXT NOT NULL,
    base_url TEXT,
    authority_rank INTEGER NOT NULL DEFAULT 99,
    usage_notes TEXT,
    reliability_notes TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS market_source_registry (
    bookmaker TEXT PRIMARY KEY,
    role TEXT NOT NULL DEFAULT 'UNCLASSIFIED' CHECK(role IN ('SHARP_REFERENCE','EXECUTION','CONSENSUS','UNCLASSIFIED')),
    reference_confidence TEXT NOT NULL DEFAULT 'UNAVAILABLE' CHECK(reference_confidence IN ('A','B','C','UNAVAILABLE')),
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS ingest_runs (
    ingest_run_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    source_locator TEXT,
    source_checksum TEXT,
    rows_seen INTEGER NOT NULL DEFAULT 0,
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    rows_updated INTEGER NOT NULL DEFAULT 0,
    rows_rejected INTEGER NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS competitions (
    competition_id TEXT PRIMARY KEY,
    source_id TEXT REFERENCES sources(source_id),
    source_code TEXT,
    name TEXT NOT NULL,
    country TEXT,
    tier INTEGER,
    competition_type TEXT NOT NULL DEFAULT 'league',
    domain_status TEXT NOT NULL CHECK(domain_status IN ('SUPPORTED','EXPERIMENTAL','OUT_OF_DOMAIN')),
    model_domain_notes TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    UNIQUE(source_id, source_code)
);

CREATE TABLE IF NOT EXISTS seasons (
    season_id TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL REFERENCES competitions(competition_id),
    season_code TEXT NOT NULL,
    label TEXT NOT NULL,
    start_date TEXT,
    end_date TEXT,
    is_current INTEGER NOT NULL DEFAULT 0 CHECK(is_current IN (0,1)),
    UNIQUE(competition_id, season_code)
);

CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    country TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(canonical_name, country)
);

CREATE TABLE IF NOT EXISTS team_aliases (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    competition_id TEXT REFERENCES competitions(competition_id),
    alias TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    team_id TEXT NOT NULL REFERENCES teams(team_id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(source_id, competition_id, alias_normalized)
);

CREATE TABLE IF NOT EXISTS referees (
    referee_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    country TEXT,
    UNIQUE(canonical_name, country)
);

CREATE TABLE IF NOT EXISTS referee_aliases (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    alias TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    referee_id TEXT NOT NULL REFERENCES referees(referee_id),
    PRIMARY KEY(source_id, alias_normalized)
);

CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    birth_date TEXT,
    country TEXT,
    primary_position TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS player_aliases (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    alias TEXT NOT NULL,
    alias_normalized TEXT NOT NULL,
    player_id TEXT NOT NULL REFERENCES players(player_id),
    PRIMARY KEY(source_id, alias_normalized)
);

CREATE TABLE IF NOT EXISTS fixtures (
    fixture_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_fixture_key TEXT NOT NULL,
    competition_id TEXT NOT NULL REFERENCES competitions(competition_id),
    season_id TEXT NOT NULL REFERENCES seasons(season_id),
    kickoff_utc TEXT NOT NULL,
    home_team_id TEXT NOT NULL REFERENCES teams(team_id),
    away_team_id TEXT NOT NULL REFERENCES teams(team_id),
    referee_id TEXT REFERENCES referees(referee_id),
    status TEXT NOT NULL DEFAULT 'FT',
    round_name TEXT,
    stage TEXT,
    leg INTEGER,
    aggregate_home_before INTEGER,
    aggregate_away_before INTEGER,
    venue TEXT,
    neutral_venue INTEGER NOT NULL DEFAULT 0 CHECK(neutral_venue IN (0,1)),
    data_freshness TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(data_freshness IN ('FRESH','ACCEPTABLE','STALE','UNKNOWN')),
    source_row_hash TEXT NOT NULL,
    first_ingested_at TEXT NOT NULL,
    last_ingested_at TEXT NOT NULL,
    UNIQUE(source_id, source_fixture_key),
    CHECK(home_team_id <> away_team_id)
);

CREATE TABLE IF NOT EXISTS team_match_stats (
    fixture_id TEXT PRIMARY KEY REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    home_goals INTEGER,
    away_goals INTEGER,
    home_ht_goals INTEGER,
    away_ht_goals INTEGER,
    home_shots INTEGER,
    away_shots INTEGER,
    home_sot INTEGER,
    away_sot INTEGER,
    home_fouls INTEGER,
    away_fouls INTEGER,
    home_corners INTEGER,
    away_corners INTEGER,
    home_yellow INTEGER,
    away_yellow INTEGER,
    home_red INTEGER,
    away_red INTEGER,
    home_xg REAL,
    away_xg REAL,
    home_blocked_shots INTEGER,
    away_blocked_shots INTEGER,
    home_crosses INTEGER,
    away_crosses INTEGER,
    home_box_touches INTEGER,
    away_box_touches INTEGER,
    home_possession REAL,
    away_possession REAL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    CHECK(home_goals IS NULL OR home_goals >= 0),
    CHECK(away_goals IS NULL OR away_goals >= 0)
);

CREATE TABLE IF NOT EXISTS source_rows (
    source_row_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    ingest_run_id TEXT REFERENCES ingest_runs(ingest_run_id),
    fixture_id TEXT REFERENCES fixtures(fixture_id),
    source_locator TEXT NOT NULL,
    row_number INTEGER,
    row_hash TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    UNIQUE(source_id, source_locator, row_hash)
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    odds_snapshot_id TEXT PRIMARY KEY,
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    bookmaker TEXT NOT NULL,
    market_family TEXT NOT NULL,
    market_key TEXT NOT NULL,
    selection_key TEXT NOT NULL,
    line REAL,
    line_key TEXT NOT NULL,
    decimal_odds REAL NOT NULL CHECK(decimal_odds > 1.0),
    observed_at TEXT,
    ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    timestamp_quality TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(timestamp_quality IN ('EXACT','APPROXIMATE','SOURCE_CLASSIFIED','UNKNOWN')),
    snapshot_type TEXT NOT NULL CHECK(snapshot_type IN ('OPENING','PRECLOSE','ENTRY','CLOSING','LIVE','UNKNOWN')),
    is_sharp INTEGER NOT NULL DEFAULT 0 CHECK(is_sharp IN (0,1)),
    is_execution INTEGER NOT NULL DEFAULT 0 CHECK(is_execution IN (0,1)),
    reference_confidence TEXT CHECK(reference_confidence IN ('A','B','C','UNAVAILABLE') OR reference_confidence IS NULL),
    no_vig_probability REAL CHECK(no_vig_probability IS NULL OR (no_vig_probability>=0 AND no_vig_probability<=1)),
    no_vig_method TEXT,
    overround REAL,
    raw_column TEXT,
    source_row_hash TEXT,
    provider_event_id TEXT,
    requested_snapshot_at TEXT,
    snapshot_basis TEXT,
    bookmaker_last_update TEXT,
    participant_type TEXT,
    participant_name TEXT,
    participant_key TEXT NOT NULL DEFAULT '',
    provider_participant_key TEXT,
    exchange_market_id TEXT,
    exchange_runner_id TEXT,
    source_tier TEXT,
    price_age_seconds REAL,
    pair_skew_seconds REAL,
    runner_traded_volume REAL,
    UNIQUE(fixture_id, source_id, bookmaker, market_key, selection_key, line_key, snapshot_type, source_row_hash)
);

CREATE TABLE IF NOT EXISTS lineups (
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    team_id TEXT NOT NULL REFERENCES teams(team_id),
    player_id TEXT NOT NULL REFERENCES players(player_id),
    is_starting INTEGER NOT NULL CHECK(is_starting IN (0,1)),
    shirt_number INTEGER,
    position TEXT,
    role TEXT,
    side TEXT,
    lineup_status TEXT NOT NULL DEFAULT 'CONFIRMED',
    source_id TEXT REFERENCES sources(source_id),
    observed_at TEXT NOT NULL,
    PRIMARY KEY(fixture_id, team_id, player_id)
);

CREATE TABLE IF NOT EXISTS player_match_stats (
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    team_id TEXT NOT NULL REFERENCES teams(team_id),
    player_id TEXT NOT NULL REFERENCES players(player_id),
    minutes REAL,
    started INTEGER CHECK(started IN (0,1)),
    position TEXT,
    role TEXT,
    shots INTEGER,
    sot INTEGER,
    goals INTEGER,
    assists INTEGER,
    xg REAL,
    xa REAL,
    fouls_committed INTEGER,
    fouls_drawn INTEGER,
    yellow INTEGER,
    red INTEGER,
    dribbles_attempted INTEGER,
    dribbles_success INTEGER,
    crosses INTEGER,
    box_touches INTEGER,
    PRIMARY KEY(fixture_id, player_id)
);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    feature_snapshot_id TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    competition_id TEXT,
    asof_utc TEXT NOT NULL,
    feature_set_version TEXT NOT NULL,
    features_json TEXT NOT NULL,
    source_max_kickoff_utc TEXT,
    sample_size INTEGER,
    snapshot_scope TEXT NOT NULL DEFAULT 'LATEST' CHECK(snapshot_scope IN ('LATEST','PRE_FIXTURE','MANUAL')),
    fixture_id TEXT REFERENCES fixtures(fixture_id),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(entity_type, entity_id, competition_id, asof_utc, feature_set_version)
);

CREATE TABLE IF NOT EXISTS model_registry (
    model_name TEXT NOT NULL,
    version TEXT NOT NULL,
    market_family TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('ACTIVE','PROVISIONAL','NO MODEL')),
    algorithm TEXT,
    training_window TEXT,
    validation_window TEXT,
    oos_test TEXT,
    supported_domain TEXT,
    reason TEXT,
    last_calibration_at TEXT,
    last_drift_check_at TEXT,
    feature_set_version TEXT,
    PRIMARY KEY(model_name, version)
);

CREATE TABLE IF NOT EXISTS model_validation_evidence (
    evidence_id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    market_family TEXT NOT NULL,
    domain_key TEXT NOT NULL,
    evaluated_at TEXT NOT NULL,
    oos_predictions INTEGER NOT NULL DEFAULT 0,
    walk_forward_folds INTEGER NOT NULL DEFAULT 0,
    brier REAL,
    log_loss REAL,
    calibration_slope REAL,
    calibration_intercept REAL,
    ece REAL,
    market_brier REAL,
    market_log_loss REAL,
    clv_mean REAL,
    clv_evaluable_n INTEGER NOT NULL DEFAULT 0,
    drift_state TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(drift_state IN ('OK','WARNING','FAIL','UNKNOWN')),
    data_pipeline_state TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(data_pipeline_state IN ('OK','FAIL','UNKNOWN')),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(model_name,model_version) REFERENCES model_registry(model_name,version)
);

CREATE TABLE IF NOT EXISTS model_status_history (
    status_event_id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    old_status TEXT,
    new_status TEXT NOT NULL CHECK(new_status IN ('ACTIVE','PROVISIONAL','NO MODEL')),
    changed_at TEXT NOT NULL,
    reason TEXT NOT NULL,
    evidence_id TEXT REFERENCES model_validation_evidence(evidence_id)
);

CREATE TABLE IF NOT EXISTS model_runs (
    model_run_id TEXT PRIMARY KEY,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    run_type TEXT NOT NULL CHECK(run_type IN ('TRAIN','VALIDATE','TEST','RETRAIN','DRIFT_CHECK')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    data_cutoff_utc TEXT,
    feature_set_version TEXT,
    metrics_json TEXT,
    artifact_checksum TEXT,
    status TEXT NOT NULL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS prediction_locks (
    prediction_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id),
    market_family TEXT NOT NULL,
    market_key TEXT NOT NULL,
    selection_key TEXT NOT NULL,
    line REAL,
    model_name TEXT,
    model_version TEXT,
    model_status TEXT NOT NULL CHECK(model_status IN ('ACTIVE','PROVISIONAL','NO MODEL')),
    computation_status TEXT NOT NULL DEFAULT 'NONE' CHECK(computation_status IN ('REAL','MARKET_ANCHORED','NONE')),
    model_probability REAL CHECK(model_probability IS NULL OR (model_probability >=0 AND model_probability <=1)),
    interval_low REAL,
    interval_high REAL,
    fair_odds REAL,
    entry_odds REAL,
    entry_bookmaker TEXT,
    sharp_reference_source TEXT,
    sharp_reference_price REAL,
    sharp_no_vig_probability REAL,
    data_confidence TEXT,
    model_uncertainty TEXT,
    context_uncertainty TEXT,
    mechanism_id TEXT,
    bull TEXT,
    bear TEXT,
    lineup_confirmed INTEGER,
    kill_conditions TEXT,
    discovery_depth TEXT,
    double_counting TEXT,
    stale_price TEXT,
    rule_check TEXT,
    final_hard_gate TEXT,
    verdict TEXT NOT NULL,
    input_snapshot_json TEXT NOT NULL,
    source_ids_json TEXT NOT NULL,
    CHECK(NOT (model_status <> 'ACTIVE' AND verdict IN ('CORE','SECONDARY','HIGH-ODDS','BET'))),
    CHECK(model_probability IS NULL OR computation_status='REAL'),
    CHECK(NOT (computation_status='MARKET_ANCHORED' AND model_probability IS NOT NULL))
);

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    prediction_id TEXT PRIMARY KEY REFERENCES prediction_locks(prediction_id) ON DELETE CASCADE,
    evaluated_at TEXT NOT NULL,
    result TEXT,
    event_occurred INTEGER CHECK(event_occurred IN (0,1) OR event_occurred IS NULL),
    closing_odds REAL,
    closing_no_vig_probability REAL,
    clv_pct REAL,
    brier_score REAL,
    log_loss REAL,
    pnl_units REAL,
    thesis_outcome TEXT,
    analysis_quality TEXT,
    notes_post_hoc TEXT
);

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id TEXT PRIMARY KEY,
    detected_at TEXT NOT NULL,
    severity TEXT NOT NULL CHECK(severity IN ('INFO','WARN','ERROR','BLOCKER')),
    issue_type TEXT NOT NULL,
    source_id TEXT,
    fixture_id TEXT,
    entity_id TEXT,
    details_json TEXT NOT NULL,
    resolved_at TEXT,
    resolution_notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_fixtures_comp_kickoff ON fixtures(competition_id, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_fixtures_home_kickoff ON fixtures(home_team_id, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_fixtures_away_kickoff ON fixtures(away_team_id, kickoff_utc);
CREATE INDEX IF NOT EXISTS idx_odds_fixture_market_time ON odds_snapshots(fixture_id, market_key, observed_at);
CREATE INDEX IF NOT EXISTS idx_odds_fixture_ingested ON odds_snapshots(fixture_id, market_key, ingested_at);
CREATE INDEX IF NOT EXISTS idx_features_fixture_scope ON feature_snapshots(fixture_id, snapshot_scope);
CREATE INDEX IF NOT EXISTS idx_prediction_fixture ON prediction_locks(fixture_id, created_at);
CREATE INDEX IF NOT EXISTS idx_source_rows_fixture ON source_rows(fixture_id);
CREATE INDEX IF NOT EXISTS idx_features_entity_asof ON feature_snapshots(entity_type, entity_id, asof_utc);

CREATE TRIGGER IF NOT EXISTS trg_prediction_locks_no_update
BEFORE UPDATE ON prediction_locks
BEGIN
  SELECT RAISE(ABORT, 'prediction_locks are immutable; write post-match data to prediction_outcomes');
END;

CREATE TRIGGER IF NOT EXISTS trg_prediction_locks_no_delete
BEFORE DELETE ON prediction_locks
BEGIN
  SELECT RAISE(ABORT, 'prediction_locks are immutable');
END;

CREATE VIEW IF NOT EXISTS v_fixture_legacy_model AS
SELECT
  f.fixture_id,
  substr(f.kickoff_utc,1,10) AS Date,
  ht.canonical_name AS HomeTeam,
  at.canonical_name AS AwayTeam,
  s.home_goals AS FTHG, s.away_goals AS FTAG,
  CASE WHEN s.home_goals>s.away_goals THEN 'H' WHEN s.home_goals<s.away_goals THEN 'A' ELSE 'D' END AS FTR,
  s.home_ht_goals AS HTHG, s.away_ht_goals AS HTAG,
  CASE WHEN s.home_ht_goals>s.away_ht_goals THEN 'H' WHEN s.home_ht_goals<s.away_ht_goals THEN 'A' ELSE 'D' END AS HTR,
  r.canonical_name AS Referee,
  s.home_shots AS HS, s.away_shots AS "AS",
  s.home_sot AS HST, s.away_sot AS AST,
  s.home_fouls AS HF, s.away_fouls AS AF,
  s.home_corners AS HC, s.away_corners AS AC,
  s.home_yellow AS HY, s.away_yellow AS AY,
  s.home_red AS HR, s.away_red AS AR,
  c.source_code AS league_code,
  c.name AS competition,
  se.season_code AS season
FROM fixtures f
JOIN team_match_stats s USING(fixture_id)
JOIN teams ht ON ht.team_id=f.home_team_id
JOIN teams at ON at.team_id=f.away_team_id
JOIN competitions c USING(competition_id)
JOIN seasons se USING(season_id)
LEFT JOIN referees r USING(referee_id);

CREATE VIEW IF NOT EXISTS v_team_latest_data AS
SELECT team_id, MAX(kickoff_utc) AS latest_kickoff_utc
FROM (
  SELECT home_team_id AS team_id, kickoff_utc FROM fixtures WHERE status='FT'
  UNION ALL
  SELECT away_team_id AS team_id, kickoff_utc FROM fixtures WHERE status='FT'
) x
GROUP BY team_id;

CREATE VIEW IF NOT EXISTS v_latest_odds AS
SELECT o.*
FROM odds_snapshots o
JOIN (
  SELECT fixture_id, market_key, selection_key, bookmaker, MAX(COALESCE(observed_at,ingested_at)) AS mx
  FROM odds_snapshots GROUP BY fixture_id, market_key, selection_key, bookmaker
) z ON z.fixture_id=o.fixture_id AND z.market_key=o.market_key AND z.selection_key=o.selection_key
   AND z.bookmaker=o.bookmaker AND z.mx=COALESCE(o.observed_at,o.ingested_at);


-- MASTER Data/Market Engine v1.3: advanced/context data layer.
CREATE TABLE IF NOT EXISTS data_provider_capabilities (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    capability_key TEXT NOT NULL,
    coverage_scope TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(coverage_scope IN ('PRODUCTION','PARTIAL','RESEARCH_ONLY','UNKNOWN')),
    supported_competitions_json TEXT NOT NULL DEFAULT '[]',
    supported_seasons_json TEXT NOT NULL DEFAULT '[]',
    timing_granularity TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(timing_granularity IN ('EXACT','DATE_ONLY','POST_MATCH','UNKNOWN')),
    license_class TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(license_class IN ('OPEN_RESEARCH','COMMERCIAL','RESTRICTED','UNKNOWN')),
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(source_id, capability_key)
);

CREATE TABLE IF NOT EXISTS fixture_source_links (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_fixture_key TEXT NOT NULL,
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    link_method TEXT NOT NULL CHECK(link_method IN ('EXPLICIT_ID','MANUAL_VERIFIED','OFFICIAL_MAPPING')),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    linked_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(source_id, source_fixture_key),
    UNIQUE(source_id, fixture_id)
);

CREATE TABLE IF NOT EXISTS fixture_metric_provenance (
    metric_observation_id TEXT PRIMARY KEY,
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    entity_type TEXT NOT NULL CHECK(entity_type IN ('TEAM','PLAYER','REFEREE','FIXTURE')),
    entity_id TEXT NOT NULL,
    team_side TEXT CHECK(team_side IN ('HOME','AWAY') OR team_side IS NULL),
    metric_name TEXT NOT NULL,
    metric_value REAL,
    unit TEXT,
    observed_at TEXT,
    availability_class TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(availability_class IN ('EXACT_TIMESTAMP','POST_MATCH_SOURCE','DATE_ONLY','UNKNOWN')),
    source_locator TEXT,
    source_record_key TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(fixture_id, source_id, entity_type, entity_id, metric_name, source_record_key)
);

CREATE TABLE IF NOT EXISTS lineup_snapshots (
    lineup_snapshot_id TEXT PRIMARY KEY,
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    team_id TEXT NOT NULL REFERENCES teams(team_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    lineup_status TEXT NOT NULL CHECK(lineup_status IN ('EXPECTED','PREDICTED','CONFIRMED','CORRECTED')),
    observed_at TEXT NOT NULL,
    source_locator TEXT,
    source_record_key TEXT,
    confidence TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(confidence IN ('A','B','C','UNKNOWN')),
    formation TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(fixture_id, team_id, source_id, lineup_status, observed_at, source_record_key)
);

CREATE TABLE IF NOT EXISTS lineup_snapshot_members (
    lineup_snapshot_id TEXT NOT NULL REFERENCES lineup_snapshots(lineup_snapshot_id) ON DELETE CASCADE,
    player_id TEXT NOT NULL REFERENCES players(player_id),
    is_starting INTEGER NOT NULL CHECK(is_starting IN (0,1)),
    shirt_number INTEGER,
    position TEXT,
    role TEXT,
    side TEXT,
    captain INTEGER CHECK(captain IN (0,1) OR captain IS NULL),
    PRIMARY KEY(lineup_snapshot_id, player_id)
);

CREATE TABLE IF NOT EXISTS player_availability_snapshots (
    availability_snapshot_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(player_id),
    team_id TEXT REFERENCES teams(team_id),
    fixture_id TEXT REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    status TEXT NOT NULL CHECK(status IN ('AVAILABLE','DOUBTFUL','INJURED','SUSPENDED','ILL','RESTED','UNKNOWN')),
    reason TEXT,
    expected_return TEXT,
    observed_at TEXT NOT NULL,
    effective_from TEXT,
    effective_to TEXT,
    confidence TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(confidence IN ('A','B','C','UNKNOWN')),
    source_locator TEXT,
    source_record_key TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(player_id, source_id, fixture_id, observed_at, source_record_key)
);

CREATE TABLE IF NOT EXISTS squad_memberships (
    squad_membership_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(player_id),
    team_id TEXT NOT NULL REFERENCES teams(team_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    squad_role TEXT,
    shirt_number INTEGER,
    observed_at TEXT NOT NULL,
    source_record_key TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(player_id, team_id, source_id, valid_from, source_record_key)
);

CREATE TABLE IF NOT EXISTS transfer_events (
    transfer_event_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(player_id),
    from_team_id TEXT REFERENCES teams(team_id),
    to_team_id TEXT REFERENCES teams(team_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    event_type TEXT NOT NULL CHECK(event_type IN ('TRANSFER','LOAN_IN','LOAN_OUT','RETURN','RELEASE','SIGNING','OTHER')),
    effective_date TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    source_locator TEXT,
    source_record_key TEXT,
    evidence_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(player_id, source_id, event_type, effective_date, source_record_key)
);

CREATE TABLE IF NOT EXISTS player_feature_snapshots (
    player_feature_snapshot_id TEXT PRIMARY KEY,
    player_id TEXT NOT NULL REFERENCES players(player_id),
    team_id TEXT REFERENCES teams(team_id),
    competition_id TEXT REFERENCES competitions(competition_id),
    asof_utc TEXT NOT NULL,
    feature_set_version TEXT NOT NULL,
    features_json TEXT NOT NULL,
    source_max_kickoff_utc TEXT,
    sample_minutes REAL,
    model_status TEXT NOT NULL DEFAULT 'RESEARCH' CHECK(model_status IN ('RESEARCH','PROVISIONAL','ACTIVE')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    UNIQUE(player_id, team_id, competition_id, asof_utc, feature_set_version)
);

CREATE INDEX IF NOT EXISTS idx_metric_fixture_name ON fixture_metric_provenance(fixture_id, metric_name);
CREATE INDEX IF NOT EXISTS idx_lineup_fixture_team_time ON lineup_snapshots(fixture_id, team_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_availability_fixture_time ON player_availability_snapshots(fixture_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_availability_player_time ON player_availability_snapshots(player_id, observed_at);
CREATE INDEX IF NOT EXISTS idx_transfer_effective ON transfer_events(effective_date, observed_at);
CREATE INDEX IF NOT EXISTS idx_player_feature_asof ON player_feature_snapshots(player_id, asof_utc);

-- v1.4 domain expansion / competition-specific validation layer

-- v1.5 external provider fetch audit. Never store API secrets/tokens here.
CREATE TABLE IF NOT EXISTS provider_fetch_log (
    fetch_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    endpoint_key TEXT NOT NULL,
    request_fingerprint TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    provider_snapshot_at TEXT,
    http_status INTEGER,
    response_sha256 TEXT,
    raw_path TEXT,
    success INTEGER NOT NULL CHECK(success IN (0,1)),
    notes TEXT,
    UNIQUE(source_id, endpoint_key, request_fingerprint, requested_at)
);

CREATE TABLE IF NOT EXISTS domain_profiles (
    domain_key TEXT PRIMARY KEY,
    domain_family TEXT NOT NULL CHECK(domain_family IN ('DOMESTIC_LEAGUE','UEFA_LEAGUE_PHASE','UEFA_KNOCKOUT','UEFA_QUALIFYING','CUP','OTHER')),
    status TEXT NOT NULL CHECK(status IN ('SUPPORTED','EXPERIMENTAL','OUT_OF_DOMAIN')),
    requires_knockout_context INTEGER NOT NULL DEFAULT 0 CHECK(requires_knockout_context IN (0,1)),
    requires_cross_league_strength INTEGER NOT NULL DEFAULT 0 CHECK(requires_cross_league_strength IN (0,1)),
    requires_market_validation INTEGER NOT NULL DEFAULT 1 CHECK(requires_market_validation IN (0,1)),
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS competition_domain_assignments (
    competition_id TEXT PRIMARY KEY REFERENCES competitions(competition_id) ON DELETE CASCADE,
    domain_key TEXT NOT NULL REFERENCES domain_profiles(domain_key),
    assignment_method TEXT NOT NULL DEFAULT 'CONFIG' CHECK(assignment_method IN ('CONFIG','MANUAL_VERIFIED','SOURCE_NATIVE')),
    evidence_json TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS knockout_context (
    fixture_id TEXT PRIMARY KEY REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    tie_id TEXT,
    round_name TEXT,
    phase TEXT CHECK(phase IN ('QUALIFYING','PLAYOFF','LEAGUE_PHASE','GROUP','KNOCKOUT','FINAL','UNKNOWN')),
    leg_number INTEGER,
    legs_total INTEGER,
    aggregate_home_before INTEGER,
    aggregate_away_before INTEGER,
    extra_time_possible INTEGER NOT NULL DEFAULT 0 CHECK(extra_time_possible IN (0,1)),
    penalties_possible INTEGER NOT NULL DEFAULT 0 CHECK(penalties_possible IN (0,1)),
    away_goals_rule_active INTEGER NOT NULL DEFAULT 0 CHECK(away_goals_rule_active IN (0,1)),
    must_score_home INTEGER NOT NULL DEFAULT 0 CHECK(must_score_home IN (0,1)),
    must_score_away INTEGER NOT NULL DEFAULT 0 CHECK(must_score_away IN (0,1)),
    context_observed_at TEXT,
    source_id TEXT REFERENCES sources(source_id),
    source_record_key TEXT,
    evidence_json TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE INDEX IF NOT EXISTS idx_domain_assignment_key ON competition_domain_assignments(domain_key);
CREATE INDEX IF NOT EXISTS idx_knockout_tie ON knockout_context(tie_id);

CREATE TABLE IF NOT EXISTS fixture_time_metadata (
    fixture_id TEXT PRIMARY KEY REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    kickoff_precision TEXT NOT NULL CHECK(kickoff_precision IN ('EXACT','INHERITED_SAME_BLOCK','DATE_ONLY','UNKNOWN')),
    source_timezone TEXT,
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS fixture_domain_assignments (
    fixture_id TEXT PRIMARY KEY REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    domain_key TEXT NOT NULL REFERENCES domain_profiles(domain_key),
    assignment_method TEXT NOT NULL DEFAULT 'RULE' CHECK(assignment_method IN ('RULE','MANUAL_VERIFIED','SOURCE_NATIVE')),
    evidence_json TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_fixture_domain_key ON fixture_domain_assignments(domain_key);

-- v1.7 staged cross-provider fixture linking; proposals never become canonical links automatically.
CREATE TABLE IF NOT EXISTS fixture_link_proposals (
    proposal_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_fixture_key TEXT NOT NULL,
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    match_method TEXT NOT NULL CHECK(match_method IN ('EXACT_KICKOFF_TEAMS','DATE_TEAMS_REVIEW')),
    confidence TEXT NOT NULL CHECK(confidence IN ('A','B','C')),
    evidence_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING','APPROVED','REJECTED')),
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    reviewed_at TEXT,
    UNIQUE(source_id, source_fixture_key, fixture_id)
);


-- MASTER Free Data Engine v2.0: free-source acquisition, own-history recorder and research staging.
CREATE TABLE IF NOT EXISTS external_fixture_observations (
    external_fixture_observation_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_fixture_key TEXT NOT NULL,
    competition_hint TEXT,
    kickoff_utc TEXT,
    source_event_date TEXT,
    source_event_time TEXT,
    event_temporal_relation TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(event_temporal_relation IN ('PRE_EVENT_DATE','POST_EVENT_DATE','SAME_DATE_UNKNOWN','UNKNOWN')),
    home_name TEXT NOT NULL,
    away_name TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    timing_quality TEXT NOT NULL DEFAULT 'FETCH_TIME_ONLY' CHECK(timing_quality IN ('EXACT_SOURCE','SOURCE_CLASSIFIED','FETCH_TIME_ONLY','UNKNOWN')),
    raw_locator TEXT,
    raw_json TEXT NOT NULL DEFAULT '{}',
    linked_fixture_id TEXT REFERENCES fixtures(fixture_id),
    UNIQUE(source_id, source_fixture_key, observed_at)
);

CREATE TABLE IF NOT EXISTS external_odds_observations (
    external_odds_observation_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_fixture_key TEXT NOT NULL,
    bookmaker TEXT NOT NULL,
    market_family TEXT NOT NULL,
    market_key TEXT NOT NULL,
    selection_key TEXT NOT NULL,
    line REAL,
    line_key TEXT NOT NULL DEFAULT '',
    decimal_odds REAL NOT NULL CHECK(decimal_odds > 1.0),
    participant_type TEXT,
    participant_name TEXT,
    participant_key TEXT NOT NULL DEFAULT '',
    observed_at TEXT NOT NULL,
    timing_quality TEXT NOT NULL DEFAULT 'FETCH_TIME_ONLY' CHECK(timing_quality IN ('EXACT_SOURCE','SOURCE_CLASSIFIED','FETCH_TIME_ONLY','UNKNOWN')),
    event_temporal_relation TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(event_temporal_relation IN ('PRE_EVENT_DATE','POST_EVENT_DATE','SAME_DATE_UNKNOWN','UNKNOWN')),
    snapshot_type TEXT NOT NULL DEFAULT 'UNKNOWN' CHECK(snapshot_type IN ('OPENING','PRECLOSE','ENTRY','CLOSING','LIVE','UNKNOWN')),
    raw_column TEXT,
    source_record_key TEXT,
    linked_fixture_id TEXT REFERENCES fixtures(fixture_id),
    UNIQUE(source_id, source_fixture_key, bookmaker, market_key, selection_key, line_key, participant_key, observed_at, raw_column)
);

CREATE TABLE IF NOT EXISTS free_collector_runs (
    collector_run_id TEXT PRIMARY KEY,
    collector_key TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK(status IN ('RUNNING','SUCCESS','PARTIAL','SKIPPED','FAILED')),
    requests_used INTEGER NOT NULL DEFAULT 0,
    observations_written INTEGER NOT NULL DEFAULT 0,
    notes TEXT,
    metrics_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS free_quota_ledger (
    ledger_id TEXT PRIMARY KEY,
    provider_key TEXT NOT NULL,
    period_key TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    requests_used INTEGER NOT NULL DEFAULT 0,
    requests_remaining INTEGER,
    request_cost INTEGER,
    source TEXT NOT NULL DEFAULT 'LOCAL_ESTIMATE',
    notes TEXT,
    UNIQUE(provider_key, period_key, observed_at, source)
);

CREATE TABLE IF NOT EXISTS free_source_coverage (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    domain_key TEXT NOT NULL,
    competition_key TEXT NOT NULL DEFAULT '',
    season_key TEXT NOT NULL DEFAULT '',
    capability_key TEXT NOT NULL,
    observed_rows INTEGER NOT NULL DEFAULT 0,
    expected_rows INTEGER,
    coverage_ratio REAL CHECK(coverage_ratio IS NULL OR (coverage_ratio>=0 AND coverage_ratio<=1)),
    timing_semantics TEXT NOT NULL DEFAULT 'UNKNOWN',
    rights_status TEXT NOT NULL DEFAULT 'UNKNOWN',
    model_use_permission TEXT NOT NULL DEFAULT 'RESEARCH_ONLY',
    measured_at TEXT NOT NULL,
    notes TEXT,
    PRIMARY KEY(source_id, domain_key, competition_key, season_key, capability_key)
);

CREATE TABLE IF NOT EXISTS research_player_match_stats (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    source_fixture_key TEXT NOT NULL,
    source_team_key TEXT NOT NULL,
    source_player_key TEXT NOT NULL,
    competition_key TEXT,
    season_key TEXT,
    kickoff_utc TEXT,
    team_name TEXT,
    player_name TEXT,
    started INTEGER CHECK(started IN (0,1) OR started IS NULL),
    minutes REAL,
    minutes_quality TEXT NOT NULL DEFAULT 'UNKNOWN',
    position TEXT,
    role TEXT,
    shots INTEGER,
    sot INTEGER,
    sot_definition TEXT,
    goals INTEGER,
    assists INTEGER,
    fouls_committed INTEGER,
    fouls_drawn INTEGER,
    yellow INTEGER,
    red INTEGER,
    dribbles_attempted INTEGER,
    dribbles_success INTEGER,
    crosses INTEGER,
    event_rows INTEGER,
    license_class TEXT NOT NULL,
    rights_status TEXT NOT NULL,
    model_use_permission TEXT NOT NULL DEFAULT 'RESEARCH_ONLY',
    evidence_json TEXT NOT NULL DEFAULT '{}',
    ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(source_id, source_fixture_key, source_player_key)
);

CREATE TABLE IF NOT EXISTS free_watchlist (
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    market_family TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 50,
    collect_context INTEGER NOT NULL DEFAULT 1 CHECK(collect_context IN (0,1)),
    collect_odds INTEGER NOT NULL DEFAULT 1 CHECK(collect_odds IN (0,1)),
    added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    PRIMARY KEY(fixture_id, market_family)
);


-- MASTER Stats Monitor v1.0 / Data+Market Engine v2.4
CREATE TABLE IF NOT EXISTS fixture_stat_snapshots (
    stat_snapshot_id TEXT PRIMARY KEY,
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    team_id TEXT NOT NULL REFERENCES teams(team_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    observed_at TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('PRE_MATCH','LIVE','POST_MATCH_FINAL','NON_STANDARD','UNKNOWN')),
    provider_status TEXT,
    elapsed INTEGER,
    shots INTEGER,
    sot INTEGER,
    blocked_shots INTEGER,
    fouls INTEGER,
    corners INTEGER,
    yellow INTEGER,
    red INTEGER,
    xg REAL,
    possession REAL,
    offsides INTEGER,
    saves INTEGER,
    passes INTEGER,
    passes_accurate INTEGER,
    raw_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(fixture_id,team_id,source_id,observed_at,phase)
);

CREATE TABLE IF NOT EXISTS player_stat_snapshots (
    player_stat_snapshot_id TEXT PRIMARY KEY,
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    team_id TEXT NOT NULL REFERENCES teams(team_id),
    player_id TEXT NOT NULL REFERENCES players(player_id),
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    observed_at TEXT NOT NULL,
    phase TEXT NOT NULL CHECK(phase IN ('PRE_MATCH','LIVE','POST_MATCH_FINAL','NON_STANDARD','UNKNOWN')),
    provider_status TEXT,
    minutes REAL,
    started INTEGER CHECK(started IN (0,1) OR started IS NULL),
    position TEXT,
    shots INTEGER,
    sot INTEGER,
    goals INTEGER,
    assists INTEGER,
    fouls_committed INTEGER,
    fouls_drawn INTEGER,
    yellow INTEGER,
    red INTEGER,
    dribbles_attempted INTEGER,
    dribbles_success INTEGER,
    raw_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(fixture_id,player_id,source_id,observed_at,phase)
);

CREATE TABLE IF NOT EXISTS stats_monitor_watchlist (
    fixture_id TEXT PRIMARY KEY REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    priority INTEGER NOT NULL DEFAULT 50,
    collect_players INTEGER NOT NULL DEFAULT 1 CHECK(collect_players IN (0,1)),
    collect_lineups INTEGER NOT NULL DEFAULT 1 CHECK(collect_lineups IN (0,1)),
    collect_injuries INTEGER NOT NULL DEFAULT 1 CHECK(collect_injuries IN (0,1)),
    collect_live INTEGER NOT NULL DEFAULT 0 CHECK(collect_live IN (0,1)),
    active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

CREATE TABLE IF NOT EXISTS stats_monitor_state (
    fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    provider_fixture_key TEXT NOT NULL,
    last_observed_at TEXT,
    last_phase TEXT,
    last_provider_status TEXT,
    prematch_observed_at TEXT,
    prematch_lineup_count INTEGER NOT NULL DEFAULT 0,
    live_observed_at TEXT,
    postmatch_observed_at TEXT,
    finalized_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY(fixture_id,source_id)
);

CREATE INDEX IF NOT EXISTS idx_fixture_stat_snapshots_time ON fixture_stat_snapshots(fixture_id,observed_at);
CREATE INDEX IF NOT EXISTS idx_player_stat_snapshots_time ON player_stat_snapshots(fixture_id,player_id,observed_at);
CREATE INDEX IF NOT EXISTS idx_stats_monitor_finalized ON stats_monitor_state(source_id,finalized_at,last_provider_status);
