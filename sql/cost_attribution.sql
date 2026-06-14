-- sql/cost_attribution.sql
-- Shared cost-attribution logic: convert DBU usage → list dollars, correctly handling
-- mid-period SKU price changes via the time-window join on list_prices.
-- Reused by every $-quantified rule. Group-by grain is rule-specific (job_id, cluster_id, …).
--
-- Bound params: :lookback_days
-- Caveat: pricing.effective_list.default is the LIST price. Actual invoice may be lower
-- (private discounts / commit). Surface as "estimated at list price". Optional :discount_pct
-- scales the figure downstream. currency_code comes from list_prices.

SELECT
  -- <<rule-specific grouping columns here>>
  SUM(u.usage_quantity * lp.pricing.effective_list.default) AS list_cost_usd
FROM system.billing.usage u
JOIN system.billing.list_prices lp
  ON  u.sku_name = lp.sku_name
  AND u.usage_end_time >= lp.price_start_time
  AND (lp.price_end_time IS NULL OR u.usage_end_time < lp.price_end_time)
WHERE u.usage_date >= DATE_SUB(current_date(), :lookback_days)
-- GROUP BY <<grouping columns>>
;
