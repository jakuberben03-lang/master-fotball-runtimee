# MASTER Football — FREE Multi-Provider Stats Monitor 24/7

Canonical basis: MASTER Football v2.4.2 / Data + Market Engine v2.4 / Stats Monitor v1.0 safety contract.
Patch runtime: **FREE MULTI-PROVIDER v1**.

## Why this patch exists
API-Football Free returned a provider-plan block for season 2026. The scheduler worked, but 2026 catalogs were empty. This patch therefore makes current collection independent of a paid API-Football plan.

## Current free stack

- **OpenFootball current schedules**: provider-native 2026/27 current catalogs where a verified current file exists (currently EPL, Bundesliga, La Liga, Serie A).
- **Football-Data.co.uk public fixture files**: timestamped by MASTER at actual fetch time; stores current fixture observations and bookmaker odds observations.
- **TheSportsDB free v1 (`123`)**: small-window enrichment only after exact date + declared-alias team verification; can collect lineups and team event statistics when available.
- **API-Football**: optional fallback only; `API_FOOTBALL_KEY` is no longer required for the FREE monitor.

## Safety

- no fuzzy fixture merge;
- no fabricated historical `observed_at`;
- DATE_ONLY schedule rows remain explicitly low precision;
- lineup captured after kickoff is not backfilled as pre-match known;
- TheSportsDB event stats are team-level and are **not** converted into fake player shots/fouls rows;
- gaps are printed in `monitor/last_success.json`;
- success requires at least one real current free-source catalog/observation;
- stats collection does not change model status: **8 PROVISIONAL / 12 NO MODEL / 0 ACTIVE**.

## Known coverage gaps in v1

- full current Ligue 1 catalog is not yet verified from the chosen OpenFootball current path;
- full current Czech top-flight catalog still needs a robust verified official/free ingest;
- full UEFA 2026/27 current catalog is not yet available from the verified OpenFootball repository path;
- current FREE enrichment does not provide complete player-match shots/SOT/fouls needed for Player Engine training.

Those are explicit gaps, not silent SUCCESS claims.
