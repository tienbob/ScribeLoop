"""Rebuild and run the silent disposable-app metadata spike on macOS."""
import os
import plistlib
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
spike = root/'.spike'
contents = spike/'LivenessTarget.app/Contents'
(contents/'MacOS').mkdir(parents=True,exist_ok=True)
(contents/'Info.plist').write_bytes(plistlib.dumps({
    'CFBundleIdentifier':'dev.scribeloop.liveness-target',
    'CFBundleExecutable':'LivenessTarget','CFBundleName':'LivenessTarget',
    'CFBundlePackageType':'APPL'}))
env = dict(os.environ,DEVELOPER_DIR='/Applications/Xcode.app/Contents/Developer')
compiler = ['xcrun','swiftc','-module-cache-path',str(root/'build/macos/swift-cache')]
target = contents/'MacOS/LivenessTarget'
probe = spike/'probe_liveness'
subprocess.run([*compiler,str(spike/'target.swift'),'-o',str(target)],env=env,check=True)
subprocess.run([*compiler,'-parse-as-library',str(spike/'probe_liveness.swift'),'-o',str(probe)],env=env,check=True)
with (spike/'liveness-trace.txt').open('w') as trace:
    subprocess.run([str(probe),str(target)],stdout=trace,check=True)
