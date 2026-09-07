#!/usr/bin/env python3
"""Render a standard Markdown report from an execution summary JSON file."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

from redact_evidence import redact_text


def load_summary(path: Path) -> dict[str, Any]:
    """Load and validate the summary JSON object."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("summary JSON must contain an object")
    return payload


def _text(value: object, default: str = "-") -> str:
    if value is None or value == "":
        return default
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _bullets(items: object, empty: str = "- None") -> str:
    if not isinstance(items, list) or not items:
        return empty
    rows: list[str] = []
    for item in items:
        if isinstance(item, dict):
            label = item.get("id") or item.get("title") or item.get("caseId") or "item"
            detail = item.get("reason") or item.get("summary") or item.get("status") or item
            rows.append(f"- {label}: {_text(detail)}")
        else:
            rows.append(f"- {_text(item)}")
    return "\n".join(rows)


def _requirements_table(requirements: object) -> str:
    if not isinstance(requirements, list) or not requirements:
        return "| - | - | - | - |"
    rows: list[str] = []
    for item in requirements:
        if not isinstance(item, dict):
            rows.append(f"| {_text(item)} | - | - | - |")
            continue
        rows.append(
            "| {id} | {goal} | {cases} | {status} |".format(
                id=_text(item.get("id") or item.get("requirementId")),
                goal=_text(item.get("goal") or item.get("summary")),
                cases=_text(item.get("cases")),
                status=_text(item.get("status")),
            )
        )
    return "\n".join(rows)


def _case_details(cases: object) -> str:
    if not isinstance(cases, list) or not cases:
        return "- No cases were executed."
    sections: list[str] = []
    for case in cases:
        if not isinstance(case, dict):
            sections.append(f"- {_text(case)}")
            continue
        endpoint = case.get("endpoint") or {}
        endpoint_text = (
            f"{_text(endpoint.get('method'))} {_text(endpoint.get('path'))}"
            if isinstance(endpoint, dict)
            else _text(endpoint)
        )
        sections.append(
            "\n".join(
                [
                    f"### {_text(case.get('id'))} - {_text(case.get('businessFlow'))}",
                    f"- 分类：{_text(case.get('category'))}",
                    f"- 接口：{endpoint_text}",
                    f"- 执行状态：{_text(case.get('executionStatus') or case.get('status'))}",
                    f"- API 结果：{_text(case.get('apiResult') or case.get('api'))}",
                    f"- DB 结果：{_text(case.get('databaseResult') or case.get('database'))}",
                    f"- 脱敏证据：{_text(case.get('evidence'))}",
                    f"- 清理结果：{_text(case.get('cleanup'))}",
                ]
            )
        )
    return "\n\n".join(sections)


def _endpoint_index(cases: object) -> str:
    if not isinstance(cases, list):
        return "-"
    values: list[str] = []
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("endpoint"), dict):
            continue
        endpoint = case["endpoint"]
        value = f"{_text(endpoint.get('method'))} {_text(endpoint.get('path'))}"
        if value not in values:
            values.append(value)
    return ", ".join(values) or "-"


def render_report(summary: dict[str, Any], template_text: str) -> str:
    """Render fixed headings and summary data while preserving the template sections."""

    metadata = summary.get("metadata") or {}
    counts = summary.get("counts") or {}
    conclusion = summary.get("conclusion") or {}
    cases = summary.get("cases") or []
    context = {
        "task_name": metadata.get("task_name") or metadata.get("task") or "-",
        "prd_id": metadata.get("prd_id") or metadata.get("prd") or "-",
        "environment": metadata.get("environment") or "-",
        "execution_time": metadata.get("execution_time") or metadata.get("executed_at") or "-",
        "apifox_preflight": metadata.get("apifox_preflight") or "-",
        "dbhub_preflight": metadata.get("dbhub_preflight") or "-",
        "total": counts.get("total", 0),
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "blocked": counts.get("blocked", 0),
        "skipped": counts.get("skipped", 0),
        "errors": counts.get("errors", 0),
        "api_assertions_passed": counts.get("api_assertions_passed", 0),
        "api_assertions_failed": counts.get("api_assertions_failed", 0),
        "db_assertions_passed": counts.get("db_assertions_passed", 0),
        "db_assertions_failed": counts.get("db_assertions_failed", 0),
        "conclusion_status": conclusion.get("status") or "BLOCKED",
        "conclusion_reason": conclusion.get("reason") or "-",
        "requirements_table": _requirements_table(summary.get("requirements")),
        "case_details": _case_details(cases),
        "defects": _bullets(summary.get("defects")),
        "blockers": _bullets(summary.get("blockers")),
        "cleanup": _bullets(summary.get("cleanup")),
        "recommendations": _bullets(summary.get("recommendations"))
        if summary.get("recommendations")
        else _text(conclusion.get("reason")),
        "tool_summary": metadata.get("tool_summary") or "-",
        "endpoint_index": _endpoint_index(cases),
        "database_index": metadata.get("database_index") or "-",
        "rules_version": metadata.get("rules_version") or "0.1.0",
    }
    rendered = re.sub(
        r"\{\{([A-Za-z0-9_]+)\}\}",
        lambda match: _text(context.get(match.group(1))),
        template_text,
    )
    return redact_text(rendered)


def main(argv: list[str] | None = None) -> int:
    """Render to stdout or an explicitly requested output path."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary", type=Path)
    parser.add_argument("--template", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    template_path = args.template or Path(__file__).resolve().parents[1] / "templates" / "test-report.md"
    try:
        summary = load_summary(args.summary)
        template = template_path.read_text(encoding="utf-8")
        report = render_report(summary, template)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"unable to render report: {exc}", file=sys.stderr)
        return 2

    if args.output:
        try:
            args.output.write_text(report, encoding="utf-8")
        except OSError as exc:
            print(f"unable to write report: {exc}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
