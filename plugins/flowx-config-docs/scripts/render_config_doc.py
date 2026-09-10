"""Render normalized FlowX configuration data as deterministic Markdown."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


DICTIONARY_FIELDS = ("operation", "dict_code", "dict_key", "dict_value", "en", "ja", "ko", "es", "pt")
MENU_FIELDS = ("level1", "level2", "button_or_button_menu", "menu_code", "route", "en", "ja", "ko", "es", "pt", "sort", "authorization", "resource", "operation_type")
I18N_FIELDS = ("operation_type", "level1_key", "level2_key", "zh", "en", "ja", "ko", "es", "pt")
TEMPLATE_TOKEN_RE = re.compile(
    r"\{\{(DATABASE|SCOPE|GENERATED_AT|QUERY_STATUS|DICTIONARY_NOTES|DICTIONARY_TABLE|MENUS_NOTES|MENUS_TABLE|I18N_NOTES|I18N_TABLE|TRANSLATION_SUMMARY)\}\}"
)


def _cell(value: Any) -> str:
    if value is None or value == "":
        return ""
    text = str(value).replace("\r\n", "\n").replace("\r", "\n")
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "<br>")


def _table_rows(rows: object, fields: tuple[str, ...]) -> str:
    if not isinstance(rows, list):
        return ""
    rendered: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            row = {}
        rendered.append("| " + " | ".join(_cell(row.get(field)) for field in fields) + " |")
    return "\n".join(rendered)


def _notes(items: object) -> str:
    if not isinstance(items, list):
        return ""
    return "\n".join("> " + _cell(item) for item in items if item not in (None, ""))


def _translation_summary(value: object) -> str:
    sources = value if isinstance(value, dict) else {}
    return "数据库已有翻译：{}；插件自动翻译：{}；翻译待人工确认：{}".format(
        sources.get("database", 0), sources.get("automatic", 0), sources.get("pending", 0)
    )


def load_payload(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        payload = json.load(stream)
    if not isinstance(payload, dict):
        raise ValueError("payload must be a JSON object")
    return payload


def render_config_document(payload: dict[str, Any], template_text: str) -> str:
    metadata = payload.get("metadata", {})
    metadata = metadata if isinstance(metadata, dict) else {}
    notices = payload.get("notices", {})
    notices = notices if isinstance(notices, dict) else {}
    replacements = {
        "DATABASE": _cell(metadata.get("database")),
        "SCOPE": _cell(metadata.get("scope")),
        "GENERATED_AT": _cell(metadata.get("generated_at")),
        "QUERY_STATUS": _cell(metadata.get("query_status")),
        "DICTIONARY_NOTES": _notes(notices.get("dictionary", [])),
        "DICTIONARY_TABLE": _table_rows(payload.get("dictionary", []), DICTIONARY_FIELDS),
        "MENUS_NOTES": _notes(notices.get("menus", [])),
        "MENUS_TABLE": _table_rows(payload.get("menus", []), MENU_FIELDS),
        "I18N_NOTES": _notes(notices.get("i18n", [])),
        "I18N_TABLE": _table_rows(payload.get("i18n", []), I18N_FIELDS),
        "TRANSLATION_SUMMARY": _translation_summary(payload.get("translation_sources", {})),
    }
    return TEMPLATE_TOKEN_RE.sub(
        lambda match: replacements[match.group(1)], template_text
    ).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_path", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        payload = load_payload(args.json_path)
        template_path = args.template or Path(__file__).resolve().parent.parent / "templates" / "flowx-config-docs.md"
        report = render_config_document(payload, template_path.read_text(encoding="utf-8"))
        if args.output:
            args.output.write_text(report, encoding="utf-8")
        else:
            sys.stdout.write(report)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
