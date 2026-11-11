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
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

try:
    from .plan_engine import OutlineIntentError
except ImportError:
    from plan_engine import OutlineIntentError


DEFAULT_CONFIG_PATH = Path.home() / ".config" / "outline-intent" / "config.json"
DEFAULT_PAGE_SIZE = 100
MAX_COMMENT_PAGES = 10000
READ_ONLY_METHODS = frozenset(
    {
        "documents.info",
        "comments.info",
        "comments.list",
        "collections.info",
    }
)
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
_UUID_SUFFIX = re.compile(
    r"(?:^|-)([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[1-8][0-9a-fA-F]{3}-[89abAB][0-9a-fA-F]{3}-[0-9a-fA-F]{12}|[0-9a-fA-F]{32})$"
)


class ConfigError(OutlineIntentError, ValueError):
    """Configuration or URL error with no credential material in its text."""


def _safe_verified_capabilities(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    last_revision = value.get("last_revision") is True
    evidence = value.get("evidence")
    if not last_revision or not isinstance(evidence, str) or not evidence.strip():
        return {}
    return {"last_revision": True, "evidence": evidence.strip()}


@dataclass(frozen=True)
class RuntimeConfig:
    url: str = ""
    token: str = field(default="", repr=False)
    config_path: str = ""
    credential_origin_mismatch: bool = False
    verified_capabilities: Mapping[str, Any] = field(default_factory=dict, repr=False)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "url": self.url or None,
            "configured": bool(self.token),
            "verified_capabilities": _safe_verified_capabilities(
                self.verified_capabilities
            ),
        }


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
    if scheme != "https" and not (
        allow_localhost_test and scheme == "http" and _is_local_host(host)
    ):
        raise ConfigError(
            "OUTLINE_URL must use HTTPS (HTTP is allowed only for explicit localhost tests)"
        )
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


def _write_config(
    path: Path,
    url: str,
    token: str,
    *,
    verified_capabilities: Optional[Mapping[str, Any]] = None,
) -> None:
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
            config = {"url": url, "token": token}
            capabilities = _safe_verified_capabilities(verified_capabilities)
            if capabilities:
                config["verified_capabilities"] = capabilities
            json.dump(config, stream, ensure_ascii=False, indent=2)
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
    selected_path = Path(
        config_path or source_env.get("OUTLINE_CONFIG_FILE") or DEFAULT_CONFIG_PATH
    ).expanduser()
    file_url = ""
    file_token = ""
    file_capabilities: Mapping[str, Any] = {}
    file_capability_origin = ""
    if selected_path.exists():
        try:
            payload = json.loads(selected_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError("Outline config file could not be read as JSON") from exc
        if not isinstance(payload, dict):
            raise ConfigError("Outline config file must contain a JSON object")
        file_url = _config_value(payload, "url", "base_url", "OUTLINE_URL")
        file_token = _config_value(payload, "token", "api_token", "OUTLINE_API_TOKEN")
        file_capabilities = _safe_verified_capabilities(
            payload.get("verified_capabilities")
        )
        file_capability_origin = (
            _config_value(payload, "verified_capabilities_origin", "capability_origin")
            or file_url
        )

    env_url = _config_value(source_env, "OUTLINE_URL")
    env_token = _config_value(source_env, "OUTLINE_API_TOKEN")
    url = env_url or file_url
    mismatch = bool(
        env_url and file_url and not _same_origin(env_url, file_url) and not env_token
    )
    token = env_token or ("" if mismatch else file_token)
    capabilities = file_capabilities
    if (
        not file_capability_origin
        or not url
        or not _same_origin(file_capability_origin, url)
    ):
        capabilities = {}

    if prompt and url and not token:
        token_reader = getpass_fn or getpass.getpass
        token = token_reader("Outline API token: ").strip()
        if token:
            # A freshly entered token is an explicit credential for the selected URL.
            mismatch = False
    if save:
        if not url:
            raise ConfigError("OUTLINE_URL is required before saving configuration")
        _write_config(selected_path, url, token, verified_capabilities=capabilities)
    return RuntimeConfig(
        url=url,
        token=token,
        config_path=str(selected_path),
        credential_origin_mismatch=mismatch,
        verified_capabilities=capabilities,
    )


def check_config(
    config: RuntimeConfig, *, allow_localhost_test: bool = False
) -> list[str]:
    errors: list[str] = []
    if not config.url:
        errors.append("OUTLINE_URL")
    else:
        try:
            validate_base_url(config.url, allow_localhost_test=allow_localhost_test)
        except ConfigError as exc:
            errors.append(str(exc))
    if config.credential_origin_mismatch:
        errors.append(
            "OUTLINE_API_TOKEN must match the selected OUTLINE_URL deployment"
        )
    elif not config.token:
        errors.append("OUTLINE_API_TOKEN")
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
    if parsed.username or parsed.password or parsed.query:
        raise ConfigError("target URL must not contain credentials or query")
    if not _same_origin(origin, url):
        raise ConfigError("target URL must be on the configured Outline origin")
    decoded_path = unquote(parsed.path)
    if "%2f" in parsed.path.lower() or "%5c" in parsed.path.lower():
        raise ConfigError("target URL contains an encoded path separator")
    match = re.fullmatch(r"/(doc|collection)/([^/]+)", decoded_path)
    if not match:
        raise ConfigError(
            "target must be an Outline /doc/slug-id or /collection/slug-id URL"
        )
    route, slug_id = match.groups()
    id_match = _UUID_SUFFIX.search(slug_id)
    if id_match:
        item_id = id_match.group(1).lower()
        try:
            item_id = str(uuid.UUID(item_id))
        except ValueError as exc:
            raise ConfigError(
                "target URL does not contain a valid Outline UUID"
            ) from exc
        slug = slug_id[: id_match.start(1)].rstrip("-")
    else:
        slug, separator, item_id = slug_id.rpartition("-")
        if not separator or not re.fullmatch(r"[A-Za-z0-9]{6,32}", item_id):
            raise ConfigError("target URL must end with a safe Outline URL ID or UUID")
    if not slug or slug in {".", ".."}:
        raise ConfigError("target URL must include a slug before its Outline ID")
    return OutlineTarget(
        "document" if route == "doc" else "collection", item_id, url, slug
    )


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
    in_html_comment = False
    for line in markdown.splitlines():
        parts = []
        cursor = 0
        while cursor < len(line):
            if in_html_comment:
                end = line.find("-->", cursor)
                if end < 0:
                    cursor = len(line)
                    break
                cursor = end + 3
                in_html_comment = False
                continue
            start = line.find("<!--", cursor)
            if start < 0:
                parts.append(line[cursor:])
                break
            parts.append(line[cursor:start])
            cursor = start + 4
            in_html_comment = True
        line = "".join(parts)
        if not line.strip() or re.match(r"^\s{0,3}>", line):
            continue
        match = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if fence_char is None and match:
            fence_char = match.group(1)[0]
            fence_size = len(match.group(1))
            continue
        if fence_char is not None:
            close = re.match(r"^\s{0,3}(`+|~+)\s*$", line)
            if (
                close
                and close.group(1)[0] == fence_char
                and len(close.group(1)) >= fence_size
            ):
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
        chunks.append((title, "\n".join(lines[line_index + 1 : end])))
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
    if re.search(
        r"\b(?:TODO|TBD)\b|待补充|待填写|待完善|请填写|请补充", clean, re.IGNORECASE
    ):
        return False
    if _PLACEHOLDER.fullmatch(clean):
        return False
    if "不适用" in clean:
        remainder = re.sub(r"不适用\s*[:：]?", "", clean).strip(
            " \t-—:：，,。；;()（）"
        )
        if len(re.sub(r"\W", "", remainder, flags=re.UNICODE)) < 3:
            return False
    if "未验证" in clean and not re.search(
        r"验证|核实|确认|补齐|计划|后续|路径|负责人", clean
    ):
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
    alias_patterns = [
        re.compile(r"^\s*" + re.escape(alias) + r"\s*[:：]\s*(.*)$", re.IGNORECASE)
        for alias in aliases
    ]
    for index, line in enumerate(cleaned):
        match = next(
            (pattern.match(line) for pattern in alias_patterns if pattern.match(line)),
            None,
        )
        if not match:
            continue
        parts = [match.group(1).strip()]
        for follow in cleaned[index + 1 :]:
            if not follow:
                if parts[-1]:
                    break
                continue
            if follow.startswith("#") or any(
                pattern.match(follow) for pattern in alias_patterns
            ):
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
        return "\n".join(lines[line_index + 1 : end])
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
            report.content_errors.append(
                f"{section_name}: required field '{label}' is missing"
            )
        elif not _has_substantive_value(value):
            report.content_errors.append(
                f"{section_name}: required field '{label}' is blank or placeholder-only"
            )


BACKGROUND_FIELDS = (
    ("现状与问题",),
    ("为什么需要这个功能", "为什么需要"),
    ("系统/模块", "系统 / 模块"),
    ("目标用户 / 业务角色", "目标用户", "业务角色"),
    ("影响的业务对象 / 范围", "影响的业务对象", "范围"),
)
NONFUNCTIONAL_FIELDS = (
    ("性能",),
    ("安全",),
    ("兼容性",),
    ("数据 / 权限约束", "数据/权限约束"),
)
RISK_FIELDS = (
    ("风险点",),
    ("边界情况",),
    ("异常流 / 失败场景", "异常流/失败场景"),
    ("回滚 / 降级约束", "回滚/降级约束"),
)
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
    try:
        try:
            from .comment_mirror import CommentMirrorError, canonical_body
        except ImportError:
            from comment_mirror import CommentMirrorError, canonical_body
        markdown = canonical_body(markdown)
    except CommentMirrorError as exc:
        report.content_errors.append(str(exc))
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
        report.structural_errors.append(
            "bundled template H2 headings do not match the approved eight-section order"
        )

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
            report.structural_errors.append(
                "missing H2 headings: " + ", ".join(missing)
            )
        expected_index = {heading: index for index, heading in enumerate(REQUIRED_H2)}
        known_order = [
            expected_index[heading] for heading in headings if heading in expected_index
        ]
        if known_order != sorted(known_order):
            report.structural_errors.append("H2 headings are not in the approved order")

    by_heading = _section_map(chunks)
    for required in REQUIRED_H2:
        body = by_heading.get(required)
        if body is None:
            continue
        if not _has_substantive_value(body):
            report.content_errors.append(
                f"{required}: section is empty or contains only scaffolding/placeholders"
            )

    _check_fields(
        report, "Background", by_heading.get(REQUIRED_H2[1], ""), BACKGROUND_FIELDS
    )
    requirements = by_heading.get(REQUIRED_H2[2], "")
    functional = _heading_body(requirements, "功能需求")
    nonfunctional = _heading_body(requirements, "非功能需求")
    if functional is None:
        report.content_errors.append(
            "Requirements: required subsection '功能需求' is missing"
        )
    elif not _has_substantive_value(functional):
        report.content_errors.append(
            "Requirements: '功能需求' is blank or placeholder-only"
        )
    if nonfunctional is None:
        report.content_errors.append(
            "Requirements: required subsection '非功能需求' is missing"
        )
    else:
        _check_fields(
            report, "Requirements / 非功能需求", nonfunctional, NONFUNCTIONAL_FIELDS
        )

    acceptance = by_heading.get(REQUIRED_H2[4], "")
    acceptance_visible, _ = _visible_lines(acceptance)
    acceptance_text = "\n".join(acceptance_visible)
    for marker in ("Given", "When", "Then"):
        if not re.search(r"\b" + marker + r"\b", acceptance_text, re.IGNORECASE):
            report.content_errors.append(
                f"Acceptance Criteria: required Given/When/Then marker '{marker}' is missing"
            )

    _check_fields(
        report, "Risks & Edge Cases", by_heading.get(REQUIRED_H2[5], ""), RISK_FIELDS
    )
    _check_fields(
        report,
        "Release / Rollback Notes",
        by_heading.get(REQUIRED_H2[6], ""),
        RELEASE_FIELDS,
    )
    _check_fields(
        report, "References", by_heading.get(REQUIRED_H2[7], ""), REFERENCE_FIELDS
    )
    return report


class _NoRedirectHandler(HTTPRedirectHandler):
    """Do not forward an API request or its bearer token to any redirect target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OutlineClient:
    """Minimal Outline API client with mutation-safe retry behavior."""

    READ_ONLY_METHODS = READ_ONLY_METHODS | frozenset(
        {"collections.list", "documents.list"}
    )
    MUTATION_METHODS = frozenset(
        {"documents.create", "documents.update", "comments.create"}
    )
    SUPPORTED_METHODS = READ_ONLY_METHODS | MUTATION_METHODS

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout: float = 30,
        retries: int = 2,
        retry_delay: float = 0.5,
        page_size: int = DEFAULT_PAGE_SIZE,
        allow_localhost_test: bool = False,
        verified_capabilities: Optional[Mapping[str, Any]] = None,
        opener: Any = None,
    ) -> None:
        self.base_url = validate_base_url(
            base_url, allow_localhost_test=allow_localhost_test
        )
        if not isinstance(token, str) or not token.strip():
            raise OutlineIntentError("Outline API token is required")
        if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
            raise OutlineIntentError("Retry count must be a nonnegative integer")
        if (
            not isinstance(page_size, int)
            or isinstance(page_size, bool)
            or not 1 <= page_size <= 100
        ):
            raise OutlineIntentError("Page size must be between 1 and 100")
        if timeout <= 0 or retry_delay < 0:
            raise OutlineIntentError("Timeout and retry delay must be nonnegative")
        self.token = token.strip()
        self.timeout = timeout
        self.retries = retries
        self.retry_delay = retry_delay
        self.page_size = page_size
        self.verified_capabilities = _safe_verified_capabilities(verified_capabilities)
        if opener is not None:
            self.opener = opener
        else:
            self.opener = build_opener(
                _NoRedirectHandler(),
                HTTPSHandler(context=ssl.create_default_context()),
            )

    def _request(
        self, name: str, params: Mapping[str, Any], *, api_version: Optional[int] = None
    ) -> bytes:
        if name not in self.SUPPORTED_METHODS:
            raise OutlineIntentError("Unsupported Outline API method")
        url = self.base_url + "/api/" + name
        body = json.dumps(
            dict(params), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        retryable = name in self.READ_ONLY_METHODS
        attempts = self.retries + 1 if retryable else 1
        for attempt in range(attempts):
            headers = {
                "Accept": "application/json",
                "Authorization": "Bearer " + self.token,
                "Content-Type": "application/json",
                "User-Agent": "outline-intent/1.0",
            }
            if api_version is not None:
                headers["X-Api-Version"] = str(api_version)
            request = Request(url, data=body, headers=headers, method="POST")
            try:
                response = self.opener.open(request, timeout=self.timeout)
                try:
                    final_url = (
                        response.geturl()
                        if callable(getattr(response, "geturl", None))
                        else url
                    )
                    if not _same_origin(self.base_url, final_url):
                        raise OutlineIntentError(
                            "Outline API response changed origin; request refused"
                        )
                    payload = response.read(64 * 1024 * 1024 + 1)
                finally:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()
                if len(payload) > 64 * 1024 * 1024:
                    raise OutlineIntentError(
                        "Outline API response exceeds the size limit"
                    )
                return payload
            except HTTPError as exc:
                retryable_error = exc.code == 429 or 500 <= exc.code <= 599
                if not retryable_error or not retryable or attempt + 1 >= attempts:
                    exc.close()
                    raise OutlineIntentError(
                        f"Outline API returned HTTP {exc.code}"
                    ) from None
                delay = self.retry_delay * (2**attempt)
                try:
                    retry_after = float(exc.headers.get("Retry-After", ""))
                    if retry_after >= 0:
                        delay = retry_after
                except (AttributeError, TypeError, ValueError):
                    pass
                exc.close()
            except (URLError, TimeoutError, OSError) as exc:
                if not retryable or attempt + 1 >= attempts:
                    raise OutlineIntentError(
                        "Outline API network request failed or timed out"
                    ) from None
                delay = self.retry_delay * (2**attempt)
            if delay > 0:
                time.sleep(min(delay, 10))
        raise OutlineIntentError("Outline API request could not be completed")

    def call(
        self, name: str, params: Mapping[str, Any], *, api_version: Optional[int] = None
    ) -> dict[str, Any]:
        if not isinstance(params, Mapping):
            raise OutlineIntentError("Outline API parameters must be an object")
        raw = self._request(name, params, api_version=api_version)
        try:
            result = json.loads(raw.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise OutlineIntentError("Outline API returned invalid JSON") from None
        if (
            not isinstance(result, dict)
            or result.get("ok") is False
            or result.get("success") is False
            or "data" not in result
        ):
            raise OutlineIntentError(
                "Outline API response did not contain successful data"
            )
        return result

    def pages(self, name: str, **params: Any) -> list[dict[str, Any]]:
        return _paginate(self, name, params, self.page_size, MAX_COMMENT_PAGES)

    def rich_document(self, document_id: str) -> dict[str, Any]:
        response = self.call("documents.info", {"id": document_id}, api_version=3)
        return _api_data(response, "document")

    def rich_comment(self, comment_id: str) -> dict[str, Any]:
        response = self.call("comments.info", {"id": comment_id}, api_version=3)
        return _api_data(response, "comment")


def extract_rich_text(value: Any) -> str:
    """Flatten Outline's ProseMirror document JSON into readable plain text."""
    block_types = {
        "doc",
        "paragraph",
        "heading",
        "blockquote",
        "bulletList",
        "orderedList",
        "listItem",
        "codeBlock",
        "table",
        "tableRow",
        "tableCell",
        "tableHeader",
    }

    def visit(node: Any) -> str:
        if isinstance(node, list):
            return "".join(visit(child) for child in node)
        if not isinstance(node, dict):
            return ""
        kind = node.get("type")
        if kind == "text":
            text = node.get("text")
            return text if isinstance(text, str) else ""
        if kind == "hardBreak":
            return "\n"
        children = node.get("content", [])
        if not isinstance(children, list):
            return ""
        if kind in {"doc", "bulletList", "orderedList", "table", "tableRow"}:
            separator = "\n"
            return separator.join(
                part.strip("\n")
                for child in children
                if (part := visit(child)).strip("\n")
            )
        if kind in {"listItem", "blockquote"}:
            separator = "\n"
            return separator.join(
                part.strip("\n")
                for child in children
                if (part := visit(child)).strip("\n")
            )
        return "".join(visit(child) for child in children)

    return visit(value).strip("\n")


def _paginate(
    client: Any,
    method: str,
    params: Mapping[str, Any],
    page_size: int,
    max_pages: int,
) -> list[dict[str, Any]]:
    offset = 0
    seen: set[str] = set()
    items: list[dict[str, Any]] = []
    for _ in range(max_pages):
        response = _client_call(
            client, method, {**params, "limit": page_size, "offset": offset}
        )
        batch = response.get("data") if isinstance(response, dict) else None
        if not isinstance(batch, list) or any(
            not isinstance(item, dict) for item in batch
        ):
            raise OutlineIntentError("Outline API returned an invalid page")
        ids = [item.get("id") for item in batch]
        if any(not isinstance(item_id, str) or not item_id for item_id in ids):
            raise OutlineIntentError(
                "Outline page contains an item without a stable ID"
            )
        if len(ids) != len(set(ids)) or any(item_id in seen for item_id in ids):
            raise OutlineIntentError(
                "Outline pagination repeated an item; a complete snapshot is unavailable"
            )
        items.extend(batch)
        seen.update(ids)
        offset += len(batch)
        pagination = response.get("pagination") or {}
        if not isinstance(pagination, dict):
            raise OutlineIntentError("Outline API returned invalid pagination metadata")
        explicit_more = (
            pagination.get("hasNext") is True
            or bool(pagination.get("nextPath"))
            or pagination.get("hasMore") is True
        )
        total = pagination.get("total")
        if isinstance(total, int) and not isinstance(total, bool) and offset < total:
            explicit_more = True
        if not batch:
            if explicit_more:
                raise OutlineIntentError(
                    "Outline pagination ended before the advertised collection"
                )
            return items
        if explicit_more:
            continue
        if pagination.get("hasNext") is False or pagination.get("hasMore") is False:
            return items
        if len(batch) < page_size:
            return items
    raise OutlineIntentError("Outline pagination exceeded its safety limit")


def _client_call(client: Any, method: str, params: Mapping[str, Any]) -> Any:
    if isinstance(client, OutlineClient) and method in {
        "documents.info",
        "comments.info",
        "comments.list",
    }:
        return client.call(method, params, api_version=3)
    return client.call(method, params)


def _api_data(response: Any, key: str) -> dict[str, Any]:
    data = response.get("data") if isinstance(response, dict) else None
    if not isinstance(data, dict):
        raise OutlineIntentError("Outline API returned an invalid item")
    item = data.get(key, data)
    if not isinstance(item, dict):
        raise OutlineIntentError("Outline API returned an invalid item")
    return dict(item)


def document_snapshot(client: Any, document_id: str | OutlineTarget) -> dict[str, Any]:
    """Capture a document and every comment page as one fingerprinted read snapshot."""
    if isinstance(document_id, OutlineTarget):
        if document_id.kind != "document":
            raise OutlineIntentError(
                "A document target is required for a document snapshot"
            )
        document_id = document_id.id
    if not isinstance(document_id, str) or not document_id.strip():
        raise OutlineIntentError("Document ID is required for a snapshot")
    rich_reader = getattr(client, "rich_document", None)
    if callable(rich_reader):
        document = rich_reader(document_id)
    else:
        response = client.call("documents.info", {"id": document_id})
        document = _api_data(response, "document")
    if not isinstance(document.get("id"), str) or not document["id"]:
        raise OutlineIntentError("Document snapshot has no canonical document ID")
    rich_data = document.get("data")
    if "text" not in document and rich_data is not None:
        document["text"] = extract_rich_text(rich_data)
    if "data" not in document:
        document["data"] = None
    comments = _paginate(
        client,
        "comments.list",
        {"documentId": document["id"], "sort": "createdAt", "direction": "ASC"},
        getattr(client, "page_size", DEFAULT_PAGE_SIZE),
        MAX_COMMENT_PAGES,
    )
    complete_comments: list[dict[str, Any]] = []
    for item in comments:
        row = dict(item)
        if not isinstance(row.get("data"), dict):
            rich_comment = getattr(client, "rich_comment", None)
            if callable(rich_comment):
                detail = rich_comment(row["id"])
            else:
                detail = _api_data(
                    client.call("comments.info", {"id": row["id"]}), "comment"
                )
            row.update(detail)
        if row.get("documentId") != document["id"] or not isinstance(
            row.get("data"), dict
        ):
            raise OutlineIntentError(
                "Comment snapshot is incomplete or belongs to another document"
            )
        row.setdefault("parentCommentId", None)
        row.setdefault("anchorText", None)
        row.setdefault("anchorPrefix", None)
        row.setdefault("anchorSuffix", None)
        row.setdefault("text", extract_rich_text(row["data"]))
        complete_comments.append(row)
    snapshot: dict[str, Any] = {
        "complete": True,
        "document": document,
        "comments": complete_comments,
    }
    try:
        encoded = json.dumps(
            snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise OutlineIntentError("Outline snapshot contains unsupported data") from None
    snapshot["fingerprint"] = hashlib.sha256(encoded).hexdigest()
    return snapshot


# Keep the plan engine as the single implementation of the guarded write protocol.
try:
    from .plan_engine import apply_plan, content_digest, prepare_plan
except ImportError:
    from plan_engine import apply_plan, content_digest, prepare_plan


def _read_json(path: os.PathLike[str] | str) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        raise OutlineIntentError("JSON input could not be read") from None


def _write_json(path: os.PathLike[str] | str, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    payload = (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    fd, temporary = tempfile.mkstemp(
        prefix="." + destination.name + ".", dir=str(destination.parent)
    )
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _cli_config(args: argparse.Namespace) -> RuntimeConfig:
    config = resolve_config(config_path=args.config, prompt=False, save=False)
    problems = check_config(config, allow_localhost_test=args.allow_localhost_test)
    if problems:
        raise OutlineIntentError(
            "Configuration preflight failed: " + "; ".join(problems)
        )
    return config


def _cli_client(args: argparse.Namespace) -> OutlineClient:
    config = _cli_config(args)
    return OutlineClient(
        config.url,
        config.token,
        allow_localhost_test=args.allow_localhost_test,
        verified_capabilities=config.verified_capabilities,
    )


def _cli_preflight_config(args: argparse.Namespace) -> RuntimeConfig:
    source_env = dict(os.environ)
    if args.url:
        source_env["OUTLINE_URL"] = args.url
    config = resolve_config(config_path=args.config, env=source_env)
    if args.prompt and not config.url:
        try:
            entered_url = input("Outline URL: ").strip()
        except EOFError:
            raise OutlineIntentError(
                "OUTLINE_URL is required; pass --url or provide it at the prompt"
            ) from None
        if not entered_url:
            raise OutlineIntentError(
                "OUTLINE_URL is required; pass --url or provide it at the prompt"
            )
        source_env["OUTLINE_URL"] = entered_url
    config = resolve_config(
        config_path=args.config,
        env=source_env,
        prompt=args.prompt,
        save=args.save_config,
    )
    problems = check_config(config, allow_localhost_test=args.allow_localhost_test)
    if problems:
        raise OutlineIntentError(
            "Configuration preflight failed: " + "; ".join(problems)
        )
    return config


def _add_config_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Outline config JSON path",
    )
    parser.add_argument(
        "--allow-localhost-test",
        action="store_true",
        help="Allow HTTP only for localhost tests",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate, snapshot, prepare, and apply Outline intent plans"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    preflight = commands.add_parser(
        "preflight", help="Check local Outline URL and credential configuration"
    )
    _add_config_options(preflight)
    preflight.add_argument("--url", help="Override the Outline origin for this check")
    preflight.add_argument(
        "--prompt",
        action="store_true",
        help="Prompt for a missing URL and silently prompt for a token",
    )
    preflight.add_argument(
        "--save-config",
        action="store_true",
        help="Save explicitly entered configuration with mode 0600",
    )
    preflight.add_argument(
        "--target-url", help="Optional same-origin Outline document or collection URL"
    )

    validate = commands.add_parser(
        "validate", help="Validate an intent Markdown file against the bundled template"
    )
    validate.add_argument("--file", type=Path, required=True)
    validate.add_argument("--template", type=Path)

    snapshot = commands.add_parser(
        "snapshot", help="Read a document and all paginated comments"
    )
    _add_config_options(snapshot)
    snapshot.add_argument("--target-url", required=True)
    snapshot.add_argument("--out", type=Path)

    prepare = commands.add_parser(
        "prepare", help="Freeze source and remote state into a reviewable plan"
    )
    _add_config_options(prepare)
    prepare.add_argument("--plan", type=Path, required=True)
    prepare.add_argument("--repo-root", type=Path, required=True)
    prepare.add_argument("--out", type=Path, required=True)
    prepare.add_argument("--journal", type=Path)

    apply = commands.add_parser("apply", help="Apply an exactly approved prepared plan")
    _add_config_options(apply)
    apply.add_argument("--plan", type=Path, required=True)
    apply.add_argument("--repo-root", type=Path, required=True)
    apply.add_argument("--approved-digest", required=True)
    apply.add_argument("--journal", type=Path)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "preflight":
            config = _cli_preflight_config(args)
            result: dict[str, Any] = {"ok": True, **config.to_public_dict()}
            if args.target_url:
                target = parse_target(
                    args.target_url,
                    config.url,
                    allow_localhost_test=args.allow_localhost_test,
                )
                result["target"] = {
                    "kind": target.kind,
                    "id": target.id,
                    "slug": target.slug,
                }
            print(json.dumps(result, ensure_ascii=False))
            return 0
        if args.command == "validate":
            try:
                markdown = args.file.read_text(encoding="utf-8")
                template = (
                    args.template.read_text(encoding="utf-8") if args.template else None
                )
            except OSError:
                raise OutlineIntentError(
                    "Markdown or template file could not be read"
                ) from None
            report = validate_template(markdown, template_text=template)
            print(json.dumps(report.to_dict(), ensure_ascii=False))
            return 0 if report.ok else 1
        if args.command == "snapshot":
            client = _cli_client(args)
            target = parse_target(
                args.target_url,
                client.base_url,
                allow_localhost_test=args.allow_localhost_test,
            )
            if target.kind != "document":
                raise OutlineIntentError("snapshot requires a document URL")
            result = document_snapshot(client, target)
            if args.out:
                _write_json(args.out, result)
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "prepare":
            client = _cli_client(args)
            result = prepare_plan(
                _read_json(args.plan), args.repo_root, client, journal_path=args.journal
            )
            _write_json(args.out, result)
            print(
                json.dumps(
                    {
                        "prepared": True,
                        "digest": result.get("digest"),
                        "out": str(args.out),
                    },
                    ensure_ascii=False,
                )
            )
            return 0
        if args.command == "apply":
            client = _cli_client(args)
            result = apply_plan(
                _read_json(args.plan),
                args.approved_digest,
                args.repo_root,
                client,
                journal_path=args.journal,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
    except OutlineIntentError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    # plan_engine intentionally imports validation lazily to keep the read-only
    # runtime independent when imported normally and runnable as a script.
    sys.modules.setdefault("outline_intent", sys.modules[__name__])
    raise SystemExit(main())
