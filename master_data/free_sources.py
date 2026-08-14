from __future__ import annotations
from .advanced import ensure_source, register_provider_capability


def seed_free_source_catalog(con):
    out={}
    fd=ensure_source(con,'Football-Data.co.uk Public','OPEN_PUBLIC_DATA','https://www.football-data.co.uk',20,
        'Free historical results/match stats/main odds plus public upcoming fixture odds files. Public fixture odds are recorder inputs, not exact provider-time observations.',
        'Historical files are useful for league prediction. Current fixture-file prices have provider collection cadence but MASTER records its own fetch time separately.')
    out['football_data_public']=fd
    for cap,scope,timing,notes in [
        ('historical_results','PRODUCTION','UNKNOWN','League results; long history varies by competition.'),
        ('historical_match_stats','PRODUCTION','UNKNOWN','Shots/SOT/corners/fouls/cards/referees where columns exist.'),
        ('historical_main_odds','PRODUCTION','UNKNOWN','1X2, totals and AH columns where available.'),
        ('public_fixture_odds','PARTIAL','UNKNOWN','Upcoming fixture CSVs; record local fetch timestamp, do not fabricate opening/closing timestamp.'),
    ]:
        register_provider_capability(con,fd,cap,scope,timing_granularity=timing,license_class='OPEN_RESEARCH',notes=notes)

    wy=ensure_source(con,'Wyscout Open Event Dataset','OPEN_RESEARCH_DATA','https://figshare.com/collections/Soccer_match_event_dataset/4415000',30,
        'CC BY 4.0 public 2017/18 Big-5 event/match/player dataset. Attribution required.',
        'Actual lineups/substitutions are post-match truth and must not be treated as archived pre-match knowledge.')
    out['wyscout_open']=wy
    for cap in ['matches','players','events','actual_lineups','substitutions','player_minutes_research','shots','fouls','cards','dribbles']:
        register_provider_capability(con,wy,cap,'RESEARCH_ONLY',competitions=['BIG5_2017_18'],seasons=['2017/18'],
                                     timing_granularity='POST_MATCH',license_class='OPEN_RESEARCH',
                                     notes='CC BY 4.0. Model use only through research/validation; actual XI is post-kickoff guarded.')

    ofe=ensure_source(con,'OpenFootball Europe','OPEN_PUBLIC_DATA','https://github.com/openfootball/europe',20,
        'CC0/public-domain European league result schedules. MASTER currently bundles selected Czech First League seasons as an EXPERIMENTAL result-history seed.',
        'Czech coverage is discontinuous; result history does not imply odds, advanced stats, player data or model validation.')
    out['openfootball_europe']=ofe
    register_provider_capability(con,ofe,'cz_result_history','RESEARCH_ONLY',competitions=['CZ_FIRST_LEAGUE'],timing_granularity='UNKNOWN',license_class='OPEN_RESEARCH',
                                 notes='CC0 result-history only; current bundled coverage is partial/discontinuous.')

    # OpenFootball's European-club repository is CC0/public domain. Keep each competition as a distinct source identity
    # so coverage reports cannot accidentally imply that UCL history also covers UEL/UECL.
    for key,name,cap_prefix in [
        ('openfootball_ucl','OpenFootball Champions League','ucl'),
        ('openfootball_uel','OpenFootball Europa League','uel'),
        ('openfootball_uecl','OpenFootball Conference League','uecl')]:
        sid=ensure_source(con,name,'OPEN_PUBLIC_DATA','https://github.com/openfootball/champions-league',18,
            f'CC0/public-domain {name.replace("OpenFootball ","")} result history. Result history only: no odds, xG or archived pre-match context.',
            'Use fixture-level UEFA_LEAGUE_PHASE / UEFA_KNOCKOUT / UEFA_QUALIFYING routing. Administrative/cancelled matches are excluded from performance history; AET is separated from 90-minute result.')
        out[key]=sid
        for cap in [f'{cap_prefix}_results',f'{cap_prefix}_knockout_results',f'{cap_prefix}_qualifying_results']:
            register_provider_capability(con,sid,cap,'RESEARCH_ONLY',timing_granularity='UNKNOWN',license_class='OPEN_RESEARCH',
                                         notes='CC0/public domain result history. Separate domain validation required; never implies historical market or lineup coverage.')
    out['openfootball_uefa']=out['openfootball_ucl']  # backward-compatible alias

    sc=ensure_source(con,'Schochastics Football Data','OPEN_RESULTS_DATA','https://github.com/schochastics/football-data',28,
        'ODC Attribution licensed large historical result dataset, plus partial formations/lineups and goal-time folders.',
        'Coverage is broad but source README warns of errors in older games and team identity changes. Must be filtered/audited per domain.')
    out['schochastics']=sc
    for cap in ['broad_results','uefa_results','cz_results','formations_partial','goal_times_partial']:
        register_provider_capability(con,sc,cap,'RESEARCH_ONLY',timing_granularity='POST_MATCH',license_class='OPEN_RESEARCH',
                                     notes='ODC Attribution licensed; coverage and identity quality must be audited before model use.')

    sb=ensure_source(con,'StatsBomb Open Data','OPEN_RESEARCH_DATA','https://github.com/hudl/open-data',35,
        'Selected open event/lineup/360 data for research and genuine football analytics interest; attribution required by source terms.',
        'Coverage is selective, not a continuous Big-5/CZ/UEFA archive.')
    out['statsbomb_open']=sb
    for cap in ['events','lineups','xg','player_events','360_selected']:
        register_provider_capability(con,sb,cap,'RESEARCH_ONLY',timing_granularity='POST_MATCH',license_class='OPEN_RESEARCH',
                                     notes='Selected seasons only; actual lineups are post-match guarded in backtests.')

    af=ensure_source(con,'API-Football Free Tier','FREE_API','https://www.api-football.com',18,
        'Free tier current/context collector. API key is environment-only. Use quota-aware shortlist collection.',
        '100 requests/day on current free plan; free plan season availability is limited. Coverage must be probed per league-season.')
    out['api_football_free']=af
    for cap in ['fixtures','events','lineups','player_match_stats','injuries','transfers','current_odds']:
        register_provider_capability(con,af,cap,'PARTIAL',timing_granularity='EXACT',license_class='RESTRICTED',
                                     notes='Free API tier; current/pre-match collection is valuable when archived by MASTER. Historical odds are not a deep archive.')

    toa=ensure_source(con,'The Odds API Free Tier','FREE_MARKET_API','https://the-odds-api.com',16,
        'Free current odds collector. API key is environment-only; use strict quota budgets and shortlist only.',
        'Current free plan has limited monthly credits; historical odds are not included in free plan.')
    out['the_odds_api_free']=toa
    register_provider_capability(con,toa,'current_featured_odds','PARTIAL',timing_granularity='EXACT',license_class='RESTRICTED',
                                 notes='Use current /v4/sports/{sport}/odds endpoint; archive responses to build own time series.')
    register_provider_capability(con,toa,'current_event_additional_markets','PARTIAL',timing_granularity='EXACT',license_class='RESTRICTED',
                                 notes='Availability varies by sport/bookmaker; never assume player/corners/cards coverage without returned data.')
    return out
