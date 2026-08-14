# MASTER Football FREE Multi-Provider Patch v2.4.3-RC1

**Status:** IMPLEMENTED LOCALLY / TESTED / NOT YET DEPLOYED TO LIVE GITHUB RUNTIME

## Důvod
První live GitHub run Stats Monitoru prošel technicky SUCCESS, ale API-Football Free pro sezonu 2026 vrátil provider-plan blocker a 0 fixture rows. Proto tento patch odstraňuje závislost current collectoru na placeném API-Football tarifu.

## Implementováno
- `master_data/free_current_monitor.py`
  - OpenFootball 2026/27 current schedule ingestion pro ověřené cesty EPL / Bundesliga / La Liga / Serie A,
  - explicitní kickoff precision (`EXACT`, `INHERITED_SAME_BLOCK`, `DATE_ONLY`),
  - provider-native source links bez fuzzy merge,
  - Football-Data.co.uk public current fixture + odds recorder s MASTER fetch timestampem,
  - TheSportsDB free v1 enrichment (`123`) s exact date + declared-alias verification,
  - team event-stat snapshots,
  - time-safe lineup snapshots,
  - explicitní coverage gaps.
- `scripts/run_free_current_monitor.py`
- GitHub Actions workflow přepnutý na FREE-first režim; `API_FOOTBALL_KEY` je optional.
- hard fail `BLOCKED_NO_CURRENT_FREE_SOURCE`, pokud žádný free current zdroj skutečně nic nedodal.

## Safety
- žádný fuzzy fixture merge,
- žádný vymyšlený historical observed_at,
- DATE_ONLY != exact kickoff,
- post-kickoff lineup != pre-match known lineup,
- team event stats se nepřepisují na fake player shots/SOT/fouls,
- žádná změna model registry.

## Testy
- new free-monitor tests: **4/4 PASS**,
- canonical Data + Market Engine v2.4 regression suite: **38/38 PASS**.

## Co není hotové
- live GitHub deployment nebyl proveden, protože GitHub ChatGPT integration stále vrací 403 pro write actions;
- full current Ligue 1 catalog ještě není ověřen z použitého OpenFootball path;
- full current CZ top-flight ingest potřebuje robustní official/free parser;
- full UEFA 2026/27 current catalog potřebuje ověřený free current source;
- complete player-match shots/SOT/fouls free layer stále chybí.

## Betting authority
Beze změny: **8 PROVISIONAL / 12 NO MODEL / 0 ACTIVE**.
