"""Check frozen dependency paths and parent-pipe shutdown; no provider requests."""
import json
import secrets
import select
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / 'dist/macos/ScribeLoop M0.app'
CORE = APP / 'Contents/Resources/Core/scribeloop-core-m0'


def main():
    inspected = 0
    for path in APP.rglob('*'):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open('rb') as f:
            magic = f.read(4)
        if magic not in (b'\xcf\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xca\xfe\xba\xbe'):
            continue
        inspected += 1
        linked = subprocess.check_output(['otool', '-L', str(path)], text=True)
        for line in linked.splitlines()[1:]:
            dependency = line.strip().split(' (')[0]
            if dependency.startswith('/') and not dependency.startswith(('/usr/lib/', '/System/Library/')):
                raise RuntimeError(f'Unbundled native dependency: {path.name}: {dependency}')
    results = []
    for control in (b'STOP\n', b''):
        child = subprocess.Popen([str(CORE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, cwd='/', env={'PATH': '/usr/bin:/bin'})
        try:
            bootstrap = {'protocol': 1, 'token': secrets.token_hex(32),
                             'launch_id': secrets.token_hex(16), 'fixture': True}
            child.stdin.write(json.dumps(bootstrap).encode() + b'\n')
            child.stdin.flush()
            if not select.select([child.stdout], [], [], 35)[0]:
                raise TimeoutError('Frozen startup timed out')
            ready = child.stdout.readline(2049)
            output, _ = child.communicate(input=control, timeout=10)
            output = ready + output
            messages = [json.loads(line) for line in output.splitlines()]
            assert child.returncode == 0 and messages[0]['type'] == 'ready'
            assert bootstrap['token'] not in output.decode()
            results.append('STOP' if control else 'EOF')
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
    print(json.dumps({'native_binaries_checked': inspected, 'external_dependencies': 0,
                      'parent_shutdown': results, 'ok': True}))


if __name__ == '__main__':
    main()
