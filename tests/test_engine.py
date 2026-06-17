"""Golden tests for the rule engine — no live Databricks connection required."""

import pytest

from tellurite.engine import (
    _eval_savings,
    _pick_instance_key,
    _with_total_cost,
    _with_vm_cost,
    load_rules,
    run,
)
from tellurite.models import Remediation, Rule
from tellurite.pricing import PriceCatalog

_CATALOG = PriceCatalog(
    "azure",
    "northeurope",
    "USD",
    "fallback",
    "t",
    {"Standard_E8_v3": 1.0, "Standard_E4_v3": 0.5},
)


class _FakeConnector:
    """Returns canned rows for any query — lets us exercise the engine offline."""

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows

    def query(self, sql: str, params: dict | None = None) -> list[dict]:
        return self._rows


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


# ---------------------------------------------------------------------------
# Engine end-to-end (offline) — savings_pct + attributed cost (Option A, §7)
# ---------------------------------------------------------------------------


def _toy_rule(savings_model: str = "list_cost_usd * 0.5") -> Rule:
    return Rule(
        id="toy",
        title="Toy rule",
        category="compute_config",
        severity="high",
        confidence="high",
        summary="s",
        detection_sql="SELECT 1",
        savings_model=savings_model,
        remediation=Remediation(summary="fix it"),
    )


def test_run_computes_savings_pct_and_attributed_cost():
    conn = _FakeConnector([{"job_id": "j1", "list_cost_usd": 1000.0}])
    findings = run(conn, [_toy_rule()], lookback_days=30, min_cost_usd=50)
    assert len(findings) == 1
    f = findings[0]
    assert f.monthly_waste_usd == pytest.approx(500.0)
    assert f.attributed_cost_usd == pytest.approx(1000.0)
    assert f.savings_pct == pytest.approx(0.5)  # discount-invariant
    assert f.instance_key == "j1"


def test_run_savings_pct_none_without_attributed_cost():
    """A governance-style finding (savings_model '0', no list_cost_usd) has no % basis."""
    conn = _FakeConnector([{"untagged_cost_usd": 5000.0, "total_cost_usd": 20000.0}])
    findings = run(conn, [_toy_rule("0")], lookback_days=30, min_cost_usd=50)
    assert len(findings) == 1
    assert findings[0].monthly_waste_usd == 0.0
    assert findings[0].savings_pct is None
    assert findings[0].attributed_cost_usd is None


def test_run_ranks_by_waste_desc():
    rows = [
        {"job_id": "small", "list_cost_usd": 200.0},
        {"job_id": "big", "list_cost_usd": 5000.0},
    ]
    findings = run(_FakeConnector(rows), [_toy_rule()], lookback_days=30, min_cost_usd=50)
    assert [f.instance_key for f in findings] == ["big", "small"]


# ---------------------------------------------------------------------------
# Cost assembly seam (Option A, §7): list_cost_usd = dbu_cost_usd + vm_cost_usd
# ---------------------------------------------------------------------------


def test_with_total_cost_assembles_from_dbu():
    assert _with_total_cost({"dbu_cost_usd": 100.0})["list_cost_usd"] == 100.0


def test_with_total_cost_adds_vm():
    # The M1.5 seam: a vm_cost_usd column raises the basis with zero YAML change.
    assert _with_total_cost({"dbu_cost_usd": 100.0, "vm_cost_usd": 40.0})["list_cost_usd"] == 140.0


def test_with_total_cost_fallback_and_passthrough():
    assert _with_total_cost({"list_cost_usd": 7.0})["list_cost_usd"] == 7.0  # rule-provided total
    assert "list_cost_usd" not in _with_total_cost({"foo": 1})  # no cost columns → unchanged


# ---------------------------------------------------------------------------
# Golden tests — anchor rules 1–4 through the engine (offline, fixture rows)
# ---------------------------------------------------------------------------


def _run_rule(rule_id: str, rows: list[dict], min_cost_usd: float = 50.0):
    return run(
        _FakeConnector(rows), [_make_rule(rule_id)], lookback_days=30, min_cost_usd=min_cost_usd
    )


def test_golden_jobs_on_all_purpose():
    rows = [
        {"job_id": "a", "job_name": "nightly", "dbu_cost_usd": 2000.0},
        {"job_id": "b", "job_name": "adhoc", "dbu_cost_usd": 500.0},
    ]
    findings = _run_rule("jobs_on_all_purpose", rows)
    assert [f.instance_key for f in findings] == ["a", "b"]  # ranked by waste
    assert findings[0].monthly_waste_usd == pytest.approx(1000.0)  # 2000 * 0.5
    assert findings[0].attributed_cost_usd == pytest.approx(2000.0)
    assert findings[0].savings_pct == pytest.approx(0.5)


def test_golden_idle_sql_warehouses():
    rows = [{"warehouse_id": "w1", "dbu_cost_usd": 1000.0, "query_count": 3, "active_hours": 0.2}]
    findings = _run_rule("idle_sql_warehouses", rows)
    assert findings[0].instance_key == "w1"
    assert findings[0].monthly_waste_usd == pytest.approx(600.0)  # 1000 * 0.6
    assert findings[0].savings_pct == pytest.approx(0.6)


def test_golden_missing_auto_termination():
    # idle_fraction is now MEASURED (a column), not a flat param.
    rows = [
        {
            "cluster_id": "c1",
            "cluster_name": "interactive",
            "auto_termination_minutes": None,
            "dbu_cost_usd": 1000.0,
            "idle_fraction": 0.30,
        }
    ]
    findings = _run_rule("missing_auto_termination", rows)
    assert findings[0].instance_key == "c1"
    assert findings[0].monthly_waste_usd == pytest.approx(300.0)  # 1000 * measured 0.30


def test_golden_untagged_spend_is_governance_finding():
    rows = [{"untagged_cost_usd": 5000.0, "total_cost_usd": 20000.0}]
    findings = _run_rule("untagged_spend", rows)
    assert len(findings) == 1  # kept despite savings_model "0"
    f = findings[0]
    assert f.monthly_waste_usd == 0.0
    assert f.savings_pct is None
    assert f.attributed_cost_usd is None
    assert f.evidence["untagged_cost_usd"] == 5000.0


def test_golden_vm_cost_lifts_basis_and_waste():
    """End-to-end of the M1.5 seam through a real rule: vm_cost_usd raises both."""
    rows = [{"job_id": "a", "dbu_cost_usd": 1000.0, "vm_cost_usd": 1000.0}]
    findings = _run_rule("jobs_on_all_purpose", rows)
    assert findings[0].attributed_cost_usd == pytest.approx(2000.0)
    assert findings[0].monthly_waste_usd == pytest.approx(1000.0)  # 2000 * 0.5


# ---------------------------------------------------------------------------
# VM-cost enrichment (M1.5b): catalog × structural columns → vm_cost_usd
# ---------------------------------------------------------------------------


def test_vm_cost_driver_plus_workers():
    row = {
        "vm_node_type": "Standard_E8_v3",  # 1.0/hr
        "vm_worker_node_type": "Standard_E4_v3",  # 0.5/hr
        "vm_worker_count": 4,
        "vm_runtime_hours": 10.0,
    }
    out = _with_vm_cost(row, _CATALOG)
    # driver 1.0*10 + workers 0.5*4*10 = 10 + 20 = 30
    assert out["vm_cost_usd"] == pytest.approx(30.0)
    assert out["vm_priced"] is True


def test_vm_cost_missing_price_is_flagged_not_fabricated():
    row = {"vm_node_type": "Standard_UNKNOWN", "vm_runtime_hours": 5.0}
    out = _with_vm_cost(row, _CATALOG)
    assert out["vm_cost_usd"] == 0.0  # unknown price contributes 0, not a guess
    assert out["vm_priced"] is False  # flagged as understated


def test_vm_cost_noop_without_catalog_or_columns():
    assert "vm_cost_usd" not in _with_vm_cost({"vm_node_type": "Standard_E8_v3"}, None)
    assert "vm_cost_usd" not in _with_vm_cost({"dbu_cost_usd": 10.0}, _CATALOG)


def test_golden_over_provisioned_autoscale():
    """Rule 6: rightsizing_factor (from SQL) × full-TCO basis = waste; VM cost folded in."""
    # avg_worker_cpu 15 → severely oversized → factor 0.40 (set by the rule's SQL CASE).
    rows = [
        {
            "cluster_id": "c1",
            "cluster_name": "etl",
            "vm_node_type": "Standard_E8_v3",  # 1.0/hr
            "vm_worker_node_type": None,
            "vm_worker_count": 0,
            "vm_runtime_hours": 1000.0,  # VM = 1000
            "dbu_cost_usd": 1000.0,
            "avg_worker_cpu": 15.0,
            "metric_samples": 200,
            "rightsizing_factor": 0.40,
        }
    ]
    findings = run(
        _FakeConnector(rows),
        [_make_rule("over_provisioned_autoscale")],
        lookback_days=30,
        min_cost_usd=50,
        price_catalog=_CATALOG,
    )
    assert findings[0].attributed_cost_usd == pytest.approx(2000.0)  # 1000 dbu + 1000 vm
    assert findings[0].monthly_waste_usd == pytest.approx(800.0)  # 2000 * 0.40
    assert findings[0].savings_pct == pytest.approx(0.40)


def test_golden_vm_family_mismatch():
    """Memory-family switch: savings = full-TCO basis × family_switch_factor (param)."""
    rows = [
        {
            "cluster_id": "c2",
            "vm_node_type": "Standard_E8_v3",
            "vm_worker_count": 0,
            "vm_runtime_hours": 100.0,  # VM = 100
            "dbu_cost_usd": 900.0,
            "avg_worker_mem": 20.0,
        }
    ]
    findings = run(
        _FakeConnector(rows),
        [_make_rule("vm_family_mismatch")],
        lookback_days=30,
        min_cost_usd=50,
        price_catalog=_CATALOG,
    )
    assert findings[0].attributed_cost_usd == pytest.approx(1000.0)  # 900 + 100
    assert findings[0].monthly_waste_usd == pytest.approx(200.0)  # 1000 * 0.20


def test_golden_vm_generation_outdated_low_confidence():
    """Split-out generation rule: conservative factor (0.10), low confidence."""
    rows = [
        {
            "cluster_id": "c3",
            "vm_node_type": "Standard_E8_v3",
            "vm_worker_count": 0,
            "vm_runtime_hours": 100.0,  # VM = 100
            "dbu_cost_usd": 900.0,
            "vm_generation": "v3",
        }
    ]
    findings = run(
        _FakeConnector(rows),
        [_make_rule("vm_generation_outdated")],
        lookback_days=30,
        min_cost_usd=50,
        price_catalog=_CATALOG,
    )
    assert findings[0].monthly_waste_usd == pytest.approx(100.0)  # 1000 * 0.10
    assert findings[0].confidence == "low"


def test_run_folds_vm_cost_into_basis():
    """Full path: rule returns DBU + structural cols; engine adds VM → list_cost_usd."""
    rows = [
        {
            "job_id": "a",
            "dbu_cost_usd": 1000.0,
            "vm_node_type": "Standard_E8_v3",  # 1.0/hr × 1000h = 1000
            "vm_runtime_hours": 1000.0,
        }
    ]
    findings = run(
        _FakeConnector(rows),
        [_make_rule("jobs_on_all_purpose")],
        lookback_days=30,
        min_cost_usd=50,
        price_catalog=_CATALOG,
    )
    assert findings[0].attributed_cost_usd == pytest.approx(2000.0)  # 1000 dbu + 1000 vm
    assert findings[0].monthly_waste_usd == pytest.approx(1000.0)  # 2000 * 0.5
    assert findings[0].evidence["vm_cost_usd"] == pytest.approx(1000.0)
    assert findings[0].evidence["vm_priced"] is True
