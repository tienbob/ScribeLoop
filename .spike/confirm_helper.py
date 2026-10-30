"""Silent process-only confirmation; never starts capture or plays audio."""
import os
import signal
import subprocess
import time
from pathlib import Path

root = Path(__file__).resolve().parents[1]
fixture = root/'.spike/LivenessTarget.app/Contents/MacOS/LivenessTarget'
helper = root/'build/macos/scribeloop-meeting-v6'
for scenario in ('graceful','force','fast_relaunch','stream_dead'):
    target = subprocess.Popen([str(fixture)])
    replacement = None
    try:
        time.sleep(.5)
        args = [str(helper),'--self-test-liveness-pid',str(target.pid)]
        if scenario == 'stream_dead': args.append('--stream-dead')
        probe = subprocess.Popen(args,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        time.sleep(.5)
        began = time.monotonic()
        os.kill(target.pid,signal.SIGTERM if scenario == 'graceful' else signal.SIGKILL)
        if scenario == 'fast_relaunch': replacement = subprocess.Popen([str(fixture)])
        data, err = probe.communicate(timeout=12)
        elapsed = time.monotonic()-began
        assert probe.returncode == 0 and len(data) == 32 and data[:4] == b'SLC2' and data[12:16] == b'SLE2'
        assert err == b'Source ended: selected_app_exited\n', err
        print(f'{scenario}: end after {elapsed:.3f}s; one marker; SLE2; exit=0; no PCM',flush=True)
    finally:
        if target.poll() is None: target.terminate()
        target.wait(timeout=3)
        if replacement is not None:
            replacement.terminate(); replacement.wait(timeout=3)
