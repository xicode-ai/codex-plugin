"""Approved plan execution. Digests cannot prove authorization or semantic alignment."""

from __future__ import annotations
import copy
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

try:
    from .comment_mirror import CommentMirrorError, canonical_body, render_mirror
except ImportError:
    from comment_mirror import CommentMirrorError, canonical_body, render_mirror


class OutlineIntentError(Exception):
    pass


def content_digest(plan):
    return hashlib.sha256(
        json.dumps(
            {k: v for k, v in plan.items() if k != "digest"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _runtime():
    return importlib.import_module("outline_intent")


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def _path(root, relative, writing=False):
    if not isinstance(relative, str) or not relative or "\\" in relative:
        raise OutlineIntentError("Invalid repository-relative path")
    parts = Path(relative)
    if parts.is_absolute() or ".." in parts.parts:
        raise OutlineIntentError("Absolute or escaping paths forbidden")
    target = root
    for part in parts.parts:
        target = target / part
        if target.is_symlink():
            raise OutlineIntentError("Symlinks in approved paths forbidden")
    if not target.resolve().is_relative_to(root):
        raise OutlineIntentError("Path escapes approved repository")
    if writing and (
        parts.parts[0] not in ("docs", ".trellis")
        or parts.suffix not in (".md", ".jsonl", ".json")
        or (
            parts.parts[0] == ".trellis"
            and (len(parts.parts) < 3 or parts.parts[1] != "tasks")
        )
    ):
        raise OutlineIntentError(
            "Only explicit planning files under docs/ or .trellis/tasks/ may change"
        )
    return target


def _atomic(path, data):
    path = Path(path)
    if path.is_symlink():
        raise OutlineIntentError("Refusing symlink output")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".outline-intent-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        if hasattr(os, "O_DIRECTORY"):
            directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _save(path, value):
    _atomic(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def _document(response):
    data = response.get("data", {})
    for key in ("document", "comment", "collection"):
        if isinstance(data.get(key), dict):
            return data[key]
    return data


def _snapshot(client, docid):
    result = _runtime().document_snapshot(client, docid)
    if not result.get("complete") or not result.get("fingerprint"):
        raise OutlineIntentError("Complete document/comment snapshot required")
    return result


def _revision(snapshot):
    d = snapshot["document"]
    value = d.get("lastRevision", d.get("revision", d.get("revisionCount")))
    if not isinstance(value, int) or isinstance(value, bool):
        raise OutlineIntentError("Usable document revision required")
    return value


def _body(text):
    if not isinstance(text, str) or not text.strip():
        raise OutlineIntentError("Proposed intent body required")
    report = _runtime().validate_template(text)
    if not report.ok:
        raise OutlineIntentError(
            "Proposed intent lacks essential sections: " + "; ".join(report.errors)
        )


def _canonical(text):
    try:
        return canonical_body(text)
    except CommentMirrorError as exc:
        raise OutlineIntentError(str(exc)) from None


def _render(mapping, snapshot):
    try:
        result = render_mirror(
            mapping["body"],
            snapshot,
            document_id=mapping["document_id"],
            placements=mapping.get("placements"),
            max_bytes=mapping.get("max_bytes"),
        )
    except CommentMirrorError as exc:
        raise OutlineIntentError(str(exc)) from None
    _body(result["text"])
    return result


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def _prepare_mirrors(result, root, guards):
    mappings = result.get("comment_mirrors", [])
    if not isinstance(mappings, list) or any(
        not isinstance(m, dict)
        or not isinstance(m.get("document_id"), str)
        or not m["document_id"]
        for m in mappings
    ):
        raise OutlineIntentError(
            "Explicit document/path comment mirror mappings required"
        )
    if result["purpose"] != "revise":
        if mappings:
            raise OutlineIntentError("Only revise may write local comment mirrors")
        return
    mappings = copy.deepcopy(mappings)
    for action in result["remote_actions"]:
        if action["type"] == "document.update":
            matches = [
                m for m in mappings if m.get("document_id") == action["document_id"]
            ]
            if not matches:
                mappings.append(
                    {
                        "document_id": action["document_id"],
                        "path": action["source_path"],
                    }
                )
            elif any(m.get("path") != action["source_path"] for m in matches):
                raise OutlineIntentError(
                    "Mirror must bind the reviewed document source"
                )
    required = {
        a["document_id"] for a in result["remote_actions"] if "document_id" in a
    }
    if {m.get("document_id") for m in mappings} != required:
        raise OutlineIntentError(
            "Every revised document requires its explicit local mirror mapping"
        )
    docs, paths = set(), set()
    changes = {c["path"]: c["content"] for c in result["local_changes"]}
    sources = {x["path"]: x for x in result["source_files"]}
    for m in mappings:
        docid, relative = m.get("document_id"), m.get("path")
        target = _path(root, relative, True)
        if docid in docs or relative in paths or target.suffix != ".md":
            raise OutlineIntentError(
                "Comment mirror mapping must be one-to-one Markdown"
            )
        docs.add(docid)
        paths.add(relative)
        if not target.is_file() and relative not in changes:
            raise OutlineIntentError("Mirror source unreadable")
        # Validate even a superseded projection before replacing it.
        if target.is_file():
            _canonical(target.read_text(encoding="utf-8"))
        m["body"] = _canonical(
            changes[relative]
            if relative in changes
            else target.read_text(encoding="utf-8")
        )
        _body(m["body"])
        body_update = next(
            (
                a
                for a in result["remote_actions"]
                if a["type"] == "document.update" and a["document_id"] == docid
            ),
            None,
        )
        if body_update:
            m["intent_binding"] = copy.deepcopy(body_update["source_binding"])
        else:
            remote_text = guards[docid]["document"].get("text")
            if not isinstance(remote_text, str) or not remote_text.strip():
                raise OutlineIntentError(
                    "Comments-only revise requires a readable current intent body"
                )
            binding = {
                "source_path": relative,
                "text": remote_text,
                "presentation": m.get("presentation", {}),
            }
            _source_binding(binding, result["local_changes"], root)
            m["intent_binding"] = binding["source_binding"]
        m.setdefault(
            "metadata_path",
            str(
                Path(relative).parent
                / "research"
                / "outline-comments"
                / (hashlib.sha256(docid.encode()).hexdigest() + ".json")
            ),
        )
        meta = _path(root, m["metadata_path"], True)
        if (
            meta.suffix != ".json"
            or m["metadata_path"] in paths
            or m["metadata_path"] in changes
        ):
            raise OutlineIntentError("Distinct JSON mirror metadata path required")
        paths.add(m["metadata_path"])
        m["preview"] = _render(m, guards[docid])["text"]
        anticipated = copy.deepcopy(guards[docid])
        for action in result["remote_actions"]:
            if action.get("document_id") == docid and action["type"].startswith(
                "comment."
            ):
                anticipated["comments"].append(
                    {
                        "id": action["id"],
                        "documentId": docid,
                        "parentCommentId": action.get("parent_comment_id"),
                        "data": copy.deepcopy(action["data"]),
                        **{
                            k: action[k]
                            for k in ("anchorText", "anchorPrefix", "anchorSuffix")
                            if k in action
                        },
                    }
                )
        # Check all presently known bytes before any remote action. Server-supplied
        # author/timestamp fields still require the final, actual-snapshot check.
        proposed = _render(m, anticipated)
        m["draft_preview"] = (
            "DRAFT PREVIEW: planned replies below are not published.\n\n"
            + proposed["text"]
        )
        # A staged body change must not erase the already verified discussion if
        # the later remote/mirror phase fails. This interim view contains ONLY
        # the original complete snapshot, never the proposed reply preview.
        for change in result["local_changes"]:
            if change["path"] == m["path"]:
                change["content"] = m["preview"]
                change["canonical_result_sha256"] = hashlib.sha256(
                    m["body"].encode()
                ).hexdigest()
                change["result_sha256"] = hashlib.sha256(
                    change["content"].encode()
                ).hexdigest()
        for name in ("path", "metadata_path"):
            relative = m[name]
            sources.setdefault(
                relative, {"path": relative, "sha256": _hash(_path(root, relative))}
            )
    # Catch later reader paths colliding with an earlier metadata output, too.
    if len(paths) != 2 * len(mappings):
        raise OutlineIntentError("Mirror output paths overlap")
    result["comment_mirrors"] = sorted(mappings, key=lambda m: m["document_id"])
    result["source_files"] = [sources[k] for k in sorted(sources)]


def _mirror_outputs(plan, state):
    if any(
        state["actions"].get(str(i), {}).get("status") != "verified"
        for i, _ in enumerate(plan["remote_actions"])
    ):
        return {}
    outputs = {}
    for m in plan.get("comment_mirrors", []):
        rendered = _render(m, state["remote_current"][m["document_id"]])
        outputs[m["path"]] = rendered["text"].encode()
        outputs[m["metadata_path"]] = _json_bytes(rendered["metadata"])
    return outputs


def _source_binding(action, changes, root):
    relative = action.get("source_path")
    path = _path(root, relative)
    staged = next((c["content"] for c in changes if c["path"] == relative), None)
    source = _canonical(
        staged if staged is not None else path.read_text(encoding="utf-8")
    )

    def without_title(value):
        return re.sub(r"\A\s*# [^\n]*\n", "", value, count=1)

    exact = _normalized(without_title(source)) == _normalized(
        without_title(action["text"])
    )
    presentation = action.get("presentation", {})
    if not exact and (
        not isinstance(presentation, dict)
        or not presentation.get("transformations")
        or not str(presentation.get("evidence", "")).strip()
    ):
        raise OutlineIntentError(
            "Remote body differs from reviewed source; declare specific presentation transforms and review evidence"
        )
    action["source_binding"] = {
        "path": relative,
        "source_sha256": hashlib.sha256(source.encode()).hexdigest(),
        "raw_source_sha256": _hash(path),
        "remote_sha256": hashlib.sha256(action["text"].encode()).hexdigest(),
        "mode": "exact" if exact else "reviewed_presentation",
        "presentation": presentation if not exact else {},
    }


def prepare_plan(plan, repo_root, client, *, journal_path=None):
    root = Path(repo_root).resolve()
    if (
        not root.is_dir()
        or not isinstance(plan, dict)
        or not str(plan.get("plan_id", "")).strip()
    ):
        raise OutlineIntentError("Plan ID and readable repository required")
    result = copy.deepcopy(plan)
    if not isinstance(getattr(client, "base_url", None), str) or not client.base_url:
        raise OutlineIntentError(
            "Plan must be bound to an explicit configured Outline origin"
        )
    purpose = result.get("purpose")
    if purpose not in ("sync", "review", "revise"):
        raise OutlineIntentError("Explicit sync/review/revise purpose required")
    changes = result.setdefault("local_changes", [])
    actions = result.setdefault("remote_actions", [])
    if (
        not isinstance(changes, list)
        or not isinstance(actions, list)
        or not (changes or actions)
    ):
        raise OutlineIntentError("Explicit plan actions required")
    if changes and purpose != "revise":
        raise OutlineIntentError("Only revise may edit local artifacts")
    alignment = result.get("alignment", {})
    if changes:
        for name in ("prd", "design", "implement"):
            entry = alignment.get(name)
            if (
                not isinstance(entry, dict)
                or entry.get("status") not in ("aligned", "not_applicable")
                or not str(entry.get("evidence", "")).strip()
            ):
                raise OutlineIntentError(
                    "PRD/design/implement review evidence or non-applicability reason required"
                )
            if entry["status"] == "aligned" and (
                not isinstance(entry.get("paths"), list) or not entry["paths"]
            ):
                raise OutlineIntentError(
                    "Each aligned artifact kind needs actual reviewed file paths"
                )
        if not {x.get("path") for x in changes}.issubset(
            set(alignment.get("artifacts", []))
        ):
            raise OutlineIntentError("Every changed artifact must be reviewed")
    sources = {x["path"]: x for x in result.get("source_files", [])}
    for name in ("prd", "design", "implement"):
        for relative in alignment.get(name, {}).get("paths", []):
            _path(root, relative)
            sources.setdefault(relative, {"path": relative})
    for action in actions:
        if action.get("type") in ("document.create", "document.update"):
            relative = action.get("source_path")
            _path(root, relative)
            sources.setdefault(relative, {"path": relative})
    for relative in alignment.get("artifacts", []):
        sources.setdefault(relative, {"path": relative})
    seen = set()
    for change in changes:
        relative = change.get("path")
        target = _path(root, relative, True)
        if (
            relative in seen
            or not isinstance(change.get("content"), str)
            or not change["content"].strip()
        ):
            raise OutlineIntentError("Duplicate path or missing staged content")
        seen.add(relative)
        if target.name == "task.json":
            if not target.is_file():
                raise OutlineIntentError("Task creation outside scope")
            old, new = json.loads(target.read_text()), json.loads(change["content"])
            if {k: v for k, v in old.items() if k != "meta"} != {
                k: v for k, v in new.items() if k != "meta"
            }:
                raise OutlineIntentError("Task lifecycle/ownership may not change")
        if target.is_file() and target.suffix == ".md":
            _canonical(target.read_text(encoding="utf-8"))
        if target.suffix == ".md":
            change["content"] = _canonical(change["content"])
        if target.name in ("prd.md", "intent.md"):
            _body(change["content"])
        change["expected_sha256"] = _hash(target)
        change["result_sha256"] = hashlib.sha256(change["content"].encode()).hexdigest()
        sources.setdefault(relative, {"path": relative})
    for relative, entry in sources.items():
        target = _path(root, relative)
        if not target.is_file() and relative not in seen:
            raise OutlineIntentError("Required source artifact unreadable")
        entry["sha256"] = _hash(target)
    result["source_files"] = [sources[k] for k in sorted(sources)]
    guards, destinations, updates = {}, {}, set()
    for index, action in enumerate(actions):
        kind = action.get("type")
        if kind not in (
            "document.create",
            "document.update",
            "comment.create",
            "comment.reply",
        ):
            raise OutlineIntentError("Unsupported mutation; resolve/delete forbidden")
        if purpose == "review" and kind != "comment.create":
            raise OutlineIntentError("Initial review permits new review comments only")
        if purpose == "sync" and not kind.startswith("document."):
            raise OutlineIntentError("Sync does not include comments")
        if kind.startswith("document."):
            action["text"] = _canonical(action.get("text"))
            _body(action.get("text"))
            _source_binding(action, changes, root)
        if kind == "document.create":
            if (
                purpose == "revise"
                or not action.get("destination_url")
                or not getattr(client, "base_url", None)
            ):
                raise OutlineIntentError(
                    "Creation requires sync scope and user destination URL"
                )
            target = _runtime().parse_target(action["destination_url"], client.base_url)
            method = (
                "collections.info" if target.kind == "collection" else "documents.info"
            )
            directory = _document(client.call(method, {"id": target.id}))
            if not directory.get("id") or not str(action.get("title", "")).strip():
                raise OutlineIntentError(
                    "Resolved directory and explicit title required"
                )
            action["collection_id"] = (
                directory["id"]
                if target.kind == "collection"
                else directory.get("collectionId")
            )
            action["parent_document_id"] = (
                directory["id"] if target.kind == "document" else None
            )
            destinations[action["destination_url"]] = {
                "method": method,
                "id": target.id,
                "data": directory,
            }
        else:
            docid = action.get("document_id")
            if not isinstance(docid, str) or not docid:
                raise OutlineIntentError(
                    "Explicit document ID required; title lookup forbidden"
                )
            if docid not in guards:
                guards[docid] = _snapshot(client, docid)
            if kind == "document.update":
                cap = result.get("capabilities", {})
                if (
                    cap.get("last_revision") is not True
                    or not str(cap.get("evidence", "")).strip()
                ):
                    raise OutlineIntentError(
                        "Verified lastRevision capability evidence required"
                    )
                verified = getattr(client, "verified_capabilities", {})
                if (
                    verified.get("last_revision") is not True
                    or not str(verified.get("evidence", "")).strip()
                ):
                    raise OutlineIntentError(
                        "Deployment revision capability must be confirmed separately from this plan"
                    )
                if (
                    docid in updates
                    or action.get("full_replacement_acknowledged") is not True
                ):
                    raise OutlineIntentError(
                        "One final body per document and explicit rich-format/anchor replacement acknowledgement required"
                    )
                updates.add(docid)
                guards[docid]["lastRevision"] = _revision(guards[docid])
            else:
                if kind == "comment.reply" and not any(
                    c.get("id") == action.get("parent_comment_id")
                    and c.get("documentId") == guards[docid]["document"]["id"]
                    for c in guards[docid]["comments"]
                ):
                    raise OutlineIntentError(
                        "Reply parent missing from original snapshot"
                    )
                data = action.get("data")
                if (
                    data is None
                    and isinstance(action.get("text"), str)
                    and action["text"].strip()
                ):
                    data = {
                        "type": "doc",
                        "content": [
                            {
                                "type": "paragraph",
                                "content": [{"type": "text", "text": action["text"]}],
                            }
                        ],
                    }
                if (
                    not isinstance(data, dict)
                    or data.get("type") != "doc"
                    or not data.get("content")
                ):
                    raise OutlineIntentError("Nonempty comment data/text required")
                action["data"] = data
        if kind != "document.update":
            action.setdefault(
                "id",
                str(
                    uuid.uuid5(
                        uuid.NAMESPACE_URL,
                        result["plan_id"]
                        + ":"
                        + str(index)
                        + ":"
                        + content_digest(action),
                    )
                ),
            )
            try:
                uuid.UUID(action["id"])
            except (ValueError, TypeError):
                raise OutlineIntentError(
                    "Fixed UUID required for create/reply"
                ) from None
    identifiers = [x["id"] for x in actions if "id" in x]
    if len(identifiers) != len(set(identifiers)):
        raise OutlineIntentError("Duplicate create/reply action IDs")
    _prepare_mirrors(result, root, guards)
    result.update(
        schema_version=1,
        repository_root=str(root),
        outline_origin=getattr(client, "base_url", None),
        deployment_capabilities=copy.deepcopy(
            getattr(client, "verified_capabilities", {})
        ),
        remote_guards=guards,
        destination_guards=destinations,
    )
    result["digest"] = content_digest(result)
    if journal_path:
        _save(
            Path(journal_path),
            {
                "status": "prepared",
                "digest": result["digest"],
                "plan_id": result["plan_id"],
            },
        )
    return result


def _normalized(text):
    lines, fenced = [], False
    for line in text.replace("\r\n", "\n").splitlines():
        if re.match(r"^\s{0,3}(```|~~~)", line):
            fenced = not fenced
        if fenced:
            lines.append(line.rstrip())
            continue
        line = re.sub(r"^\* ", "- ", line.strip())
        if line.startswith("|"):
            line = re.sub(r"\s*\|\s*", "|", line)
            if re.fullmatch(r"\|(?:\:?-+\:?\|)+", line):
                continue
        if line:
            lines.append(line)
    return "\n".join(lines)


def _retained(before, after, allowed_new=()):
    current = {c["id"]: c for c in after["comments"]}
    if set(current) - {c["id"] for c in before["comments"]} - set(allowed_new):
        raise OutlineIntentError("Other comments arrived during this action; reassess")
    for old in before["comments"]:
        new = current.get(old["id"])
        if new is None or any(
            new.get(k) != old.get(k)
            for k in (
                "parentCommentId",
                "data",
                "resolvedAt",
                "resolvedById",
                "anchorText",
                "anchorPrefix",
                "anchorSuffix",
            )
        ):
            raise OutlineIntentError("Original comment/thread changed; reassess")


def _without_own_anchor(value, comment_id):
    if isinstance(value, list):
        return [_without_own_anchor(item, comment_id) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key == "marks" and isinstance(item, list):
            retained = [
                m
                for m in item
                if not (
                    isinstance(m, dict)
                    and m.get("type") == "comment"
                    and m.get("attrs", {}).get("id") == comment_id
                )
            ]
            if retained:
                result[key] = _without_own_anchor(retained, comment_id)
        else:
            result[key] = _without_own_anchor(item, comment_id)
    return result


def _verify(client, action, before):
    kind = action["type"]
    docid = action.get("document_id", action.get("id"))
    if kind.startswith("document."):
        after = _snapshot(client, docid)
        doc = after["document"]
        if _normalized(doc.get("text", "")) != _normalized(action["text"]):
            raise OutlineIntentError("Document readback differs from approved body")
        if kind == "document.create":
            if any(
                doc.get(k) != action.get(v)
                for k, v in (
                    ("title", "title"),
                    ("collectionId", "collection_id"),
                    ("parentDocumentId", "parent_document_id"),
                )
            ):
                raise OutlineIntentError("Created document title/destination mismatch")
        else:
            if any(
                doc.get(k) != before["document"].get(k)
                for k in ("title", "collectionId", "parentDocumentId")
            ):
                raise OutlineIntentError("Unexpected title/destination change")
            _retained(before, after)
        return after
    row = _document(
        client.call("comments.info", {"id": action["id"], "includeAnchorText": True})
    )
    if (
        row.get("documentId") != docid
        or row.get("parentCommentId") != action.get("parent_comment_id")
        or row.get("data") != action["data"]
    ):
        raise OutlineIntentError("Comment/reply readback mismatch")
    for field in ("anchorText", "anchorPrefix", "anchorSuffix"):
        if action.get(field) and row.get(field) != action[field]:
            raise OutlineIntentError(
                "Requested inline anchor context was not confirmed by the deployment"
            )
    after = _snapshot(client, docid)
    _retained(before, after, (action["id"],))
    original, current = before["document"], after["document"]
    stable_fields = ("id", "title", "text", "collectionId", "parentDocumentId")
    if any(
        current.get(k) != original.get(k) for k in stable_fields
    ) or _without_own_anchor(current.get("data"), action["id"]) != _without_own_anchor(
        original.get("data"), action["id"]
    ):
        raise OutlineIntentError("Document changed while posting a comment/reply")
    return after


def _check_sources(plan, state, root):
    writes = {x["path"]: x for x in plan["local_changes"]}
    projections = _mirror_outputs(plan, state)
    for source in plan["source_files"]:
        path = _path(root, source["path"], source["path"] in writes)
        change = writes.get(source["path"])
        entry = state["local"].get(source["path"], {})
        if (
            source["path"] in projections
            and _hash(path) == hashlib.sha256(projections[source["path"]]).hexdigest()
        ):
            if change:
                state["local"].setdefault(source["path"], {})["status"] = "verified"
            continue
        if (
            change
            and entry.get("status") in ("writing", "verified")
            and _hash(path) == change["result_sha256"]
        ):
            entry["status"] = "verified"
        elif _hash(path) != source["sha256"]:
            raise OutlineIntentError("Local source changed after preparation")


def _journal_consistent(plan, state):
    """A journal cannot grant a new baseline; transitions need planned readbacks."""
    if any(
        not isinstance(state.get(key), dict)
        for key in ("local", "actions", "remote_current")
    ):
        raise OutlineIntentError("Invalid recovery journal structure")
    baselines = plan["remote_guards"]
    current = state["remote_current"]
    active = [
        (a, state["actions"].get(str(i), {}))
        for i, a in enumerate(plan["remote_actions"])
        if state["actions"].get(str(i), {}).get("status") in ("sending", "verified")
    ]
    created = {a["id"] for a, _ in active if a["type"] == "document.create"}
    if not set(baselines).issubset(current) or set(current) - set(baselines) - created:
        raise OutlineIntentError(
            "Recovery journal has missing or unplanned document guards"
        )
    for docid, original in baselines.items():
        recorded = current[docid]
        own = [a for a, _ in active if a.get("document_id") == docid]
        if not own:
            if recorded.get("fingerprint") != original["fingerprint"]:
                raise OutlineIntentError(
                    "Journal may not rebase an untouched approved guard"
                )
            continue
        update = next((a for a in own if a["type"] == "document.update"), None)
        comment_ids = {a["id"] for a in own if a["type"].startswith("comment.")}
        _retained(original, recorded, comment_ids)
        old_doc, new_doc = original["document"], recorded["document"]
        if any(
            old_doc.get(k) != new_doc.get(k)
            for k in ("id", "title", "collectionId", "parentDocumentId")
        ):
            raise OutlineIntentError("Unplanned recovery document metadata change")
        # 'sending' may still have the original checkpoint. It is reconciled next.
        permitted = (
            (old_doc.get("text", ""), update["text"])
            if update
            else (old_doc.get("text", ""),)
        )
        if _normalized(new_doc.get("text", "")) not in {
            _normalized(text) for text in permitted
        }:
            raise OutlineIntentError(
                "Journal body does not derive from an approved action"
            )
        if not update:
            old_data, new_data = old_doc.get("data"), new_doc.get("data")
            for identifier in comment_ids:
                old_data = _without_own_anchor(old_data, identifier)
                new_data = _without_own_anchor(new_data, identifier)
            if old_data != new_data:
                raise OutlineIntentError(
                    "Unplanned rich document change in recovery journal"
                )


def _finalize_mirrors(plan, state, root, client, backup, save):
    mappings = plan.get("comment_mirrors", [])
    if not mappings:
        return
    state["failed_stage"] = "comment_mirror"
    save()
    for m in mappings:
        docid = m["document_id"]
        snapshot = _snapshot(client, docid)
        if snapshot["fingerprint"] != state["remote_current"][docid]["fingerprint"]:
            raise OutlineIntentError(
                "Final mirror snapshot changed after verified actions"
            )
        # Never derive a committed mirror from drafts or journal-provided hashes.
        rendered = _render(m, snapshot)
        outputs = {
            m["metadata_path"]: _json_bytes(rendered["metadata"]),
            m["path"]: rendered["text"].encode(),
        }
        _check_sources(plan, state, root)
        record = {
            "status": "writing",
            "recipe": {
                "canonical_body_hash": rendered["metadata"]["canonical_body_hash"],
                "snapshot_fingerprint": snapshot["fingerprint"],
                "comment_fingerprint": rendered["metadata"]["comment_fingerprint"],
                "mapping_digest": content_digest(m),
            },
            "outputs": {},
        }
        for relative, data in outputs.items():
            path = _path(root, relative, True)
            saved = backup / "comment_mirror" / (relative + ".bak")
            if path.exists() and path.read_bytes() != data and not saved.exists():
                _atomic(saved, path.read_bytes())
            record["outputs"][relative] = {
                "sha256": hashlib.sha256(data).hexdigest(),
                "backup": str(saved) if saved.exists() else None,
            }
        state.setdefault("comment_mirror", {})[docid] = record
        save()
        for relative, data in outputs.items():
            _check_sources(plan, state, root)
            path = _path(root, relative, True)
            if not path.is_file() or path.read_bytes() != data:
                _atomic(path, data)
            if path.read_bytes() != data:
                raise OutlineIntentError("Comment mirror readback mismatch")
        # Verify the pair together after its last write; an edit to metadata
        # during the reader replacement must not produce a completed journal.
        for relative, data in outputs.items():
            if _path(root, relative, True).read_bytes() != data:
                raise OutlineIntentError("Comment mirror final readback mismatch")
        _check_sources(plan, state, root)
        record["status"] = "verified"
        save()


def apply_plan(
    plan, approved_digest, repo_root, client, *, journal_path=None, backup_dir=None
):
    root = Path(repo_root).resolve()
    digest = content_digest(plan)
    if (
        approved_digest != digest
        or plan.get("digest") != digest
        or plan.get("repository_root") != str(root)
        or plan.get("schema_version") != 1
    ):
        raise OutlineIntentError("Exact prepared digest/repository binding required")
    if plan.get("outline_origin") != getattr(client, "base_url", None):
        raise OutlineIntentError(
            "Configured Outline deployment changed after preparation"
        )
    if any(a.get("type") == "document.update" for a in plan["remote_actions"]):
        current_capabilities = getattr(client, "verified_capabilities", {})
        if current_capabilities.get(
            "last_revision"
        ) is not True or current_capabilities != plan.get("deployment_capabilities"):
            raise OutlineIntentError(
                "Verified deployment capability changed after preparation"
            )
    journal = (
        Path(journal_path)
        if journal_path
        else root / ".outline-intent-backups" / (digest + ".journal.json")
    )
    backup = (
        Path(backup_dir) if backup_dir else root / ".outline-intent-backups" / digest
    )
    journal.parent.mkdir(parents=True, exist_ok=True)
    lock = journal.with_name(journal.name + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise OutlineIntentError(
            "Journal locked; inspect concurrent/interrupted run before recovery"
        ) from None
    os.close(fd)
    state = None
    try:
        if journal.exists():
            candidate = json.loads(journal.read_text())
            if not isinstance(candidate, dict) or candidate.get("digest") != digest:
                raise OutlineIntentError("Journal belongs to another plan")
            state = candidate
        if not state or state.get("status") == "prepared":
            state = {
                "digest": digest,
                "plan_id": plan["plan_id"],
                "status": "checking",
                "local": {},
                "actions": {},
                "remote_current": copy.deepcopy(plan["remote_guards"]),
            }
        _journal_consistent(plan, state)

        def save():
            _save(journal, state)

        save()
        # Unknown writes are read back, never automatically resent.
        recovery_order = sorted(
            enumerate(plan["remote_actions"]),
            key=lambda pair: (
                0
                if state["actions"].get(str(pair[0]), {}).get("status") == "sending"
                else 1
            ),
        )
        for index, action in recovery_order:
            entry = state["actions"].get(str(index), {})
            if entry.get("status") in ("sending", "verified"):
                docid = action.get("document_id", action.get("id"))
                try:
                    after = _verify(client, action, state["remote_current"].get(docid))
                except Exception:
                    raise OutlineIntentError(
                        "Prior write remains unknown; reconcile saved ID manually, do not resend"
                    ) from None
                state["remote_current"][after["document"]["id"]] = after
                entry["status"] = "verified"
                save()
        _check_sources(plan, state, root)
        for docid, expected in state["remote_current"].items():
            if _snapshot(client, docid)["fingerprint"] != expected["fingerprint"]:
                raise OutlineIntentError(
                    "Remote document/comment changed after approval"
                )
        for directory in plan.get("destination_guards", {}).values():
            if (
                _document(client.call(directory["method"], {"id": directory["id"]}))
                != directory["data"]
            ):
                raise OutlineIntentError("Approved destination changed")
        save()
        state["failed_stage"] = "local_write"
        for change in plan["local_changes"]:
            relative = change["path"]
            if state["local"].get(relative, {}).get("status") == "verified":
                continue
            path = _path(root, relative, True)
            if _hash(path) != change["expected_sha256"]:
                raise OutlineIntentError("Local file changed immediately before write")
            original = path.read_bytes() if path.exists() else None
            saved = backup / (relative + ".bak")
            if original is not None:
                if saved.exists() and saved.read_bytes() != original:
                    raise OutlineIntentError("Refusing to replace a different backup")
                _atomic(saved, original)
            state["local"][relative] = {
                "status": "writing",
                "backup": str(saved) if original is not None else None,
            }
            save()
            _atomic(path, change["content"].encode())
            if _hash(path) != change["result_sha256"]:
                raise OutlineIntentError("Local readback mismatch")
            state["local"][relative]["status"] = "verified"
            save()
        state["failed_stage"] = "remote_mutation"
        _check_sources(plan, state, root)
        for index, action in enumerate(plan["remote_actions"]):
            kind = action["type"]
            if state["actions"].get(str(index), {}).get("status") == "verified":
                continue
            _check_sources(plan, state, root)
            docid = action.get("document_id", action.get("id"))
            before = state["remote_current"].get(docid)
            if (
                before
                and _snapshot(client, docid)["fingerprint"] != before["fingerprint"]
            ):
                raise OutlineIntentError("Remote target changed before next action")
            state["actions"][str(index)] = {
                "status": "sending",
                "type": kind,
                "id": action.get("id"),
                "document_id": docid,
            }
            save()
            if kind == "document.update":
                client.call(
                    "documents.update",
                    {
                        "id": docid,
                        "text": action["text"],
                        "lastRevision": _revision(before),
                    },
                )
            elif kind == "document.create":
                payload = {
                    "id": action["id"],
                    "title": action["title"],
                    "text": action["text"],
                    "publish": True,
                }
                for source, destination in (
                    ("collection_id", "collectionId"),
                    ("parent_document_id", "parentDocumentId"),
                ):
                    if action.get(source):
                        payload[destination] = action[source]
                client.call("documents.create", payload)
            else:
                payload = {
                    "id": action["id"],
                    "documentId": docid,
                    "data": action["data"],
                }
                if kind == "comment.reply":
                    payload["parentCommentId"] = action["parent_comment_id"]
                for field in ("anchorText", "anchorPrefix", "anchorSuffix"):
                    if action.get(field):
                        payload[field] = action[field]
                client.call("comments.create", payload)
            after = _verify(client, action, before)
            state["remote_current"][after["document"]["id"]] = after
            state["actions"][str(index)]["status"] = "verified"
            save()
        _finalize_mirrors(plan, state, root, client, backup, save)
        state.update(status="complete", complete=True)
        state.pop("failed_stage", None)
        save()
        return {
            "complete": True,
            "digest": digest,
            "journal": str(journal),
            "local_files": list(state["local"]),
            "actions": copy.deepcopy(state["actions"]),
        }
    except Exception as exc:
        if state is not None:
            state.update(status="partial_failure", complete=False)
            state.setdefault("failed_stage", "precheck")
            _save(journal, state)
        if isinstance(exc, OutlineIntentError):
            raise
        raise OutlineIntentError(
            "Plan stopped; inspect journal and reconcile before recovery"
        ) from None
    finally:
        lock.unlink(missing_ok=True)
