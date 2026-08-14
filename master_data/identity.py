
from __future__ import annotations
import re, unicodedata, uuid

NS=uuid.UUID('fca79c36-a69a-5f95-85b1-7fd4127e5048')

def stable_id(kind: str, *parts: object) -> str:
    key='|'.join([kind]+[str(x).strip() for x in parts])
    return str(uuid.uuid5(NS,key))

def normalize_name(s: str) -> str:
    s=unicodedata.normalize('NFKD',str(s)).encode('ascii','ignore').decode('ascii')
    s=s.casefold().replace('&',' and ')
    s=re.sub(r'[^a-z0-9]+',' ',s)
    return re.sub(r'\s+',' ',s).strip()
