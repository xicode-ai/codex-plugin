#!/usr/bin/env python3
"""Read-only helpers for an Obsidian knowledge-base maintenance run."""
import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo('Asia/Shanghai')
EXCLUDED = {'.git', '.obsidian', '.trash', '.cache', 'node_modules', '知识库阅读导读', '备份', 'backup', 'backups'}


def local_date(value):
    if not isinstance(value, str):
        return None
    try:
        date = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if date.tzinfo is None or date.utcoffset() is None:
            return None
        return date.astimezone(SHANGHAI).date().isoformat()
    except ValueError:
        return None


def is_today(item, run_date):
    if item.get('previously_reported') or item.get('historical_backfill'):
        return False
    if item.get('collected_at'):
        return local_date(item['collected_at']) == run_date
    return item.get('baseline_complete_before_discovery') is True and local_date(item.get('first_seen_at')) == run_date


def inventory(root):
    root = Path(root).resolve(strict=True)
    rows = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED and not d.startswith('.') and not (Path(current) / d).is_symlink())
        for name in sorted(files):
            path = Path(current) / name
            if name.startswith('.') or path.is_symlink() or path.suffix.lower() != '.md':
                continue
            data = path.read_bytes()
            rows.append({'path': path.relative_to(root).as_posix(), 'sha256': hashlib.sha256(data).hexdigest()})
    return sorted(rows, key=lambda row: row['path'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    scan = sub.add_parser('inventory')
    scan.add_argument('vault')
    date = sub.add_parser('eligible')
    date.add_argument('date', help='run start date, YYYY-MM-DD in Asia/Shanghai')
    date.add_argument('input', help='JSON array of source records')
    args = parser.parse_args()
    if args.command == 'inventory':
        result = inventory(args.vault)
    else:
        datetime.strptime(args.date, '%Y-%m-%d')
        records = json.loads(Path(args.input).read_text(encoding='utf-8'))
        result = [item for item in records if is_today(item, args.date)]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
