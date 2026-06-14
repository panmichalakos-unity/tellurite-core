# Databricks Cost Doctor — Product Overview

_A pitch / explainer doc. Use it on a landing page, in a DM, or to describe the product out loud._

---

## One-liner

**Databricks already shows you what you spent. The Cost Doctor tells you why — and hands you the fix.**

Alt one-liners:
- "A read-only checkup that finds the money leaking out of your Databricks bill."
- "FinOps for the lakehouse, built by someone who actually runs dbt on Databricks."

## The problem (buyer language)

Your Databricks bill keeps climbing and nobody can say exactly why. The native dashboards show totals and let you set budget alerts, but they don't tell you *which* jobs, clusters, and warehouses are wasting money — or what to change. Unless you have a dedicated FinOps person (you don't), that money just keeps leaking.

Common culprits, all invisible on a totals dashboard:
- Jobs running on expensive all-purpose compute instead of jobs compute.
- Clusters that never auto-terminate, burning money while idle.
- SQL warehouses sitting on with no queries hitting them.
- Failed jobs retrying in a loop.
- "Full refresh" pipelines that should be incremental.
- Spend you can't even attribute because nothing's tagged.

## The solution — how it works

Three steps, **nothing leaves your environment**:

1. **Run** a read-only scan inside your own Databricks workspace (a notebook or one CLI command). It only reads system tables — never your business data.
2. **Get a report** that ranks every source of waste by estimated monthly dollars, with a confidence level and a specific fix for each.
3. **Fix the top items** — each finding comes with the exact change to make (and, in the paid tier, a ready-to-review PR).

## Why this, and not the alternatives

**vs. native Databricks cost tools:** Those *report and alert*. The Cost Doctor *diagnoses and prescribes*. It doesn't replace your dashboard — it tells you what to do about it.

**vs. a generic FinOps SaaS:** Generic tools treat Databricks like any cloud line item. They can't tell you that a job is on the wrong compute type, or that a model should be incremental, or how dbt scheduling drives your bill. This is warehouse-specific, dbt-aware judgment a horizontal tool can't fake.

**vs. a big consultancy:** No six-week engagement, no access headaches, no five-figure invoice. A focused checkup, fast, at a price that pays for itself many times over.

## The trust model (why your security team says yes)

This is usually the dealbreaker for data tooling — so it's the design center here:
- **Read-only**, system tables only. It cannot see or touch customer or business data.
- **No data egress.** The report is generated inside your workspace. Nothing phones home.
- **Inspectable.** You can read exactly what it runs before you run it.

That's the difference between "let me check with security" and "sure, run it."

## What you get

A single self-contained report:
- Headline: total estimated monthly waste, in dollars and as a % of spend.
- Ranked findings: each with the offending resource, the dollar impact, a confidence level, and the fix.
- Spend overview by product, SKU, and tag — including an "untagged / unattributable" call-out.
- Clear methodology and caveats (list-price basis, lookback window).

## Packages & pricing

| Tier | What it is | Price |
|---|---|---|
| **Cost Audit** | One-off checkup: you run the scan, I deliver a prioritized findings report + a 45-min readout. | **€750–€1,500** |
| **Self-serve Scanner** | The scanner licensed to run yourself, anytime. | **€199–€499** (one-off / annual) |
| **Hosted Monitoring** | Scheduled scans, cost-trend tracking, regression alerts ("Job X cost +40% this week"), monthly health report. | **€99–€499/mo** solo·small · **€2k–€10k/yr** teams |

Pricing anchor: a single audit is a fraction of one month's waste on a five-figure bill. The audit pays for itself the day you act on it.

## Who it's for (ICP)

Data / platform teams of ~3–20 engineers, spending roughly **$15k–$150k/mo** on Databricks, **without** a dedicated FinOps function. Buyer is the Head of Data / Platform Lead who feels the bill and owns the budget conversation. Best moment to reach them: right after a scary invoice, a "cut cloud spend" mandate, or a fresh round of funding.

## FAQ

**Does it need access to our data?** No. Read-only on system tables only. It never reads business/customer data, and no data leaves your workspace.

**Isn't this just the Databricks usage dashboard?** The dashboard tells you *what* you spent. This tells you *why* and *what to change* — ranked by dollars, with fixes.

**How accurate are the savings numbers?** Estimated at list price and labeled as such; your effective rate may be lower due to private discounts. Each finding carries a confidence level so you know which numbers are solid vs. heuristic.

**Will it break anything?** No. It's strictly read-only and advisory. You decide what to change and merge it yourself.

**Why you?** I run dbt on Databricks for a living and lead a data team. I can diagnose compute, SQL, *and* team process — and tell the difference between a number that matters and noise.

## Call to action

> "Send me read-only access to your `system` schema — or just run my scanner yourself — and I'll show you what your Databricks bill is leaking. First look is free."

---

### 30-second elevator pitch

> Most data teams can't tell you why their Databricks bill is what it is — the native tools show totals, not causes. I built a read-only checkup that runs inside your own workspace, reads only the system tables, and produces a ranked report of exactly what's wasting money — jobs on the wrong compute, idle warehouses, retry loops, full-refreshes that should be incremental — each with a dollar figure and the fix. Nothing leaves your environment. The first audit usually finds more than it costs. I do this because I run dbt on Databricks every day, so I can diagnose compute *and* process, which a generic cost tool can't.
