#!/usr/bin/env python3
"""Check analysis structure, references and optional local snapshots, never business truth."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import string
import sys
from urllib.parse import unquote, urlsplit


TABLES = ('repositories', 'data_sources', 'queries', 'evidence', 'claims',
          'scenarios', 'handoffs', 'coverage', 'documents')
STATUSES = {'unseen', 'in_progress', 'analyzed', 'blocked', 'excluded'}
ID = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,127}\Z')
INLINE = re.compile(r'\b(claim|scenario):([A-Za-z][A-Za-z0-9_.-]*)')


def _contained_path(value: object) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and '\\' not in value
            and not PurePosixPath(value).is_absolute() and ':' not in value
            and '..' not in PurePosixPath(value).parts and '\x00' not in value)


def _inline_link_paths(body):
    """Read inline destinations with balanced parentheses, angle brackets and escapes."""
    for match in re.finditer(r'!?\[[^\]\n]*\]\([ \t]*', body):
        position = match.end()
        angle = body[position:position + 1] == '<'
        position += int(angle)
        depth = 0
        destination = []
        while position < len(body) and body[position] != '\n':
            char = body[position]
            if char == '\\' and position + 1 < len(body) and body[position + 1] in string.punctuation:
                destination.append(body[position + 1])
                position += 2
                continue
            if angle and char == '>':
                yield ''.join(destination)
                break
            if not angle:
                if depth == 0 and (char == ')' or char.isspace()):
                    yield ''.join(destination)
                    break
                if char == '(':
                    depth += 1
                elif char == ')':
                    depth -= 1
            destination.append(char)
            position += 1


class _Check:
    def __init__(self, payload):
        self.errors = []
        self.tables = {}
        self.index = {}
        seen = set()
        for table in TABLES:
            rows = payload.get(table)
            if not isinstance(rows, list):
                self.fail(table, 'must be an array')
                rows = []
            clean = []
            lookup = {}
            for number, row in enumerate(rows):
                where = f'{table}[{number}]'
                if not isinstance(row, dict):
                    self.fail(where, 'must be an object')
                    continue
                clean.append((where, row))
                if table == 'documents':
                    continue
                identifier = row.get('id')
                if not isinstance(identifier, str) or not ID.fullmatch(identifier):
                    self.fail(where + '.id', 'must be a stable identifier')
                    continue
                if identifier in seen:
                    self.fail(where + '.id', 'duplicate id')
                seen.add(identifier)
                lookup[identifier] = row
            self.tables[table] = clean
            self.index[table] = lookup

    def fail(self, where, message):
        # Locations are generated from schema fields, never from user-supplied IDs/content.
        self.errors.append(f'{where}: {message}')

    def text(self, row, field, where):
        value = row.get(field)
        if not isinstance(value, str) or not value.strip():
            self.fail(where + '.' + field, 'non-empty text required')
            return ''
        return value

    def choice(self, row, field, choices, where):
        value = row.get(field)
        if not isinstance(value, str) or value not in choices:
            self.fail(where + '.' + field, 'invalid value')
            return ''
        return value

    def strings(self, row, field, where):
        values = row.get(field)
        if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
            self.fail(where + '.' + field, 'array of non-empty strings required')
            return []
        return values

    def refs(self, row, field, table, where):
        values = self.strings(row, field, where)
        for value in values:
            if value not in self.index[table]:
                self.fail(where + '.' + field, 'unknown reference')
        return [self.index[table][v] for v in values if v in self.index[table]]

    def ref(self, row, field, table, where):
        value = self.text(row, field, where)
        found = self.index[table].get(value)
        if not found:
            self.fail(where + '.' + field, 'unknown reference')
        return found

    def timestamp(self, row, field, where):
        value = self.text(row, field, where)
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            if parsed.tzinfo is None or parsed.utcoffset() is None:
                raise ValueError
        except ValueError:
            self.fail(where + '.' + field, 'timezone-aware ISO timestamp required')

    def boolean(self, row, field, where):
        if type(row.get(field)) is not bool:
            self.fail(where + '.' + field, 'boolean required')

    def source(self, row, where, check_sources):
        repo = self.ref(row, 'repo_id', 'repositories', where)
        self.text(row, 'symbol', where)
        path = row.get('path')
        if not _contained_path(path):
            self.fail(where + '.path', 'relative contained path required')
        start, end = row.get('line_start'), row.get('line_end')
        valid_range = type(start) is int and type(end) is int and 1 <= start <= end
        if not valid_range:
            self.fail(where, 'positive 1-based line range required')
        digest = row.get('sha256')
        if not isinstance(digest, str) or not re.fullmatch(r'[a-f0-9]{64}', digest):
            self.fail(where + '.sha256', 'full file SHA-256 required')
        if not check_sources or not repo or not _contained_path(path):
            return
        root = repo.get('root')
        if not isinstance(root, str) or not root.strip():
            return
        if not Path(root).is_absolute():
            self.fail(where, 'source root must be absolute on current platform; remap local root')
            return
        try:
            root = Path(root).resolve()
            target = (root / path).resolve()
            if not target.is_relative_to(root):
                self.fail(where + '.path', 'source escapes root')
                return
            content = target.read_bytes()
            if hashlib.sha256(content).hexdigest() != digest:
                self.fail(where, 'source snapshot differs; dependent conclusions need review')
            if valid_range and end > len(content.splitlines()):
                self.fail(where, 'line range exceeds source length')
        except (OSError, ValueError, RuntimeError):
            self.fail(where, 'source cannot be read')

    def query(self, row, where):
        self.ref(row, 'source_id', 'data_sources', where)
        for field in ('sql', 'window', 'tenant_filter', 'soft_delete_filter', 'grain'):
            self.text(row, field, where)
        self.timestamp(row, 'captured_at', where)
        self.choice(row, 'outcome', {'succeeded', 'failed'}, where)
        count = row.get('row_count')
        if type(count) is not int or count < 0:
            self.fail(where + '.row_count', 'non-negative integer required')
        self.boolean(row, 'truncated', where)

    def claim(self, row, where):
        kind = self.choice(row, 'kind', {'implementation', 'data_observation', 'documented_intent',
                                         'product_hypothesis', 'question'}, where)
        for field in ('text', 'scope'):
            self.text(row, field, where)
        self.strings(row, 'limitations', where)
        support = self.refs(row, 'evidence_ids', 'evidence', where)
        self.refs(row, 'counter_evidence_ids', 'evidence', where)
        required = {'implementation': 'code', 'data_observation': 'database', 'documented_intent': 'document'}
        if kind in required and not any(e.get('kind') == required[kind] for e in support):
            self.fail(where, required[kind] + ' evidence required')
        if kind == 'product_hypothesis':
            self.text(row, 'rationale', where)

    def scenario(self, row, where):
        for field in ('name', 'actor', 'trigger', 'outcome'):
            self.text(row, field, where)
        self.strings(row, 'preconditions', where)
        self.refs(row, 'claim_ids', 'claims', where)
        handoffs = self.refs(row, 'handoff_ids', 'handoffs', where)
        closure = self.choice(row, 'closure', {'closed', 'boundary', 'gap'}, where)
        if closure != 'closed':
            self.text(row, 'gap', where)
        if closure == 'closed' and any(h.get('status') != 'matched' for h in handoffs):
            self.fail(where, 'closed scenario has unresolved handoff')
        for handoff in handoffs:
            if handoff.get('scenario_id') != row.get('id'):
                self.fail(where + '.handoff_ids', 'reciprocal scenario link required')
        for field in ('steps', 'states', 'exceptions'):
            values = row.get(field)
            if not isinstance(values, list):
                self.fail(where + '.' + field, 'array required')
                continue
            if field == 'steps' and not values:
                self.fail(where + '.steps', 'at least one step required')
            for n, value in enumerate(values):
                loc = f'{where}.{field}[{n}]'
                if not isinstance(value, dict):
                    self.fail(loc, 'object required')
                    continue
                for key in (('object', 'from', 'to') if field == 'states' else ('text',)):
                    self.text(value, key, loc)
                if not self.refs(value, 'claim_ids', 'claims', loc):
                    self.fail(loc, 'claim references required; unknown behavior uses a question claim')

    def handoff(self, row, where):
        scenario = self.ref(row, 'scenario_id', 'scenarios', where)
        self.ref(row, 'from_repo', 'repositories', where)
        target = row.get('to_repo')
        if target is not None:
            self.ref(row, 'to_repo', 'repositories', where)
        else:
            self.text(row, 'external_name', where)
        self.choice(row, 'transport', {'http', 'event', 'shared_data', 'rpc', 'other'}, where)
        for key in ('contract', 'business_key'):
            self.text(row, key, where)
        status = self.choice(row, 'status', {'matched', 'one_sided', 'conflict'}, where)
        producers = self.refs(row, 'producer_evidence_ids', 'evidence', where)
        consumers = self.refs(row, 'consumer_evidence_ids', 'evidence', where)
        if not producers and not consumers:
            self.fail(where, 'at least one endpoint evidence required')
        if status == 'matched' and (not producers or not consumers or target is None):
            self.fail(where, 'matched handoff requires both endpoints and repositories')
        if status != 'matched':
            self.text(row, 'issue', where)
        for endpoint, evidence in (('from_repo', producers), ('to_repo', consumers)):
            for item in evidence:
                if item.get('kind') != 'code' or item.get('repo_id') != row.get(endpoint):
                    self.fail(where, 'code evidence must belong to endpoint repository')
        if scenario and row.get('id') not in _string_list(scenario.get('handoff_ids')):
            self.fail(where, 'reciprocal scenario link required')

    def document(self, row, where, docs_root):
        path = row.get('path')
        if not _contained_path(path):
            self.fail(where + '.path', 'relative contained path required')
        self.refs(row, 'claim_ids', 'claims', where)
        self.refs(row, 'scenario_ids', 'scenarios', where)
        if docs_root is None or not _contained_path(path):
            return
        try:
            root = Path(docs_root).resolve()
            target = (root / path).resolve()
            if not target.is_relative_to(root):
                self.fail(where, 'document escapes root')
                return
            body = target.read_text(encoding='utf-8')
            # Knowledge prose is for business readers; evidence stays in this document
            # mapping and the analysis graph. Never require audit tags in import content.
            is_knowledge = PurePosixPath(path).parts[0] == 'knowledge' or 'knowledge' in row
            if not is_knowledge:
                for key, prefix in (('claim_ids', 'claim'), ('scenario_ids', 'scenario')):
                    for value in _string_list(row.get(key)):
                        if not re.search(r'\b' + prefix + ':' + re.escape(value) + r'(?![\w.-])', body):
                            self.fail(where, 'declared reference missing from document')
            for prefix, identifier in INLINE.findall(body):
                table = 'claims' if prefix == 'claim' else 'scenarios'
                if identifier not in self.index[table]:
                    self.fail(where, 'unknown inline reference')
            fence = None
            prose = []
            for line in body.splitlines():
                match = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line)
                if match:
                    marker, rest = match.groups()
                    if fence is None:
                        fence = marker
                    elif marker[0] == fence[0] and len(marker) >= len(fence) and not rest.strip():
                        fence = None
                elif fence is None:
                    prose.append(line)
            if fence:
                self.fail(where, 'unclosed fence')
            # Inline local Markdown links only. Reference-style links, anchors and diagram
            # rendering remain author checks, documented in the Skill.
            for link in _inline_link_paths('\n'.join(prose)):
                parsed = urlsplit(link)
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                linked = (target.parent / unquote(parsed.path)).resolve()
                if not linked.is_relative_to(root) or not linked.is_file():
                    self.fail(where, 'local link missing or outside document root')
        except (OSError, ValueError, UnicodeError, RuntimeError):
            self.fail(where, 'document cannot be read')


def _string_list(value):
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def coverage_summary(payload: dict) -> dict[str, int]:
    rows = payload.get('coverage', [])
    counts = Counter(row.get('status') for row in rows
                     if isinstance(row, dict) and isinstance(row.get('status'), str)) if isinstance(rows, list) else Counter()
    return {status: counts[status] for status in sorted(STATUSES)}


def validate_analysis(payload: object, *, check_sources: bool = False,
                      docs_root: Path | None = None) -> list[str]:
    """Return diagnostics without echoing artifact contents or executing supplied code/SQL."""
    if not isinstance(payload, dict):
        return ['analysis: object required']
    check = _Check(payload)
    if payload.get('schema_version') != '1.0':
        check.fail('schema_version', 'supported version is 1.0')
    run = payload.get('run')
    if not isinstance(run, dict):
        check.fail('run', 'object required')
        run = {}
    for field in ('id', 'system', 'scope', 'audience'):
        check.text(run, field, 'run')
    check.timestamp(run, 'generated_at', 'run')
    status = check.choice(run, 'status', {'draft', 'complete'}, 'run')
    check.boolean(run, 'discovery_complete', 'run')
    check.strings(run, 'limitations', 'run')
    check.strings(run, 'next_actions', 'run')
    if not check.tables['repositories']:
        check.fail('repositories', 'at least one repository required')
    for where, row in check.tables['repositories']:
        root = check.text(row, 'root', where)
        if root and not (PurePosixPath(root).is_absolute() or PureWindowsPath(root).is_absolute()):
            check.fail(where + '.root', 'absolute root required')
        check.text(row, 'commit', where)
        check.boolean(row, 'dirty', where)
    for where, row in check.tables['data_sources']:
        for field in ('alias', 'environment', 'schema'):
            check.text(row, field, where)
        check.choice(row, 'status', {'available', 'unavailable', 'not_requested'}, where)
    for where, row in check.tables['queries']:
        check.query(row, where)
    for where, row in check.tables['evidence']:
        kind = check.choice(row, 'kind', {'code', 'database', 'document'}, where)
        for field in ('observed', 'method'):
            check.text(row, field, where)
        check.timestamp(row, 'captured_at', where)
        if kind == 'code':
            check.source(row, where, check_sources)
        elif kind == 'database':
            query = check.ref(row, 'query_id', 'queries', where)
            if query and query.get('outcome') != 'succeeded':
                check.fail(where, 'database evidence requires successful query')
        elif kind == 'document':
            for field in ('locator', 'version'):
                check.text(row, field, where)
    for where, row in check.tables['claims']:
        check.claim(row, where)
    for where, row in check.tables['scenarios']:
        check.scenario(row, where)
    for where, row in check.tables['handoffs']:
        check.handoff(row, where)
    covered = set()
    coverage_tables = {'repository': 'repositories', 'scenario': 'scenarios', 'handoff': 'handoffs'}
    for where, row in check.tables['coverage']:
        kind = check.choice(row, 'kind', set(coverage_tables) | {'entrypoint', 'capability'}, where)
        subject = check.text(row, 'subject_id', where)
        state = check.choice(row, 'status', STATUSES, where)
        pair = (kind, subject)
        if pair in covered:
            check.fail(where, 'duplicate coverage subject')
        covered.add(pair)
        if kind in coverage_tables and subject not in check.index[coverage_tables[kind]]:
            check.fail(where, 'unknown reference')
        if state in {'blocked', 'excluded'}:
            check.text(row, 'reason', where)
        if status == 'complete' and state not in {'analyzed', 'excluded'}:
            check.fail(where, 'complete run has unfinished coverage')
    if status == 'complete':
        if run.get('discovery_complete') is not True:
            check.fail('run.discovery_complete', 'must be true for complete scoped discovery')
        for kind, table in coverage_tables.items():
            for identifier in check.index[table]:
                if (kind, identifier) not in covered:
                    check.fail('coverage', 'missing coverage for declared subject')
        if not check.tables['documents']:
            check.fail('documents', 'complete run requires document mapping')
    seen_paths = set()
    for where, row in check.tables['documents']:
        check.document(row, where, docs_root)
        path = row.get('path')
        if isinstance(path, str):
            if path in seen_paths:
                check.fail(where, 'duplicate document path')
            seen_paths.add(path)
    return check.errors


def _reject_constant(_value):
    raise ValueError('Non-standard JSON constant')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path, help='analysis.json checkpoint')
    parser.add_argument('--check-sources', action='store_true', help='read source files to check hashes and line ranges')
    parser.add_argument('--docs-root', type=Path, help='read mapped documents to check inline references, links and fences')
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.path.read_text(encoding='utf-8'), parse_constant=_reject_constant)
    except (OSError, ValueError, UnicodeError, RecursionError):
        print('INPUT_ERROR: unable to read UTF-8 JSON analysis', file=sys.stderr)
        return 2
    errors = validate_analysis(payload, check_sources=args.check_sources, docs_root=args.docs_root)
    if errors:
        for error in errors:
            print('VALIDATION_ERROR: ' + error)
        return 1
    print('VALID_STRUCTURAL: business meaning, coverage sufficiency and runtime behavior remain unverified')
    print(json.dumps(coverage_summary(payload), sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
