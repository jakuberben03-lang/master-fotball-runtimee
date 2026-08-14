from __future__ import annotations
import json
from .identity import stable_id

DOMAIN_PROFILES = {
    'BIG5_DOMESTIC': dict(domain_family='DOMESTIC_LEAGUE', status='SUPPORTED', requires_knockout_context=0, requires_cross_league_strength=0,
                          notes='Current validated data domain for Big-5 top domestic leagues. Model status remains separate.'),
    'CZ_FIRST_LEAGUE': dict(domain_family='DOMESTIC_LEAGUE', status='EXPERIMENTAL', requires_knockout_context=0, requires_cross_league_strength=0,
                            notes='Czech First League / Chance Liga. Result history can be ingested, but model/market validation is not yet sufficient for BET.'),
    'UEFA_LEAGUE_PHASE': dict(domain_family='UEFA_LEAGUE_PHASE', status='EXPERIMENTAL', requires_knockout_context=0, requires_cross_league_strength=1,
                              notes='UEFA club league/group phase. Requires cross-league strength calibration and competition-specific market validation.'),
    'UEFA_KNOCKOUT': dict(domain_family='UEFA_KNOCKOUT', status='EXPERIMENTAL', requires_knockout_context=1, requires_cross_league_strength=1,
                          notes='UEFA two-leg/single-leg knockout. Aggregate/game-state context is mandatory.'),
    'UEFA_QUALIFYING': dict(domain_family='UEFA_QUALIFYING', status='EXPERIMENTAL', requires_knockout_context=1, requires_cross_league_strength=1,
                            notes='UEFA qualifying/playoff ties. Domestic Big-5 model must not be reused as an official betting model without separate validation.'),
}

BIG5_CODES={'E0','D1','I1','SP1','F1'}
UEFA_MAP={
    'UCL':'UEFA_LEAGUE_PHASE','UEL':'UEFA_LEAGUE_PHASE','UECL':'UEFA_LEAGUE_PHASE',
    'UCLQ':'UEFA_QUALIFYING','UELQ':'UEFA_QUALIFYING','UECLQ':'UEFA_QUALIFYING',
}

def seed_domain_profiles(con):
    for key,d in DOMAIN_PROFILES.items():
        con.execute('''INSERT INTO domain_profiles(domain_key,domain_family,status,requires_knockout_context,requires_cross_league_strength,requires_market_validation,notes)
                       VALUES(?,?,?,?,?,?,?) ON CONFLICT(domain_key) DO UPDATE SET domain_family=excluded.domain_family,status=excluded.status,
                       requires_knockout_context=excluded.requires_knockout_context,requires_cross_league_strength=excluded.requires_cross_league_strength,
                       requires_market_validation=excluded.requires_market_validation,notes=excluded.notes,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''',
                    (key,d['domain_family'],d['status'],d['requires_knockout_context'],d['requires_cross_league_strength'],1,d['notes']))
    # Assign known competitions by source_code/name; explicit and deterministic.
    rows=con.execute('SELECT competition_id,source_code,name,country FROM competitions').fetchall()
    for r in rows:
        code=r['source_code'] or ''
        name=(r['name'] or '').lower()
        if code in BIG5_CODES:
            domain='BIG5_DOMESTIC'
        elif code=='CZ1' or ('czech' in (r['country'] or '').lower() and 'league' in name):
            domain='CZ_FIRST_LEAGUE'
        elif code in UEFA_MAP:
            domain=UEFA_MAP[code]
        else:
            continue
        con.execute('''INSERT INTO competition_domain_assignments(competition_id,domain_key,assignment_method,evidence_json)
                       VALUES(?,?,?,?) ON CONFLICT(competition_id) DO UPDATE SET domain_key=excluded.domain_key,assignment_method=excluded.assignment_method,
                       evidence_json=excluded.evidence_json,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''',
                    (r['competition_id'],domain,'CONFIG',json.dumps({'source_code':code,'name':r['name']},ensure_ascii=False)))
    con.commit()


def set_knockout_context(con, fixture_id, *, tie_id=None, round_name=None, phase='UNKNOWN', leg_number=None, legs_total=None,
                         aggregate_home_before=None, aggregate_away_before=None, extra_time_possible=False, penalties_possible=False,
                         away_goals_rule_active=False, must_score_home=False, must_score_away=False, context_observed_at=None,
                         source_id=None, source_record_key=None, evidence=None):
    con.execute('''INSERT INTO knockout_context(fixture_id,tie_id,round_name,phase,leg_number,legs_total,aggregate_home_before,aggregate_away_before,
                   extra_time_possible,penalties_possible,away_goals_rule_active,must_score_home,must_score_away,context_observed_at,source_id,source_record_key,evidence_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(fixture_id) DO UPDATE SET tie_id=excluded.tie_id,round_name=excluded.round_name,
                   phase=excluded.phase,leg_number=excluded.leg_number,legs_total=excluded.legs_total,aggregate_home_before=excluded.aggregate_home_before,
                   aggregate_away_before=excluded.aggregate_away_before,extra_time_possible=excluded.extra_time_possible,penalties_possible=excluded.penalties_possible,
                   away_goals_rule_active=excluded.away_goals_rule_active,must_score_home=excluded.must_score_home,must_score_away=excluded.must_score_away,
                   context_observed_at=excluded.context_observed_at,source_id=excluded.source_id,source_record_key=excluded.source_record_key,evidence_json=excluded.evidence_json,
                   updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')''',
                (fixture_id,tie_id,round_name,phase,leg_number,legs_total,aggregate_home_before,aggregate_away_before,int(extra_time_possible),int(penalties_possible),
                 int(away_goals_rule_active),int(must_score_home),int(must_score_away),context_observed_at,source_id,source_record_key,json.dumps(evidence or {},ensure_ascii=False)))
    con.commit()
    # UEFA competition-level domains are only defaults. Once fixture-specific
    # leg/phase context is known, deterministically re-route the fixture to the
    # correct phase domain (league phase / knockout / qualifying).
    row = con.execute("""SELECT c.source_code FROM fixtures f
                       JOIN competitions c USING(competition_id)
                       WHERE f.fixture_id=?""", (fixture_id,)).fetchone()
    if row and (row['source_code'] or '') in UEFA_MAP:
        infer_and_assign_uefa_fixture_domain(con, fixture_id)


def assign_fixture_domain(con, fixture_id, domain_key, method='RULE', evidence=None):
    if domain_key not in DOMAIN_PROFILES:
        raise ValueError(f'Unknown domain_key {domain_key}')
    con.execute("""INSERT INTO fixture_domain_assignments(fixture_id,domain_key,assignment_method,evidence_json) VALUES(?,?,?,?)
                 ON CONFLICT(fixture_id) DO UPDATE SET domain_key=excluded.domain_key,assignment_method=excluded.assignment_method,evidence_json=excluded.evidence_json,
                 updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
                (fixture_id,domain_key,method,json.dumps(evidence or {},ensure_ascii=False)))
    con.commit()

def infer_and_assign_uefa_fixture_domain(con, fixture_id):
    r=con.execute("""SELECT f.fixture_id,c.source_code,c.name,k.phase,k.leg_number,k.legs_total FROM fixtures f JOIN competitions c USING(competition_id)
                    LEFT JOIN knockout_context k USING(fixture_id) WHERE f.fixture_id=?""",(fixture_id,)).fetchone()
    if not r: raise ValueError('fixture not found')
    code=r['source_code'] or ''
    if code.endswith('Q'):
        key='UEFA_QUALIFYING'
    elif (r['phase'] or '').upper() in ('QUALIFYING','PLAYOFF'):
        key='UEFA_QUALIFYING'
    elif (r['phase'] or '').upper() in ('KNOCKOUT','FINAL') or r['leg_number'] is not None:
        key='UEFA_KNOCKOUT'
    else:
        key='UEFA_LEAGUE_PHASE'
    assign_fixture_domain(con,fixture_id,key,'RULE',{'source_code':code,'phase':r['phase'],'leg_number':r['leg_number'],'legs_total':r['legs_total']})
    return key

def domain_gate(con, fixture_id):
    row=con.execute('''SELECT f.fixture_id,c.name competition,c.source_code,c.domain_status,COALESCE(fda.domain_key,cda.domain_key) AS domain_key,
                      dp.domain_family,dp.status domain_profile_status,dp.requires_knockout_context,dp.requires_cross_league_strength,k.fixture_id AS has_knockout_context
                      FROM fixtures f JOIN competitions c USING(competition_id)
                      LEFT JOIN fixture_domain_assignments fda USING(fixture_id)
                      LEFT JOIN competition_domain_assignments cda USING(competition_id)
                      LEFT JOIN domain_profiles dp ON dp.domain_key=COALESCE(fda.domain_key,cda.domain_key)
                      LEFT JOIN knockout_context k ON k.fixture_id=f.fixture_id WHERE f.fixture_id=?''',(fixture_id,)).fetchone()
    if not row: return {'status':'FAIL','reason':'FIXTURE_NOT_FOUND'}
    d=dict(row)
    reasons=[]
    if not d.get('domain_key'): reasons.append('NO_DOMAIN_PROFILE')
    if d.get('domain_profile_status')!='SUPPORTED': reasons.append('DOMAIN_NOT_SUPPORTED')
    if d.get('requires_knockout_context') and not d.get('has_knockout_context'): reasons.append('KNOCKOUT_CONTEXT_MISSING')
    return {**d,'gate':'PASS' if not reasons else 'FAIL','reasons':reasons}
