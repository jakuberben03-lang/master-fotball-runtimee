from __future__ import annotations
from pathlib import Path
import pandas as pd
from .free_sources import seed_free_source_catalog

SOURCE_NAME='Schochastics Football Data'
LICENSE='ODC Attribution'
RIGHTS='VERIFIED_ODC_ATTRIBUTION'

def manifest():
    return {'source':SOURCE_NAME,'license':LICENSE,'rights_status':RIGHTS,'use':'RESEARCH_ONLY_UNTIL_DOMAIN_AUDIT',
            'repo':'https://github.com/schochastics/football-data',
            'warnings':['broad historical coverage','older matches may have identity/data errors','competition-specific audit required']}

def load_results(path:str|Path):
    p=Path(path)
    if p.suffix.lower()=='.parquet':
        try: return pd.read_parquet(p)
        except ImportError as e: raise RuntimeError('PARQUET_REQUIRES_PYARROW_OR_FASTPARQUET') from e
    if p.suffix.lower() in {'.csv','.txt'}: return pd.read_csv(p)
    raise ValueError('UNSUPPORTED_RESULTS_FORMAT:'+p.suffix)

def profile_results(path:str|Path, *, competition_contains=None, country_contains=None):
    df=load_results(path); work=df.copy()
    cols={c.casefold():c for c in work.columns}
    comp_col=next((cols[x] for x in ['league','competition','tournament','league_name'] if x in cols),None)
    country_col=next((cols[x] for x in ['country','country_name'] if x in cols),None)
    if competition_contains and comp_col:
        work=work[work[comp_col].astype(str).str.contains(competition_contains,case=False,na=False,regex=False)]
    if country_contains and country_col:
        work=work[work[country_col].astype(str).str.contains(country_contains,case=False,na=False,regex=False)]
    date_col=next((cols[x] for x in ['date','match_date','datetime'] if x in cols),None)
    return {'rows':int(len(work)),'columns':list(df.columns),'competition_column':comp_col,'country_column':country_col,
            'min_date':None if not date_col or work.empty else str(work[date_col].min()),
            'max_date':None if not date_col or work.empty else str(work[date_col].max()),**manifest()}

def register(con):
    return seed_free_source_catalog(con)['schochastics']
