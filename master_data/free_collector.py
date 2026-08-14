from __future__ import annotations
import json, os, urllib.request
from pathlib import Path
from datetime import datetime, timezone
from .identity import stable_id
from .provider_fetch import utcnow
from .football_data_public import ingest_public_fixture_csv, MAIN_FIXTURES_URL, EXTRA_FIXTURES_URL
from .quota import plan_free_budget, can_spend, record_cost
from .free_coverage import rebuild_free_coverage


def _download(url,path,timeout=45):
    req=urllib.request.Request(url,headers={'User-Agent':'MASTER-Football-Free-Collector/2.0'})
    with urllib.request.urlopen(req,timeout=timeout) as r: data=r.read()
    Path(path).parent.mkdir(parents=True,exist_ok=True); Path(path).write_bytes(data); return len(data)

def _run_start(con,key):
    now=utcnow(); rid=stable_id('free-collector',key,now)
    con.execute("INSERT INTO free_collector_runs(collector_run_id,collector_key,started_at,status) VALUES(?,?,?,'RUNNING')",(rid,key,now)); con.commit(); return rid,now

def _run_end(con,rid,status,metrics,notes=None):
    con.execute('''UPDATE free_collector_runs SET finished_at=?,status=?,requests_used=?,observations_written=?,notes=?,metrics_json=? WHERE collector_run_id=?''',
                (utcnow(),status,int(metrics.get('requests_used',0)),int(metrics.get('observations_written',0)),notes,json.dumps(metrics,ensure_ascii=False),rid)); con.commit()

def collect_public_odds(con,raw_dir:str|Path):
    rid,obs=_run_start(con,'FOOTBALL_DATA_PUBLIC_ODDS'); raw=Path(raw_dir); metrics={'requests_used':0,'observations_written':0,'files':[]}
    try:
        for key,url,extra in [('main',MAIN_FIXTURES_URL,False),('extra',EXTRA_FIXTURES_URL,True)]:
            p=raw/f'{key}_{obs.replace(":","").replace("-","")}.csv'; n=_download(url,p); metrics['requests_used']+=1
            r=ingest_public_fixture_csv(con,p,observed_at=obs,extra=extra,raw_locator=str(p)); metrics['observations_written']+=r['fixture_observations']+r['odds_observations']; metrics['files'].append({'key':key,'bytes':n,**r})
        _run_end(con,rid,'SUCCESS',metrics); return metrics
    except Exception as e:
        _run_end(con,rid,'FAILED',metrics,type(e).__name__+': '+str(e)); raise

def collect_shortlist_context(con,fixture_ids,raw_dir:str|Path):
    """Use API-Football free tier only for explicitly linked provider fixtures on shortlist.
    Missing API key is a SKIPPED state, never a fatal engine error.
    """
    rid,_=_run_start(con,'API_FOOTBALL_SHORTLIST'); metrics={'requests_used':0,'observations_written':0,'fixtures':[]}
    if not os.getenv('API_FOOTBALL_KEY'):
        _run_end(con,rid,'SKIPPED',metrics,'API_FOOTBALL_KEY_NOT_SET'); return {'status':'SKIPPED','reason':'API_FOOTBALL_KEY_NOT_SET',**metrics}
    from .api_football import source_id_and_capabilities, fetch_fixture_bundle, ingest_fixture_bundle
    sid=source_id_and_capabilities(con)
    for fid in fixture_ids:
        link=con.execute('SELECT source_fixture_key FROM fixture_source_links WHERE source_id=? AND fixture_id=?',(sid,fid)).fetchone()
        if not link: metrics['fixtures'].append({'fixture_id':fid,'status':'NO_EXPLICIT_PROVIDER_LINK'}); continue
        # Existing bundle uses fixture + lineups + players + optional injuries. Budget 4 calls conservatively.
        if not can_spend(con,'API_FOOTBALL',4): metrics['fixtures'].append({'fixture_id':fid,'status':'QUOTA_RESERVE'}); break
        bundle=fetch_fixture_bundle(con,link['source_fixture_key'],raw_dir=raw_dir); metrics['requests_used']+=4; record_cost(con,'API_FOOTBALL',4,notes=f'fixture_bundle:{fid}')
        r=ingest_fixture_bundle(con,link['source_fixture_key'],bundle,historical_backfill=False); metrics['observations_written']+=r.get('player_match_stats',0)+r.get('lineup_snapshots',0)+r.get('availability_snapshots',0); metrics['fixtures'].append({'fixture_id':fid,'status':'OK',**r})
    _run_end(con,rid,'SUCCESS' if metrics['fixtures'] else 'PARTIAL',metrics); return metrics

def collect_free_cycle(con,raw_dir:str|Path,shortlist_fixture_ids=(),odds_sport_keys=()):
    out={'budget_before':plan_free_budget(con,max(1,len(shortlist_fixture_ids))),'public_odds':collect_public_odds(con,raw_dir)}
    if shortlist_fixture_ids: out['context']=collect_shortlist_context(con,list(shortlist_fixture_ids),Path(raw_dir)/'api_football')
    if odds_sport_keys: out['current_odds']=collect_current_featured_odds(con,list(odds_sport_keys),Path(raw_dir)/'current_odds')
    out['coverage']=rebuild_free_coverage(con); out['budget_after']=plan_free_budget(con,max(1,len(shortlist_fixture_ids)))
    return out

def collect_current_featured_odds(con, sport_keys, raw_dir:str|Path, *, regions='eu', markets='h2h,totals'):
    """Quota-aware current odds recorder using The Odds API free tier.

    This builds MASTER's own timestamp history from now forward. It never claims historical API access.
    """
    rid,_=_run_start(con,'THE_ODDS_API_CURRENT'); metrics={'requests_used':0,'observations_written':0,'sports':[]}
    if not os.getenv('THE_ODDS_API_KEY'):
        _run_end(con,rid,'SKIPPED',metrics,'THE_ODDS_API_KEY_NOT_SET')
        return {'status':'SKIPPED','reason':'THE_ODDS_API_KEY_NOT_SET',**metrics}
    from .the_odds_api_free import collect_current_odds
    for sport in sport_keys:
        estimated=2  # one region x two featured markets under current provider cost model
        if not can_spend(con,'THE_ODDS_API',estimated):
            metrics['sports'].append({'sport_key':sport,'status':'QUOTA_RESERVE'}); break
        try:
            r=collect_current_odds(con,sport,regions=regions,markets=markets,raw_dir=Path(raw_dir)/'the_odds_api')
            actual=int(r.get('requests_last') or estimated)
            record_cost(con,'THE_ODDS_API',actual,requests_remaining=r.get('requests_remaining'),source='PROVIDER_HEADER' if r.get('requests_last') is not None else 'LOCAL_ESTIMATE',notes=f'current:{sport}:{markets}')
            metrics['requests_used']+=actual; metrics['observations_written']+=r.get('odds_observations',0)+r.get('fixture_observations',0)
            metrics['sports'].append({'sport_key':sport,'status':'OK','odds_observations':r.get('odds_observations',0),'fixture_observations':r.get('fixture_observations',0),'cost':actual})
        except Exception as e:
            metrics['sports'].append({'sport_key':sport,'status':'FAILED','error':type(e).__name__+': '+str(e)})
    _run_end(con,rid,'SUCCESS' if any(x.get('status')=='OK' for x in metrics['sports']) else 'PARTIAL',metrics)
    return metrics
