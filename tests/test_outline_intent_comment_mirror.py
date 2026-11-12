"""Behavioral contract for the reversible local comment projection."""

import json
from pathlib import Path
import re
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools/outline_intent"))
import comment_mirror as mirror

BODY = (
    "".join(f"## Section {n}\n\nText {n}.\n\n" for n in range(7))
    + "## 参考资料 / References\n\nOriginal reference.\n"
)
BODY = BODY.replace("Text 0.", "A unique original sentence.")


def comment(identifier="root", parent=None, text="Original review", **extra):
    return dict(
        id=identifier,
        documentId="doc",
        parentCommentId=parent,
        text=text,
        anchorText="unique original sentence",
        createdAt="2026-11-11T00:00:00Z",
        createdBy={"id": "author", "name": "Alice", "email": "private@example.org"},
        **extra,
    )


def snapshot(*comments):
    return {
        "complete": True,
        "document": {"id": "doc", "revision": 7},
        "fingerprint": "remote-fingerprint",
        "comments": list(comments),
    }


class CommentMirrorTests(unittest.TestCase):
    def render(self, body=BODY, snap=None, **kwargs):
        return mirror.render_mirror(
            body,
            snapshot(comment()) if snap is None else snap,
            document_id="doc",
            **kwargs,
        )

    def test_visible_full_reply_tree_and_exact_body(self):
        snap = snapshot(
            comment(),
            comment("reply", "root", "Published reply"),
            comment("nested", "reply", "Nested actual reply", resolvedAt="today"),
        )
        result = self.render(snap=snap)
        text = result["text"]
        self.assertIn(
            '<mark style="background-color: #fff59d;">unique original sentence</mark>',
            text,
        )
        for value in (
            "Original review",
            "Published reply",
            "Nested actual reply",
            "Alice",
            "root",
            "reply",
            "nested",
            "resolved",
            "2026-11-11",
        ):
            self.assertIn(value, text)
        self.assertEqual(8, len(re.findall(r"^## ", text, re.M)))
        self.assertEqual(BODY, result["body"])
        self.assertEqual(BODY, mirror.canonical_body(text))
        self.assertNotIn("private@example.org", json.dumps(result))

    def test_repeat_update_delete_are_deterministic(self):
        first = self.render()
        self.assertEqual(first, self.render(first["text"]))
        changed = self.render(
            first["text"],
            snapshot(comment(text="Edited remote review", resolvedAt="today")),
        )
        self.assertNotIn("Original review", changed["text"])
        self.assertIn("Edited remote review", changed["text"])
        self.assertEqual(BODY, self.render(changed["text"], snapshot())["text"])

    def test_body_edits_and_unmanaged_notes_survive(self):
        body = BODY + "\n> Human review note\n<!-- unrelated note -->\n"
        first = self.render(body)
        edited = first["text"].replace("Text 4.", "Human changed the source.")
        self.assertEqual(
            body.replace("Text 4.", "Human changed the source."),
            mirror.canonical_body(edited),
        )
        # Highlighted text remains user-owned source; managed quotes do not.
        edited = first["text"].replace(
            ">unique original sentence<", ">edited original sentence<", 1
        )
        self.assertEqual(
            body.replace("unique original sentence", "edited original sentence"),
            mirror.canonical_body(edited),
        )

    def test_ambiguous_removed_code_table_and_html_quotes_are_unlocated(self):
        for source, quote in (
            (BODY + "unique original sentence\n", "unique original sentence"),
            (BODY, "removed words"),
            (BODY + "\n```python\ncode quote\n```\n", "code quote"),
            (BODY + "\n| table quote | value |\n", "table quote"),
            (BODY + "\n<div>html quote</div>\n", "html quote"),
            (BODY + "\n<div>\nhtml block quote\n</div>\n", "html block quote"),
        ):
            with self.subTest(quote=quote):
                c = comment()
                c["anchorText"] = quote
                result = self.render(source, snapshot(c))
                self.assertNotIn("<mark ", result["text"])
                self.assertIn("### 待定位评审线程", result["text"])
                self.assertIn("unlocated", result["text"])
                self.assertEqual(source, mirror.canonical_body(result["text"]))

    def test_multiple_overlapping_roots_and_orphan_retain_every_comment(self):
        c2 = comment("second", text="Other original review")
        c2["anchorText"] = "original sentence"
        orphan = comment("orphan", "missing", "Orphan actual reply")
        result = self.render(snap=snapshot(c2, orphan, comment()))
        for value in (
            "Other original review",
            "Original review",
            "Orphan actual reply",
            "missing",
        ):
            self.assertIn(value, result["text"])
        self.assertEqual(BODY, mirror.canonical_body(result["text"]))
        self.assertEqual(1, result["text"].count("<mark "))

    def test_untrusted_content_is_visible_text_and_metadata_is_whitelisted(self):
        c = comment(
            text="<script>bad()</script>\n## injection\n-->\n<!-- outline-intent-mirror:v1 bogus -->\n```\n![x](https://evil)"
        )
        c["createdBy"]["name"] = "<img src=x onerror=bad()>"
        c["secret"] = "do-not-copy"
        c["data"] = {"token": "do-not-copy"}
        result = self.render(snap=snapshot(c))
        self.assertNotIn("<script>", result["text"])
        self.assertNotIn("<img ", result["text"])
        self.assertNotIn("![x]", result["text"])
        self.assertNotIn("do-not-copy", json.dumps(result))
        self.assertEqual(BODY, mirror.canonical_body(result["text"]))

    def test_incomplete_duplicate_wrong_document_and_cycles_are_conflicts(self):
        invalid = [
            dict(snapshot(comment()), complete=False),
            snapshot(comment(), comment()),
            snapshot(dict(comment(), documentId="wrong")),
            snapshot(comment("root", "child"), comment("child", "root")),
        ]
        for snap in invalid:
            with self.subTest(snap=snap), self.assertRaises(mirror.CommentMirrorError):
                self.render(snap=snap)

    def test_changed_and_corrupt_managed_blocks_are_conflicts(self):
        original = self.render()["text"]
        corruptions = [
            original.replace("Original review", "Human edited managed review"),
            original.replace("background-color: #fff59d;", "color: red;"),
            original.replace(
                "outline-intent-mirror:v1 block", "outline-intent-mirror:v1 broken", 1
            ),
        ]
        for text in corruptions:
            with self.subTest(text=text), self.assertRaises(mirror.CommentMirrorError):
                mirror.canonical_body(text)

    def test_code_fence_marker_examples_are_ordinary_body(self):
        body = (
            BODY + "\n```markdown\n<!-- outline-intent-mirror:v1 malformed -->\n```\n"
        )
        self.assertEqual(body, mirror.canonical_body(body))
        self.assertEqual(body, mirror.canonical_body(self.render(body)["text"]))

    def test_frozen_byte_limit_rejects_without_truncation(self):
        result = self.render()
        size = len(result["text"].encode("utf-8"))
        self.assertEqual(result, self.render(max_bytes=size))
        with self.assertRaises(mirror.CommentMirrorError):
            self.render(max_bytes=size - 1)

    def test_explicit_placement_never_forces_ambiguous_match(self):
        c = comment()
        c["anchorText"] = "remote rewritten words"
        result = self.render(
            snap=snapshot(c), placements={"root": {"quote": "Text 2."}}
        )
        self.assertIn(
            '<mark style="background-color: #fff59d;">Text 2.</mark>', result["text"]
        )
        self.assertEqual(BODY, mirror.canonical_body(result["text"]))

    def test_multiline_author_and_carriage_returns_cannot_inject_headings(self):
        c = comment(text="Review\r## Injected section\r<script>bad</script>")
        c["createdBy"]["name"] = "Author\n## Forged heading"
        result = self.render(snap=snapshot(c))
        self.assertEqual(8, len(re.findall(r"^## ", result["text"], re.M)))
        self.assertNotIn("\r", result["text"])
        self.assertEqual(BODY, mirror.canonical_body(result["text"]))

    def test_adjacent_quotes_never_create_nested_highlight_wrappers(self):
        body = BODY.replace("unique original sentence", "firstsecond")
        first, second = comment("z"), comment("a")
        first["anchorText"], second["anchorText"] = "first", "second"
        result = self.render(body, snapshot(first, second))
        self.assertIn(">first</mark>", result["text"])
        self.assertIn(">second</mark>", result["text"])
        self.assertEqual(body, mirror.canonical_body(result["text"]))

    def test_rich_comment_snapshot_and_order_do_not_lose_replies(self):
        root, reply = comment(), comment("reply", "root")
        del reply["text"]
        reply["data"] = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [{"type": "text", "text": "Actual rich reply"}],
                }
            ],
        }
        one = self.render(snap=snapshot(reply, root))
        self.assertIn("Actual rich reply", one["text"])
        self.assertEqual(one, self.render(snap=snapshot(root, reply)))

    def test_roundtrip_crlf_unicode_and_no_final_newline(self):
        for body in (BODY.replace("\n", "\r\n"), BODY.rstrip("\n"), BODY + "汉字🙂"):
            with self.subTest(body=body):
                self.assertEqual(body, mirror.canonical_body(self.render(body)["text"]))

    def test_missing_references_cannot_hide_unlocated_thread(self):
        c = comment()
        c["anchorText"] = "removed"
        body = "## Goal\nBody\n"
        result = self.render(body, snapshot(c))
        self.assertIn("待定位评审线程", result["text"])
        self.assertEqual(body, mirror.canonical_body(result["text"]))

    def test_container_fenced_marker_examples_roundtrip_as_source(self):
        examples = (
            "> ```markdown\n> <!-- outline-intent-mirror:v1 sample -->\n> ```\n",
            "> > ~~~markdown\n> > <!-- outline-intent-mirror:v1 sample -->\n> > ~~~\n",
            "- ```markdown\n  <!-- outline-intent-mirror:v1 sample -->\n  ```\n",
        )
        for example in examples:
            with self.subTest(example=example):
                body = BODY + "\n" + example
                try:
                    clean = mirror.canonical_body(body)
                    rendered = self.render(body)["text"]
                except mirror.CommentMirrorError as exc:
                    self.fail(f"Code example was treated as a managed block: {exc}")
                self.assertEqual(body, clean)
                self.assertEqual(body, mirror.canonical_body(rendered))

    def test_top_level_fence_content_prefixes_cannot_close_the_fence(self):
        for inner in ("- ```", "> ```"):
            with self.subTest(inner=inner):
                example = (
                    "```markdown\n"
                    + inner
                    + "\n<!-- outline-intent-mirror:v1 sample -->\n```"
                )
                body = BODY + "\n" + example
                try:
                    clean = mirror.canonical_body(body)
                    rendered = self.render(body)["text"]
                except mirror.CommentMirrorError as exc:
                    self.fail(
                        f"Fence content prematurely closed the code example: {exc}"
                    )
                self.assertEqual(body, clean)
                self.assertEqual(body, mirror.canonical_body(rendered))

    def test_multiline_inline_code_setext_and_nested_html_are_unlocated(self):
        examples = (
            "`start\ntarget\nend`\n",
            "target\n------\n",
            "<div>\n<div>inner</div>\ntarget\n</div>\n",
        )
        for example in examples:
            with self.subTest(example=example):
                body = BODY + "\n" + example
                c = comment()
                c["anchorText"] = "target"
                result = self.render(body, snapshot(c))
                self.assertNotIn("<mark ", result["text"])
                self.assertEqual(
                    "unlocated", result["metadata"]["threads"][0]["location"]
                )
                self.assertEqual(body, mirror.canonical_body(result["text"]))

    def test_rich_mentions_and_blockquote_boundaries_remain_visible(self):
        c = comment()
        del c["text"]
        c["data"] = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Ask "},
                        {
                            "type": "mention",
                            "attrs": {
                                "label": "Alice",
                                "id": "person",
                                "email": "do-not-copy",
                            },
                        },
                        {"type": "text", "text": " to review"},
                    ],
                },
                {
                    "type": "blockquote",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "first paragraph"}],
                        },
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "second paragraph"}],
                        },
                    ],
                },
            ],
        }
        result = self.render(snap=snapshot(c))
        text = result["metadata"]["comments"][0]["text"]
        self.assertIn("Ask @Alice to review", text)
        self.assertIn("first paragraph\nsecond paragraph", text)
        self.assertNotIn("do-not-copy", json.dumps(result))

    def test_unknown_visible_rich_atoms_conflict_instead_of_disappearing(self):
        c = comment()
        del c["text"]
        c["data"] = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "future_visible_widget",
                            "attrs": {"label": "Important detail"},
                        }
                    ],
                }
            ],
        }
        with self.assertRaises(mirror.CommentMirrorError):
            self.render(snap=snapshot(c))

    def test_runtime_cached_text_cannot_drop_rich_mention(self):
        c = comment(text="Ask  to review")
        c["data"] = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {"type": "text", "text": "Ask "},
                        {"type": "mention", "attrs": {"label": "Alice"}},
                        {"type": "text", "text": " to review"},
                    ],
                }
            ],
        }
        result = self.render(snap=snapshot(c))
        self.assertIn("Ask @Alice to review", result["text"])

    def test_cached_text_cannot_hide_unknown_visible_rich_content(self):
        c = comment(text="Cached incomplete text")
        c["data"] = {
            "type": "doc",
            "content": [
                {"type": "unknown_visible_atom", "attrs": {"label": "Lost detail"}}
            ],
        }
        with self.assertRaises(mirror.CommentMirrorError):
            self.render(snap=snapshot(c))

    def test_rich_link_marks_preserve_caption_and_readable_target_inertly(self):
        c = comment(text="Cached caption only")
        c["data"] = {
            "type": "doc",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        {
                            "type": "text",
                            "text": "<b>见规范</b>\n## Forged heading",
                            "marks": [
                                {
                                    "type": "link",
                                    "attrs": {
                                        "href": "https://example.org/spec?item=1&v=2"
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
        result = self.render(snap=snapshot(c))
        text = result["metadata"]["comments"][0]["text"]
        self.assertIn("https://example.org/spec?item=1&v=2", text)
        self.assertIn("见规范", text)
        self.assertNotIn("<b>", result["text"])
        self.assertEqual(8, len(re.findall(r"^## ", result["text"], re.M)))
        self.assertEqual(BODY, mirror.canonical_body(result["text"]))

    def test_unsafe_or_incomplete_rich_link_targets_conflict(self):
        for target in (
            "javascript:bad()",
            "file:///secret",
            "https:///missing-host",
            "https://example.org/\nforged",
            None,
        ):
            with self.subTest(target=target):
                c = comment()
                del c["text"]
                c["data"] = {
                    "type": "doc",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [
                                {
                                    "type": "text",
                                    "text": "见规范",
                                    "marks": [
                                        {"type": "link", "attrs": {"href": target}}
                                    ],
                                }
                            ],
                        }
                    ],
                }
                with self.assertRaises(mirror.CommentMirrorError):
                    self.render(snap=snapshot(c))


if __name__ == "__main__":
    unittest.main()
