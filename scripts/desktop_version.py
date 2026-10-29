"""Map a desktop preview SemVer to the numeric version required by Windows MSI."""
import argparse
import json
import re
from pathlib import Path


def windows_version(version):
    match = re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-beta\.([1-9][0-9]*))?', version)
    if not match:
        raise ValueError('Desktop versions must be X.Y.Z or X.Y.Z-beta.N')
    major, minor, patch = (int(match[i]) for i in (1, 2, 3))
    revision = int(match[4]) if match[4] else 65535
    if major > 255 or minor > 255 or patch > 65535 or not 1 <= revision <= 65535 or (match[4] and revision == 65535):
        raise ValueError('Version exceeds Windows MSI numeric limits')
    return f'{major}.{minor}.{patch}.{revision}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('version')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    numeric = windows_version(args.version)
    if not args.check:
        path = Path(__file__).resolve().parents[1] / 'frontend/src-tauri/tauri.conf.json'
        config = json.loads(path.read_text(encoding='utf-8'))
        config['bundle']['windows']['wix']['version'] = numeric
        path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(f'MSI version: {numeric}')


if __name__ == '__main__':
    main()
