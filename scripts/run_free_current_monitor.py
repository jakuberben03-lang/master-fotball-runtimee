#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

def main():
    ap=argparse.ArgumentParser(description='MASTER FREE Multi-Provider Current Monitor v1')
    ap.add_argument('--db',default=str(ROOT/'data/master_monitor.db'))
    ap.add_argument('--raw-dir',default=str(ROOT/'data/raw/free_current'))
    ap.add_argument('--refresh-catalog',action='store_true')
    ap.add_argument('--max-enrich',type=int,default=6)
    a=ap.parse_args()
    from master_data.db import init_db
    from master_data.free_current_monitor import run_free_cycle
    con=init_db(a.db)
    out=run_free_cycle(con,a.raw_dir,refresh_catalog=a.refresh_catalog,max_enrich=a.max_enrich)
    print(json.dumps(out,indent=2,ensure_ascii=False,default=str))
    return 0 if out['status']!='BLOCKED_NO_CURRENT_FREE_SOURCE' else 3

if __name__=='__main__': raise SystemExit(main())
