import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'plugins/obsidian-kb-maintenance/scripts/dataview_indexes.py'


class DataviewIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('dataview_indexes', SCRIPT)
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

    def config(self):
        return {'indexes': [{'root': '06-Wiki', 'index': '06-Wiki/知识首页.md',
                             'exclude_folders': ['06-Wiki/备份'],
                             'exclude_files': ['06-Wiki/更新记录.md'],
                             'property_audit': True}]}

    def test_catalog_scoped_before_exclusions_and_no_limit(self):
        plan = self.module.build_plan(self.config())[0]
        catalog = plan['queries'][0]['dql']
        self.assertIn('FROM "06-Wiki" AND -"06-Wiki/备份"', catalog)
        self.assertIn('file.path != "06-Wiki/知识首页.md"', catalog)
        self.assertIn('file.path != "06-Wiki/更新记录.md"', catalog)
        self.assertIn('GROUP BY file.folder', catalog)
        self.assertNotIn('LIMIT', catalog)
        self.assertIn('LIMIT 20', plan['queries'][1]['dql'])

    def test_optional_property_audit_and_generated_query_blocks(self):
        config = self.config()
        plan = self.module.build_plan(config)[0]
        self.assertTrue(any(q['name'] == '属性缺口' for q in plan['queries']))
        self.assertEqual(plan['block'].count('```dataview\n'), len(plan['queries']))
        config['indexes'][0]['property_audit'] = False
        self.assertFalse(any(q['name'] == '属性缺口' for q in self.module.build_plan(config)[0]['queries']))

    def test_invalid_paths_and_duplicate_indexes_rejected(self):
        for root, index in [('..', '../x.md'), ('Notion', 'Notion/index.md'),
                            ('06-Wiki', '05-Skills/index.md'), ('06-Wiki', '/tmp/x.md'),
                            ('06-Wiki', '06-Wiki/../x.md')]:
            with self.subTest(root=root, index=index), self.assertRaises(ValueError):
                self.module.build_plan({'indexes': [{'root': root, 'index': index}]})
        config = self.config()
        config['indexes'].append(dict(config['indexes'][0]))
        with self.assertRaises(ValueError):
            self.module.build_plan(config)

    def test_exclusions_cannot_escape_authorized_root(self):
        config = self.config()
        config['indexes'][0]['exclude_folders'] = ['Notion']
        with self.assertRaises(ValueError):
            self.module.build_plan(config)

    def test_merge_preserves_handwritten_text_and_is_idempotent(self):
        m = self.module
        block = m.build_plan(self.config())[0]['block']
        original = '---\ntitle: 自定义\ncustom: 保留\n---\n# 我的导航\n\n手写链接 [[主题]]\n'
        once = m.merge_block(original, block)
        self.assertTrue(once.startswith(original))
        self.assertEqual(m.merge_block(once, block), once)
        replaced = m.merge_block(once, block.replace('最近文件改动', '近期改动'))
        self.assertTrue(replaced.startswith(original))
        self.assertEqual(replaced.count(m.START), 1)
        self.assertIn('近期改动', replaced)

    def test_malformed_managed_blocks_are_not_silently_overwritten(self):
        m = self.module
        for text in (m.START, m.END, m.END + m.START,
                     m.START + m.START + m.END, m.START + m.END + m.START + m.END):
            with self.subTest(text=text), self.assertRaises(ValueError):
                m.merge_block(text, 'replacement')
        with self.assertRaises(ValueError):
            m.merge_block('# old\n```dataview\nLIST\n```\n', 'replacement')

    def test_seven_roots_example_and_expected_exclusions(self):
        config_path = SCRIPT.parent.parent / 'skills/obsidian-kb-maintenance/references/dataview-indexes.example.json'
        plans = self.module.build_plan(json.loads(config_path.read_text()))
        self.assertEqual(len(plans), 7)
        self.assertEqual({p['root'][:2] for p in plans}, {f'{i:02d}' for i in range(7)})
        source = next(p for p in plans if p['root'] == '03-外部资源')
        self.assertIn('-"03-外部资源/outline"', source['queries'][0]['dql'])
        history = next(p for p in plans if p['root'] == '04-历史记录')
        self.assertIn('-"04-历史记录/修改前备份"', history['queries'][0]['dql'])

    def test_cli_eval_uses_argument_vector_and_never_embeds_content_as_code(self):
        calls = []
        def runner(args, **kwargs):
            calls.append((args, kwargs))
            if args[-1].startswith('code=JSON.stringify(globalThis['):
                data = {'state': 'done', 'result': {'ready': True, 'checks': [{'successful': True}]}}
            else:
                data = {'started': True}
            return subprocess.CompletedProcess(args, 0, '=> ' + json.dumps(data) + '\n', '')
        result = self.module.verify('a vault; $HOME', self.module.build_plan(self.config()), runner=runner)
        self.assertTrue(result['ready'])
        args, kwargs = calls[0]
        self.assertEqual(args[:3], ['obsidian', 'vault=a vault; $HOME', 'eval'])
        self.assertFalse(kwargs.get('shell', False))
        self.assertNotIn('app.vault.modify', args[-1])
        self.assertNotIn('app.vault.read', args[-1])
        self.assertIn('api.query', args[-1])

    def test_failed_eval_and_plugin_readiness_are_explicit(self):
        def failed(args, **kwargs):
            return subprocess.CompletedProcess(args, 1, '', 'failure')
        with self.assertRaises(ValueError):
            self.module.verify('vault', self.module.build_plan(self.config()), runner=failed)
        def missing(args, **kwargs):
            data = {'state': 'done', 'result': {'ready': False, 'reason': 'dataview_unavailable'}} if args[-1].startswith('code=JSON.stringify(globalThis[') else {'started': True}
            return subprocess.CompletedProcess(args, 0, '=> ' + json.dumps(data) + '\n', '')
        result = self.module.verify('vault', self.module.build_plan(self.config()), runner=missing)
        self.assertFalse(result['ready'])

    def test_missing_checks_are_not_accepted_as_success(self):
        def incomplete(args, **kwargs):
            data = {'state': 'done', 'result': {'ready': True, 'checks': []}} if args[-1].startswith('code=JSON.stringify(globalThis[') else {'started': True}
            return subprocess.CompletedProcess(args, 0, '=> ' + json.dumps(data) + '\n', '')
        with self.assertRaises(ValueError):
            self.module.verify('vault', self.module.build_plan(self.config()), runner=incomplete)

    def test_empty_cli_reply_and_async_running_state_are_retried(self):
        polls = [None, {'state': 'running'}, {'state': 'done', 'result': {'ready': True, 'checks': [{'successful': True}]}}]
        calls = []
        def runner(args, **kwargs):
            calls.append(args[-1])
            if args[-1].startswith('code=JSON.stringify(globalThis['):
                data = polls.pop(0) if polls else {'state': 'done', 'result': {'ready': True, 'checks': [{'successful': True}]}}
            else:
                data = {'started': True}
            return subprocess.CompletedProcess(args, 0, '' if data is None else '=> ' + json.dumps(data), '')
        with patch.object(self.module.time, 'sleep'):
            result = self.module.verify('vault', self.module.build_plan(self.config()), runner=runner)
        self.assertTrue(result['ready'])
        self.assertTrue(any('delete globalThis' in code for code in calls))
        self.assertTrue(any('setTimeout' in code and 'if(globalThis[k])return' in code for code in calls))


if __name__ == '__main__':
    unittest.main()
