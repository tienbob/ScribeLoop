"""Rebuild pinned sqlite-vec for macOS 13 in the active packaging environment.

Run with build/macos/venv13/bin/python after installing requirements-desktop.txt.
The checkout must already exist; this script never downloads or installs tools.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path
from string import Template

import sqlite_vec
from package_internal import native_minimum, version_tuple

ROOT = Path(__file__).resolve().parents[2]
BUILD = ROOT/'build/macos'
COMMIT = 'e9f598abfa0c06b328d8fe5da9c3760cce74be10'


def main(headers):
    source = BUILD/'sqlite-vec-src'
    head = subprocess.check_output(['git','-C',str(source),'rev-parse','HEAD'],text=True).strip()
    if head != COMMIT or (source/'VERSION').read_text().strip() != '0.1.9':
        raise ValueError('Expected the pinned sqlite-vec v0.1.9 source checkout')
    subprocess.run(['git','-C',str(source),'diff','--exit-code','HEAD','--',
                    'sqlite-vec.c','sqlite-vec.h.tmpl','VERSION'],check=True)
    work = BUILD/'compat13'
    work.mkdir(parents=True,exist_ok=True)
    template = Template((source/'sqlite-vec.h.tmpl').read_text())
    (work/'sqlite-vec.h').write_text(template.substitute(VERSION='0.1.9',DATE='2026-10-29',
        SOURCE=COMMIT,VERSION_MAJOR=0,VERSION_MINOR=1,VERSION_PATCH=9))
    binary = work/'vec0.dylib'
    headers = Path(headers).resolve()
    if not all((headers/name).is_file() for name in ('sqlite3.h','sqlite3ext.h')):
        raise ValueError('Provide the directory containing upstream SQLite/SQLCipher headers')
    env = dict(os.environ,DEVELOPER_DIR='/Applications/Xcode.app/Contents/Developer')
    subprocess.run(['xcrun','clang','-target','arm64-apple-macos13.0','-dynamiclib','-fPIC','-O3',
        '-mcpu=apple-m1','-DSQLITE_VEC_ENABLE_NEON',f'-I{work}',f'-I{headers}',str(source/'sqlite-vec.c'),
        '-o',str(binary)],check=True,env=env)
    commands = subprocess.check_output(['otool','-l',str(binary)],text=True)
    if version_tuple(native_minimum(commands)) > (13,0,0):
        raise ValueError('Compiled extension requires a newer OS than macOS 13')
    # Only replace the extension inside this isolated packaging environment.
    destination = Path(sqlite_vec.__file__).parent/'vec0.dylib'
    if BUILD/'venv13' not in destination.parents:
        raise ValueError('Run with build/macos/venv13/bin/python, not the development interpreter')
    shutil.copy2(binary,destination)
    with sqlite3.connect(':memory:') as conn:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        version = conn.execute('SELECT vec_version()').fetchone()[0]
        assert version == 'v0.1.9'
        conn.execute('CREATE VIRTUAL TABLE smoke USING vec0(embedding float[2])')
        conn.execute("INSERT INTO smoke(rowid,embedding) VALUES (1,'[1,2]')")
        assert conn.execute("SELECT rowid FROM smoke WHERE embedding MATCH '[1,2]' AND k=1").fetchone()[0] == 1
    (work/'sqlite-vec-build.json').write_text(json.dumps({'commit':COMMIT,
        'version':version,'minimum_macos':'13.0',
        'sqlite_header_sha256':hashlib.sha256((headers/'sqlite3.h').read_bytes()).hexdigest(),
        'sha256':hashlib.sha256(destination.read_bytes()).hexdigest()},indent=2)+'\n')
    print('sqlite-vec v0.1.9 rebuilt for macOS 13; load and nearest-neighbor smoke passed.')


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sqlite-headers',type=Path,default=Path('/opt/homebrew/opt/sqlcipher/include/sqlcipher'))
    main(parser.parse_args().sqlite_headers)
