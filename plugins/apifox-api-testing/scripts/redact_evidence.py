#!/usr/bin/env python3
"""Redact sensitive values from local evidence without contacting external systems."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


SENSITIVE_KEY_PATTERN = re.compile(
    r"(authorization|token|access_token|refresh_token|cookie|password|secret|"
    r"client_secret|appointment_code|verification_code)",
    re.IGNORECASE,
)
BEARER_PATTERN = re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/-]+=*")
COOKIE_PATTERN = re.compile(r"(?im)(\bCookie:\s*)[^\r\n]+")
SENSITIVE_TEXT_PATTERN = re.compile(
    r"(?i)(\b(?:authorization|token|access_token|refresh_token|cookie|password|secret|"
    r"client_secret|appointment_code|verification_code)\b\s*[:=]\s*)"
    r"(?:Bearer\s+)?(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)


def redact_text(text: str) -> str:
    """Mask bearer tokens, cookies, password-like fields, and secret values."""

    redacted = BEARER_PATTERN.sub(r"\1[REDACTED]", text)
    redacted = COOKIE_PATTERN.sub(r"\1[REDACTED]", redacted)
    return SENSITIVE_TEXT_PATTERN.sub(r"\1[REDACTED]", redacted)


def redact_value(value: object) -> object:
    """Return a JSON-compatible redacted copy without mutating the input."""

    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SENSITIVE_KEY_PATTERN.search(str(key)) else redact_value(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _redact_document(text: str) -> str:
    stripped = text.lstrip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return redact_text(text)
        return json.dumps(redact_value(payload), ensure_ascii=False, indent=2) + "\n"
    return redact_text(text)


def main(argv: list[str] | None = None) -> int:
    """Redact a file and write only to an explicit output path or stdout."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        redacted = _redact_document(args.path.read_text(encoding="utf-8"))
    except OSError as exc:
        print(f"unable to read evidence: {exc}", file=sys.stderr)
        return 2

    if args.output:
        try:
            args.output.write_text(redacted, encoding="utf-8")
        except OSError as exc:
            print(f"unable to write redacted evidence: {exc}", file=sys.stderr)
            return 2
    else:
        sys.stdout.write(redacted)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
