import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from urllib.error import HTTPError, URLError


RUNTIME_DIR = Path(__file__).resolve().parents[1] / "tools" / "outline_intent"
sys.path.insert(0, str(RUNTIME_DIR))


HEADINGS = [
    "目标 / Goal",
    "背景 / Background",
    "需求 / Requirements",
    "非目标 / Out of Scope",
    "验收标准 / Acceptance Criteria",
    "风险与边界情况 / Risks & Edge Cases",
    "发布与回滚说明 / Release / Rollback Notes",
    "参考资料 / References",
]

TEMPLATE = "\n".join(f"## {heading}\n" for heading in HEADINGS)

VALID_INTENT = """# Intent title

## 目标 / Goal
Operators can see the intent and verify the desired outcome.

## 背景 / Background
现状与问题：Current requests are tracked in several places.
为什么需要这个功能：A shared intent reduces conflicting changes.
系统/模块：The Outline intent authoring workflow.
目标用户 / 业务角色：Developers and reviewers.
影响的业务对象 / 范围：The intent document and its approved changes.

## 需求 / Requirements
### 功能需求
- The validator checks required content and document structure.
### 非功能需求
性能：Validation completes for normal project documents.
安全：Secrets never appear in output.
兼容性：The tool uses Python standard library only.
数据 / 权限约束：不适用：本变更不读取业务数据，仅校验本地 Markdown。

## 非目标 / Out of Scope
Changing product behavior is out of scope.

## 验收标准 / Acceptance Criteria
- Given a complete intent, When the validator checks it, Then it reports the document as valid.

## 风险与边界情况 / Risks & Edge Cases
风险点：An incomplete snapshot must block writes.
边界情况：A remote document may change after preparation.
异常流 / 失败场景：Stop when readback does not match the approved plan.
回滚 / 降级约束：Restore a local backup manually; never roll back remote changes automatically.

## 发布与回滚说明 / Release / Rollback Notes
是否需要灰度 / 分批发布：不需要：This is a local authoring utility.
是否有发布时间窗口：No deployment window is required.
失败后的回退方式：Restore the saved local backup and inspect the journal.

## 参考资料 / References
Intent / PRD：This intent document; 不适用：纯技术改造，无产品 PRD。
设计文档：The runtime design.
Work Item / Issue：The implementation work item.
Outline / 现有资料：The user-provided Outline document.
相关仓库 / 模块：The documentation repository.
"""


RICH_DOC = {
    "type": "doc",
    "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "Original rich text", "marks": [{"type": "strong"}]}]},
        {"type": "paragraph", "content": [{"type": "text", "text": "Second paragraph"}]},
    ],
}
RICH_COMMENT = {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Please review"}]}]}


def comment(comment_id="c1", parent_id=None, text="Please review"):
    return {
        "id": comment_id,
        "documentId": "d1",
        "parentCommentId": parent_id,
        "anchorText": "Original rich text",
        "anchorPrefix": "before ",
        "anchorSuffix": " after",
        "data": {"type": "doc", "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]},
        "createdAt": "2026-11-10T00:00:00.000Z",
    }


class FakeClient:
    def __init__(self):
        self.docs = {
            "d1": {
                "id": "d1",
                "title": "Intent",
                "text": "Original rich text\nSecond paragraph",
                "data": copy.deepcopy(RICH_DOC),
                "lastRevision": 7,
                "collectionId": "col1",
                "parentDocumentId": None,
            }
        }
        self.comments = [comment(), comment("c2", "c1", "A threaded reply")]
        self.calls = []
        self.fail_method = None

    def call(self, method, params):
        self.calls.append((method, copy.deepcopy(params)))
        if method == self.fail_method:
            raise RuntimeError("injected mutation failure")
        if method == "documents.info":
            row = self.docs.get(params.get("id"))
            if row is None:
                raise RuntimeError("document not found")
            return {"data": {"document": copy.deepcopy(row)}}
        if method == "comments.list":
            rows = [x for x in self.comments if x["documentId"] == params["documentId"]]
            offset = params.get("offset", 0)
            limit = params.get("limit", 100)
            page = rows[offset:offset + limit]
            return {"data": copy.deepcopy(page), "pagination": {"hasNext": offset + limit < len(rows)}}
        if method == "comments.info":
            row = next((x for x in self.comments if x["id"] == params["id"]), None)
            if row is None:
                raise RuntimeError("comment not found")
            return {"data": copy.deepcopy(row)}
        if method == "documents.update":
            row = self.docs[params["id"]]
            if params.get("lastRevision") != row["lastRevision"]:
                raise RuntimeError("revision conflict")
            if "text" in params:
                row["text"] = params["text"]
            if "data" in params:
                row["data"] = copy.deepcopy(params["data"])
            row["lastRevision"] += 1
            return {"data": {"document": copy.deepcopy(row)}}
        if method == "documents.create":
            row = {
                "id": params["id"],
                "title": params["title"],
                "text": params.get("text", ""),
                "data": copy.deepcopy(params.get("data")),
                "lastRevision": 1,
                "collectionId": params["collectionId"],
                "parentDocumentId": params.get("parentDocumentId"),
            }
            self.docs[row["id"]] = row
            return {"data": {"document": copy.deepcopy(row)}}
        if method == "comments.create":
            row = {
                "id": params["id"],
                "documentId": params["documentId"],
                "parentCommentId": params.get("parentCommentId"),
                "data": copy.deepcopy(params["data"]),
                "anchorText": params.get("anchorText"),
                "anchorPrefix": params.get("anchorPrefix"),
                "anchorSuffix": params.get("anchorSuffix"),
            }
            self.comments.append(row)
            return {"data": copy.deepcopy(row)}
        if method == "collections.info":
            return {"data": {"id": params["id"]}}
        raise AssertionError(f"unexpected method {method}")


def alignment(paths=("docs/intent.md",)):
    return {
        "semantic_judgment": "Manual review checked the plan against each source; hashes only pin inputs.",
        "prd": {"status": "aligned", "evidence": "Scope and acceptance criteria match."},
        "design": {"status": "aligned", "evidence": "The design preserves current document behavior."},
        "implement": {"status": "aligned", "evidence": "The implementation changes only the validator."},
        "artifacts": list(paths),
    }


class OutlineIntentConfigTests(unittest.TestCase):
    def setUp(self):
        import outline_intent
        self.runtime = outline_intent

    def test_environment_resolution_never_uses_legacy_token_or_leaks_token(self):
        config = self.runtime.resolve_config(env={
            "OUTLINE_URL": "https://outline.example",
            "OUTLINE_API_TOKEN": "SECRET-TOKEN",
            "OUTLINE_TOKEN": "LEGACY-TOKEN",
        })
        self.assertEqual(config.url, "https://outline.example")
        self.assertEqual(config.token, "SECRET-TOKEN")
        self.assertNotIn("SECRET-TOKEN", repr(config))
        self.assertNotIn("LEGACY-TOKEN", repr(config))

    def test_explicit_config_file_resolves_without_echoing_secret(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "outline.json"
            config_path.write_text(json.dumps({"url": "https://outline.example", "token": "FILE-SECRET"}))
            config = self.runtime.resolve_config(config_path=config_path, env={})
        self.assertEqual(config.url, "https://outline.example")
        self.assertEqual(config.token, "FILE-SECRET")
        self.assertNotIn("FILE-SECRET", json.dumps(config.to_public_dict()))

    def test_prompt_is_opt_in_and_saving_is_explicit(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            env = {"OUTLINE_URL": "https://outline.example"}
            no_prompt = self.runtime.resolve_config(config_path=config_path, env=env)
            self.assertFalse(config_path.exists())
            self.assertIn("OUTLINE_API_TOKEN", self.runtime.check_config(no_prompt))
            prompted = self.runtime.resolve_config(
                config_path=config_path, env=env, prompt=True, getpass_fn=lambda _: "PROMPT-SECRET"
            )
            self.assertEqual(prompted.token, "PROMPT-SECRET")
            self.assertFalse(config_path.exists())
            self.runtime.resolve_config(
                config_path=config_path, env=env, prompt=True,
                getpass_fn=lambda _: "PROMPT-SECRET", save=True,
            )
            self.assertEqual(json.loads(config_path.read_text())["token"], "PROMPT-SECRET")
            self.assertEqual(config_path.stat().st_mode & 0o777, 0o600)

    def test_environment_url_does_not_reuse_a_different_deployments_file_token(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.json"
            config_path.write_text(json.dumps({"url": "https://one.example", "token": "ONE-SECRET"}))
            config = self.runtime.resolve_config(
                config_path=config_path, env={"OUTLINE_URL": "https://two.example"}
            )
        self.assertEqual(config.token, "")
        errors = self.runtime.check_config(config)
        self.assertTrue(any("match" in x for x in errors))
        self.assertNotIn("ONE-SECRET", " ".join(errors))

    def test_config_check_reports_only_missing_keys(self):
        config = self.runtime.resolve_config(env={"OUTLINE_API_TOKEN": "DO-NOT-PRINT"})
        errors = self.runtime.check_config(config)
        self.assertTrue(any("OUTLINE_URL" in x for x in errors))
        self.assertFalse(any("DO-NOT-PRINT" in x for x in errors))


class OutlineIntentTargetTests(unittest.TestCase):
    def setUp(self):
        import outline_intent
        self.runtime = outline_intent

    def test_accepts_document_and_collection_urls_and_extracts_uuid(self):
        doc = self.runtime.parse_target(
            "https://outline.example/doc/intent-12345678-1234-4234-8234-123456789abc",
            "https://outline.example",
        )
        collection = self.runtime.parse_target(
            "https://outline.example/collection/engineering-12345678-1234-4234-8234-123456789abd",
            "https://outline.example",
        )
        self.assertEqual((doc.kind, doc.id), ("document", "12345678-1234-4234-8234-123456789abc"))
        self.assertEqual(collection.kind, "collection")

    def test_rejects_cross_origin_unsupported_paths_and_http_unless_local_test(self):
        bad_urls = [
            "https://attacker.example/doc/title-12345678-1234-4234-8234-123456789abc",
            "https://outline.example/doc/title-12345678-1234-4234-8234-123456789abc/extra",
            "https://outline.example/settings",
        ]
        for url in bad_urls:
            with self.subTest(url=url), self.assertRaises(self.runtime.OutlineIntentError):
                self.runtime.parse_target(url, "https://outline.example")
        with self.assertRaises(self.runtime.OutlineIntentError):
            self.runtime.parse_target("http://outline.example/doc/x-12345678-1234-4234-8234-123456789abc", "http://outline.example")
        local = self.runtime.parse_target(
            "http://localhost:3000/doc/x-12345678-1234-4234-8234-123456789abc",
            "http://localhost:3000", allow_localhost_test=True,
        )
        self.assertEqual(local.kind, "document")


class OutlineIntentTemplateTests(unittest.TestCase):
    def setUp(self):
        import outline_intent
        self.runtime = outline_intent

    def test_accepts_outline_document_without_h1_and_ignores_h2_inside_fences(self):
        markdown = VALID_INTENT.replace("# Intent title\n", "") + "\n```markdown\n## Not a real section\n```\n"
        report = self.runtime.validate_template(markdown, template_text=TEMPLATE)
        self.assertTrue(report.ok, report.errors)

    def test_rejects_missing_duplicate_reordered_and_extra_h2(self):
        cases = {
            "missing": VALID_INTENT.replace("## 目标 / Goal\n", ""),
            "duplicate": VALID_INTENT.replace("## 目标 / Goal\n", "## 目标 / Goal\n\n## 目标 / Goal\n"),
            "reordered": VALID_INTENT.replace("## 目标 / Goal", "## TEMP", 1).replace("## 背景 / Background", "## 目标 / Goal", 1).replace("## TEMP", "## 背景 / Background", 1),
            "extra": VALID_INTENT.replace("## 目标 / Goal", "## Extra\n\n## 目标 / Goal", 1),
        }
        for name, markdown in cases.items():
            with self.subTest(name=name):
                self.assertFalse(self.runtime.validate_template(markdown, template_text=TEMPLATE).ok)

    def test_requires_background_requirements_gwt_and_references_content(self):
        markdown = VALID_INTENT.replace("目标用户 / 业务角色：Developers and reviewers.\n", "")
        markdown = markdown.replace("Given a valid intent, When a reviewer reads it, Then the expected behavior is clear.", "The behavior should be clear.")
        markdown = markdown.replace("相关仓库 / 模块：The documentation repository.\n", "")
        report = self.runtime.validate_template(markdown, template_text=TEMPLATE)
        self.assertFalse(report.ok)
        joined = " ".join(report.errors)
        self.assertTrue("业务角色" in joined or "Background" in joined)
        self.assertIn("Given", joined)
        self.assertTrue("相关仓库" in joined or "References" in joined)

    def test_given_when_then_must_be_in_acceptance_criteria_not_requirements_or_code(self):
        markdown = VALID_INTENT.replace(
            "- Given a complete intent, When the validator checks it, Then it reports the document as valid.",
            "- The validator reports a complete document as valid.",
        ).replace(
            "- The validator checks required content and document structure.",
            "- Given a complete intent, When a reviewer reads it, Then the behavior is clear.\n"
            "```text\nGiven this is code, When it is ignored, Then it is not acceptance criteria.\n```",
        )
        report = self.runtime.validate_template(markdown, template_text=TEMPLATE)
        self.assertFalse(report.ok)
        self.assertTrue(any("Acceptance Criteria" in error for error in report.errors))

    def test_rejects_bare_scaffolds_and_placeholder_only_sections(self):
        markdown = VALID_INTENT.replace("Current requests are tracked in several places.", "待补充")
        markdown = markdown.replace("Operators can see the intent and verify the desired outcome.", "TODO")
        report = self.runtime.validate_template(markdown, template_text=TEMPLATE)
        self.assertFalse(report.ok)

    def test_rejects_the_unfilled_bundled_template(self):
        template = (RUNTIME_DIR / "intent-template.md").read_text(encoding="utf-8")
        report = self.runtime.validate_template(template, template_text=template)
        self.assertFalse(report.ok)

    def test_accepts_reasoned_not_applicable_value(self):
        report = self.runtime.validate_template(VALID_INTENT, template_text=TEMPLATE)
        self.assertTrue(report.ok, report.errors)


class OutlineIntentClientTests(unittest.TestCase):
    def setUp(self):
        import outline_intent
        self.runtime = outline_intent

    def test_retries_transient_read_only_requests_but_never_mutations(self):
        class FlakyOpener:
            def __init__(self):
                self.calls = []

            def open(self, request, timeout=None):
                self.calls.append(request)
                raise URLError("temporary")

        read_opener = FlakyOpener()
        read_client = self.runtime.OutlineClient("https://outline.example", "TOKEN", opener=read_opener, retries=2, retry_delay=0)
        with self.assertRaises(self.runtime.OutlineIntentError):
            read_client.call("documents.info", {"id": "d1"})
        self.assertEqual(len(read_opener.calls), 3)
        write_opener = FlakyOpener()
        write_client = self.runtime.OutlineClient("https://outline.example", "TOKEN", opener=write_opener, retries=2, retry_delay=0)
        with self.assertRaises(self.runtime.OutlineIntentError):
            write_client.call("comments.create", {"id": "c1"})
        self.assertEqual(len(write_opener.calls), 1)

    def test_rejects_cross_origin_redirect_without_following_it(self):
        class RedirectResponse:
            headers = {"Content-Type": "application/json"}

            def geturl(self):
                return "https://attacker.example/api/documents.info"

            def read(self):
                return b'{"data":{}}'

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        class RedirectOpener:
            def open(self, request, timeout=None):
                return RedirectResponse()

        client = self.runtime.OutlineClient("https://outline.example", "TOKEN", opener=RedirectOpener())
        with self.assertRaises(self.runtime.OutlineIntentError):
            client.call("documents.info", {"id": "d1"})

    def test_snapshot_paginates_and_retains_revision_rich_content_and_comment_anchors(self):
        client = FakeClient()
        client.comments = [comment(f"c{i}", "c1" if i > 0 else None, f"Comment {i}") for i in range(205)]
        snapshot = self.runtime.document_snapshot(client, "d1")
        self.assertTrue(snapshot["complete"])
        self.assertEqual(len(snapshot["comments"]), 205)
        self.assertEqual(snapshot["document"]["lastRevision"], 7)
        self.assertEqual(snapshot["document"]["data"], RICH_DOC)
        self.assertEqual(snapshot["comments"][0]["id"], "c0")
        self.assertEqual(snapshot["comments"][1]["parentCommentId"], "c1")
        self.assertEqual(snapshot["comments"][0]["anchorText"], "Original rich text")
        self.assertIn("fingerprint", snapshot)

    def test_extracts_text_from_outline_rich_text(self):
        text = self.runtime.extract_rich_text(RICH_DOC)
        self.assertEqual(text, "Original rich text\nSecond paragraph")


class OutlineIntentPlanTests(unittest.TestCase):
    def setUp(self):
        import outline_intent
        self.runtime = outline_intent
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "docs" / "intent.md").write_text("old local document\n")

    def raw_plan(self):
        return {
            "plan_id": "review-42",
            "purpose": "revise",
            "alignment": alignment(),
            "source_files": [{"path": "docs/intent.md"}],
            "local_changes": [{"path": "docs/intent.md", "content": "new local document\n"}],
            "remote_actions": [
                {"type": "document.update", "document_id": "d1", "text": "Approved replacement", "full_replacement_acknowledged": True},
                {"type": "comment.create", "document_id": "d1", "data": copy.deepcopy(RICH_COMMENT)},
                {"type": "comment.reply", "document_id": "d1", "parent_comment_id": "c1", "data": copy.deepcopy(RICH_COMMENT)},
            ],
        }

    def test_prepare_freezes_source_and_remote_state_and_stable_comment_ids(self):
        client = FakeClient()
        raw = self.raw_plan()
        first = self.runtime.prepare_plan(raw, self.root, client)
        second = self.runtime.prepare_plan(raw, self.root, client)
        self.assertEqual(first["local_changes"][0]["expected_sha256"], hashlib.sha256(b"old local document\n").hexdigest())
        self.assertEqual(first["remote_guards"]["d1"]["lastRevision"], 7)
        self.assertEqual(first["remote_actions"][1]["id"], second["remote_actions"][1]["id"])
        self.assertNotEqual(first["remote_actions"][1]["id"], first["remote_actions"][2]["id"])
        self.assertEqual(self.runtime.content_digest(first), self.runtime.content_digest(second))

    def test_prepare_rejects_missing_alignment_or_product_code_local_write(self):
        client = FakeClient()
        bad = self.raw_plan()
        del bad["alignment"]["implement"]
        with self.assertRaises(self.runtime.OutlineIntentError):
            self.runtime.prepare_plan(bad, self.root, client)
        code = self.raw_plan()
        code["local_changes"][0]["path"] = "src/Feature.java"
        code["alignment"]["artifacts"] = ["src/Feature.java"]
        with self.assertRaises(self.runtime.OutlineIntentError):
            self.runtime.prepare_plan(code, self.root, client)

    def test_prepare_rejects_comment_resolution_and_title_only_updates(self):
        client = FakeClient()
        resolving = self.raw_plan()
        resolving["remote_actions"] = [{"type": "comment.resolve", "comment_id": "c1"}]
        with self.assertRaises(self.runtime.OutlineIntentError):
            self.runtime.prepare_plan(resolving, self.root, client)
        title_only = self.raw_plan()
        title_only["remote_actions"] = [{"type": "document.update", "title": "Intent", "text": "x"}]
        with self.assertRaises(self.runtime.OutlineIntentError):
            self.runtime.prepare_plan(title_only, self.root, client)

    def test_apply_requires_exact_digest_and_prechecks_every_guard_before_writes(self):
        client = FakeClient()
        prepared = self.runtime.prepare_plan(self.raw_plan(), self.root, client)
        with self.assertRaises(self.runtime.OutlineIntentError):
            self.runtime.apply_plan(prepared, "wrong-digest", self.root, client)
        self.assertEqual((self.root / "docs" / "intent.md").read_text(), "old local document\n")
        client.docs["d1"]["lastRevision"] += 1
        with self.assertRaises(self.runtime.OutlineIntentError):
            self.runtime.apply_plan(prepared, self.runtime.content_digest(prepared), self.root, client)
        self.assertEqual((self.root / "docs" / "intent.md").read_text(), "old local document\n")

    def test_apply_backups_local_changes_verifies_remote_readback_and_journals(self):
        client = FakeClient()
        prepared = self.runtime.prepare_plan(self.raw_plan(), self.root, client)
        digest = self.runtime.content_digest(prepared)
        journal = self.root / "journal.json"
        report = self.runtime.apply_plan(prepared, digest, self.root, client, journal_path=journal)
        self.assertTrue(report["complete"])
        self.assertEqual((self.root / "docs" / "intent.md").read_text(), "new local document\n")
        self.assertTrue(list((self.root / ".outline-intent-backups").rglob("*.bak")))
        self.assertTrue(journal.exists())
        self.assertEqual(client.docs["d1"]["text"], "Approved replacement")
        self.assertEqual(len([x for x in client.comments if x["id"] != "c1" and x["id"] != "c2"]), 2)

    def test_partial_failure_stops_and_journal_records_stage_for_resume(self):
        client = FakeClient()
        client.fail_method = "comments.create"
        prepared = self.runtime.prepare_plan(self.raw_plan(), self.root, client)
        journal = self.root / "journal.json"
        with self.assertRaises(self.runtime.OutlineIntentError):
            self.runtime.apply_plan(prepared, self.runtime.content_digest(prepared), self.root, client, journal_path=journal)
        state = json.loads(journal.read_text())
        self.assertEqual(state["status"], "partial_failure")
        self.assertEqual(state["failed_stage"], "remote_mutation")
        self.assertEqual((self.root / "docs" / "intent.md").read_text(), "new local document\n")


if __name__ == "__main__":
    unittest.main()
