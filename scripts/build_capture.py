"""Build the local macOS ScreenCaptureKit fallback, without signing/notarization."""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('data/native/scribeloop-sck'))
    args = parser.parse_args()
    if sys.platform != 'darwin':
        parser.error('This helper requires macOS and the Xcode command-line tools')
    root = Path(__file__).resolve().parents[1]
    source = root / 'core/scribeloop_core/capture/native/macos_sck/main.swift'
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(['xcrun', 'swiftc', '-parse-as-library', '-O', '-module-cache-path',
                    str(root / 'data/native/swift-cache'), str(source), '-o', str(output)], check=True)
    subprocess.run([str(output), '--version'], check=True)


if __name__ == '__main__':
    main()
