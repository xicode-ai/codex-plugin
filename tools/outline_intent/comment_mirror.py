"""Standalone, reversible local view of a complete Outline comment snapshot.

Only this module's integrity-checked notes and exact highlight wrappers are
managed. Everything else is user-owned Markdown, including old human notes.
"""

import hashlib
import html
import json
import re
from urllib.parse import urlsplit

_NAMESPACE = "outline-intent-mirror:v1"
_MARK = '<mark style="background-color: #fff59d;">'
_TOKEN = r"[0-9a-f]{24}"
_START = re.compile(r"<!--\s*outline-intent-mirror")
_BLOCK = re.compile(
    r"<!-- outline-intent-mirror:v1 block (" + _TOKEN + r") ([0-9a-f]{64}) -->"
)
_HIGHLIGHT = re.compile(
    r"<!-- outline-intent-mirror:v1 highlight (" + _TOKEN + r") -->"
)


class CommentMirrorError(ValueError):
    """The snapshot or managed projection cannot be safely reconciled."""

    pass


def _hash(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _code_ranges(text):
    """Protect fenced and indented code, including unterminated fences."""
    ranges = []
    fence = None
    closer = None
    start = offset = 0
    for line in text.splitlines(keepends=True):
        if fence:
            # Match only the opening fence's container. Prefix-looking text
            # inside a top-level fence is literal code, never a new context.
            if closer.fullmatch(line):
                ranges.append((start, offset + len(line)))
                fence = None
        else:
            content, closing_prefix = line, ""
            while container := re.match(
                r"^ {0,3}(?:>[ \t]?|[-+*][ \t]+|\d+[.)][ \t]+)", content
            ):
                prefix = container[0]
                if prefix.lstrip(" ").startswith(">"):
                    closing_prefix += r" {0,3}>[ \t]?"
                else:
                    # A list marker becomes continuation indentation on the
                    # closing line; it is not another literal list marker.
                    closing_prefix += r"[ \t]{" + str(len(prefix)) + "}"
                content = content[container.end() :]
            opening = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)", content)
            if opening and not (opening[1][0] == "`" and "`" in opening[2]):
                fence, start = opening[1], offset
                closer = re.compile(
                    closing_prefix
                    + r" {0,3}"
                    + re.escape(fence[0])
                    + "{"
                    + str(len(fence))
                    + r",}[ \t]*\r?\n?"
                )
            elif line.startswith(("    ", "\t")):
                ranges.append((offset, offset + len(line)))
        offset += len(line)
    if fence:
        ranges.append((start, len(text)))
    return ranges


def _inline_code_ranges(text, block_ranges):
    """Pair equal-length backtick runs, including multiline inline spans."""
    runs = list(re.finditer(r"(?<!`)`+(?!`)", text))
    ranges, index = [], 0
    while index < len(runs):
        opening = runs[index]
        if _protected(opening.start(), block_ranges):
            index += 1
            continue
        closing_index = next(
            (
                other
                for other in range(index + 1, len(runs))
                if len(runs[other][0]) == len(opening[0])
                and not _protected(runs[other].start(), block_ranges)
            ),
            None,
        )
        if closing_index is None:
            index += 1
            continue
        ranges.append((opening.start(), runs[closing_index].end()))
        index = closing_index + 1
    return ranges


def _protected(position, ranges):
    return any(start <= position < end for start, end in ranges)


def canonical_body(markdown: str) -> str:
    """Remove verified managed notes/wrappers, retaining source bytes exactly.

    Managed comment edits conflict. Highlight contents remain editable source.
    Marker examples in fenced/indented code have no management semantics.
    """
    if not isinstance(markdown, str):
        raise CommentMirrorError("Markdown must be text")
    ranges = _code_ranges(markdown)
    output, cursor, seen = [], 0, set()
    while match := _START.search(markdown, cursor):
        if _protected(match.start(), ranges):
            output.append(markdown[cursor : match.end()])
            cursor = match.end()
            continue
        block = _BLOCK.match(markdown, match.start())
        highlight = _HIGHLIGHT.match(markdown, match.start())
        if not (block or highlight):
            raise CommentMirrorError("Unknown or corrupt managed comment marker")
        marker = block or highlight
        kind = "block" if block else "highlight"
        token = marker[1]
        if (kind, token) in seen:
            raise CommentMirrorError("Duplicate managed comment marker")
        seen.add((kind, token))
        footer = f"<!-- {_NAMESPACE} /{kind} {token} -->"
        end = markdown.find(footer, marker.end())
        if end < 0:
            raise CommentMirrorError("Unclosed managed comment marker")
        payload = markdown[marker.end() : end]
        if _START.search(payload):
            raise CommentMirrorError("Nested or corrupt managed comment marker")
        output.append(markdown[cursor : match.start()])
        if block:
            if _hash(payload) != block[2]:
                raise CommentMirrorError("Managed comment content was edited")
        else:
            if not payload.startswith(_MARK) or not payload.endswith("</mark>"):
                raise CommentMirrorError("Managed highlight wrapper was edited")
            source = payload[len(_MARK) : -len("</mark>")]
            if re.search(r"</?mark\b", source, re.I):
                raise CommentMirrorError("Corrupt managed highlight wrapper")
            output.append(source)
        cursor = end + len(footer)
    output.append(markdown[cursor:])
    return "".join(output)


def _text(value, field, *, required=False):
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        raise CommentMirrorError(f"Invalid comment {field}")
    return value


def _rich_text(node):
    if not isinstance(node, dict):
        raise CommentMirrorError("Incomplete comment text")
    kind = node.get("type")
    if kind == "text":
        text = _text(node.get("text"), "text")
        marks = node.get("marks", [])
        if not isinstance(marks, list):
            raise CommentMirrorError("Invalid rich comment marks")
        for mark in marks:
            if not isinstance(mark, dict):
                raise CommentMirrorError("Invalid rich comment mark")
            if mark.get("type") != "link":
                continue
            attrs = mark.get("attrs")
            if not isinstance(attrs, dict):
                raise CommentMirrorError("Incomplete rich link")
            target = _text(attrs.get("href"), "link target", required=True)
            try:
                parsed = urlsplit(target)
                safe = (
                    parsed.scheme.lower() in {"http", "https"}
                    and parsed.hostname
                    and not parsed.username
                    and not parsed.password
                    and not re.search(r"[\s\x00-\x1f\x7f<>]", target)
                )
            except ValueError:
                safe = False
            if not safe:
                raise CommentMirrorError("Unsupported or unsafe rich link target")
            text += " (" + target + ")"
        return text
    if kind in ("hard_break", "hardBreak", "horizontal_rule", "horizontalRule"):
        return "\n"
    if kind == "mention":
        attrs = node.get("attrs")
        if not isinstance(attrs, dict):
            raise CommentMirrorError("Incomplete rich mention")
        label = _text(attrs.get("label"), "mention label", required=True)
        return label if label.startswith("@") else "@" + label
    inline = {"paragraph", "heading", "code_block", "codeBlock"}
    blocks = {
        "doc",
        "blockquote",
        "bullet_list",
        "ordered_list",
        "bulletList",
        "orderedList",
        "list_item",
        "listItem",
        "table",
        "table_row",
        "tableRow",
        "table_cell",
        "tableCell",
        "table_header",
        "tableHeader",
    }
    if kind not in inline | blocks:
        raise CommentMirrorError(f"Unsupported visible rich comment node: {kind!r}")
    children = node.get("content", [])
    if not isinstance(children, list):
        raise CommentMirrorError("Incomplete rich comment content")
    separator = "\n" if kind in blocks else ""
    return separator.join(_rich_text(child) for child in children)


def _comments(snapshot, document_id):
    if (
        not isinstance(snapshot, dict)
        or snapshot.get("complete") is not True
        or not isinstance(snapshot.get("document"), dict)
        or snapshot["document"].get("id") != document_id
        or not isinstance(snapshot.get("comments"), list)
    ):
        raise CommentMirrorError("Complete matching document/comment snapshot required")
    result = {}
    for row in snapshot["comments"]:
        if not isinstance(row, dict) or row.get("documentId") != document_id:
            raise CommentMirrorError("Incomplete or wrong-document comment")
        identifier = _text(row.get("id"), "ID", required=True)
        if identifier in result:
            raise CommentMirrorError("Duplicate comment ID in complete snapshot")
        parent = row.get("parentCommentId")
        if parent is not None:
            parent = _text(parent, "parent ID", required=True)
        author = row.get("createdBy") or row.get("user") or {}
        if not isinstance(author, dict):
            raise CommentMirrorError("Invalid comment author")
        # Runtime snapshots can contain a lossy cached text flattening. A typed
        # rich document is authoritative and must be checked for unknown atoms.
        rich = row.get("data")
        if isinstance(rich, dict) and "type" in rich:
            text = _rich_text(rich)
        elif "text" in row:
            text = _text(row["text"], "text")
        elif isinstance(rich, dict):
            text = _rich_text(rich)
        else:
            raise CommentMirrorError("Incomplete comment text")
        result[identifier] = {
            "id": identifier,
            "parent_id": parent,
            "text": text,
            "author": _text(author.get("name"), "author name") or "Unknown author",
            "created_at": _text(row.get("createdAt"), "created time"),
            "updated_at": _text(row.get("updatedAt"), "updated time"),
            "status": (
                "resolved"
                if row.get("resolvedAt") or row.get("resolved") is True
                else "open"
            ),
            "quote": _text(row.get("anchorText"), "anchor text"),
        }
    # Resolve ancestry without dropping orphan replies or accepting cycles.
    for identifier in result:
        visited, current = set(), identifier
        while current in result:
            if current in visited:
                raise CommentMirrorError("Comment parent cycle")
            visited.add(current)
            current = result[current]["parent_id"]
    return result


def _safe_display(value):
    # Numeric entities preserve readable literal punctuation without allowing
    # Markdown links, fences, HTML, comment terminators or forged headings.
    escaped = html.escape(value, quote=True)
    escaped = re.sub(r"[\x00-\x1f\x7f]", lambda m: f"&#{ord(m[0])};", escaped)
    return re.sub(r"[\\`*_{}\[\]()#+.!|~]", lambda m: f"&#{ord(m[0])};", escaped)


def _block(token, payload):
    return (
        f"<!-- {_NAMESPACE} block {token} {_hash(payload)} -->"
        + payload
        + f"<!-- {_NAMESPACE} /block {token} -->"
    )


def _inside_html(body, position, code_ranges):
    # A plain-looking line inside a raw HTML container is not a safe paragraph.
    stack = []
    for opening in re.finditer(
        r"<!--.*?(?:-->|$)|</?([a-z][\w-]*)\b[^>]*>", body, re.I | re.S
    ):
        if opening.start() >= position:
            break
        if _protected(opening.start(), code_ranges):
            continue
        if position < opening.end():
            return True
        if opening[0].startswith("<!--"):
            continue
        tag = opening[1].lower()
        if opening[0].startswith("</"):
            if tag in stack:
                index = len(stack) - 1 - stack[::-1].index(tag)
                del stack[index:]
        elif not opening[0].endswith("/>") and tag not in {
            "br",
            "hr",
            "img",
            "input",
            "meta",
            "link",
            "wbr",
            "source",
            "area",
            "base",
            "col",
            "embed",
            "param",
            "track",
        }:
            stack.append(tag)
    return bool(stack)


def _anchor(body, quote, ranges):
    if not quote or "\n" in quote or "\r" in quote:
        return None, "missing or multiline quote"
    matches = list(re.finditer(re.escape(quote), body))
    if len(matches) != 1:
        return None, "ambiguous quote" if matches else "removed or unavailable quote"
    start, end = matches[0].span()
    line_start = body.rfind("\n", 0, start) + 1
    line_end = body.find("\n", end)
    if line_end < 0:
        line_end = len(body)
    line = body[line_start:line_end]
    next_line = body[line_end + 1 :].split("\n", 1)[0] if line_end < len(body) else ""
    # Deliberately conservative: only plain paragraphs can host wrappers.
    # Pipes include table cells, backticks include inline code; all HTML is unsafe.
    if (
        _protected(start, ranges)
        or _protected(end - 1, ranges)
        or _inside_html(body, start, ranges)
        or re.search(r"[|`<>\[\]\\*_~]", line)
        or re.fullmatch(r" {0,3}(?:=+|-+)[ \t]*\r?", next_line)
        or re.match(r"^\s*(?:#{1,6}\s|>|[-+]\s|\d+[.)]\s)", line)
    ):
        return None, "unsafe code, table, markup or non-paragraph anchor"
    if line_end and body[line_end - 1 : line_end] == "\r":
        line_end -= 1
    return (start, end, line_end), "located"


def _fallback_position(body, ranges):
    for match in re.finditer(r"^##[ \t]+([^\r\n]+)", body, re.M):
        if (
            not _protected(match.start(), ranges)
            and not _inside_html(body, match.start(), ranges)
            and re.search(r"\bReferences\b|参考资料", match[1], re.I)
        ):
            return match.end()
    raise CommentMirrorError("Unlocated comments require an existing References H2")


def render_mirror(
    body: str,
    snapshot: dict,
    *,
    document_id: str,
    placements: dict | None = None,
    max_bytes: int | None = None,
) -> dict:
    """Render every actual comment/reply; never synthesize unpublished drafts.

    placements optionally maps root IDs to {"quote": exact_source_quote}.
    Unsafe, duplicate or overlapping anchors fall back under References.
    """
    body = canonical_body(body)
    document_id = _text(document_id, "document ID", required=True)
    comments = _comments(snapshot, document_id)
    if placements is None:
        placements = {}
    if not isinstance(placements, dict) or any(
        key not in comments for key in placements
    ):
        raise CommentMirrorError("Placements must reference existing snapshot comments")
    if max_bytes is not None and (
        not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0
    ):
        raise CommentMirrorError("max_bytes must be a nonnegative integer")
    ordered = sorted(comments, key=lambda key: (comments[key]["created_at"], key))
    children = {key: [] for key in ordered}
    roots = []
    for key in ordered:
        parent = comments[key]["parent_id"]
        if parent in comments:
            children[parent].append(key)
        else:
            roots.append(key)
    ranges = _code_ranges(body)
    ranges += _inline_code_ranges(body, ranges)
    occupied, edits, threads, unlocated = [], [], [], []
    for root in roots:
        row = comments[root]
        placement = placements.get(root, {})
        if not isinstance(placement, dict):
            raise CommentMirrorError("Invalid placement mapping")
        quote = _text(placement.get("quote", row["quote"]), "placement quote")
        location, reason = _anchor(body, quote, ranges)
        if row["parent_id"]:
            location, reason = (
                None,
                "orphan reply; parent absent from complete snapshot",
            )
        if location and any(
            location[0] < end and start < location[1] for start, end in occupied
        ):
            location, reason = None, "overlapping thread anchor"
        token = _hash(document_id + "\0" + root)[:24]
        lines = [
            f"> **评审线程 / Review thread {_safe_display(root)}** — "
            + ("located" if location else "unlocated: " + reason),
            "> 原引用 / Original quote: "
            + _safe_display(row["quote"]).replace("\n", "\n> "),
        ]
        comment_ids = []
        pending = [(root, 0)]
        while pending:
            identifier, depth = pending.pop()
            item = comments[identifier]
            comment_ids.append(identifier)
            prefix = "> " * (depth + 1)
            lines.extend(
                [
                    prefix,
                    prefix
                    + "ID: "
                    + _safe_display(identifier)
                    + "; parent ID: "
                    + _safe_display(item["parent_id"] or "none")
                    + "; author: "
                    + _safe_display(item["author"])
                    + "; time: "
                    + _safe_display(item["created_at"] or "unknown")
                    + "; status: "
                    + item["status"],
                ]
            )
            lines.extend(
                prefix + _safe_display(line) for line in item["text"].split("\n")
            )
            pending.extend((key, depth + 1) for key in reversed(children[identifier]))
        payload = "\n\n" + "\n".join(lines) + "\n\n"
        note = _block(token, payload)
        threads.append(
            {
                "root_id": root,
                "comment_ids": comment_ids,
                "location": "located" if location else "unlocated",
                "reason": reason,
            }
        )
        if location:
            start, end, position = location
            occupied.append((start, end))
            edits.extend(
                [
                    (start, 1, f"<!-- {_NAMESPACE} highlight {token} -->" + _MARK),
                    (end, 0, "</mark>" + f"<!-- {_NAMESPACE} /highlight {token} -->"),
                    (position, 2, note),
                ]
            )
        else:
            unlocated.append(note)
    if unlocated:
        position = _fallback_position(body, ranges)
        heading = _block(
            _hash(document_id + "\0unlocated-heading")[:24],
            "\n\n### 待定位评审线程\n\n",
        )
        edits.append((position, 2, heading + "".join(unlocated)))
    # Stable order also handles an anchor ending at its paragraph boundary.
    insertions = {}
    for position, priority, value in sorted(edits, key=lambda item: (item[0], item[1])):
        insertions[position] = insertions.get(position, "") + value
    text = body
    for position in sorted(insertions, reverse=True):
        text = text[:position] + insertions[position] + text[position:]
    if max_bytes is not None and len(text.encode("utf-8")) > max_bytes:
        raise CommentMirrorError(
            "Complete comment mirror exceeds frozen max_bytes; no threads truncated"
        )
    metadata = {
        "schema": 1,
        "document_id": document_id,
        "canonical_body_hash": _hash(body),
        "projection_hash": _hash(text),
        "comments": [comments[key] for key in ordered],
        "threads": threads,
    }
    if isinstance(snapshot.get("fingerprint"), str):
        metadata["snapshot_fingerprint"] = snapshot["fingerprint"]
    metadata["comment_fingerprint"] = _hash(
        json.dumps(metadata["comments"], ensure_ascii=False, sort_keys=True)
    )
    return {"text": text, "body": body, "metadata": metadata}
