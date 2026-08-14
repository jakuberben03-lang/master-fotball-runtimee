# MASTER Data + Market Engine v2.4 — STATS MONITOR DELIVERY

**Build:** 2026-08-13  
**Status:** IMPLEMENTED / TESTED / CURRENT NETWORK COLLECTION REQUIRES USER API KEY

## Novinka v2.4

MASTER Stats Monitor v1.0 je integrovaný přímo do Data + Market Engine.

Umí:
- refresh current fixture katalogu,
- pre-match lineup/injury snapshots,
- optional live team/player stat snapshots,
- post-match final team/player stat collection,
- canonical materialization až po final provider status,
- quota-aware scheduling,
- watchlist priority,
- Windows Task Scheduler helper,
- explicit provider provenance a observed_at.

## Canonical DB po migraci

- fixtures: **15 768**
- team-match stats: **15 768**
- historical odds rows: **19 656**
- all feature snapshots: **59 435**
- PRE_FIXTURE feature snapshots: **58 726**
- current canonical player-match rows: **0**
- lineup snapshots: **0**
- API-Football fixture links: **0**
- team stat monitor snapshots: **0**
- player stat monitor snapshots: **0**
- ACTIVE models: **0**
- schema version: **2.4.0**

Nulové current-monitor rows jsou správně: build prostředí nepoužilo uživatelův API key a žádná data nebyla synteticky vytvořena.

## Safety

- klíč pouze z `API_FOOTBALL_KEY`,
- žádné secrets v DB/reportu,
- exact/declared alias competition mapping, žádný fuzzy fixture merge,
- live data se nepromují na final,
- post-match data nevstupují zpětně do pre-match snapshotu stejného zápasu,
- finalizovaný fixture se zbytečně znovu nestahuje,
- free quota reserve chrání před vyčerpáním celého denního limitu,
- AET/PEN score se nepřepisuje jako 90min score bez bezpečné semantiky.

## Test

**38/38 PASS** + forward migration na skutečné canonical DB: PASS + database audit `ok=true`.
