# FlowX Config Docs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a standalone `flowx-config-docs` Codex plugin that queries FlowX configuration through the runtime dbhub MCP, fills missing multilingual values, and writes three fixed Markdown tables.

**Architecture:** The Skill owns runtime dbhub discovery, read-only schema/record queries, candidate confirmation, field normalization, and model-driven translation. A dependency-free Python renderer consumes a normalized JSON payload and a committed Markdown template; a dependency-free validator checks the payload, table contracts, placeholder preservation, and sensitive-data boundaries. The new plugin and marketplace entry are isolated from `apifox-api-testing`.

**Tech Stack:** Codex Skill Markdown, JSON, Markdown templates, Python 3 standard library, `unittest`, Codex plugin manifest and personal marketplace metadata.

## Global Constraints

- Create a new plugin at `plugins/flowx-config-docs`; do not modify `plugins/apifox-api-testing`.
- Use the dbhub MCP already configured in the active Codex session; do not add an external MCP server declaration or store credentials.
- Every generated document contains data dictionary, menu permissions, and internationalization sections in that order.
- Fixed translation columns are English, Japanese, Korean, Spanish, and Portuguese; there is no language-selection input.
- dbhub operations are read-only; no INSERT, UPDATE, DELETE, or cleanup operation is allowed.
- Missing database or configuration scope stops before dbhub calls; ambiguous candidates require user confirmation.
- Only a confirmed candidate whose successful scoped query returns zero rows produces a complete empty section with a `未查询到匹配配置` note; unresolved, ambiguous, and blocked states use distinct notices and are not treated as empty results.
- Existing database translations take precedence; missing natural-language translations are filled while keys, codes, routes, permissions, and placeholders remain unchanged. Each row carries a sanitized `_source` snapshot of its pre-translation fixed fields for validation.
- Output is `flowx-config-{数据库名}-{日期}.md` in the current workspace, with unsafe filename characters normalized only in the filename.
- Do not write passwords, tokens, cookies, connection strings, complete SQL parameters, or complete raw responses to files or reports.
- Local scripts read and write local files only and must not connect to dbhub or external translation services.
- Use Python standard library only; do not add third-party utility dependencies.

---

### Task 1: Add the standalone plugin contract, manifest, and fixed template

**Files:**
- Create: `plugins/flowx-config-docs/.codex-plugin/plugin.json`
- Create: `plugins/flowx-config-docs/templates/flowx-config-docs.md`
- Create: `plugins/flowx-config-docs/README.md`
- Create: `plugins/flowx-config-docs/CHANGELOG.md`
- Create: `tests/test_flowx_config_docs.py`
- Modify: `.agents/plugins/marketplace.json`

**Interfaces:**
- Produces the plugin manifest consumed by Codex and a fixed template consumed by `render_config_doc.py`.
- Produces the test fixture `BASE_PAYLOAD` used by later renderer and validator tests.

- [ ] **Step 1: Write failing artifact tests**

Add the following test skeleton before creating the plugin files:

```python
import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "flowx-config-docs"

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


class FlowxConfigArtifactTests(unittest.TestCase):
    def test_plugin_manifest_is_standalone_and_has_expected_identity(self):
        manifest = json.loads(
            (PLUGIN_ROOT / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["name"], "flowx-config-docs")
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
        self.assertIn("操作 | 字典类型 | 字典key | 字典值 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语", template)
        self.assertIn("一级菜单 | 二级菜单 | 按钮菜单 or 按钮 | 菜单编码 | 路由", template)
        self.assertIn("操作类型 | 一级key | 二级key | 中文 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语", template)

    def test_marketplace_keeps_existing_plugin_and_adds_new_plugin(self):
        marketplace = json.loads(
            (ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
        )
        names = [entry["name"] for entry in marketplace["plugins"]]
        self.assertIn("apifox-api-testing", names)
        self.assertIn("flowx-config-docs", names)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the focused tests and verify they fail for missing artifacts**

Run:

```bash
python3 -m unittest tests.test_flowx_config_docs -v
```

Expected: failures for the missing manifest, template, and marketplace entry. Do not treat the missing implementation as a passing test.

- [ ] **Step 3: Create the plugin manifest**

Write `plugins/flowx-config-docs/.codex-plugin/plugin.json` with this valid manifest:

```json
{
  "name": "flowx-config-docs",
  "version": "0.1.0",
  "description": "Query FlowX dictionaries, menu permissions, and internationalization through dbhub and render fixed multilingual Markdown documentation.",
  "author": {
    "name": "Xicode AI",
    "email": "379323664@qq.com"
  },
  "homepage": "https://github.com/xicode-ai/codex-plugin",
  "repository": "https://github.com/xicode-ai/codex-plugin",
  "license": "MIT",
  "keywords": ["flowx", "dbhub", "data-dictionary", "menu-permission", "i18n", "markdown"],
  "skills": "./skills/",
  "interface": {
    "displayName": "FlowX Config Docs",
    "shortDescription": "Generate fixed multilingual FlowX configuration Markdown",
    "longDescription": "Queries FlowX data dictionaries, menu permissions, and internationalization data through the user's configured dbhub MCP, fills missing translations, and writes a fixed three-section Markdown document.",
    "developerName": "Xicode AI",
    "category": "Developer Tools",
    "capabilities": ["Analyze", "Write"],
    "defaultPrompt": [
      "查询 FlowX 数据字典、菜单权限和国际化配置并生成 Markdown",
      "补齐 FlowX 配置缺失的英文、日文、韩文、西班牙语和葡萄牙语翻译",
      "按固定模板输出 FlowX 配置文档"
    ],
    "brandColor": "#0F766E"
  }
}
```

- [ ] **Step 4: Create the fixed template and plugin documentation**

Create `templates/flowx-config-docs.md` with placeholders only for metadata, notes, tables, and translation summary:

```markdown
# FlowX 配置文档

- 数据库：{{DATABASE}}
- 配置范围：{{SCOPE}}
- 生成日期：{{GENERATED_AT}}
- 查询状态：{{QUERY_STATUS}}

## 数据字典

{{DICTIONARY_NOTES}}

| 操作 | 字典类型 | 字典key | 字典值 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语 |
|---|---|---|---|---|---|---|---|---|
{{DICTIONARY_TABLE}}

## 菜单权限

{{MENUS_NOTES}}

| 一级菜单 | 二级菜单 | 按钮菜单 or 按钮 | 菜单编码 | 路由 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语 | 排序 | 授权标识 | 授权资源 | 操作类型 |
|---|---|---|---|---|---|---|---|---|---|---:|---|---|---|
{{MENUS_TABLE}}

## 国际化

{{I18N_NOTES}}

| 操作类型 | 一级key | 二级key | 中文 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语 |
|---|---|---|---|---|---|---|---|---|
{{I18N_TABLE}}

## 翻译说明

{{TRANSLATION_SUMMARY}}
```

Create `README.md` with the fixed input example, output naming rule, read-only dbhub boundary, ambiguity behavior, three fixed sections, five fixed languages, and the statement that no credentials are stored. Create `CHANGELOG.md` with version `0.1.0` and the standalone plugin feature.

- [ ] **Step 5: Append only the new marketplace entry**

Append this object to `.agents/plugins/marketplace.json` while preserving the existing `apifox-api-testing` object and all root metadata:

```json
{
  "name": "flowx-config-docs",
  "source": {
    "source": "local",
    "path": "./plugins/flowx-config-docs"
  },
  "policy": {
    "installation": "AVAILABLE",
    "authentication": "ON_INSTALL"
  },
  "category": "Developer Tools"
}
```

- [ ] **Step 6: Run the artifact tests and commit the standalone contract**

Run:

```bash
python3 -m unittest tests.test_flowx_config_docs.FlowxConfigArtifactTests -v
```

Expected: PASS for the manifest, template, and marketplace assertions. Commit only the new plugin contract, documentation, marketplace entry, and test file:

```bash
git add plugins/flowx-config-docs tests/test_flowx_config_docs.py
git add .agents/plugins/marketplace.json
git commit -m "feat: scaffold flowx config docs plugin"
```

---

### Task 2: Implement deterministic Markdown rendering

**Files:**
- Create: `plugins/flowx-config-docs/scripts/render_config_doc.py`
- Modify: `tests/test_flowx_config_docs.py`

**Interfaces:**
- Consumes: the normalized JSON payload defined below and `templates/flowx-config-docs.md`.
- Produces: `load_payload(path: Path) -> dict[str, Any]`, `render_config_document(payload: dict[str, Any], template_text: str) -> str`, and a CLI that reads a JSON path plus optional `--template` and `--output` paths.

The normalized payload has this shape:

```json
{
  "metadata": {
    "database": "oms_test",
    "scope": "预约单",
    "generated_at": "2026-09-10",
    "query_status": "complete"
  },
  "dictionary": [{
    "operation": "", "dict_code": "productStatus", "dict_key": "1", "dict_value": "待审核",
    "en": "Pending Review", "ja": "", "ko": "", "es": "", "pt": "",
    "_source": {
      "operation": "", "dict_code": "productStatus", "dict_key": "1", "dict_value": "待审核",
      "en": "Pending Review", "ja": "", "ko": "", "es": "", "pt": ""
    }
  }],
  "menus": [{
    "level1": "商品管理", "level2": "", "button_or_button_menu": "", "menu_code": "product",
    "route": "/product", "en": "Product Management", "ja": "", "ko": "", "es": "", "pt": "",
    "sort": 1, "authorization": "product:list", "resource": "product", "operation_type": "",
    "_source": {
      "level1": "商品管理", "level2": "", "button_or_button_menu": "", "menu_code": "product",
      "route": "/product", "en": "Product Management", "ja": "", "ko": "", "es": "", "pt": "",
      "sort": 1, "authorization": "product:list", "resource": "product", "operation_type": ""
    }
  }],
  "i18n": [{
    "operation_type": "", "level1_key": "inboundOrder", "level2_key": "pendingShelving", "zh": "待上架{unit}",
    "en": "Pending Putaway {unit}", "ja": "", "ko": "", "es": "", "pt": "",
    "_source": {
      "operation_type": "", "level1_key": "inboundOrder", "level2_key": "pendingShelving", "zh": "待上架{unit}",
      "en": "Pending Putaway {unit}", "ja": "", "ko": "", "es": "", "pt": ""
    }
  }],
  "notices": {"dictionary": [], "menus": [], "i18n": []},
  "translation_sources": {"database": 0, "automatic": 0, "pending": 0}
}
```

- [ ] **Step 1: Add failing renderer tests**

Extend `tests/test_flowx_config_docs.py` with the following imports and tests:

```python
import sys

sys.path.insert(0, str(PLUGIN_ROOT / "scripts"))
from render_config_doc import render_config_document  # noqa: E402


class FlowxConfigRendererTests(unittest.TestCase):
    def test_render_has_three_tables_and_preserves_protected_values(self):
        template = (PLUGIN_ROOT / "templates" / "flowx-config-docs.md").read_text(encoding="utf-8")
        report = render_config_document(BASE_PAYLOAD, template)
        self.assertEqual(report.count("| 操作 | 字典类型 | 字典key | 字典值 | 英文 | 日文 | 韩文 | 西班牙语 | 葡萄牙语 |"), 1)
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
```

- [ ] **Step 2: Run renderer tests and verify the import failure**

Run:

```bash
python3 -m unittest tests.test_flowx_config_docs.FlowxConfigRendererTests -v
```

Expected: FAIL with `ModuleNotFoundError` because `render_config_doc.py` does not exist yet.

- [ ] **Step 3: Implement the renderer with fixed field constants**

Create `render_config_doc.py` using Python standard library imports and these exact interfaces:

```text
DICTIONARY_FIELDS = ("operation", "dict_code", "dict_key", "dict_value", "en", "ja", "ko", "es", "pt")
MENU_FIELDS = ("level1", "level2", "button_or_button_menu", "menu_code", "route", "en", "ja", "ko", "es", "pt", "sort", "authorization", "resource", "operation_type")
I18N_FIELDS = ("operation_type", "level1_key", "level2_key", "zh", "en", "ja", "ko", "es", "pt")
load_payload(path: Path) -> dict[str, Any]
render_config_document(payload: dict[str, Any], template_text: str) -> str
main(argv: list[str] | None = None) -> int
```

Implement the body with these rules:

1. `_cell(value)` converts `None` and empty values to an empty string, normalizes CRLF/CR to LF, converts LF to `<br>`, escapes `|` as `\\|`, and escapes backslashes once before pipe escaping.
2. `_table_rows(rows, fields)` emits one row per mapping using the exact field tuple; an empty list emits an empty string so the header and separator remain the complete empty table.
3. `_notes(items)` emits one `> ` line per note and an empty string for no notes.
4. `_translation_summary(value)` emits database, automatic, and pending counts in Chinese and defaults missing counts to zero.
5. Replace exactly the template tokens `DATABASE`, `SCOPE`, `GENERATED_AT`, `QUERY_STATUS`, `DICTIONARY_NOTES`, `DICTIONARY_TABLE`, `MENUS_NOTES`, `MENUS_TABLE`, `I18N_NOTES`, `I18N_TABLE`, and `TRANSLATION_SUMMARY` in one pass so inserted values that resemble tokens remain literal data.
6. Ignore unknown row keys so unrecognized dbhub fields cannot enter the final document.
7. Do not redact normal configuration values; sensitive-data rejection is handled by the validator before rendering. The renderer must never include fields outside the fixed field tuples.

The CLI must load JSON, load the default template from `../templates/flowx-config-docs.md` when `--template` is omitted, render UTF-8 text, write `--output` when provided, otherwise write stdout, and return code `2` for file/JSON errors.

- [ ] **Step 4: Run the renderer tests and full current test suite**

Run:

```bash
python3 -m unittest tests.test_flowx_config_docs.FlowxConfigRendererTests -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all renderer tests and all pre-existing `apifox-api-testing` tests PASS.

- [ ] **Step 5: Commit the renderer**

```bash
git add plugins/flowx-config-docs/scripts/render_config_doc.py tests/test_flowx_config_docs.py
git commit -m "feat: render flowx config markdown"
```

---

### Task 3: Implement normalized-payload validation and security checks

**Files:**
- Create: `plugins/flowx-config-docs/scripts/validate_config_data.py`
- Modify: `tests/test_flowx_config_docs.py`

**Interfaces:**
- Consumes: a normalized JSON payload with the exact top-level sections from Task 2.
- Produces: `validate_config_payload(payload: dict[str, Any]) -> list[str]` and a CLI that returns `0` for valid input, `1` with `VALIDATION_ERROR:` lines for invalid input, and `2` for unreadable or malformed JSON.

- [ ] **Step 1: Add failing validator tests**

Add:

```python
from validate_config_data import validate_config_payload  # noqa: E402


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
```

- [ ] **Step 2: Run validator tests and verify the import failure**

Run:

```bash
python3 -m unittest tests.test_flowx_config_docs.FlowxConfigValidatorTests -v
```

Expected: FAIL with `ModuleNotFoundError` because `validate_config_data.py` does not exist yet.

- [ ] **Step 3: Implement deterministic validation**

Create `validate_config_data.py` with these constants and functions:

```text
REQUIRED_SECTIONS = ("metadata", "dictionary", "menus", "i18n", "notices", "translation_sources")
SECTION_FIELDS = {
    "dictionary": ("operation", "dict_code", "dict_key", "dict_value", "en", "ja", "ko", "es", "pt"),
    "menus": ("level1", "level2", "button_or_button_menu", "menu_code", "route", "en", "ja", "ko", "es", "pt", "sort", "authorization", "resource", "operation_type"),
    "i18n": ("operation_type", "level1_key", "level2_key", "zh", "en", "ja", "ko", "es", "pt"),
}
LANGUAGE_FIELDS = ("en", "ja", "ko", "es", "pt")
PLACEHOLDER_RE = re.compile(r"\{\{[^{}]+\}\}|\$\{[^{}]+\}|\{[A-Za-z_][A-Za-z0-9_.-]*\}|:[A-Za-z_][A-Za-z0-9_.-]*")
SENSITIVE_KEY_RE = re.compile(r"(?:password|passwd|token|cookie|secret|credential|connection|string|dsn)", re.IGNORECASE)
SENSITIVE_VALUE_RE = re.compile(r"(?:Bearer\s+\S+|Basic\s+\S+|Digest\s+\S+|jdbc:[^\s]+|(?:mysql|postgres(?:ql)?|mongodb)://[^\s]+|(?:password|passwd|token|secret|cookie|authorization)\s*[:=]\s*\S+)", re.IGNORECASE)
PROTECTED_FIELDS = {
    "dictionary": ("operation", "dict_code", "dict_key", "dict_value"),
    "menus": ("level1", "level2", "button_or_button_menu", "menu_code", "route", "sort", "authorization", "resource", "operation_type"),
    "i18n": ("operation_type", "level1_key", "level2_key", "zh"),
}
validate_config_payload(payload: dict[str, Any]) -> list[str]
```

Implement validation in this order:

1. Verify the payload is a mapping and all `REQUIRED_SECTIONS` exist.
2. Require `metadata.database`, `metadata.scope`, `metadata.generated_at`, and `metadata.query_status` to be non-empty strings; permit `query_status` values `complete`, `empty`, `ambiguous`, `blocked`, or `unresolved`.
3. Require `dictionary`, `menus`, and `i18n` to be lists; every row must be a mapping containing every field in `SECTION_FIELDS[section]` plus a complete `_source` snapshot of those fixed fields. Empty natural-language and metadata fields are allowed because empty templates and missing translations are valid.
4. Require `notices` to be a mapping with list values for all three sections, and `translation_sources` to be a mapping whose `database`, `automatic`, and `pending` values are non-negative integers.
5. For dictionary rows compare placeholders extracted from `dict_value` with each non-empty language value. For i18n rows compare placeholders extracted from `zh` with each non-empty language value. For menus compare placeholders in the concatenation of the three label fields (`level1`, `level2`, `button_or_button_menu`) with placeholders in each non-empty language value. Report errors containing the word `placeholder` when sets differ.
6. Compare protected fields to `_source` and reject changes; reject changes to non-empty language values already present in `_source`; pending translation counts require a `翻译待人工确认` notice while the target language cell remains empty. Recursively inspect keys and scalar values in the entire payload. Report `sensitive` errors for sensitive key names or values matching the regexes above. Private implementation keys beginning with `_` are not rendered but are still scanned for sensitive values.
7. Do not reject ordinary route strings, permission strings, or language text; only the explicit key/value patterns above are blocked.

The CLI must print one `VALIDATION_ERROR: <error>` per error and never print the offending sensitive value.

- [ ] **Step 4: Run validator and regression tests**

Run:

```bash
python3 -m unittest tests.test_flowx_config_docs.FlowxConfigValidatorTests -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

Expected: all validator tests and all existing tests PASS.

- [ ] **Step 5: Commit the validator**

```bash
git add plugins/flowx-config-docs/scripts/validate_config_data.py tests/test_flowx_config_docs.py
git commit -m "feat: validate flowx config payloads"
```

---

### Task 4: Add the runtime Skill, dbhub references, examples, and package validation

**Files:**
- Create: `plugins/flowx-config-docs/skills/flowx-config-docs/SKILL.md`
- Create: `plugins/flowx-config-docs/skills/flowx-config-docs/references/dbhub-workflow.md`
- Create: `plugins/flowx-config-docs/skills/flowx-config-docs/references/field-mapping.md`
- Create: `plugins/flowx-config-docs/skills/flowx-config-docs/references/translation-rules.md`
- Modify: `tests/test_flowx_config_docs.py`

**Interfaces:**
- Consumes: user input containing database/source identifier and configuration scope; runtime dbhub tools discovered from the active session.
- Produces: the normalized payload from Task 2, then `flowx-config-{database}-{local-date}.md` after validation and rendering.

- [ ] **Step 1: Add failing Skill contract tests**

Add assertions that `SKILL.md` contains the operational contract:

```python
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
```

- [ ] **Step 2: Run Skill tests and verify they fail**

Run:

```bash
python3 -m unittest tests.test_flowx_config_docs.FlowxConfigSkillTests -v
```

Expected: failures for the missing Skill and reference files.

- [ ] **Step 3: Write `SKILL.md` with the runtime workflow**

Use frontmatter:

```markdown
---
name: flowx-config-docs
description: Use when a user requests FlowX data dictionary, menu permission, or internationalization configuration documentation from a specified database through dbhub.
---
```

The body must state all of the following, in executable order:

1. Trigger on FlowX data dictionary, menu permission, internationalization, multilingual configuration, or fixed-template Markdown requests.
2. Require `数据库`/data-source identifier and `配置需求`/business scope. Do not ask for language selection because five target languages are fixed.
3. Before a concrete dbhub call, inspect the current session's actual MCP tool directory and match capabilities for target-source identity, schema/table/field discovery, and read-only query. Do not invent tool names and do not assume remembered aliases exist.
4. Confirm target database/data source using only non-sensitive identity fields, then inspect schema before querying records.
5. Match candidate tables and fields using table names, field names, dictionary code/key/value semantics, menu parent/name/route/permission semantics, i18n keys, language fields, and user business keywords. Do not dump the whole database by default.
6. If one candidate is clear, query only the requested scope. If multiple candidates exist, show candidate names, field summaries, and match reasons and wait for confirmation. If no candidate exists, retain all three empty templates with an unresolved-schema note; use `未查询到匹配配置` only after a confirmed scoped query succeeds with zero rows. If dbhub fails, mark the result blocked and do not call it an empty result.
7. Normalize rows to the exact internal fields: `operation/dict_code/dict_key/dict_value/en/ja/ko/es/pt`, the 14 menu fields, and the 9 i18n fields. Keep missing operation fields empty rather than inventing CRUD actions.
8. Prefer non-empty database translations. Automatically translate only missing natural-language values into English, Japanese, Korean, Spanish, and Portuguese. Never translate keys, codes, dictionary values/keys, routes, sort values, authorization markers, resources, or operation codes.
9. Extract placeholders before translation and require the translated value to preserve the same placeholder set. Preserve `{unit}`, `${name}`, `{{count}}`, `:id`, Markdown, HTML, and line-break semantics. Failed checks leave the target language cell empty, become `翻译待人工确认` notes, and are not silently accepted.
10. Resolve the plugin root from the Skill path, build the normalized JSON payload with pre-translation `_source` snapshots, run `python3 <plugin-root>/scripts/validate_config_data.py payload.json`, and only then run `python3 <plugin-root>/scripts/render_config_doc.py payload.json --output flowx-config-{数据库名}-{日期}.md`. The runtime Skill may create the temporary payload in the current workspace but must not retain credentials or raw responses.
11. Report the generated absolute path and distinguish complete, empty, ambiguous, unresolved, blocked, and translation-pending results. Never claim a blocked query produced an empty configuration document.

Include the exact fixed table headers from the template contract and the recommended input example. State that local scripts never connect to dbhub or translation services.

- [ ] **Step 4: Write the three focused references**

`dbhub-workflow.md` must define dynamic tool discovery, read-only target identity confirmation, schema-first discovery, candidate matching, the exact empty-result condition versus unresolved/ambiguous/blocked classification, and the requirement to avoid sensitive values in evidence.

`field-mapping.md` must define the three internal field tuples, columnar versus language-row i18n aggregation, parent/child menu relation handling, conflict behavior for duplicate language values, and the rule to leave uncertain fields empty with a note.

`translation-rules.md` must define the five fixed languages, database-first precedence, protected fields, placeholder extraction/verification, Markdown/HTML preservation, empty target cells on translation failure, and the `翻译待人工确认` failure note.

Each reference must contain concrete rules and examples, not links to undocumented external conventions.

- [ ] **Step 5: Run Skill, package, and regression validation**

Run:

```bash
python3 -m unittest tests.test_flowx_config_docs.FlowxConfigSkillTests -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 /Users/louis/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/flowx-config-docs
python3 -m py_compile plugins/flowx-config-docs/scripts/render_config_doc.py plugins/flowx-config-docs/scripts/validate_config_data.py
```

Expected: all tests PASS, plugin validation reports a valid manifest, and `py_compile` exits successfully.

- [ ] **Step 6: Commit the Skill and references**

```bash
git add plugins/flowx-config-docs/skills tests/test_flowx_config_docs.py
git commit -m "feat: add flowx config docs skill"
```

---

### Task 5: Run final verification and review the isolated diff

**Files:**
- Verify: `plugins/flowx-config-docs/**`
- Verify: `.agents/plugins/marketplace.json`
- Verify: `tests/test_flowx_config_docs.py`
- Do not modify: `plugins/apifox-api-testing/**`

**Interfaces:**
- Consumes: the completed plugin, normalized payload fixtures, fixed template, and current repository tests.
- Produces: verified standalone plugin artifacts and a clean reviewable diff.

- [ ] **Step 1: Run the full local verification suite**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 /Users/louis/.codex/skills/.system/plugin-creator/scripts/validate_plugin.py plugins/flowx-config-docs
python3 -m py_compile plugins/flowx-config-docs/scripts/render_config_doc.py plugins/flowx-config-docs/scripts/validate_config_data.py
git diff --check HEAD~4..HEAD
```

Expected: all tests and plugin checks pass; `git diff --check` reports no whitespace errors.

- [ ] **Step 2: Exercise the CLI with a safe fixture**

Create a temporary JSON fixture from `BASE_PAYLOAD` outside the repository or use a test-generated temporary file. Run:

```bash
python3 plugins/flowx-config-docs/scripts/validate_config_data.py /private/tmp/flowx-config-payload.json
python3 plugins/flowx-config-docs/scripts/render_config_doc.py /private/tmp/flowx-config-payload.json --output /private/tmp/flowx-config-oms_test-2026-09-10.md
```

Expected: the validator prints `VALID: /private/tmp/flowx-config-payload.json`; the output contains exactly one data dictionary header, one menu permission header, one internationalization header, all fixed language columns, protected route/permission values, and the `{unit}` placeholder.

- [ ] **Step 3: Confirm isolation and repository status**

Run:

```bash
git diff HEAD~4..HEAD --name-only
git status --short
```

Expected: committed paths are limited to the new plugin, its marketplace entry, its tests, and the implementation commits; no file under `plugins/apifox-api-testing` is changed. Preserve unrelated `.codegraph/` and `.serena/` worktree state.

- [ ] **Step 4: Report completion with evidence boundaries**

Report the generated plugin paths and local test/validation results. State that dbhub integration itself is runtime-dependent and was not claimed as live database evidence unless the active session supplied and successfully executed the required read-only tools.
