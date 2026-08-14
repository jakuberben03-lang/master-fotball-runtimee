#!/usr/bin/env python3
"""MASTER Stats Monitor v1.0 runner.

Default mode is quota-safe pre-match + post-match collection. Live snapshots are opt-in.
The runner never fabricates timestamps, never fuzzy-links fixtures and stops spending when the free quota reserve is reached.
"""
from __future__ import annotations
import argparse, json, os, time
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]


def main():
    ap=argparse.ArgumentParser(description='MASTER Stats Monitor v1.0')
    ap.add_argument('--db',default=str(ROOT/'data/master_football.db'))
    ap.add_argument('--raw-dir',default=str(ROOT/'data/raw/stats_monitor'))
    ap.add_argument('--bootstrap-defaults',action='store_true',help='Discover current Big-5/CZ/UEFA competition IDs and refresh fixture catalogs first')
    ap.add_argument('--no-uefa',action='store_true')
    ap.add_argument('--live',action='store_true',help='Enable LIVE snapshots only for fixtures explicitly watchlisted with collect_live=1')
    ap.add_argument('--watchlist-only',action='store_true')
    ap.add_argument('--max-fixtures',type=int,default=12)
    ap.add_argument('--prematch-min',type=int,default=150)
    ap.add_argument('--post-delay-min',type=int,default=105)
    ap.add_argument('--lookback-hours',type=int,default=18)
    ap.add_argument('--loop',action='store_true')
    ap.add_argument('--interval-min',type=int,default=60)
    a=ap.parse_args()

    os.environ['PYTHONPATH']=str(ROOT)+os.pathsep+os.environ.get('PYTHONPATH','')
    from master_data.db import init_db
    from master_data.ingest import bootstrap_reference_data
    from master_data.stats_monitor import monitor_cycle, status_report
    from master_data.api_football import bootstrap_default_fixture_catalog

    con=init_db(a.db); bootstrap_reference_data(con)
    if a.bootstrap_defaults:
        if not os.getenv('API_FOOTBALL_KEY'):
            print(json.dumps({'status':'SKIPPED','stage':'bootstrap','reason':'API_FOOTBALL_KEY_NOT_SET'},indent=2)); return 2
        boot=bootstrap_default_fixture_catalog(con,raw_dir=Path(a.raw_dir)/'catalog',include_uefa=not a.no_uefa)
        print(json.dumps({'stage':'bootstrap','result':boot},indent=2,ensure_ascii=False,default=str))

    while True:
        out=monitor_cycle(con,raw_dir=a.raw_dir,prematch_window_minutes=a.prematch_min,postmatch_delay_minutes=a.post_delay_min,
                          postmatch_lookback_hours=a.lookback_hours,include_live=a.live,watchlist_only=a.watchlist_only,max_fixtures=a.max_fixtures)
        print(json.dumps({'cycle':out,'status_report':status_report(con)},indent=2,ensure_ascii=False,default=str),flush=True)
        if not a.loop: break
        time.sleep(max(5,a.interval_min)*60)
    return 0

if __name__=='__main__':
    raise SystemExit(main())
