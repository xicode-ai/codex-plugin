import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "apifox-api-testing"
sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))

from redact_evidence import redact_value, redact_text  # noqa: E402
from render_report import render_report  # noqa: E402
from validate_test_plan import validate_plan_text  # noqa: E402


VALID_PLAN = """environment: staging-oms
cases:
  - id: API-001
    requirementRefs: [REQ-001]
    businessFlow: create appointment
    category: normal
    endpoint:
      method: GET
      path: /appointments
    assertions:
      api:
        - status
      database:
        - record existence
    risk: low
    requiresConfirmation: false
    executionStatus: planned
"""

PROVIDED_PLAN = """environment: staging-oms
cases:
  - id: API-USER-001
    source: provided
    sourceEvidence:
      userCaseId: TC-001
    requirementRefs: [REQ-001]
    businessFlow: create appointment
    category: normal
    endpoint:
      method: POST
      path: /appointments
    assertions:
      api:
        - status
      database:
        - record existence
    risk: medium
    requiresConfirmation: true
    executionStatus: planned
"""

CONFLICT_PLAN = PROVIDED_PLAN.replace(
    "    source: provided\n", "    source: conflict\n"
).replace(
    "    executionStatus: planned\n", "    executionStatus: passed\n"
)


class PluginArtifactTests(unittest.TestCase):
    def test_skill_supports_user_cases_as_high_priority_baseline(self):
        skill = (PLUGIN_ROOT / "skills" / "apifox-api-testing" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for phrase in ("用户测试用例", "provided", "merged", "conflict", "不得静默"):
            self.assertIn(phrase, skill)

    def test_manifest_has_no_external_mcp_declaration(self):
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "apifox-api-testing")
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("apps", manifest)
        self.assertNotIn("hooks", manifest)

    def test_validate_test_plan_accepts_minimal_case(self):
        self.assertEqual(validate_plan_text(VALID_PLAN), [])

    def test_validate_test_plan_rejects_case_without_api_or_db_assertion(self):
        invalid = VALID_PLAN.replace("      api:\n        - status\n", "")
        invalid = invalid.replace("      database:\n        - record existence\n", "")
        errors = validate_plan_text(invalid)
        self.assertTrue(any("assertions" in error for error in errors))

    def test_validate_test_plan_accepts_provided_case_traceability(self):
        self.assertEqual(validate_plan_text(PROVIDED_PLAN), [])

    def test_validate_test_plan_rejects_executable_conflict_case(self):
        errors = validate_plan_text(CONFLICT_PLAN)
        self.assertTrue(any("conflict" in error for error in errors))

    def test_redact_evidence_masks_auth_and_secret_fields(self):
        value = {
            "authorization": "Bearer abc123",
            "password": "plain-secret",
            "nested": {"token": "token-value"},
        }
        redacted = redact_value(value)
        self.assertEqual(redacted["authorization"], "[REDACTED]")
        self.assertEqual(redacted["password"], "[REDACTED]")
        self.assertEqual(redacted["nested"]["token"], "[REDACTED]")
        text = redact_text("Authorization: Bearer abc123\nCookie: session=secret")
        self.assertNotIn("abc123", text)
        self.assertNotIn("session=secret", text)
        self.assertIn("[REDACTED]", text)

    def test_render_report_includes_counts_and_conclusion(self):
        summary = {
            "metadata": {
                "task_name": "Appointment API",
                "prd_id": "PRD-001",
                "environment": "staging-oms",
            },
            "counts": {
                "total": 2,
                "passed": 1,
                "failed": 0,
                "blocked": 1,
                "skipped": 0,
                "errors": 0,
            },
            "requirements": [],
            "cases": [
                {
                    "id": "API-001",
                    "businessFlow": "create appointment",
                    "endpoint": {"method": "GET", "path": "/appointments"},
                    "executionStatus": "passed",
                    "apiResult": "PASS",
                    "databaseResult": "PASS",
                }
            ],
            "defects": [],
            "blockers": ["dbhub fixture capability unavailable"],
            "cleanup": [],
            "conclusion": {"status": "PARTIAL", "reason": "One case is blocked."},
        }
        template = (PLUGIN_ROOT / "templates" / "test-report.md").read_text(encoding="utf-8")
        report = render_report(summary, template)
        self.assertIn("Appointment API", report)
        self.assertIn("| 2 |", report)
        self.assertIn("PARTIAL", report)
        self.assertIn("API-001", report)
        self.assertIn("dbhub fixture capability unavailable", report)

    def test_render_report_includes_source_counts_and_conflicts(self):
        summary = {
            "metadata": {"environment": "staging-oms"},
            "counts": {"total": 4},
            "sourceCounts": {"provided": 1, "generated": 1, "merged": 1, "conflict": 1},
            "requirements": [],
            "cases": [
                {
                    "id": "API-USER-001",
                    "source": "merged",
                    "userCaseId": "TC-001",
                    "requirementRefs": ["REQ-001"],
                    "endpoint": {"method": "POST", "path": "/appointments"},
                    "executionStatus": "passed",
                }
            ],
            "conflicts": [
                {
                    "caseId": "API-USER-002",
                    "source": "conflict",
                    "reason": "Expected status differs from contract",
                    "impact": "case blocked",
                }
            ],
            "defects": [],
            "blockers": [],
            "cleanup": [],
            "conclusion": {"status": "PARTIAL", "reason": "Conflict requires review."},
        }
        template = (PLUGIN_ROOT / "templates" / "test-report.md").read_text(encoding="utf-8")
        report = render_report(summary, template)
        for value in ("provided: 1", "generated: 1", "merged: 1", "conflict: 1"):
            self.assertIn(value, report)
        self.assertIn("TC-001", report)
        self.assertIn("API-USER-002", report)
        self.assertIn("Expected status differs from contract", report)

    def test_render_report_includes_professional_sections(self):
        summary = {
            "metadata": {
                "task_name": "OMS 回归",
                "environment": "test-oms",
                "project_name": "OMS",
                "apifox_project_id": 8120721,
                "apifox_branch": "ai/20260911-from-main-regression",
                "apifox_cli_version": "2.2.9",
                "plugin_version": "0.2.0",
                "execution_duration": "3m20s",
                "apifox_report_files": ["apifox-reports/report.html", "apifox-reports/junit.xml"],
            },
            "counts": {
                "total": 2,
                "passed": 1,
                "failed": 1,
                "blocked": 0,
                "skipped": 0,
                "errors": 0,
                "retries": 1,
                "api_assertions_passed": 2,
                "api_assertions_failed": 1,
                "db_assertions_passed": 1,
                "db_assertions_failed": 0,
            },
            "requirements": [
                {"id": "REQ-001", "goal": "创建入库单", "cases": ["API-001"], "status": "covered"},
                {"id": "REQ-002", "goal": "查询入库单", "cases": [], "status": "pending"},
            ],
            "cases": [
                {
                    "id": "API-001",
                    "businessFlow": "创建入库单",
                    "endpoint": {"method": "POST", "path": "/biz/inboundOrder"},
                    "apifox": {"caseId": 400273209, "scenarioId": 637132, "branch": "ai/regression"},
                    "preconditions": "测试租户已存在",
                    "steps": [{"action": "提交入库单", "detail": "返回单号"}],
                    "executionStatus": "failed",
                    "assertions": {
                        "api": [
                            {"expression": "responseJson.code", "expected": 0, "actual": 500, "status": "failed"}
                        ],
                        "db": [
                            {"expression": "count(*) where status='CREATED'", "expected": 1, "actual": 1, "status": "passed"}
                        ],
                    },
                    "retry": 0,
                    "durationMs": 850,
                }
            ],
            "defects": [
                {
                    "title": "创建入库单返回 500",
                    "severity": "HIGH",
                    "description": "服务端异常",
                    "caseRefs": ["API-001"],
                    "suggestion": "检查库存服务依赖",
                }
            ],
            "blockers": [],
            "cleanup": [],
            "conclusion": {"status": "FAIL", "reason": "One API assertion failed."},
        }
        template = (PLUGIN_ROOT / "templates" / "test-report.md").read_text(encoding="utf-8")
        report = render_report(summary, template)
        self.assertIn("50.0%", report)
        self.assertIn("3m20s", report)
        self.assertIn("1/2（50.0%）", report)
        self.assertIn("用例 #400273209", report)
        self.assertIn("场景 #637132", report)
        self.assertIn("[FAILED] responseJson.code", report)
        self.assertIn("（期望 0，实际 500）", report)
        self.assertIn("[HIGH] 创建入库单返回 500", report)
        self.assertIn("apifox-reports/report.html", report)
        self.assertIn("共 2 条用例", report)


if __name__ == "__main__":
    unittest.main()
