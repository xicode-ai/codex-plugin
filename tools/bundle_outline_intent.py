#!/usr/bin/env python3
"""Bundle shared Outline intent resources into independently installable skills."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
NAMES = ('outline-intent-sync', 'outline-intent-review', 'outline-intent-revise')
RESOURCES = {
    'outline_intent.py': 'scripts/outline_intent.py',
    'intent-template.md': 'assets/intent-template.md',
    'workflow-contract.md': 'references/workflow-contract.md',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check drift without writing')
    parser.add_argument('--plugin', action='append', choices=NAMES)
    args = parser.parse_args()
    sources = {}
    for source, destination in RESOURCES.items():
        path = ROOT / 'tools/outline_intent' / source
        if not path.is_file():
            parser.error(f'Canonical resource missing: {path.relative_to(ROOT)}')
        sources[destination] = path.read_bytes()
    manifest = {
        'bundle_version': 1,
        'template_version': 'trellis-intent-2026-11-10',
        'sha256': {p: hashlib.sha256(b).hexdigest() for p, b in sources.items()},
    }
    sources['bundle.json'] = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    problems = []
    for name in args.plugin or NAMES:
        skill = ROOT / 'plugins' / name / 'skills' / name
        for destination, content in sources.items():
            target = skill / destination
            if args.check:
                if not target.is_file() or target.read_bytes() != content:
                    problems.append(str(target.relative_to(ROOT)))
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
    if problems:
        print('Bundle drift: ' + ', '.join(problems), file=sys.stderr)
        return 1
    print(('Checked' if args.check else 'Bundled') + ': ' + ', '.join(args.plugin or NAMES))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
