"""Bounded concurrent transport; document/state writes remain on the sync thread."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import mimetypes
import os
from pathlib import Path
import tempfile
from outline_client import OutlineError


class MediaStore:
    def __init__(self, client, root, cache, workers=4):
        self.client, self.root, self.cache = client, root, dict(cache)
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self.futures = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.pool.shutdown(wait=True, cancel_futures=True)

    def prefetch(self, urls):
        for url in urls:
            if url not in self.futures:
                self.futures[url] = self.pool.submit(self.fetch, url)

    def get(self, url):
        self.prefetch([url])
        return self.futures[url].result()

    def safe(self, relative):
        relative = Path(relative)
        if relative.is_absolute() or '..' in relative.parts:
            raise OutlineError('Unsafe attachment path')
        path = self.root
        for part in relative.parts:
            path = path / part
            if path.is_symlink():
                raise OutlineError('Symlink in attachment path')
        return path

    def fetch(self, url):
        key = hashlib.sha256(url.encode()).hexdigest()
        cached = self.cache.get(key)
        if cached:
            path = self.safe(cached['path'])
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == cached['sha256']:
                return cached
        body, mime = self.client.download(url)
        sha = hashlib.sha256(body).hexdigest()
        ext = mimetypes.guess_extension(mime) or '.bin'
        if ext == '.jpe':
            ext = '.jpg'
        relative = '_附件/' + sha + ext
        path = self.safe(relative)
        if path.exists():
            if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != sha:
                raise OutlineError('Local attachment edit conflict')
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, temp = tempfile.mkstemp(prefix='.outline-', dir=path.parent)
            try:
                with os.fdopen(fd, 'wb') as output:
                    output.write(body)
                self.safe(relative)
                os.replace(temp, path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)
        return {'path': relative, 'sha256': sha}
