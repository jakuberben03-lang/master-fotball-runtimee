from __future__ import annotations
from .advanced import ensure_source, register_provider_capability
from .identity import stable_id


def seed_domain_source_catalog(con):
    """Register source capabilities without pretending that a web source is a historical training feed.

    This catalog is descriptive. A source becomes model-usable only after an explicit ingest adapter,
    provenance, timing semantics, coverage audit and validation exist.
    """
    # Czech domestic research history bundled in this build.
    # Keep the same deterministic source identity used by the CZ ingest adapter.
    of = stable_id('source','openfootball-europe')
    con.execute('''INSERT INTO sources(source_id,name,source_type,base_url,authority_rank,usage_notes,reliability_notes)
                   VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET source_type=excluded.source_type,
                   base_url=excluded.base_url,authority_rank=excluded.authority_rank,usage_notes=excluded.usage_notes,
                   reliability_notes=excluded.reliability_notes''',
                (of,'OpenFootball Europe','OPEN_RESULTS_DATA','https://github.com/openfootball/europe',25,
                 'Public historical result-history research source; bundled CZ1 seasons are discontinuous and contain no market odds or advanced event data.',
                 'Useful for fixture/result history only. Never infer complete Czech-league coverage from the repository.'))
    con.commit()
    register_provider_capability(con, of, 'cz_results', 'PARTIAL', competitions=['CZ1'],
                                 seasons=['1819','2021','2324','2425'], timing_granularity='UNKNOWN',
                                 license_class='OPEN_RESEARCH', notes='Result/score/stage history only; no sharp odds, xG, lineup or injury history.')

    chance = ensure_source(
        con, 'Chance Liga Official', 'OFFICIAL_WEB', 'https://www.chanceliga.cz', 1,
        'Primary current-context source for Czech top-flight competition information and official statistics when observable at analysis time.',
        'No bulk historical API/feed is assumed by MASTER. Current web availability does not equal reproducible historical training coverage.'
    )
    for cap in ['current_fixtures','current_team_stats','current_player_stats','current_referee_stats']:
        register_provider_capability(con, chance, cap, 'PARTIAL', competitions=['CZ1'],
                                     timing_granularity='EXACT', license_class='RESTRICTED',
                                     notes='Use for current verification/context only unless a reproducible licensed/history ingest is implemented.')

    uefa = ensure_source(
        con, 'UEFA Official', 'OFFICIAL_WEB', 'https://www.uefa.com', 1,
        'Primary source for current UEFA fixture identity, competition phase/leg/rules and official competition context.',
        'Current official pages are not treated as a complete historical event/odds training feed without an explicit archive/API adapter.'
    )
    for cap in ['current_fixtures','competition_phase','knockout_rules','current_stats']:
        register_provider_capability(con, uefa, cap, 'PARTIAL', competitions=['UCL','UEL','UECL','UCLQ','UELQ','UECLQ'],
                                     timing_granularity='EXACT', license_class='RESTRICTED',
                                     notes='Fixture/phase/rule verification; historical model use requires explicit reproducible ingest.')

    sb = ensure_source(
        con, 'StatsBomb Open Data', 'OPEN_RESEARCH_DATA', 'https://github.com/statsbomb/open-data', 35,
        'Selected competition/season event data for research; coverage is partial and must be checked season-by-season.',
        'Selected historical Champions League seasons/matches exist, but this is not a continuous UEFA qualifying/current archive.'
    )
    for cap in ['events','lineups','xg','shots','player_events']:
        register_provider_capability(con, sb, cap, 'RESEARCH_ONLY', competitions=['SELECTED_STATSBOOMB_OPEN_COMPETITIONS'],
                                     timing_granularity='POST_MATCH', license_class='OPEN_RESEARCH',
                                     notes='Historical actual XI is post-kickoff guarded for pre-match backtests; availability must be verified per season.')
    # External Understat-derived research dataset discovered during xG feature research.
    # It is deliberately registered as UNVERIFIED_RESEARCH and is never a production authority.
    from .understat_research import register_understat_research_source
    understat_research = register_understat_research_source(con)

    # Commercial production candidates. Registration does NOT imply a subscription or ingested coverage.
    toa = ensure_source(
        con, 'The Odds API', 'COMMERCIAL_MARKET_API', 'https://the-odds-api.com', 15,
        'Historical/current odds provider candidate. Credentials are environment-only and are never stored by MASTER.',
        'Historical featured-market snapshots are documented from 2020-06-06; actual bookmaker/market availability must be audited.'
    )
    register_provider_capability(con, toa, 'historical_odds', 'PRODUCTION', timing_granularity='EXACT', license_class='COMMERCIAL',
                                 notes='Paid historical endpoint; snapshots 10m initially and 5m from Sep 2022 according to provider docs.')
    sm = ensure_source(
        con, 'Sportmonks Football API', 'COMMERCIAL_DATA_API', 'https://www.sportmonks.com/football-api/', 10,
        'Production candidate for fixtures, xG, statistics, lineups and referee data. Credentials are environment-only.',
        'Coverage/xG availability depends on subscribed leagues/package and must be audited before model admission.'
    )
    for cap in ['fixtures','match_statistics','lineups','referees']:
        register_provider_capability(con, sm, cap, 'PRODUCTION', timing_granularity='EXACT', license_class='COMMERCIAL',
                                     notes='Provider coverage must be checked per league/season.')
    register_provider_capability(con, sm, 'xg', 'PRODUCTION', timing_granularity='POST_MATCH', license_class='COMMERCIAL',
                                 notes='Post-match xG may feed only future fixture features through shifted as-of snapshots. Provider documentation checked 2026-08-12 indicates xG coverage from 2024/25 to date, so this source alone is insufficient for MASTER multi-season historical xG confirmation.')
    return {'openfootball':of,'chance_liga':chance,'uefa':uefa,'statsbomb_open':sb,'understat_research':understat_research,'the_odds_api':toa,'sportmonks':sm}
