from __future__ import annotations
from dataclasses import dataclass

@dataclass(frozen=True)
class NoVigResult:
    probabilities: dict
    method: str
    overround: float


def proportional_no_vig(prices: dict[str,float]) -> NoVigResult:
    inv={k:1.0/float(v) for k,v in prices.items() if v and float(v)>1.0}
    if len(inv)<2: raise ValueError('Need at least two valid prices')
    s=sum(inv.values())
    return NoVigResult({k:v/s for k,v in inv.items()},'PROPORTIONAL',s-1.0)


def power_no_vig_1x2(prices: dict[str,float], tol: float=1e-12) -> NoVigResult:
    if set(prices) != {'HOME','DRAW','AWAY'}: raise ValueError('Power no-vig requires HOME/DRAW/AWAY')
    q={k:1.0/float(v) for k,v in prices.items()}
    lo,hi=0.01,10.0
    for _ in range(200):
        mid=(lo+hi)/2; s=sum(v**mid for v in q.values())
        if abs(s-1)<tol: break
        if s>1: lo=mid
        else: hi=mid
    probs={k:v**mid for k,v in q.items()}; z=sum(probs.values()); probs={k:v/z for k,v in probs.items()}
    return NoVigResult(probs,'POWER',sum(q.values())-1.0)


def compute_no_vig(market_key: str, prices: dict[str,float]) -> NoVigResult:
    if market_key=='1X2' and set(prices)=={'HOME','DRAW','AWAY'}: return power_no_vig_1x2(prices)
    return proportional_no_vig(prices)


def register_market_source(con, bookmaker: str, role='UNCLASSIFIED', confidence='UNAVAILABLE', notes=None):
    if role not in {'SHARP_REFERENCE','EXECUTION','CONSENSUS','UNCLASSIFIED'}: raise ValueError(role)
    if confidence not in {'A','B','C','UNAVAILABLE'}: raise ValueError(confidence)
    con.execute('''INSERT INTO market_source_registry(bookmaker,role,reference_confidence,notes)
                   VALUES(?,?,?,?) ON CONFLICT(bookmaker) DO UPDATE SET role=excluded.role,reference_confidence=excluded.reference_confidence,notes=excluded.notes''',
                (bookmaker,role,confidence,notes)); con.commit()


def normalize_snapshot_group(con, fixture_id: str, bookmaker: str, market_key: str, snapshot_type: str,
                             *, source_id:str|None=None, line_key:str|None=None, requested_snapshot_at:str|None=None, participant_key:str|None=None):
    """Normalize exactly one two/three-sided market snapshot.

    Group identity includes source, line and requested snapshot time so a real historical time-series cannot mix
    prices from different providers, handicap/total lines or timestamps. NULL requested_snapshot_at is preserved
    as the legacy static-CSV group.
    """
    where=['fixture_id=?','bookmaker=?','market_key=?','snapshot_type=?']; args=[fixture_id,bookmaker,market_key,snapshot_type]
    if source_id is not None: where.append('source_id=?'); args.append(source_id)
    if line_key is not None: where.append('line_key=?'); args.append(line_key)
    if requested_snapshot_at is None: where.append('requested_snapshot_at IS NULL')
    else: where.append('requested_snapshot_at=?'); args.append(requested_snapshot_at)
    if participant_key is not None: where.append("COALESCE(participant_key,'')=?"); args.append(participant_key)
    rows=con.execute('SELECT odds_snapshot_id,selection_key,decimal_odds FROM odds_snapshots WHERE '+' AND '.join(where),args).fetchall()
    # Duplicate selections inside one exact group are not safe to collapse silently.
    if len({r['selection_key'] for r in rows}) != len(rows): raise ValueError('Duplicate selections in exact market snapshot group')
    prices={r['selection_key']:r['decimal_odds'] for r in rows}
    if len(prices)<2: return None
    result=compute_no_vig(market_key,prices)
    reg=con.execute('SELECT role,reference_confidence FROM market_source_registry WHERE bookmaker=?',(bookmaker,)).fetchone()
    role=reg['role'] if reg else 'UNCLASSIFIED'; conf=reg['reference_confidence'] if reg else 'UNAVAILABLE'
    for r in rows:
        con.execute('''UPDATE odds_snapshots SET no_vig_probability=?, no_vig_method=?, overround=?,
          is_sharp=?, is_execution=?, reference_confidence=? WHERE odds_snapshot_id=?''',
          (result.probabilities.get(r['selection_key']),result.method,result.overround,
           1 if role=='SHARP_REFERENCE' else 0,1 if role=='EXECUTION' else 0,conf,r['odds_snapshot_id']))
    con.commit(); return result


def normalize_all_odds(con):
    groups=con.execute('''SELECT DISTINCT fixture_id,source_id,bookmaker,market_key,line_key,snapshot_type,requested_snapshot_at,COALESCE(participant_key,'') participant_key
                          FROM odds_snapshots''').fetchall()
    ok=fail=0
    for g in groups:
        try:
            if normalize_snapshot_group(con,g['fixture_id'],g['bookmaker'],g['market_key'],g['snapshot_type'],
                source_id=g['source_id'],line_key=g['line_key'],requested_snapshot_at=g['requested_snapshot_at'],participant_key=g['participant_key']): ok+=1
        except Exception: fail+=1
    return {'groups_normalized':ok,'groups_failed':fail}
