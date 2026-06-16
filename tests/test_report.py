"""Report rendering tests (offline, synthetic findings)."""

from tellurite.models import Finding, Remediation
from tellurite.report import render_html

_FINDINGS = [
    Finding(
        rule_id="jobs_on_all_purpose",
        title="Jobs on all-purpose compute",
        category="compute_config",
        severity="high",
        confidence="high",
        instance_key="job_42",
        monthly_waste_usd=1200.0,
        attributed_cost_usd=2400.0,
        savings_pct=0.5,
        evidence={"job_id": "job_42", "dbu_cost_usd": 2400.0},
        remediation=Remediation(summary="Move to jobs compute."),
    ),
    Finding(
        rule_id="untagged_spend",
        title="Untagged spend",
        category="attribution",
        severity="medium",
        confidence="high",
        instance_key="global",
        monthly_waste_usd=0.0,
        attributed_cost_usd=None,
        savings_pct=None,
        evidence={"untagged_cost_usd": 5000.0},
        remediation=Remediation(summary="Enforce tagging policy."),
    ),
]


def test_basic_theme_renders():
    html = render_html(_FINDINGS, lookback_days=30, theme="basic")
    assert "Tellurite" in html or "Cost Doctor" in html
    assert "$1,200" in html or "1200" in html


def test_corona_theme_renders_and_inlines_css():
    html = render_html(_FINDINGS, lookback_days=30, theme="corona", show_savings_pct=True)
    assert "container-scroller" in html  # Corona layout present
    assert ".card" in html  # inlined Corona stylesheet present
    assert "Jobs on all-purpose compute" in html
    assert "50%" in html  # savings_pct rendered
    assert "badge-danger" in html  # high severity badge


def test_corona_handles_none_savings_pct():
    # The untagged (savings_pct=None) finding must render the em-dash, not crash.
    html = render_html(_FINDINGS, lookback_days=30, theme="corona")
    assert "Untagged spend" in html
