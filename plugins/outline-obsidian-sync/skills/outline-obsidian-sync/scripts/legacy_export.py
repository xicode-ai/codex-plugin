"""Reuse images only from an identity/version/body-verified, read-only prior export."""
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.parse
from markdown_links import rewrite
from outline_client import OutlineError


def image_type(body):
    if body.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if body.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if body.startswith((b'GIF87a', b'GIF89a')):
        return '.gif'
    if body.startswith(b'RIFF') and body[8:12] == b'WEBP':
        return '.webp'
    if re.search(br'<svg(?:\s|>)', body[:1024], re.I) and b'<html' not in body[:1024].lower():
        return '.svg'
    return None


def reuse_assets(client, documents, export_root, save_asset):
    root = Path(export_root).absolute()
    if root.is_symlink() or not root.is_dir():
        raise OutlineError('Historical export must be an existing real directory')
    indexed = {}
    for base, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in {'assets', '_附件', 'Notion'} and not (Path(base) / d).is_symlink()]
        for name in files:
            path = Path(base) / name
            if not name.endswith('.md') or path.is_symlink():
                continue
            with path.open('rb') as source:
                head = source.read(4096).decode('utf-8', errors='replace')
            if not head.startswith('---\n') or '\n---\n' not in head:
                continue
            header = head.split('\n---\n', 1)[0]
            doc_match = re.search(r'^outline_id:\s*("[^"\n]+")\s*$', header, re.M)
            date_match = re.search(r'^updated_at:\s*("[^"\n]+")\s*$', header, re.M)
            if doc_match and date_match:
                try:
                    doc_id, updated = json.loads(doc_match[1]), json.loads(date_match[1])
                except ValueError:
                    continue
                indexed.setdefault(doc_id, []).append((path, updated))
    cache = {}
    for doc in documents.values():
        matches = indexed.get(doc['id'], [])
        if len(matches) != 1 or matches[0][1] != doc.get('updatedAt') or not isinstance(doc.get('text'), str):
            continue
        old_path = matches[0][0]
        before = old_path.stat()
        old = old_path.read_text().split('\n---\n', 1)[1]
        after = old_path.stat()
        if (before.st_mtime_ns, before.st_size) != (after.st_mtime_ns, after.st_size):
            continue
        old_images, new_images = [], []
        def normalize(text, images):
            def destination(url, image):
                if image:
                    images.append(url)
                return 'OUTLINE_RESOURCE'
            return rewrite(text, destination).strip()
        if normalize(old, old_images) != normalize(doc['text'], new_images) or len(old_images) != len(new_images):
            continue
        for old_url, new_url in zip(old_images, new_images):
            parsed = urllib.parse.urlsplit(old_url)
            if parsed.scheme or parsed.netloc:
                continue
            local = old_path.parent / urllib.parse.unquote(parsed.path)
            if not local.resolve().is_relative_to(root.resolve()) or not local.is_file() or local.is_symlink():
                continue
            if any(parent.is_symlink() for parent in local.parents if parent != root and root in parent.parents):
                continue
            if local.stat().st_size > 64 * 1024 * 1024:
                continue
            body = local.read_bytes()
            extension = image_type(body)
            if not extension:
                continue
            sha = hashlib.sha256(body).hexdigest()
            relative = '_附件/' + sha + extension
            if not save_asset(relative, body, sha):
                continue
            absolute = urllib.parse.urljoin(client.base_url + '/', new_url.replace('\\)', ')').replace('\\(', '('))
            if urllib.parse.urlsplit(absolute).scheme not in ('https', 'http'):
                continue
            key = hashlib.sha256(absolute.encode()).hexdigest()
            cache[key] = {'path': relative, 'sha256': sha}
    return cache
