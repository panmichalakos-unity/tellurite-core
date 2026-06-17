"""Report generator — Jinja2 → HTML from ranked Findings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from tellurite.models import Finding

_TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
_CORONA_CSS = _TEMPLATES_DIR / "corona" / "css" / "style.css"
_CORONA_FONTS = _TEMPLATES_DIR / "corona" / "css" / "fonts.css"  # base64 Rubik @font-face

_THEME_TEMPLATES = {
    "basic": "report.html.j2",
    "corona": "report.corona.html.j2",
}


@dataclass
class Summary:
    """Presentation-time view over findings: separates dollar vs informational, and
    de-duplicates overlap (one resource flagged by several rules shares the same spend)."""

    dollar: list[Finding]  # waste > 0, ranked
    info: list[Finding]  # waste <= 0 (governance / time-proxy) — shown separately
    gross_waste_usd: float  # naive sum of dollar findings (overstates: overlaps)
    dedup_waste_usd: float  # conservative: max recoverable per distinct resource
    dedup_savings_pct: float | None
    n_resources: int  # distinct resources with a dollar finding
    by_rule: list[tuple[str, float]]  # (rule title, summed dollar waste), ranked desc
    # (instance_key, its findings) grouped per resource, ranked by the resource's max waste
    resources: list[tuple[str, list[Finding]]]

    @property
    def n_high(self) -> int:
        return sum(1 for f in self.dollar if f.severity == "high")

    @property
    def n_medium(self) -> int:
        return sum(1 for f in self.dollar if f.severity == "medium")

    @property
    def n_low(self) -> int:
        return sum(1 for f in self.dollar if f.severity == "low")


def summarize(findings: list[Finding]) -> Summary:
    """Split findings and compute a de-duplicated headline.

    The same resource (instance_key) can be flagged by multiple rules, each estimating
    recovery against the *same* underlying cost. Summing them overstates waste, so the
    headline takes the **largest** estimate per resource (conservative). Findings with no
    dollar impact (savings_model "0" — e.g. untagged spend, expensive-query time proxies)
    are routed to an informational list, out of the dollar headline and count.
    """
    dollar = sorted(
        (f for f in findings if f.monthly_waste_usd > 0),
        key=lambda f: (-f.monthly_waste_usd, f.severity_rank),
    )
    info = [f for f in findings if f.monthly_waste_usd <= 0]
    gross = sum(f.monthly_waste_usd for f in dollar)

    rep: dict[str, Finding] = {}  # representative (max-waste) finding per resource
    for f in dollar:
        cur = rep.get(f.instance_key)
        if cur is None or f.monthly_waste_usd > cur.monthly_waste_usd:
            rep[f.instance_key] = f
    dedup = sum(f.monthly_waste_usd for f in rep.values())
    dedup_attr = sum(f.attributed_cost_usd or 0.0 for f in rep.values())
    dedup_pct = (dedup / dedup_attr) if dedup_attr else None

    by_rule_map: dict[str, float] = {}
    for f in dollar:
        by_rule_map[f.title] = by_rule_map.get(f.title, 0.0) + f.monthly_waste_usd
    by_rule = sorted(by_rule_map.items(), key=lambda kv: -kv[1])

    groups: dict[str, list[Finding]] = {}
    for f in dollar:  # dollar is already ranked, so each group's list stays ranked
        groups.setdefault(f.instance_key, []).append(f)
    resources = sorted(
        groups.items(),
        key=lambda kv: -max(x.monthly_waste_usd for x in kv[1]),
    )

    return Summary(
        dollar=dollar,
        info=info,
        gross_waste_usd=gross,
        dedup_waste_usd=dedup,
        dedup_savings_pct=dedup_pct,
        n_resources=len(rep),
        by_rule=by_rule,
        resources=resources,
    )


def render_html(
    findings: list[Finding],
    lookback_days: int,
    currency: str = "USD",
    discount_pct: float = 0.0,
    show_savings_pct: bool = True,
    theme: str = "basic",
    out_path: Path | None = None,
) -> str:
    env = Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template(_THEME_TEMPLATES.get(theme, _THEME_TEMPLATES["basic"]))
    s = summarize(findings)

    ctx = {
        "findings": s.dollar,  # the dollar report
        "info_findings": s.info,
        "total_waste_usd": s.dedup_waste_usd,  # de-duplicated, conservative
        "gross_waste_usd": s.gross_waste_usd,
        "n_resources": s.n_resources,
        "total_savings_pct": s.dedup_savings_pct,
        "show_savings_pct": show_savings_pct,
        "lookback_days": lookback_days,
        "currency": currency,
        "discount_pct": discount_pct,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%d"),
        "n_high": s.n_high,
        "n_medium": s.n_medium,
        "n_low": s.n_low,
        "by_rule": s.by_rule,
        "by_rule_max": max((w for _, w in s.by_rule), default=0.0),
        "resources": s.resources,
    }
    if theme == "corona":
        # Inline the vendored Corona stylesheet + embedded fonts → single offline file.
        css = _CORONA_CSS.read_text(encoding="utf-8")
        if _CORONA_FONTS.exists():
            css += "\n" + _CORONA_FONTS.read_text(encoding="utf-8")
        ctx["corona_css"] = css

    html = template.render(**ctx)
    if out_path:
        out_path.write_text(html, encoding="utf-8")
    return html
