"""Build local macOS capture or metadata helper, without signing/notarization."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=['capture', 'observer', 'meeting'], default='capture')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--minimum-macos', default='13.0')
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This helper requires macOS and the Xcode command-line tools')
    root = Path(__file__).resolve().parents[1]
    name, binary = {'capture': ('macos_sck', 'scribeloop-sck'),
                    'observer': ('macos_observer', 'scribeloop-observer'),
                    'meeting': ('macos_meeting', 'scribeloop-meeting')}[args.kind]
    source = root / 'core/scribeloop_core/capture/native' / name / 'main.swift'
    output = (args.output or root / 'data/native' / binary).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = (['-Xlinker', '-sectcreate', '-Xlinker', '__TEXT', '-Xlinker', '__info_plist',
                 '-Xlinker', str(source.parent / 'Info.plist')] if args.kind == 'meeting' else [])
    import re
    if not re.fullmatch(r'\d+\.\d+(?:\.\d+)?',args.minimum_macos):
        parser.error('Invalid macOS deployment version')
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-O',
                    '-target',f'arm64-apple-macos{args.minimum_macos}', '-module-cache-path',
                    str(root / 'data/native/swift-cache'), str(source), *metadata, '-o', str(output)], check=True)
    subprocess.run([str(output), '--version'], check=True)


if __name__ == '__main__':
    main()
