"""The public example exercises SQL selection, enrichment and overlap handling."""

import runpy
from pathlib import Path

from tellurite.report import render_html, summarize

demo = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/offline_demo.py"))


def test_offline_example_cost_and_governance_contract():
    findings = demo["demo_findings"]()
    summary = summarize(findings)
    assert len(summary.dollar) == 3
    assert len(summary.info) == 1
    assert summary.gross_waste_usd == 1300.0
    assert summary.dedup_waste_usd == 860.0
    assert all(f.instance_key != "demo-ingestion" for f in findings)
    assert "Governance Findings" in render_html(findings, 30, theme="basic")
    assert "Assign a cost owner" in render_html(findings, 30, theme="basic")


def test_offline_example_is_repeatable():
    assert demo["demo_findings"]() == demo["demo_findings"]()
