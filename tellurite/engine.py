"""Rule engine — loads rules/*.yaml, runs each rule's SQL, builds ranked Findings."""

from __future__ import annotations

import importlib.resources
import logging
import warnings
from pathlib import Path
from typing import Any

import yaml

from tellurite.connector import DatabricksConnector
from tellurite.models import Finding, Remediation, Rule

logger = logging.getLogger(__name__)

_RULES_DIR = Path(__file__).parent.parent / "rules"


def load_rules(rules_dir: Path | None = None) -> list[Rule]:
    """Load and parse all rule YAML files from the rules directory."""
    directory = rules_dir or _RULES_DIR
    rules: list[Rule] = []
    for path in sorted(directory.glob("*.yaml")):
        with path.open() as f:
            doc = yaml.safe_load(f)
        defaults = doc.get("defaults", {})
        for entry in doc.get("rules", []):
            merged_params = {**defaults, **entry.get("parameters", {})}
            entry["parameters"] = merged_params
            entry.setdefault("parameters", merged_params)
            rules.append(Rule(**entry))
    return rules


def run(
    connector: DatabricksConnector,
    rules: list[Rule],
    lookback_days: int = 30,
    min_cost_usd: float = 50.0,
    discount_pct: float = 0.0,
) -> list[Finding]:
    """Execute each rule and return ranked Findings (highest waste first)."""
    findings: list[Finding] = []
    global_params = {
        "lookback_days": lookback_days,
        "min_cost_usd": min_cost_usd,
        "discount_pct": discount_pct,
    }

    for rule in rules:
        params = {**global_params, **rule.parameters}
        try:
            rows = connector.query(rule.detection_sql, params)
        except Exception as exc:
            warnings.warn(f"[{rule.id}] query failed — skipping: {exc}", stacklevel=2)
            logger.warning("Rule %s failed: %s", rule.id, exc)
            continue

        for row in rows:
            waste = _eval_savings(rule.savings_model, row, params)
            if waste < min_cost_usd and rule.savings_model != "0":
                continue
            instance_key = _pick_instance_key(row)
            findings.append(
                Finding(
                    rule_id=rule.id,
                    title=rule.title,
                    category=rule.category,
                    severity=rule.severity,
                    confidence=rule.confidence,
                    instance_key=instance_key,
                    monthly_waste_usd=waste,
                    evidence={k: v for k, v in row.items()},
                    remediation=rule.remediation,
                )
            )

    findings.sort(key=lambda f: (-f.monthly_waste_usd, f.severity_rank))
    return findings


def _eval_savings(model: str, row: dict[str, Any], params: dict[str, Any]) -> float:
    """Evaluate the savings_model expression against the result row + rule params."""
    if model == "0":
        return 0.0
    ctx = {**params, **row}
    try:
        return float(eval(model, {"__builtins__": {}}, ctx))  # noqa: S307
    except Exception as exc:
        logger.debug("savings_model eval failed (%s): %s", model, exc)
        return 0.0


def _pick_instance_key(row: dict[str, Any]) -> str:
    """Return a human-readable identifier from the result row."""
    for key in ("job_id", "warehouse_id", "cluster_id", "workload", "executed_by"):
        if row.get(key) is not None:
            return str(row[key])
    return "global"
