# Tellurite — Databricks Cost Diagnostics

A Python and SQL prototype that turns Databricks system telemetry into ranked cost-optimization findings, with evidence, confidence and suggested remediation.

**Author and project owner: Panos Michalakos.** Independent side project. The implementation is a working prototype, not a production-certified service. Estimates are opportunities to investigate, not measured savings.

## See it working without Databricks

The offline example executes illustrative SQL against an in-memory SQLite database, then uses the same rule engine, pricing enrichment and report renderer as the live path. All example workloads and VM prices are invented. No credentials, workspace connection or network calls are required for the example after installing dependencies.

```bash
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e '.[dev]'
python examples/offline_demo.py --out demo-output
python -m pytest -q
```

Open `demo-output/report.html` in a browser. A committed [sample report](examples/sample-report.html) and [findings JSON](examples/sample-findings.json) are available without running anything.

The example produces three cost findings and one governance finding. Two cost findings concern the same workload: their naive sum is **$1,300**, while the per-resource maximum gives an **$860 overlap-adjusted estimate**. These are synthetic 30-day figures, not customer results.

## What is implemented

| Component | Evidence |
| --- | --- |
| Data-driven detection | [Rule engine](tellurite/engine.py) loads validated YAML rules and executes SQL through a connector |
| Cost assembly | Combines DBU cost with available VM pricing; missing VM prices are explicitly flagged |
| Prioritization | Findings include severity, confidence, evidence and remediation, ranked by estimated impact |
| Overlap handling | [Report summary](tellurite/report.py) uses the maximum estimate per resource rather than summing overlapping findings |
| Governance findings | Findings without dollar impact remain separate from the savings headline |
| Configuration and connectivity | Profiles, environment-based authentication, workspace scoping and a Databricks SQL adapter |
| Validation | Offline tests for configuration, engine calculations, price-catalog parsing and report summaries |

## Engineering decisions worth reviewing

1. **Keep detection separate from execution.** SQL and rule parameters live in YAML; the engine assembles findings without embedding individual detection logic.
2. **Expose uncertainty.** Unknown VM prices flag an incomplete cost basis. Confidence is carried with each finding instead of presenting every heuristic as proven savings.
3. **Avoid additive savings claims.** Multiple changes may recover the same underlying spend. The headline takes the largest estimate per resource; this is a simple overlap heuristic, not an optimization model.
4. **Make core behavior testable offline.** Connectors are an execution seam. Tests and the example exercise the engine without a paid Databricks workspace.

Read [architecture and limitations](docs/architecture.md) for the execution path, trust boundaries and shortcuts in this prototype. [Validation evidence](docs/validation.md) records the checks actually executed.

## Live path and boundaries

The live CLI is retained: `tellurite-audit --help`, `tellurite-audit ping --help` and `tellurite-audit run --help`. Copy [profiles.example.yaml](profiles.example.yaml) to the gitignored `profiles.yaml` only if you intend to configure a live workspace. Keep credentials in environment variables or your Databricks authentication setup.

The supplied live queries are intended for system telemetry. **Read-only access must be enforced through workspace permissions**; the connector is not a SQL authorization layer. Live operation was not validated as part of the offline showcase checks.

The full rule catalogue is proprietary. This repository remains a source-visible showcase if its owner chooses to publish it; no open-source licence is granted by this README. Third-party dependencies retain their own licences; bundled components are credited in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Status and limitations

- SQL compatibility and system-table availability vary across workspaces. A failed query is skipped with a warning; a partial report does not establish that all checks passed.
- Savings expressions and rule SQL are trusted project configuration. The evaluator is not a sandbox for untrusted rules.
- The live connector currently uses custom placeholder substitution. Treat it as a prototype boundary, not a general-purpose safe query interface.
- Azure public VM pricing is supported, with a bundled fallback; live AWS pricing is not implemented. Public/list rates may differ from negotiated costs.
- Monthly estimates assume a representative observation window. Changes require workload validation before implementation.

`SPEC.md`, `PRODUCT-OVERVIEW.md` and older planning notes document design intent; this README and executable checks describe the current implementation.

There is no claim here of production adoption, independently verified financial savings or a finished commercial product.
