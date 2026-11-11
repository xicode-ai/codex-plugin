#!/usr/bin/env python3
"""Incremental Outline mirror; the Skill agent performs knowledge maintenance."""
import argparse
import copy
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import unicodedata
import urllib.parse
from zoneinfo import ZoneInfo
from outline_client import OutlineClient, OutlineError, origin
from markdown_links import rewrite
from media_store import MediaStore
from legacy_export import reuse_assets
from comments import normalize_comments, fingerprint, render_comments, plan_comments, RENDER_VERSION


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_name(value):
    value = unicodedata.normalize('NFC', str(value))
    value = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|%]', lambda m: '%' + m.group().encode().hex().upper(), value)
    if value in ('', '.', '..') or value.startswith('.'):
        value = '%2E' + value[1:] if value else '_'
    value = value.rstrip(' .') or '_'
    while len(value.encode()) > 170:
        value = value[:-1]
    return value


def contained(root, relative):
    relative = Path(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise OutlineError('Unsafe managed path')
    path = root
    for part in relative.parts:
        path = path / part
        if path.is_symlink():
            raise OutlineError('Symlink in managed path')
    if not path.resolve().is_relative_to(root.resolve()):
        raise OutlineError('Managed path escaped target')
    return path


def read_hash(path):
    return digest(path.read_bytes()) if path.is_file() else None


def atomic_write(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix='.outline-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(root):
    if root.is_symlink():
        raise OutlineError('Target cannot be a symlink')
    root.mkdir(parents=True, exist_ok=True)
    meta = contained(root, '.outline-sync')
    meta.mkdir(exist_ok=True)
    lock = contained(root, '.outline-sync/sync.lock')
    try:
        fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise OutlineError('Sync lock exists; verify owner process before removing a stale lock') from None
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump({'pid': os.getpid(), 'started_at': datetime.now(timezone.utc).isoformat()}, handle)
        yield
    finally:
        lock.unlink()


def version(doc):
    return {'updated_at': doc.get('updatedAt'), 'revision': doc.get('revision')}


def complete_tree(tree, documents):
    """Recover omitted nodes from explicit parent IDs; never invent missing parents."""
    tree = copy.deepcopy(tree)
    nodes = {}
    def index(items):
        for item in items:
            if item['id'] in nodes:
                raise OutlineError('Duplicate or cyclic directory tree')
            nodes[item['id']] = item
            index(item.get('children') or [])
    index(tree)
    original_count = len(nodes)
    by_id = {doc['id']: doc for doc in documents}
    visiting = set()
    def insert(doc_id):
        if doc_id in nodes:
            return nodes[doc_id]
        if doc_id in visiting or doc_id not in by_id:
            return None
        visiting.add(doc_id)
        doc = by_id[doc_id]
        parent_id = doc.get('parentDocumentId')
        siblings = tree
        if parent_id:
            parent = insert(parent_id)
            if parent is None:
                visiting.remove(doc_id)
                return None
            siblings = parent.setdefault('children', [])
        node = {'id': doc_id, 'title': doc['title'], 'url': doc.get('url'), 'children': []}
        siblings.append(node)
        nodes[doc_id] = node
        visiting.remove(doc_id)
        return node
    for doc in documents:
        insert(doc['id'])
    return tree, len(nodes) - original_count


def layout(collections, trees):
    paths, nodes, navigation, reserved = {}, {}, [], set()
    def names(items, title_key):
        counts = {}
        for item in items:
            key = safe_name(item[title_key]).casefold()
            counts[key] = counts.get(key, 0) + 1
        used = set()
        for item in items:
            name = safe_name(item[title_key])
            if counts[name.casefold()] > 1 or name.casefold() in {'_附件', '_目录.md', '.outline-sync'}:
                name += '~' + digest(str(item['id']).encode())[:12]
            if name.casefold() in used:
                raise OutlineError('Path name collision')
            used.add(name.casefold())
            yield item, name
    def walk(items, parent, depth):
        for node, name in names(items, 'title'):
            doc_id = node['id']
            if doc_id in nodes:
                raise OutlineError('Cycle or duplicate document in directory tree')
            folder = parent / name
            path = (folder / (name + '.md')).as_posix()
            if path.casefold() in reserved:
                raise OutlineError('Directory collision')
            reserved.add(path.casefold())
            paths[doc_id], nodes[doc_id] = path, node
            navigation.append((depth, node['title'], path))
            walk(node.get('children') or [], folder, depth + 1)
    for collection, name in names(collections, 'name'):
        navigation.append((0, collection['name'], None))
        walk(trees.get(collection['id'], []), Path(name), 1)
    return paths, nodes, navigation


def managed_write(root, relative, body, expected_hash, state, checkpoint):
    path = contained(root, relative)
    current = read_hash(path)
    pending = state.setdefault('pending', {})
    wanted = digest(body)
    if current == wanted and (expected_hash == current or pending.get(relative) == wanted):
        pending.pop(relative, None)
        return wanted
    if current != expected_hash or (path.exists() and not path.is_file()):
        raise OutlineError('Local edit or unmanaged file conflict')
    if current is not None:
        backup = contained(root, '.outline-sync/backups/' + digest(relative.encode()) + '/' + current + path.suffix)
        if backup.exists() and read_hash(backup) != current:
            raise OutlineError('Backup conflict')
        if not backup.exists():
            atomic_write(backup, path.read_bytes())
    pending[relative] = wanted
    checkpoint()
    path = contained(root, relative)
    if read_hash(path) != current:
        raise OutlineError('Concurrent edit detected before write')
    # Hash comparison + replace is not atomic against arbitrary external editors.
    atomic_write(path, body)
    if read_hash(path) != wanted:
        raise OutlineError('Write verification failed')
    pending.pop(relative, None)
    return wanted


def sync(client, target, *, progress=None, reuse_export=None):
    root = Path(target).absolute()
    started = datetime.now(ZoneInfo('Asia/Shanghai')).isoformat()
    result = {'saved': 0, 'skipped': 0, 'conflicts': 0, 'errors': [], 'warnings': [], 'retained_paths': [], 'remote_missing': [], 'started_at': started, 'reused_media': 0}
    with locked(root), ExitStack() as resources:
        state_path = contained(root, '.outline-sync/state.json')
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text())
            except (ValueError, UnicodeError):
                raise OutlineError('Invalid sync state; restore a verified state backup') from None
            if state.get('source') != client.base_url or state.get('schema') != 1:
                raise OutlineError('State belongs to a different source or schema')
        else:
            state = {'schema': 1, 'source': client.base_url, 'documents': {}, 'baseline_complete': False, 'generated': {}, 'assets': {}}
        baseline_before = state.get('discovery_baseline_complete', state['baseline_complete'])
        def checkpoint():
            atomic_write(contained(root, '.outline-sync/state.json'), (json.dumps(state, ensure_ascii=False, indent=2) + '\n').encode())
        state['run'] = dict(result, maintenance='pending')
        checkpoint()
        collections = client.pages('collections.list')
        trees, docs = {}, {}
        for collection in collections:
            cid = collection['id']
            if progress:
                progress('collection', collection['name'])
            try:
                tree = client.call('collections.documents', {'id': cid})['data']
                if not isinstance(tree, list):
                    raise OutlineError('Invalid directory tree')
                # index sorting on legacy Outline restricts the list to root siblings.
                # Creation time is stable while editors update document bodies during a crawl.
                batch = client.pages('documents.list', collectionId=cid, sort='createdAt', direction='ASC')
                batch = [doc for doc in batch if not doc.get('archivedAt') and not doc.get('deletedAt')]
                trees[cid], recovered = complete_tree(tree, batch)
                if recovered:
                    result['warnings'].append({'collection_id': cid, 'reason': 'Directory nodes recovered from parent metadata; original sibling order unavailable, recovered nodes follow creation order', 'recovered_nodes': recovered})
                docs.update({doc['id']: doc for doc in batch})
            except OutlineError as error:
                result['errors'].append({'collection_id': cid, 'reason': str(error)})
        paths, nodes, navigation = layout(collections, trees)
        for doc_id in paths:
            if doc_id not in docs:
                try:
                    docs[doc_id] = client.call('documents.info', {'id': doc_id})['data']
                except OutlineError as error:
                    result['errors'].append({'document_id': doc_id, 'reason': str(error)})
        extra = sorted(set(docs) - set(paths))
        if extra:
            result['errors'].append({'reason': 'Published documents absent from directory tree', 'document_ids': extra})
        url_paths = {}
        for doc_id, path in paths.items():
            doc = docs.get(doc_id, nodes[doc_id])
            for url in (doc.get('url'), nodes[doc_id].get('url')):
                if url:
                    absolute = urllib.parse.urljoin(client.base_url, url)
                    if origin(absolute) == origin(client.base_url):
                        url_paths[urllib.parse.urlsplit(absolute).path.rstrip('/')] = path
            url_paths['/doc/' + doc_id] = path
        layout_hash = digest(json.dumps(url_paths, sort_keys=True, ensure_ascii=False).encode())
        comments_by_doc = {doc_id: [] for doc_id in paths}
        result['comments_complete'] = False
        try:
            rows = normalize_comments(client.pages('comments.list', sort='createdAt', direction='ASC'))
            for row in rows:
                if row['documentId'] in comments_by_doc:
                    comments_by_doc[row['documentId']].append(row)
            result['comments_complete'] = True
            result['comments'] = sum(len(rows) for rows in comments_by_doc.values())
        except OutlineError as error:
            result['errors'].append({'reason': 'Comments incomplete: ' + str(error)})
        state['tree'] = [{'collection': {'id': c['id'], 'name': c['name']}, 'documents': trees.get(c['id'], [])} for c in collections]
        if reuse_export is not None:
            def save_reused(relative, body, sha):
                path = contained(root, relative)
                if path.exists():
                    return read_hash(path) == sha
                atomic_write(path, body)
                return read_hash(path) == sha
            reused = reuse_assets(client, docs, reuse_export, save_reused)
            state['assets'].update(reused)
            result['reused_media'] = len(reused)
            if progress:
                progress('reused_media', str(len(reused)))
        media = resources.enter_context(MediaStore(client, root, state['assets']))
        prefetch_urls = []
        for doc_id, relative in paths.items():
            doc, old = docs.get(doc_id, {}), state['documents'].get(doc_id)
            if old and any(value is not None for value in version(doc).values()) and old.get('media_complete', True) and old['path'] == relative and old['version'] == version(doc) and old.get('layout_hash') == layout_hash and read_hash(contained(root, relative)) == old['sha256'] and all(read_hash(contained(root, x['path'])) == x['sha256'] for x in old.get('assets', [])):
                continue
            def gather(url, image):
                absolute = urllib.parse.urljoin(client.base_url + '/', url.replace('\\)', ')').replace('\\(', '('))
                parsed = urllib.parse.urlsplit(absolute)
                attachment = origin(absolute) == origin(client.base_url) and parsed.path in ('/api/attachments.redirect', '/api/files.get')
                if (image or attachment) and parsed.scheme in ('http', 'https'):
                    prefetch_urls.append(absolute)
                return url
            rewrite(doc.get('text', ''), gather)
        media.prefetch(prefetch_urls)
        for doc_id, relative in paths.items():
            if doc_id not in docs:
                continue
            doc, old = docs[doc_id], state['documents'].get(doc_id)
            try:
                path = contained(root, relative)
                old_hash = old.get('sha256') if old and old['path'] == relative else None
                if old:
                    existing_hash = read_hash(contained(root, old['path']))
                    journaled_hash = state.get('pending', {}).get(old['path'])
                    if existing_hash not in (None, old['sha256'], journaled_hash):
                        raise OutlineError('Local edit or unmanaged file conflict')
                    if journaled_hash and existing_hash == journaled_hash and old['path'] == relative:
                        old_hash = existing_hash
                assets_ok = old is not None and all(read_hash(contained(root, item['path'])) == item['sha256'] for item in old.get('assets', []))
                if not result['comments_complete'] and old:
                    result['skipped'] += 1
                    continue
                comment_rows = comments_by_doc[doc_id]
                comment_hash = fingerprint(comment_rows)
                comments_ok = old and old.get('comments_complete') and old.get('comments_hash') == comment_hash and old.get('comment_render_version') == RENDER_VERSION
                if comments_ok and old and any(value is not None for value in version(doc).values()) and old.get('media_complete', True) and old['path'] == relative and old['version'] == version(doc) and old.get('layout_hash') == layout_hash and read_hash(path) == old['sha256'] and assets_ok:
                    result['skipped'] += 1
                    continue
                if 'text' not in doc:
                    full = client.call('documents.info', {'id': doc_id})['data']
                    if version(full) != version(doc):
                        raise OutlineError('Document changed during fetch; retry')
                    doc = full
                if not isinstance(doc.get('text'), str):
                    raise OutlineError('Document has no Markdown text')
                assets, media_errors = {}, []
                def replace(url, image):
                    absolute = urllib.parse.urljoin(client.base_url + '/', url.replace('\\)', ')').replace('\\(', '('))
                    parsed = urllib.parse.urlsplit(absolute)
                    local_doc = url_paths.get(parsed.path.rstrip('/')) if origin(absolute) == origin(client.base_url) else None
                    if local_doc and not image:
                        link = urllib.parse.quote(os.path.relpath(local_doc, Path(relative).parent), safe='/')
                        return link + ('#' + parsed.fragment if parsed.fragment else '')
                    attachment = origin(absolute) == origin(client.base_url) and parsed.path in ('/api/attachments.redirect', '/api/files.get')
                    if not image and not attachment:
                        return url
                    if parsed.scheme not in ('https', 'http'):
                        if parsed.scheme == 'data':
                            return url
                        media_errors.append({'resource_hash': digest(absolute.encode()), 'reason': 'Unsupported embedded media scheme'})
                        return url
                    key = digest(absolute.encode())
                    cached = state['assets'].get(key)
                    if cached and read_hash(contained(root, cached['path'])) == cached['sha256']:
                        item = cached
                    else:
                        try:
                            item = media.get(absolute)
                        except (OutlineError, OSError) as error:
                            media_errors.append({'resource_hash': key, 'reason': str(error) if isinstance(error, OutlineError) else type(error).__name__})
                            return url
                        state['assets'][key] = item
                    assets[key] = item
                    return urllib.parse.quote(os.path.relpath(item['path'], Path(relative).parent), safe='/')
                markdown = rewrite(doc['text'], replace)
                rich_data, anchor_complete = None, True
                if comment_rows:
                    try:
                        rich = client.rich_document(doc_id)
                        if version(rich) != version(doc):
                            raise OutlineError('Document changed while fetching comment anchors; retry')
                        rich_data = rich.get('data')
                    except OutlineError as error:
                        anchor_complete = False
                        result['errors'].append({'document_id': doc_id, 'reason': 'Comment anchors incomplete: ' + str(error)})
                placements=plan_comments(markdown,rich_data,comment_rows)
                markdown, unmapped = render_comments(markdown, rich_data, comment_rows, replace, placements=placements)
                rendered = markdown.encode()
                comment_snapshot = None
                if result['comments_complete']:
                    comment_snapshot = '.outline-sync/comments/' + digest(doc_id.encode()) + '/' + comment_hash + '.json'
                    snapshot = contained(root, comment_snapshot)
                    body = (json.dumps(comment_rows, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode()
                    if snapshot.exists() and snapshot.read_bytes() != body:
                        raise OutlineError('Comment snapshot conflict')
                    if not snapshot.exists():
                        atomic_write(snapshot, body)
                original_hash = digest(doc['text'].encode())
                original_path = '.outline-sync/originals/' + digest(doc_id.encode()) + '/' + original_hash + '.md'
                original = contained(root, original_path)
                if not original.exists():
                    atomic_write(original, doc['text'].encode())
                elif read_hash(original) != original_hash:
                    raise OutlineError('Original snapshot conflict')
                new_hash = managed_write(root, relative, rendered, old_hash, state, checkpoint)
                retained = list(old.get('retained_paths', [])) if old else []
                if old and old['path'] != relative:
                    retained.append(old['path'])
                    result['retained_paths'].append(old['path'])
                state['documents'][doc_id] = {
                    'path': relative, 'title': doc['title'], 'version': version(doc), 'sha256': new_hash,
                    'layout_hash': layout_hash, 'assets': list(assets.values()), 'original': original_path,
                    'media_complete': not media_errors, 'media_errors': media_errors,
                    'comments_hash': comment_hash if result['comments_complete'] else None,
                    'comments_snapshot': comment_snapshot,
                    'comments_complete': result['comments_complete'] and anchor_complete,
                    'comment_render_version': RENDER_VERSION,
                    'unmapped_comments': unmapped,
                    'comment_placements': placements,
                    'created_at': doc.get('createdAt'), 'source_url': urllib.parse.urljoin(client.base_url, doc.get('url', '')).split('?')[0].split('#')[0],
                    'first_seen_at': old.get('first_seen_at') if old else started,
                    'baseline_complete_before_discovery': old.get('baseline_complete_before_discovery', False) if old else baseline_before,
                    'retained_paths': retained, 'collected_at': started,
                }
                result['saved'] += 1
                if media_errors:
                    result['errors'].append({'document_id': doc_id, 'reason': 'Embedded media incomplete', 'media_errors': media_errors})
                checkpoint()
                if progress:
                    progress('saved', f"{result['saved']} / {len(paths)}")
            except (OutlineError, OSError, UnicodeError) as error:
                reason = str(error) if isinstance(error, OutlineError) else type(error).__name__
                if 'conflict' in reason.lower() or 'Concurrent edit' in reason:
                    result['conflicts'] += 1
                result['errors'].append({'document_id': doc_id, 'reason': reason})
        if not any('collection_id' in error for error in result['errors']):
            result['remote_missing'] = sorted(set(state['documents']) - set(paths))
        nav = '# 企业知识库目录\n\n按 Outline 返回的目录顺序排列；远端只读。\n\n'
        saved_paths = {item['path'] for item in state['documents'].values()}
        for depth, title, path in navigation:
            title = title.replace('\n', ' ').replace('[', '\\[').replace(']', '\\]')
            nav += '  ' * depth + '- ' + (f'[{title}]({urllib.parse.quote(path, safe="/")})' if path and path in saved_paths else title) + '\n'
        try:
            nav_hash = managed_write(root, '_目录.md', nav.encode(), state['generated'].get('_目录.md'), state, checkpoint)
            state['generated']['_目录.md'] = nav_hash
        except OutlineError as error:
            result['errors'].append({'reason': str(error), 'file': '_目录.md'})
        state['discovery_baseline_complete'] = baseline_before or (not any('collection_id' in error for error in result['errors']) and set(paths) == set(docs))
        state['baseline_complete'] = state['baseline_complete'] or not result['errors']
        state['run'] = dict(result, maintenance='pending', finished_at=datetime.now(ZoneInfo('Asia/Shanghai')).isoformat())
        checkpoint()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('probe', 'sync'))
    parser.add_argument('--url', default=os.environ.get('OUTLINE_URL'))
    parser.add_argument('--page-size', type=int, default=25, help='1..100; smaller pages help slow servers')
    parser.add_argument('--target', type=Path, help='Explicit mirror directory inside the chosen vault')
    parser.add_argument('--reuse-export', type=Path, help='Read-only historical export with outline_id/updated_at frontmatter')
    args = parser.parse_args()
    if not args.url or (args.command == 'sync' and not args.target):
        parser.error('--url and, for sync, --target are required')
    token = os.environ.get('OUTLINE_TOKEN', '').strip()
    if not token:
        parser.error('OUTLINE_TOKEN is required in the process environment; interactive token input is disabled')
    try:
        client = OutlineClient(args.url, token, page_size=args.page_size)
        if args.command == 'probe':
            collections = client.pages('collections.list')
            result = {'collections': len(collections), 'directory_roots': 0, 'sample_markdown': False, 'sample_image': 'not_found'}
            for collection in collections[:1]:
                tree = client.call('collections.documents', {'id': collection['id']})['data']
                result['directory_roots'] += len(tree)
                docs = client.call('documents.list', {'collectionId': collection['id'], 'limit': args.page_size, 'offset': 0})['data']
                for doc in docs:
                    if not isinstance(doc.get('text'), str):
                        doc = client.call('documents.info', {'id': doc['id']})['data']
                    result['sample_markdown'] = isinstance(doc.get('text'), str)
                    found = []
                    def find_image(url, image):
                        if image:
                            found.append(url)
                        return url
                    rewrite(doc.get('text', ''), find_image)
                    if found:
                        try:
                            body, mime = client.download(found[0])
                            result.update(sample_image='downloaded', image_bytes=len(body), image_mime=mime)
                        except OutlineError as error:
                            result.update(sample_image='failed', image_error=str(error))
                        break
        else:
            result = sync(client, args.target, reuse_export=args.reuse_export, progress=lambda kind, value: print(f'{kind}: {value}', file=sys.stderr, flush=True))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 1 if result.get('errors') or result.get('sample_image') == 'failed' else 0
    except (OutlineError, OSError, KeyError, TypeError, ValueError, RecursionError) as error:
        print('Outline sync failed: ' + (str(error) if isinstance(error, OutlineError) else type(error).__name__), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
