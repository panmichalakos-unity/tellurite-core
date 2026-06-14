# CLAUDE.md — Agent Instructions

Context and guardrails for **Claude Code** and **Cursor** working on this repo. Read this before generating code. Full intent is in `SPEC.md`; the catalog is in `rules/detection-rules.yaml`.

> Cursor note: this file is the canonical agent context. If Cursor isn't picking it up, mirror it to `.cursor/rules/tellurite.mdc` (or symlink `AGENTS.md` → `CLAUDE.md`).

## What this project is

A read-only CLI/notebook tool that scans a Databricks workspace's `system.*` tables and produces a ranked, dollar-quantified report of cost waste, with a specific remediation per finding. Diagnostic and advisory only.

## Golden rules (never violate)

1. **READ-ONLY.** Generate only `SELECT` statements. Never `INSERT/UPDATE/DELETE/MERGE/CREATE/DROP/ALTER`, never write to any table, never modify cluster/job/warehouse config. The tool *recommends* changes; humans apply them.
2. **`system.*` ONLY.** Query `system.billing.*`, `system.compute.*`, `system.query.*`, `system.lakeflow.*` (verify schema name at runtime), `system.access.*`. Never query user/business catalogs. No business data ever enters the process.
3. **NO EGRESS.** No network call may send workspace data anywhere. Output is written to local files only. No telemetry, no "phone home." **One allowed exception:** fetching a cloud's **public price catalog** (Azure Retail Prices / AWS Pricing) for full-TCO costing — this is an *inbound pull* parameterized on **cloud + region only**, carrying zero workspace/job/cluster identifiers. Snapshot it per run (Rule #4) and prefer the bundled fallback table when offline. If you can't fetch prices without including workspace identifiers in the request, don't fetch.
4. **DETERMINISTIC & IDEMPOTENT.** Same inputs → same output. Re-running is always safe.
5. **FAIL SOFT on schema drift.** Column/schema names vary across workspaces and Databricks releases. Introspect (`DESCRIBE` / `information_schema`) and skip a rule with a clear warning rather than crashing the whole run.

## Stack & conventions

- Python **3.11+**, fully type-hinted. `pydantic` models, `pyyaml`, `jinja2`, `click` CLI, `databricks-sql-connector` (or SDK), `pytest`.
- Lint/format: `ruff` + `ruff format`. No unused imports, no dead code.
- Small, focused modules (see `SPEC.md` §11). Pure functions where possible; side effects (I/O, queries) isolated in `connector.py` and `report.py`.
- Config via a profile (host, http_path/warehouse, lookback_days, currency, discount_pct). Secrets via env vars or Databricks auth — **never hardcode tokens**, never commit secrets.
- Every SQL query is parameterized (lookback window, thresholds) — no string-concatenated user input.

## Architecture (keep it this shape)

`cli.py` → loads profile + rules → `engine.run()` → for each rule: `connector.query(rule.detection_sql)` → apply `savings_model` → build `Finding` → rank → `report.render()`.

- `attribution.py` holds the shared **full-TCO** cost join — DBU cost (`usage × list_prices`, see `sql/cost_attribution.sql`) **plus** VM/infra cost from `pricing.py`. Rules that need $ reuse it rather than re-deriving pricing. `list_cost_usd = dbu_cost_usd + vm_cost_usd`.
- `pricing.py` fetches/snapshots the cloud VM price catalog (`--cloud azure|aws`) with a bundled offline fallback; the network call obeys Golden Rule #3 (cloud + region only).
- Right-sizing rules (6 & 12) join `system.compute.node_timeline` for real CPU/mem utilization. **Never fabricate missing utilization** — missing metrics lower confidence or drop the finding (see `SPEC.md` §5).
- `models.py`: `Rule` (parsed YAML) and `Finding` (one detected instance with `monthly_waste_usd`, `savings_pct` (= waste / attributed cost; the discount-invariant figure), `severity`, `confidence`, `evidence`, `remediation`).
- The engine is **generic**: it must not contain rule-specific logic. All rule specifics live in YAML.

## How to add a detection rule (the core workflow)

1. Add a YAML entry in `rules/detection-rules.yaml` (or a new file in `rules/`) following the schema in `SPEC.md` §7.
2. Write `detection_sql` that returns one row per finding instance, including a `list_cost_usd` column (or the columns your `savings_model` references) and an `evidence`-friendly key (e.g. `job_id`, `warehouse_id`).
3. Set `savings_model` as an expression over the returned columns (e.g. `list_cost_usd * jobs_compute_discount`).
4. Add a golden test in `tests/` using a small fixture result set; assert the rule yields the expected `monthly_waste_usd` and ranking.
5. Do **not** modify the engine to special-case the rule. If a rule can't be expressed in the contract, propose a contract change in `SPEC.md` first.

**Definition of done for a rule:** parameterized read-only SQL; returns evidence + cost columns; savings model defined; references cited; golden test passing; runs without error against an introspected real workspace (fail-soft if a column is missing).

## Build order (from SPEC milestones)

M0 scaffold → M1 attribution + rules 1–4 → M1.5 full-TCO costing (`pricing.py`) + utilization right-sizing (rules 6 & 12) → M2 report → M3 rules 5 & 10 + hardening → M4 packaging. Don't jump ahead to v2 items (SQL/dbt AST analysis, PR generation, hosted monitoring) until M4 is done.

## What NOT to do

- ❌ No write operations, ever. ❌ No querying outside `system.*`. ❌ No network egress of workspace data (the public price-catalog *pull* in Rule #3 is the only allowed network call, and it sends no workspace data).
- ❌ Don't overstate savings — absolute figures are **full-TCO list-price estimates** (DBU + VM); always carry the confidence level and label the basis. Always also surface `savings_pct` — it's the discount-invariant claim that holds even under promotional/negotiated pricing.
- ❌ Don't fabricate missing `node_timeline` utilization — lower confidence or drop the finding instead.
- ❌ Don't hardcode DBU SKU prices — read them from `system.billing.list_prices`. (VM prices come from the cloud catalog via `pricing.py`; the bundled fallback table is the one sanctioned static price source, for offline runs.)
- ❌ Don't bake rule logic into the engine. ❌ Don't add a dependency without noting why.

## Commands (wire these up in M0)

```bash
tellurite-audit run --profile work --lookback 30 --cloud azure --out report.html   # full scan
tellurite-audit run --profile work --no-savings-pct --out report.html              # hide inline savings %
tellurite-audit rules list                                           # show loaded rules
tellurite-audit rules check                                          # validate rule YAML schema
pytest                                                           # run tests
ruff check . && ruff format --check .                            # lint
```
