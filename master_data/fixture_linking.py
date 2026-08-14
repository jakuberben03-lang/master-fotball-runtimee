from __future__ import annotations
import json
from datetime import datetime, timezone
from .identity import stable_id, normalize_name
from .advanced import link_external_fixture


def _iso(v):
    dt=datetime.fromisoformat(str(v).replace('Z','+00:00'))
    return dt.astimezone(timezone.utc).isoformat().replace('+00:00','Z')


def stage_fixture_link_proposals(con, source_id:str, provider_fixtures:list[dict]):
    """Create proposals from exact names/time only. Never writes fixture_source_links.
    provider row: source_fixture_key,kickoff_utc,home_team,away_team
    """
    created=0; ambiguous=0
    for r in provider_fixtures:
        key=str(r['source_fixture_key']); ko=_iso(r['kickoff_utc']); hn=normalize_name(r['home_team']); an=normalize_name(r['away_team'])
        date=ko[:10]
        cands=con.execute("""SELECT f.fixture_id,f.kickoff_utc,th.canonical_name home_name,ta.canonical_name away_name
                             FROM fixtures f JOIN teams th ON th.team_id=f.home_team_id JOIN teams ta ON ta.team_id=f.away_team_id
                             WHERE substr(f.kickoff_utc,1,10)=?""",(date,)).fetchall()
        matches=[]
        for c in cands:
            if normalize_name(c['home_name'])==hn and normalize_name(c['away_name'])==an:
                method='EXACT_KICKOFF_TEAMS' if _iso(c['kickoff_utc'])==ko else 'DATE_TEAMS_REVIEW'
                conf='A' if method=='EXACT_KICKOFF_TEAMS' else 'B'; matches.append((c,method,conf))
        if len(matches)!=1:
            ambiguous+=1; continue
        c,method,conf=matches[0]
        pid=stable_id('fixture-link-proposal',source_id,key,c['fixture_id'])
        con.execute('''INSERT OR IGNORE INTO fixture_link_proposals(proposal_id,source_id,source_fixture_key,fixture_id,match_method,confidence,evidence_json)
                       VALUES(?,?,?,?,?,?,?)''',(pid,source_id,key,c['fixture_id'],method,conf,json.dumps({'provider':r,'canonical_kickoff':c['kickoff_utc']},ensure_ascii=False)))
        created += con.execute('SELECT changes()').fetchone()[0]
    con.commit(); return {'proposals_created':created,'unmatched_or_ambiguous':ambiguous}


def approve_exact_proposals(con, source_id:str, *, reviewer_note='OPERATOR_APPROVED_EXACT_MATCH'):
    rows=con.execute("SELECT * FROM fixture_link_proposals WHERE source_id=? AND status='PENDING' AND match_method='EXACT_KICKOFF_TEAMS' AND confidence='A'",(source_id,)).fetchall()
    n=0
    for r in rows:
        evidence=json.loads(r['evidence_json'] or '{}'); evidence['reviewer_note']=reviewer_note; evidence['proposal_id']=r['proposal_id']
        link_external_fixture(con,source_id,r['source_fixture_key'],r['fixture_id'],'MANUAL_VERIFIED',evidence)
        con.execute("UPDATE fixture_link_proposals SET status='APPROVED',reviewed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE proposal_id=?",(r['proposal_id'],)); n+=1
    con.commit(); return {'approved':n,'contract':'Only operator-approved A/exact proposals become canonical fixture links.'}
