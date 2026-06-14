"""Databricks SQL connector — executes read-only queries against system.* tables."""

from __future__ import annotations

import os
from typing import Any

from databricks import sql as dbsql


class ConnectorConfig:
    def __init__(
        self,
        host: str | None = None,
        http_path: str | None = None,
        token: str | None = None,
        lookback_days: int = 30,
        currency: str = "USD",
        discount_pct: float = 0.0,
    ) -> None:
        self.host = host or os.environ["DATABRICKS_HOST"]
        self.http_path = http_path or os.environ["DATABRICKS_HTTP_PATH"]
        self.token = token or os.environ.get("DATABRICKS_TOKEN")
        self.lookback_days = lookback_days
        self.currency = currency
        self.discount_pct = discount_pct


class DatabricksConnector:
    def __init__(self, config: ConnectorConfig) -> None:
        self._config = config
        self._conn: Any = None

    def connect(self) -> None:
        kwargs: dict[str, Any] = {
            "server_hostname": self._config.host,
            "http_path": self._config.http_path,
        }
        if self._config.token:
            kwargs["access_token"] = self._config.token
        self._conn = dbsql.connect(**kwargs)

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "DatabricksConnector":
        self.connect()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def query(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """Execute a read-only SQL statement and return rows as dicts."""
        assert self._conn is not None, "call connect() first"
        # Replace :param placeholders with values for connectors that don't support named params
        resolved = _bind_params(sql, params or {})
        with self._conn.cursor() as cur:
            cur.execute(resolved)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]

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
