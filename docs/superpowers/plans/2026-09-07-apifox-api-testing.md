# Apifox API Testing Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax (- [ ]) for tracking.

**Goal:** 在当前仓库中创建可安装、可校验的 apifox-api-testing Codex 插件，通过已配置的 Apifox MCP 和 dbhub MCP，根据 PRD 与环境标识设计、门禁并执行全场景接口测试。

**Architecture:** 插件采用 Skill 编排型架构，不内置 MCP 服务或凭据。Skill 负责 MCP 能力发现、接口和数据库测试流程、写操作确认与标准报告；本地 Python 脚本只负责计划校验、证据脱敏和报告渲染。

**Tech Stack:** Codex plugin manifest、Markdown Skill/reference/template 文件、Python 3 标准库、JSON、YAML-like 测试计划文本、unittest。

## Global Constraints

- 运行时依赖用户已配置的 Apifox MCP 和 dbhub MCP，不在插件内保存或配置凭据。
- 用户输入最小化为 PRD 文档和环境标识；环境映射不明确时必须阻塞。
- 只读/校验场景可自动执行；API 和数据库副作用操作必须逐条确认。
- 报告不得持久化 Token、Cookie、密码、预约码、完整响应体或完整 SQL 参数。
- 插件不固定 Apifox 项目、数据库、接口数量或某一版本 dbhub 工具名。
- 插件目录名与 .codex-plugin/plugin.json 的 name 必须同为 apifox-api-testing。
- 不创建 .mcp.json、.app.json 或 marketplace 文件，因为用户要求复用现有 MCP 且未要求 marketplace 注册。
- 所有代码和文档变更保留当前仓库已有内容；每次校验必须能在无外部 MCP 的本地环境运行。

---

## 文件结构

将创建以下文件：

~~~text
plugins/apifox-api-testing/
├── .codex-plugin/plugin.json
├── skills/apifox-api-testing/SKILL.md
├── skills/apifox-api-testing/references/
│   ├── apifox-mcp-workflow.md
│   ├── dbhub-mcp-workflow.md
│   ├── test-design-rules.md
│   └── safety-and-data-policy.md
├── templates/test-case-matrix.md
├── templates/test-report.md
├── templates/execution-summary.json
├── scripts/validate_test_plan.py
├── scripts/redact_evidence.py
├── scripts/render_report.py
├── examples/prd-input.md
├── examples/environment-input.yaml
├── README.md
└── CHANGELOG.md
~~~

测试文件：

~~~text
tests/test_plugin_artifacts.py
~~~

---

### Task 1: Scaffold the plugin and metadata

**Files:**
- Create: plugins/apifox-api-testing/.codex-plugin/plugin.json
- Create: plugins/apifox-api-testing/README.md
- Create: plugins/apifox-api-testing/CHANGELOG.md

**Interfaces:**
- Produces the plugin root and manifest consumed by Codex plugin validation.
- Leaves MCP configuration external; the manifest contains no mcpServers, apps, or unsupported hooks field.

- [ ] **Step 1: Generate the basic plugin root**

Run from the plugin-creator skill directory:

~~~bash
python3 scripts/create_basic_plugin.py apifox-api-testing \
  --path /Users/louis/Documents/idea_workspace/github/xicode-ai/codex-plugin/plugins \
  --with-skills \
  --with-scripts
~~~

Expected: plugins/apifox-api-testing/.codex-plugin/plugin.json exists and the plugin name is normalized as apifox-api-testing.

- [ ] **Step 2: Replace the generated manifest with the repository metadata**

The manifest must contain this shape:

~~~json
{
  "name": "apifox-api-testing",
  "version": "0.1.0",
  "description": "Design and execute PRD-driven full-scenario API tests through Apifox MCP and dbhub MCP.",
  "author": {
    "name": "Xicode AI",
    "email": "engineering@example.com"
  },
  "license": "MIT",
  "keywords": ["apifox", "api-testing", "dbhub", "prd", "test-report"],
  "skills": "./skills/",
  "interface": {
    "displayName": "Apifox API Testing",
    "shortDescription": "PRD-driven Apifox API testing with database verification",
    "longDescription": "Designs full-scenario API tests from a PRD and executes safe, traceable API and database verification through the user's configured Apifox and dbhub MCPs.",
    "developerName": "Xicode AI",
    "category": "Developer Tools",
    "capabilities": ["Analyze", "Test", "Write"],
    "defaultPrompt": [
      "根据 PRD 在指定环境执行 Apifox 全场景接口测试",
      "生成 API 与数据库一致性测试计划",
      "输出脱敏的标准接口测试报告"
    ],
    "brandColor": "#2563EB"
  }
}
~~~

Do not add placeholder values, local credentials, fixed project IDs, or MCP declarations.

- [ ] **Step 3: Add the user-facing README and changelog**

README.md must document the two required MCP prerequisites, the minimal PRD-plus-environment input, the read-only default, the per-case confirmation gate, the three output artifacts, and the fact that local scripts do not call external systems.

CHANGELOG.md must contain a 0.1.0 entry describing the initial plugin capabilities.

- [ ] **Step 4: Validate the manifest**

Run:

~~~bash
python3 /Users/louis/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  /Users/louis/Documents/idea_workspace/github/xicode-ai/codex-plugin/plugins/apifox-api-testing
~~~

Expected: validator exits with code 0 and reports no manifest errors.

---

### Task 2: Implement the runtime Skill and MCP references

**Files:**
- Create: plugins/apifox-api-testing/skills/apifox-api-testing/SKILL.md
- Create: plugins/apifox-api-testing/skills/apifox-api-testing/references/apifox-mcp-workflow.md
- Create: plugins/apifox-api-testing/skills/apifox-api-testing/references/dbhub-mcp-workflow.md
- Create: plugins/apifox-api-testing/skills/apifox-api-testing/references/test-design-rules.md
- Create: plugins/apifox-api-testing/skills/apifox-api-testing/references/safety-and-data-policy.md
- Create: plugins/apifox-api-testing/examples/prd-input.md
- Create: plugins/apifox-api-testing/examples/environment-input.yaml

**Interfaces:**
- SKILL.md consumes a PRD and environment identifier and produces a test plan, approval prompts, MCP execution steps, and report artifacts.
- The Apifox reference defines the preferred sequence listOpenApiEndpoints → getOpenApiDetails → executeOpenApi, with dynamic tool discovery before invocation.
- The dbhub reference defines capability discovery for schema inspection, read queries, fixture setup, postcondition checks, and cleanup.

- [ ] **Step 1: Write the Skill front matter and invocation contract**

The Skill must declare a focused name and description and state that it is used for PRD-driven API testing through existing Apifox and dbhub MCPs. Its first runtime checks must require both a PRD and an environment identifier; if either is absent, ask for the missing input before any tool call.

- [ ] **Step 2: Add the preflight and dynamic tool-discovery procedure**

The Skill must instruct Codex to:

1. Discover actual MCP tools available in the current conversation.
2. Match Apifox capabilities for endpoint listing, detail retrieval, and execution.
3. Match dbhub capabilities for schema/read/setup/assertion/cleanup.
4. Resolve the same environment alias through both MCPs.
5. Stop with BLOCKED if a capability or environment mapping is missing.

The procedure must treat remembered tool names as preferred aliases, not guaranteed availability.

- [ ] **Step 3: Add test-plan generation and approval gates**

The Skill must generate all required scenario categories, attach API and DB assertions to every applicable case, classify side effects using contract and business semantics, and show a concise impact summary before each mutating API or database action. A user approval for one case must not authorize unrelated cases.

- [ ] **Step 4: Add execution, evidence, cleanup, and report instructions**

The Skill must define:

- one explicit retry maximum for read-only network failures;
- no automatic retries for writes;
- PASS, FAIL, BLOCKED, SKIPPED, and ERROR semantics;
- API-before/after database verification;
- redaction rules for all evidence;
- final output paths test-report.md, test-plan.yaml, and execution-summary.json;
- a rule that local execution evidence is not production or release readiness proof.

- [ ] **Step 5: Write the four reference documents and examples**

Each reference file must be self-contained and concise:

- apifox-mcp-workflow.md: pagination, filtering, detail retrieval, request construction, execution, and response evidence limits.
- dbhub-mcp-workflow.md: schema discovery, read-only query first, fixture marker, snapshots, assertions, cleanup, and capability blockers.
- test-design-rules.md: scenario categories, requirement traceability, state/idempotency, permissions, pagination, and consistency.
- safety-and-data-policy.md: confirmation rules, secrets, retries, environment mismatch, destructive operations, and cleanup.

Examples must use placeholders such as staging-oms and synthetic field values only.

---

### Task 3: Add standard test-plan and report templates

**Files:**
- Create: plugins/apifox-api-testing/templates/test-case-matrix.md
- Create: plugins/apifox-api-testing/templates/test-report.md
- Create: plugins/apifox-api-testing/templates/execution-summary.json

**Interfaces:**
- Templates are consumed by the Skill and by render_report.py.
- execution-summary.json is a valid empty summary document with stable keys: metadata, counts, requirements, cases, defects, blockers, cleanup, and conclusion.

- [ ] **Step 1: Create the test-case matrix template**

Include columns/sections for case ID, requirement references, business flow, category, endpoint, preconditions, data setup, API assertions, DB assertions, risk, confirmation status, execution status, evidence, and cleanup.

- [ ] **Step 2: Create the report template**

Include the nine fixed sections from the approved design: basic information, execution summary, requirement traceability, case details, defects and risks, blockers and skipped scope, cleanup/environment impact, conclusion/recommendations, and appendix. Include explicit placeholders for API and DB assertion statistics.

- [ ] **Step 3: Create the valid empty JSON summary**

Use this exact top-level shape:

~~~json
{
  "metadata": {},
  "counts": {
    "total": 0,
    "passed": 0,
    "failed": 0,
    "blocked": 0,
    "skipped": 0,
    "errors": 0
  },
  "requirements": [],
  "cases": [],
  "defects": [],
  "blockers": [],
  "cleanup": [],
  "conclusion": {
    "status": "BLOCKED",
    "reason": "No test cases have been executed."
  }
}
~~~

---

### Task 4: Implement local safety and report scripts

**Files:**
- Create: plugins/apifox-api-testing/scripts/validate_test_plan.py
- Create: plugins/apifox-api-testing/scripts/redact_evidence.py
- Create: plugins/apifox-api-testing/scripts/render_report.py
- Create: tests/test_plugin_artifacts.py

**Interfaces:**
- validate_test_plan.py PATH exits 0 for a valid YAML-like plan and nonzero with actionable errors for missing required fields.
- redact_evidence.py PATH [--output PATH] reads UTF-8 text or JSON, replaces sensitive values, and writes the redacted result without changing the source unless --output is explicitly provided.
- render_report.py SUMMARY_JSON [--template PATH] [--output PATH] renders a Markdown report to stdout or the explicit output file.
- Test helpers are pure standard-library functions so the unit tests do not require Apifox, dbhub, network access, or third-party packages.

- [ ] **Step 1: Write failing unit tests for artifact and script contracts**

Add tests with these cases:

~~~python
def test_manifest_has_no_external_mcp_declaration(): ...
def test_validate_test_plan_accepts_minimal_case(): ...
def test_validate_test_plan_rejects_case_without_api_or_db_assertion(): ...
def test_redact_evidence_masks_auth_and_secret_fields(): ...
def test_render_report_includes_counts_and_conclusion(): ...
~~~

Run:

~~~bash
python3 -m unittest tests.test_plugin_artifacts -v
~~~

Expected: the tests initially fail because the scripts and plugin artifacts are incomplete.

- [ ] **Step 2: Implement the plan validator**

Implement these standard-library functions:

~~~python
REQUIRED_CASE_FIELDS = (
    "id",
    "requirementRefs",
    "businessFlow",
    "category",
    "endpoint",
    "assertions",
    "risk",
    "requiresConfirmation",
    "executionStatus",
)

def validate_plan_text(text: str) -> list[str]:
    """Return deterministic validation errors for a YAML-like test plan."""

def main(argv: list[str]) -> int:
    """Read one UTF-8 plan path, print errors, and return a process code."""
~~~

The validator may use line-based structural checks so it remains dependency-free, but it must detect missing top-level environment metadata, missing case IDs, missing endpoint method/path, missing both API and DB assertion sections, invalid execution status, and unsafe empty confirmation metadata for mutating cases.

- [ ] **Step 3: Implement recursive evidence redaction**

Implement:

~~~python
SENSITIVE_KEY_PATTERN = re.compile(
    r"(authorization|token|access_token|refresh_token|cookie|password|secret|"
    r"client_secret|appointment_code|verification_code)",
    re.IGNORECASE,
)

def redact_value(value: object) -> object:
    """Return a JSON-compatible redacted copy without mutating the input."""

def redact_text(text: str) -> str:
    """Mask bearer tokens, cookies, password-like fields, and secret values."""

def main(argv: list[str]) -> int:
    """Redact a file and write only to an explicit output path or stdout."""
~~~

Values must be replaced with [REDACTED]; bearer tokens and cookie headers must be masked even when they occur in free text.

- [ ] **Step 4: Implement deterministic report rendering**

Implement:

~~~python
def load_summary(path: Path) -> dict:
    """Load and validate the summary JSON object."""

def render_report(summary: dict, template_text: str) -> str:
    """Render fixed headings and summary data while preserving the template sections."""

def main(argv: list[str]) -> int:
    """Render to stdout or an explicitly requested output path."""
~~~

The renderer must include total/pass/fail/blocked/skipped/error counts, conclusion status, environment, and each case's API/DB result; it must pass the final text through redact_text before writing.

- [ ] **Step 5: Run the focused unit tests**

Run:

~~~bash
python3 -m unittest tests.test_plugin_artifacts -v
~~~

Expected: all tests pass without external services.

---

### Task 5: Validate the complete plugin and finish the handoff

**Files:**
- Modify: any implementation file that fails validation or contains a placeholder.
- Test: tests/test_plugin_artifacts.py

**Interfaces:**
- Produces a validated plugin directory and reproducible local verification commands.

- [ ] **Step 1: Run manifest validation**

~~~bash
python3 /Users/louis/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py \
  /Users/louis/Documents/idea_workspace/github/xicode-ai/codex-plugin/plugins/apifox-api-testing
~~~

Expected: exit code 0.

- [ ] **Step 2: Run Python syntax and unit checks**

~~~bash
python3 -m compileall -q plugins/apifox-api-testing/scripts tests
python3 -m unittest discover -s tests -p 'test_*.py' -v
~~~

Expected: no syntax errors and all tests pass.

- [ ] **Step 3: Check documentation and sensitive-value hygiene**

~~~bash
rg -n -e '\\x5b\\x54\\x4f\\x44\\x4f:' -e '\\x54\\x42\\x44' -e 'sk-[A-Za-z0-9]{20,}' \
  plugins/apifox-api-testing docs/superpowers/plans/2026-09-07-apifox-api-testing.md
git diff --check
~~~

Expected: no placeholder or credential-like matches and no whitespace errors.

- [ ] **Step 4: Inspect final repository state**

~~~bash
git status --short
git diff --stat
~~~

Confirm that the only changes are the approved design/plan documents and the new plugin implementation files.

- [ ] **Step 5: Report implementation boundaries**

The handoff must distinguish:

- local manifest/template/script validation;
- Skill behavior that depends on the user's configured Apifox MCP and dbhub MCP;
- integration/UAT evidence that was not executed because no external MCP session was authorized in this workspace.
