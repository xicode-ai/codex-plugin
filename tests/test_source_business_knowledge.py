"""Behavioral tests for a read-only, Markdown knowledge-pack export boundary."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from test_source_business_prd import minimal


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'plugins' / 'source-business-knowledge'
SCRIPT = PLUGIN / 'skills/source-business-knowledge/scripts/export_knowledge.py'


class KnowledgePackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source'
        self.source.mkdir()
        (self.source / 'service.py').write_text('def cancel():\n    return "CANCELLED"\n')
        self.payload = minimal(self.source)
        self.payload['run'].update(status='complete', discovery_complete=True, next_actions=[])
        self.payload['coverage'][0]['status'] = 'analyzed'
        self.docs = self.root / 'output'
        (self.docs / 'knowledge/orders').mkdir(parents=True)
        self.payload['documents'] = []
        self.add_document('knowledge/system-overview.md', 'KB-SYSTEM', 'system', None,
                          '系统总览', 'Orders 系统的系统总览仅覆盖取消行为，其他业务未知。')
        self.add_document('knowledge/orders/overview.md', 'KB-ORDERS', 'module', 'KB-SYSTEM',
                          '订单管理', 'Orders 系统的订单管理模块在本次范围只分析取消行为。', module='订单管理')
        self.add_document('knowledge/orders/cancel.md', 'KB-CANCEL', 'topic', 'KB-ORDERS',
                          '订单取消', 'Orders 系统订单管理模块的订单取消操作结束后显示已取消，退款需要另行确认。', module='订单管理')
        self.input = self.root / 'analysis.json'

    def add_document(self, path, identifier, kind, parent, topic, paragraph, module=None):
        knowledge = {'id': identifier, 'kind': kind, 'parent_id': parent, 'system': 'Orders',
                     'topic': topic, 'aliases': []}
        if module is not None:
            knowledge['module'] = module
        self.payload['documents'].append({'path': path, 'claim_ids': ['C1'], 'scenario_ids': ['S1'],
                                           'knowledge': knowledge})
        target = self.docs / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f'# {topic}\n\n## 业务说明\n\n{paragraph}\n\n'
                          '## 处理边界\n\nOrders 系统中的订单取消与退款分别处理，取消结果不能证明退款已经完成。\n')

    def run_export(self, *, check_sources=False):
        self.input.write_text(json.dumps(self.payload, ensure_ascii=False))
        cmd = [sys.executable, str(SCRIPT), str(self.input), '--docs-root', str(self.docs)]
        if check_sources:
            cmd.append('--check-sources')
        return subprocess.run(cmd, capture_output=True, text=True)

    def assert_error(self, expected):
        result = self.run_export()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(expected, result.stderr)
        self.assertEqual(result.stdout, '')

    def test_valid_hierarchy_emits_hashes_and_does_not_write_files(self):
        before = {str(p.relative_to(self.docs)): p.read_bytes() for p in self.docs.rglob('*') if p.is_file()}
        result = self.run_export(check_sources=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        manifest = json.loads(result.stdout)
        self.assertEqual(len(manifest['documents']), 3)
        topic = next(row for row in manifest['documents'] if row['id'] == 'KB-CANCEL')
        self.assertEqual(topic['parent_id'], 'KB-ORDERS')
        self.assertEqual(topic['sha256'], hashlib.sha256((self.docs / topic['path']).read_bytes()).hexdigest())
        self.assertEqual(manifest['checks']['paragraph_semantics'], 'not_automatically_checked')
        self.assertEqual(manifest['checks']['source_snapshots'], 'checked')
        after = {str(p.relative_to(self.docs)): p.read_bytes() for p in self.docs.rglob('*') if p.is_file()}
        self.assertEqual(before, after)

    def test_source_snapshot_check_is_optional_and_reported(self):
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['checks']['source_snapshots'], 'not_checked')

    def test_new_plugin_runs_from_its_own_directory_without_original_plugin(self):
        copied = self.root / 'standalone'
        shutil.copytree(PLUGIN, copied, ignore=shutil.ignore_patterns('__pycache__'))
        self.input.write_text(json.dumps(self.payload, ensure_ascii=False))
        independent_script = copied / 'skills/source-business-knowledge/scripts/export_knowledge.py'
        result = subprocess.run([sys.executable, str(independent_script), str(self.input),
                                 '--docs-root', str(self.docs), '--check-sources'], cwd=copied,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(json.loads(result.stdout)['documents']), 3)

    def test_unlisted_stale_markdown_prevents_export(self):
        (self.docs / 'knowledge/stale.md').write_text('# Old rules\n')
        self.assert_error('unmapped Markdown')

    def test_evidence_and_reports_outside_import_directory_are_not_exported(self):
        (self.docs / 'README.md').write_text('# Analysis navigation\n')
        self.payload['documents'].append({'path': 'README.md', 'claim_ids': [], 'scenario_ids': []})
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(row['path'].startswith('knowledge/') for row in json.loads(result.stdout)['documents']))

    def test_knowledge_metadata_outside_import_directory_is_rejected(self):
        row = self.payload['documents'][0]
        row['path'] = 'system-overview.md'
        (self.docs / row['path']).write_bytes((self.docs / 'knowledge/system-overview.md').read_bytes())
        (self.docs / 'knowledge/system-overview.md').unlink()
        self.assert_error('knowledge/ directory')

    def test_missing_and_malformed_metadata_are_diagnostics(self):
        row = self.payload['documents'][-1]
        for value in (None, [], {}, {'id': []}, {'id': True}):
            with self.subTest(value=value):
                row['knowledge'] = value
                result = self.run_export()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertNotIn('Traceback', result.stderr)

    def test_duplicate_stable_ids_are_rejected(self):
        self.payload['documents'][-1]['knowledge']['id'] = 'KB-ORDERS'
        self.assert_error('duplicate knowledge id')

    def test_topic_needs_module_parent(self):
        self.payload['documents'][-1]['knowledge']['parent_id'] = 'KB-SYSTEM'
        self.assert_error('parent kind')

    def test_missing_parent_is_rejected(self):
        self.payload['documents'][-1]['knowledge']['parent_id'] = 'KB-MISSING'
        self.assert_error('unknown parent')

    def test_topic_needs_module_context(self):
        del self.payload['documents'][-1]['knowledge']['module']
        self.assert_error('module')

    def test_child_system_and_module_must_match_parent(self):
        self.payload['documents'][-1]['knowledge']['system'] = 'OtherSystem'
        self.assert_error('system context differs')
        self.payload['documents'][-1]['knowledge']['system'] = 'Orders'
        self.payload['documents'][-1]['knowledge']['module'] = '退款'
        self.assert_error('module context differs')

    def test_root_must_be_one_system_overview(self):
        self.payload['documents'][0]['knowledge']['kind'] = 'glossary'
        self.assert_error('one system overview')

    def test_draft_analysis_cannot_be_exported_as_ready(self):
        self.payload['run']['status'] = 'draft'
        self.assert_error('complete scoped analysis')

    def test_clean_business_prose_uses_private_document_claim_mapping(self):
        result = self.run_export(check_sources=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        document = self.docs / 'knowledge/orders/cancel.md'
        self.assertNotIn('C1', document.read_text())
        self.assertEqual(json.loads(result.stdout)['documents'][2]['claim_ids'], ['C1'])

    def test_audit_references_are_rejected_even_in_comments(self):
        path = self.docs / 'knowledge/orders/cancel.md'
        path.write_text(path.read_text() + '\n<!-- claim:C1 scenario:S1 -->\n')
        self.assert_error('audit identifiers')

    def test_business_markdown_requires_sections_and_explanatory_paragraphs(self):
        path = self.docs / 'knowledge/orders/cancel.md'
        path.write_text('# 订单取消\n\nOrders 系统的订单取消功能允许用户撤回订单。\n')
        self.assert_error('level-2 sections')
        path.write_text('# 订单取消\n\n## 业务说明\n\n- Orders 系统允许取消订单\n\n## 处理边界\n\n- 退款未知\n')
        self.assert_error('explanatory paragraphs')

    def test_technical_citations_and_code_are_not_business_knowledge(self):
        path = self.docs / 'knowledge/orders/cancel.md'
        original = path.read_text()
        for residue in ('依据 claim:C1，源码 service.py 第 1–2 行。',
                        'OrderController.submit 调用 OrderServiceImpl.submitOrder。',
                        '通过 POST /orders/cancel 取消。',
                        '```python\norder.status = "CANCELLED"\n```',
                        '`cancel(order)`',
                        '采用 Redis 分布式锁与 CAS 更新。',
                        '参考 src/orders/cancel 路径。',
                        'public interface OrderCancellationService { boolean cancel(String id); }',
                        '由 OrderCancellationService 处理。',
                        'C-CANCEL / S-CANCEL / E-CANCEL',
                        '[查看源码](../../src/OrderService.java#L10)',
                        '静态实现；代码版本 663f434；源码文件 OrderService.java。'):
            with self.subTest(residue=residue):
                path.write_text(original + '\n' + residue + '\n')
                result = self.run_export()
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(result.stdout, '')

    def test_frontmatter_with_crlf_and_private_bare_ids_are_rejected(self):
        path = self.docs / 'knowledge/orders/cancel.md'
        original = path.read_text()
        path.write_bytes(('---\r\ntitle: Orders\r\n---\r\n' + original).encode())
        self.assert_error('frontmatter')
        path.write_text(original + '\n结论C1及场景S1。\n')
        self.assert_error('audit identifiers')

    def test_business_system_names_are_allowed_without_implementation_details(self):
        path = self.docs / 'knowledge/orders/cancel.md'
        path.write_text(path.read_text() + '\nOMS 将取消申请交给 WMS，BMS 负责费用处理，PMS 负责运输信息。\n')
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_raw_diagram_source_stays_outside_import_text(self):
        path = self.docs / 'knowledge/orders/cancel.md'
        path.write_text(path.read_text() + '\n```mermaid\nflowchart LR\n A[取消申请] --> B[确认可取消范围]\n```\n')
        self.assert_error('code/diagram source blocks')

    def test_unknown_private_claim_reference_still_fails(self):
        self.payload['documents'][-1]['claim_ids'] = ['MISSING']
        self.assert_error('unknown reference')

    def test_private_claim_mapping_is_required_even_without_public_citations(self):
        self.payload['documents'][-1]['claim_ids'] = []
        self.assert_error('private claim mapping')

    def test_custom_knowledge_id_cannot_leak_into_prose(self):
        self.payload['documents'][-1]['knowledge']['id'] = 'ORDER-CANCEL'
        path = self.docs / 'knowledge/orders/cancel.md'
        path.write_text(path.read_text() + '\n内部编号ORDER-CANCEL。\n')
        self.assert_error('audit identifiers')

    def test_indented_code_cannot_supply_visible_evidence_or_context(self):
        path = self.docs / 'knowledge/orders/cancel.md'
        for indent in ('    ', '\t', '  \t'):
            with self.subTest(indent=repr(indent)):
                path.write_text('# 订单取消\n\n## 业务说明\n\n'
                                + indent + 'Orders 系统的订单取消操作。\n\n## 处理边界\n\n退款需要另行确认。\n')
                self.assert_error('indented code')

    def test_context_in_only_headings_comments_or_frontmatter_is_insufficient(self):
        path = self.docs / 'knowledge/orders/cancel.md'
        path.write_text('# Orders 订单取消\n\n## 业务说明\n\n仅退款申请尚待确认。\n\n'
                        '## 处理结果\n\n销售单据显示已撤回。\n')
        self.assert_error('visible business context')

    def test_symlink_escape_is_rejected(self):
        path = self.docs / 'knowledge/orders/cancel.md'
        original = path.read_text()
        outside = self.root / 'outside.md'
        outside.write_text(original)
        path.unlink()
        path.symlink_to(outside)
        self.assert_error('escapes')

    def test_relative_path_escape_is_rejected(self):
        self.payload['documents'][-1]['path'] = '../outside.md'
        self.assert_error('relative contained path')

    def test_alias_can_supply_the_visible_topic_name(self):
        metadata = self.payload['documents'][-1]['knowledge']
        metadata.update(topic='撤单', aliases=['订单取消'])
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_overviews_need_not_echo_generic_metadata_type_names(self):
        self.payload['documents'][0]['knowledge']['topic'] = '概览索引'
        self.payload['documents'][1]['knowledge']['topic'] = '模块概览'
        result = self.run_export()
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_nonstandard_json_is_rejected_without_echoing_content(self):
        self.input.write_text(json.dumps(self.payload)[:-1] + ', "SECRET_FAKE_VALUE": NaN}')
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.input), '--docs-root', str(self.docs)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertNotIn('SECRET_FAKE_VALUE', result.stdout + result.stderr)


class KnowledgePluginPackagingTests(unittest.TestCase):
    def test_both_plugins_have_separate_marketplace_entries_and_skill_names(self):
        marketplace = json.loads((ROOT / 'marketplace.json').read_text())
        for name in ('source-business-prd', 'source-business-knowledge'):
            entries = [item for item in marketplace['plugins'] if item['name'] == name]
            self.assertEqual(len(entries), 1)
            plugin = ROOT / entries[0]['source']
            self.assertEqual(plugin.resolve(), ROOT / 'plugins' / name)
            for folder in ('.codex-plugin', '.claude-plugin'):
                manifest = json.loads((plugin / folder / 'plugin.json').read_text())
                self.assertEqual(manifest['name'], name)
                self.assertEqual(manifest['version'], entries[0]['version'])
                skill = plugin / manifest['skills'] / name / 'SKILL.md'
                self.assertIn('name: ' + name + '\n', skill.read_text())


if __name__ == '__main__':
    unittest.main()
