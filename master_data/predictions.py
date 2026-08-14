from __future__ import annotations
import json
from datetime import datetime, timezone
from .identity import stable_id

def utcnow(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')

def lock_prediction(con, p: dict):
    status=p['model_status']; verdict=p['verdict']
    if status!='ACTIVE' and verdict in {'BET','CORE','SECONDARY','HIGH-ODDS'}:
        raise ValueError('MASTER hard gate: PROVISIONAL/NO MODEL cannot be locked as BET')
    comp=p.get('computation_status')
    if comp is None: comp='REAL' if p.get('real_computation',False) else 'NONE'
    if comp not in {'REAL','MARKET_ANCHORED','NONE'}: raise ValueError('Invalid computation_status')
    if p.get('model_probability') is not None and comp!='REAL':
        raise ValueError('NO REAL COMPUTATION = NO MODEL PROBABILITY')
    if comp=='MARKET_ANCHORED' and p.get('model_probability') is not None:
        raise ValueError('MARKET-ANCHORED ESTIMATE != MODEL PROBABILITY')
    created=p.get('created_at',utcnow())
    pid=p.get('prediction_id') or stable_id('prediction',p['fixture_id'],p['market_key'],p['selection_key'],p.get('line'),created)
    vals=[pid,created,p['fixture_id'],p['market_family'],p['market_key'],p['selection_key'],p.get('line'),p.get('model_name'),p.get('model_version'),status,comp,p.get('model_probability'),p.get('interval_low'),p.get('interval_high'),p.get('fair_odds'),p.get('entry_odds'),p.get('entry_bookmaker'),p.get('sharp_reference_source'),p.get('sharp_reference_price'),p.get('sharp_no_vig_probability'),p.get('data_confidence'),p.get('model_uncertainty'),p.get('context_uncertainty'),p.get('mechanism_id'),p.get('bull'),p.get('bear'),1 if p.get('lineup_confirmed') else 0,p.get('kill_conditions'),p.get('discovery_depth'),p.get('double_counting'),p.get('stale_price'),p.get('rule_check'),p.get('final_hard_gate'),verdict,json.dumps(p.get('input_snapshot',{}),ensure_ascii=False,sort_keys=True),json.dumps(p.get('source_ids',[]),ensure_ascii=False)]
    con.execute("""INSERT INTO prediction_locks(prediction_id,created_at,fixture_id,market_family,market_key,selection_key,line,model_name,model_version,model_status,computation_status,model_probability,interval_low,interval_high,fair_odds,entry_odds,entry_bookmaker,sharp_reference_source,sharp_reference_price,sharp_no_vig_probability,data_confidence,model_uncertainty,context_uncertainty,mechanism_id,bull,bear,lineup_confirmed,kill_conditions,discovery_depth,double_counting,stale_price,rule_check,final_hard_gate,verdict,input_snapshot_json,source_ids_json)
    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",vals)
    con.commit(); return pid

def append_outcome(con, prediction_id: str, outcome: dict):
    vals=(prediction_id,outcome.get('evaluated_at',utcnow()),outcome.get('result'),outcome.get('event_occurred'),outcome.get('closing_odds'),outcome.get('closing_no_vig_probability'),outcome.get('clv_pct'),outcome.get('brier_score'),outcome.get('log_loss'),outcome.get('pnl_units'),outcome.get('thesis_outcome'),outcome.get('analysis_quality'),outcome.get('notes_post_hoc'))
    con.execute("""INSERT INTO prediction_outcomes(prediction_id,evaluated_at,result,event_occurred,closing_odds,closing_no_vig_probability,clv_pct,brier_score,log_loss,pnl_units,thesis_outcome,analysis_quality,notes_post_hoc) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(prediction_id) DO UPDATE SET evaluated_at=excluded.evaluated_at,result=excluded.result,event_occurred=excluded.event_occurred,closing_odds=excluded.closing_odds,closing_no_vig_probability=excluded.closing_no_vig_probability,clv_pct=excluded.clv_pct,brier_score=excluded.brier_score,log_loss=excluded.log_loss,pnl_units=excluded.pnl_units,thesis_outcome=excluded.thesis_outcome,analysis_quality=excluded.analysis_quality,notes_post_hoc=excluded.notes_post_hoc""",vals)
    con.commit()
