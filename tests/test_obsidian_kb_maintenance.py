import importlib.util
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

PATH = Path(__file__).resolve().parents[1] / 'plugins/obsidian-kb-maintenance/scripts/maintenance.py'
spec = importlib.util.spec_from_file_location('maintenance', PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class MaintenanceTests(unittest.TestCase):
    def test_collect_date_is_not_collection_date(self):
        self.assertFalse(m.is_today({'collected_at': '2026-11-01T23:00:00+08:00', 'saved_at': '2026-11-02T09:00:00+08:00'}, '2026-11-02'))
        self.assertTrue(m.is_today({'collected_at': '2026-11-01T17:00:00Z'}, '2026-11-02'))

    def test_first_discovery_requires_previous_baseline(self):
        self.assertFalse(m.is_today({'first_seen_at': '2026-11-02T09:00:00+08:00', 'baseline_complete_before_discovery': False}, '2026-11-02'))
        self.assertTrue(m.is_today({'first_seen_at': '2026-11-02T09:00:00+08:00', 'baseline_complete_before_discovery': True}, '2026-11-02'))

    def test_inventory_excludes_private_and_generated_files(self):
        with TemporaryDirectory() as d:
            root = Path(d)
            for name in ['06-Wiki/a.md', '.obsidian/config.md', '01-进行中/知识库阅读导读/a.md', '04-历史记录/备份/a.md']:
                p = root / name
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text('example')
            self.assertEqual([x['path'] for x in m.inventory(root)], ['06-Wiki/a.md'])

if __name__ == '__main__':
    unittest.main()
