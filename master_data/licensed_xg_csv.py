from __future__ import annotations
from pathlib import Path
import pandas as pd
from .advanced import ensure_source, register_provider_capability, resolve_linked_fixture, upsert_team_advanced_stats

REQUIRED={'source_fixture_key','home_xg','away_xg'}


def register_licensed_xg_source(con, name:str, base_url=None, notes=None):
    """Register a rights-cleared licensed xG dump/feed.

    This helper is intentionally explicit: callers must only use it for a source whose production
    rights have actually been verified. Registration alone still does not make the feature validated.
    """
    sid=ensure_source(con,name,'LICENSED_ADVANCED_DATA',base_url,20,
                      'Rights-cleared historical advanced-data source for canonical ingestion.',notes)
    register_provider_capability(con,sid,'xg','PRODUCTION',timing_granularity='POST_MATCH',license_class='COMMERCIAL',
                                 notes='Production-rights verified by operator; model admission still requires frozen A/B validation.')
    return sid


def ingest_licensed_xg_csv(con, path, source_name:str, base_url=None, rights_verified:bool=False):
    if not rights_verified:
        raise PermissionError('RIGHTS_VERIFICATION_REQUIRED')
    p=Path(path); df=pd.read_csv(p)
    missing=sorted(REQUIRED-set(df.columns))
    if missing: raise ValueError(f'MISSING_REQUIRED_COLUMNS:{missing}')
    sid=register_licensed_xg_source(con,source_name,base_url,notes='Ingested via provider-neutral MASTER licensed xG CSV adapter.')
    counts={'rows':0,'metrics':0,'unlinked':0}
    failures=[]
    for i,r in df.iterrows():
        key=str(r['source_fixture_key'])
        try:
            fid=resolve_linked_fixture(con,sid,key)
        except KeyError:
            counts['unlinked']+=1; failures.append({'row':int(i),'source_fixture_key':key,'reason':'NO_EXPLICIT_FIXTURE_LINK'}); continue
        hx=pd.to_numeric(pd.Series([r['home_xg']]),errors='coerce').iloc[0]
        ax=pd.to_numeric(pd.Series([r['away_xg']]),errors='coerce').iloc[0]
        if pd.isna(hx) or pd.isna(ax) or float(hx)<0 or float(ax)<0:
            failures.append({'row':int(i),'source_fixture_key':key,'reason':'INVALID_XG'}); continue
        obs=None if 'observed_at' not in df.columns or pd.isna(r.get('observed_at')) else str(r.get('observed_at'))
        loc=None if 'source_locator' not in df.columns or pd.isna(r.get('source_locator')) else str(r.get('source_locator'))
        counts['metrics']+=upsert_team_advanced_stats(
            con,fid,sid,home={'xg':float(hx)},away={'xg':float(ax)},observed_at=obs,
            availability_class='POST_MATCH_SOURCE',source_locator=loc or str(p),source_record_key=key
        )
        counts['rows']+=1
    return {'source_id':sid,'source_name':source_name,**counts,'failures':failures,
            'hard_rule':'Canonical xG presence does not imply feature deployment. Run advanced readiness + frozen feature A/B + market comparison.'}
