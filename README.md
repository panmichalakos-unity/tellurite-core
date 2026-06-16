# Tellurite Audit

Read-only diagnostic that scans a Databricks workspace's `system.*` tables and produces a ranked, dollar-quantified report of cost waste — each finding with a specific fix. **Advisory only. No writes. No data egress.**

- **What it is / why:** `PRODUCT-OVERVIEW.md`
- **How it's built / milestones:** `SPEC.md`
- **Agent instructions (Cursor + Claude Code):** `CLAUDE.md`
- **Detection rules (the IP):** `rules/detection-rules.yaml`

## Safety (read before running)

- Strictly **read-only**: only `SELECT` on `system.*`. Never touches business/customer data.
- **No egress**: the report is written locally inside the workspace. Nothing phones home. The only outbound call is a pull of the cloud's **public** VM price catalog (cloud + region only — no workspace data leaves); it can be skipped for air-gapped runs via a bundled fallback price table.
- Validating on your employer's workspace is a legit cost-optimization task — keep outputs internal and **anonymize** before anything becomes a public sample. See `SPEC.md` §15.

## Quickstart (target state — Claude Code builds this)

```bash
# install (editable, from repo)
pip install -e .

# configure a profile: host, http_path/warehouse, auth (env vars / Databricks auth)
export DATABRICKS_HOST=... DATABRICKS_TOKEN=... DATABRICKS_HTTP_PATH=...

# run a scan over the last 30 days → ranked HTML + Markdown report (full-TCO: DBU + VM)
tellurite-audit run --profile work --lookback 30 --cloud azure --out report.html

# savings are shown as $ and as a % of cost; the % holds even under promo/negotiated pricing.
# hide the inline % with --no-savings-pct (it stays in the JSON output regardless)

# inspect / validate the rule catalog
tellurite-audit rules list
tellurite-audit rules check
```

Prereqs in the workspace: Unity Catalog enabled; `SELECT` granted on the `system` schemas you scan (billing is on by default; others may need enabling by an account admin).

### Authentication

Three modes, selected by `auth:` in a profile or `--auth`:

- **`cli` — Databricks unified auth (recommended, no PAT needed).** One-time login, then headless (tokens auto-refresh):
  ```bash
  winget install Databricks.DatabricksCLI          # or see docs for other OSes
  databricks auth login --host https://adb-XXXX.NN.azuredatabricks.net
  ```
  The SDK reads `~/.databrickscfg` (matched by host, or pin a section with `--databricks-profile`). `az login` also works if the Azure CLI is configured.
- **`oauth`** — connector-native browser login (no CLI install); token cached by the connector.
- **`pat`** — personal access token via `DATABRICKS_TOKEN` / `--token`.

Copy `profiles.example.yaml` → `profiles.yaml` (gitignored) to save a workspace, then:

```bash
tellurite-audit ping --profile work     # verify connectivity (SELECT 1)
tellurite-audit run  --profile work --out report.html
```

## How it works

`cli.py` → load profile + `rules/*.yaml` → `engine.run()` executes each rule's read-only SQL via `connector.py` → applies `savings_model` → builds ranked `Finding`s → `report.py` renders HTML/Markdown. Cost is **full TCO** (DBU + VM/infra): the DBU `usage × list_prices` join lives in `attribution.py` / `sql/cost_attribution.sql`, and VM prices come from `pricing.py` (cloud catalog snapshot, `--cloud azure|aws`, bundled fallback). The engine is generic — **all rule logic is data, not code.**

## Extending: add a detection rule

1. Add an entry to `rules/detection-rules.yaml` (schema in `SPEC.md` §7).
2. Write read-only `detection_sql` returning one row per finding + a `list_cost_usd` (or your `savings_model` columns) + evidence keys.
3. Set `savings_model`, `remediation`, `references`.
4. Add a golden test in `tests/`. Don't special-case the engine.

Full contract + guardrails: `CLAUDE.md`.

## Status

Pre-MVP scaffold. Build order: **M0** scaffold → **M1** attribution + rules 1–4 → **M2** report → **M3** rules 5 & 10 + hardening → **M4** audit-ready. See `SPEC.md` §12.

## Roadmap (v2)

SQL/dbt AST analysis · remediation-PR generation (Claude Code) · hosted scheduled monitoring + regression alerts · multi-workspace.
