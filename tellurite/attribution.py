"""Shared DBU cost-attribution logic: usage × list_prices → list dollars.

This is the canonical DBU half of full-TCO costing (SPEC §6); the VM/infra half is
added by `pricing.py` at M1.5, and the engine assembles the total per Option A (§7):
`list_cost_usd = dbu_cost_usd + vm_cost_usd`.

Rules embed this join inline in their `detection_sql` (each groups at a different grain
— job_id, warehouse_id, cluster_id …), emitting `dbu_cost_usd`. This module is the
single source of truth for the join's shape; keep it in sync with
`sql/cost_attribution.sql`. The price expression prefers the promotional price where
present, falling back to the effective list price.
"""

# Promotional-aware per-unit DBU price (see SPEC §6). Mirrors sql/cost_attribution.sql.
DBU_PRICE_EXPR = "COALESCE(lp.pricing.promotional.default, lp.pricing.effective_list.default)"

ATTRIBUTION_CTE = """\
WITH _attr AS (
  SELECT
    {grouping_cols},
    SUM(u.usage_quantity * {price_expr}) AS dbu_cost_usd
  FROM system.billing.usage u
  JOIN system.billing.list_prices lp
    ON  u.sku_name = lp.sku_name
    AND u.usage_end_time >= lp.price_start_time
    AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
  WHERE u.usage_date >= DATE_SUB(current_date(), {lookback_days})
  GROUP BY {grouping_cols}
)"""


def build_attribution_cte(grouping_cols: str, lookback_days: int) -> str:
    """Return a `_attr` CTE (grouped at `grouping_cols`) exposing `dbu_cost_usd`."""
    return ATTRIBUTION_CTE.format(
        grouping_cols=grouping_cols,
        price_expr=DBU_PRICE_EXPR,
        lookback_days=lookback_days,
    )
