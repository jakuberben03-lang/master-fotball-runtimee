from __future__ import annotations

"""Acquisition policy for the three current MASTER expansion tracks.

This is intentionally a policy/target matrix, not a statement that data were fetched.
Provider coverage must always be verified from the provider response/subscription before use.
"""


def acquisition_plan():
    return {
      'contract': {
        'provider_listed': 'DOES_NOT_MEAN_DATA_PRESENT',
        'coverage_probe_required': True,
        'fixture_link_rule': 'EXPLICIT_OR_MANUAL_VERIFIED_ONLY',
        'historical_backfill_lineup_rule': 'NO_PREMATCH_CLAIM_WITHOUT_ARCHIVED_OBSERVED_AT',
        'model_rule': 'DATA_READY != MODEL_ACTIVE',
      },
      'tracks': {
        'PLAYER_LINEUP_INJURY': {
          'primary_candidate': 'API-Football',
          'required_capabilities': ['fixtures','lineups','player_match_stats','injuries'],
          'required_fields_for_player_model_research': [
             'minutes','started','position_or_role','shots','sot','goals','fouls_committed','fouls_drawn','yellow_cards'
          ],
          'prematch_history_requirement': [
             'actual_observed_at_before_kickoff_for_predicted_or_confirmed_XI',
             'actual_observed_at_before_kickoff_for_availability_news'
          ],
          'historical_backfill_use': 'POST_MATCH_PLAYER_HISTORY_ONLY_UNLESS_ARCHIVED_PREMATCH_TIMESTAMP_IS_PROVEN',
          'domains': ['BIG5_DOMESTIC','CZ_FIRST_LEAGUE','UEFA_LEAGUE_PHASE','UEFA_KNOCKOUT','UEFA_QUALIFYING'],
        },
        'SECONDARY_MARKET_HISTORY': {
          'primary_candidate': 'The Odds API historical event odds',
          'provider_declared_soccer_player_prop_scope': 'BIG5_PLUS_MLS_SELECTED_US_BOOKMAKERS_ONLY_CURRENTLY',
          'historical_additional_market_start': '2023-05-03T05:30:00Z',
          'canonical_snapshot_plan': ['T-24H','T-6H','T-60M','T-5M_CLOSING_PROXY'],
          'market_targets': {
             'corners': ['alternate_spreads_corners','alternate_totals_corners','alternate_team_totals_corners','corners_1x2'],
             'cards': ['alternate_spreads_cards','alternate_totals_cards'],
             'player': ['player_shots','player_shots_on_target','player_to_receive_card','player_goal_scorer_anytime','player_assists'],
             'player_scope_warning': ['DO_NOT_ASSUME_UEFA_OR_CZ_PLAYER_PROP_COVERAGE_FROM_BIG5_SUPPORT'],
             'fouls': ['NO_CONFIRMED_THE_ODDS_API_MARKET_IN_CURRENT_ADAPTER'],
          },
          'secondary_reference_candidate': 'Betfair Historical Data (external purchased time-stamped Exchange data; parser not yet canonical)',
          'hard_requirements': ['exact_snapshot_timestamp','same_bookmaker_market_line_participant_snapshot_group','two_sided_no_vig_when_applicable','minimum_two_seasons_for_research_split'],
        },
        'CZ_UEFA_COVERAGE': {
          'CZ_FIRST_LEAGUE': {
             'current_canonical_fixtures': 1136,
             'player_data_candidate': 'API-Football coverage probe for Czech Liga by league-season',
             'historical_secondary_odds': 'UNRESOLVED_LICENSED_SOURCE_REQUIRED',
             'status': 'EXPERIMENTAL',
          },
          'UEFA': {
             'player_data_candidate': 'API-Football coverage probe per UEFA competition-season',
             'odds_api_sport_keys': {
                'UEFA_LEAGUE_PHASE_OR_KNOCKOUT_UCL': 'soccer_uefa_champs_league',
                'UEFA_QUALIFYING_UCL': 'soccer_uefa_champs_league_qualification',
                'UEFA_EUROPA_LEAGUE': 'soccer_uefa_europa_league',
                'UEFA_CONFERENCE_LEAGUE': 'soccer_uefa_europa_conference_league',
             },
             'domain_split_required': ['UEFA_LEAGUE_PHASE','UEFA_KNOCKOUT','UEFA_QUALIFYING'],
             'status': 'EXPERIMENTAL_UNTIL_DOMAIN_VALIDATION',
          },
        },
      }
    }
