#!/usr/bin/env python3
"""Offline-safe helpers and CLI for guarded Outline intent workflows.

The module uses only Python's standard library. Semantic review and approval
remain outside the runtime; its checks pin structure, inputs, and snapshots.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import ssl
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urlsplit
from urllib.request import (HTTPRedirectHandler, HTTPSHandler, Request,
                            build_opener)


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "outline-intent" / "config.json"
DEFAULT_PAGE_SIZE = 100
MAX_COMMENT_PAGES = 10000
READ_ONLY_METHODS = frozenset({
    "documents.info", "comments.info", "comments.list", "collections.info",
})
REQUIRED_H2 = (
    "目标 / Goal",
    "背景 / Background",
    "需求 / Requirements",
    "非目标 / Out of Scope",
    "验收标准 / Acceptance Criteria",
    "风险与边界情况 / Risks & Edge Cases",
    "发布与回滚说明 / Release / Rollback Notes",
    "参考资料 / References",
)
_UUID_SUFFIX = re.compile(r"(?:^|-)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})$")


class ConfigError(ValueError):
    """Configuration or URL error with no credential material in its text."""


@dataclass(frozen=True)
class RuntimeConfig:
    url: str = ""
    token: str = field(default="", repr=False)
    config_path: str = ""
    credential_origin_mismatch: bool = False

    def to_public_dict(self) -> dict[str, Any]:
        return {"url": self.url or None, "configured": bool(self.token)}


@dataclass(frozen=True)
class OutlineTarget:
    kind: str
    id: str
    url: str
    slug: str = ""


@dataclass
class ValidationReport:
    headings: list[str] = field(default_factory=list)
    structural_errors: list[str] = field(default_factory=list)
    content_errors: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[str]:
        return [*self.structural_errors, *self.content_errors]

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "headings": self.headings,
            "structural_errors": self.structural_errors,
            "content_errors": self.content_errors,
        }


def _origin_parts(value: str) -> tuple[str, str, Optional[int]]:
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise ConfigError("invalid URL") from exc
    if not parsed.scheme or not parsed.hostname:
        raise ConfigError("URL must include a scheme and host")
    scheme = parsed.scheme.lower()
    host = parsed.hostname.rstrip(".").lower()
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, host, port


def _is_local_host(host: str) -> bool:
    return host.lower().rstrip(".") in {"localhost", "127.0.0.1", "::1"}


def validate_base_url(url: str, *, allow_localhost_test: bool = False) -> str:
    if not isinstance(url, str) or not url.strip():
        raise ConfigError("OUTLINE_URL is required")
    value = url.strip()
    try:
        parsed = urlsplit(value)
        scheme, host, _ = _origin_parts(value)
    except ConfigError:
        raise
    if parsed.username or parsed.password:
        raise ConfigError("OUTLINE_URL must not contain user information")
    if parsed.query or parsed.fragment:
        raise ConfigError("OUTLINE_URL must not contain a query or fragment")
    if parsed.path not in ("", "/"):
        raise ConfigError("OUTLINE_URL must be an origin without a path")
    if scheme != "https" and not (allow_localhost_test and scheme == "http" and _is_local_host(host)):
        raise ConfigError("OUTLINE_URL must use HTTPS (HTTP is allowed only for explicit localhost tests)")
    if scheme not in {"https", "http"}:
        raise ConfigError("OUTLINE_URL must use HTTPS")
    if not host:
        raise ConfigError("OUTLINE_URL must include a host")
    # Keep the caller's explicit port while removing a harmless trailing slash.
    return value.rstrip("/")


def _same_origin(left: str, right: str) -> bool:
    try:
        return _origin_parts(left) == _origin_parts(right)
    except ConfigError:
        return False


def _config_value(data: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _write_config(path: Path, url: str, token: str) -> None:
    if not url or not token:
        raise ConfigError("cannot save incomplete configuration")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    fd, temporary = tempfile.mkstemp(prefix=".outline-intent-", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"url": url, "token": token}, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        os.chmod(path, 0o600)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def resolve_config(
    env: Optional[Mapping[str, str]] = None,
    config_path: Optional[os.PathLike[str] | str] = None,
    *,
    prompt: bool = False,
    save: bool = False,
    getpass_fn: Optional[Callable[[str], str]] = None,
) -> RuntimeConfig:
    """Resolve URL and API key without ever printing or implicitly saving them."""
    source_env = os.environ if env is None else env
    selected_path = Path(config_path or source_env.get("OUTLINE_CONFIG_FILE") or DEFAULT_CONFIG_PATH).expanduser()
    file_url = ""
    file_token = ""
    if selected_path.exists():
        try:
            payload = json.loads(selected_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError("Outline config file could not be read as JSON") from exc
        if not isinstance(payload, dict):
            raise ConfigError("Outline config file must contain a JSON object")
        file_url = _config_value(payload, "url", "base_url", "OUTLINE_URL")
        file_token = _config_value(payload, "token", "api_token", "OUTLINE_API_TOKEN")

    env_url = _config_value(source_env, "OUTLINE_URL")
    env_token = _config_value(source_env, "OUTLINE_API_TOKEN")
    url = env_url or file_url
    mismatch = bool(env_url and file_url and not _same_origin(env_url, file_url) and not env_token)
    token = env_token or ("" if mismatch else file_token)

    if prompt and url and not token:
        token_reader = getpass_fn or getpass.getpass
        token = token_reader("Outline API token: ").strip()
    if save:
        if not url:
            raise ConfigError("OUTLINE_URL is required before saving configuration")
        _write_config(selected_path, url, token)
    return RuntimeConfig(url=url, token=token, config_path=str(selected_path), credential_origin_mismatch=mismatch)


def check_config(config: RuntimeConfig, *, allow_localhost_test: bool = False) -> list[str]:
    errors: list[str] = []
    if not config.url:
        errors.append("OUTLINE_URL is missing")
    else:
        try:
            validate_base_url(config.url, allow_localhost_test=allow_localhost_test)
        except ConfigError as exc:
            errors.append(str(exc))
    if config.credential_origin_mismatch:
        errors.append("OUTLINE_API_TOKEN must match the selected OUTLINE_URL deployment")
    elif not config.token:
        errors.append("OUTLINE_API_TOKEN is missing")
    return errors


def parse_target(
    url: str,
    base_url: str,
    *,
    allow_localhost_test: bool = False,
) -> OutlineTarget:
    origin = validate_base_url(base_url, allow_localhost_test=allow_localhost_test)
    try:
        parsed = urlsplit(url)
        _origin_parts(url)
    except ConfigError as exc:
        raise ConfigError("target URL is invalid") from exc
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigError("target URL must not contain credentials, query, or fragment")
    if not _same_origin(origin, url):
        raise ConfigError("target URL must be on the configured Outline origin")
    decoded_path = unquote(parsed.path)
    if "%2f" in parsed.path.lower() or "%5c" in parsed.path.lower():
        raise ConfigError("target URL contains an encoded path separator")
    match = re.fullmatch(r"/(doc|collection)/([^/]+)", decoded_path)
    if not match:
        raise ConfigError("target must be an Outline /doc/slug-id or /collection/slug-id URL")
    route, slug_id = match.groups()
    id_match = _UUID_SUFFIX.search(slug_id)
    if not id_match:
        raise ConfigError("target URL does not end with an Outline UUID")
    item_id = id_match.group(1).lower()
    try:
        item_id = str(uuid.UUID(item_id))
    except ValueError as exc:
        raise ConfigError("target URL does not contain a valid Outline UUID") from exc
    slug = slug_id[:id_match.start(1)].rstrip("-")
    if not slug or slug in {".", ".."}:
        raise ConfigError("target URL must include a slug before its UUID")
    return OutlineTarget("document" if route == "doc" else "collection", item_id, url, slug)


def default_template_path() -> Path:
    module = Path(__file__).resolve()
    candidates = (
        module.with_name("intent-template.md"),
        module.parent / "assets" / "intent-template.md",
        module.parent.parent / "assets" / "intent-template.md",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise ConfigError("bundled intent template is missing")


def _read_template(template_path: Optional[os.PathLike[str] | str]) -> str:
    path = Path(template_path) if template_path else default_template_path()
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError("intent template could not be read") from exc


def _visible_lines(markdown: str) -> tuple[list[str], bool]:
    """Return lines outside fenced blocks and whether the final fence was closed."""
    visible: list[str] = []
    fence_char: Optional[str] = None
    fence_size = 0
    for line in markdown.splitlines():
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence_char is None and match:
            fence_char = match.group(1)[0]
            fence_size = len(match.group(1))
            continue
        if fence_char is not None:
            close = re.match(r"^\s{0,3}(`+|~+)\s*$", line)
            if close and close.group(1)[0] == fence_char and len(close.group(1)) >= fence_size:
                fence_char = None
                fence_size = 0
            continue
        visible.append(line)
    return visible, fence_char is None


def _h2_headings(markdown: str) -> tuple[list[str], list[str]]:
    lines, fence_closed = _visible_lines(markdown)
    headings: list[str] = []
    for line in lines:
        match = re.match(r"^\s{0,3}##[ \t]+(.+?)\s*#*\s*$", line)
        if match:
            headings.append(match.group(1).strip())
    errors = [] if fence_closed else ["Markdown contains an unclosed fenced code block"]
    return headings, errors


def _sections(markdown: str) -> tuple[list[tuple[str, str]], list[str], list[str]]:
    lines, fence_closed = _visible_lines(markdown)
    headings: list[str] = []
    found: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s{0,3}##[ \t]+(.+?)\s*#*\s*$", line)
        if match:
            title = match.group(1).strip()
            headings.append(title)
            found.append((index, title))
    chunks: list[tuple[str, str]] = []
    for position, (line_index, title) in enumerate(found):
        end = found[position + 1][0] if position + 1 < len(found) else len(lines)
        chunks.append((title, "\n".join(lines[line_index + 1:end])))
    errors = [] if fence_closed else ["Markdown contains an unclosed fenced code block"]
    return chunks, headings, errors


def _clean_markdown_line(line: str) -> str:
    value = re.sub(r"</?mark\b[^>]*>", "", line, flags=re.IGNORECASE)
    value = re.sub(r"<[^>]+>", " ", value)
    value = value.strip()
    value = re.sub(r"^\s*>\s?", "", value)
    value = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)", "", value)
    value = re.sub(r"^\s*\[[ xX]\]\s*", "", value)
    value = value.replace("**", "").replace("__", "").replace("`", "")
    return value.strip()


_PLACEHOLDER = re.compile(
    r"^(?:[-*+.:：\s]|\[\s*\])*(?:todo|tbd|待补充|待填写|待完善|请填写|请补充|填写|补充|需求\s*\d+|明确不做什么|无|n/?a|none|\.\.\.)*(?:[-*+.:：\s]|\[\s*\])*$",
    re.IGNORECASE,
)


def _has_substantive_value(value: str) -> bool:
    clean = _clean_markdown_line(value)
    if not clean:
        return False
    if re.search(r"\b(?:TODO|TBD)\b|待补充|待填写|待完善|请填写|请补充", clean, re.IGNORECASE):
        return False
    if _PLACEHOLDER.fullmatch(clean):
        return False
    if "不适用" in clean:
        remainder = re.sub(r"不适用\s*[:：]?", "", clean).strip(" \t-—:：，,。；;()（）")
        if len(re.sub(r"\W", "", remainder, flags=re.UNICODE)) < 3:
            return False
    if "未验证" in clean and not re.search(r"验证|核实|确认|补齐|计划|后续|路径|负责人", clean):
        return False
    return len(re.sub(r"\W", "", clean, flags=re.UNICODE)) >= 2


def _section_map(chunks: list[tuple[str, str]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for title, body in chunks:
        if title in result:
            continue
        result[title] = body
    return result


def _field_value(body: str, aliases: tuple[str, ...]) -> Optional[str]:
    lines, _ = _visible_lines(body)
    cleaned = [_clean_markdown_line(line) for line in lines]
    alias_patterns = [re.compile(r"^\s*" + re.escape(alias) + r"\s*[:：]\s*(.*)$", re.IGNORECASE) for alias in aliases]
    for index, line in enumerate(cleaned):
        match = next((pattern.match(line) for pattern in alias_patterns if pattern.match(line)), None)
        if not match:
            continue
        parts = [match.group(1).strip()]
        for follow in cleaned[index + 1:]:
            if not follow:
                if parts[-1]:
                    break
                continue
            if follow.startswith("#") or any(pattern.match(follow) for pattern in alias_patterns):
                break
            # Stop when a different bold/colon field begins.
            if re.match(r"^[^:：]{1,60}\s*[:：]", follow):
                break
            parts.append(follow)
        return " ".join(part for part in parts if part).strip()
    return None


def _heading_body(section: str, heading: str) -> Optional[str]:
    lines, _ = _visible_lines(section)
    starts = []
    for index, line in enumerate(lines):
        match = re.match(r"^\s{0,3}#{3,6}[ \t]+(.+?)\s*#*\s*$", line)
        if match:
            starts.append((index, match.group(1).strip()))
    for position, (line_index, title) in enumerate(starts):
        if title.casefold() != heading.casefold():
            continue
        end = starts[position + 1][0] if position + 1 < len(starts) else len(lines)
        return "\n".join(lines[line_index + 1:end])
    return None


def _check_fields(
    report: ValidationReport,
    section_name: str,
    body: str,
    fields: tuple[tuple[str, ...], ...],
) -> None:
    for aliases in fields:
        value = _field_value(body, aliases)
        label = aliases[0]
        if value is None:
            report.content_errors.append(f"{section_name}: required field '{label}' is missing")
        elif not _has_substantive_value(value):
            report.content_errors.append(f"{section_name}: required field '{label}' is blank or placeholder-only")


BACKGROUND_FIELDS = (
    ("现状与问题",),
    ("为什么需要这个功能", "为什么需要"),
    ("系统/模块", "系统 / 模块"),
    ("目标用户 / 业务角色", "目标用户", "业务角色"),
    ("影响的业务对象 / 范围", "影响的业务对象", "范围"),
)
NONFUNCTIONAL_FIELDS = (("性能",), ("安全",), ("兼容性",), ("数据 / 权限约束", "数据/权限约束"))
RISK_FIELDS = (("风险点",), ("边界情况",), ("异常流 / 失败场景", "异常流/失败场景"), ("回滚 / 降级约束", "回滚/降级约束"))
RELEASE_FIELDS = (
    ("是否需要灰度 / 分批发布", "是否需要灰度/分批发布"),
    ("是否有发布时间窗口",),
    ("失败后的回退方式",),
)
REFERENCE_FIELDS = (
    ("Intent / PRD", "Intent/PRD"),
    ("设计文档",),
    ("Work Item / Issue", "Work Item/Issue"),
    ("Outline / 现有资料", "Outline/现有资料"),
    ("相关仓库 / 模块", "相关仓库/模块"),
)


def validate_template(
    markdown: str,
    *,
    template_text: Optional[str] = None,
    template_path: Optional[os.PathLike[str] | str] = None,
) -> ValidationReport:
    """Check the pinned eight-section structure and required, non-scaffold content."""
    report = ValidationReport()
    if not isinstance(markdown, str):
        report.structural_errors.append("document content must be Markdown text")
        return report
    if template_text is None:
        try:
            template_text = _read_template(template_path)
        except ConfigError as exc:
            report.structural_errors.append(str(exc))
            return report
    template_headings, template_errors = _h2_headings(template_text)
    if template_errors:
        report.structural_errors.extend(template_errors)
    if tuple(template_headings) != REQUIRED_H2:
        report.structural_errors.append("bundled template H2 headings do not match the approved eight-section order")

    chunks, headings, parse_errors = _sections(markdown)
    report.headings = headings
    report.structural_errors.extend(parse_errors)
    seen: set[str] = set()
    for heading in headings:
        if heading not in REQUIRED_H2:
            report.structural_errors.append(f"unexpected H2 heading: {heading}")
        if heading in seen:
            report.structural_errors.append(f"duplicate H2 heading: {heading}")
        seen.add(heading)
    if tuple(headings) != REQUIRED_H2:
        missing = [heading for heading in REQUIRED_H2 if heading not in seen]
        if missing:
            report.structural_errors.append("missing H2 headings: " + ", ".join(missing))
        expected_index = {heading: index for index, heading in enumerate(REQUIRED_H2)}
        known_order = [expected_index[heading] for heading in headings if heading in expected_index]
        if known_order != sorted(known_order):
            report.structural_errors.append("H2 headings are not in the approved order")

    by_heading = _section_map(chunks)
    for required in REQUIRED_H2:
        body = by_heading.get(required)
        if body is None:
            continue
        if not _has_substantive_value(body):
            report.content_errors.append(f"{required}: section is empty or contains only scaffolding/placeholders")

    _check_fields(report, "Background", by_heading.get(REQUIRED_H2[1], ""), BACKGROUND_FIELDS)
    requirements = by_heading.get(REQUIRED_H2[2], "")
    functional = _heading_body(requirements, "功能需求")
    nonfunctional = _heading_body(requirements, "非功能需求")
    if functional is None:
        report.content_errors.append("Requirements: required subsection '功能需求' is missing")
    elif not _has_substantive_value(functional):
        report.content_errors.append("Requirements: '功能需求' is blank or placeholder-only")
    if nonfunctional is None:
        report.content_errors.append("Requirements: required subsection '非功能需求' is missing")
    else:
        _check_fields(report, "Requirements / 非功能需求", nonfunctional, NONFUNCTIONAL_FIELDS)

    acceptance = by_heading.get(REQUIRED_H2[4], "")
    acceptance_visible, _ = _visible_lines(acceptance)
    acceptance_text = "\n".join(acceptance_visible)
    for marker in ("Given", "When", "Then"):
        if not re.search(r"\b" + marker + r"\b", acceptance_text, re.IGNORECASE):
            report.content_errors.append(f"Acceptance Criteria: required Given/When/Then marker '{marker}' is missing")

    _check_fields(report, "Risks & Edge Cases", by_heading.get(REQUIRED_H2[5], ""), RISK_FIELDS)
    _check_fields(report, "Release / Rollback Notes", by_heading.get(REQUIRED_H2[6], ""), RELEASE_FIELDS)
    _check_fields(report, "References", by_heading.get(REQUIRED_H2[7], ""), REFERENCE_FIELDS)
    return report


