"""Golden tests for the rule engine — no live Databricks connection required."""

import pytest

from tellurite.engine import _eval_savings, _pick_instance_key, load_rules
from tellurite.models import Finding, Remediation, Rule


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------

def test_load_rules_returns_nonempty_list():
    rules = load_rules()
    assert len(rules) >= 4, "expected at least the 4 anchor rules"


def test_all_rules_have_required_fields():
    for rule in load_rules():
        assert rule.id, f"rule missing id: {rule}"
        assert rule.detection_sql.strip(), f"rule {rule.id} has empty detection_sql"
        assert rule.savings_model, f"rule {rule.id} missing savings_model"


# ---------------------------------------------------------------------------
# Savings model evaluation
# ---------------------------------------------------------------------------

def test_eval_savings_simple_multiplication():
    row = {"list_cost_usd": 1000.0}
    params = {"jobs_compute_discount": 0.5}
    result = _eval_savings("list_cost_usd * jobs_compute_discount", row, params)
    assert result == pytest.approx(500.0)


def test_eval_savings_zero_model():
    assert _eval_savings("0", {}, {}) == 0.0


def test_eval_savings_bad_expression_returns_zero():
    assert _eval_savings("nonexistent_col * 0.5", {}, {}) == 0.0


# ---------------------------------------------------------------------------
# Instance key selection
# ---------------------------------------------------------------------------

def test_pick_instance_key_job_id():
    assert _pick_instance_key({"job_id": "123", "list_cost_usd": 500}) == "123"


def test_pick_instance_key_fallback():
    assert _pick_instance_key({"list_cost_usd": 100}) == "global"


# ---------------------------------------------------------------------------
# Golden test — jobs_on_all_purpose rule
# ---------------------------------------------------------------------------

def _make_rule(rule_id: str) -> Rule:
    rules = {r.id: r for r in load_rules()}
    return rules[rule_id]


def test_jobs_on_all_purpose_golden():
    """Fixture: 2 jobs with known list_cost_usd; assert monthly_waste is half."""
    rule = _make_rule("jobs_on_all_purpose")
    fixture_rows = [
        {"job_id": "job_a", "job_name": "nightly_transform", "list_cost_usd": 2000.0},
        {"job_id": "job_b", "job_name": "adhoc_query", "list_cost_usd": 500.0},
    ]
    params = {**rule.parameters, "min_cost_usd": 50}
    wastes = [_eval_savings(rule.savings_model, row, params) for row in fixture_rows]
    assert wastes[0] == pytest.approx(1000.0)
    assert wastes[1] == pytest.approx(250.0)


def test_untagged_spend_savings_is_zero():
    """untagged_spend has savings_model = '0' — it's a governance gap, not direct savings."""
    rule = _make_rule("untagged_spend")
    row = {"untagged_cost_usd": 5000.0, "total_cost_usd": 20000.0}
    assert _eval_savings(rule.savings_model, row, {}) == 0.0
