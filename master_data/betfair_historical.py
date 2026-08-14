from __future__ import annotations

import bz2
import gzip
import io
import json
import math
import re
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Iterable, Iterator

from .advanced import ensure_source, register_provider_capability, resolve_linked_fixture
from .fixture_linking import stage_fixture_link_proposals
from .identity import stable_id, normalize_name
from .market import normalize_snapshot_group, register_market_source

SOURCE_NAME = 'Betfair Historical Data'
BASE_URL = 'https://historicdata.betfair.com/'

# Betfair football Exchange market types observed/documented in listMarketTypes.
_CORNER_TOTAL_RE = re.compile(r'^OVER_UNDER_(\d+)_CORNR$')
_CARD_TOTAL_RE = re.compile(r'^OVER_UNDER_(\d+)_CARDS$')

SUPPORTED_MARKET_TYPE_PATTERNS = {
    'corners': _CORNER_TOTAL_RE.pattern,
    'cards': _CARD_TOTAL_RE.pattern,
}

TARGET_OFFSETS = {
    'OPENING': timedelta(hours=24),
    'PRECLOSE': timedelta(hours=6),
    'ENTRY': timedelta(hours=1),
    'CLOSING': timedelta(seconds=0),
}


def _utc_iso_ms(ms: int | float | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(float(ms) / 1000.0, tz=timezone.utc).isoformat().replace('+00:00', 'Z')


def _parse_iso(v: str | None) -> datetime | None:
    if not v:
        return None
    return datetime.fromisoformat(str(v).replace('Z', '+00:00')).astimezone(timezone.utc)


def _line_from_code(raw: str) -> float:
    # Betfair encodes 8.5 as 85, 10.5 as 105, etc.
    return int(raw) / 10.0


def _market_descriptor(market_type: str | None):
    mt = str(market_type or '').upper()
    m = _CORNER_TOTAL_RE.match(mt)
    if m:
        return {'family': 'CORNERS', 'market_key': 'TOTAL_CORNERS', 'line': _line_from_code(m.group(1)), 'market_type': mt}
    m = _CARD_TOTAL_RE.match(mt)
    if m:
        return {'family': 'CARDS', 'market_key': 'TOTAL_CARDS', 'line': _line_from_code(m.group(1)), 'market_type': mt}
    return None


def _selection_key(name: str | None) -> str | None:
    n = str(name or '').strip().lower()
    if n.startswith('over'):
        return 'OVER'
    if n.startswith('under'):
        return 'UNDER'
    return None


def _split_event_name(name: str | None):
    s = str(name or '').strip()
    for sep in (' v ', ' vs ', ' - '):
        if sep in s:
            a, b = s.split(sep, 1)
            if a.strip() and b.strip():
                return a.strip(), b.strip()
    return None, None


def source_id_and_capabilities(con):
    sid = ensure_source(
        con, SOURCE_NAME, 'EXCHANGE_HISTORICAL', BASE_URL, 12,
        'Account-licensed Betfair Exchange historical data. MASTER may ingest files lawfully downloaded by the operator. '
        'Do not redistribute raw Betfair files from the MASTER pack.',
        'BASIC is 1-minute last-traded-price data without volume; ADVANCED/PRO are more granular. '
        'Market availability varies by event and period.'
    )
    register_provider_capability(
        con, sid, 'historical_exchange_odds', 'PRODUCTION', timing_granularity='UNKNOWN',
        license_class='RESTRICTED',
        notes='Official Betfair historical Exchange files; free BASIC tier can be acquired through the Betfair historical portal by registered customers.'
    )
    register_provider_capability(
        con, sid, 'historical_secondary_markets', 'PARTIAL', timing_granularity='UNKNOWN',
        license_class='RESTRICTED',
        notes='MASTER currently normalizes total corners and total cards O/U marketType codes. Other market types are discovery-only until explicit settlement mapping is implemented.'
    )
    return sid


def _iter_plain_lines(data: bytes) -> Iterator[bytes]:
    for line in data.splitlines():
        if line.strip():
            yield line


def _decode_member_bytes(name: str, raw: bytes) -> bytes:
    ln = name.lower()
    if ln.endswith('.bz2'):
        return bz2.decompress(raw)
    if ln.endswith('.gz'):
        return gzip.decompress(raw)
    return raw


def iter_stream_files(paths: Iterable[str | Path]):
    """Yield (logical_name, bytes) for Betfair JSON stream files.

    Supports official .tar archives and individual .bz2/.gz/.json files. Archives are streamed member-by-member.
    """
    for p0 in paths:
        p = Path(p0)
        if not p.exists():
            raise FileNotFoundError(p)
        if tarfile.is_tarfile(p):
            with tarfile.open(p, 'r:*') as tf:
                for member in tf:
                    if not member.isfile():
                        continue
                    fh = tf.extractfile(member)
                    if fh is None:
                        continue
                    raw = fh.read()
                    try:
                        raw = _decode_member_bytes(member.name, raw)
                    except OSError:
                        continue
                    if raw.strip():
                        yield f'{p.name}:{member.name}', raw
        else:
            raw = p.read_bytes()
            raw = _decode_member_bytes(p.name, raw)
            if raw.strip():
                yield p.name, raw


def _updates(raw: bytes):
    for line in _iter_plain_lines(raw):
        try:
            doc = json.loads(line)
        except Exception:
            continue
        if doc.get('op') != 'mcm':
            continue
        pt = doc.get('pt')
        for mc in doc.get('mc', []) or []:
            yield pt, mc


@dataclass
class RunnerSeries:
    name: str
    points: list


def parse_market_file(logical_name: str, raw: bytes):
    market_id = None
    market_def = None
    runner_names = {}
    points: dict[int, list[tuple[int, float, float | None]]] = {}
    discovered_types = set()

    for pt, mc in _updates(raw):
        market_id = str(mc.get('id') or market_id or '')
        md = mc.get('marketDefinition')
        if md:
            market_def = {**(market_def or {}), **md}
            mt = md.get('marketType')
            if mt:
                discovered_types.add(str(mt))
            for r in md.get('runners', []) or []:
                if r.get('id') is not None and r.get('name'):
                    runner_names[int(r['id'])] = str(r['name'])
        for rc in mc.get('rc', []) or []:
            rid = rc.get('id')
            ltp = rc.get('ltp')
            if rid is None or ltp is None or pt is None:
                continue
            try:
                price = float(ltp)
            except Exception:
                continue
            if not math.isfinite(price) or price <= 1.0:
                continue
            tv = rc.get('tv')
            try:
                tv = float(tv) if tv is not None else None
            except Exception:
                tv = None
            points.setdefault(int(rid), []).append((int(pt), price, tv))

    if not market_id or not market_def:
        return None
    return {
        'logical_name': logical_name,
        'market_id': market_id,
        'market_definition': market_def,
        'runner_names': runner_names,
        'runner_points': points,
        'discovered_market_types': sorted(discovered_types),
    }


def scan_archives(paths: Iterable[str | Path]):
    """Return event rows and market-type coverage without writing canonical odds."""
    events = {}
    market_types = {}
    files = parsed = relevant = 0
    for name, raw in iter_stream_files(paths):
        files += 1
        m = parse_market_file(name, raw)
        if not m:
            continue
        parsed += 1
        md = m['market_definition']
        mt = str(md.get('marketType') or 'UNKNOWN')
        market_types[mt] = market_types.get(mt, 0) + 1
        desc = _market_descriptor(mt)
        if desc:
            relevant += 1
        eid = str(md.get('eventId') or '')
        home, away = _split_event_name(md.get('eventName'))
        if eid and home and away and md.get('marketTime'):
            events[eid] = {
                'source_fixture_key': eid,
                'kickoff_utc': str(md['marketTime']),
                'home_team': home,
                'away_team': away,
                'event_name': md.get('eventName'),
            }
    return {
        'stream_files': files,
        'parsed_markets': parsed,
        'supported_secondary_markets': relevant,
        'events': list(events.values()),
        'market_types': dict(sorted(market_types.items(), key=lambda kv: (-kv[1], kv[0]))),
        'supported_patterns': SUPPORTED_MARKET_TYPE_PATTERNS,
    }


def stage_archive_links(con, paths: Iterable[str | Path]):
    sid = source_id_and_capabilities(con)
    scan = scan_archives(paths)
    staged = stage_fixture_link_proposals(con, sid, scan['events'])
    return {'source_id': sid, **staged, 'scan': scan}


def approve_date_team_time_unknown(con, *, acknowledge=False, reviewer_note='OPERATOR_APPROVED_DATE_TEAMS_CANONICAL_TIME_UNKNOWN'):
    """Explicitly approve unique same-date/exact-team proposals when canonical kickoff time is unknown.

    Historical Football-Data rows use 00:00:00Z as a date-only sentinel. This function never fuzzy-matches,
    never changes canonical kickoff time, and requires an explicit operator acknowledgement.
    """
    if not acknowledge:
        raise ValueError('Explicit --acknowledge-canonical-time-unknown is required')
    sid=source_id_and_capabilities(con)
    rows=con.execute("SELECT * FROM fixture_link_proposals WHERE source_id=? AND status='PENDING' AND match_method='DATE_TEAMS_REVIEW' AND confidence='B'",(sid,)).fetchall()
    approved=skipped=0
    from .advanced import link_external_fixture
    for r in rows:
        try:
            evidence=json.loads(r['evidence_json'] or '{}')
        except Exception:
            evidence={}
        provider=evidence.get('provider') or {}
        cko=str(evidence.get('canonical_kickoff') or '')
        pko=str(provider.get('kickoff_utc') or '')
        if len(cko)<19 or cko[11:19] != '00:00:00' or cko[:10] != pko[:10]:
            skipped+=1
            continue
        cr=con.execute("""SELECT th.canonical_name h,ta.canonical_name a FROM fixtures f
                          JOIN teams th ON th.team_id=f.home_team_id JOIN teams ta ON ta.team_id=f.away_team_id
                          WHERE f.fixture_id=?""",(r['fixture_id'],)).fetchone()
        if not cr or normalize_name(cr['h'])!=normalize_name(provider.get('home_team')) or normalize_name(cr['a'])!=normalize_name(provider.get('away_team')):
            skipped+=1
            continue
        evidence['reviewer_note']=reviewer_note
        evidence['proposal_id']=r['proposal_id']
        evidence['time_contract']='CANONICAL_DATE_KNOWN_KICKOFF_TIME_UNKNOWN_BETFAIR_TIME_NOT_WRITTEN_BACK'
        link_external_fixture(con,sid,r['source_fixture_key'],r['fixture_id'],'MANUAL_VERIFIED',evidence)
        con.execute("UPDATE fixture_link_proposals SET status='APPROVED',reviewed_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE proposal_id=?",(r['proposal_id'],))
        approved+=1
    con.commit()
    return {
      'approved':approved,'skipped':skipped,
      'contract':'Explicit operator approval; exact normalized date+home+away only; canonical midnight must represent unknown time; Betfair time is not silently written back.'
    }

def _last_point_at(points: list[tuple[int, float, float | None]], target_ms: int):
    # Data are chronological in official files, but sorting protects against concatenated/synthetic streams.
    best = None
    for pt, price, tv in points:
        if pt <= target_ms and (best is None or pt > best[0]):
            best = (pt, price, tv)
    return best


def _snapshot_targets(market_time: datetime):
    return {
        kind: market_time - offset
        for kind, offset in TARGET_OFFSETS.items()
    }


def _candidate_snapshot(parsed, snapshot_type: str):
    md = parsed['market_definition']
    market_time = _parse_iso(md.get('marketTime'))
    if market_time is None:
        return None
    target = _snapshot_targets(market_time)[snapshot_type]
    target_ms = int(target.timestamp() * 1000)
    desc = _market_descriptor(md.get('marketType'))
    if not desc:
        return None

    runners = []
    for rid, name in parsed['runner_names'].items():
        sel = _selection_key(name)
        if sel not in {'OVER', 'UNDER'}:
            continue
        pt = _last_point_at(parsed['runner_points'].get(rid, []), target_ms)
        if pt:
            runners.append({'runner_id': rid, 'runner_name': name, 'selection': sel, 'pt': pt[0], 'price': pt[1], 'tv': pt[2]})
    by_sel = {r['selection']: r for r in runners}
    if set(by_sel) != {'OVER', 'UNDER'}:
        return None

    pts = [by_sel['OVER']['pt'], by_sel['UNDER']['pt']]
    age_seconds = [max(0.0, target_ms - x) / 1000.0 for x in pts]
    skew = abs(pts[0] - pts[1]) / 1000.0
    return {
        'descriptor': desc,
        'market_time': market_time,
        'target': target,
        'target_ms': target_ms,
        'runners': [by_sel['OVER'], by_sel['UNDER']],
        'max_price_age_seconds': max(age_seconds),
        'pair_skew_seconds': skew,
    }


def ingest_archives(con, paths: Iterable[str | Path], *, tier='BASIC', snapshot_types=('ENTRY', 'CLOSING'), max_price_age_minutes=60.0, max_pair_skew_minutes=30.0):
    """Ingest supported Betfair total-corners/cards exchange snapshots.

    Strict rules:
    - fixture must already have an explicit fixture_source_link (stage/approve first),
    - only half-point corner/card O/U market types are normalized,
    - both OVER and UNDER must have a traded price at or before the target,
    - stale/asynchronous pairs beyond configured guardrails are skipped,
    - BASIC timestamps are classified APPROXIMATE despite carrying a precise publish timestamp because the tier is sampled at 1-minute intervals.
    """
    tier = str(tier).upper()
    if tier not in {'BASIC', 'ADVANCED', 'PRO'}:
        raise ValueError('tier must be BASIC/ADVANCED/PRO')
    snapshot_types = tuple(str(x).upper() for x in snapshot_types)
    bad = set(snapshot_types) - set(TARGET_OFFSETS)
    if bad:
        raise ValueError(f'Unsupported snapshot types: {sorted(bad)}')

    sid = source_id_and_capabilities(con)
    bookmaker = f'BETFAIR_EXCHANGE_LTP_{tier}'
    # Basic lacks liquidity; even ADVANCED/PRO are ingested via LTP here, so keep confidence B until a volume-aware gate is added.
    register_market_source(con, bookmaker, role='SHARP_REFERENCE', confidence='B',
                           notes=f'Betfair Exchange historical {tier} LTP. Two-sided no-vig normalized; confidence B until explicit liquidity gate is applied.')

    counts = {'stream_files': 0, 'parsed_markets': 0, 'supported_markets': 0, 'linked_markets': 0, 'rows_inserted': 0,
              'groups_normalized': 0, 'skipped_unlinked': 0, 'skipped_stale_pair': 0, 'skipped_non_half_line': 0,
              'unsupported_market_types': {}}

    for logical_name, raw in iter_stream_files(paths):
        counts['stream_files'] += 1
        parsed = parse_market_file(logical_name, raw)
        if not parsed:
            continue
        counts['parsed_markets'] += 1
        md = parsed['market_definition']
        mt = str(md.get('marketType') or 'UNKNOWN')
        desc = _market_descriptor(mt)
        if not desc:
            counts['unsupported_market_types'][mt] = counts['unsupported_market_types'].get(mt, 0) + 1
            continue
        counts['supported_markets'] += 1
        # Push lines (whole numbers) are intentionally not mapped to our binary >=k OOS targets.
        line = float(desc['line'])
        if abs((line * 2) - round(line * 2)) > 1e-8 or abs(line - math.floor(line) - 0.5) > 1e-8:
            counts['skipped_non_half_line'] += 1
            continue

        eid = str(md.get('eventId') or '')
        try:
            fixture_id = resolve_linked_fixture(con, sid, eid)
        except KeyError:
            counts['skipped_unlinked'] += 1
            continue
        counts['linked_markets'] += 1

        for st in snapshot_types:
            snap = _candidate_snapshot(parsed, st)
            if not snap:
                continue
            if snap['max_price_age_seconds'] > max_price_age_minutes * 60 or snap['pair_skew_seconds'] > max_pair_skew_minutes * 60:
                counts['skipped_stale_pair'] += 1
                continue
            requested_at = snap['target'].isoformat().replace('+00:00', 'Z')
            line_key = format(line, '.6g')
            for r in snap['runners']:
                observed_at = _utc_iso_ms(r['pt'])
                age = max(0.0, snap['target_ms'] - r['pt']) / 1000.0
                raw_hash = stable_id('betfair-hist-row', parsed['market_id'], r['runner_id'], st, r['pt'], r['price'], tier)
                oid = stable_id('odds', fixture_id, sid, bookmaker, desc['market_key'], '', r['selection'], line_key, st, raw_hash)
                con.execute('''INSERT OR IGNORE INTO odds_snapshots(
                    odds_snapshot_id,fixture_id,source_id,bookmaker,market_family,market_key,selection_key,line,line_key,decimal_odds,
                    observed_at,timestamp_quality,snapshot_type,is_sharp,is_execution,reference_confidence,raw_column,source_row_hash,
                    provider_event_id,requested_snapshot_at,snapshot_basis,bookmaker_last_update,participant_type,participant_name,participant_key,
                    provider_participant_key,exchange_market_id,exchange_runner_id,source_tier,price_age_seconds,pair_skew_seconds,runner_traded_volume)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (oid, fixture_id, sid, bookmaker, desc['family'], desc['market_key'], r['selection'], line, line_key, float(r['price']),
                     observed_at, 'APPROXIMATE' if tier == 'BASIC' else 'EXACT', st, 1, 0, 'B', mt, raw_hash, eid, requested_at,
                     f'BETFAIR_{tier}_LTP_TARGET', observed_at, None, None, '', str(r['runner_id']), parsed['market_id'], str(r['runner_id']), tier,
                     age, snap['pair_skew_seconds'], r.get('tv')))
                counts['rows_inserted'] += con.execute('SELECT changes()').fetchone()[0]
            try:
                if normalize_snapshot_group(con, fixture_id, bookmaker, desc['market_key'], st, source_id=sid, line_key=line_key,
                                            requested_snapshot_at=requested_at, participant_key=''):
                    counts['groups_normalized'] += 1
            except Exception:
                pass
    con.commit()
    counts['unsupported_market_types'] = dict(sorted(counts['unsupported_market_types'].items(), key=lambda kv: (-kv[1], kv[0])))
    return counts


def readiness(con):
    sid = source_id_and_capabilities(con)
    rows = con.execute('''SELECT market_family,market_key,snapshot_type,bookmaker,COUNT(*) n,
                                 COUNT(DISTINCT fixture_id) fixtures,
                                 SUM(CASE WHEN no_vig_probability IS NOT NULL THEN 1 ELSE 0 END) normalized
                          FROM odds_snapshots WHERE source_id=?
                          GROUP BY market_family,market_key,snapshot_type,bookmaker ORDER BY market_family,market_key,snapshot_type''',(sid,)).fetchall()
    return {
        'source_id': sid,
        'rows': [dict(r) for r in rows],
        'fixture_links': con.execute('SELECT COUNT(*) n FROM fixture_source_links WHERE source_id=?',(sid,)).fetchone()['n'],
        'pending_link_proposals': con.execute("SELECT COUNT(*) n FROM fixture_link_proposals WHERE source_id=? AND status='PENDING'",(sid,)).fetchone()['n'],
        'contract': 'No secondary market is promotion-ready unless real linked, two-sided, pre-match market rows exist and validation passes.'
    }
