import json
import re
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "flowx-config-docs"

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
from render_config_doc import render_config_document  # noqa: E402
from validate_config_data import validate_config_payload  # noqa: E402

BASE_PAYLOAD = {
    "metadata": {
        "database": "oms_test",
        "scope": "预约单",
        "generated_at": "2026-09-10",
        "query_status": "complete",
    },
    "dictionary": [
        {
            "operation": "",
            "dict_code": "productStatus",
            "dict_key": "1",
            "dict_value": "待审核",
            "en": "Pending Review",
            "ja": "",
            "ko": "검토 대기",
            "es": "",
            "pt": "",
        }
    ],
    "menus": [
        {
            "level1": "商品管理",
            "level2": "",
            "button_or_button_menu": "",
            "menu_code": "product",
            "route": "/product",
            "en": "Product Management",
            "ja": "",
            "ko": "",
            "es": "",
            "pt": "",
            "sort": 1,
            "authorization": "product:list",
            "resource": "product",
            "operation_type": "",
        }
    ],
    "i18n": [
        {
            "operation_type": "",
            "level1_key": "inboundOrder",
            "level2_key": "pendingShelving",
            "zh": "待上架{unit}",
            "en": "Pending Putaway {unit}",
            "ja": "",
            "ko": "",
            "es": "",
            "pt": "",
        }
    ],
    "notices": {"dictionary": [], "menus": [], "i18n": []},
    "translation_sources": {"database": 1, "automatic": 0, "pending": 0},
}


class FlowxConfigValidatorTests(unittest.TestCase):
    def test_base_payload_is_valid(self):
        self.assertEqual(validate_config_payload(BASE_PAYLOAD), [])

    def test_validator_requires_all_three_sections_and_fields(self):
        invalid = dict(BASE_PAYLOAD)
        invalid.pop("menus")
        invalid["dictionary"] = [dict(BASE_PAYLOAD["dictionary"][0], dict_code=None)]
        errors = validate_config_payload(invalid)
        self.assertTrue(any("menus" in error for error in errors))
        self.assertTrue(any("dict_code" in error for error in errors))

    def test_validator_rejects_changed_placeholders(self):
        invalid = dict(BASE_PAYLOAD)
        invalid["i18n"] = [dict(BASE_PAYLOAD["i18n"][0], en="Pending Putaway")]
        errors = validate_config_payload(invalid)
        self.assertTrue(any("placeholder" in error for error in errors))

    def test_validator_rejects_sensitive_values(self):
        invalid = dict(BASE_PAYLOAD)
        invalid["metadata"] = dict(BASE_PAYLOAD["metadata"], connection="jdbc:postgresql://user:pass@db/app")
        errors = validate_config_payload(invalid)
        self.assertTrue(any("sensitive" in error for error in errors))


class FlowxConfigRendererTests(unittest.TestCase):
    def test_render_has_three_tables_and_preserves_protected_values(self):
        template = (PLUGIN_ROOT / "templates" / "flowx-config-docs.md").read_text(encoding="utf-8")
        report = render_config_document(BASE_PAYLOAD, template)
        self.assertEqual(report.count("| 操作 | 字典编码 | 字典key | 字典值 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语 |"), 1)
        self.assertIn("|  | productStatus | 1 | 待审核 |", report)
        self.assertIn("| 商品管理 |  |  | product | /product |", report)
        self.assertIn("|  | inboundOrder | pendingShelving | 待上架{unit} |", report)
        self.assertIn("/product", report)
        self.assertIn("product:list", report)

    def test_render_keeps_empty_sections_and_notes(self):
        payload = dict(BASE_PAYLOAD)
        payload["dictionary"] = []
        payload["notices"] = {"dictionary": ["未查询到匹配配置。"], "menus": [], "i18n": []}
        template = (PLUGIN_ROOT / "templates" / "flowx-config-docs.md").read_text(encoding="utf-8")
        report = render_config_document(payload, template)
        self.assertIn("## 数据字典", report)
        self.assertIn("> 未查询到匹配配置。", report)
        self.assertIn("## 菜单权限", report)
        self.assertIn("## 国际化", report)

    def test_render_escapes_markdown_cells_without_changing_values_semantically(self):
        payload = dict(BASE_PAYLOAD)
        payload["i18n"] = [dict(BASE_PAYLOAD["i18n"][0], zh="名称 | 说明\n第二行")]
        template = (PLUGIN_ROOT / "templates" / "flowx-config-docs.md").read_text(encoding="utf-8")
        report = render_config_document(payload, template)
        self.assertIn("名称 \\| 说明<br>第二行", report)


class FlowxConfigSkillTests(unittest.TestCase):
    def test_skill_mentions_fixed_outputs_and_runtime_safety(self):
        skill = (PLUGIN_ROOT / "skills" / "flowx-config-docs" / "SKILL.md").read_text(encoding="utf-8")
        for phrase in (
            "数据库",
            "配置需求",
            "数据字典",
            "菜单权限",
            "国际化",
            "英文",
            "日文",
            "韩文",
            "西班牙语",
            "葡萄牙语",
            "只读",
            "候选",
            "未查询到匹配配置",
            "占位符",
            "flowx-config-",
            "不调用",
        ):
            self.assertIn(phrase, skill)

    def test_reference_files_exist(self):
        reference_root = PLUGIN_ROOT / "skills" / "flowx-config-docs" / "references"
        for name in ("dbhub-workflow.md", "field-mapping.md", "translation-rules.md"):
            self.assertTrue((reference_root / name).is_file())


class FlowxConfigArtifactTests(unittest.TestCase):
    def test_plugin_manifest_is_standalone_and_has_expected_identity(self):
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "flowx-config-docs")
        self.assertEqual(manifest["version"], "0.1.0")
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("apps", manifest)
        self.assertNotIn("hooks", manifest)

    def test_template_contains_all_three_fixed_sections_and_headers(self):
        template = (PLUGIN_ROOT / "templates" / "flowx-config-docs.md").read_text(
            encoding="utf-8"
        )
        for title in ("## 数据字典", "## 菜单权限", "## 国际化"):
            self.assertIn(title, template)
        self.assertIn("操作 | 字典编码 | 字典key | 字典值 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语", template)
        self.assertIn("一级菜单 | 二级菜单 | 按钮菜单 or 按钮 | 菜单编码 | 路由", template)
        self.assertIn("操作类型 | 一级key | 二级key | 中文 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语", template)
        self.assertEqual(
            re.findall(r"^## (.+)$", template, re.MULTILINE),
            ["数据字典", "菜单权限", "国际化", "翻译说明"],
        )
        self.assertEqual(
            re.findall(r"\{\{(.*?)\}\}", template),
            ["DATABASE", "SCOPE", "GENERATED_AT", "QUERY_STATUS",
             "DICTIONARY_NOTES", "DICTIONARY_TABLE", "MENUS_NOTES",
             "MENUS_TABLE", "I18N_NOTES", "I18N_TABLE", "TRANSLATION_SUMMARY"],
        )
        headers = [line for line in template.splitlines() if line.startswith("| ")]
        self.assertEqual(len(headers), 3)
        self.assertEqual([len(line.split("|")) - 2 for line in headers], [9, 14, 9])
        self.assertEqual(
            headers[1],
            "| 一级菜单 | 二级菜单 | 按钮菜单 or 按钮 | 菜单编码 | 路由 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语 | 排序 | 授权标识 | 授权资源 | 操作类型 |",
        )

    def test_marketplace_keeps_existing_plugin_and_adds_new_plugin(self):
        marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )
        names = [entry["name"] for entry in marketplace["plugins"]]
        self.assertIn("apifox-api-testing", names)
        self.assertIn("flowx-config-docs", names)
        self.assertEqual(names.count("flowx-config-docs"), 1)
        self.assertEqual(marketplace["plugins"][-1], {
            "name": "flowx-config-docs",
            "source": {"source": "local", "path": "./plugins/flowx-config-docs"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Developer Tools",
        })


class FlowxConfigSkillTests(unittest.TestCase):
    def test_skill_mentions_fixed_outputs_and_runtime_safety(self):
        skill = (PLUGIN_ROOT / "skills" / "flowx-config-docs" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        for phrase in (
            "数据库",
            "配置需求",
            "数据字典",
            "菜单权限",
            "国际化",
            "英文",
            "日文",
            "韩文",
            "西班牙语",
            "葡萄牙语",
            "只读",
            "候选",
            "未查询到匹配配置",
            "占位符",
            "flowx-config-",
            "不调用",
        ):
            self.assertIn(phrase, skill)

    def test_reference_files_exist(self):
        reference_root = PLUGIN_ROOT / "skills" / "flowx-config-docs" / "references"
        for name in ("dbhub-workflow.md", "field-mapping.md", "translation-rules.md"):
            self.assertTrue((reference_root / name).is_file())


if __name__ == "__main__":
    unittest.main()
