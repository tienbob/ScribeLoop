"""Import the application module tree; opt into optional local ML stacks explicitly."""
from __future__ import annotations

import argparse
import importlib
import pkgutil

import scribeloop_core


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full-ml', action='store_true',
                        help='also require pyannote.audio and sherpa_onnx extras')
    args = parser.parse_args()
    modules = sorted(module.name for module in pkgutil.walk_packages(
        scribeloop_core.__path__, prefix='scribeloop_core.'))
    for module in modules:
        importlib.import_module(module)
    if args.full_ml:
        for module in ('pyannote.audio', 'sherpa_onnx', 'faster_whisper', 'silero_vad'):
            importlib.import_module(module)
    print(f'SMOKE-OK application_modules={len(modules)} full_ml={args.full_ml}')


if __name__ == '__main__':
    main()
