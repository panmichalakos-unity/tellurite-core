-- sql/cost_attribution.sql
-- Shared DBU cost-attribution logic: convert DBU usage → list dollars, correctly handling
-- mid-period SKU price changes via the time-window join on list_prices.
-- This is the DBU half of full-TCO costing (SPEC §6); VM/infra cost is added in pricing.py /
-- attribution.py, so the final list_cost_usd a rule sees = dbu_cost_usd + vm_cost_usd.
-- Reused by every $-quantified rule. Group-by grain is rule-specific (job_id, cluster_id, …).
--
-- Bound params: :lookback_days
-- Caveat: this is the LIST price. Actual invoice may be lower (private discounts / commit).
-- Surface as "estimated at list price". Optional :discount_pct scales the figure downstream.
-- currency_code comes from list_prices. Prefer the promotional price where present.

SELECT
  -- <<rule-specific grouping columns here>>
  SUM(u.usage_quantity * COALESCE(lp.pricing.promotional.default, lp.pricing.effective_list.default)) AS dbu_cost_usd
FROM system.billing.usage u
JOIN system.billing.list_prices lp
  ON  u.sku_name = lp.sku_name
  AND u.usage_end_time >= lp.price_start_time
  AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
WHERE u.usage_date >= DATE_SUB(current_date(), :lookback_days)
-- GROUP BY <<grouping columns>>
;
