import copy
import json
import unittest
from unittest.mock import patch
import test_outline_obsidian_sync as support
from urllib.parse import unquote


def comment(id='c1', parent=None, text='请核对', **extra):
    return dict(id=id, documentId='d1', parentCommentId=parent,
                createdBy={'name': '张三'}, createdAt='2026-11-10T00:00:00Z',
                updatedAt='2026-11-10T00:00:00Z', resolvedAt=None,
                data={'type': 'doc', 'content': [{'type': 'paragraph', 'content': [
                    {'type': 'text', 'text': text}]}]}, **extra)


class CommentSyncTests(unittest.TestCase):
    setUp = support.SyncTests.setUp
    run_sync = support.SyncTests.run_sync
    state = support.SyncTests.state
    note = support.SyncTests.note
    def test_comment_only_change_reply_delete_and_skip(self):
        self.run_sync()
        self.client.comments = [comment(), comment('c2', 'c1', '已经确认')]
        self.assertEqual(self.run_sync()['saved'], 1)
        note = self.note('d1')
        self.assertIn('已经确认', note.read_text())
        self.assertIn('请核对', note.read_text())
        before = note.stat().st_mtime_ns
        self.assertEqual(self.run_sync()['skipped'], 2)
        self.assertEqual(note.stat().st_mtime_ns, before)
        self.client.comments[1]['data']['content'][0]['content'][0]['text'] = '新回复'
        self.run_sync()
        self.assertIn('新回复', note.read_text())
        self.client.comments = []
        self.run_sync()
        self.assertNotIn('新回复', note.read_text())
        self.assertEqual(unquote(note.read_text()), self.client.docs['d1']['text'].replace('/doc/child-two', '子文档/子文档.md'))

    def test_failed_comments_preserve_existing_view_and_retry(self):
        from outline_client import OutlineError
        self.client.comments = [comment()]
        self.run_sync()
        original = self.note('d1').read_bytes()
        pages = self.client.pages
        def failed(name, **params):
            if name == 'comments.list':
                raise OutlineError('HTTP 503')
            return pages(name, **params)
        with patch.object(self.client, 'pages', side_effect=failed):
            result = self.run_sync()
        self.assertTrue(result['errors'])
        self.assertEqual(self.note('d1').read_bytes(), original)
        self.assertFalse(self.state()['run']['comments_complete'])
        self.assertTrue(self.run_sync()['comments_complete'])

    def test_inline_highlight_replies_original_and_conflict(self):
        self.client.docs['d1']['text'] = '需要核对的内容。\n'
        self.client.docs['d1']['data'] = {'type': 'doc', 'content': [
            {'type': 'paragraph', 'content': [{'type': 'text', 'text': '需要核对',
             'marks': [{'type': 'comment', 'attrs': {'id': 'c1'}}]},
             {'type': 'text', 'text': '的内容。'}]}]}
        self.client.comments = [comment(), comment('c2', 'c1', '确认回复')]
        self.run_sync()
        text = self.note('d1').read_text()
        self.assertIn('<!--c:', text)
        self.assertIn('<!--co:', text)
        self.assertIn('确认回复', text)
        self.assertRegex(text, r'<!--c:[A-Za-z0-9]+-->需要核对<!--/c:[A-Za-z0-9]+-->的内容。')
        original = self.root / self.state()['documents']['d1']['original']
        self.assertEqual(original.read_text(), '需要核对的内容。\n')
        self.note('d1').write_text('本地编辑')
        self.client.comments.append(comment('c3', 'c1', '另一个回复'))
        self.assertEqual(self.run_sync()['conflicts'], 1)
        self.assertEqual(self.note('d1').read_text(), '本地编辑')


class RenderingTests(unittest.TestCase):
    def render(self, markdown, data, rows):
        from comments import render_comments, normalize_comments
        return render_comments(markdown, data, normalize_comments(rows))

    def rich(self, text, marks=None):
        return {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [
            {'type': 'text', 'text': text, 'marks': marks or [{'type': 'comment', 'attrs': {'id': 'c1'}}]}]}]}

    def test_absent_or_ambiguous_native_anchor_uses_best_content_position(self):
        for text,data in [('重复\n\n重复', self.rich('重复')), ('正文', None),
                          ('[链接](https://正文.test)', self.rich('正文'))]:
            rendered,document_ids = self.render(text,data,[comment()])
            self.assertIn('placement:inferred', rendered)
            self.assertIn('推断定位',rendered)
            self.assertEqual(document_ids,[])
        rendered,document_ids=self.render('```\n代码\n```',self.rich('代码'),[comment()])
        self.assertNotIn('<!--c:',rendered.split('## 文档级评论')[0])
        self.assertEqual(document_ids,['c1'])

    def test_native_thread_encodes_multiline_and_comment_terminators(self):
        text,_ = self.render('正文', self.rich('正文'), [
            comment(text='第一行\n--> <script>'), comment('c2', 'c1', '回复')])
        self.assertIn('<!--co:', text)
        self.assertIn('\\n', text)
        self.assertNotIn('--> <script>', text)
        self.assertNotIn('<script>', text)
        self.assertIn('回复', text)
        self.assertNotIn('<mark ', text)

    def test_overlap_has_one_stable_pair_per_thread(self):
        import re
        rich = {'type': 'doc', 'content': [{'type': 'paragraph', 'content': [
            {'type': 'text', 'text': '甲', 'marks': [{'type': 'comment', 'attrs': {'id': 'c1'}}]},
            {'type': 'text', 'text': '乙', 'marks': [{'type': 'comment', 'attrs': {'id': 'c1'}}, {'type': 'comment', 'attrs': {'id': 'c2'}}]}]}]}
        text,unmapped = self.render('甲乙', rich, [comment(),comment('c2')])
        self.assertEqual(unmapped, [])
        opens = re.findall(r'<!--c:([A-Za-z0-9]+)-->', text)
        closes = re.findall(r'<!--/c:([A-Za-z0-9]+)-->', text)
        self.assertEqual(len(opens), 2)
        self.assertEqual(set(opens), set(closes))
        self.assertEqual(text.count('<!--co:'), 2)
        self.assertTrue(text.startswith('\u200b<!--c:'))

    def test_snapshot_drops_user_emails_and_detects_resolution(self):
        from comments import normalize_comments, fingerprint
        rows = [comment()]; rows[0]['createdBy']['email'] = 'private@example.test'
        normalized = normalize_comments(rows)
        self.assertNotIn('email', json.dumps(normalized))
        changed = copy.deepcopy(normalized);changed[0]['resolvedAt'] = '2026-11-10'
        self.assertNotEqual(fingerprint(normalized), fingerprint(changed))

    def test_multiline_comment_body_stays_in_one_html_block(self):
        rendered,_ = self.render('正文', None, [comment(text='第一段\n\n第二段')])
        self.assertNotIn('第一段\n\n第二段', rendered)
        self.assertIn('第二段', rendered)

    def test_unclosed_code_fence_reports_incomplete_instead_of_hiding_threads(self):
        from outline_client import OutlineError
        with self.assertRaises(OutlineError):
            self.render('```python\n代码', self.rich('代码'), [comment()])

    def test_comments_paginate_and_rich_api_preserves_regular_markdown_calls(self):
        from outline_client import OutlineClient
        client = OutlineClient('https://outline.test', 'test-token', page_size=1)
        responses = [{'data': [comment()], 'pagination': {'nextPath': '/next'}},
                     {'data': [comment('c2', 'c1')]}, {'data': []}]
        with patch.object(client, 'call', side_effect=responses):
            rows = client.pages('comments.list')
        self.assertEqual(len(rows), 2)
        with patch.object(client, 'request', return_value=(b'{"data":{"document":{"data":{}}}}', 'application/json')) as request:
            self.assertEqual(client.rich_document('d1'), {'data': {}})
            self.assertEqual(request.call_args.kwargs['api_version'], 3)
        with patch.object(client, 'request', return_value=(b'{"data":{}}', 'application/json')) as request:
            client.call('documents.info', {'id': 'd1'})
            self.assertNotIn('api_version', request.call_args.kwargs)

    def test_indented_code_does_not_gain_inline_anchors(self):
        rendered,unmapped = self.render('    代码\n', self.rich('代码'), [comment()])
        self.assertNotIn('<!--c:', rendered.split('## 文档级评论')[0])
        self.assertEqual(unmapped, ['c1'])

    def test_multiline_thread_retains_markdown_links_and_local_media(self):
        from comments import render_comments, normalize_comments
        row = comment()
        row['data']['content'].append({'type': 'paragraph', 'content': [
            {'type': 'text', 'text': '说明', 'marks': [{'type':'link','attrs':{'href':'https://example.test'}}]},
            {'type': 'image', 'attrs': {'src': '/api/files.get?id=1', 'alt': '截图'}}]})
        rendered,_ = render_comments('正文', None, normalize_comments([row]), lambda url,image: 'local.png' if image else url)
        self.assertIn('https://example.test', rendered)
        self.assertIn('local.png', rendered)
        self.assertIn('\\n', rendered)
        self.assertEqual(rendered.count('<!--co:'),1)

    def test_metadata_cannot_terminate_native_comment(self):
        from comments import render_comments,normalize_comments
        row=comment()
        row['createdAt']='2026-11-10-->\ninvalid'
        row['createdBy']['name']='A: (B)-->\nC'
        rendered,_=render_comments('正文',None,normalize_comments([row]))
        self.assertEqual(rendered.count('-->'),3)
        self.assertNotIn('2026-11-10-->\n',rendered)
