# Changelog

## 0.2.0 - 2026-09-11

- Replaced the Apifox MCP runtime dependency with the Apifox CLI across skill, workflow, templates, and examples.
- Added Apifox CLI preflight: install check (`npm install -g apifox-cli` with user consent), `APIFOX_TOKEN` environment variable check with configuration guidance, `apifox login`/`whoami` identity verification, and project location via `apifox project list`.
- Added CLI-based test authoring flow: `cli-schema get/validate` before every write, `test-case create`, `test-scenario create`, and single-case execution via one-step scenarios or scenario folders.
- Added AI-branch isolation for project writes with opt-in merge.
- Kept dbhub MCP as the database verification channel; execution now runs via `apifox run` with cli/json/junit reports.
- Upgraded the standard report template to a professional structure: document control block, executive summary with derived pass rate and assertion totals, environment & toolchain versioning, requirement coverage, per-case assertion details (expression/expected/actual/status), Apifox resource references, retry/duration, severity-tagged defects, and CLI report artifact index; extended `execution-summary.json` schema and `render_report.py` accordingly.

## 0.1.1 - 2026-09-09

- Added optional user-provided test case input alongside PRD and environment.
- Added provided/generated/merged/conflict source tracking and conflict blocking.
- Added user-case traceability and source statistics to the standard report.

## 0.1.0 - 2026-09-07

- Added PRD-driven full-scenario API test planning.
- Added Apifox MCP and dbhub MCP capability-discovery workflow.
- Added per-case approval gates for API and database side effects.
- Added standard test-plan, report, evidence-redaction, and summary-rendering artifacts.
