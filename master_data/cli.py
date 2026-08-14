from __future__ import annotations
import argparse, json
from pathlib import Path
from .db import init_db
from .ingest import bootstrap_reference_data, seed_from_v1, ingest_csv, refresh_football_data
from .registry import import_model_registry
from .features import rebuild_latest_team_features, rebuild_historical_fixture_features
from .export import export_v1_model_csvs
from .audit import audit_database
from .market import normalize_all_odds, register_market_source
from .context import build_fixture_context
from .advanced import ensure_source, register_provider_capability, link_external_fixture, ingest_normalized_provider_json
from .statsbomb_open import ingest_match as ingest_statsbomb_match
from .advanced_features import rebuild_advanced_historical_features
from .openfootball_cz import ingest_openfootball_cz, seed_bundled_cz
from .domains import seed_domain_profiles, domain_gate
from .the_odds_api import fetch_historical_snapshot, fetch_historical_events, fetch_historical_event_odds, ingest_historical_snapshot, historical_snapshot_plan, secondary_snapshot_plan, SECONDARY_MARKETS
from .sportmonks import fetch_fixture as fetch_sportmonks_fixture, ingest_fixture_payload as ingest_sportmonks_fixture_payload
from .understat_research import normalize_shot_csv as normalize_understat_research, write_research_manifest
from .licensed_xg_csv import ingest_licensed_xg_csv
from .api_football import fetch_leagues as fetch_api_football_leagues, coverage_catalog as api_football_coverage_catalog, target_discovery_queries as api_football_targets, fetch_fixture_bundle as fetch_api_football_bundle, ingest_fixture_bundle as ingest_api_football_bundle, fixture_rows_for_linking as api_football_link_rows, backfill_request_plan as api_football_backfill_request_plan, ingest_fixture_catalog as api_football_ingest_fixture_catalog, refresh_fixture_catalog as api_football_refresh_fixture_catalog, bootstrap_default_fixture_catalog as api_football_bootstrap_default_fixture_catalog
from .fixture_linking import stage_fixture_link_proposals, approve_exact_proposals
from .understat_player_research import normalize_player_shot_research, player_research_readiness
from .acquisition import acquisition_plan
from .free_sources import seed_free_source_catalog
from .quota import plan_free_budget
from .free_coverage import rebuild_free_coverage, coverage_matrix
from .free_collector import collect_public_odds, collect_free_cycle, collect_current_featured_odds
from .wyscout_open import dataset_manifest as wyscout_manifest, ingest_competition as ingest_wyscout_competition, ingest_all_big5 as ingest_wyscout_all, validate_dataset as validate_wyscout_dataset, readiness as wyscout_readiness
from .schochastics_open import profile_results as schochastics_profile
from .openfootball_uefa import ingest_openfootball_uefa, seed_bundled_uefa
from .betfair_historical import scan_archives as betfair_scan_archives, stage_archive_links as betfair_stage_links, approve_date_team_time_unknown as betfair_approve_date_links, ingest_archives as betfair_ingest_archives, readiness as betfair_readiness
from .stats_monitor import monitor_cycle as stats_monitor_cycle, status_report as stats_monitor_status, add_watch as stats_monitor_add_watch, remove_watch as stats_monitor_remove_watch, select_targets as stats_monitor_targets, ingest_monitor_bundle as stats_monitor_ingest_bundle

def load_config(root): return json.loads((root/'config.json').read_text(encoding='utf-8'))

def main():
    root=Path(__file__).resolve().parents[1]; cfg=load_config(root)
    ap=argparse.ArgumentParser(prog='master-data',description='MASTER Football Data/Market Engine v2.4 + Stats Monitor v1.0')
    ap.add_argument('--db',default=cfg['database_path'])
    sub=ap.add_subparsers(dest='cmd',required=True)
    sub.add_parser('init')
    p=sub.add_parser('seed-v1'); p.add_argument('--engine-root',required=True); p.add_argument('--registry-csv')
    p=sub.add_parser('ingest-csv'); p.add_argument('path'); p.add_argument('--league-code',required=True); p.add_argument('--season',required=True)
    p=sub.add_parser('ingest-cz-openfootball'); p.add_argument('path'); p.add_argument('--season')
    sub.add_parser('seed-cz-openfootball')
    p=sub.add_parser('domain-gate'); p.add_argument('fixture_id')
    p=sub.add_parser('refresh-football-data'); p.add_argument('--season',default=cfg['football_data']['current_season']); p.add_argument('--codes',default=','.join(cfg['football_data']['codes']))
    sub.add_parser('features')
    sub.add_parser('features-historical')
    sub.add_parser('advanced-features-historical')
    sub.add_parser('market-normalize')
    p=sub.add_parser('market-source'); p.add_argument('bookmaker'); p.add_argument('--role',required=True); p.add_argument('--confidence',default='UNAVAILABLE'); p.add_argument('--notes')
    p=sub.add_parser('provider-source'); p.add_argument('name'); p.add_argument('--type',default='DATA_PROVIDER'); p.add_argument('--base-url'); p.add_argument('--authority-rank',type=int,default=50); p.add_argument('--usage-notes'); p.add_argument('--reliability-notes')
    p=sub.add_parser('provider-capability'); p.add_argument('source_id'); p.add_argument('capability'); p.add_argument('--coverage',default='UNKNOWN'); p.add_argument('--timing',default='UNKNOWN'); p.add_argument('--license',default='UNKNOWN'); p.add_argument('--notes')
    p=sub.add_parser('link-fixture'); p.add_argument('source_id'); p.add_argument('source_fixture_key'); p.add_argument('fixture_id'); p.add_argument('--method',default='MANUAL_VERIFIED'); p.add_argument('--evidence-json',default='{}')
    p=sub.add_parser('provider-json'); p.add_argument('path')
    p=sub.add_parser('statsbomb-research-match'); p.add_argument('--root',required=True); p.add_argument('--match-id',required=True)
    p=sub.add_parser('context'); p.add_argument('fixture_id'); p.add_argument('--asof')
    p=sub.add_parser('odds-api-plan'); p.add_argument('kickoff_iso')
    p=sub.add_parser('odds-api-fetch'); p.add_argument('--sport-key',required=True); p.add_argument('--date',required=True); p.add_argument('--regions',default='eu'); p.add_argument('--markets',default='h2h,totals'); p.add_argument('--raw-dir')
    p=sub.add_parser('odds-api-ingest-json'); p.add_argument('path'); p.add_argument('--snapshot-type',required=True); p.add_argument('--requested-at'); p.add_argument('--basis',default='PROVIDER_HISTORICAL_SNAPSHOT')

    p=sub.add_parser('odds-api-secondary-plan'); p.add_argument('kickoff_iso')
    p=sub.add_parser('odds-api-historical-events'); p.add_argument('--sport-key',required=True); p.add_argument('--date',required=True); p.add_argument('--raw-dir')
    p=sub.add_parser('odds-api-secondary-fetch'); p.add_argument('--sport-key',required=True); p.add_argument('--event-id',required=True); p.add_argument('--date',required=True); p.add_argument('--regions',default='eu'); p.add_argument('--markets',default=','.join(SECONDARY_MARKETS)); p.add_argument('--raw-dir')
    p=sub.add_parser('free-sources')
    p=sub.add_parser('free-budget'); p.add_argument('--shortlist',type=int,default=5)
    p=sub.add_parser('free-public-collect'); p.add_argument('--raw-dir',default='data/raw/free')
    p=sub.add_parser('free-current-odds'); p.add_argument('--sport-keys',required=True); p.add_argument('--regions',default='eu'); p.add_argument('--markets',default='h2h,totals'); p.add_argument('--raw-dir',default='data/raw/free')
    p=sub.add_parser('free-cycle'); p.add_argument('--raw-dir',default='data/raw/free'); p.add_argument('--shortlist-fixtures',default=''); p.add_argument('--odds-sport-keys',default='')
    p=sub.add_parser('free-coverage')
    p=sub.add_parser('wyscout-manifest')
    p=sub.add_parser('wyscout-validate'); p.add_argument('--root',required=True); p.add_argument('--allow-partial',action='store_true')
    p=sub.add_parser('wyscout-ingest'); p.add_argument('--root',required=True); p.add_argument('--competition',required=True)
    p=sub.add_parser('wyscout-ingest-all'); p.add_argument('--root',required=True); p.add_argument('--allow-partial',action='store_true')
    p=sub.add_parser('schochastics-profile'); p.add_argument('path'); p.add_argument('--competition-contains'); p.add_argument('--country-contains')
    p=sub.add_parser('ingest-uefa-openfootball'); p.add_argument('path'); p.add_argument('--season',required=True); p.add_argument('--competition',choices=['cl','el','conf'],default='cl'); p.add_argument('--qualifying',action='store_true')
    sub.add_parser('seed-uefa-openfootball')
    p=sub.add_parser('betfair-scan'); p.add_argument('paths',nargs='+')
    p=sub.add_parser('betfair-stage-links'); p.add_argument('paths',nargs='+')
    p=sub.add_parser('betfair-approve-date-links'); p.add_argument('--acknowledge-canonical-time-unknown',action='store_true')
    p=sub.add_parser('betfair-ingest'); p.add_argument('paths',nargs='+'); p.add_argument('--tier',choices=['BASIC','ADVANCED','PRO'],default='BASIC'); p.add_argument('--snapshots',default='ENTRY,CLOSING'); p.add_argument('--max-age-min',type=float,default=60.0); p.add_argument('--max-skew-min',type=float,default=30.0)
    sub.add_parser('betfair-readiness')
    p=sub.add_parser('acquisition-plan')
    p=sub.add_parser('api-football-targets')
    p=sub.add_parser('api-football-coverage'); p.add_argument('--country'); p.add_argument('--name'); p.add_argument('--league-id',type=int); p.add_argument('--season',type=int); p.add_argument('--raw-dir')
    p=sub.add_parser('api-football-backfill-plan'); p.add_argument('coverage_json'); p.add_argument('--fixture-counts-json')
    p=sub.add_parser('api-football-fetch-bundle'); p.add_argument('provider_fixture_id'); p.add_argument('--raw-dir'); p.add_argument('--no-injuries',action='store_true')
    p=sub.add_parser('api-football-ingest-bundle-json'); p.add_argument('provider_fixture_id'); p.add_argument('path'); p.add_argument('--current-observation',action='store_true')

    p=sub.add_parser('api-football-fixture-catalog-json'); p.add_argument('path')
    p=sub.add_parser('api-football-refresh-catalog'); p.add_argument('--league-id',type=int,required=True); p.add_argument('--season',type=int,required=True); p.add_argument('--raw-dir',default='data/raw/stats_monitor/catalog')
    p=sub.add_parser('stats-monitor-bootstrap-defaults'); p.add_argument('--raw-dir',default='data/raw/stats_monitor/catalog'); p.add_argument('--no-uefa',action='store_true')
    p=sub.add_parser('stats-monitor-add'); p.add_argument('fixture_id'); p.add_argument('--priority',type=int,default=50); p.add_argument('--no-players',action='store_true'); p.add_argument('--no-lineups',action='store_true'); p.add_argument('--no-injuries',action='store_true'); p.add_argument('--live',action='store_true')
    p=sub.add_parser('stats-monitor-remove'); p.add_argument('fixture_id')
    p=sub.add_parser('stats-monitor-targets'); p.add_argument('--now'); p.add_argument('--prematch-min',type=int,default=150); p.add_argument('--post-delay-min',type=int,default=105); p.add_argument('--lookback-hours',type=int,default=18); p.add_argument('--live',action='store_true'); p.add_argument('--watchlist-only',action='store_true'); p.add_argument('--max-fixtures',type=int,default=12)
    p=sub.add_parser('stats-monitor-cycle'); p.add_argument('--raw-dir',default='data/raw/stats_monitor'); p.add_argument('--now'); p.add_argument('--prematch-min',type=int,default=150); p.add_argument('--post-delay-min',type=int,default=105); p.add_argument('--lookback-hours',type=int,default=18); p.add_argument('--live',action='store_true'); p.add_argument('--watchlist-only',action='store_true'); p.add_argument('--max-fixtures',type=int,default=12)
    p=sub.add_parser('stats-monitor-ingest-json'); p.add_argument('provider_fixture_id'); p.add_argument('path'); p.add_argument('--observed-at'); p.add_argument('--no-materialize-final',action='store_true')
    sub.add_parser('stats-monitor-status')

    p=sub.add_parser('api-football-stage-links'); p.add_argument('path')
    p=sub.add_parser('api-football-approve-exact-links')
    p=sub.add_parser('understat-player-research'); p.add_argument('path'); p.add_argument('--out',required=True)
    p=sub.add_parser('sportmonks-fetch-fixture'); p.add_argument('provider_fixture_id'); p.add_argument('--raw-dir')
    p=sub.add_parser('sportmonks-ingest-json'); p.add_argument('path'); p.add_argument('--observed-at')
    p=sub.add_parser('understat-research-normalize'); p.add_argument('path'); p.add_argument('--out',required=True); p.add_argument('--manifest')
    p=sub.add_parser('licensed-xg-csv'); p.add_argument('path'); p.add_argument('--source-name',required=True); p.add_argument('--base-url'); p.add_argument('--rights-verified',action='store_true')
    p=sub.add_parser('export-v1'); p.add_argument('--out',default='data/exports/v1_model_data')
    sub.add_parser('audit')
    a=ap.parse_args(); con=init_db(a.db); bootstrap_reference_data(con)
    if a.cmd=='init': res={'status':'OK','db':a.db}
    elif a.cmd=='seed-v1':
        runs=seed_from_v1(con,a.engine_root); nreg=0
        rp=a.registry_csv or str(Path(a.engine_root)/'model_registry.csv')
        if Path(rp).exists(): nreg=import_model_registry(con,rp)
        nf=rebuild_latest_team_features(con,cfg['feature_set_version'])
        res={'ingests':runs,'registry_rows':nreg,'feature_snapshots':nf,'audit':audit_database(con)}
    elif a.cmd=='ingest-csv': res=ingest_csv(con,a.path,a.league_code,a.season)
    elif a.cmd=='ingest-cz-openfootball': res=ingest_openfootball_cz(con,a.path,a.season)
    elif a.cmd=='seed-cz-openfootball': res={'ingests':seed_bundled_cz(con,root)}
    elif a.cmd=='domain-gate': res=domain_gate(con,a.fixture_id)
    elif a.cmd=='refresh-football-data': res=refresh_football_data(con,a.season,[x.strip() for x in a.codes.split(',') if x.strip()],root/'data'/'raw',cfg['football_data']['base_url'])
    elif a.cmd=='features': res={'feature_snapshots':rebuild_latest_team_features(con,cfg['feature_set_version'])}
    elif a.cmd=='features-historical': res={'pre_fixture_snapshots':rebuild_historical_fixture_features(con,cfg['feature_set_version'])}
    elif a.cmd=='advanced-features-historical': res={'advanced_pre_fixture_snapshots':rebuild_advanced_historical_features(con)}
    elif a.cmd=='market-normalize': res=normalize_all_odds(con)
    elif a.cmd=='market-source': register_market_source(con,a.bookmaker,a.role,a.confidence,a.notes); res={'status':'OK','bookmaker':a.bookmaker}
    elif a.cmd=='provider-source':
        sid=ensure_source(con,a.name,a.type,a.base_url,a.authority_rank,a.usage_notes,a.reliability_notes); res={'status':'OK','source_id':sid}
    elif a.cmd=='provider-capability':
        register_provider_capability(con,a.source_id,a.capability,a.coverage,timing_granularity=a.timing,license_class=a.license,notes=a.notes); res={'status':'OK'}
    elif a.cmd=='link-fixture':
        link_external_fixture(con,a.source_id,a.source_fixture_key,a.fixture_id,a.method,json.loads(a.evidence_json)); res={'status':'OK'}
    elif a.cmd=='provider-json': res=ingest_normalized_provider_json(con,a.path)
    elif a.cmd=='statsbomb-research-match': res=ingest_statsbomb_match(con,a.root,a.match_id)
    elif a.cmd=='context': res=build_fixture_context(con,a.fixture_id,a.asof)
    elif a.cmd=='odds-api-plan': res={'snapshots':historical_snapshot_plan(a.kickoff_iso)}
    elif a.cmd=='odds-api-fetch': res=fetch_historical_snapshot(con,a.sport_key,a.date,regions=a.regions,markets=a.markets,raw_dir=a.raw_dir)
    elif a.cmd=='odds-api-ingest-json':
        doc=json.loads(Path(a.path).read_text(encoding='utf-8')); res=ingest_historical_snapshot(con,doc,snapshot_type=a.snapshot_type,requested_snapshot_at=a.requested_at,snapshot_basis=a.basis)

    elif a.cmd=='odds-api-secondary-plan': res={'snapshots':secondary_snapshot_plan(a.kickoff_iso),'markets':list(SECONDARY_MARKETS)}
    elif a.cmd=='odds-api-historical-events': res=fetch_historical_events(con,a.sport_key,a.date,raw_dir=a.raw_dir)
    elif a.cmd=='odds-api-secondary-fetch': res=fetch_historical_event_odds(con,a.sport_key,a.event_id,a.date,regions=a.regions,markets=a.markets,raw_dir=a.raw_dir)
    elif a.cmd=='free-sources':
        ids=seed_free_source_catalog(con); res={'sources':ids}
    elif a.cmd=='free-budget': res=plan_free_budget(con,a.shortlist)
    elif a.cmd=='free-public-collect': res=collect_public_odds(con,a.raw_dir)
    elif a.cmd=='free-current-odds': res=collect_current_featured_odds(con,[x.strip() for x in a.sport_keys.split(',') if x.strip()],a.raw_dir,regions=a.regions,markets=a.markets)
    elif a.cmd=='free-cycle':
        fids=[x.strip() for x in a.shortlist_fixtures.split(',') if x.strip()]; sports=[x.strip() for x in a.odds_sport_keys.split(',') if x.strip()]
        res=collect_free_cycle(con,a.raw_dir,fids,sports)
    elif a.cmd=='free-coverage': rebuild_free_coverage(con); res={'coverage':coverage_matrix(con)}
    elif a.cmd=='wyscout-manifest': res=wyscout_manifest()
    elif a.cmd=='wyscout-validate': res=validate_wyscout_dataset(a.root,strict_counts=not a.allow_partial)
    elif a.cmd=='wyscout-ingest': res=ingest_wyscout_competition(con,a.root,a.competition); res['readiness']=wyscout_readiness(con)
    elif a.cmd=='wyscout-ingest-all': res=ingest_wyscout_all(con,a.root,strict_counts=not a.allow_partial)
    elif a.cmd=='schochastics-profile': res=schochastics_profile(a.path,competition_contains=a.competition_contains,country_contains=a.country_contains)
    elif a.cmd=='ingest-uefa-openfootball': res=ingest_openfootball_uefa(con,a.path,a.season,qualifying=a.qualifying,competition_key=a.competition)
    elif a.cmd=='seed-uefa-openfootball': res={'ingests':seed_bundled_uefa(con,root)}
    elif a.cmd=='betfair-scan': res=betfair_scan_archives(a.paths)
    elif a.cmd=='betfair-stage-links': res=betfair_stage_links(con,a.paths)
    elif a.cmd=='betfair-approve-date-links': res=betfair_approve_date_links(con,acknowledge=a.acknowledge_canonical_time_unknown)
    elif a.cmd=='betfair-ingest':
        res=betfair_ingest_archives(con,a.paths,tier=a.tier,snapshot_types=[x.strip() for x in a.snapshots.split(',') if x.strip()],max_price_age_minutes=a.max_age_min,max_pair_skew_minutes=a.max_skew_min)
    elif a.cmd=='betfair-readiness': res=betfair_readiness(con)
    elif a.cmd=='acquisition-plan': res=acquisition_plan()
    elif a.cmd=='api-football-targets': res={'queries':api_football_targets()}
    elif a.cmd=='api-football-coverage':
        doc=fetch_api_football_leagues(con,country=a.country,name=a.name,league_id=a.league_id,season=a.season,raw_dir=a.raw_dir); res={'coverage':api_football_coverage_catalog(doc)}
    elif a.cmd=='api-football-backfill-plan':
        cov=json.loads(Path(a.coverage_json).read_text(encoding='utf-8')); cov=cov.get('coverage',cov) if isinstance(cov,dict) else cov
        counts={}
        if a.fixture_counts_json:
            raw=json.loads(Path(a.fixture_counts_json).read_text(encoding='utf-8'))
            for k,v in raw.items():
                lid,season=k.split(':',1); counts[(int(lid),int(season))]=int(v)
        res=api_football_backfill_request_plan(cov,counts)
    elif a.cmd=='api-football-fetch-bundle': res=fetch_api_football_bundle(con,a.provider_fixture_id,raw_dir=a.raw_dir,include_injuries=not a.no_injuries)
    elif a.cmd=='api-football-ingest-bundle-json':
        doc=json.loads(Path(a.path).read_text(encoding='utf-8')); res=ingest_api_football_bundle(con,a.provider_fixture_id,doc,historical_backfill=not a.current_observation)

    elif a.cmd=='api-football-fixture-catalog-json':
        doc=json.loads(Path(a.path).read_text(encoding='utf-8')); res=api_football_ingest_fixture_catalog(con,doc)
    elif a.cmd=='api-football-refresh-catalog': res=api_football_refresh_fixture_catalog(con,a.league_id,a.season,raw_dir=a.raw_dir)
    elif a.cmd=='stats-monitor-bootstrap-defaults': res=api_football_bootstrap_default_fixture_catalog(con,raw_dir=a.raw_dir,include_uefa=not a.no_uefa)
    elif a.cmd=='stats-monitor-add': res=stats_monitor_add_watch(con,a.fixture_id,priority=a.priority,collect_players=not a.no_players,collect_lineups=not a.no_lineups,collect_injuries=not a.no_injuries,collect_live=a.live)
    elif a.cmd=='stats-monitor-remove': res=stats_monitor_remove_watch(con,a.fixture_id)
    elif a.cmd=='stats-monitor-targets': res={'targets':stats_monitor_targets(con,now=a.now,prematch_window_minutes=a.prematch_min,postmatch_delay_minutes=a.post_delay_min,postmatch_lookback_hours=a.lookback_hours,include_live=a.live,watchlist_only=a.watchlist_only,max_fixtures=a.max_fixtures)}
    elif a.cmd=='stats-monitor-cycle': res=stats_monitor_cycle(con,raw_dir=a.raw_dir,now=a.now,prematch_window_minutes=a.prematch_min,postmatch_delay_minutes=a.post_delay_min,postmatch_lookback_hours=a.lookback_hours,include_live=a.live,watchlist_only=a.watchlist_only,max_fixtures=a.max_fixtures)
    elif a.cmd=='stats-monitor-ingest-json':
        doc=json.loads(Path(a.path).read_text(encoding='utf-8')); res=stats_monitor_ingest_bundle(con,a.provider_fixture_id,doc,observed_at=a.observed_at,materialize_final=not a.no_materialize_final)
    elif a.cmd=='stats-monitor-status': res=stats_monitor_status(con)

    elif a.cmd=='api-football-stage-links':
        from .api_football import source_id_and_capabilities as af_source
        doc=json.loads(Path(a.path).read_text(encoding='utf-8')); res=stage_fixture_link_proposals(con,af_source(con),api_football_link_rows(doc))
    elif a.cmd=='api-football-approve-exact-links':
        from .api_football import source_id_and_capabilities as af_source
        res=approve_exact_proposals(con,af_source(con))
    elif a.cmd=='understat-player-research':
        frame=normalize_player_shot_research(a.path); frame.to_csv(a.out,index=False); res=player_research_readiness(frame); res['out']=a.out
    elif a.cmd=='sportmonks-fetch-fixture': res=fetch_sportmonks_fixture(con,a.provider_fixture_id,raw_dir=a.raw_dir)
    elif a.cmd=='sportmonks-ingest-json':
        doc=json.loads(Path(a.path).read_text(encoding='utf-8')); res=ingest_sportmonks_fixture_payload(con,doc,observed_at=a.observed_at)
    elif a.cmd=='understat-research-normalize':
        frame=normalize_understat_research(a.path,a.out); manifest=a.manifest or (str(a.out)+'.manifest.json'); res=write_research_manifest(frame,manifest); res.update({'normalized_path':str(a.out),'manifest_path':manifest})
    elif a.cmd=='licensed-xg-csv': res=ingest_licensed_xg_csv(con,a.path,a.source_name,a.base_url,a.rights_verified)
    elif a.cmd=='export-v1': res={'files':export_v1_model_csvs(con,root/a.out if not Path(a.out).is_absolute() else a.out)}
    elif a.cmd=='audit': res=audit_database(con)
    print(json.dumps(res,indent=2,ensure_ascii=False,default=str))

if __name__=='__main__': main()
