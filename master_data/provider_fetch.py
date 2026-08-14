from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from .identity import stable_id


def utcnow():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def canonical_request_fingerprint(endpoint_key:str, params:dict|None=None):
    clean={k:v for k,v in (params or {}).items() if str(k).lower() not in {'apikey','api_key','token','authorization'}}
    blob=json.dumps({'endpoint_key':endpoint_key,'params':clean},sort_keys=True,ensure_ascii=False,default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def record_provider_fetch(con, source_id:str, endpoint_key:str, params:dict|None, *, requested_at:str|None=None,
                          provider_snapshot_at:str|None=None, http_status:int|None=None, response_bytes:bytes|None=None,
                          raw_path:str|Path|None=None, success:bool=True, notes:str|None=None):
    requested_at=requested_at or utcnow()
    fp=canonical_request_fingerprint(endpoint_key,params)
    rh=hashlib.sha256(response_bytes).hexdigest() if response_bytes is not None else None
    fid=stable_id('provider-fetch',source_id,endpoint_key,fp,requested_at)
    con.execute('''INSERT OR REPLACE INTO provider_fetch_log(fetch_id,source_id,endpoint_key,request_fingerprint,
                   requested_at,provider_snapshot_at,http_status,response_sha256,raw_path,success,notes)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                (fid,source_id,endpoint_key,fp,requested_at,provider_snapshot_at,http_status,rh,
                 str(raw_path) if raw_path else None,1 if success else 0,notes))
    con.commit()
    return fid
