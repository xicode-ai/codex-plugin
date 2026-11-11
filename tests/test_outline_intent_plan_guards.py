import copy
import hashlib
import json
import re
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools/outline_intent"))
import plan_engine as engine
from test_outline_intent_runtime import (
    FakeClient,
    VALID_INTENT,
    alignment,
    RICH_COMMENT,
)


class PlanGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "docs/intent.md").write_text("old local document\n")
        self.client = FakeClient()
        self.client.base_url = "https://outline.example"

    def plan(self):
        return {
            "plan_id": "synthetic-1",
            "purpose": "revise",
            "capabilities": {
                "last_revision": True,
                "evidence": "fake server enforces lastRevision",
            },
            "alignment": alignment(),
            "source_files": [{"path": "docs/intent.md"}],
            "local_changes": [{"path": "docs/intent.md", "content": VALID_INTENT}],
            "remote_actions": [
                {
                    "type": "document.update",
                    "document_id": "d1",
                    "source_path": "docs/intent.md",
                    "text": VALID_INTENT,
                    "full_replacement_acknowledged": True,
                },
                {
                    "type": "comment.reply",
                    "document_id": "d1",
                    "parent_comment_id": "c1",
                    "data": copy.deepcopy(RICH_COMMENT),
                },
            ],
        }

    def mutations(self):
        return [
            x
            for x in self.client.calls
            if x[0] in ("documents.update", "documents.create", "comments.create")
        ]

    def test_exact_approval_digest_and_all_hashes_precede_writes(self):
        prepared = engine.prepare_plan(self.plan(), self.root, self.client)
        self.assertEqual(
            prepared["local_changes"][0]["expected_sha256"],
            hashlib.sha256(b"old local document\n").hexdigest(),
        )
        with self.assertRaises(engine.OutlineIntentError):
            engine.apply_plan(prepared, "wrong", self.root, self.client)
        (self.root / "docs/intent.md").write_text("concurrent local change")
        with self.assertRaises(engine.OutlineIntentError):
            engine.apply_plan(
                prepared, engine.content_digest(prepared), self.root, self.client
            )
        self.assertFalse(self.mutations())

    def test_comment_change_blocks_even_local_edits(self):
        prepared = engine.prepare_plan(self.plan(), self.root, self.client)
        self.client.comments[0]["resolvedAt"] = "changed"
        with self.assertRaises(engine.OutlineIntentError):
            engine.apply_plan(
                prepared, engine.content_digest(prepared), self.root, self.client
            )
        self.assertEqual(
            (self.root / "docs/intent.md").read_text(), "old local document\n"
        )

    def test_invalid_upload_cannot_bypass_prepare(self):
        raw = self.plan()
        raw["remote_actions"][0]["text"] = "scaffold"
        with self.assertRaises(engine.OutlineIntentError):
            engine.prepare_plan(raw, self.root, self.client)

    def test_scope_product_path_symlink_and_missing_revision_capability_block(self):
        for path in ("src/Main.java", "../outside.md", "/tmp/outside.md"):
            raw = self.plan()
            raw["local_changes"][0]["path"] = path
            with self.assertRaises(engine.OutlineIntentError):
                engine.prepare_plan(raw, self.root, self.client)
        (self.root / "docs/link.md").symlink_to(self.root / "docs/intent.md")
        raw = self.plan()
        raw["local_changes"][0]["path"] = "docs/link.md"
        with self.assertRaises(engine.OutlineIntentError):
            engine.prepare_plan(raw, self.root, self.client)
        raw = self.plan()
        raw["purpose"] = "review"
        with self.assertRaises(engine.OutlineIntentError):
            engine.prepare_plan(raw, self.root, self.client)
        raw = self.plan()
        raw.pop("capabilities")
        with self.assertRaises(engine.OutlineIntentError):
            engine.prepare_plan(raw, self.root, self.client)

    def test_completed_plan_replay_has_no_duplicate_reply_or_writes(self):
        prepared = engine.prepare_plan(self.plan(), self.root, self.client)
        digest = engine.content_digest(prepared)
        journal = self.root / "journal.json"
        self.assertTrue(
            engine.apply_plan(
                prepared, digest, self.root, self.client, journal_path=journal
            )["complete"]
        )
        self.assertTrue(
            engine.apply_plan(
                prepared, digest, self.root, self.client, journal_path=journal
            )["complete"]
        )
        self.assertEqual(len(self.mutations()), 2)
        self.assertEqual(self.client.comments[-1]["parentCommentId"], "c1")
        self.assertTrue(list((self.root / ".outline-intent-backups").rglob("*.bak")))

    def test_committed_reply_timeout_reconciles_without_duplicate(self):
        original = self.client.call
        fired = []

        def call(method, params):
            result = original(method, params)
            if method == "comments.create" and not fired:
                fired.append(True)
                raise RuntimeError("timeout after commit")
            return result

        self.client.call = call
        prepared = engine.prepare_plan(self.plan(), self.root, self.client)
        digest = engine.content_digest(prepared)
        journal = self.root / "journal.json"
        with self.assertRaises(engine.OutlineIntentError):
            engine.apply_plan(
                prepared, digest, self.root, self.client, journal_path=journal
            )
        self.assertEqual(json.loads(journal.read_text())["status"], "partial_failure")
        self.assertTrue(
            engine.apply_plan(
                prepared, digest, self.root, self.client, journal_path=journal
            )["complete"]
        )
        self.assertEqual(len(self.mutations()), 2)

    def test_foreign_journal_is_not_overwritten_on_recovery_error(self):
        plan = {
            "schema_version": 1,
            "repository_root": str(self.root.resolve()),
            "plan_id": "journal-test",
            "purpose": "review",
            "source_files": [],
            "local_changes": [],
            "remote_actions": [],
            "remote_guards": {},
            "destination_guards": {},
        }
        plan["digest"] = engine.content_digest(plan)
        journal = self.root / "foreign.json"
        original = '{"digest":"other-plan","important":"preserve"}'
        journal.write_text(original)
        with self.assertRaises(engine.OutlineIntentError):
            engine.apply_plan(
                plan, plan["digest"], self.root, self.client, journal_path=journal
            )
        self.assertEqual(journal.read_text(), original)

    def test_valid_but_divergent_remote_intent_needs_explicit_presentation_mapping(
        self,
    ):
        raw = self.plan()
        changed = re.sub(
            r"(## 目标 / Goal\n)[^\n]+",
            r"\1A different concrete outcome.",
            VALID_INTENT,
            count=1,
        )
        self.assertNotEqual(changed, VALID_INTENT)
        raw["remote_actions"][0]["text"] = changed
        with self.assertRaises(engine.OutlineIntentError):
            engine.prepare_plan(raw, self.root, self.client)

    def test_verified_journal_flags_cannot_replace_remote_readback(self):
        prepared = engine.prepare_plan(self.plan(), self.root, self.client)
        journal = self.root / "forged.json"
        journal.write_text(
            json.dumps(
                {
                    "digest": prepared["digest"],
                    "status": "complete",
                    "local": {},
                    "actions": {
                        "0": {"status": "verified"},
                        "1": {"status": "verified"},
                    },
                    "remote_current": prepared["remote_guards"],
                }
            )
        )
        with self.assertRaises(engine.OutlineIntentError):
            engine.apply_plan(
                prepared,
                prepared["digest"],
                self.root,
                self.client,
                journal_path=journal,
            )

    def test_source_only_change_during_local_write_blocks_remote_actions(self):
        source = self.root / "docs/design.md"
        source.write_text("reviewed design")
        raw = self.plan()
        raw["source_files"].append({"path": "docs/design.md"})
        prepared = engine.prepare_plan(raw, self.root, self.client)
        original = engine._atomic

        def write(path, data):
            original(path, data)
            if Path(path).resolve() == (self.root / "docs/intent.md").resolve():
                source.write_text("concurrent design change")

        with patch.object(engine, "_atomic", write), self.assertRaises(
            engine.OutlineIntentError
        ):
            engine.apply_plan(prepared, prepared["digest"], self.root, self.client)
        self.assertFalse(self.mutations())

    def test_existing_anchor_changes_are_not_silently_successful(self):
        prepared = engine.prepare_plan(self.plan(), self.root, self.client)
        original = self.client.call

        def call(method, params):
            result = original(method, params)
            if method == "documents.update":
                self.client.comments[0]["anchorText"] = "lost old anchor"
            return result

        self.client.call = call
        with self.assertRaises(engine.OutlineIntentError):
            engine.apply_plan(prepared, prepared["digest"], self.root, self.client)

    def test_nested_comment_api_shape_is_supported(self):
        raw = self.plan()
        raw["local_changes"] = []
        raw["remote_actions"] = raw["remote_actions"][1:]
        (self.root / "docs/intent.md").write_text(VALID_INTENT)
        self.client.docs["d1"]["text"] = VALID_INTENT
        raw["comment_mirrors"] = [{"document_id": "d1", "path": "docs/intent.md"}]
        original = self.client.call

        def call(method, params):
            result = original(method, params)
            if method == "comments.info":
                return {"data": {"comment": result["data"]}}
            return result

        self.client.call = call
        prepared = engine.prepare_plan(raw, self.root, self.client)
        self.assertTrue(
            engine.apply_plan(prepared, prepared["digest"], self.root, self.client)[
                "complete"
            ]
        )

    def test_nested_collection_create_uses_resolved_user_directory(self):
        (self.root / "docs/intent.md").write_text(VALID_INTENT)
        self.client.base_url = "https://outline.example"
        original = self.client.call

        def call(method, params):
            result = original(method, params)
            if method == "collections.info":
                return {"data": {"collection": result["data"]}}
            return result

        self.client.call = call
        raw = {
            "plan_id": "create-test",
            "purpose": "sync",
            "source_files": [{"path": "docs/intent.md"}],
            "remote_actions": [
                {
                    "type": "document.create",
                    "source_path": "docs/intent.md",
                    "destination_url": "https://outline.example/collection/team-12345678-1234-4234-8234-123456789abc",
                    "title": "Intent",
                    "text": VALID_INTENT,
                }
            ],
        }
        prepared = engine.prepare_plan(raw, self.root, self.client)
        self.assertTrue(
            engine.apply_plan(prepared, prepared["digest"], self.root, self.client)[
                "complete"
            ]
        )
        created = next(
            params for name, params in self.client.calls if name == "documents.create"
        )
        self.assertEqual(
            created["collectionId"], "12345678-1234-4234-8234-123456789abc"
        )

    def test_native_anchor_only_revision_change_is_verified(self):
        raw = self.plan()
        raw["purpose"] = "review"
        raw["local_changes"] = []
        raw["remote_actions"] = [
            {
                "type": "comment.create",
                "document_id": "d1",
                "text": "A precise new review",
                "anchorText": "Original rich text",
            }
        ]
        original = self.client.call

        def call(method, params):
            result = original(method, params)
            if method == "comments.create":
                self.client.docs["d1"]["data"]["content"][0]["content"][0][
                    "marks"
                ].append({"type": "comment", "attrs": {"id": params["id"]}})
                self.client.docs["d1"]["lastRevision"] += 1
            return result

        self.client.call = call
        prepared = engine.prepare_plan(raw, self.root, self.client)
        self.assertTrue(
            engine.apply_plan(prepared, prepared["digest"], self.root, self.client)[
                "complete"
            ]
        )

    def test_plan_cannot_grant_unverified_transport_revision_support(self):
        self.client.verified_capabilities = {}
        with self.assertRaises(engine.OutlineIntentError):
            engine.prepare_plan(self.plan(), self.root, self.client)

    def test_config_origin_or_capability_change_blocks_before_local_writes(self):
        prepared = engine.prepare_plan(self.plan(), self.root, self.client)
        self.client.base_url = "https://other-deployment.example"
        with self.assertRaises(engine.OutlineIntentError):
            engine.apply_plan(prepared, prepared["digest"], self.root, self.client)
        self.assertFalse(self.mutations())
        self.client.base_url = "https://outline.example"
        self.client.verified_capabilities = {}
        with self.assertRaises(engine.OutlineIntentError):
            engine.apply_plan(prepared, prepared["digest"], self.root, self.client)
        self.assertFalse(self.mutations())

    def test_parent_create_timeout_reconciles_same_document_without_recreate(self):
        (self.root / "docs/intent.md").write_text(VALID_INTENT)
        alias = "12345678-1234-4234-8234-123456789abc"
        self.client.docs[alias] = copy.deepcopy(self.client.docs["d1"])
        original = self.client.call
        committed = []

        def call(method, params):
            result = original(method, params)
            if method == "documents.create" and not committed:
                committed.append(True)
                raise RuntimeError("timeout after successful create")
            return result

        self.client.call = call
        raw = {
            "plan_id": "parent-create",
            "purpose": "sync",
            "source_files": [{"path": "docs/intent.md"}],
            "remote_actions": [
                {
                    "type": "document.create",
                    "source_path": "docs/intent.md",
                    "title": "Intent",
                    "text": VALID_INTENT,
                    "destination_url": "https://outline.example/doc/parent-" + alias,
                }
            ],
        }
        prepared = engine.prepare_plan(raw, self.root, self.client)
        journal = self.root / "create-journal.json"
        with self.assertRaises(engine.OutlineIntentError):
            engine.apply_plan(
                prepared,
                prepared["digest"],
                self.root,
                self.client,
                journal_path=journal,
            )
        self.assertTrue(
            engine.apply_plan(
                prepared,
                prepared["digest"],
                self.root,
                self.client,
                journal_path=journal,
            )["complete"]
        )
        creates = [
            params for name, params in self.client.calls if name == "documents.create"
        ]
        self.assertEqual(len(creates), 1)
        self.assertEqual(creates[0]["parentDocumentId"], "d1")
        self.assertEqual(creates[0]["collectionId"], "col1")

    def test_aligned_design_needs_actual_reviewed_path_not_only_a_claim(self):
        raw = self.plan()
        raw["alignment"]["design"] = {"status": "aligned", "evidence": "checked design"}
        with self.assertRaises(engine.OutlineIntentError):
            engine.prepare_plan(raw, self.root, self.client)

    def test_journal_cannot_rebase_approved_guards_onto_concurrent_edits(self):
        prepared = engine.prepare_plan(self.plan(), self.root, self.client)
        self.client.docs["d1"]["text"] = "Concurrent author changes that must survive"
        self.client.docs["d1"]["lastRevision"] += 1
        journal = self.root / "forged-rebase.json"
        journal.write_text(
            json.dumps(
                {
                    "digest": prepared["digest"],
                    "status": "partial_failure",
                    "local": {},
                    "actions": {},
                    "remote_current": {"d1": engine._snapshot(self.client, "d1")},
                }
            )
        )
        with self.assertRaises(engine.OutlineIntentError):
            engine.apply_plan(
                prepared,
                prepared["digest"],
                self.root,
                self.client,
                journal_path=journal,
            )
        self.assertFalse(self.mutations())
        self.assertEqual(
            self.client.docs["d1"]["text"],
            "Concurrent author changes that must survive",
        )


if __name__ == "__main__":
    unittest.main()
