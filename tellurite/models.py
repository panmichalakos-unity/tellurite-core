"""Pydantic models for Rule (parsed YAML) and Finding (one detected instance)."""

from typing import Any
from pydantic import BaseModel, Field


class Remediation(BaseModel):
    summary: str
    pr_hint: str = ""


class Rule(BaseModel):
    id: str
    title: str
    category: str
    severity: str  # high | medium | low
    confidence: str  # high | medium | low
    summary: str
    signal_tables: list[str] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    detection_sql: str
    savings_model: str
    remediation: Remediation
    references: list[str] = Field(default_factory=list)


class Finding(BaseModel):
    rule_id: str
    title: str
    category: str
    severity: str
    confidence: str
    instance_key: str
    monthly_waste_usd: float
    evidence: dict[str, Any]
    remediation: Remediation

    @property
    def severity_rank(self) -> int:
        return {"high": 0, "medium": 1, "low": 2}.get(self.severity, 3)
