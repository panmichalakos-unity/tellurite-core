"""Databricks SQL adapter — intended for trusted system-telemetry queries.

Read-only access is enforced by workspace permissions, not by this adapter.

Connection-only config lives here; run parameters (lookback, currency, discount,
cloud, region) live on the Profile in `config.py`. The `databricks` SQL driver is
imported lazily inside `connect()` so the engine and models stay unit-testable
without the (heavy) driver installed.
"""

from __future__ import annotations

import os
from datetime import date, datetime
from decimal import Decimal
from typing import Any


class ConnectorConfig:
    def __init__(
        self,
        host: str | None = None,
        http_path: str | None = None,
        token: str | None = None,
        auth: str | None = None,
        databricks_profile: str | None = None,
    ) -> None:
        self.host = host or os.environ.get("DATABRICKS_HOST")
        self.http_path = http_path or os.environ.get("DATABRICKS_HTTP_PATH")
        self.token = token or os.environ.get("DATABRICKS_TOKEN")
        # Auth mode: "pat" | "oauth" | "cli". Default: PAT if a token is available,
        # else OAuth user-to-machine (browser login, no PAT required).
        #   pat   — personal access token
        #   oauth — connector-native U2M browser login (cached by the connector)
        #   cli   — Databricks unified auth via the SDK (~/.databrickscfg from
        #           `databricks auth login`, env vars, Azure CLI); headless after login
        self.auth = auth or os.environ.get("DATABRICKS_AUTH")
        # Optional ~/.databrickscfg section name for auth="cli" (else matched by host).
        self.databricks_profile = databricks_profile or os.environ.get("DATABRICKS_CONFIG_PROFILE")


class DatabricksConnector:
    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config
        self._conn: Any = None

    def connect(self) -> None:
        if not self._config.host or not self._config.http_path:
            raise ValueError(
                "Missing connection config: set host and http_path via a profile, "
                "CLI flags, or DATABRICKS_HOST / DATABRICKS_HTTP_PATH env vars."
            )
        from databricks import sql as dbsql  # lazy: heavy driver, only needed for live runs

        kwargs: dict[str, Any] = {
            "server_hostname": self._config.host,
            "http_path": self._config.http_path,
        }
        auth = self._config.auth
        # PAT when a token is present (or auth=pat) and not explicitly oauth/cli.
        use_pat = auth == "pat" or (auth not in ("oauth", "cli") and self._config.token)
        if auth == "cli":
            # Databricks unified auth via the SDK: reuses ~/.databrickscfg credentials
            # created by `databricks auth login` (and env vars / Azure CLI). Headless
            # after the first login; the SDK refreshes OAuth tokens automatically.
            from databricks.sdk.core import Config

            cfg = (
                Config(profile=self._config.databricks_profile)
                if self._config.databricks_profile
                else Config(host=f"https://{self._config.host}")
            )
            kwargs["credentials_provider"] = lambda: cfg.authenticate
        elif use_pat:
            if not self._config.token:
                raise ValueError("auth=pat requires a token (DATABRICKS_TOKEN or --token).")
            kwargs["access_token"] = self._config.token
        else:
            # Connector-native user-to-machine OAuth: opens a browser, caches the token.
            kwargs["auth_type"] = "databricks-oauth"
        self._conn = dbsql.connect(**kwargs)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> DatabricksConnector:
        self.connect()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute trusted SQL and return rows as dicts; privileges enforce read-only access."""
        assert self._conn is not None, "call connect() first"
        # Replace :param placeholders with values for connectors that don't support named params
        resolved = _bind_params(sql, params or {})
        with self._conn.cursor() as cur:
            cur.execute(resolved)
            cols = [d[0] for d in cur.description]
            return [
                {c: _jsonable(v) for c, v in zip(cols, row, strict=True)} for row in cur.fetchall()
            ]

    def ping(self) -> bool:
        """Verify connectivity with a trivial query."""
        rows = self.query("SELECT 1 AS ok")
        return bool(rows and rows[0].get("ok") == 1)

    def column_exists(self, table: str, column: str) -> bool:
        """Introspect whether a column exists — used for fail-soft schema drift."""
        try:
            rows = self.query(f"DESCRIBE {table}")  # noqa: S608
            col_names = {r.get("col_name", "").lower() for r in rows}
            return column.lower() in col_names
        except Exception:
            return False


def _jsonable(value: Any) -> Any:
    """Coerce Databricks result values to JSON-safe primitives, recursing into
    structs/maps/arrays. Numeric cells arrive as Decimal, timestamps as datetime —
    neither is JSON-serializable, and float keeps the engine's cost math simple.
    """
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return value


def _bind_params(sql: str, params: dict[str, Any]) -> str:
    """Naive named-param substitution (:name → value). SQL-safe for numeric/string literals."""
    for key, value in params.items():
        placeholder = f":{key}"
        if isinstance(value, str):
            safe = value.replace("'", "''")
            sql = sql.replace(placeholder, f"'{safe}'")
        else:
            sql = sql.replace(placeholder, str(value))
    return sql
