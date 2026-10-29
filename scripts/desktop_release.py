"""Flatten reviewed installers into predictable names plus SHA256SUMS."""
import argparse
import hashlib
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument('--assets', type=Path, required=True)
parser.add_argument('--version', required=True)


def main():
    args = parser.parse_args()
    sums = []
    expected = {
        'x86_64-pc-windows-msvc': {'.exe', '.msi'},
        'x86_64-unknown-linux-gnu': {'.AppImage', '.deb'},
        'aarch64-apple-darwin': {'.dmg'},
        'x86_64-apple-darwin': {'.dmg'},
    }
    for target, extensions in expected.items():
        source = args.assets / f'desktop-{target}'
        found = {}
        for path in source.rglob('*'):
            if path.is_file() and path.suffix in extensions:
                if path.suffix in found:
                    raise RuntimeError(f'Ambiguous installer for {target}')
                found[path.suffix] = path
        if set(found) != extensions:
            raise RuntimeError(f'Missing installer for {target}')
        for extension, path in found.items():
            name = f'xuedao_{args.version}_{target}{extension}'
            destination = args.assets / name
            path.replace(destination)
            with destination.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            sums.append(f'{digest}  {name}\n')
    (args.assets / 'SHA256SUMS').write_text(''.join(sorted(sums)), encoding='utf-8')


if __name__ == '__main__':
    main()
