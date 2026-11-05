"""Read-only Outline API and bounded attachment transport (stdlib only)."""
import json
import gzip
import io
import time
import urllib.error
import urllib.parse
import urllib.request


class OutlineError(Exception):
    """Safe diagnostic: never include HTTP bodies, credentials or signed URLs."""


def origin(url):
    p = urllib.parse.urlsplit(url)
    return p.scheme, p.hostname, p.port or (443 if p.scheme == 'https' else 80)


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, base_url, api=False, allow_http=False):
        self.base_url, self.api, self.allow_http = base_url, api, allow_http

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        p = urllib.parse.urlsplit(newurl)
        if p.scheme not in ('https', 'http') or p.username or p.password:
            raise OutlineError('Unsafe redirect')
        if p.scheme != 'https' and not self.allow_http:
            raise OutlineError('Insecure redirect')
        if self.api and origin(newurl) != origin(self.base_url):
            raise OutlineError('Cross-origin API redirect refused')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected and origin(req.full_url) != origin(newurl):
            redirected.remove_header('Authorization')
            redirected.remove_header('Cookie')
        return redirected


class OutlineClient:
    READ_METHODS = {'collections.list', 'collections.documents', 'documents.list', 'documents.info', 'attachments.info'}

    def __init__(self, base_url, token, *, timeout=30, retries=3, allow_http=False, page_size=25):
        p = urllib.parse.urlsplit(base_url)
        if (p.scheme != 'https' and not (allow_http and p.scheme == 'http')) or not p.hostname or p.username or p.password or p.query or p.fragment:
            raise OutlineError('Use an HTTPS Outline base URL without credentials or query')
        self.base_url = base_url.rstrip('/')
        if not isinstance(page_size, int) or not 1 <= page_size <= 100:
            raise OutlineError('Page size must be between 1 and 100')
        self.page_size = page_size
        self.token, self.timeout, self.retries = token, timeout, retries
        self.allow_http = allow_http
        self.api_opener = urllib.request.build_opener(SafeRedirect(self.base_url, api=True, allow_http=allow_http))
        self.file_opener = urllib.request.build_opener(SafeRedirect(self.base_url, allow_http=allow_http))

    def request(self, url, data=None, *, api=False, max_bytes=64 * 1024 * 1024):
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ('http', 'https') or p.username or p.password or (p.scheme == 'http' and not self.allow_http):
            raise OutlineError('Unsafe download URL')
        for attempt in range(self.retries):
            headers = {'User-Agent': 'outline-obsidian-sync/0.1'}
            if api:
                headers['Accept-Encoding'] = 'gzip'
            if origin(url) == origin(self.base_url):
                headers['Authorization'] = 'Bearer ' + self.token
            if data is not None:
                headers['Content-Type'] = 'application/json'
            req = urllib.request.Request(url, data=data, headers=headers)
            try:
                with (self.api_opener if api else self.file_opener).open(req, timeout=self.timeout) as response:
                    body = response.read(max_bytes + 1)
                    if len(body) > max_bytes:
                        raise OutlineError('Response exceeds size limit')
                    if response.headers.get('Content-Encoding', '').lower() == 'gzip':
                        try:
                            with gzip.GzipFile(fileobj=io.BytesIO(body)) as decoded:
                                body = decoded.read(max_bytes + 1)
                        except (OSError, EOFError):
                            raise OutlineError('Invalid compressed response') from None
                        if len(body) > max_bytes:
                            raise OutlineError('Decoded response exceeds size limit')
                    return body, response.headers.get_content_type()
            except urllib.error.HTTPError as exc:
                retry = exc.code == 429 or exc.code >= 500
                if not retry or attempt == self.retries - 1:
                    code = exc.code
                    exc.close()
                    raise OutlineError(f'HTTP {code}') from None
                try:
                    delay = min(float(exc.headers.get('Retry-After', 2 ** attempt)), 10)
                except ValueError:
                    delay = 2 ** attempt
                exc.close()
            except (urllib.error.URLError, TimeoutError, OSError):
                if attempt == self.retries - 1:
                    raise OutlineError('Network request failed or timed out') from None
                delay = 2 ** attempt
            time.sleep(max(delay, 0))
        raise OutlineError('No request attempts configured')

    def call(self, name, params):
        if name not in self.READ_METHODS:
            raise OutlineError('Only read-only API methods are allowed')
        raw, _ = self.request(self.base_url + '/api/' + name, json.dumps(params).encode(), api=True)
        try:
            result = json.loads(raw)
        except (ValueError, UnicodeError):
            raise OutlineError('Invalid JSON API response') from None
        if not isinstance(result, dict) or result.get('ok') is False or result.get('success') is False or 'data' not in result:
            raise OutlineError('API response missing successful data')
        return result

    def pages(self, name, **params):
        offset, seen, items = 0, set(), []
        for _ in range(10000):
            response = self.call(name, dict(params, limit=self.page_size, offset=offset))
            batch = response.get('data')
            if not isinstance(batch, list):
                raise OutlineError('Invalid paginated response')
            page_ids = [x.get('id') for x in batch]
            if any(not x or x in seen for x in page_ids) or len(page_ids) != len(set(page_ids)):
                raise OutlineError('Pagination repeated or missing IDs; retry a stable snapshot')
            items.extend(batch)
            seen.update(page_ids)
            pagination = response.get('pagination', {})
            total = pagination.get('total')
            offset += len(batch)
            if not batch:
                if isinstance(total, int) and offset < total:
                    raise OutlineError('Pagination ended before advertised total')
                return items
            # Some deployments report the page count as total, not the collection count.
            # A full page or explicit nextPath therefore always needs another request.
            if not pagination.get('nextPath') and len(batch) < self.page_size and (total is None or offset >= total):
                return items
        raise OutlineError('Pagination limit exceeded')

    def download(self, url):
        url = urllib.parse.urljoin(self.base_url + '/', url)
        body, mime = self.request(url)
        if mime in ('text/html', 'application/json'):
            raise OutlineError('Attachment returned HTML/JSON instead of file bytes')
        if not body:
            raise OutlineError('Empty attachment')
        return body, mime
