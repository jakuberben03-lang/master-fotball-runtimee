
from __future__ import annotations
from pathlib import Path
import pandas as pd

def export_v1_model_csvs(con,out_dir):
    out=Path(out_dir); out.mkdir(parents=True,exist_ok=True)
    mapping={'E0':'england','F1':'france','D1':'germany','I1':'italy','SP1':'spain'}
    written=[]
    for code,league in mapping.items():
        seasons=[r[0] for r in con.execute("SELECT DISTINCT season FROM v_fixture_legacy_model WHERE league_code=? ORDER BY season",(code,)).fetchall()]
        for season in seasons:
            df=pd.read_sql_query('SELECT Date,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,HS,"AS",HST,AST,HF,AF,HC,AC,HY,AY,HR,AR FROM v_fixture_legacy_model WHERE league_code=? AND season=? ORDER BY Date,HomeTeam,AwayTeam',con,params=(code,season))
            if df.empty: continue
            p=out/f'model_data_{league}_{season}.csv'; df.to_csv(p,index=False); written.append(str(p))
    return written
