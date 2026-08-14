from __future__ import annotations
from pathlib import Path
import sqlite3

ROOT=Path(__file__).resolve().parents[1]
SCHEMA_VERSION='2.4.0'


def connect(db_path: str|Path):
    p=Path(db_path)
    if not p.is_absolute(): p=ROOT/p
    p.parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(p)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    con.execute('PRAGMA busy_timeout=5000')
    return con


def _columns(con, table):
    return {r['name'] for r in con.execute(f'PRAGMA table_info({table})').fetchall()}


def _add_column_if_missing(con, table, name, ddl):
    if name not in _columns(con, table):
        con.execute(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}')
        return 1
    return 0


def migrate_schema(con):
    """Idempotent forward-only migrations for databases created by older MASTER builds.

    SQLite CREATE TABLE IF NOT EXISTS does not add columns to an existing table. Migrations therefore
    must be explicit. They never fabricate historical timestamps or rewrite prior observations.
    """
    changes=0
    changes += _add_column_if_missing(con,'odds_snapshots','provider_event_id','TEXT')
    changes += _add_column_if_missing(con,'odds_snapshots','requested_snapshot_at','TEXT')
    changes += _add_column_if_missing(con,'odds_snapshots','snapshot_basis','TEXT')
    changes += _add_column_if_missing(con,'odds_snapshots','bookmaker_last_update','TEXT')
    changes += _add_column_if_missing(con,'odds_snapshots','participant_type','TEXT')
    changes += _add_column_if_missing(con,'odds_snapshots','participant_name','TEXT')
    changes += _add_column_if_missing(con,'odds_snapshots','participant_key',"TEXT NOT NULL DEFAULT ''")
    changes += _add_column_if_missing(con,'odds_snapshots','provider_participant_key','TEXT')
    changes += _add_column_if_missing(con,'odds_snapshots','exchange_market_id','TEXT')
    changes += _add_column_if_missing(con,'odds_snapshots','exchange_runner_id','TEXT')
    changes += _add_column_if_missing(con,'odds_snapshots','source_tier','TEXT')
    changes += _add_column_if_missing(con,'odds_snapshots','price_age_seconds','REAL')
    changes += _add_column_if_missing(con,'odds_snapshots','pair_skew_seconds','REAL')
    changes += _add_column_if_missing(con,'odds_snapshots','runner_traded_volume','REAL')
    changes += _add_column_if_missing(con,'external_fixture_observations','source_event_date','TEXT')
    changes += _add_column_if_missing(con,'external_fixture_observations','source_event_time','TEXT')
    changes += _add_column_if_missing(con,'external_fixture_observations','event_temporal_relation',"TEXT NOT NULL DEFAULT 'UNKNOWN'")
    changes += _add_column_if_missing(con,'external_odds_observations','event_temporal_relation',"TEXT NOT NULL DEFAULT 'UNKNOWN'")
    con.execute('''CREATE TABLE IF NOT EXISTS provider_fetch_log (
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
    )''')
    con.executescript("""
    CREATE TABLE IF NOT EXISTS external_fixture_observations (
        external_fixture_observation_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(source_id),
        source_fixture_key TEXT NOT NULL, competition_hint TEXT, kickoff_utc TEXT, source_event_date TEXT, source_event_time TEXT, event_temporal_relation TEXT NOT NULL DEFAULT 'UNKNOWN', home_name TEXT NOT NULL, away_name TEXT NOT NULL,
        observed_at TEXT NOT NULL, timing_quality TEXT NOT NULL DEFAULT 'FETCH_TIME_ONLY', raw_locator TEXT, raw_json TEXT NOT NULL DEFAULT '{}',
        linked_fixture_id TEXT REFERENCES fixtures(fixture_id), UNIQUE(source_id, source_fixture_key, observed_at));
    CREATE TABLE IF NOT EXISTS external_odds_observations (
        external_odds_observation_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES sources(source_id), source_fixture_key TEXT NOT NULL,
        bookmaker TEXT NOT NULL, market_family TEXT NOT NULL, market_key TEXT NOT NULL, selection_key TEXT NOT NULL, line REAL,
        line_key TEXT NOT NULL DEFAULT '', decimal_odds REAL NOT NULL CHECK(decimal_odds > 1.0), participant_type TEXT, participant_name TEXT,
        participant_key TEXT NOT NULL DEFAULT '', observed_at TEXT NOT NULL, timing_quality TEXT NOT NULL DEFAULT 'FETCH_TIME_ONLY', event_temporal_relation TEXT NOT NULL DEFAULT 'UNKNOWN',
        snapshot_type TEXT NOT NULL DEFAULT 'UNKNOWN', raw_column TEXT, source_record_key TEXT, linked_fixture_id TEXT REFERENCES fixtures(fixture_id),
        UNIQUE(source_id, source_fixture_key, bookmaker, market_key, selection_key, line_key, participant_key, observed_at, raw_column));
    CREATE TABLE IF NOT EXISTS free_collector_runs (
        collector_run_id TEXT PRIMARY KEY, collector_key TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
        status TEXT NOT NULL, requests_used INTEGER NOT NULL DEFAULT 0, observations_written INTEGER NOT NULL DEFAULT 0,
        notes TEXT, metrics_json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS free_quota_ledger (
        ledger_id TEXT PRIMARY KEY, provider_key TEXT NOT NULL, period_key TEXT NOT NULL, observed_at TEXT NOT NULL,
        requests_used INTEGER NOT NULL DEFAULT 0, requests_remaining INTEGER, request_cost INTEGER, source TEXT NOT NULL DEFAULT 'LOCAL_ESTIMATE', notes TEXT,
        UNIQUE(provider_key, period_key, observed_at, source));
    CREATE TABLE IF NOT EXISTS free_source_coverage (
        source_id TEXT NOT NULL REFERENCES sources(source_id), domain_key TEXT NOT NULL, competition_key TEXT NOT NULL DEFAULT '',
        season_key TEXT NOT NULL DEFAULT '', capability_key TEXT NOT NULL, observed_rows INTEGER NOT NULL DEFAULT 0, expected_rows INTEGER,
        coverage_ratio REAL, timing_semantics TEXT NOT NULL DEFAULT 'UNKNOWN', rights_status TEXT NOT NULL DEFAULT 'UNKNOWN',
        model_use_permission TEXT NOT NULL DEFAULT 'RESEARCH_ONLY', measured_at TEXT NOT NULL, notes TEXT,
        PRIMARY KEY(source_id, domain_key, competition_key, season_key, capability_key));
    CREATE TABLE IF NOT EXISTS research_player_match_stats (
        source_id TEXT NOT NULL REFERENCES sources(source_id), source_fixture_key TEXT NOT NULL, source_team_key TEXT NOT NULL,
        source_player_key TEXT NOT NULL, competition_key TEXT, season_key TEXT, kickoff_utc TEXT, team_name TEXT, player_name TEXT,
        started INTEGER, minutes REAL, minutes_quality TEXT NOT NULL DEFAULT 'UNKNOWN', position TEXT, role TEXT,
        shots INTEGER, sot INTEGER, sot_definition TEXT, goals INTEGER, assists INTEGER, fouls_committed INTEGER, fouls_drawn INTEGER,
        yellow INTEGER, red INTEGER, dribbles_attempted INTEGER, dribbles_success INTEGER, crosses INTEGER, event_rows INTEGER,
        license_class TEXT NOT NULL, rights_status TEXT NOT NULL, model_use_permission TEXT NOT NULL DEFAULT 'RESEARCH_ONLY',
        evidence_json TEXT NOT NULL DEFAULT '{}', ingested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        PRIMARY KEY(source_id, source_fixture_key, source_player_key));
    CREATE TABLE IF NOT EXISTS free_watchlist (
        fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE, market_family TEXT NOT NULL,
        priority INTEGER NOT NULL DEFAULT 50, collect_context INTEGER NOT NULL DEFAULT 1, collect_odds INTEGER NOT NULL DEFAULT 1,
        added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')), active INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(fixture_id, market_family));
    """)
    con.executescript("""
    CREATE TABLE IF NOT EXISTS fixture_stat_snapshots (
        stat_snapshot_id TEXT PRIMARY KEY, fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
        team_id TEXT NOT NULL REFERENCES teams(team_id), source_id TEXT NOT NULL REFERENCES sources(source_id), observed_at TEXT NOT NULL,
        phase TEXT NOT NULL CHECK(phase IN ('PRE_MATCH','LIVE','POST_MATCH_FINAL','NON_STANDARD','UNKNOWN')), provider_status TEXT, elapsed INTEGER,
        shots INTEGER,sot INTEGER,blocked_shots INTEGER,fouls INTEGER,corners INTEGER,yellow INTEGER,red INTEGER,xg REAL,possession REAL,
        offsides INTEGER,saves INTEGER,passes INTEGER,passes_accurate INTEGER,raw_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(fixture_id,team_id,source_id,observed_at,phase));
    CREATE TABLE IF NOT EXISTS player_stat_snapshots (
        player_stat_snapshot_id TEXT PRIMARY KEY, fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,
        team_id TEXT NOT NULL REFERENCES teams(team_id), player_id TEXT NOT NULL REFERENCES players(player_id), source_id TEXT NOT NULL REFERENCES sources(source_id),
        observed_at TEXT NOT NULL, phase TEXT NOT NULL CHECK(phase IN ('PRE_MATCH','LIVE','POST_MATCH_FINAL','NON_STANDARD','UNKNOWN')), provider_status TEXT,
        minutes REAL,started INTEGER CHECK(started IN (0,1) OR started IS NULL),position TEXT,shots INTEGER,sot INTEGER,goals INTEGER,assists INTEGER,
        fouls_committed INTEGER,fouls_drawn INTEGER,yellow INTEGER,red INTEGER,dribbles_attempted INTEGER,dribbles_success INTEGER,raw_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(fixture_id,player_id,source_id,observed_at,phase));
    CREATE TABLE IF NOT EXISTS stats_monitor_watchlist (
        fixture_id TEXT PRIMARY KEY REFERENCES fixtures(fixture_id) ON DELETE CASCADE,priority INTEGER NOT NULL DEFAULT 50,
        collect_players INTEGER NOT NULL DEFAULT 1 CHECK(collect_players IN (0,1)),collect_lineups INTEGER NOT NULL DEFAULT 1 CHECK(collect_lineups IN (0,1)),
        collect_injuries INTEGER NOT NULL DEFAULT 1 CHECK(collect_injuries IN (0,1)),collect_live INTEGER NOT NULL DEFAULT 0 CHECK(collect_live IN (0,1)),
        active INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),added_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')));
    CREATE TABLE IF NOT EXISTS stats_monitor_state (
        fixture_id TEXT NOT NULL REFERENCES fixtures(fixture_id) ON DELETE CASCADE,source_id TEXT NOT NULL REFERENCES sources(source_id),provider_fixture_key TEXT NOT NULL,
        last_observed_at TEXT,last_phase TEXT,last_provider_status TEXT,prematch_observed_at TEXT,prematch_lineup_count INTEGER NOT NULL DEFAULT 0,
        live_observed_at TEXT,postmatch_observed_at TEXT,finalized_at TEXT,last_error TEXT,updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        PRIMARY KEY(fixture_id,source_id));
    CREATE INDEX IF NOT EXISTS idx_fixture_stat_snapshots_time ON fixture_stat_snapshots(fixture_id,observed_at);
    CREATE INDEX IF NOT EXISTS idx_player_stat_snapshots_time ON player_stat_snapshots(fixture_id,player_id,observed_at);
    CREATE INDEX IF NOT EXISTS idx_stats_monitor_finalized ON stats_monitor_state(source_id,finalized_at,last_provider_status);
    """)
    con.execute("INSERT INTO schema_meta(key,value) VALUES('schema_version',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')",(SCHEMA_VERSION,))
    con.commit()
    return {'schema_version':SCHEMA_VERSION,'columns_added':changes}


def init_db(db_path: str|Path):
    con=connect(db_path)
    sql=(ROOT/'sql'/'schema_sqlite.sql').read_text(encoding='utf-8')
    con.executescript(sql)
    migrate_schema(con)
    return con
