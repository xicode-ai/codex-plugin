#!/usr/bin/env python3
"""Validate a normalized FlowX configuration payload before rendering."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


REQUIRED_SECTIONS = (
    "metadata",
    "dictionary",
    "menus",
    "i18n",
    "notices",
    "translation_sources",
)
SECTION_FIELDS = {
    "dictionary": (
        "operation",
        "dict_code",
        "dict_key",
        "dict_value",
        "en",
        "ja",
        "ko",
        "es",
        "pt",
    ),
    "menus": (
        "level1",
        "level2",
        "button_or_button_menu",
        "menu_code",
        "route",
        "en",
        "ja",
        "ko",
        "es",
        "pt",
        "sort",
        "authorization",
        "resource",
        "operation_type",
    ),
    "i18n": (
        "operation_type",
        "level1_key",
        "level2_key",
        "zh",
        "en",
        "ja",
        "ko",
        "es",
        "pt",
    ),
}
NONEMPTY_FIELDS = {
    "dictionary": ("dict_code", "dict_key", "dict_value"),
    "menus": ("menu_code",),
    "i18n": ("level1_key", "level2_key", "zh"),
}
LANGUAGE_FIELDS = ("en", "ja", "ko", "es", "pt")
PROTECTED_FIELDS = {
    "dictionary": ("operation", "dict_code", "dict_key", "dict_value"),
    "menus": (
        "level1",
        "level2",
        "button_or_button_menu",
        "menu_code",
        "route",
        "sort",
        "authorization",
        "resource",
        "operation_type",
    ),
    "i18n": ("operation_type", "level1_key", "level2_key", "zh"),
}
PLACEHOLDER_RE = re.compile(
    r"\{\{[^{}]+\}\}|\$\{[^{}]+\}|\{[A-Za-z_][A-Za-z0-9_.-]*\}|:[A-Za-z_][A-Za-z0-9_.-]*"
)
SENSITIVE_KEY_RE = re.compile(
    r"(?:password|passwd|token|cookie|secret|credential|connection|string|dsn)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_RE = re.compile(
    r"(?:Bearer\s+\S+|Basic\s+\S+|Digest\s+\S+|jdbc:[^\s]+|(?:mysql|postgres(?:ql)?|mongodb)://[^\s]+|(?:password|passwd|token|secret|cookie|authorization)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
QUERY_STATUSES = {"complete", "empty", "ambiguous", "blocked", "unresolved"}


def _is_nonempty(value: object) -> bool:
    return value is not None and value != ""


def _placeholders(value: object) -> set[str]:
    if value is None:
        return set()
    return set(PLACEHOLDER_RE.findall(str(value)))


def _scalar_items(value: object, path: str = ""):
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else str(key)
            yield from _scalar_items(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _scalar_items(child, f"{path}[{index}]")
    else:
        yield path, value


def _placeholder_errors(section: str, index: int, row: dict[str, Any]) -> list[str]:
    if section == "dictionary":
        source = row.get("dict_value")
        source_label = "dict_value"
    elif section == "i18n":
        source = row.get("zh")
        source_label = "zh"
    else:
        source = "".join(str(row.get(field) or "") for field in (
            "level1",
            "level2",
            "button_or_button_menu",
        ))
        source_label = "menu labels"

    expected = _placeholders(source)
    errors: list[str] = []
    for language in LANGUAGE_FIELDS:
        value = row.get(language)
        if not _is_nonempty(value):
            continue
        actual = _placeholders(value)
        if actual != expected:
            errors.append(
                f"{section}[{index}] {language} placeholder set differs from {source_label}"
            )
    return errors


def _source_baseline_errors(section: str, index: int, row: dict[str, Any]) -> list[str]:
    source = row.get("_source")
    if not isinstance(source, dict):
        return [f"{section}[{index}] missing source snapshot"]

    errors: list[str] = []
    for field in SECTION_FIELDS[section]:
        if field not in source:
            errors.append(f"{section}[{index}] source snapshot missing field {field}")
            continue
        original = source.get(field)
        current = row.get(field)
        if field in PROTECTED_FIELDS[section] and current != original:
            errors.append(f"{section}[{index}] protected field changed: {field}")
        elif field in LANGUAGE_FIELDS and _is_nonempty(original) and current != original:
            errors.append(f"{section}[{index}] existing translation changed: {field}")
    return errors


def validate_config_payload(payload: dict[str, Any]) -> list[str]:
    """Return deterministic validation errors without exposing sensitive values."""

    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["payload must be an object"]

    for section in REQUIRED_SECTIONS:
        if section not in payload:
            errors.append(f"missing top-level section {section}")

    metadata = payload.get("metadata")
    if not isinstance(metadata, dict):
        errors.append("metadata must be an object")
        metadata = {}
    for field in ("database", "scope", "generated_at", "query_status"):
        if not _is_nonempty(metadata.get(field)):
            errors.append(f"metadata.{field} is required")
    query_status = metadata.get("query_status", "")
    if query_status and (
        not isinstance(query_status, str) or query_status not in QUERY_STATUSES
    ):
        errors.append("metadata.query_status has invalid value")

    for section, fields in SECTION_FIELDS.items():
        rows = payload.get(section)
        if not isinstance(rows, list):
            errors.append(f"{section} must be a list")
            continue
        for index, row in enumerate(rows):
            if not isinstance(row, dict):
                errors.append(f"{section}[{index}] must be an object")
                continue
            for field in fields:
                if field not in row or (
                    field in NONEMPTY_FIELDS[section] and not _is_nonempty(row.get(field))
                ):
                    errors.append(f"{section}[{index}] missing field {field}")
            errors.extend(_source_baseline_errors(section, index, row))
            errors.extend(_placeholder_errors(section, index, row))

    notices = payload.get("notices")
    if not isinstance(notices, dict):
        errors.append("notices must be an object")
    else:
        for section in ("dictionary", "menus", "i18n"):
            if not isinstance(notices.get(section), list):
                errors.append(f"notices.{section} must be a list")

    translation_sources = payload.get("translation_sources")
    if not isinstance(translation_sources, dict):
        errors.append("translation_sources must be an object")
    else:
        pending = translation_sources.get("pending")
        for source in ("database", "automatic", "pending"):
            value = translation_sources.get(source)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                errors.append(f"translation_sources.{source} must be a non-negative integer")
        if isinstance(pending, int) and pending > 0:
            notice_values = [
                notice
                for section in ("dictionary", "menus", "i18n")
                for notice in notices.get(section, [])
            ] if isinstance(notices, dict) else []
            if not any("翻译待人工确认" in str(notice) for notice in notice_values):
                errors.append("translation pending requires a 翻译待人工确认 notice")

    for path, value in _scalar_items(payload):
        key = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if SENSITIVE_KEY_RE.search(key):
            errors.append(f"sensitive field name at {path}")
        if isinstance(value, str) and SENSITIVE_VALUE_RE.search(value):
            errors.append(f"sensitive value pattern at {path}")
    return errors


def main(argv: list[str] | None = None) -> int:
    """Validate one UTF-8 JSON payload path."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"unable to read payload: {exc}", file=sys.stderr)
        return 2

    errors = validate_config_payload(payload)
    if errors:
        for error in errors:
            print(f"VALIDATION_ERROR: {error}")
        return 1
    print(f"VALID: {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
