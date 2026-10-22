"""Build local macOS capture or metadata helper, without signing/notarization."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind', choices=['capture', 'observer'], default='capture')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This helper requires macOS and the Xcode command-line tools')
    root = Path(__file__).resolve().parents[1]
    name = 'macos_sck' if args.kind == 'capture' else 'macos_observer'
    binary = 'scribeloop-sck' if args.kind == 'capture' else 'scribeloop-observer'
    source = root / 'core/scribeloop_core/capture/native' / name / 'main.swift'
    output = (args.output or root / 'data/native' / binary).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-O', '-module-cache-path',
                    str(root / 'data/native/swift-cache'), str(source), '-o', str(output)], check=True)
    subprocess.run([str(output), '--version'], check=True)


if __name__ == '__main__':
    main()
