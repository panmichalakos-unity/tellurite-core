"""Profile / run configuration.

A *profile* bundles non-secret connection + run settings (host, http_path, cloud,
region, lookback, currency, discount). Secrets — the access token — always come from
the environment or Databricks auth, **never** the profile file (Golden Rule: never
commit secrets; profile files are gitignored as defence in depth).

Resolution precedence (highest first): explicit CLI flag → profile file entry →
environment variable → built-in default.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel

# Searched in order; first existing file wins.
PROFILE_SEARCH_PATHS = [
    Path.cwd() / "profiles.yaml",
    Path.home() / ".tellurite" / "profiles.yaml",
]


class Profile(BaseModel):
    name: str = "default"
    host: str | None = None
    http_path: str | None = None
    auth: Literal["pat", "oauth", "cli"] | None = None
    databricks_profile: str | None = None  # ~/.databrickscfg section for auth="cli"
    cloud: Literal["azure", "aws"] = "azure"
    region: str | None = None
    lookback_days: int = 30
    currency: str = "USD"
    discount_pct: float = 0.0
    min_cost_usd: float = 50.0
    show_savings_pct: bool = True


def _find_profiles_file(explicit: Path | None = None) -> Path | None:
    if explicit:
        return explicit
    for path in PROFILE_SEARCH_PATHS:
        if path.exists():
            return path
    return None


def load_profile(
    name: str | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    profile_path: Path | None = None,
) -> Profile:
    """Build a Profile from (file entry ⊕ CLI overrides ⊕ env), per the precedence above.

    `name` selects an entry in the profiles YAML (a mapping of name → settings).
    If no profiles file exists, the profile is built purely from overrides + env —
    so the tool runs with just env vars / CLI flags and no config file.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v is not None}

    file_data: dict[str, Any] = {}
    if name:
        path = _find_profiles_file(profile_path)
        if path is not None:
            doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            if name not in doc:
                raise KeyError(f"Profile '{name}' not found in {path}")
            file_data = doc[name] or {}

    merged: dict[str, Any] = {**file_data, **overrides}
    # Env fallbacks only fill gaps left by file + overrides.
    merged.setdefault("host", os.environ.get("DATABRICKS_HOST"))
    merged.setdefault("http_path", os.environ.get("DATABRICKS_HTTP_PATH"))
    merged["name"] = name or "default"
    return Profile(**merged)
