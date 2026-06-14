"""Shared cost-attribution logic: usage × list_prices → list dollars.

See sql/cost_attribution.sql for the canonical SQL pattern.
Rules that need $ cost reuse this CTE rather than re-deriving pricing.
"""

ATTRIBUTION_CTE = """\
WITH _attr AS (
  SELECT
    {grouping_cols},
    SUM(u.usage_quantity * lp.pricing.effective_list.default) AS list_cost_usd
  FROM system.billing.usage u
  JOIN system.billing.list_prices lp
    ON  u.sku_name = lp.sku_name
    AND u.usage_end_time >= lp.price_start_time
    AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
  WHERE u.usage_date >= DATE_SUB(current_date(), {lookback_days})
  GROUP BY {grouping_cols}
)"""


def build_attribution_cte(grouping_cols: str, lookback_days: int) -> str:
    """Return a CTE string that can be prepended to a rule's detection SQL."""
    return ATTRIBUTION_CTE.format(
        grouping_cols=grouping_cols,
        lookback_days=lookback_days,
    )
