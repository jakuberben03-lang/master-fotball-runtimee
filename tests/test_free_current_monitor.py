from pathlib import Path
from master_data.db import init_db
from master_data.ingest import bootstrap_reference_data
from master_data.free_current_monitor import parse_openfootball_schedule, ingest_openfootball_current_text, OPENFOOTBALL_CURRENT_SPECS, _candidate_exact

SAMPLE='''= English Premier League 2026/27\n\n▪ Matchday 1\n  Fri Aug 21 2026\n    20:00  Arsenal FC              v Coventry City FC\n  Sat Aug 22\n    12:30  Hull City AFC           v Manchester United FC\n    15:00  Ipswich Town FC         v Sunderland AFC\n           Nottingham Forest FC    v Leeds United FC\n'''

def test_parse_schedule():
    rows=parse_openfootball_schedule(SAMPLE,'Europe/London')
    assert len(rows)==4
    assert rows[0]['kickoff_precision']=='EXACT'
    assert rows[3]['kickoff_precision']=='INHERITED_SAME_BLOCK'
    assert rows[0]['status']=='NS'

def test_ingest_fixture_catalog(tmp_path):
    con=init_db(tmp_path/'m.db'); bootstrap_reference_data(con)
    r=ingest_openfootball_current_text(con,OPENFOOTBALL_CURRENT_SPECS[0],SAMPLE,observed_at='2026-08-14T07:00:00Z',raw_locator='test')
    assert r['inserted']==4
    assert con.execute("select count(*) from fixtures where status='NS'").fetchone()[0]==4
    assert con.execute('select count(*) from fixture_source_links').fetchone()[0]==4

def test_declared_alias_verification():
    fx={'kickoff_utc':'2026-08-21T19:00:00Z','home_name':'Arsenal FC','away_name':'Coventry City FC'}
    ev={'dateEvent':'2026-08-21','strSport':'Soccer','strHomeTeam':'Arsenal','strAwayTeam':'Coventry City'}
    assert _candidate_exact(ev,fx)
    ev['strAwayTeam']='Chelsea'
    assert not _candidate_exact(ev,fx)

def test_tsdb_team_stats_and_lineup_snapshot(tmp_path):
    from master_data.free_current_monitor import _tsdb_source, _insert_tsdb_stats, _insert_tsdb_lineups
    con=init_db(tmp_path/'m2.db'); bootstrap_reference_data(con)
    ingest_openfootball_current_text(con,OPENFOOTBALL_CURRENT_SPECS[0],SAMPLE,observed_at='2026-08-14T07:00:00Z',raw_locator='test')
    fx=con.execute('select fixture_id,kickoff_utc from fixtures order by kickoff_utc limit 1').fetchone()
    sid=_tsdb_source(con)
    stats={'eventstats':[{'strStat':'Shots on Goal','intHome':'5','intAway':'2'},{'strStat':'Total Shots','intHome':'11','intAway':'8'},{'strStat':'Blocked Shots','intHome':'3','intAway':'1'}]}
    assert _insert_tsdb_stats(con,sid,fx['fixture_id'],'99',stats,'2026-08-21T21:30:00Z')==2
    assert con.execute('select count(*) from fixture_stat_snapshots').fetchone()[0]==2
    lineup={'lineup':[{'idPlayer':'1','strPlayer':'Test Home','strHome':'Yes','strSubstitute':'No','strPosition':'Midfielder','intSquadNumber':'8'}, {'idPlayer':'2','strPlayer':'Test Away','strHome':'No','strSubstitute':'No','strPosition':'Defender','intSquadNumber':'4'}]}
    assert _insert_tsdb_lineups(con,sid,fx['fixture_id'],'99',lineup,'2026-08-21T18:00:00Z')==2
    assert con.execute("select count(*) from lineup_snapshots where lineup_status='CONFIRMED'").fetchone()[0]==2
