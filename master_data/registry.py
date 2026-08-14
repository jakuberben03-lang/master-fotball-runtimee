
from __future__ import annotations
import csv

def import_model_registry(con, csv_path):
    with open(csv_path,encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    for r in rows:
        con.execute("""INSERT INTO model_registry(model_name,version,market_family,status,algorithm,training_window,validation_window,oos_test,supported_domain,reason)
        VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(model_name,version) DO UPDATE SET market_family=excluded.market_family,status=excluded.status,algorithm=excluded.algorithm,training_window=excluded.training_window,validation_window=excluded.validation_window,oos_test=excluded.oos_test,supported_domain=excluded.supported_domain,reason=excluded.reason""",
        (r.get('model_name'),r.get('version'),r.get('market_family'),r.get('status'),r.get('algorithm'),r.get('training_window'),r.get('validation_window'),r.get('oos_test'),r.get('supported_domain'),r.get('reason')))
    con.commit(); return len(rows)
