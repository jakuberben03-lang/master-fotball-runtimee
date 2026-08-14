from __future__ import annotations
from pathlib import Path
import pandas as pd

REQUIRED={'match_id','date','season','h_team','a_team','h_a','player','player_id','result','xG','player_assisted'}


def normalize_player_shot_research(path:str|Path):
    """Research-only player shot staging derived from an external Understat-style shot CSV.
    It intentionally does NOT fabricate minutes or role data, so it cannot activate player markets.
    """
    df=pd.read_csv(path,usecols=lambda c: c in REQUIRED)
    miss=REQUIRED-set(df.columns)
    if miss: raise ValueError('MISSING_REQUIRED_COLUMNS:'+','.join(sorted(miss)))
    df['xG']=pd.to_numeric(df['xG'],errors='coerce').fillna(0.0)
    df['sot']=df['result'].isin(['Goal','SavedShot']).astype('int16')
    df['goal']=(df['result']=='Goal').astype('int16')
    grpcols=['match_id','date','season','h_team','a_team','h_a','player','player_id']
    out=(df.groupby(grpcols,dropna=False,sort=False)
           .agg(shots=('result','size'),sot=('sot','sum'),goals=('goal','sum'),xg=('xG','sum'))
           .reset_index())
    # xA-like research proxy: shot xG credited to the assister name on the same team/match.
    ast=df[df['player_assisted'].notna() & (df['player_assisted'].astype(str).str.len()>0)]
    if len(ast):
        xa=(ast.groupby(['match_id','h_a','player_assisted'],sort=False)
              .agg(xa_proxy=('xG','sum'),assisted_shots_proxy=('xG','size')).reset_index()
              .rename(columns={'player_assisted':'player'}))
        out=out.merge(xa,on=['match_id','h_a','player'],how='left')
    else:
        out['xa_proxy']=0.0; out['assisted_shots_proxy']=0
    out['xa_proxy']=out['xa_proxy'].fillna(0.0)
    out['assisted_shots_proxy']=out['assisted_shots_proxy'].fillna(0).astype('int16')
    out['minutes']=pd.NA; out['role']=pd.NA; out['lineup_known']=0; out['research_only']=1; out['model_use_permission']='RESEARCH_ONLY'
    return out


def player_research_readiness(df:pd.DataFrame):
    if df.empty: return {'rows':0,'matches':0,'players':0,'minutes_coverage':0.0,'status':'NO_DATA'}
    return {'rows':int(len(df)),'matches':int(df['match_id'].nunique()),'players':int(df['player_id'].nunique()),
            'minutes_coverage':float(df['minutes'].notna().mean()),'lineup_coverage':float((df['lineup_known']==1).mean()),
            'status':'RESEARCH_ONLY_NO_MINUTES' if df['minutes'].notna().mean()<0.8 else 'RESEARCH_ONLY'}
