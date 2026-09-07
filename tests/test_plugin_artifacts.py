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


class PluginArtifactTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
