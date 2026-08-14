from __future__ import annotations
import json
from pathlib import Path
import pandas as pd
from .advanced import ensure_source, register_provider_capability

SOURCE_NAME='Understat-derived GitHub Research Dataset'
SOURCE_URL='https://github.com/douglasbc/scraping-understat-dataset'
LICENSE_CLASS='UNKNOWN'
RIGHTS_STATUS='UNVERIFIED_RESEARCH'
COVERAGE_SCOPE='RESEARCH_ONLY'

# Deterministic competition inference inside the external dataset only. This is NOT a cross-source fixture merge.
LEAGUE_ANCHORS={
    'england': {'Arsenal','Manchester United','Liverpool'},
    'spain': {'Barcelona','Real Madrid','Atletico Madrid'},
    'germany': {'Bayern Munich','Borussia Dortmund'},
    'italy': {'Juventus','Inter','AC Milan'},
    'france': {'Paris Saint Germain','Lyon','Marseille'},
    'russia': {'Zenit St. Petersburg','CSKA Moscow','Spartak Moscow'},
}

REQUIRED_SHOT_COLUMNS={
    'match_id','h_team','a_team','h_a','xG','result','date','season'
}


def register_understat_research_source(con):
    """Register the external research dataset without granting model-use permission.

    No raw data is bundled and this registration is never evidence of a production licence.
    """
    sid=ensure_source(
        con, SOURCE_NAME, 'UNVERIFIED_RESEARCH_DATA', SOURCE_URL, 80,
        'External research-only dataset derived from Understat. MASTER does not bundle the raw dataset and does not treat it as production training authority.',
        'No explicit repository licence was verified at build time. Any commercial/production use requires separate rights verification and a licensed-data repeat of the experiment.'
    )
    for cap in ('xg','shots','shot_events'):
        register_provider_capability(
            con, sid, cap, COVERAGE_SCOPE,
            competitions=['BIG5_RESEARCH_2014_15_TO_2021_22_PARTIAL'],
            seasons=['2014','2015','2016','2017','2018','2019','2020','2021_PARTIAL'],
            timing_granularity='POST_MATCH', license_class=LICENSE_CLASS,
            notes='RESEARCH ONLY. Must not populate canonical production model features or change model registry status.'
        )
    return sid


def _connected_components(matches: pd.DataFrame, season: int):
    s=matches[matches['season']==season]
    graph={}
    for h,a in zip(s.h_team.astype(str),s.a_team.astype(str)):
        graph.setdefault(h,set()).add(a); graph.setdefault(a,set()).add(h)
    comps=[]; seen=set()
    for node in graph:
        if node in seen: continue
        stack=[node]; comp=set()
        while stack:
            x=stack.pop()
            if x in seen: continue
            seen.add(x); comp.add(x); stack.extend(graph.get(x,set())-seen)
        comps.append(comp)
    return comps


def infer_research_league(matches: pd.DataFrame):
    """Infer competition components deterministically from within-dataset team graph.

    This does not resolve canonical MASTER teams and therefore cannot silently fuzzy-merge sources.
    """
    out=matches.copy(); out['research_league']=None
    for season in sorted(out.season.dropna().astype(int).unique()):
        comps=_connected_components(out,season)
        for comp in comps:
            label=None
            scores={k:len(comp & anchors) for k,anchors in LEAGUE_ANCHORS.items()}
            if scores and max(scores.values())>0:
                best=[k for k,v in scores.items() if v==max(scores.values())]
                if len(best)==1: label=best[0]
            if label:
                mask=(out.season.astype(int)==season) & out.h_team.isin(comp) & out.a_team.isin(comp)
                out.loc[mask,'research_league']=label
    return out


def normalize_shot_csv(input_path, output_path=None):
    """Aggregate an externally supplied shot-level CSV to match-level research xG.

    The normalized output is a *research staging artifact only*. It is never written to
    canonical team_match_stats by this function.
    """
    p=Path(input_path); df=pd.read_csv(p)
    missing=sorted(REQUIRED_SHOT_COLUMNS-set(df.columns))
    if missing: raise ValueError(f'MISSING_REQUIRED_COLUMNS:{missing}')
    df['xG']=pd.to_numeric(df['xG'],errors='coerce')
    if df['xG'].isna().any(): raise ValueError('INVALID_XG_VALUES')
    # Stable match metadata from shot rows.
    meta=df.groupby('match_id',as_index=False).agg(
        date=('date','first'), season=('season','first'), home_team=('h_team','first'), away_team=('a_team','first')
    )
    home=df[df.h_a.astype(str).str.lower().eq('h')].groupby('match_id').agg(
        home_xg=('xG','sum'), home_shots=('xG','size'),
        home_sot=('result',lambda s: s.astype(str).isin(['SavedShot','Goal']).sum())
    )
    away=df[df.h_a.astype(str).str.lower().eq('a')].groupby('match_id').agg(
        away_xg=('xG','sum'), away_shots=('xG','size'),
        away_sot=('result',lambda s: s.astype(str).isin(['SavedShot','Goal']).sum())
    )
    m=meta.merge(home,on='match_id',how='left').merge(away,on='match_id',how='left')
    for c in ['home_xg','away_xg','home_shots','away_shots','home_sot','away_sot']:
        m[c]=m[c].fillna(0)
    m=m.rename(columns={'home_team':'h_team','away_team':'a_team'})
    m=infer_research_league(m)
    m=m.rename(columns={'h_team':'home_team','a_team':'away_team'})
    m['source_name']=SOURCE_NAME
    m['license_class']=LICENSE_CLASS
    m['rights_status']=RIGHTS_STATUS
    m['model_use_permission']='RESEARCH_ONLY'
    cols=['match_id','date','season','research_league','home_team','away_team','home_xg','away_xg','home_shots','away_shots','home_sot','away_sot','source_name','license_class','rights_status','model_use_permission']
    m=m[cols].sort_values(['season','research_league','date','match_id'],na_position='last').reset_index(drop=True)
    if output_path:
        Path(output_path).parent.mkdir(parents=True,exist_ok=True); m.to_csv(output_path,index=False)
    return m


def write_research_manifest(normalized: pd.DataFrame, path):
    coverage=(normalized.groupby(['season','research_league']).size().reset_index(name='matches')
              .sort_values(['season','research_league']))
    doc={
        'source_name':SOURCE_NAME,'source_url':SOURCE_URL,'license_class':LICENSE_CLASS,'rights_status':RIGHTS_STATUS,
        'model_use_permission':'RESEARCH_ONLY','raw_data_bundled':False,
        'match_rows':int(len(normalized)),
        'date_min':str(normalized.date.min()) if len(normalized) else None,
        'date_max':str(normalized.date.max()) if len(normalized) else None,
        'coverage':coverage.to_dict(orient='records'),
        'hard_rule':'Research staging output may support feature discovery only. Production model admission requires a rights-cleared/licensed source and a fresh repeat of the frozen confirmation protocol.'
    }
    Path(path).write_text(json.dumps(doc,indent=2,ensure_ascii=False),encoding='utf-8')
    return doc
