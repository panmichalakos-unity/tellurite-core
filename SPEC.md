# SPEC — Databricks Cost Doctor

> Read-only diagnostic that tells a Databricks team **why** they're overspending and **how to fix it** — as ranked findings with dollar figures and remediation, not another dashboard.

**Status:** v0 (pre-MVP) · **Owner:** Panos · **Last updated:** _(set on first commit)_

---

## 1. Problem

Databricks bills are large and opaque. Native tooling (usage dashboards, budgets, alerts) reports **what** was spent and can alert on thresholds, but it does not diagnose **root cause** or prescribe **fixes**. Teams without a dedicated FinOps function leak money on compute misconfiguration, orchestration anti-patterns, and idle resources they can't easily see.

The Cost Doctor closes the gap between *reporting* and *remediation*.

## 2. Goals / Non-goals

**Goals (MVP)**
- Run **read-only** against a workspace's `system.*` tables and produce a prioritized, dollar-quantified findings report.
- Ship ~10–12 high-signal detection rules covering compute config, orchestration, and idle waste.
- Be **data-driven**: rules live in config (`rules/*.yaml`), engine is generic, adding a rule = adding a file.
- Be **trustable by a stranger's security team**: no business data touched, no data egress, source inspectable.

**Non-goals (MVP — explicitly later)**
- SQL/dbt **AST static analysis** (v2 — high effort, lower initial ROI).
- Auto-applying fixes / write access of any kind (always advisory; humans merge).
- Cross-warehouse support (Snowflake/BigQuery) — Databricks-only is the moat.
- A hosted multi-tenant SaaS (that's Rung 3; MVP runs in the customer's own workspace).

## 3. Product principles (non-negotiable)

1. **Read-only.** Only `SELECT` on `system.*`. Never touch user/business catalogs.
2. **No egress.** Output is a self-contained file generated inside the customer's environment. Nothing phones home.
3. **Prescriptive, not just descriptive.** Every finding has a fix.
4. **Quantified.** Every finding has an estimated monthly $ impact and a confidence level.
5. **dbt/orchestration-aware.** Context a generic cost tool can't encode — this is the differentiation.
6. **Deterministic & idempotent.** Same inputs → same report. Safe to re-run.

## 4. Architecture (MVP = run-in-workspace)

```
┌─────────────────────────────────────────────────────────┐
│  Customer's Databricks workspace (nothing leaves here)   │
│                                                          │
│  system.billing.usage ─┐                                 │
│  system.billing.list_prices ─┤                           │
│  system.compute.clusters ─┤   ┌──────────────┐           │
│  system.compute.warehouse_events ─┼─▶│ Rule engine  │──▶ report.html / .md │
│  system.query.history ─┤        │ (rules/*.yaml)│        │
│  system.lakeflow.* ─────┘        └──────────────┘        │
│                          ▲                               │
│                          │ cost attribution join         │
│                          └─ usage × list_prices          │
└─────────────────────────────────────────────────────────┘
```

**Components**
- **Connector** — Databricks SQL connector / SDK; runs each rule's SQL. Config: host, warehouse/cluster, lookback window, currency.
- **Rule engine** — loads `rules/*.yaml`, executes each rule's SQL, applies its savings model, normalizes to a `Finding`.
- **Cost attribution** — shared logic joining `usage` to `list_prices` to convert DBUs → list $ (see §6).
- **Report generator** — Jinja → HTML + Markdown. Ranked findings, totals, per-finding fix.

**Deployment modes**
- **MVP / audit:** customer runs a notebook or `pip install`-ed CLI in their workspace; sends you the report (or screen-share).
- **Rung 3 (later):** service principal with `SELECT` on `system`, scheduled, trend + regression alerts.

## 5. Data sources

| System table | Used for |
|---|---|
| `system.billing.usage` | DBU consumption per hour; `usage_metadata` (job_id, job_run_id, job_name, cluster_id, warehouse_id, notebook_path, dlt_pipeline_id), `sku_name`, `custom_tags`, `billing_origin_product`, `identity_metadata.run_as` |
| `system.billing.list_prices` | Historical SKU $ pricing; `pricing.effective_list.default` for cost attribution |
| `system.compute.clusters` | Cluster config: autoscale min/max, auto-termination, node types, owner, tags |
| `system.compute.warehouse_events` | SQL warehouse start/stop/scaling events → uptime |
| `system.compute.node_types` | Node specs (for right-sizing context) |
| `system.query.history` | Per-statement execution: duration, `executed_by`, `statement_type`, warehouse |
| `system.lakeflow.job_run_timeline` / `job_task_run_timeline` | Run outcomes & retries (schema recently renamed from `system.workflow.*` → verify in target workspace) |
| `system.access.audit` | Optional: who changed configs |

> **Schema caveat for the agent:** column names and the lakeflow/workflow schema name evolve. The engine must **introspect** (`DESCRIBE`/`information_schema`) and fail soft on a missing column rather than hard-crash. Verify against the customer's actual workspace at runtime.

## 6. Cost attribution (the shared join)

Convert DBUs to list dollars (handles mid-period price changes). This is load-bearing; every $-quantified rule reuses it.

```sql
-- $ cost attributed to whatever grain the rule groups by
SELECT
  /* rule-specific grouping cols */,
  SUM(u.usage_quantity * lp.pricing.effective_list.default) AS list_cost_usd
FROM system.billing.usage u
JOIN system.billing.list_prices lp
  ON  u.sku_name = lp.sku_name
  AND u.usage_end_time >= lp.price_start_time
  AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
WHERE u.usage_date >= DATE_SUB(current_date(), :lookback_days)
GROUP BY 1
```

Notes:
- `effective_list.default` = list price; actual invoice may differ via private discounts/commit. Report **list $** and label it clearly ("estimated at list price; your effective rate may be lower"). Optional `discount_pct` param scales the figure.
- Currency from `list_prices.currency_code`; surface it in the report.

## 7. Detection rule model

Each rule is a YAML file in `rules/`. Engine contract:

```yaml
id: jobs_on_all_purpose          # unique, snake_case
title: "Jobs running on all-purpose compute"
category: compute_config         # compute_config | orchestration | idle | attribution | query
severity: high                   # high | medium | low
confidence: high                 # high | medium | low  (how sure the $ estimate is)
summary: "..."                   # one line, buyer-readable
signal_tables: [system.billing.usage]
parameters:
  lookback_days: 30
  jobs_compute_discount: 0.5     # assumed all-purpose→jobs DBU saving
detection_sql: |                 # returns one row per finding instance + a list_cost_usd
  ...
savings_model: "list_cost_usd * jobs_compute_discount"   # expression over result cols
remediation:
  summary: "Move these jobs to jobs compute."
  pr_hint: "In the job/cluster definition set data_security_mode + jobs compute SKU."
references:
  - https://docs.databricks.com/aws/en/admin/system-tables/billing
```

Engine maps each detection row → `Finding{rule_id, instance_key, monthly_waste_usd, severity, confidence, evidence, remediation}` and ranks by `monthly_waste_usd`.

## 8. Detection catalog v1

Seeded in `rules/` (see `rules/detection-rules.yaml` for full definitions + SQL). Build order = ROI order.

| # | Rule | Category | Effort | Notes |
|---|------|----------|:--:|------|
| 1 | Jobs on all-purpose compute | compute_config | Low | Anchor rule; clear $ |
| 2 | Idle / underutilized SQL warehouses | idle | Med | warehouse_events vs query.history |
| 3 | Missing / excessive auto-termination | compute_config | Low | clusters config |
| 4 | Untagged spend (can't attribute) | attribution | Low | enables chargeback upsell |
| 5 | Retry / failure waste | orchestration | Med | lakeflow run timelines |
| 6 | Over-provisioned autoscale | compute_config | Med | lower confidence; heuristic |
| 7 | Over-frequent schedules | orchestration | Med | cadence vs freshness need |
| 8 | Photon spend review | compute_config | Low | report share; manual judgment |
| 9 | Serverless sprawl / misattribution | compute_config | Med | serverless usage by job/run_as |
| 10 | Expensive long-running queries | query | Med | query.history top-N |
| 11 | Full-refresh candidates (→ incremental) | orchestration | Med | dbt-aware heuristic |

Start with **1–4** (fast, high-confidence, demo-ready), then 5/10, then the heuristic ones.

## 9. Output / deliverable spec

Single `report.html` (+ `report.md` for pasting). Sections:
1. **Headline** — total estimated monthly waste ($ + % of spend), # findings by severity.
2. **Top findings** — ranked table: rule, instance, $/mo, confidence, one-line fix.
3. **Per-finding detail** — evidence (the offending job/cluster/warehouse), why it's wasteful, the fix, a PR hint.
4. **Spend overview** — by product/SKU, by tag (and an "untagged" call-out).
5. **Methodology & caveats** — read-only, list-price basis, lookback window, confidence legend.

## 10. Tech stack

- **Python 3.11+**, `databricks-sql-connector` (or SDK), `pydantic` (Finding model), `pyyaml`, `jinja2`, `rich`/`click` (CLI), `pytest`.
- Packaging: `pyproject.toml`; entry point `tellurite-audit`.
- Lint/format: `ruff` + `ruff format`. Typed throughout.

## 11. Repo structure

```
tellurite-core/
├── SPEC.md                  ← this file
├── PRODUCT-OVERVIEW.md      ← pitch / sales doc
├── CLAUDE.md                ← agent instructions (Cursor + Claude Code)
├── README.md                ← quickstart + how to extend
├── pyproject.toml
├── tellurite/
│   ├── __init__.py
│   ├── cli.py               ← `tellurite-audit run --profile ...`
│   ├── connector.py         ← Databricks SQL execution, introspection
│   ├── engine.py            ← load rules, run, build Findings
│   ├── attribution.py       ← the usage×list_prices join
│   ├── models.py            ← pydantic Finding/Rule
│   └── report.py            ← Jinja render
├── rules/
│   └── detection-rules.yaml ← seed catalog (provided)
├── sql/
│   └── cost_attribution.sql ← shared snippet (provided)
├── templates/
│   └── report.html.j2       ← (Claude Code generates)
└── tests/
    └── test_engine.py       ← rule-contract + golden tests
```

## 12. Milestones

**M0 — Scaffold (½ day)**
- Repo init, `pyproject.toml`, deps, CLI stub, config/profile loading (host, warehouse, lookback, currency, discount).
- ✅ Done when: `tellurite-audit --help` runs; connects to your work workspace; runs a trivial `SELECT 1`.

**M1 — Attribution + 4 anchor rules (Week 1)**
- Implement `attribution.py` and rules **1–4**. Engine produces ranked `Finding`s as JSON.
- ✅ Done when: a JSON report from your real workspace shows ≥1 real finding per rule with sane $.

**M2 — Report generator (Week 1–2)**
- Jinja HTML + Markdown per §9. Ranked, dollar-quantified, per-finding fix.
- ✅ Done when: one polished `report.html` you'd be comfortable sending a buyer (anonymized).

**M3 — Rules 5 + 10 + hardening (Week 2)**
- Add retry-waste and expensive-queries; introspection/fail-soft for missing columns; `--discount-pct`.
- ✅ Done when: clean run on a second workspace with different config; no hard crashes on schema diffs.

**M4 — Audit-ready packaging (Week 3)**
- One-command run (`pip install` from git or a notebook), the methodology/caveats section, a one-page findings summary template.
- ✅ Done when: you can hand this to a friendly team and they self-run it, or you run it on a screen-share, and produce a sellable report.

_(v2 backlog: SQL/dbt AST analysis, remediation-PR generation via Claude Code, hosted monitoring + regression alerts, multi-workspace.)_

## 13. Success metrics

- **Build:** M4 reached in ≤3 weeks of evenings.
- **Validation:** identifies ≥10% estimated-waste on the first real workspace (yours).
- **Commercial:** 1 paid audit within 30 days of M4; first €99–499/mo monitoring within 90 days.

## 14. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Databricks ships more native FinOps | Own **remediation + dbt/process context**; reporting is not the moat |
| "Free script" undercuts pricing | OSS a thin scanner as lead-gen; keep remediation library + monitoring paid |
| Schema drift across workspaces | Introspect at runtime; fail soft; version-pin rule SQL with caveats |
| List vs effective price gap | Label as list-price estimate; `--discount-pct` flag; never overclaim savings |
| Long sales cycle for monitoring | Cheap fixed-fee audit is the wedge that earns the monitoring upsell |

## 15. Data handling & responsible use

- The tool reads **only `system.*` and resource metadata** — never customer/business data. Keep it that way; it's both the security story and the ethics.
- When validating on your employer's workspace: it's a legitimate cost-optimization activity squarely in your remit, but treat outputs as internal, give your manager a heads-up, and **anonymize** workspace/job/cluster names before any output becomes a public case study or sales sample.
- Sample reports for marketing must use synthetic or fully anonymized data.

## 16. Open questions

- Currency/locale defaults (you'll likely see EUR list prices — confirm).
- Audit pricing anchor: flat €750–€1,500 vs % of monthly spend.
- OSS scope: how thin is the free scanner vs the paid rule library?
