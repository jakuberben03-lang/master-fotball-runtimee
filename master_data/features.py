from __future__ import annotations
import json
from datetime import datetime, timezone
import pandas as pd
from .identity import stable_id

METRICS=['goals','h1_goals','corners','cards','fouls','shots','sot','red']
COLS={'goals':('home_goals','away_goals'),'h1_goals':('home_ht_goals','away_ht_goals'),'corners':('home_corners','away_corners'),'cards':('home_yellow','away_yellow'),'fouls':('home_fouls','away_fouls'),'shots':('home_shots','away_shots'),'sot':('home_sot','away_sot'),'red':('home_red','away_red')}

def _ewm(vals,span):
    s=pd.Series(vals,dtype='float64').dropna()
    return None if s.empty else float(s.ewm(span=span,adjust=False).mean().iloc[-1])

def _query(con):
    return pd.read_sql_query("""SELECT f.fixture_id AS fixture_key,f.kickoff_utc,f.competition_id,f.home_team_id,f.away_team_id,s.* FROM fixtures f JOIN team_match_stats s USING(fixture_id) WHERE f.status='FT' ORDER BY f.kickoff_utc,f.fixture_id""",con)

def _team_rows(d,team):
    rows=[]
    for _,r in d.iterrows():
        if r.home_team_id==team: side='home'; opp=r.away_team_id
        elif r.away_team_id==team: side='away'; opp=r.home_team_id
        else: continue
        rec={'fixture_id':r.fixture_key,'kickoff':r.kickoff_utc,'is_home':1 if side=='home' else 0,'opponent_id':opp}
        for m,(hc,ac) in COLS.items(): rec[m+'_for']=r[hc] if side=='home' else r[ac]; rec[m+'_against']=r[ac] if side=='home' else r[hc]
        rows.append(rec)
    return pd.DataFrame(rows).sort_values(['kickoff','fixture_id']) if rows else pd.DataFrame()

def _make_features(hist, league_hist, long_span, short_span):
    priors={m:None for m in METRICS}
    for m,(hc,ac) in COLS.items():
        vals=pd.concat([league_hist[hc],league_hist[ac]],ignore_index=True).dropna() if not league_hist.empty else pd.Series(dtype=float)
        priors[m]=None if vals.empty else float(vals.mean())
    feat={'sample_size':int(len(hist)),'league_priors':priors,'latest_kickoff_utc':None if hist.empty else str(hist.iloc[-1].kickoff)}
    for m in METRICS:
        feat[m]={'for_long':_ewm(hist[m+'_for'],long_span) if not hist.empty else None,'against_long':_ewm(hist[m+'_against'],long_span) if not hist.empty else None,'for_short':_ewm(hist[m+'_for'],short_span) if not hist.empty else None,'against_short':_ewm(hist[m+'_against'],short_span) if not hist.empty else None,'home_for':_ewm(hist.loc[hist.is_home==1,m+'_for'],long_span) if not hist.empty else None,'home_against':_ewm(hist.loc[hist.is_home==1,m+'_against'],long_span) if not hist.empty else None,'away_for':_ewm(hist.loc[hist.is_home==0,m+'_for'],long_span) if not hist.empty else None,'away_against':_ewm(hist.loc[hist.is_home==0,m+'_against'],long_span) if not hist.empty else None}
        p=priors[m]; fl=feat[m]['for_long']; al=feat[m]['against_long']
        feat[m]['attack_vs_league']=None if p in (None,0) or fl is None else fl/p
        feat[m]['defense_allowed_vs_league']=None if p in (None,0) or al is None else al/p
    return feat

def rebuild_latest_team_features(con, feature_set_version='master_team_features_v1.2', long_span=20, short_span=6):
    df=_query(con)
    if df.empty:return 0
    now=datetime.now(timezone.utc).isoformat().replace('+00:00','Z'); count=0
    for comp in df['competition_id'].unique():
        d=df[df.competition_id==comp]
        for team in pd.unique(pd.concat([d.home_team_id,d.away_team_id],ignore_index=True)):
            t=_team_rows(d,team)
            feat=_make_features(t,d,long_span,short_span)
            fsid=stable_id('feature-snapshot','team',team,comp,now,feature_set_version)
            con.execute('''INSERT OR REPLACE INTO feature_snapshots(feature_snapshot_id,entity_type,entity_id,competition_id,asof_utc,feature_set_version,features_json,source_max_kickoff_utc,sample_size,snapshot_scope,fixture_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)''',(fsid,'team',team,comp,now,feature_set_version,json.dumps(feat,ensure_ascii=False,allow_nan=False),feat['latest_kickoff_utc'],len(t),'LATEST',None)); count+=1
    con.commit(); return count

def rebuild_historical_fixture_features(con, feature_set_version='master_team_features_v1.2', long_span=20, short_span=6, replace=True):
    """Vectorized leakage-safe PRE_FIXTURE snapshots for every team-fixture row.
    Every rolling feature is shifted; league priors use only strictly earlier kickoff groups.
    """
    df=_query(con)
    if df.empty:return 0
    if replace:
        con.execute("DELETE FROM feature_snapshots WHERE snapshot_scope='PRE_FIXTURE' AND feature_set_version=?",(feature_set_version,))
    records=[]
    for _,r in df.iterrows():
        for is_home,team,opp in ((1,r.home_team_id,r.away_team_id),(0,r.away_team_id,r.home_team_id)):
            x={'fixture_id':r.fixture_key,'kickoff':r.kickoff_utc,'competition_id':r.competition_id,'team_id':team,'opponent_id':opp,'is_home':is_home}
            for m,(hc,ac) in COLS.items():
                x[m+'_for']=r[hc] if is_home else r[ac]
                x[m+'_against']=r[ac] if is_home else r[hc]
            records.append(x)
    long=pd.DataFrame(records).sort_values(['competition_id','team_id','kickoff','fixture_id']).reset_index(drop=True)
    # Shifted team rolling features: current fixture never enters its own snapshot.
    for m in METRICS:
        for src,name,span in [(m+'_for','for_long',long_span),(m+'_against','against_long',long_span),(m+'_for','for_short',short_span),(m+'_against','against_short',short_span)]:
            long[m+'_'+name]=long.groupby(['competition_id','team_id'],sort=False)[src].transform(lambda x: x.shift(1).ewm(span=span,adjust=False).mean())
        home_for=long[m+'_for'].where(long.is_home==1); home_against=long[m+'_against'].where(long.is_home==1)
        away_for=long[m+'_for'].where(long.is_home==0); away_against=long[m+'_against'].where(long.is_home==0)
        for ser,name in [(home_for,'home_for'),(home_against,'home_against'),(away_for,'away_for'),(away_against,'away_against')]:
            long[m+'_'+name]=ser.groupby([long.competition_id,long.team_id],sort=False).transform(lambda x: x.shift(1).ewm(span=long_span,adjust=False).mean())
    # Sample size before current fixture.
    long['sample_size']=long.groupby(['competition_id','team_id'],sort=False).cumcount()
    long['source_max_kickoff_utc']=long.groupby(['competition_id','team_id'],sort=False)['kickoff'].shift(1)
    # League priors by strictly earlier kickoff group (prevents same-kickoff leakage).
    for comp in df['competition_id'].unique():
        d=df[df.competition_id==comp].copy()
        for m,(hc,ac) in COLS.items():
            g=d.groupby('kickoff_utc').agg(sum_h=(hc,'sum'),count_h=(hc,'count'),sum_a=(ac,'sum'),count_a=(ac,'count')).sort_index()
            g['sum_all']=g.sum_h+g.sum_a; g['count_all']=g.count_h+g.count_a
            g['prior']=(g.sum_all.cumsum().shift(1)/g.count_all.cumsum().shift(1))
            mp=g['prior'].to_dict()
            mask=long.competition_id==comp
            long.loc[mask,m+'_league_prior']=long.loc[mask,'kickoff'].map(mp)
    inserts=[]
    for _,r in long.iterrows():
        feat={'sample_size':int(r.sample_size),'league_priors':{},'latest_kickoff_utc':None if pd.isna(r.source_max_kickoff_utc) else str(r.source_max_kickoff_utc)}
        for m in METRICS:
            p=None if pd.isna(r[m+'_league_prior']) else float(r[m+'_league_prior'])
            def val(k):
                v=r[m+'_'+k]; return None if pd.isna(v) else float(v)
            fl=val('for_long'); al=val('against_long')
            feat['league_priors'][m]=p
            feat[m]={'for_long':fl,'against_long':al,'for_short':val('for_short'),'against_short':val('against_short'),'home_for':val('home_for'),'home_against':val('home_against'),'away_for':val('away_for'),'away_against':val('away_against'),'attack_vs_league':None if p in (None,0) or fl is None else fl/p,'defense_allowed_vs_league':None if p in (None,0) or al is None else al/p}
        fsid=stable_id('feature-snapshot','team',r.team_id,r.competition_id,r.kickoff,feature_set_version,r.fixture_id)
        inserts.append((fsid,'team',r.team_id,r.competition_id,r.kickoff,feature_set_version,json.dumps(feat,ensure_ascii=False,allow_nan=False),feat['latest_kickoff_utc'],int(r.sample_size),'PRE_FIXTURE',r.fixture_id))
    con.executemany("""INSERT OR REPLACE INTO feature_snapshots(feature_snapshot_id,entity_type,entity_id,competition_id,asof_utc,feature_set_version,features_json,source_max_kickoff_utc,sample_size,snapshot_scope,fixture_id) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",inserts)
    con.commit(); return len(inserts)
