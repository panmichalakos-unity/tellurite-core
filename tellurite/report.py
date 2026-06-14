"""Report generator — Jinja2 → HTML + Markdown from ranked Findings."""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from tellurite.models import Finding

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def render_html(
    findings: list[Finding],
    lookback_days: int,
    currency: str = "USD",
    discount_pct: float = 0.0,
    out_path: Path | None = None,
) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("report.html.j2")
    total_waste = sum(f.monthly_waste_usd for f in findings)
    html = template.render(
        findings=findings,
        total_waste_usd=total_waste,
        lookback_days=lookback_days,
        currency=currency,
        discount_pct=discount_pct,
        n_high=sum(1 for f in findings if f.severity == "high"),
        n_medium=sum(1 for f in findings if f.severity == "medium"),
        n_low=sum(1 for f in findings if f.severity == "low"),
    )
    if out_path:
        out_path.write_text(html, encoding="utf-8")
    return html
