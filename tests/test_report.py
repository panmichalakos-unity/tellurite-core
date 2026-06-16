"""Report rendering tests (offline, synthetic findings)."""

from tellurite.models import Finding, Remediation
from tellurite.report import render_html, summarize


def _f(rule, inst, waste, attr=None, sev="medium"):
    return Finding(
        rule_id=rule,
        title=rule,
        category="compute_config",
        severity=sev,
        confidence="medium",
        instance_key=inst,
        monthly_waste_usd=waste,
        attributed_cost_usd=attr,
        savings_pct=(waste / attr if attr else None),
        evidence={},
        remediation=Remediation(summary="fix"),
    )


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


# ---------------------------------------------------------------------------
# summarize() — informational split + per-resource de-duplication
# ---------------------------------------------------------------------------


def test_summarize_splits_informational():
    s = summarize([_f("a", "c1", 100.0, 200.0), _f("expensive_long_queries", "q1", 0.0)])
    assert [f.rule_id for f in s.dollar] == ["a"]
    assert [f.rule_id for f in s.info] == ["expensive_long_queries"]


def test_summarize_dedups_same_resource():
    # cluster c1 flagged by 3 rules → headline = max per resource, not the sum.
    findings = [
        _f("vm_family_mismatch", "c1", 148.0, 1000.0),
        _f("missing_auto_termination", "c1", 80.0, 1000.0),
        _f("over_provisioned_autoscale", "c2", 73.0, 500.0),
    ]
    s = summarize(findings)
    assert s.gross_waste_usd == 301.0
    assert s.dedup_waste_usd == 221.0  # 148 (c1 max) + 73 (c2)
    assert s.n_resources == 2


def test_summarize_dollar_ranked_desc():
    s = summarize([_f("a", "c1", 50.0, 100.0), _f("b", "c2", 500.0, 1000.0)])
    assert [f.instance_key for f in s.dollar] == ["c2", "c1"]
