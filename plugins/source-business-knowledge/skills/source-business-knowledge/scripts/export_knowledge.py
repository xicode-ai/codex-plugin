#!/usr/bin/env python3
"""Check an explicitly mapped Markdown import directory and emit a manifest; never import vectors."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

from validate_analysis import ID, _reject_constant, validate_analysis


KINDS = {'system', 'module', 'topic', 'cross_system', 'glossary'}
PARENTS = {'module': 'system', 'topic': 'module', 'cross_system': 'system', 'glossary': 'system'}
AUDIT = re.compile(r'(?i)\b(?:claim|scenario|evidence)\s*[:：]|\b(?:KB|REPO)-[\w.-]+|'
                   r'\b[CSE]-[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)*\b|'
                   r'知识(?:条目)?(?:编号|标识)|代码版本|源码(?:文件|路径|位置)|静态实现|'
                   r'第\s*\d+\s*(?:[-–—]\s*\d+\s*)?行')
TECHNICAL = re.compile(
    r'\b[\w./\\-]+\.(?:java|py|ts|tsx|js|jsx|go|rs|sql|cs|php|kt|scala|cpp|hpp)\b|'
    r'\b[A-Za-z_$][\w$]*(?:Controller|Service|ServiceImpl|FeignClient|Repository|Mapper|Executor)\b|'
    r'\b(?:src|source|app|lib)[/\\][A-Za-z0-9_./\\-]+|'
    r'\b[A-Za-z_$][\w$]*\.[a-z][\w$]*\s*\(|'
    r'\b(?:GET|POST|PUT|PATCH|DELETE)\s+/|'
    r'\b(?:Redis|Feign|CAS|SQL)\b|线程池|分布式锁|'
    r'\b(?:def|class|interface|enum|function)\s+[A-Za-z_]\w*\s*[{:(]|'
    r'\bSELECT\b[^\n]+\bFROM\b', re.IGNORECASE)


def visible_prose(body: str) -> str:
    """Approximate text surviving a basic importer; this is not a Markdown renderer."""
    lines = body.splitlines()
    if lines and lines[0].strip() == '---':
        end = next((n for n in range(1, len(lines)) if lines[n].strip() in {'---', '...'}), None)
        lines = lines[end + 1:] if end is not None else []
    body = re.sub(r'<!--.*?-->', '', '\n'.join(lines), flags=re.DOTALL)
    prose = []
    fence = None
    for line in body.splitlines():
        match = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line)
        if match:
            marker, rest = match.groups()
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence) and not rest.strip():
                fence = None
            continue
        if fence is None and not re.match(r'^(?: {4}| {0,3}#{1,6}\s)', line.expandtabs(4)):
            prose.append(line)
    return '\n'.join(prose)


def check_business_document(body: str, *, private_ids=()) -> list[str]:
    """Reject obvious audit/code leakage and missing Markdown prose, not semantic errors."""
    errors = []
    private_id_found = any(re.search(r'(?<![A-Za-z0-9_.-])' + re.escape(value) + r'(?![A-Za-z0-9_.-])', body)
                           for value in private_ids if isinstance(value, str) and value)
    if AUDIT.search(body) or private_id_found:
        errors.append('audit identifiers/source citations belong only in analysis/')
    if TECHNICAL.search(body):
        errors.append('technical implementation/code references do not belong in business knowledge')
    if re.match(r'\A\ufeff?---[ \t]*\r?\n', body):
        errors.append('frontmatter belongs in the private document manifest')
    # Track rendered headings and prose approximately, ignoring comments and diagram source.
    content = re.sub(r'<!--.*?-->', '', body, flags=re.DOTALL)
    h1 = 0
    sections = []
    section = None
    paragraph = []
    fence = None
    list_block = False

    def flush():
        if section is not None and paragraph:
            sections[section].append(' '.join(paragraph))
        paragraph.clear()

    for line in content.splitlines():
        marker = re.match(r'^\s{0,3}(`{3,}|~{3,})(.*)$', line)
        if marker:
            flush()
            token, rest = marker.groups()
            if fence is None:
                fence = token
                errors.append('code/diagram source blocks belong only in analysis/')
            elif token[0] == fence[0] and len(token) >= len(fence) and not rest.strip():
                fence = None
            continue
        if fence is not None:
            continue
        if line.expandtabs(4).startswith('    ') and line.strip():
            flush()
            errors.append('indented code blocks are not business knowledge')
            continue
        if '`' in line:
            errors.append('inline code formatting is not business prose')
        heading = re.match(r'^\s{0,3}(#{1,6})\s+\S', line)
        if heading:
            flush()
            list_block = False
            level = len(heading[1])
            if level == 1:
                h1 += 1
                section = None
            elif level == 2:
                sections.append([])
                section = len(sections) - 1
            continue
        if not line.strip():
            flush()
            list_block = False
            continue
        if re.match(r'^\s*(?:[-*+]\s|\d+[.)]\s|\||>)', line):
            flush()
            list_block = True
            continue
        if not list_block:
            paragraph.append(line.strip())
    flush()
    if h1 != 1:
        errors.append('exactly one Markdown H1 title required')
    if len(sections) < 2:
        errors.append('business documents need meaningful level-2 sections')
    if sum(bool(paragraphs) for paragraphs in sections) < 2:
        errors.append('explanatory paragraphs are required under multiple business sections')
    return list(dict.fromkeys(errors))


def build_manifest(payload: object, docs_root: Path, *, check_sources: bool = False):
    """Return (manifest, errors); the manifest is an inventory, not a semantic quality score."""
    errors = validate_analysis(payload, check_sources=check_sources, docs_root=docs_root)
    if errors:
        return None, errors
    if payload['run']['status'] != 'complete':
        return None, ['run.status: knowledge export requires complete scoped analysis']
    root = Path(docs_root).resolve()
    import_root = root / 'knowledge'
    if import_root.is_symlink() or not import_root.is_dir():
        return None, ['knowledge/: real import directory required; symlinks are not accepted']
    rows = []
    lookup = {}
    mapped = set()
    private_ids = {row['id'] for table in ('repositories', 'data_sources', 'queries', 'evidence',
                                          'claims', 'scenarios', 'handoffs', 'coverage')
                   for row in payload[table]}
    private_ids.update(row['knowledge']['id'] for row in payload['documents']
                       if isinstance(row.get('knowledge'), dict)
                       and isinstance(row['knowledge'].get('id'), str))
    for number, row in enumerate(payload['documents']):
        loc = f'documents[{number}]'
        path = PurePosixPath(row['path'])
        in_pack = path.parts[0] == 'knowledge' and len(path.parts) > 1 and path.suffix.lower() == '.md'
        metadata = row.get('knowledge')
        if not in_pack:
            if metadata is not None:
                errors.append(loc + ': knowledge metadata requires Markdown in knowledge/ directory')
            continue
        mapped.add(path.as_posix())
        if not row['claim_ids']:
            errors.append(loc + ': private claim mapping is required for knowledge content')
        if not isinstance(metadata, dict):
            errors.append(loc + '.knowledge: object required')
            continue
        valid = True
        for field in ('id', 'kind', 'system', 'topic'):
            if not isinstance(metadata.get(field), str) or not metadata[field].strip():
                errors.append(loc + '.knowledge.' + field + ': non-empty text required')
                valid = False
        if not valid:
            continue
        identifier, kind = metadata['id'], metadata['kind']
        if not ID.fullmatch(identifier):
            errors.append(loc + '.knowledge.id: stable identifier required')
        if identifier in lookup:
            errors.append(loc + '.knowledge.id: duplicate knowledge id')
        if kind not in KINDS:
            errors.append(loc + '.knowledge.kind: invalid value')
        parent = metadata.get('parent_id')
        if 'parent_id' not in metadata or (parent is not None and (not isinstance(parent, str) or not ID.fullmatch(parent))):
            errors.append(loc + '.knowledge.parent_id: stable identifier or null required')
        aliases = metadata.get('aliases')
        if not isinstance(aliases, list) or any(not isinstance(v, str) or not v.strip() for v in aliases):
            errors.append(loc + '.knowledge.aliases: string array required')
            aliases = []
        module = metadata.get('module')
        if kind in {'module', 'topic'} and (not isinstance(module, str) or not module.strip()):
            errors.append(loc + '.knowledge.module: module context required')
        if kind == 'system' and parent is not None:
            errors.append(loc + '.knowledge.parent_id: system overview must be root')
        target = root / row['path']
        if target.is_symlink():
            errors.append(loc + ': symlink Markdown cannot be exported')
            continue
        try:
            content = target.read_bytes()
            body = content.decode('utf-8')
        except (OSError, UnicodeError):
            errors.append(loc + ': unable to read Markdown')
            continue
        prose = visible_prose(body)
        errors.extend(loc + ': ' + error for error in check_business_document(body, private_ids=private_ids))
        contexts = [p for p in re.split(r'\n\s*\n', prose) if p.strip()]
        # Generic type labels (e.g. "module overview") are maintenance metadata,
        # not phrases to force into an otherwise natural business paragraph.
        topics = [metadata['topic'], *aliases] if kind == 'topic' else []
        if kind == 'module' and isinstance(module, str):
            topics = [module]
        if not any(metadata['system'] in p and (not topics or any(topic in p for topic in topics))
                   for p in contexts):
            errors.append(loc + ': visible business context needs system and relevant business topic, not audit boilerplate')
        exported = {key: metadata[key] for key in ('id', 'kind', 'parent_id', 'system', 'topic') if key in metadata}
        exported.update(path=path.as_posix(), aliases=aliases,
                        sha256=hashlib.sha256(content).hexdigest(),
                        claim_ids=row['claim_ids'], scenario_ids=row['scenario_ids'])
        if isinstance(module, str):
            exported['module'] = module
        rows.append((loc, exported))
        lookup[identifier] = exported
    if sum(row['kind'] == 'system' for _, row in rows) != 1:
        errors.append('knowledge/: exactly one system overview required')
    for loc, row in rows:
        expected = PARENTS.get(row['kind'])
        parent = row.get('parent_id')
        if expected:
            found = lookup.get(parent) if isinstance(parent, str) else None
            if found is None:
                errors.append(loc + '.knowledge.parent_id: unknown parent')
            elif found['kind'] != expected:
                errors.append(loc + '.knowledge.parent_id: invalid parent kind')
            else:
                if row['system'] != found['system']:
                    errors.append(loc + '.knowledge.system: system context differs from parent')
                if row['kind'] == 'topic' and row.get('module') != found.get('module'):
                    errors.append(loc + '.knowledge.module: module context differs from parent')
    try:
        for path in import_root.rglob('*'):
            if path.is_symlink():
                errors.append('knowledge/: symlink entries cannot be exported')
            elif path.is_file():
                if path.suffix.lower() != '.md':
                    errors.append('knowledge/: only Markdown files belong in the import directory')
                elif path.relative_to(root).as_posix() not in mapped:
                    errors.append('knowledge/: unmapped Markdown; review stale or unrelated import content')
    except OSError:
        errors.append('knowledge/: unable to inspect import directory')
    if errors:
        return None, errors
    return {
        'schema_version': '1.0',
        'system': payload['run']['system'],
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'analysis_generated_at': payload['run']['generated_at'],
        'source_revisions': [{key: repo[key] for key in ('id', 'commit', 'dirty')}
                             for repo in payload['repositories']],
        'documents': sorted((row for _, row in rows), key=lambda row: row['path']),
        'checks': {'analysis_and_documents': 'checked',
                   'business_content_lint': 'checked',
                   'source_snapshots': 'checked' if check_sources else 'not_checked',
                   'paragraph_semantics': 'not_automatically_checked',
                   'sentence_semantics': 'not_automatically_checked',
                   'pg_retrieval': 'not_tested'},
    }, []


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('path', type=Path, help='analysis.json with knowledge document mappings')
    parser.add_argument('--docs-root', type=Path, required=True, help='output root containing knowledge/')
    parser.add_argument('--check-sources', action='store_true')
    args = parser.parse_args(argv)
    try:
        payload = json.loads(args.path.read_text(encoding='utf-8'), parse_constant=_reject_constant)
    except (OSError, ValueError, UnicodeError, RecursionError):
        print('INPUT_ERROR: unable to read strict UTF-8 JSON analysis', file=sys.stderr)
        return 2
    manifest, errors = build_manifest(payload, args.docs_root, check_sources=args.check_sources)
    if errors:
        for error in errors:
            print('KNOWLEDGE_ERROR: ' + error, file=sys.stderr)
        return 1
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
