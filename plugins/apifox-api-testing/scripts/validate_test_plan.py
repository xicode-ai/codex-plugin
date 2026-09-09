#!/usr/bin/env python3
"""Validate the dependency-free YAML-like test plan emitted by the Skill."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


REQUIRED_CASE_FIELDS = (
    "id",
    "requirementRefs",
    "businessFlow",
    "category",
    "endpoint",
    "assertions",
    "risk",
    "requiresConfirmation",
    "executionStatus",
)
VALID_STATUSES = {"planned", "passed", "failed", "blocked", "skipped", "error"}
VALID_SOURCES = {"provided", "generated", "merged", "conflict"}
MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MUTATING_PATH_WORDS = ("cancel", "approve", "submit", "delete", "remove", "update")
CASE_START_RE = re.compile(r"^\s*-\s+id:\s*(\S.*)$")


def _case_blocks(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    starts = [index for index, line in enumerate(lines) if CASE_START_RE.match(line)]
    blocks: list[tuple[int, str]] = []
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(lines)
        blocks.append((start + 1, "\n".join(lines[start:end])))
    return blocks


def _has_nonempty_field(block: str, field: str) -> bool:
    pattern = re.compile(rf"^\s*{re.escape(field)}\s*:\s*(\S.*)$", re.MULTILINE)
    return bool(pattern.search(block))


def _field_value(block: str, field: str) -> str | None:
    pattern = re.compile(rf"^\s*{re.escape(field)}\s*:\s*(.*?)\s*$", re.MULTILINE)
    match = pattern.search(block)
    return match.group(1).strip() if match else None


def _has_any_nonempty_field(block: str, fields: tuple[str, ...]) -> bool:
    return any(_has_nonempty_field(block, field) for field in fields)


def validate_plan_text(text: str) -> list[str]:
    """Return deterministic validation errors for a YAML-like test plan."""

    errors: list[str] = []
    if not text.strip():
        return ["plan is empty"]
    if not _has_nonempty_field(text, "environment"):
        errors.append("top-level environment is required")
    if not re.search(r"^\s*cases\s*:", text, re.MULTILINE):
        errors.append("top-level cases section is required")

    blocks = _case_blocks(text)
    if not blocks:
        errors.append("at least one case with an id is required")
        return errors

    for line_number, block in blocks:
        case_id = _field_value(block, "id") or f"at line {line_number}"
        for field in REQUIRED_CASE_FIELDS:
            if field == "id":
                continue
            if not _has_nonempty_field(block, field):
                errors.append(f"{case_id}: missing non-empty field {field}")

        method = (_field_value(block, "method") or "").upper()
        path = (_field_value(block, "path") or "").lower()
        if not _has_nonempty_field(block, "method") or not _has_nonempty_field(block, "path"):
            errors.append(f"{case_id}: endpoint method and path are required")

        has_api_assertion = bool(re.search(r"^\s{4,}api\s*:", block, re.MULTILINE))
        has_db_assertion = bool(re.search(r"^\s{4,}database\s*:", block, re.MULTILINE))
        if not has_api_assertion and not has_db_assertion:
            errors.append(f"{case_id}: assertions must contain api or database")

        status = (_field_value(block, "executionStatus") or "").lower()
        if status and status not in VALID_STATUSES:
            errors.append(f"{case_id}: invalid executionStatus {status}")

        source = (_field_value(block, "source") or "").lower()
        if source and source not in VALID_SOURCES:
            errors.append(f"{case_id}: invalid source {source}")
        if source in {"provided", "merged"} and not re.search(
            r"^\s*userCaseId\s*:\s*(\S.*)$", block, re.MULTILINE
        ):
            errors.append(f"{case_id}: {source} case requires sourceEvidence.userCaseId")
        if source == "conflict":
            if status not in {"blocked", "skipped"}:
                errors.append(f"{case_id}: conflict case must be blocked or skipped")
            if not _has_any_nonempty_field(
                block, ("conflictReason", "blockerReason", "reason")
            ):
                errors.append(f"{case_id}: conflict case requires a conflict reason")

        is_mutating = method in MUTATING_METHODS or any(word in path for word in MUTATING_PATH_WORDS)
        confirmation = (_field_value(block, "requiresConfirmation") or "").lower()
        if is_mutating and confirmation != "true":
            errors.append(f"{case_id}: mutating case requires requiresConfirmation: true")

    return errors


def main(argv: list[str] | None = None) -> int:
    """Read one UTF-8 plan path, print errors, and return a process code."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        text = args.path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"unable to read plan: {exc}", file=sys.stderr)
        return 2

    errors = validate_plan_text(text)
    if errors:
        for error in errors:
            print(f"VALIDATION_ERROR: {error}")
        return 1
    print(f"VALID: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
