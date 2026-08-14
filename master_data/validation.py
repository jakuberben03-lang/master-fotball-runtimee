from __future__ import annotations
import json
from datetime import datetime, timezone
from .identity import stable_id

def utcnow(): return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')

def add_validation_evidence(con, e:dict):
    required=['model_name','model_version','market_family','domain_key']
    for k in required:
        if not e.get(k): raise ValueError(f'missing {k}')
    eid=e.get('evidence_id') or stable_id('validation-evidence',e['model_name'],e['model_version'],e['domain_key'],e.get('evaluated_at',utcnow()))
    vals=(eid,e['model_name'],e['model_version'],e['market_family'],e['domain_key'],e.get('evaluated_at',utcnow()),int(e.get('oos_predictions',0)),int(e.get('walk_forward_folds',0)),e.get('brier'),e.get('log_loss'),e.get('calibration_slope'),e.get('calibration_intercept'),e.get('ece'),e.get('market_brier'),e.get('market_log_loss'),e.get('clv_mean'),int(e.get('clv_evaluable_n',0)),e.get('drift_state','UNKNOWN'),e.get('data_pipeline_state','UNKNOWN'),json.dumps(e.get('evidence',{}),sort_keys=True))
    con.execute('''INSERT INTO model_validation_evidence(evidence_id,model_name,model_version,market_family,domain_key,evaluated_at,oos_predictions,walk_forward_folds,brier,log_loss,calibration_slope,calibration_intercept,ece,market_brier,market_log_loss,clv_mean,clv_evaluable_n,drift_state,data_pipeline_state,evidence_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',vals)
    con.commit(); return eid

def promotion_assessment(con, model_name:str, model_version:str, domain_key:str):
    r=con.execute('''SELECT * FROM model_validation_evidence WHERE model_name=? AND model_version=? AND domain_key=? ORDER BY evaluated_at DESC LIMIT 1''',(model_name,model_version,domain_key)).fetchone()
    if not r: return {'eligible':False,'reasons':['NO_VALIDATION_EVIDENCE']}
    reasons=[]
    # Operational guardrails, not claims of statistical sufficiency. No arbitrary edge threshold is invented.
    if r['oos_predictions'] < 300: reasons.append('INSUFFICIENT_OOS_BASE_FOR_PROMOTION_REVIEW')
    if r['walk_forward_folds'] < 2: reasons.append('MULTI_FOLD_WALK_FORWARD_MISSING')
    if r['calibration_slope'] is None or r['calibration_intercept'] is None: reasons.append('CALIBRATION_NOT_EVALUATED')
    if r['market_brier'] is None and r['market_log_loss'] is None and r['clv_evaluable_n']==0: reasons.append('MARKET_COMPARISON_OR_CLV_MISSING')
    if r['drift_state']!='OK': reasons.append('DRIFT_NOT_OK')
    if r['data_pipeline_state']!='OK': reasons.append('DATA_PIPELINE_NOT_OK')
    return {'eligible':not reasons,'reasons':reasons,'evidence_id':r['evidence_id']}

def promote_model(con, model_name:str, model_version:str, domain_key:str):
    a=promotion_assessment(con,model_name,model_version,domain_key)
    if not a['eligible']: raise ValueError('MASTER promotion blocked: '+','.join(a['reasons']))
    old=con.execute('SELECT status FROM model_registry WHERE model_name=? AND version=?',(model_name,model_version)).fetchone()
    if not old: raise KeyError((model_name,model_version))
    now=utcnow(); sid=stable_id('model-status',model_name,model_version,now,'ACTIVE')
    con.execute('UPDATE model_registry SET status=? WHERE model_name=? AND version=?',('ACTIVE',model_name,model_version))
    con.execute('INSERT INTO model_status_history(status_event_id,model_name,model_version,old_status,new_status,changed_at,reason,evidence_id) VALUES(?,?,?,?,?,?,?,?)',(sid,model_name,model_version,old['status'],'ACTIVE',now,'Passed validation evidence gate',a['evidence_id']))
    con.commit(); return a
