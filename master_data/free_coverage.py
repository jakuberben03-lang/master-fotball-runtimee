from __future__ import annotations
from .provider_fetch import utcnow
from .free_sources import seed_free_source_catalog


def rebuild_free_coverage(con):
    src=seed_free_source_catalog(con); now=utcnow(); rows=[]
    def put(source_id,domain,comp,season,cap,obs,exp=None,timing='UNKNOWN',rights='UNKNOWN',perm='RESEARCH_ONLY',notes=None):
        ratio=(obs/exp) if exp and exp>0 else None
        con.execute('''INSERT OR REPLACE INTO free_source_coverage(source_id,domain_key,competition_key,season_key,capability_key,observed_rows,expected_rows,
          coverage_ratio,timing_semantics,rights_status,model_use_permission,measured_at,notes) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)''',
          (source_id,domain,comp,season,cap,int(obs),exp,ratio,timing,rights,perm,now,notes)); rows.append((domain,cap,obs,exp,ratio))
    # Canonical foundation. Fixture-level domain overrides take precedence over competition-level assignment.
    def domain_count(key):
        return con.execute('''SELECT COUNT(*) n FROM fixtures f
          LEFT JOIN fixture_domain_assignments fda ON fda.fixture_id=f.fixture_id
          LEFT JOIN competition_domain_assignments cda ON cda.competition_id=f.competition_id
          WHERE COALESCE(fda.domain_key,cda.domain_key)=?''',(key,)).fetchone()['n']
    b5=domain_count('BIG5_DOMESTIC'); cz=domain_count('CZ_FIRST_LEAGUE')
    ul=domain_count('UEFA_LEAGUE_PHASE'); uk=domain_count('UEFA_KNOCKOUT'); uq=domain_count('UEFA_QUALIFYING')
    put(src['football_data_public'],'BIG5_DOMESTIC','','','historical_fixture_stats',b5,None,'SOURCE_CLASSIFIED','PUBLIC_FREE','CANONICAL_BASELINE')
    put(src['football_data_public'],'CZ_FIRST_LEAGUE','','','historical_fixture_stats',0,None,'SOURCE_CLASSIFIED','PUBLIC_FREE','NONE','Football-Data main set does not cover CZ top flight in canonical history.')
    put(src['openfootball_europe'],'CZ_FIRST_LEAGUE','CZ1','','open_result_fixtures',cz,None,'SOURCE_CLASSIFIED','VERIFIED_CC0_PUBLIC_DOMAIN','RESEARCH_ONLY','Bundled partial Czech First League result history; separate CZ market/player validation remains mandatory.')
    # UEFA coverage by actual source/competition. Domain totals above remain useful, but source-level rows prevent
    # a UCL file from being reported as UEL/UECL coverage or vice versa.
    for sk,code,source_key in [('UCL','UCL','openfootball_ucl'),('UEL','UEL','openfootball_uel'),('UECL','UECL','openfootball_uecl')]:
        sid=src[source_key]
        for domain in ['UEFA_LEAGUE_PHASE','UEFA_KNOCKOUT','UEFA_QUALIFYING']:
            n=con.execute('''SELECT COUNT(*) n FROM fixtures f JOIN competitions c ON c.competition_id=f.competition_id
              LEFT JOIN fixture_domain_assignments d ON d.fixture_id=f.fixture_id
              WHERE f.source_id=? AND COALESCE(d.domain_key,'')=?''',(sid,domain)).fetchone()['n']
            comp_key=sk+'Q' if domain=='UEFA_QUALIFYING' else sk
            put(sid,domain,comp_key,'','open_result_fixtures',n,None,'SOURCE_CLASSIFIED','VERIFIED_CC0_PUBLIC_DOMAIN','RESEARCH_ONLY',
                'Played results only; administrative awards/cancellations excluded. Canonical goals use 90-minute regulation score; AET/shootout retained in source evidence.')
    # Own recorder
    pub=con.execute('SELECT COUNT(DISTINCT source_fixture_key) n FROM external_fixture_observations WHERE source_id=?',(src['football_data_public'],)).fetchone()['n']
    po=con.execute('SELECT COUNT(*) n FROM external_odds_observations WHERE source_id=?',(src['football_data_public'],)).fetchone()['n']
    pp=con.execute("SELECT COUNT(*) n FROM external_odds_observations WHERE source_id=? AND event_temporal_relation='PRE_EVENT_DATE'",(src['football_data_public'],)).fetchone()['n'] if 'event_temporal_relation' in {r['name'] for r in con.execute('PRAGMA table_info(external_odds_observations)')} else 0
    put(src['football_data_public'],'MULTI_DOMAIN','','','own_public_fixture_snapshots',pub,None,'FETCH_TIME_ONLY','PUBLIC_FREE','RECORDER_ONLY')
    put(src['football_data_public'],'MULTI_DOMAIN','','','own_public_odds_snapshots',po,None,'FETCH_TIME_ONLY','PUBLIC_FREE','RECORDER_ONLY')
    put(src['football_data_public'],'MULTI_DOMAIN','','','own_public_prematch_odds_snapshots',pp,None,'FETCH_TIME_ONLY','PUBLIC_FREE','RECORDER_ONLY','Only event dates strictly after recorder date; same-day rows remain temporally ambiguous.')
    # The Odds API own current recorder history
    toa=con.execute('SELECT COUNT(DISTINCT source_fixture_key) fixtures,COUNT(*) odds,MIN(observed_at) first_obs,MAX(observed_at) last_obs FROM external_odds_observations WHERE source_id=?',(src['the_odds_api_free'],)).fetchone()
    put(src['the_odds_api_free'],'MULTI_DOMAIN','','','own_exact_current_odds_rows',toa['odds'],None,'EXACT_IF_PROVIDER_LAST_UPDATE','FREE_TIER_RESTRICTED','RECORDER_ONLY',f"fixtures={toa['fixtures']}; window={toa['first_obs']}..{toa['last_obs']}")
    # Wyscout open actual research
    wy=con.execute('SELECT COUNT(*) rows,COUNT(DISTINCT source_fixture_key) matches FROM research_player_match_stats WHERE source_id=?',(src['wyscout_open'],)).fetchone()
    put(src['wyscout_open'],'BIG5_DOMESTIC','BIG5','2017/18','player_match_history',wy['rows'],None,'POST_MATCH','VERIFIED_OPEN_CC_BY_4_0','RESEARCH_ALLOWED_WITH_ATTRIBUTION')
    put(src['wyscout_open'],'BIG5_DOMESTIC','BIG5','2017/18','matches_with_player_history',wy['matches'],1826,'POST_MATCH','VERIFIED_OPEN_CC_BY_4_0','RESEARCH_ALLOWED_WITH_ATTRIBUTION')
    # CZ bundled result history
    put(src['schochastics'],'CZ_FIRST_LEAGUE','','','broad_open_results_not_yet_ingested',0,None,'POST_MATCH','VERIFIED_ODC_ATTRIBUTION','RESEARCH_ONLY')
    # API current archive counts
    lu=con.execute("SELECT COUNT(*) n FROM lineup_snapshots").fetchone()['n']; av=con.execute("SELECT COUNT(*) n FROM player_availability_snapshots").fetchone()['n']; pm=con.execute("SELECT COUNT(*) n FROM player_match_stats").fetchone()['n']
    put(src['api_football_free'],'MULTI_DOMAIN','','','archived_lineup_snapshots',lu,None,'EXACT_IF_COLLECTED_LIVE','FREE_TIER_RESTRICTED','FUTURE_MODEL_IF_PREMATCH')
    put(src['api_football_free'],'MULTI_DOMAIN','','','archived_availability_snapshots',av,None,'EXACT_IF_COLLECTED_LIVE','FREE_TIER_RESTRICTED','FUTURE_MODEL_IF_PREMATCH')
    put(src['api_football_free'],'MULTI_DOMAIN','','','player_match_stats',pm,None,'POST_MATCH','FREE_TIER_RESTRICTED','RESEARCH_OR_FUTURE_MODEL')
    con.commit(); return {'measured_at':now,'rows_written':len(rows),'matrix':rows}

def coverage_matrix(con):
    rs=con.execute('''SELECT s.name source,domain_key,competition_key,season_key,capability_key,observed_rows,expected_rows,coverage_ratio,
                      timing_semantics,rights_status,model_use_permission,notes FROM free_source_coverage f JOIN sources s USING(source_id)
                      ORDER BY domain_key,source,capability_key''').fetchall()
    return [dict(r) for r in rs]
