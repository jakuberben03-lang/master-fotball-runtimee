# MASTER Stats Monitor v1.0 — automatický current-season collector

**Součást:** MASTER Data + Market Engine v2.4  
**Build:** 2026-08-13  
**Účel:** průběžně budovat vlastní leakage-safe historii aktuálních týmových a hráčských statistik.

## Co monitor sbírá

### PRE-MATCH
- aktuální fixture status,
- potvrzené sestavy, pokud už je provider zveřejnil,
- injuries/suspensions, pokud je provider pro fixture vrací,
- skutečný `observed_at` = čas našeho fetchu.

PRE-MATCH se opakuje pouze do doby, než je zachycena skutečná sestava. Historický backfill se nikdy nevydává za tehdejší pre-match knowledge.

### LIVE — volitelné
Live sběr je defaultně vypnutý kvůli free quota. Po zapnutí pouze pro watchlist umí ukládat:
- team shots / SOT / blocked shots,
- fouls,
- corners,
- cards,
- xG a possession, pokud je provider vrací,
- player minutes / shots / SOT / fouls / cards / dribbles.

LIVE snapshoty se ukládají časově, ale **nepřepisují canonical final stats**.

### POST-MATCH
Po dostatečném odstupu od kick-offu monitor zkusí získat finální data. Teprve pokud provider vrátí final status (`FT`, `AET`, `PEN`), uloží final snapshot a materializuje dostupné hodnoty do:
- `team_match_stats`,
- `player_match_stats`.

U `AET/PEN` se skóre nesmí potichu vydávat za 90minutové skóre. Final team/player event statistiky lze uložit, ale regulation goals se nehádat.

## Nové tabulky

- `fixture_stat_snapshots` — časové team-stat snapshoty,
- `player_stat_snapshots` — časové player-stat snapshoty,
- `stats_monitor_watchlist` — priority a live opt-in,
- `stats_monitor_state` — stav PRE/LIVE/POST, finalizace, poslední chyba.

Existující provenance a provider fetch logy zůstávají zachované.

## Zdroj

Current implementation používá API-Football přes `API_FOOTBALL_KEY` v environment variable. Klíč se nikdy neukládá do DB, reportu ani source souboru.

Engine používá free quota ledger a drží rezervu 15 requestů. Missing key = `SKIPPED`, ne fail s fake daty.

## První spuštění

Windows CMD:

```bat
set API_FOOTBALL_KEY=TVUJ_KLIC
python scripts\run_stats_monitor.py --bootstrap-defaults
```

Linux/macOS:

```bash
export API_FOOTBALL_KEY='TVUJ_KLIC'
PYTHONPATH=. python scripts/run_stats_monitor.py --bootstrap-defaults
```

`--bootstrap-defaults`:
1. jedním `/leagues?current=true` fetchem zjistí current provider IDs,
2. vybere Big-5 + Czech top flight + UEFA club competitions přes exact/declared alias matching,
3. stáhne jejich aktuální fixture katalogy,
4. zapíše provider fixture ID jako explicitní canonical link.

Pokud liga nejde jednoznačně spárovat, je reportována jako missing/ambiguous — žádný fuzzy merge.

## Normální automatický běh

Jednorázově:

```bash
PYTHONPATH=. python scripts/run_stats_monitor.py
```

Každou hodinu v běžícím procesu:

```bash
PYTHONPATH=. python scripts/run_stats_monitor.py --loop --interval-min 60
```

Live pouze pro explicitní watchlist:

```bash
PYTHONPATH=. python scripts/run_stats_monitor.py --loop --interval-min 20 --live --watchlist-only
```

## Windows Task Scheduler

PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\install_stats_monitor_windows_task.ps1
```

Default = každých 60 minut. Task používá stejný quota-safe scheduler; finalizovaný zápas se znovu zbytečně nestahuje.

## Watchlist

Přidat fixture:

```bash
PYTHONPATH=. python -m master_data.cli stats-monitor-add FIXTURE_ID --priority 100
```

Povolit u konkrétního fixture i live snapshots:

```bash
PYTHONPATH=. python -m master_data.cli stats-monitor-add FIXTURE_ID --priority 100 --live
```

Stav:

```bash
PYTHONPATH=. python -m master_data.cli stats-monitor-status
```

Plán dalšího cyklu bez fetchu:

```bash
PYTHONPATH=. python -m master_data.cli stats-monitor-targets
```

## Hard safety contract

- NO API KEY = SKIPPED.
- NO EXPLICIT PROVIDER FIXTURE LINK = NO STATS INGEST.
- NO FUZZY FIXTURE MERGE.
- FETCH TIME ≠ HISTORICAL PRE-MATCH TIME.
- LIVE SNAPSHOT ≠ FINAL CANONICAL STAT.
- POST-MATCH PLAYER DATA smí ovlivnit až budoucí fixtures.
- AET/PEN ≠ automaticky 90min score.
- DATA PRESENT ≠ MODEL ACTIVE.
- STATS COLLECTOR ≠ PLAYER MODEL PROMOTION.
- PROVISIONAL = MAX WATCH/SHADOW zůstává beze změny.

## Testy

**38/38 PASS**.

Testováno také jako forward migration nad kopií skutečné 235MB canonical DB. Po migraci zůstalo:
- 15 768 fixtures,
- 15 768 team stats,
- 19 656 historical odds,
- 59 435 feature snapshots,
- audit `ok=true`.
