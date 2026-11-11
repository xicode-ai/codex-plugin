import unittest
import test_outline_obsidian_sync as support
from test_outline_comments import comment


class InferenceTests(unittest.TestCase):
    def plan(self, markdown, text, native=None):
        from comment_anchors import infer_placement
        return infer_placement(markdown, text, native=native)

    def test_numbered_acceptance_criteria_beats_incidental_mention(self):
        md = '# 方案\n\n背景提到 AC02，但没有验收细节。\n\n## 验收标准\n\n* **AC02**：批查保序，正常地址与异常场景逐项回归。\n\n## 其他\n\n其他内容。\n'
        p = self.plan(md, '【复评｜功能兼容】对应 AC02。补齐正常地址、异常场景和批查保序的回归矩阵。')
        self.assertEqual(p['kind'], 'inferred')
        self.assertIn('**AC02**', md[p['start']:p['end']])
        self.assertEqual(p['section'], '验收标准')

    def test_explicit_quote_and_duplicate_context_choose_best(self):
        md = '# 需求\n\n## 存储\n\n需要隔离。\n\n## 地址线程池\n\n需要隔离。\n\n地址 worker 和 queue 应有界。\n'
        p = self.plan(md, '地址线程池这一节中的“需要隔离”请补充 worker 和 queue 的容量。')
        self.assertEqual(md[p['start']:p['end']], '需要隔离。')
        self.assertEqual(p['section'], '地址线程池')

    def test_ambiguous_or_unrelated_comment_always_selects_best_safe_position(self):
        for text in ['请进一步说明', '宇宙飞船的燃料问题']:
            p = self.plan('# 标题\n\n第一段内容。\n\n第二段内容。\n', text)
            self.assertEqual(p['kind'], 'inferred')
            self.assertLess(p['start'], p['end'])

    def test_approval_is_document_level_but_specific_agreement_is_anchored(self):
        md='# 需求\n\n## 超时策略\n\n增加连接超时配置。\n'
        self.assertEqual(self.plan(md, '已同意开发')['kind'], 'document')
        self.assertEqual(self.plan(md, '同意增加连接超时配置')['kind'], 'inferred')

    def test_native_position_has_priority(self):
        p=self.plan('原生位置。\n\n其他位置。', '其他位置', native=(0,5))
        self.assertEqual(p['kind'], 'outline')
        self.assertEqual((p['start'],p['end']), (0,5))

    def test_reference_range_and_related_sections(self):
        md='# 需求\n\n## 功能需求\n\n* **R01**：地址批查使用专用线程池。\n* **R02**：队列容量有界。\n\n## 验收\n\n* **AC01**：压测验证隔离效果。\n'
        p=self.plan(md, '对应 R01—R02 和 AC01，地址批查改用专用线程池并验证容量。')
        self.assertEqual(p['kind'],'inferred')
        self.assertIn('R01',md[p['start']:p['end']])
        self.assertIn('验收',p['related_sections'])

    def test_code_html_tables_and_frontmatter_are_not_injected(self):
        md='---\ntitle: target\n---\n\n# 安全位置\n\n```\ntarget\n```\n\n    target\n\n<div>target</div>\n\n| target |\n| --- |\n'
        p=self.plan(md,'target')
        self.assertEqual(md[p['start']:p['end']], '安全位置')


class CachedReannotationTests(unittest.TestCase):
    setUp=support.SyncTests.setUp
    run_sync=support.SyncTests.run_sync
    state=support.SyncTests.state
    note=support.SyncTests.note

    def test_cache_reannotation_preserves_body_and_local_edits_without_network(self):
        from reannotate_comments import reannotate
        self.client.docs['d1']['text']='# 需求\n\n## 验收\n\n* **AC02**：地址批查结果保持输入顺序。\n'
        self.client.comments=[comment(text='对应 AC02，请补齐地址批查保序回归。')]
        self.run_sync()
        note=self.note('d1')
        result=reannotate(self.root)
        self.assertEqual(result['errors'],[])
        self.assertIn('推断定位',note.read_text())
        before=note.stat().st_mtime_ns
        self.assertEqual(reannotate(self.root)['saved'],0)
        self.assertEqual(before,note.stat().st_mtime_ns)
        note.write_text('用户手改')
        self.assertEqual(reannotate(self.root)['conflicts'],1)
        self.assertEqual(note.read_text(),'用户手改')

    def test_migrates_legacy_footer_without_changing_source_body_and_dry_run_is_read_only(self):
        import json,hashlib
        from comments import native_id
        from reannotate_comments import reannotate,strip_owned
        self.client.docs['d1']['text']='# 需求\n\n## 验收\n\n* **AC02**：地址批查保持顺序。\n'
        self.client.comments=[comment(text='对应 AC02，请补齐地址回归。')]
        self.run_sync();state=self.state();doc=state['documents']['d1'];cid=native_id('c1');base=self.client.docs['d1']['text']
        legacy=base+'\n\n## 文档评论（无原文定位）\n\n\u200b<!--c:'+cid+'-->全篇评论或原文位置未定位<!--/c:'+cid+'-->\n<!--co:'+cid+' by:张三 status:open quote:"全篇评论或原文位置未定位"\n张三: 对应 AC02，请补齐地址回归。\n-->\n'
        self.note('d1').write_text(legacy);doc.update(sha256=hashlib.sha256(legacy.encode()).hexdigest(),comment_render_version=2,unmapped_comments=['c1']);doc.pop('comment_placements',None)
        sp=self.root/'.outline-sync/state.json';sp.write_text(json.dumps(state));before=sp.read_bytes()
        preview=reannotate(self.root,dry_run=True)
        self.assertEqual(preview['saved'],1);self.assertEqual(sp.read_bytes(),before);self.assertEqual(self.note('d1').read_text(),legacy)
        result=reannotate(self.root);self.assertEqual(result['errors'],[])
        text=self.note('d1').read_text();clean,_=strip_owned(text,{'c1':[]},set())
        self.assertEqual(clean,base);self.assertIn('推断定位',text);self.assertNotIn('全篇评论或原文位置未定位',text)

    def test_corrupt_snapshot_is_not_used_to_rewrite_note(self):
        from reannotate_comments import reannotate
        self.client.comments=[comment()];self.run_sync()
        before=self.note('d1').read_bytes()
        snapshot=self.root/self.state()['documents']['d1']['comments_snapshot'];snapshot.write_text('[]')
        result=reannotate(self.root)
        self.assertTrue(result['errors'])
        self.assertEqual(self.note('d1').read_bytes(),before)

    def test_multiple_threads_on_same_paragraph_survive_repeat_without_source_changes(self):
        from reannotate_comments import reannotate
        self.client.docs['d1']['text']='# 方案\n\n## 地址线程池\n\n地址任务使用专用线程池，队列有界。\n'
        self.client.comments=[comment(text='专用线程池容量是多少？'),comment('c2',text='请补充队列有界的拒绝策略。')]
        self.run_sync();before=self.note('d1').read_bytes()
        result=reannotate(self.root,dry_run=True)
        self.assertEqual(result['errors'],[])
        self.assertEqual(result['saved'],0)
        self.assertEqual(before,self.note('d1').read_bytes())
