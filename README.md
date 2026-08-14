# MASTER Football — Stats Monitor 24/7 runtime

Canonical basis: MASTER Football v2.4.2 / Data + Market Engine v2.4 / Stats Monitor v1.0.

## What this repository does

- Runs the canonical Stats Monitor every hour with GitHub Actions.
- Refreshes the current Big-5 + Czech top flight + UEFA fixture catalog once per UTC day.
- Collects leakage-safe PRE-MATCH and POST-MATCH snapshots.
- Keeps LIVE collection off by default to protect free API quota.
- Persists the cumulative SQLite DB + raw provider responses between ephemeral GitHub runners using one rolling Actions artifact.
- Deletes older state artifacts after a successful new upload so storage does not grow by one full DB every hour.

## Required one-time setup

1. Upload this folder to the repository root.
2. GitHub → Settings → Secrets and variables → Actions → New repository secret.
3. Name: `API_FOOTBALL_KEY`.
4. Value: your API-Football key.
5. GitHub → Actions → `MASTER Stats Monitor 24/7` → Run workflow.

After the first successful run, the scheduled trigger runs at minute 17 of every hour (UTC).

## Persistence design

GitHub-hosted runners are disposable. The workflow therefore restores the newest `master-monitor-state-*` artifact before collection, then uploads a new cumulative `state.tgz`. It contains:

- `data/master_monitor.db`
- `data/raw/stats_monitor/`
- `data/monitor_status.json`
- `data/.last_catalog_refresh`

The repository intentionally does **not** include the 225MB historical canonical DB. This runtime is dedicated to current-season collection. The monitor DB can later be merged/audited against the canonical MASTER DB using the normal Data Engine workflow.

## Safety

- API key is only read from `API_FOOTBALL_KEY`.
- No fuzzy fixture merge.
- PRE/LIVE/POST timing firewall remains intact.
- LIVE collection remains OFF by default.
- Stats history does not promote any model.
- Current betting authority stays 8 PROVISIONAL / 12 NO MODEL / 0 ACTIVE.

## Public-repository scheduler keepalive

GitHub can disable scheduled workflows in public repositories after a long period with no repository activity. The workflow therefore writes one tiny successful-status heartbeat commit per UTC day (`monitor/last_success.json` + `monitor/heartbeat_date.txt`). It never commits the SQLite database, raw API payloads, or secrets.
