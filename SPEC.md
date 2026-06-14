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
| `system.compute.node_timeline` | **Real driver/worker CPU & memory utilization** (`cpu_user_percent`, `mem_used_percent`, `driver` flag) — the evidence base for right-sizing rules |
| `system.query.history` | Per-statement execution: duration, `executed_by`, `statement_type`, warehouse |
| `system.lakeflow.job_run_timeline` / `job_task_run_timeline` | Run outcomes & retries (schema recently renamed from `system.workflow.*` → verify in target workspace) |
| `system.access.audit` | Optional: who changed configs |
| _(external)_ Cloud public price catalog (Azure Retail Prices / AWS Pricing) | VM/infra $ for **full-TCO** costing (see §6). Fetched as a region-scoped public catalog, snapshotted per run; **no workspace data in the request.** |

> **Schema caveat for the agent:** column names and the lakeflow/workflow schema name evolve. The engine must **introspect** (`DESCRIBE`/`information_schema`) and fail soft on a missing column rather than hard-crash. Verify against the customer's actual workspace at runtime.
>
> **node_timeline caveat:** utilization data may be partial or absent on some clusters/workspaces. **Never fabricate missing metrics** — a missing value lowers a finding's confidence or drops it, it does not get a made-up default. (This is the one thing not to copy from the prototype notebook.)

## 6. Cost attribution (the shared join)

Tellurite quantifies on **full TCO = DBU cost + VM/infrastructure cost**, not DBU-only. On classic compute the cloud bills the underlying VMs separately (outside `system.billing.usage`), so a DBU-only figure understates real spend. Both halves are load-bearing; every $-quantified rule reuses this layer.

**(a) DBU cost** — convert DBUs to list dollars (handles mid-period price changes). Prefer a promotional-aware price expression where present:

```sql
-- DBU $ attributed to whatever grain the rule groups by
SELECT
  /* rule-specific grouping cols */,
  SUM(u.usage_quantity * COALESCE(lp.pricing.promotional.default, lp.pricing.effective_list.default)) AS dbu_cost_usd
FROM system.billing.usage u
JOIN system.billing.list_prices lp
  ON  u.sku_name = lp.sku_name
  AND u.usage_end_time >= lp.price_start_time
  AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
WHERE u.usage_date >= DATE_SUB(current_date(), :lookback_days)
GROUP BY 1
```

**(b) VM/infra cost** — driver + workers × node-type hourly price × runtime hours, from a **price table snapshotted at run start**:
- Cloud is parameterized: `--cloud azure|aws`. The engine fetches the region's **public** price catalog (Azure Retail Prices / AWS Pricing), then filters locally to the node types actually observed.
- The fetch is a pure inbound pull of a public catalog — the request is parameterized on **cloud + region only** and carries **no workspace/job/cluster identifiers**. This satisfies the no-egress guarantee (Golden Rule #3): no workspace data leaves, it only pulls reference data in.
- **Determinism (Golden Rule #4):** the fetched catalog is snapshotted once per run; re-running against the same snapshot reproduces output. Ship a bundled fallback table for offline/air-gapped runs.

`list_cost_usd` (the column rules' `savings_model` references) is `dbu_cost_usd + vm_cost_usd`.

Notes:
- Figures are **list-price estimates**; actual invoice may differ via private discounts/commit. Label clearly ("estimated at list price; your effective rate may be lower"). Optional `discount_pct` param scales the figure.
- Currency from `list_prices.currency_code` (DBU side) and the cloud catalog's `currencyCode` (VM side); surface it and flag any mismatch in the report.

**Express savings as a % of attributed cost, not just absolute $.** Every finding carries a `savings_pct = monthly_waste_usd / attributed_cost` alongside the dollar figure. This matters because the dollar amount is list-based, but the **percentage is invariant to the customer's actual rate**: if they run on promotional/negotiated pricing, their absolute $ differs from our list estimate, yet the *proportion* of spend that's wasted (and recoverable) stays true. So the % gain is the figure that holds regardless of discount.
- Display the % **inline** next to each finding's $ and beside the price-basis/API-snapshot info, so the real, discount-independent benefit is always visible.
- A toggle controls inline display of the percentage (default on); the underlying `savings_pct` is always computed and present in the JSON output.
- The headline shows total waste as both $ and **% of total attributed spend** — the % being the claim that survives any discount the customer actually has.

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

**The `list_cost_usd` a rule's `savings_model` sees is the full-TCO total** that the engine assembles (`dbu_cost_usd + vm_cost_usd`, §6) — **not** a column the rule SQL must compute itself (*decided: Option A*). A rule's `detection_sql` returns its cost columns (e.g. `dbu_cost_usd`, and the VM inputs the engine needs); the engine adds VM cost and exposes the combined `list_cost_usd` to the savings-model evaluator. Rules therefore stay decoupled from the DBU/VM split and the YAML never changes when the VM-cost layer lands. (Until M1.5, `vm_cost_usd = 0`, so `list_cost_usd == dbu_cost_usd`.)

Engine maps each detection row → `Finding{rule_id, instance_key, monthly_waste_usd, attributed_cost_usd, savings_pct, severity, confidence, evidence, remediation}` and ranks by `monthly_waste_usd`. `savings_pct = monthly_waste_usd / attributed_cost_usd` (the discount-invariant figure, §6); it is `None` for findings with no attributed $ basis (e.g. governance findings whose `savings_model` is `"0"`).

## 8. Detection catalog v1

Seeded in `rules/` (see `rules/detection-rules.yaml` for full definitions + SQL). Build order = ROI order.

| # | Rule | Category | Effort | Notes |
|---|------|----------|:--:|------|
| 1 | Jobs on all-purpose compute | compute_config | Low | Anchor rule; clear $ |
| 2 | Idle / underutilized SQL warehouses | idle | Med | warehouse_events vs query.history |
| 3 | Missing / excessive auto-termination | compute_config | Low | clusters config |
| 4 | Untagged spend (can't attribute) | attribution | Low | enables chargeback upsell |
| 5 | Retry / failure waste | orchestration | Med | lakeflow run timelines |
| 6 | Over-provisioned autoscale / oversized clusters | compute_config | Med | **now evidence-backed via `node_timeline`** (not a config guess); worker CPU <20% → ~40% savings, <35% → ~25% |
| 7 | Over-frequent schedules | orchestration | Med | cadence vs freshness need |
| 8 | Photon spend review | compute_config | Low | report share; manual judgment |
| 9 | Serverless sprawl / misattribution | compute_config | Med | serverless usage by job/run_as |
| 10 | Expensive long-running queries | query | Med | query.history top-N |
| 11 | Full-refresh candidates (→ incremental) | orchestration | Med | dbt-aware heuristic |
| 12 | VM family/generation mismatch | compute_config | Med | `node_timeline` + node-type: memory-optimized node at low mem use; v3→v4/v5 upgrade (~15%) |

Start with **1–4** (fast, high-confidence, demo-ready), then 5/10. Rules **6 & 12** depend on the `node_timeline` utilization join + the VM-cost half of §6, so they land alongside full-TCO costing. Then the remaining heuristic ones.

> **Utilization-based right-sizing (rules 6 & 12)** is the highest-value capability carried over from the prototype "Team Pipeline Cost & Resource Usage Calculator" notebook: real CPU/mem evidence turns right-sizing from a heuristic into a defensible $ finding. Reuse the notebook's thresholds and VM-family classification (E=memory, F=compute, D=general, L=storage; `_v3`→`_v4/_v5`), but **not** its fabricated-fallback metrics (see §5 caveat).

## 9. Output / deliverable spec

Single `report.html` (+ `report.md` for pasting). Sections:
1. **Headline** — total estimated monthly waste ($ **and % of total attributed spend**), # findings by severity. The % is the discount-invariant claim (§6).
2. **Top findings** — ranked table: rule, instance, $/mo, **savings % (inline)**, confidence, one-line fix.
3. **Per-finding detail** — evidence (the offending job/cluster/warehouse), why it's wasteful, the fix, a PR hint. Show **$ and savings % inline**, next to the price-basis / API-snapshot info, so the real proportional benefit is visible even where the customer's rate differs from list.
4. **Spend overview** — by product/SKU, by tag (and an "untagged" call-out). Show the **DBU vs VM/infra** split (full-TCO basis, §6).
5. **Methodology & caveats** — read-only, **full-TCO list-price basis** (DBU + VM, cloud + price-snapshot date), lookback window, confidence legend. State that **absolute $ is list-based but savings % holds under promotional/negotiated pricing**.

Inline display of the savings % is controlled by a toggle (default on); the value is always present in the JSON output regardless.

## 10. Tech stack

- **Python 3.11+**, `databricks-sql-connector` (or SDK), `pydantic` (Finding model), `pyyaml`, `jinja2`, `rich`/`click` (CLI), `pytest`.
- Packaging: `pyproject.toml`; entry point `tellurite-audit`.
- Cloud is parameterized (`--cloud azure|aws`) for the VM-price half of cost attribution (§6). A bundled fallback price table ships for offline/air-gapped runs.
- `--savings-pct / --no-savings-pct` toggles inline display of the discount-invariant savings % (default on; always in JSON regardless — §6, §9).
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
│   ├── attribution.py       ← DBU (usage×list_prices) + VM cost join → full TCO (§6)
│   ├── pricing.py           ← cloud VM price fetch/snapshot (azure|aws); bundled fallback
│   ├── models.py            ← pydantic Finding/Rule
│   └── report.py            ← Jinja render
├── rules/
│   └── detection-rules.yaml ← seed catalog (provided)
├── sql/
│   └── cost_attribution.sql ← shared snippet (provided)
├── data/
│   └── vm_prices_fallback.* ← bundled offline price table (azure|aws)
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
- Implement `attribution.py` (DBU cost) and rules **1–4**. Engine produces ranked `Finding`s as JSON.
- ✅ Done when: a JSON report from your real workspace shows ≥1 real finding per rule with sane $.

**M1.5 — Full-TCO costing + utilization right-sizing**
- Implement `pricing.py` (cloud VM price snapshot, `--cloud azure|aws`, bundled fallback); extend `attribution.py` to `dbu_cost + vm_cost`; add the `node_timeline` utilization join; ship rules **6 & 12**.
- ✅ Done when: findings carry a DBU/VM split and ≥1 evidence-backed right-sizing finding appears; re-run on the same price snapshot is identical.

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
- The **one** outbound network call is a pull of the cloud's public VM price catalog (cloud + region only, no workspace identifiers); nothing about the workspace leaves. It can be disabled in favour of the bundled fallback price table for air-gapped runs.
- When validating on your employer's workspace: it's a legitimate cost-optimization activity squarely in your remit, but treat outputs as internal, give your manager a heads-up, and **anonymize** workspace/job/cluster names before any output becomes a public case study or sales sample.
- Sample reports for marketing must use synthetic or fully anonymized data.

## 16. Open questions

- Currency/locale defaults (you'll likely see EUR list prices — confirm).
- Audit pricing anchor: flat €750–€1,500 vs % of monthly spend.
- OSS scope: how thin is the free scanner vs the paid rule library?
