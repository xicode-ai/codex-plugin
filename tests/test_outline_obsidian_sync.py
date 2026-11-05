import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from urllib.parse import unquote
from collections import Counter
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from unittest.mock import patch


class CredentialCliTests(unittest.TestCase):
    def test_missing_blank_or_legacy_only_token_fails_without_prompt_or_work(self):
        import sync_outline as cli
        for env in ({}, {'OUTLINE_TOKEN': ''}, {'OUTLINE_TOKEN': '  \n'},
                    {'OUTLINE_API_TOKEN': 'LEGACY-TEST-TOKEN'}):
            with self.subTest(env_keys=list(env)), tempfile.TemporaryDirectory() as folder:
                target = Path(folder) / 'not-created'
                stderr = StringIO()
                with patch.dict('os.environ', env, clear=True), \
                     patch.object(sys, 'argv', ['sync_outline.py', 'sync', '--url', 'https://outline.test', '--target', str(target)]), \
                     patch.object(sys.stdin, 'isatty', return_value=True), \
                     patch('getpass.getpass', side_effect=AssertionError('Must not ask for token')), \
                     patch('builtins.input', side_effect=AssertionError('Must not ask for token')), \
                     patch.object(cli, 'OutlineClient') as client, redirect_stderr(stderr):
                    with self.assertRaises(SystemExit) as caught:
                        cli.main()
                    self.assertEqual(caught.exception.code, 2)
                    client.assert_not_called()
                self.assertIn('OUTLINE_TOKEN', stderr.getvalue())
                self.assertNotIn('LEGACY-TEST-TOKEN', stderr.getvalue())
                self.assertFalse(target.exists())

    def test_probe_reads_outline_token_only_without_printing_secret(self):
        import sync_outline as cli
        stdout = StringIO()
        with patch.dict('os.environ', {'OUTLINE_TOKEN': 'NEW-TEST-TOKEN', 'OUTLINE_API_TOKEN': 'OLD-TEST-TOKEN'}, clear=True), \
             patch.object(sys, 'argv', ['sync_outline.py', 'probe', '--url', 'https://outline.test']), \
             patch.object(cli, 'OutlineClient') as factory, redirect_stdout(stdout):
            factory.return_value.pages.return_value = []
            self.assertEqual(cli.main(), 0)
            factory.assert_called_once_with('https://outline.test', 'NEW-TEST-TOKEN', page_size=25)
        self.assertNotIn('NEW-TEST-TOKEN', stdout.getvalue())
        self.assertNotIn('OLD-TEST-TOKEN', stdout.getvalue())

    def test_sync_uses_outline_token_without_interactive_input(self):
        import sync_outline as cli
        with patch.dict('os.environ', {'OUTLINE_TOKEN': 'NEW-TEST-TOKEN'}, clear=True), \
             patch.object(sys, 'argv', ['sync_outline.py', 'sync', '--url', 'https://outline.test', '--target', '/test/mirror']), \
             patch.object(cli, 'OutlineClient') as factory, \
             patch.object(cli, 'sync', return_value={'errors': []}) as synchronize, \
             redirect_stdout(StringIO()):
            self.assertEqual(cli.main(), 0)
            factory.assert_called_once_with('https://outline.test', 'NEW-TEST-TOKEN', page_size=25)
            self.assertIs(synchronize.call_args.args[0], factory.return_value)

SCRIPTS = Path(__file__).resolve().parents[1] / 'plugins/outline-obsidian-sync/skills/outline-obsidian-sync/scripts'
sys.path.insert(0, str(SCRIPTS))


class FakeClient:
    base_url = 'https://outline.test'

    def __init__(self):
        self.calls = Counter()
        self.collections = [{'id': 'c1', 'name': '研发'}]
        self.tree = [{'id': 'd1', 'title': '父文档', 'children': [
            {'id': 'd2', 'title': '子文档', 'children': []}]}]
        self.docs = {
            'd1': {'id': 'd1', 'title': '父文档', 'updatedAt': '2026-11-01T00:00:00Z',
                   'revision': 1, 'url': '/doc/parent-one', 'text': '# 父文档\n[子文档](/doc/child-two)\n'},
            'd2': {'id': 'd2', 'title': '子文档', 'updatedAt': '2026-11-02T00:00:00Z',
                   'revision': 1, 'url': '/doc/child-two', 'text': '# 子文档\n![图](/api/files.get?id=image)\n'},
        }
        self.docs['d1']['parentDocumentId'] = None
        self.docs['d2']['parentDocumentId'] = 'd1'
        self.fail_image = False

    def pages(self, name, **params):
        self.calls[name] += 1
        return self.collections if name == 'collections.list' else list(self.docs.values())

    def call(self, name, params):
        self.calls[name] += 1
        if name == 'collections.documents':
            return {'data': self.tree}
        return {'data': self.docs[params['id']]}

    def download(self, url):
        self.calls['download'] += 1
        if self.fail_image:
            from outline_client import OutlineError
            raise OutlineError('download HTTP 503')
        return b'\x89PNG\r\n\x1a\nimage', 'image/png'


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name) / 'mirror'
        self.client = FakeClient()

    def run_sync(self):
        from sync_outline import sync
        return sync(self.client, self.root)

    def state(self):
        return json.loads((self.root / '.outline-sync/state.json').read_text())

    def note(self, doc_id):
        return self.root / self.state()['documents'][doc_id]['path']

    def test_hierarchy_images_links_and_incremental_skip(self):
        result = self.run_sync()
        self.assertEqual(result['saved'], 2)
        self.assertEqual(self.note('d2').relative_to(self.root).as_posix(), '研发/父文档/子文档/子文档.md')
        text = unquote(self.note('d2').read_text())
        self.assertNotIn('/api/files.get', text)
        self.assertIn('_附件/', text)
        self.assertIn('子文档/子文档.md', unquote(self.note('d1').read_text()))
        before = {p: p.stat().st_mtime_ns for p in self.root.rglob('*') if p.is_file() and '.outline-sync' not in p.parts}
        second = self.run_sync()
        self.assertEqual(second['skipped'], 2)
        self.assertEqual(self.client.calls['download'], 1)
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in before})

    def test_remote_update_preserves_previous_version(self):
        self.run_sync()
        old = self.note('d1').read_bytes()
        self.client.docs['d1'].update(revision=2, text='# 新版本\n')
        self.assertEqual(self.run_sync()['saved'], 1)
        self.assertIn('新版本', self.note('d1').read_text())
        self.assertTrue(any(p.read_bytes() == old for p in (self.root / '.outline-sync/backups').rglob('*.md')))

    def test_local_edit_conflicts_without_overwrite(self):
        self.run_sync()
        self.note('d1').write_text('本地手动修改')
        self.client.docs['d1'].update(revision=2, text='remote')
        result = self.run_sync()
        self.assertEqual(result['conflicts'], 1)
        self.assertEqual(self.note('d1').read_text(), '本地手动修改')
        self.assertEqual(self.state()['documents']['d1']['version']['revision'], 1)

    def test_failed_image_preserves_text_but_not_media_checkpoint_and_retries(self):
        self.client.fail_image = True
        result = self.run_sync()
        self.assertTrue(result['errors'])
        self.assertFalse(self.state()['documents']['d2']['media_complete'])
        self.assertIn('# 子文档', self.note('d2').read_text())
        self.client.fail_image = False
        self.assertEqual(self.run_sync()['saved'], 1)
        self.assertIn('d2', self.state()['documents'])

    def test_media_filesystem_failure_does_not_discard_document_text(self):
        from unittest.mock import patch
        with patch('media_store.MediaStore.fetch', side_effect=PermissionError('test filesystem error')):
            result = self.run_sync()
        self.assertIn('d2', self.state()['documents'])
        self.assertFalse(self.state()['documents']['d2']['media_complete'])
        self.assertTrue(result['errors'])

    def test_verified_historical_export_reuses_images_without_network(self):
        from sync_outline import sync
        legacy = Path(self.tmp.name) / 'historical'
        (legacy / 'assets').mkdir(parents=True)
        (legacy / 'assets/image.png').write_bytes(b'\x89PNG\r\n\x1a\nold-image')
        (legacy / 'child.md').write_text('---\noutline_id: "d2"\nupdated_at: "2026-11-02T00:00:00Z"\n---\n# 子文档\n![图](assets/image.png)\n')
        self.client.fail_image = True
        result = sync(self.client, self.root, reuse_export=legacy)
        self.assertEqual(result['errors'], [])
        self.assertEqual(self.client.calls['download'], 0)
        self.assertEqual(result['reused_media'], 1)

    def test_changed_historical_body_is_not_used_as_attachment_evidence(self):
        from sync_outline import sync
        legacy = Path(self.tmp.name) / 'historical'
        (legacy / 'assets').mkdir(parents=True)
        (legacy / 'assets/image.png').write_bytes(b'\x89PNG\r\n\x1a\nold-image')
        (legacy / 'child.md').write_text('---\noutline_id: "d2"\nupdated_at: "2026-11-02T00:00:00Z"\n---\n# different document\n![图](assets/image.png)\n')
        result = sync(self.client, self.root, reuse_export=legacy)
        self.assertEqual(self.client.calls['download'], 1)
        self.assertEqual(result['reused_media'], 0)

    def test_discovery_baseline_is_separate_from_attachment_completeness(self):
        self.client.fail_image = True
        self.run_sync()
        self.assertFalse(self.state()['baseline_complete'])
        self.assertTrue(self.state()['discovery_baseline_complete'])
        self.client.tree.append({'id': 'd3', 'title': '新文档', 'children': []})
        self.client.docs['d3'] = dict(self.client.docs['d1'], id='d3', title='新文档', url='/doc/new-three')
        self.run_sync()
        self.assertTrue(self.state()['documents']['d3']['baseline_complete_before_discovery'])

    def test_missing_or_modified_image_repaired(self):
        self.run_sync()
        asset = next((self.root / '_附件').iterdir())
        asset.unlink()
        repaired = self.run_sync()
        self.assertEqual(repaired['saved'] + repaired['skipped'], 2)
        self.assertTrue(asset.exists())
        self.assertEqual(self.client.calls['download'], 2)

    def test_move_rewrites_inbound_links_and_keeps_previous_file(self):
        self.run_sync()
        previous = self.note('d2')
        self.client.tree[0]['children'][0]['title'] = '新子文档'
        result = self.run_sync()
        self.assertTrue(previous.exists())
        self.assertIn('新子文档', unquote(self.note('d1').read_text()))
        self.assertEqual(result['saved'], 2)
        self.assertTrue(result['retained_paths'])

    def test_colliding_names_disambiguated_and_unsafe_names_contained(self):
        self.client.tree = [{'id': 'd1', 'title': '../a', 'children': []},
                            {'id': 'd2', 'title': '../a', 'children': []}]
        self.run_sync()
        a, b = self.note('d1'), self.note('d2')
        self.assertNotEqual(a, b)
        self.assertTrue(a.is_relative_to(self.root))
        self.assertTrue(b.is_relative_to(self.root))

    def test_symlink_and_unmanaged_file_rejected(self):
        self.root.mkdir()
        elsewhere = Path(self.tmp.name) / 'elsewhere'
        elsewhere.mkdir()
        (self.root / '研发').symlink_to(elsewhere, target_is_directory=True)
        result = self.run_sync()
        self.assertTrue(result['errors'])
        self.assertEqual(list(elsewhere.iterdir()), [])

    def test_lock_blocks_concurrent_runs(self):
        self.root.mkdir()
        meta = self.root / '.outline-sync'
        meta.mkdir()
        (meta / 'sync.lock').write_text('existing')
        from outline_client import OutlineError
        with self.assertRaises(OutlineError):
            self.run_sync()

    def test_other_source_cannot_reuse_state(self):
        self.run_sync()
        self.client.base_url = 'https://other.test'
        from outline_client import OutlineError
        with self.assertRaises(OutlineError):
            self.run_sync()

    def test_remote_deletion_keeps_local_file(self):
        self.run_sync()
        old = self.note('d2')
        self.client.tree[0]['children'] = []
        self.client.docs.pop('d2')
        self.assertIn('d2', self.run_sync()['remote_missing'])
        self.assertTrue(old.exists())

    def test_empty_remote_tree_recovered_from_document_parent_metadata(self):
        self.client.tree = []
        result = self.run_sync()
        self.assertEqual(result['saved'], 2)
        self.assertEqual(result['errors'], [])
        self.assertTrue(result['warnings'])
        self.assertEqual(self.note('d2').relative_to(self.root).as_posix(), '研发/父文档/子文档/子文档.md')

    def test_missing_parent_is_reported_without_inventing_a_directory(self):
        self.client.tree = []
        self.client.docs['d2']['parentDocumentId'] = 'unknown-parent'
        result = self.run_sync()
        self.assertEqual(result['saved'], 1)
        self.assertTrue(result['errors'])
        self.assertNotIn('d2', self.state()['documents'])

    def test_metadata_changed_during_fetch_is_not_checkpointed(self):
        self.client.docs['d1'].pop('text')
        old_call = self.client.call
        def changed(name, params):
            if name == 'documents.info':
                return {'data': dict(self.client.docs['d1'], revision=9, text='new')}
            return old_call(name, params)
        self.client.call = changed
        self.assertTrue(self.run_sync()['errors'])
        self.assertNotIn('d1', self.state()['documents'])

    def test_missing_remote_version_does_not_hide_content_changes(self):
        self.client.docs['d1'].pop('revision')
        self.client.docs['d1'].pop('updatedAt')
        self.run_sync()
        self.client.docs['d1']['text'] = '# changed without revision'
        self.run_sync()
        self.assertIn('changed without revision', self.note('d1').read_text())

    def test_interrupted_managed_update_recovers_only_the_journaled_hash(self):
        import hashlib
        self.run_sync()
        self.client.docs['d1'].update(revision=2, text='# remote update\n')
        state = self.state()
        relative = state['documents']['d1']['path']
        body = b'# remote update\n'
        state['pending'][relative] = hashlib.sha256(body).hexdigest()
        (self.root / relative).write_bytes(body)
        (self.root / '.outline-sync/state.json').write_text(json.dumps(state))
        result = self.run_sync()
        self.assertEqual(result['conflicts'], 0)
        self.assertEqual(self.state()['documents']['d1']['version']['revision'], 2)

    def test_metadata_sort_does_not_limit_the_list_to_root_siblings(self):
        original = self.client.pages
        def pages(name, **params):
            if name == 'documents.list':
                self.assertEqual(params.get('sort'), 'createdAt')
            return original(name, **params)
        self.client.pages = pages
        self.run_sync()


class PackagingTests(unittest.TestCase):
    def test_registered_and_both_manifests(self):
        root = SCRIPTS.parents[4]
        entries = json.loads((root / 'marketplace.json').read_text())['plugins']
        entry = next(x for x in entries if x['name'] == 'outline-obsidian-sync')
        for directory in ('.codex-plugin', '.claude-plugin'):
            manifest = json.loads((root / entry['source'] / directory / 'plugin.json').read_text())
            self.assertEqual(manifest['name'], entry['name'])
            self.assertEqual(manifest['version'], entry['version'])




class TransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from threading import Thread
        cls.events = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def respond(self, code, body, mime='application/json', **headers):
                self.send_response(code)
                self.send_header('Content-Type', mime)
                for key, value in headers.items():
                    self.send_header(key, str(value))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))) or b'{}')
                cls.events.append((self.path, self.headers.get('Authorization'), payload))
                if self.path == '/api/collections.list':
                    offset = payload.get('offset', 0)
                    items = [{'id': str(i)} for i in range(offset, min(offset + 2, 5))]
                    self.respond(200, json.dumps({'data': items, 'pagination': {'total': 5}}).encode())
                elif self.path == '/api/attachments.info':
                    if payload['id'] == 'gzip-test':
                        import gzip
                        self.respond(200, gzip.compress(b'{"data":{"id":"gzip-test"}}'), **{'Content-Encoding': 'gzip'})
                        return
                    self.respond(200, json.dumps({'data': {'id': payload['id'], 'url': '/api/attachments.redirect?id=' + payload['id']}}).encode())
                else:
                    self.respond(404, b'{}')

            def do_GET(self):
                cls.events.append((self.path.split('?')[0], self.headers.get('Authorization'), {}))
                if self.path == '/redirect':
                    self.respond(302, b'', Location=cls.external + '/image')
                elif self.path == '/login':
                    self.respond(200, b'<html>login</html>', 'text/html')
                elif self.path.startswith('/api/files.get'):
                    self.respond(401, b'{"message":"Invalid signature"}')
                else:
                    self.respond(200, b'\x89PNG\r\nimage', 'image/png')
        cls.other = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.external = 'http://127.0.0.1:' + str(cls.other.server_port)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)
        cls.threads = [Thread(target=s.serve_forever, daemon=True) for s in (cls.server, cls.other)]
        for thread in cls.threads:
            thread.start()

    @classmethod
    def tearDownClass(cls):
        for server in (cls.server, cls.other):
            server.shutdown()
            server.server_close()

    def setUp(self):
        from outline_client import OutlineClient
        self.events.clear()
        self.client = OutlineClient(self.base, 'TEST-ONLY-TOKEN', allow_http=True, retries=1)

    def test_pagination_uses_actual_page_size(self):
        self.assertEqual(len(self.client.pages('collections.list')), 5)
        self.assertEqual([event[2]['offset'] for event in self.events], [0, 2, 4])

    def test_configurable_page_size_and_validation(self):
        from outline_client import OutlineClient, OutlineError
        client = OutlineClient(self.base, 'TEST-ONLY-TOKEN', allow_http=True, page_size=10)
        self.assertEqual(len(client.pages('collections.list')), 5)
        self.assertTrue(all(event[2]['limit'] == 10 for event in self.events))
        with self.assertRaises(OutlineError):
            OutlineClient(self.base, 'TEST-ONLY-TOKEN', allow_http=True, page_size=0)

    def test_full_page_total_is_not_treated_as_end_of_collection(self):
        from outline_client import OutlineClient
        client = OutlineClient(self.base, 'TEST-ONLY-TOKEN', allow_http=True, page_size=2)
        original = client.call
        def page_total(name, params):
            response = original(name, params)
            response['pagination']['total'] = len(response['data'])
            response['pagination']['nextPath'] = '/api/collections.list?offset=' + str(params['offset'] + 2)
            return response
        client.call = page_total
        self.assertEqual(len(client.pages('collections.list')), 5)

    def test_token_removed_on_cross_origin_redirect(self):
        self.client.download(self.base + '/redirect')
        self.assertEqual(self.events[0][1], 'Bearer TEST-ONLY-TOKEN')
        self.assertIsNone(self.events[1][1])

    def test_external_initial_request_never_receives_token(self):
        self.client.download(self.external + '/image')
        self.assertIsNone(self.events[0][1])

    def test_login_html_rejected(self):
        from outline_client import OutlineError
        with self.assertRaises(OutlineError):
            self.client.download(self.base + '/login')

    def test_gzip_api_responses_are_decoded(self):
        result = self.client.call('attachments.info', {'id': 'gzip-test'})
        self.assertEqual(result['data']['id'], 'gzip-test')

    def test_write_api_not_allowed(self):
        from outline_client import OutlineError
        with self.assertRaises(OutlineError):
            self.client.call('documents.update', {})
        self.assertEqual(self.events, [])

    def test_legacy_expired_signature_is_reported_without_leaking_url(self):
        import base64
        payload = {'key': 'uploads/user/11111111-1111-1111-1111-111111111111/image.png', 'exp': 1}
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')
        from outline_client import OutlineError
        with self.assertRaisesRegex(OutlineError, 'HTTP 401') as caught:
            self.client.download(self.base + '/api/files.get?sig=header.' + encoded + '.signature')
        self.assertNotIn(encoded, str(caught.exception))
        self.assertNotIn('/api/attachments.info', [e[0] for e in self.events])



class MarkdownTests(unittest.TestCase):
    def test_media_prefetch_downloads_in_parallel_and_reuses_cached_bytes(self):
        from media_store import MediaStore
        from threading import Barrier
        barrier = Barrier(2)
        client = FakeClient()
        def download(url):
            barrier.wait(timeout=3)
            return url.encode(), 'image/png'
        client.download = download
        with tempfile.TemporaryDirectory() as directory:
            with MediaStore(client, Path(directory), {}, workers=2) as store:
                store.prefetch(['https://outline.test/a', 'https://outline.test/b'])
                first = store.get('https://outline.test/a')
                second = store.get('https://outline.test/b')
                self.assertTrue((Path(directory) / first['path']).exists())
                self.assertNotEqual(first['path'], second['path'])

    def test_shortcut_and_collapsed_image_references(self):
        from markdown_links import rewrite
        text = '![shortcut]\n![collapsed][]\n[shortcut]: https://host/a.png\n[collapsed]: https://host/b.png\n'
        seen = []
        rewrite(text, lambda url, image: seen.append((url, image)) or url)
        self.assertEqual(seen, [('https://host/a.png', True), ('https://host/b.png', True)])

    def test_unsupported_media_preserves_document_and_reports_gap(self):
        from sync_outline import sync
        with tempfile.TemporaryDirectory() as directory:
            client = FakeClient()
            client.docs['d2']['text'] = '# 子文档\n![blob](blob:unavailable)'
            result = sync(client, directory)
            state = json.loads((Path(directory) / '.outline-sync/state.json').read_text())
            self.assertIn('d2', state['documents'])
            self.assertFalse(state['documents']['d2']['media_complete'])
            self.assertTrue(result['errors'])

    def test_balanced_urls_references_html_and_code(self):
        from markdown_links import rewrite
        text = '![a](https://host/a(b).png)\n![b][x]\n[x]: /image.png\n<img src="/html.png">\n```md\n![skip](/skip.png)\n```\n`![skip](/inline.png)`'
        seen = []
        result = rewrite(text, lambda url, image: seen.append((url, image)) or 'LOCAL' if image else url)
        self.assertEqual([x[0] for x in seen], ['https://host/a(b).png', '/image.png', '/html.png'])
        self.assertIn('![skip](/skip.png)', result)
        self.assertIn('`![skip](/inline.png)`', result)
