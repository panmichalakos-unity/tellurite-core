"""CLI entry point — tellurite-audit <command> [options]."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table

from tellurite import __version__
from tellurite.config import load_profile
from tellurite.connector import ConnectorConfig, DatabricksConnector
from tellurite.engine import load_rules, run
from tellurite.pricing import load_price_catalog
from tellurite.report import render_html, summarize

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
@cli.command("run")
@click.option("--profile", default=None, help="Named profile from profiles.yaml")
@click.option("--host", default=None, help="Workspace hostname (overrides profile/env)")
@click.option("--http-path", default=None, help="SQL warehouse HTTP path (overrides profile/env)")
@click.option(
    "--token", envvar="DATABRICKS_TOKEN", default=None, help="Personal access token (prefer env)"
)
@click.option(
    "--auth",
    type=click.Choice(["pat", "oauth", "cli"]),
    default=None,
    help="Auth mode (default: PAT if token present, else OAuth browser login)",
)
@click.option(
    "--databricks-profile",
    default=None,
    help="~/.databrickscfg section name for --auth cli (else matched by host)",
)
@click.option(
    "--cloud", type=click.Choice(["azure", "aws"]), default=None, help="Cloud for VM pricing"
)
@click.option("--region", default=None, help="Cloud region for VM price catalog")
@click.option("--lookback", "lookback_days", type=int, default=None, help="Lookback window in days")
@click.option(
    "--min-cost", "min_cost_usd", type=float, default=None, help="Minimum finding threshold (USD)"
)
@click.option("--discount-pct", type=float, default=None, help="Discount vs list price (0–1)")
@click.option("--currency", default=None, help="Display currency")
@click.option(
    "--savings-pct/--no-savings-pct",
    "show_savings_pct",
    default=None,
    help="Show the discount-invariant savings %% inline (default on)",
)
@click.option(
    "--theme",
    type=click.Choice(["basic", "corona"]),
    default=None,
    help="Report theme (default: corona dark dashboard)",
)
@click.option(
    "--no-price-fetch",
    "no_price_fetch",
    is_flag=True,
    help="Use the bundled fallback VM price table instead of fetching the live catalog",
)
@click.option("--out", default="report.html", show_default=True, help="Output file path")
@click.option("--rules-dir", default=None, type=click.Path(), help="Override rules directory")
@click.option("--json-out", is_flag=True, help="Also emit findings as JSON")
def run_cmd(
    profile: str | None,
    host: str | None,
    http_path: str | None,
    token: str | None,
    auth: str | None,
    databricks_profile: str | None,
    cloud: str | None,
    region: str | None,
    lookback_days: int | None,
    min_cost_usd: float | None,
    discount_pct: float | None,
    currency: str | None,
    show_savings_pct: bool | None,
    theme: str | None,
    no_price_fetch: bool,
    out: str,
    rules_dir: str | None,
    json_out: bool,
) -> None:
    """Run a full cost audit and produce a ranked HTML report."""
    prof = load_profile(
        profile,
        overrides={
            "host": host,
            "http_path": http_path,
            "auth": auth,
            "databricks_profile": databricks_profile,
            "cloud": cloud,
            "region": region,
            "lookback_days": lookback_days,
            "min_cost_usd": min_cost_usd,
            "discount_pct": discount_pct,
            "currency": currency,
            "show_savings_pct": show_savings_pct,
            "theme": theme,
        },
    )

    config = ConnectorConfig(
        host=prof.host,
        http_path=prof.http_path,
        token=token,
        auth=prof.auth,
        databricks_profile=prof.databricks_profile,
    )
    if not prof.workspace_id:
        console.print(
            "[red]Could not determine workspace_id.[/red] system.* tables are account-wide, "
            "so the audit must be scoped to one workspace. Set [bold]workspace_id[/bold] in the "
            "profile (or use an Azure host like adb-<workspace_id>.NN.azuredatabricks.net)."
        )
        sys.exit(1)

    rules = load_rules(Path(rules_dir) if rules_dir else None)
    console.print(
        f"[bold]Loaded {len(rules)} rules.[/bold] Workspace: {prof.workspace_id} · "
        f"Lookback: {prof.lookback_days}d · cloud: {prof.cloud}"
    )

    catalog = load_price_catalog(
        prof.cloud, prof.region, allow_fetch=not no_price_fetch, currency=prof.currency
    )
    console.print(
        f"VM prices: [bold]{catalog.source}[/bold] "
        f"({len(catalog.prices)} node types · {prof.cloud}/{prof.region or '—'})"
    )

    with DatabricksConnector(config) as connector:
        if not connector.ping():
            console.print("[red]Connection check failed — aborting.[/red]")
            sys.exit(1)
        console.print("[green]Connected.[/green] Running rules…")
        findings = run(
            connector,
            rules,
            lookback_days=prof.lookback_days,
            min_cost_usd=prof.min_cost_usd,
            discount_pct=prof.discount_pct,
            workspace_id=prof.workspace_id,
            price_catalog=catalog,
        )

    s = summarize(findings)
    gross_note = (
        f" (gross ${s.gross_waste_usd:,.0f})" if s.gross_waste_usd > s.dedup_waste_usd else ""
    )
    console.print(
        f"\n[bold]{len(s.dollar)} dollar findings[/bold] across {s.n_resources} resource(s) — "
        f"est. recoverable [bold red]${s.dedup_waste_usd:,.0f}/mo[/bold red]{gross_note}"
    )
    if s.info:
        console.print(f"[dim]+ {len(s.info)} informational (no $ estimate)[/dim]")

    out_path = Path(out)
    render_html(
        findings,
        lookback_days=prof.lookback_days,
        currency=prof.currency,
        discount_pct=prof.discount_pct,
        show_savings_pct=prof.show_savings_pct,
        theme=prof.theme,
        out_path=out_path,
    )
    console.print(f"\nReport written to [bold]{out_path}[/bold]")

    if json_out:
        json_path = out_path.with_suffix(".json")
        json_path.write_text(
            json.dumps([f.model_dump() for f in findings], indent=2, default=str),
            encoding="utf-8",
        )
        console.print(f"JSON findings written to [bold]{json_path}[/bold]")


# ---------------------------------------------------------------------------
# tellurite-audit ping
# ---------------------------------------------------------------------------
@cli.command("ping")
@click.option("--profile", default=None, help="Named profile from profiles.yaml")
@click.option("--host", default=None, help="Workspace hostname (overrides profile/env)")
@click.option("--http-path", default=None, help="SQL warehouse HTTP path (overrides profile/env)")
@click.option(
    "--token", envvar="DATABRICKS_TOKEN", default=None, help="Personal access token (prefer env)"
)
@click.option(
    "--auth",
    type=click.Choice(["pat", "oauth", "cli"]),
    default=None,
    help="Auth mode (default: PAT if token present, else OAuth browser login)",
)
@click.option(
    "--databricks-profile",
    default=None,
    help="~/.databrickscfg section name for --auth cli (else matched by host)",
)
def ping_cmd(
    profile: str | None,
    host: str | None,
    http_path: str | None,
    token: str | None,
    auth: str | None,
    databricks_profile: str | None,
) -> None:
    """Verify workspace connectivity (runs a trivial SELECT 1)."""
    prof = load_profile(
        profile,
        overrides={
            "host": host,
            "http_path": http_path,
            "auth": auth,
            "databricks_profile": databricks_profile,
        },
    )
    config = ConnectorConfig(
        host=prof.host,
        http_path=prof.http_path,
        token=token,
        auth=prof.auth,
        databricks_profile=prof.databricks_profile,
    )
    mode = {
        "cli": "Databricks CLI unified auth",
        "pat": "PAT",
        "oauth": "OAuth (browser)",
    }.get(prof.auth or ("pat" if config.token else "oauth"), "OAuth (browser)")
    console.print(f"Connecting to [bold]{prof.host}[/bold] via {mode}…")
    try:
        with DatabricksConnector(config) as connector:
            ok = connector.ping()
    except Exception as exc:
        console.print(f"[red]Connection failed:[/red] {exc}")
        if prof.auth == "cli":
            console.print(
                "[yellow]Hint:[/yellow] auth=cli reuses Databricks unified-auth credentials "
                "but none are configured. Either:\n"
                f"  • install the Databricks CLI and run: databricks auth login --host https://{prof.host}\n"
                "  • or use a one-off browser login now:   tellurite-audit ping --profile "
                f"{prof.name} --auth oauth"
            )
        sys.exit(1)
    if ok:
        console.print("[green]✓ SELECT 1 succeeded — connection OK.[/green]")
    else:
        console.print("[red]✗ SELECT 1 returned unexpected result.[/red]")
        sys.exit(1)


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
