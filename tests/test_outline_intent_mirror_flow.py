"""Approved plan integration and crash recovery for local review mirrors."""

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/outline_intent"))
import plan_engine as engine
from comment_mirror import canonical_body, render_mirror
from test_outline_intent_runtime import FakeClient, VALID_INTENT, alignment, comment


class MirrorFlowTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        (self.root / "docs").mkdir()
        self.reader = self.root / "docs/intent.md"
        self.reader.write_text(VALID_INTENT)
        self.client = FakeClient()
        self.client.docs["d1"]["text"] = VALID_INTENT
        self.journal = self.root / "journal.json"

    def plan(self, update=True):
        actions = []
        if update:
            actions.append(
                dict(
                    type="document.update",
                    document_id="d1",
                    source_path="docs/intent.md",
                    text=VALID_INTENT,
                    full_replacement_acknowledged=True,
                )
            )
        actions.append(
            dict(
                type="comment.reply",
                document_id="d1",
                parent_comment_id="c1",
                text="Actual published response",
            )
        )
        result = dict(
            plan_id="mirror-flow",
            purpose="revise",
            remote_actions=actions,
            capabilities=dict(last_revision=True, evidence="fake deployment verified"),
        )
        if not update:
            result["comment_mirrors"] = [dict(document_id="d1", path="docs/intent.md")]
        return result

    def prepare(self, raw=None):
        return engine.prepare_plan(raw or self.plan(), self.root, self.client)

    def apply(self, plan):
        return engine.apply_plan(
            plan, plan["digest"], self.root, self.client, journal_path=self.journal
        )

    def mutations(self):
        return [
            c
            for c in self.client.calls
            if c[0] in ("documents.update", "documents.create", "comments.create")
        ]

    def test_success_commits_all_originals_and_actual_replies_only_locally(self):
        self.client.comments.append(comment("other", text="Untargeted original"))
        prepared = self.prepare()
        self.assertTrue(self.apply(prepared)["complete"])
        local = self.reader.read_text()
        self.assertIn("Actual published response", local)
        self.assertIn("A threaded reply", local)
        self.assertIn("Untargeted original", local)
        self.assertEqual(canonical_body(local), VALID_INTENT)
        self.assertEqual(self.client.docs["d1"]["text"], VALID_INTENT)
        mapping = prepared["comment_mirrors"][0]
        metadata = json.loads((self.root / mapping["metadata_path"]).read_text())
        self.assertEqual(
            {c["id"] for c in self.client.comments},
            {c["id"] for c in metadata["comments"]},
        )
        self.assertEqual(
            json.loads(self.journal.read_text())["comment_mirror"]["d1"]["status"],
            "verified",
        )
        self.apply(prepared)
        self.assertEqual(2, len(self.mutations()))
        self.assertEqual(local, self.reader.read_text())

    def test_minimal_intent_without_references_mirrors_unlocated_replies(self):
        body = "## 背景\n外部调用存在延迟。\n\n## 需求\n限制连接占用。\n\n## 目标\n提升服务稳定性。\n"
        self.reader.write_text(body)
        self.client.docs["d1"]["text"] = body
        prepared = self.prepare(self.plan(update=False))
        self.assertTrue(self.apply(prepared)["complete"])
        local = self.reader.read_text()
        self.assertIn("待定位评审线程", local)
        self.assertIn("Actual published response", local)
        self.assertEqual(canonical_body(local), body)

    def test_prepare_freezes_mapping_and_stages_preview_without_local_writes(self):
        before = list(self.root.rglob("*"))
        prepared = self.prepare()
        mapping = prepared["comment_mirrors"][0]
        self.assertEqual(mapping["path"], "docs/intent.md")
        self.assertEqual(
            mapping["metadata_path"],
            "docs/research/outline-comments/"
            + hashlib.sha256(b"d1").hexdigest()
            + ".json",
        )
        self.assertEqual(mapping["body"], VALID_INTENT)
        self.assertIn("A threaded reply", mapping["preview"])
        self.assertNotIn("Actual published response", mapping["preview"])
        self.assertEqual(before, list(self.root.rglob("*")))
        self.assertEqual(self.reader.read_text(), VALID_INTENT)
        self.assertFalse(self.mutations())

    def test_comments_only_missing_mapping_blocks(self):
        raw = self.plan(False)
        raw.pop("comment_mirrors")
        with self.assertRaises(engine.OutlineIntentError):
            self.prepare(raw)
        self.assertFalse(self.mutations())

    def test_comments_only_cannot_complete_with_unsynced_local_intent(self):
        self.reader.write_text(
            VALID_INTENT.replace(
                "Operators can see the intent and verify the desired outcome.",
                "A different local goal that has not been uploaded.",
            )
        )
        with self.assertRaises(engine.OutlineIntentError):
            self.prepare(self.plan(False))
        self.assertFalse(self.mutations())

    def test_existing_threads_survive_staged_body_change_when_final_read_fails(self):
        old_view = render_mirror(
            VALID_INTENT, engine._snapshot(self.client, "d1"), document_id="d1"
        )["text"]
        self.reader.write_text(old_view)
        changed = VALID_INTENT.replace(
            "Operators can see the intent and verify the desired outcome.",
            "Operators can inspect the improved intent and its results.",
        )
        self.assertNotEqual(changed, VALID_INTENT)
        raw = self.plan()
        raw["remote_actions"][0]["text"] = changed
        raw["local_changes"] = [{"path": "docs/intent.md", "content": changed}]
        raw["alignment"] = alignment()
        prepared = self.prepare(raw)
        original = engine._finalize_mirrors

        def finalize(*args):
            with patch.object(
                engine._runtime(), "document_snapshot", return_value={"complete": False}
            ):
                return original(*args)

        with patch.object(engine, "_finalize_mirrors", side_effect=finalize):
            with self.assertRaises(engine.OutlineIntentError):
                self.apply(prepared)
        local = self.reader.read_text()
        self.assertEqual(canonical_body(local), changed)
        self.assertIn("A threaded reply", local)
        self.assertNotIn("Actual published response", local)
        self.assertFalse(json.loads(self.journal.read_text())["complete"])
        self.apply(prepared)
        self.assertIn("Actual published response", self.reader.read_text())
        self.assertEqual(2, len(self.mutations()))

    def test_comments_only_explicit_mapping_and_fresh_snapshots_replace_old_threads(
        self,
    ):
        first = self.prepare(self.plan(False))
        self.apply(first)
        self.assertEqual(1, len(self.mutations()))
        self.client.comments[0]["data"] = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Edited original"}],
                }
            ],
        }
        self.client.comments = [c for c in self.client.comments if c["id"] != "c2"]
        self.journal.unlink()
        raw = self.plan(False)
        raw["plan_id"] = "new-review"
        second = self.prepare(raw)
        self.apply(second)
        self.assertIn("Edited original", self.reader.read_text())
        self.assertNotIn("A threaded reply", self.reader.read_text())
        self.assertEqual(VALID_INTENT, canonical_body(self.reader.read_text()))

    def test_sync_roundtrip_strips_only_owned_projection_and_retains_human_notes(self):
        self.reader.write_text(VALID_INTENT + "\n> Human review note\n<!-- human -->\n")
        self.client.docs["d1"]["text"] = self.reader.read_text()
        self.apply(self.prepare(self.plan(False)))
        local = self.reader.read_text()
        raw = dict(
            plan_id="later-sync",
            purpose="sync",
            capabilities=self.plan()["capabilities"],
            remote_actions=[
                dict(
                    type="document.update",
                    document_id="d1",
                    source_path="docs/intent.md",
                    text=local,
                    full_replacement_acknowledged=True,
                )
            ],
        )
        prepared = self.prepare(raw)
        self.assertEqual(prepared["remote_actions"][0]["text"], canonical_body(local))
        self.assertNotIn(
            "Actual published response", prepared["remote_actions"][0]["text"]
        )
        self.assertIn("Human review note", prepared["remote_actions"][0]["text"])
        self.journal.unlink()
        self.apply(prepared)
        self.assertEqual(self.client.docs["d1"]["text"], canonical_body(local))
        self.assertEqual(local, self.reader.read_text())

    def test_committed_reply_timeout_resumes_to_mirror_without_resend(self):
        prepared = self.prepare()
        original = self.client.call
        fired = []

        def call(method, params):
            result = original(method, params)
            if method == "comments.create" and not fired:
                fired.append(True)
                raise RuntimeError("timeout after commit")
            return result

        self.client.call = call
        with self.assertRaises(engine.OutlineIntentError):
            self.apply(prepared)
        self.assertNotIn("Actual published response", self.reader.read_text())
        self.apply(prepared)
        self.assertIn("Actual published response", self.reader.read_text())
        self.assertEqual(2, len(self.mutations()))

    def test_resume_after_each_output_write_uses_recomputed_recipe(self):
        for phase in ("metadata_path", "path"):
            with self.subTest(phase=phase):
                if self.journal.exists():
                    self.journal.unlink()
                self.client = FakeClient()
                self.reader.write_text(VALID_INTENT)
                prepared = self.prepare()
                target = self.root / prepared["comment_mirrors"][0][phase]
                original = engine._atomic
                fired = []

                def atomic(path, data):
                    original(path, data)
                    if Path(path) == target and not fired:
                        fired.append(True)
                        raise RuntimeError("crash after atomic replacement")

                with patch.object(engine, "_atomic", side_effect=atomic):
                    with self.assertRaises(engine.OutlineIntentError):
                        self.apply(prepared)
                state = json.loads(self.journal.read_text())
                self.assertEqual("comment_mirror", state["failed_stage"])
                self.assertFalse(state["complete"])
                count = len(self.mutations())
                self.apply(prepared)
                self.assertEqual(count, len(self.mutations()))
                self.assertIn("Actual published response", self.reader.read_text())

    def test_local_source_and_managed_edits_after_mirror_failure_block_resume(self):
        for edit in ("body", "managed"):
            with self.subTest(edit=edit):
                if self.journal.exists():
                    self.journal.unlink()
                self.client = FakeClient()
                self.reader.write_text(VALID_INTENT)
                prepared = self.prepare()
                original = engine._atomic
                fired = []

                def atomic(path, data):
                    original(path, data)
                    if Path(path) == self.reader and not fired:
                        fired.append(True)
                        raise RuntimeError("reader committed")

                with patch.object(engine, "_atomic", side_effect=atomic):
                    with self.assertRaises(engine.OutlineIntentError):
                        self.apply(prepared)
                text = self.reader.read_text()
                changed = (
                    text.replace("Operators can see", "Humans changed")
                    if edit == "body"
                    else text.replace(
                        "Actual published response", "Forged managed response"
                    )
                )
                self.reader.write_text(changed)
                count = len(self.mutations())
                with self.assertRaises(engine.OutlineIntentError):
                    self.apply(prepared)
                self.assertEqual(changed, self.reader.read_text())
                self.assertEqual(count, len(self.mutations()))

    def test_forged_valid_projection_and_journal_hash_do_not_grant_new_baseline(self):
        prepared = self.prepare()
        self.apply(prepared)
        snap = copy.deepcopy(
            json.loads(self.journal.read_text())["remote_current"]["d1"]
        )
        snap["comments"][0]["data"] = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Forged original"}],
                }
            ],
        }
        forged = render_mirror(VALID_INTENT, snap, document_id="d1")["text"]
        self.reader.write_text(forged)
        state = json.loads(self.journal.read_text())
        state["comment_mirror"]["d1"]["outputs"]["docs/intent.md"]["sha256"] = (
            hashlib.sha256(forged.encode()).hexdigest()
        )
        self.journal.write_text(json.dumps(state))
        with self.assertRaises(engine.OutlineIntentError):
            self.apply(prepared)
        self.assertEqual(forged, self.reader.read_text())
        self.assertEqual(2, len(self.mutations()))

    def test_metadata_edits_before_apply_block_all_writes(self):
        raw = self.plan()
        raw["comment_mirrors"] = [
            dict(
                document_id="d1",
                path="docs/intent.md",
                metadata_path="docs/existing.json",
            )
        ]
        meta = self.root / "docs/existing.json"
        meta.write_text("{}")
        prepared = self.prepare(raw)
        meta.write_text('{"human":true}')
        with self.assertRaises(engine.OutlineIntentError):
            self.apply(prepared)
        self.assertFalse(self.mutations())

    def test_duplicate_escaping_and_conflicting_mapping_paths_block_prepare(self):
        cases = [
            [
                dict(document_id="d1", path="docs/intent.md"),
                dict(document_id="d1", path="docs/intent.md"),
            ],
            [dict(document_id="d1", path="../intent.md")],
            [
                dict(
                    document_id="d1",
                    path="docs/intent.md",
                    metadata_path="docs/intent.md",
                )
            ],
            [
                dict(
                    document_id="d1",
                    path="docs/intent.md",
                    metadata_path="src/data.json",
                )
            ],
            [dict(document_id="d1", path="docs/other.md")],
        ]
        for mappings in cases:
            with self.subTest(mappings=mappings):
                raw = self.plan()
                raw["comment_mirrors"] = mappings
                with self.assertRaises(engine.OutlineIntentError):
                    self.prepare(raw)
        self.assertFalse(self.mutations())

    def test_incomplete_snapshot_and_early_overflow_preserve_reader(self):
        raw = self.plan()
        raw["comment_mirrors"] = [
            dict(document_id="d1", path="docs/intent.md", max_bytes=1)
        ]
        with self.assertRaises(engine.OutlineIntentError):
            self.prepare(raw)
        with patch.object(
            engine._runtime(), "document_snapshot", return_value={"complete": False}
        ):
            with self.assertRaises(engine.OutlineIntentError):
                self.prepare()
        self.assertEqual(VALID_INTENT, self.reader.read_text())
        self.assertFalse(self.mutations())

    def test_known_reply_size_is_checked_during_prepare(self):
        baseline = engine._snapshot(self.client, "d1")
        size = len(
            render_mirror(VALID_INTENT, baseline, document_id="d1")["text"].encode()
        )
        raw = self.plan()
        raw["comment_mirrors"] = [
            dict(document_id="d1", path="docs/intent.md", max_bytes=size)
        ]
        with self.assertRaises(engine.OutlineIntentError):
            self.prepare(raw)
        self.assertFalse(self.mutations())
        self.assertEqual(VALID_INTENT, self.reader.read_text())

    def test_final_overflow_is_partial_without_fake_drafts_and_resumes_safely(self):
        baseline = engine._snapshot(self.client, "d1")
        size = len(
            render_mirror(VALID_INTENT, baseline, document_id="d1")["text"].encode()
        )
        raw = self.plan()
        raw["comment_mirrors"] = [
            dict(document_id="d1", path="docs/intent.md", max_bytes=size)
        ]
        raw["comment_mirrors"][0]["max_bytes"] = size + 3000
        prepared = self.prepare(raw)
        original = self.client.call

        def call(method, params):
            response = original(method, params)
            if method == "comments.create":
                self.client.comments[-1]["createdBy"] = {
                    "name": "Actual author " * 1000
                }
            return response

        self.client.call = call
        with self.assertRaises(engine.OutlineIntentError):
            self.apply(prepared)
        state = json.loads(self.journal.read_text())
        self.assertEqual("comment_mirror", state["failed_stage"])
        self.assertFalse(state["complete"])
        self.assertEqual(VALID_INTENT, self.reader.read_text())
        self.assertEqual(2, len(self.mutations()))
        with self.assertRaises(engine.OutlineIntentError):
            self.apply(prepared)
        self.assertEqual(2, len(self.mutations()))

    def test_final_snapshot_change_blocks_mirror(self):
        prepared = self.prepare()
        original = engine._finalize_mirrors

        def finalize(*args):
            self.client.comments[0]["resolvedAt"] = "concurrent"
            return original(*args)

        with patch.object(engine, "_finalize_mirrors", side_effect=finalize):
            with self.assertRaises(engine.OutlineIntentError):
                self.apply(prepared)
        self.assertEqual(VALID_INTENT, self.reader.read_text())
        self.assertEqual(
            "comment_mirror", json.loads(self.journal.read_text())["failed_stage"]
        )

    def test_managed_tampering_blocks_prepare_and_public_validation(self):
        rendered = render_mirror(
            VALID_INTENT, engine._snapshot(self.client, "d1"), document_id="d1"
        )["text"]
        broken = rendered.replace("A threaded reply", "Human edited managed reply")
        self.reader.write_text(broken)
        with self.assertRaises(engine.OutlineIntentError):
            self.prepare()
        self.assertTrue(engine._runtime().validate_template(rendered).ok)
        self.assertFalse(engine._runtime().validate_template(broken).ok)

    def test_output_write_and_readback_failures_are_partial_and_recoverable(self):
        for mode in ("write", "readback"):
            with self.subTest(mode=mode):
                if self.journal.exists():
                    self.journal.unlink()
                self.client = FakeClient()
                self.reader.write_text(VALID_INTENT)
                prepared = self.prepare()
                target = self.root / prepared["comment_mirrors"][0]["metadata_path"]
                if target.exists():
                    target.unlink()
                    prepared = self.prepare()
                original = engine._atomic

                def atomic(path, data):
                    if Path(path) == target:
                        if mode == "write":
                            raise OSError("disk denied")
                        original(path, b"{}")
                    else:
                        original(path, data)

                with patch.object(engine, "_atomic", side_effect=atomic):
                    with self.assertRaises(engine.OutlineIntentError):
                        self.apply(prepared)
                self.assertEqual(
                    "comment_mirror",
                    json.loads(self.journal.read_text())["failed_stage"],
                )
                self.assertEqual(VALID_INTENT, self.reader.read_text())
                if mode == "readback":
                    # A byte sequence that matches neither baseline nor recipe is a conflict.
                    count = len(self.mutations())
                    with self.assertRaises(engine.OutlineIntentError):
                        self.apply(prepared)
                    self.assertEqual(count, len(self.mutations()))
                    target.unlink()
                self.apply(prepared)
                self.assertIn("Actual published response", self.reader.read_text())

    def test_canonical_binding_freezes_raw_hash_and_alignment_inventory(self):
        raw = self.plan()
        (self.root / "docs/design.md").write_text("reviewed design")
        (self.root / "docs/implement.md").write_text("reviewed implementation")
        raw["alignment"] = {
            "prd": {"paths": ["docs/intent.md"]},
            "design": {"paths": ["docs/design.md"]},
            "implement": {"paths": ["docs/implement.md"]},
        }
        prepared = self.prepare(raw)
        source_paths = {s["path"] for s in prepared["source_files"]}
        self.assertTrue(
            {"docs/intent.md", "docs/design.md", "docs/implement.md"}.issubset(
                source_paths
            )
        )
        self.assertEqual(
            hashlib.sha256(self.reader.read_bytes()).hexdigest(),
            prepared["remote_actions"][0]["source_binding"]["raw_source_sha256"],
        )
        (self.root / "docs/design.md").write_text("concurrent design")
        with self.assertRaises(engine.OutlineIntentError):
            self.apply(prepared)
        self.assertFalse(self.mutations())

    def test_mirror_journal_records_recomputable_recipe(self):
        prepared = self.prepare()
        self.apply(prepared)
        state = json.loads(self.journal.read_text())
        phase = state["comment_mirror"]["d1"]
        self.assertIn("recipe", phase)
        self.assertEqual(
            phase["recipe"]["canonical_body_hash"],
            hashlib.sha256(VALID_INTENT.encode()).hexdigest(),
        )
        self.assertEqual(
            phase["recipe"]["snapshot_fingerprint"],
            state["remote_current"]["d1"]["fingerprint"],
        )
        self.assertEqual(
            set(phase["outputs"]),
            {
                prepared["comment_mirrors"][0]["path"],
                prepared["comment_mirrors"][0]["metadata_path"],
            },
        )

    def test_metadata_edit_during_last_reader_write_cannot_complete(self):
        prepared = self.prepare()
        meta = self.root / prepared["comment_mirrors"][0]["metadata_path"]
        original = engine._atomic

        def atomic(path, data):
            original(path, data)
            if Path(path) == self.reader:
                meta.write_text('{"human":"concurrent metadata edit"}')

        with patch.object(engine, "_atomic", side_effect=atomic):
            with self.assertRaises(engine.OutlineIntentError):
                self.apply(prepared)
        self.assertFalse(json.loads(self.journal.read_text())["complete"])
        self.assertEqual(
            "comment_mirror", json.loads(self.journal.read_text())["failed_stage"]
        )
        self.assertEqual('{"human":"concurrent metadata edit"}', meta.read_text())

    def test_malformed_mapping_values_raise_domain_errors_without_writes(self):
        for mappings in (
            [None],
            ["docs/intent.md"],
            [{"document_id": ["d1"], "path": "docs/intent.md"}],
        ):
            with self.subTest(mappings=mappings):
                raw = self.plan()
                raw["comment_mirrors"] = mappings
                with self.assertRaises(engine.OutlineIntentError):
                    self.prepare(raw)
        self.assertFalse(self.mutations())

    def test_cli_validate_accepts_projection_and_rejects_managed_tampering(self):
        import subprocess

        rendered = render_mirror(
            VALID_INTENT, engine._snapshot(self.client, "d1"), document_id="d1"
        )["text"]
        runtime = Path(engine.__file__).with_name("outline_intent.py")
        for text, expected in (
            (rendered, 0),
            (rendered.replace("A threaded reply", "tampered"), 1),
        ):
            self.reader.write_text(text)
            result = subprocess.run(
                [sys.executable, str(runtime), "validate", "--file", str(self.reader)],
                text=True,
                capture_output=True,
            )
            self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
            self.assertEqual(not bool(expected), json.loads(result.stdout)["ok"])

    def test_incomplete_final_snapshot_keeps_local_body_and_remote_actions_verified(
        self,
    ):
        prepared = self.prepare()
        original = engine._finalize_mirrors

        def finalize(*args):
            with patch.object(
                engine._runtime(), "document_snapshot", return_value={"complete": False}
            ):
                return original(*args)

        with patch.object(engine, "_finalize_mirrors", side_effect=finalize):
            with self.assertRaises(engine.OutlineIntentError):
                self.apply(prepared)
        state = json.loads(self.journal.read_text())
        self.assertEqual("comment_mirror", state["failed_stage"])
        self.assertTrue(
            all(a["status"] == "verified" for a in state["actions"].values())
        )
        self.assertEqual(VALID_INTENT, self.reader.read_text())
        self.apply(prepared)
        self.assertEqual(2, len(self.mutations()))

    def test_crash_before_verified_mirror_journal_is_recovered_without_remote_writes(
        self,
    ):
        prepared = self.prepare()
        original = engine._save
        fired = []

        def save(path, value):
            mirror = value.get("comment_mirror", {}).get("d1", {})
            if mirror.get("status") == "verified" and not fired:
                fired.append(True)
                raise RuntimeError("crash before phase checkpoint")
            original(path, value)

        with patch.object(engine, "_save", side_effect=save):
            with self.assertRaises(engine.OutlineIntentError):
                self.apply(prepared)
        self.assertIn("Actual published response", self.reader.read_text())
        self.apply(prepared)
        self.assertEqual(2, len(self.mutations()))

    def test_explicit_placement_and_limit_are_approved_and_forwarded(self):
        raw = self.plan()
        raw["comment_mirrors"] = [
            dict(
                document_id="d1",
                path="docs/intent.md",
                placements={"c1": {"quote": "Operators can see the intent"}},
                max_bytes=100000,
            )
        ]
        prepared = self.prepare(raw)
        self.apply(prepared)
        self.assertIn(
            '<mark style="background-color: #fff59d;">Operators can see the intent</mark>',
            self.reader.read_text(),
        )
        changed = copy.deepcopy(prepared)
        changed["comment_mirrors"][0]["max_bytes"] += 1
        with self.assertRaises(engine.OutlineIntentError):
            self.apply(changed)
        self.assertEqual(2, len(self.mutations()))


if __name__ == "__main__":
    unittest.main()
