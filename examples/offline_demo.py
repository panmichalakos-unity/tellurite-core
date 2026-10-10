"""Exercise the existing Tellurite engine with synthetic SQLite data, offline."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

from tellurite.engine import load_rules, run
from tellurite.pricing import PriceCatalog
from tellurite.report import render_html, summarize

HERE = Path(__file__).resolve().parent


class OfflineConnector:
    """Disposable query adapter; no live connector, credentials or HTTP requests."""

    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("""
            CREATE TABLE demo_workloads (
                job_id TEXT PRIMARY KEY, job_name TEXT NOT NULL,
                dbu_cost_usd REAL, idle_fraction REAL, vm_node_type TEXT,
                vm_worker_node_type TEXT, vm_worker_count INTEGER,
                vm_runtime_hours REAL, configuration_review INTEGER, cost_owner TEXT
            )
        """)
        rows = json.loads((HERE / "workloads.json").read_text(encoding="utf-8"))
        columns = tuple(rows[0])
        placeholders = ",".join("?" for _ in columns)
        self.connection.executemany(
            f"INSERT INTO demo_workloads ({','.join(columns)}) VALUES ({placeholders})",
            [tuple(row[column] for column in columns) for row in rows],
        )

    def query(self, sql, params=None):
        return [dict(row) for row in self.connection.execute(sql, params or {})]


def demo_findings():
    connector = OfflineConnector()
    # Invented rates for invented VM families, not actual cloud prices.
    catalog = PriceCatalog(
        "synthetic", "offline", "USD", "fixture", "not-applicable",
        {"Standard_DEMO_DRIVER": 1.0, "Standard_DEMO_WORKER": 0.5},
    )
    try:
        return run(connector, load_rules(HERE / "rules"), lookback_days=30, price_catalog=catalog)
    finally:
        connector.connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("demo-output"))
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    findings = demo_findings()
    html = render_html(findings, lookback_days=30, theme="basic")
    # Label the public sample explicitly without changing the live rendering contract.
    banner = '<p class="caveat"><strong>SYNTHETIC DEMONSTRATION.</strong> Invented workloads and prices; no Databricks connection. Figures are illustrative, not measured savings.</p>'
    html = html.replace("<body>", "<body>\n" + banner)
    html = html.replace(
        'Intended for trusted <code>system.*</code> telemetry queries. Read-only access must be enforced through workspace permissions.',
        'The example executed SELECT statements on a disposable in-memory SQLite database containing only synthetic data.',
    ).replace('The report is generated locally. Optional public VM-price lookups require network access; inspect warnings and evidence before acting.', 'The example makes no network requests and uses no workspace credentials.')
    html = html.replace('(<code>system.billing.list_prices</code>)', '(invented demonstration prices)')
    (args.out / "report.html").write_text(html, encoding="utf-8")
    (args.out / "findings.json").write_text(
        json.dumps([f.model_dump() for f in findings], indent=2) + "\n", encoding="utf-8"
    )
    summary = summarize(findings)
    print("SYNTHETIC DEMO — no workspace connection; not measured savings")
    print(f"Cost findings: {len(summary.dollar)}; governance findings: {len(summary.info)}")
    print(f"Naive sum: ${summary.gross_waste_usd:.0f}; overlap-adjusted estimate: ${summary.dedup_waste_usd:.0f}")
    print(f"Report: {args.out / 'report.html'}")


if __name__ == "__main__":
    main()
