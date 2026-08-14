from __future__ import annotations
import json
import pandas as pd
from datetime import datetime, timezone
from .identity import stable_id

ADV={'xg':('home_xg','away_xg'),'blocked_shots':('home_blocked_shots','away_blocked_shots'),
     'crosses':('home_crosses','away_crosses'),'box_touches':('home_box_touches','away_box_touches'),
     'possession':('home_possession','away_possession')}

def _query(con):
    return pd.read_sql_query('''SELECT f.fixture_id AS fixture_key,f.kickoff_utc,f.competition_id,f.home_team_id,f.away_team_id,
      s.home_xg,s.away_xg,s.home_blocked_shots,s.away_blocked_shots,s.home_crosses,s.away_crosses,
      s.home_box_touches,s.away_box_touches,s.home_possession,s.away_possession
      FROM fixtures f JOIN team_match_stats s USING(fixture_id) WHERE f.status='FT' ORDER BY f.kickoff_utc,f.fixture_id''',con)

def rebuild_advanced_historical_features(con, feature_set_version='master_advanced_features_v1.3', long_span=20, short_span=6, replace=True):
    df=_query(con)
    if df.empty:return 0
    if replace: con.execute("DELETE FROM feature_snapshots WHERE snapshot_scope='PRE_FIXTURE' AND feature_set_version=?",(feature_set_version,))
    rec=[]
    for _,r in df.iterrows():
        for is_home,team,opp in ((1,r.home_team_id,r.away_team_id),(0,r.away_team_id,r.home_team_id)):
            x={'fixture_id':r.fixture_key,'kickoff':r.kickoff_utc,'competition_id':r.competition_id,'team_id':team,'opponent_id':opp,'is_home':is_home}
            for m,(hc,ac) in ADV.items():
                x[m+'_for']=r[hc] if is_home else r[ac]; x[m+'_against']=r[ac] if is_home else r[hc]
            rec.append(x)
    d=pd.DataFrame(rec).sort_values(['competition_id','team_id','kickoff','fixture_id']).reset_index(drop=True)
    d['sample_size']=d.groupby(['competition_id','team_id'],sort=False).cumcount()
    d['source_max_kickoff_utc']=d.groupby(['competition_id','team_id'],sort=False)['kickoff'].shift(1)
    for m in ADV:
        for kind in ['for','against']:
            s=d[m+'_'+kind]
            g=s.groupby([d.competition_id,d.team_id],sort=False)
            d[f'{m}_{kind}_long']=g.transform(lambda x:x.shift(1).ewm(span=long_span,adjust=False).mean())
            d[f'{m}_{kind}_short']=g.transform(lambda x:x.shift(1).ewm(span=short_span,adjust=False).mean())
            d[f'{m}_{kind}_n']=g.transform(lambda x:x.notna().shift(1,fill_value=False).cumsum())
    inserts=[]
    for _,r in d.iterrows():
        feat={'sample_size_matches':int(r.sample_size),'latest_kickoff_utc':None if pd.isna(r.source_max_kickoff_utc) else str(r.source_max_kickoff_utc),'metrics':{}}
        for m in ADV:
            def v(k):
                z=r[k]; return None if pd.isna(z) else float(z)
            nf=int(r[f'{m}_for_n']); na=int(r[f'{m}_against_n'])
            feat['metrics'][m]={'for_long':v(f'{m}_for_long'),'against_long':v(f'{m}_against_long'),
                                'for_short':v(f'{m}_for_short'),'against_short':v(f'{m}_against_short'),
                                'n_for_history':nf,'n_against_history':na,
                                'coverage_for':0.0 if r.sample_size==0 else nf/int(r.sample_size),
                                'coverage_against':0.0 if r.sample_size==0 else na/int(r.sample_size)}
        fsid=stable_id('feature-snapshot','team',r.team_id,r.competition_id,r.kickoff,feature_set_version,r.fixture_id)
        inserts.append((fsid,'team',r.team_id,r.competition_id,r.kickoff,feature_set_version,json.dumps(feat,ensure_ascii=False,allow_nan=False),feat['latest_kickoff_utc'],int(r.sample_size),'PRE_FIXTURE',r.fixture_id))
    con.executemany('''INSERT OR REPLACE INTO feature_snapshots(feature_snapshot_id,entity_type,entity_id,competition_id,asof_utc,feature_set_version,features_json,source_max_kickoff_utc,sample_size,snapshot_scope,fixture_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',inserts)
    con.commit(); return len(inserts)

def rebuild_advanced_latest_features(con, feature_set_version='master_advanced_features_v1.3', long_span=20, short_span=6):
    # Build all PRE_FIXTURE snapshots first; latest research features should be derived separately from complete history.
    # For v1.3 we deliberately avoid creating a synthetic latest row without a fixture cutoff.
    return {'status':'NOT_IMPLEMENTED_BY_DESIGN','reason':'Use PRE_FIXTURE snapshots for validation; current/live advanced state requires fresh provider observations and a concrete as-of timestamp.'}
