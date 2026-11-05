"""Rewrite Markdown/HTML destinations without changing prose or fenced examples."""
import re


def rewrite(text, replace):
    protected = []
    # Mask code fences and inline code. Keep byte positions unchanged.
    for match in re.finditer(r'(?ms)^(`{3,}|~{3,})[^\n]*\n.*?^\1[^\n]*(?:\n|$)|`+[^`\n]*`+', text):
        protected.append(match.span())

    def is_code(start):
        return any(a <= start < b for a, b in protected)

    changes = []
    for match in re.finditer(r'(!?)\[[^\]\n]*\]\(\s*', text):
        if is_code(match.start()):
            continue
        start = match.end()
        if start >= len(text):
            continue
        if text[start] == '<':
            end = text.find('>', start + 1)
            if end < 0:
                continue
            start += 1
        else:
            end, depth = start, 0
            while end < len(text):
                char = text[end]
                if char == '\\':
                    end += 2
                    continue
                if char == '(':
                    depth += 1
                elif char == ')':
                    if depth == 0:
                        break
                    depth -= 1
                elif char.isspace() and depth == 0:
                    break
                end += 1
        url = text[start:end]
        new = replace(url, bool(match.group(1)))
        if new != url:
            changes.append((start, end, new))
    # Reference definitions (image references included); normal web references remain unchanged.
    image_refs = set()
    for match in re.finditer(r'!\[([^\]]*)\](?:\[([^\]]*)\])?(?!\()', text):
        if not is_code(match.start()):
            image_refs.add((match.group(2) or match.group(1)).casefold())
    for match in re.finditer(r'(?m)^\s{0,3}\[([^\]]+)\]:\s*<?([^\s>]+)', text):
        if not is_code(match.start()):
            changes.append((*match.span(2), replace(match.group(2), match.group(1).casefold() in image_refs)))
    for match in re.finditer(r'<(img|a|video|audio|source)\b[^>]*?\b(src|href)=["\']([^"\']+)["\']', text, re.I):
        if not is_code(match.start()):
            changes.append((*match.span(3), replace(match.group(3), match.group(1).lower() != 'a')))
    for start, end, value in sorted(changes, reverse=True):
        text = text[:start] + value + text[end:]
    return text
