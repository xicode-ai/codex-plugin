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
        cases_value = item.get("cases")
        cases_text = ", ".join(str(case) for case in cases_value) if isinstance(cases_value, list) else _text(cases_value)
        rows.append(
            "| {id} | {goal} | {cases} | {status} |".format(
                id=_text(item.get("id") or item.get("requirementId")),
                goal=_text(item.get("goal") or item.get("summary")),
                cases=cases_text,
                status=_text(item.get("status")),
            )
        )
    return "\n".join(rows)


def _requirement_coverage(requirements: object) -> str:
    """Render covered/total requirement coverage with a percentage."""

    if not isinstance(requirements, list) or not requirements:
        return "-"
    total = len(requirements)
    covered = sum(
        1
        for item in requirements
        if isinstance(item, dict) and item.get("cases")
    )
    if total == 0:
        return "-"
    return f"{covered}/{total}（{round(covered / total * 100, 1)}%）"


def _pass_rate(counts: dict[str, Any]) -> str:
    total = counts.get("total", 0) or 0
    if not total:
        return "-"
    passed = counts.get("passed", 0) or 0
    return f"{round(passed / total * 100, 1)}%"


def _executive_summary(summary: dict[str, Any]) -> str:
    """Prefer an authored summary; otherwise derive one from the counts."""

    metadata = summary.get("metadata") or {}
    authored = metadata.get("executive_summary")
    if authored:
        return str(authored)
    counts = summary.get("counts") or {}
    conclusion = summary.get("conclusion") or {}
    total = counts.get("total", 0) or 0
    if not total:
        return "本次没有可执行的测试用例，详见阻塞与未执行项。"
    parts = [
        f"共 {total} 条用例：通过 {counts.get('passed', 0)}、失败 {counts.get('failed', 0)}、"
        f"阻塞 {counts.get('blocked', 0)}、跳过 {counts.get('skipped', 0)}、错误 {counts.get('errors', 0)}，"
        f"通过率 {_pass_rate(counts)}。"
    ]
    reason = conclusion.get("reason")
    if reason:
        parts.append(f"结论说明：{reason}")
    return "".join(parts)


def _source_counts(summary: dict[str, Any]) -> str:
    """Render provided/generated/merged/conflict counts."""

    source_counts = summary.get("sourceCounts") or {}
    return "\n".join(
        f"- {source}: {source_counts.get(source, 0)}"
        for source in ("provided", "generated", "merged", "conflict")
    )


def _user_case_traceability(cases: object) -> str:
    """Render user case ID, normalized case ID, requirements, endpoint, and status."""

    if not isinstance(cases, list):
        return "| - | - | - | - | - | - |"
    rows: list[str] = []
    for case in cases:
        if not isinstance(case, dict) or not case.get("userCaseId"):
            continue
        endpoint = case.get("endpoint") or {}
        endpoint_text = (
            f"{_text(endpoint.get('method'))} {_text(endpoint.get('path'))}"
            if isinstance(endpoint, dict)
            else _text(endpoint)
        )
        requirements = case.get("requirementRefs") or case.get("requirements") or "-"
        rows.append(
            "| {user} | {case_id} | {source} | {requirements} | {endpoint} | {status} |".format(
                user=_text(case.get("userCaseId")),
                case_id=_text(case.get("id")),
                source=_text(case.get("source")),
                requirements=_text(requirements),
                endpoint=endpoint_text,
                status=_text(case.get("executionStatus") or case.get("status")),
            )
        )
    return "\n".join(rows) or "| - | - | - | - | - | - |"


def _conflict_details(conflicts: object) -> str:
    """Render conflict case ID, source, reason, impact, and required confirmation."""

    if not isinstance(conflicts, list) or not conflicts:
        return "- None"
    rows: list[str] = []
    for conflict in conflicts:
        if isinstance(conflict, dict):
            case_id = conflict.get("caseId") or conflict.get("id") or "case"
            reason = conflict.get("reason") or conflict.get("conflictReason") or "-"
            impact = conflict.get("impact") or conflict.get("blockerReason") or "-"
            rows.append(f"- {case_id}: {reason}；影响：{impact}")
        else:
            rows.append(f"- {_text(conflict)}")
    return "\n".join(rows)


def _assertion_lines(assertions: object, empty: str = "  - -") -> str:
    """Render assertion objects as indented status lines."""

    if not isinstance(assertions, list) or not assertions:
        return empty
    rows: list[str] = []
    for assertion in assertions:
        if not isinstance(assertion, dict):
            rows.append(f"  - {_text(assertion)}")
            continue
        status = str(assertion.get("status") or "-").upper()
        expression = assertion.get("expression") or assertion.get("target") or "-"
        expected = assertion.get("expected")
        actual = assertion.get("actual")
        line = f"  - [{status}] {expression}"
        if expected is not None or actual is not None:
            line += f"（期望 {_text(expected)}，实际 {_text(actual)}）"
        rows.append(line)
    return "\n".join(rows)


def _apifox_resource(case: dict[str, Any]) -> str:
    apifox = case.get("apifox")
    if not isinstance(apifox, dict) or not any(apifox.values()):
        return "-"
    parts: list[str] = []
    if apifox.get("caseId"):
        parts.append(f"用例 #{apifox['caseId']}")
    if apifox.get("scenarioId"):
        parts.append(f"场景 #{apifox['scenarioId']}")
    if apifox.get("branch"):
        parts.append(f"分支 {apifox['branch']}")
    return " · ".join(parts) if parts else "-"


def _steps_block(steps: object) -> str:
    if not isinstance(steps, list) or not steps:
        return ""
    lines: list[str] = ["- 测试步骤："]
    for index, step in enumerate(steps, start=1):
        if isinstance(step, dict):
            text = step.get("action") or step.get("name") or step.get("description") or "-"
            detail = step.get("detail") or step.get("expected")
            if detail:
                text = f"{text}（{detail}）"
        else:
            text = str(step)
        lines.append(f"  {index}. {text}")
    return "\n".join(lines)


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
        assertions = case.get("assertions") if isinstance(case.get("assertions"), dict) else {}
        lines: list[str] = [
            f"### {_text(case.get('id'))} - {_text(case.get('businessFlow'))}",
            f"- 来源：{_text(case.get('source'))}",
            f"- 用户用例 ID：{_text(case.get('userCaseId'))}",
            f"- 分类：{_text(case.get('category'))}",
            f"- 接口：{endpoint_text}",
            f"- Apifox 资源：{_apifox_resource(case)}",
            f"- 前置条件：{_text(case.get('preconditions'))}",
        ]
        steps_block = _steps_block(case.get("steps"))
        if steps_block:
            lines.append(steps_block)
        lines.append(f"- 执行状态：{_text(case.get('executionStatus') or case.get('status'))}")
        if assertions.get("api"):
            lines.append("- API 断言：")
            lines.append(_assertion_lines(assertions.get("api")))
        else:
            lines.append(f"- API 结果：{_text(case.get('apiResult') or case.get('api'))}")
        if assertions.get("db"):
            lines.append("- DB 断言：")
            lines.append(_assertion_lines(assertions.get("db")))
        else:
            lines.append(f"- DB 结果：{_text(case.get('databaseResult') or case.get('database'))}")
        retry = _text(case.get("retry"), default="0")
        duration = case.get("durationMs")
        duration_text = f"{duration}ms" if duration not in (None, "") else "-"
        lines.append(f"- 重试：{retry}；耗时：{duration_text}")
        lines.append(f"- 脱敏证据：{_text(case.get('evidence'))}")
        lines.append(f"- 清理结果：{_text(case.get('cleanup'))}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)


def _defects(defects: object) -> str:
    """Render defects with severity, affected cases, and suggestions."""

    if not isinstance(defects, list) or not defects:
        return "- None"
    rows: list[str] = []
    for defect in defects:
        if not isinstance(defect, dict):
            rows.append(f"- {_text(defect)}")
            continue
        severity = str(defect.get("severity") or "UNKNOWN").upper()
        title = defect.get("title") or defect.get("id") or defect.get("caseId") or "defect"
        lines: list[str] = [f"- [{severity}] {title}"]
        detail_parts: list[str] = []
        if defect.get("description") or defect.get("reason"):
            detail_parts.append(_text(defect.get("description") or defect.get("reason")))
        if defect.get("caseRefs"):
            detail_parts.append(f"关联用例：{_text(defect.get('caseRefs'))}")
        if detail_parts:
            lines.append(f"  - { '；'.join(detail_parts) }")
        if defect.get("suggestion"):
            lines.append(f"  - 建议：{_text(defect.get('suggestion'))}")
        rows.append("\n".join(lines))
    return "\n".join(rows)


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
    api_passed = counts.get("api_assertions_passed", 0) or 0
    api_failed = counts.get("api_assertions_failed", 0) or 0
    db_passed = counts.get("db_assertions_passed", 0) or 0
    db_failed = counts.get("db_assertions_failed", 0) or 0
    context = {
        "task_name": metadata.get("task_name") or metadata.get("task") or "-",
        "prd_id": metadata.get("prd_id") or metadata.get("prd") or "-",
        "environment": metadata.get("environment") or "-",
        "execution_time": metadata.get("execution_time") or metadata.get("executed_at") or "-",
        "execution_duration": metadata.get("execution_duration") or "-",
        "project_name": metadata.get("project_name") or "-",
        "apifox_project_id": metadata.get("apifox_project_id") or "-",
        "apifox_branch": metadata.get("apifox_branch") or "-",
        "apifox_cli_version": metadata.get("apifox_cli_version") or "-",
        "plugin_version": metadata.get("plugin_version") or "-",
        "report_version": metadata.get("report_version") or "1.0",
        "generated_by": metadata.get("generated_by") or "apifox-api-testing plugin",
        "review_status": metadata.get("review_status") or "draft",
        "distribution_scope": metadata.get("distribution_scope") or "-",
        "apifox_preflight": metadata.get("apifox_preflight") or "-",
        "dbhub_preflight": metadata.get("dbhub_preflight") or "-",
        "total": counts.get("total", 0),
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "blocked": counts.get("blocked", 0),
        "skipped": counts.get("skipped", 0),
        "errors": counts.get("errors", 0),
        "pass_rate": _pass_rate(counts),
        "retries": counts.get("retries", 0),
        "api_assertions_passed": api_passed,
        "api_assertions_failed": api_failed,
        "db_assertions_passed": db_passed,
        "db_assertions_failed": db_failed,
        "assertions_passed_total": api_passed + db_passed,
        "assertions_failed_total": api_failed + db_failed,
        "conclusion_status": conclusion.get("status") or "BLOCKED",
        "conclusion_reason": conclusion.get("reason") or "-",
        "executive_summary": _executive_summary(summary),
        "prd_input_summary": metadata.get("prd_input_summary") or "provided",
        "provided_case_count": (summary.get("sourceCounts") or {}).get("provided", 0),
        "generated_case_count": (summary.get("sourceCounts") or {}).get("generated", 0),
        "merged_case_count": (summary.get("sourceCounts") or {}).get("merged", 0),
        "conflict_case_count": (summary.get("sourceCounts") or {}).get("conflict", 0),
        "source_counts": _source_counts(summary),
        "requirement_coverage": _requirement_coverage(summary.get("requirements")),
        "requirements_table": _requirements_table(summary.get("requirements")),
        "case_details": _case_details(cases),
        "user_case_traceability": _user_case_traceability(cases),
        "conflicts": _conflict_details(summary.get("conflicts")),
        "defects": _defects(summary.get("defects")),
        "blockers": _bullets(summary.get("blockers")),
        "cleanup": _bullets(summary.get("cleanup")),
        "recommendations": _bullets(summary.get("recommendations"))
        if summary.get("recommendations")
        else _text(conclusion.get("reason")),
        "tool_summary": metadata.get("tool_summary") or "-",
        "endpoint_index": _endpoint_index(cases),
        "database_index": metadata.get("database_index") or "-",
        "apifox_report_files": _bullets(metadata.get("apifox_report_files")),
        "rules_version": metadata.get("rules_version") or "0.2.0",
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
