"""CLI entry point — tellurite-audit <command> [options]."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from tellurite import __version__
from tellurite.connector import ConnectorConfig, DatabricksConnector
from tellurite.engine import load_rules, run
from tellurite.report import render_html

console = Console()


@click.group()
@click.version_option(__version__, prog_name="tellurite-audit")
def cli() -> None:
    """Tellurite Audit — Databricks cost audit tool by Akmonlabs.

    Reads only system.* tables. No writes. No data egress.
    """


# ---------------------------------------------------------------------------
# tellurite-audit run
# ---------------------------------------------------------------------------
@cli.command()
@click.option("--host", envvar="DATABRICKS_HOST", help="Workspace hostname")
@click.option("--http-path", envvar="DATABRICKS_HTTP_PATH", help="SQL warehouse HTTP path")
@click.option("--token", envvar="DATABRICKS_TOKEN", help="Personal access token")
@click.option("--lookback", default=30, show_default=True, help="Lookback window in days")
@click.option("--min-cost", default=50.0, show_default=True, help="Minimum finding threshold (USD)")
@click.option("--discount-pct", default=0.0, show_default=True, help="Discount vs list price (0–1)")
@click.option("--currency", default="USD", show_default=True, help="Display currency")
@click.option("--out", default="report.html", show_default=True, help="Output file path")
@click.option("--rules-dir", default=None, type=click.Path(), help="Override rules directory")
@click.option("--json-out", is_flag=True, help="Also emit findings as JSON")
def run_cmd(
    host: str | None,
    http_path: str | None,
    token: str | None,
    lookback: int,
    min_cost: float,
    discount_pct: float,
    currency: str,
    out: str,
    rules_dir: str | None,
    json_out: bool,
) -> None:
    """Run a full cost audit and produce a ranked HTML report."""
    config = ConnectorConfig(
        host=host,
        http_path=http_path,
        token=token,
        lookback_days=lookback,
        currency=currency,
        discount_pct=discount_pct,
    )
    rules_path = Path(rules_dir) if rules_dir else None
    rules = load_rules(rules_path)
    console.print(f"[bold]Loaded {len(rules)} rules.[/bold] Lookback: {lookback}d")

    with DatabricksConnector(config) as connector:
        if not connector.ping():
            console.print("[red]Connection check failed — aborting.[/red]")
            sys.exit(1)
        console.print("[green]Connected.[/green] Running rules…")
        findings = run(connector, rules, lookback_days=lookback, min_cost_usd=min_cost, discount_pct=discount_pct)

    total = sum(f.monthly_waste_usd for f in findings)
    console.print(f"\n[bold]{len(findings)} findings[/bold] — estimated monthly waste: [bold red]${total:,.0f}[/bold red]")

    out_path = Path(out)
    render_html(findings, lookback_days=lookback, currency=currency, discount_pct=discount_pct, out_path=out_path)
    console.print(f"\nReport written to [bold]{out_path}[/bold]")

    if json_out:
        json_path = out_path.with_suffix(".json")
        json_path.write_text(
            json.dumps([f.model_dump() for f in findings], indent=2, default=str),
            encoding="utf-8",
        )
        console.print(f"JSON findings written to [bold]{json_path}[/bold]")


# ---------------------------------------------------------------------------
# tellurite-audit rules list / check
# ---------------------------------------------------------------------------
@cli.group()
def rules() -> None:
    """Inspect the detection rule catalog."""


@rules.command("list")
@click.option("--rules-dir", default=None, type=click.Path())
def rules_list(rules_dir: str | None) -> None:
    """List all loaded detection rules."""
    loaded = load_rules(Path(rules_dir) if rules_dir else None)
    table = Table(title="Detection Rules", show_lines=True)
    table.add_column("ID", style="cyan")
    table.add_column("Category")
    table.add_column("Severity")
    table.add_column("Confidence")
    table.add_column("Summary")
    for rule in loaded:
        sev_style = {"high": "red", "medium": "yellow", "low": "dim"}.get(rule.severity, "")
        table.add_row(
            rule.id,
            rule.category,
            f"[{sev_style}]{rule.severity}[/{sev_style}]",
            rule.confidence,
            rule.summary,
        )
    console.print(table)


@rules.command("check")
@click.option("--rules-dir", default=None, type=click.Path())
def rules_check(rules_dir: str | None) -> None:
    """Validate rule YAML schema."""
    try:
        loaded = load_rules(Path(rules_dir) if rules_dir else None)
        console.print(f"[green]OK[/green] — {len(loaded)} rules validated.")
    except Exception as exc:
        console.print(f"[red]INVALID[/red] — {exc}")
        sys.exit(1)
