import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/outline_intent'))
import plan_engine as engine
from test_outline_intent_runtime import FakeClient, VALID_INTENT, alignment, RICH_COMMENT


class PlanGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);(self.root/'docs').mkdir()
        (self.root/'docs/intent.md').write_text('old local document\n')
        self.client=FakeClient()

    def plan(self):
        return {'plan_id':'synthetic-1','purpose':'revise',
                'capabilities':{'last_revision':True,'evidence':'fake server enforces lastRevision'},
                'alignment':alignment(), 'source_files':[{'path':'docs/intent.md'}],
                'local_changes':[{'path':'docs/intent.md','content':VALID_INTENT}],
                'remote_actions':[{'type':'document.update','document_id':'d1','text':VALID_INTENT,'full_replacement_acknowledged':True},
                  {'type':'comment.reply','document_id':'d1','parent_comment_id':'c1','data':copy.deepcopy(RICH_COMMENT)}]}

    def mutations(self):
        return [x for x in self.client.calls if x[0] in ('documents.update','documents.create','comments.create')]

    def test_exact_approval_digest_and_all_hashes_precede_writes(self):
        prepared=engine.prepare_plan(self.plan(),self.root,self.client)
        self.assertEqual(prepared['local_changes'][0]['expected_sha256'],hashlib.sha256(b'old local document\n').hexdigest())
        with self.assertRaises(engine.OutlineIntentError):engine.apply_plan(prepared,'wrong',self.root,self.client)
        (self.root/'docs/intent.md').write_text('concurrent local change')
        with self.assertRaises(engine.OutlineIntentError):engine.apply_plan(prepared,engine.content_digest(prepared),self.root,self.client)
        self.assertFalse(self.mutations())

    def test_comment_change_blocks_even_local_edits(self):
        prepared=engine.prepare_plan(self.plan(),self.root,self.client)
        self.client.comments[0]['resolvedAt']='changed'
        with self.assertRaises(engine.OutlineIntentError):engine.apply_plan(prepared,engine.content_digest(prepared),self.root,self.client)
        self.assertEqual((self.root/'docs/intent.md').read_text(),'old local document\n')

    def test_invalid_upload_cannot_bypass_prepare(self):
        raw=self.plan();raw['remote_actions'][0]['text']='scaffold'
        with self.assertRaises(engine.OutlineIntentError):engine.prepare_plan(raw,self.root,self.client)

    def test_scope_product_path_symlink_and_missing_revision_capability_block(self):
        for path in ('src/Main.java','../outside.md','/tmp/outside.md'):
            raw=self.plan();raw['local_changes'][0]['path']=path
            with self.assertRaises(engine.OutlineIntentError):engine.prepare_plan(raw,self.root,self.client)
        (self.root/'docs/link.md').symlink_to(self.root/'docs/intent.md')
        raw=self.plan();raw['local_changes'][0]['path']='docs/link.md'
        with self.assertRaises(engine.OutlineIntentError):engine.prepare_plan(raw,self.root,self.client)
        raw=self.plan();raw['purpose']='review'
        with self.assertRaises(engine.OutlineIntentError):engine.prepare_plan(raw,self.root,self.client)
        raw=self.plan();raw.pop('capabilities')
        with self.assertRaises(engine.OutlineIntentError):engine.prepare_plan(raw,self.root,self.client)

    def test_completed_plan_replay_has_no_duplicate_reply_or_writes(self):
        prepared=engine.prepare_plan(self.plan(),self.root,self.client);digest=engine.content_digest(prepared);journal=self.root/'journal.json'
        self.assertTrue(engine.apply_plan(prepared,digest,self.root,self.client,journal_path=journal)['complete'])
        self.assertTrue(engine.apply_plan(prepared,digest,self.root,self.client,journal_path=journal)['complete'])
        self.assertEqual(len(self.mutations()),2)
        self.assertEqual(self.client.comments[-1]['parentCommentId'],'c1')
        self.assertTrue(list((self.root/'.outline-intent-backups').rglob('*.bak')))

    def test_committed_reply_timeout_reconciles_without_duplicate(self):
        original=self.client.call
        fired=[]
        def call(method,params):
            result=original(method,params)
            if method=='comments.create' and not fired:
                fired.append(True);raise RuntimeError('timeout after commit')
            return result
        self.client.call=call
        prepared=engine.prepare_plan(self.plan(),self.root,self.client);digest=engine.content_digest(prepared);journal=self.root/'journal.json'
        with self.assertRaises(engine.OutlineIntentError):engine.apply_plan(prepared,digest,self.root,self.client,journal_path=journal)
        self.assertEqual(json.loads(journal.read_text())['status'],'partial_failure')
        self.assertTrue(engine.apply_plan(prepared,digest,self.root,self.client,journal_path=journal)['complete'])
        self.assertEqual(len(self.mutations()),2)


if __name__=='__main__':unittest.main()
