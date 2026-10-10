# Architecture and trade-offs

## Execution path

`profile + YAML rules -> connector queries -> VM/DBU cost assembly -> ranked Findings -> overlap-adjusted report`

The live adapter reads Databricks query results. The offline example supplies synthetic rows from SQLite through the same query interface. The example rules demonstrate mechanics; they do not validate the commercial SQL against Databricks system tables.

## Start here in the code

- `tellurite/models.py`: the Rule, Finding and Remediation contracts.
- `tellurite/engine.py`: rule loading, query failure handling, cost enrichment and ranking.
- `tellurite/pricing.py`: public-price parsing, fallback behavior and snapshots.
- `tellurite/report.py`: separation of governance findings and overlapping cost estimates.
- `tests/test_engine.py` and `tests/test_report.py`: executable examples of the calculation and reporting contracts.

## Important limits

The same string identifier can occur across resource types, and the current report groups by `instance_key` alone. Its maximum-per-resource heuristic prevents some overlapping estimates from being summed, but does not solve every dependency between proposed changes.

Savings-model evaluation uses Python `eval` with builtins removed. That is convenient for trusted expressions, not a security sandbox. SQL execution relies on trusted rules and least-privilege workspace permissions. Placeholder substitution is custom, not driver-level parameter binding.

Failed queries warn and continue; missing pricing can understate the cost basis. Both behaviors prioritize a useful partial report but require the reviewer to inspect warnings and evidence. Discounts, currency conversion, representative observation windows and workload constraints need care before treating a figure as actionable financial impact.

The public example uses an exact 30-day synthetic window, invented VM prices and intentionally overlapping estimates. Its output is a demonstration of the implementation, not a live audit result.
