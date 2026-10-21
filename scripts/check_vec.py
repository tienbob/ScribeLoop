"""Phase 0a checks for sqlite-vec (ARCHITECTURE.md §6 / §3.1, README task list).

1. SQLCipher + sqlite-vec coexistence — load vec0 into an encrypted SQLCipher
   database via the sqlcipher CLI and exercise a vec0 virtual table.
2. vec0 delete-scrub — insert a known byte pattern, delete, checkpoint, then
   scan the raw DB file for the pattern (secure_delete=ON + WAL TRUNCATE).

Usage: .venv/bin/python scripts/check_vec.py
"""
from __future__ import annotations

import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))
from scribeloop_core.storage.vectors import load_vec_extension

VECDYLIB = Path(__import__("sqlite_vec").loadable_path())


def check_coexistence(VEC: Path) -> bool:
    """SQLCipher CLI + sqlite-vec loadable extension in one encrypted DB."""
    if shutil.which("sqlcipher") is None:
        print("skip: sqlcipher CLI not on PATH")
        return True
    tmp = Path(tempfile.mkdtemp())
    db, script = tmp / "t.db", tmp / "t.sql"
    script.write_text(
        f".load {VEC}\n"
        "PRAGMA key = 'x';\n"
        "CREATE VIRTUAL TABLE v USING vec0(id integer primary key, vec float[4]);\n"
        "INSERT INTO v(id, vec) VALUES (1, x'0000803f000000400000404000004040');\n"
        "SELECT id, distance FROM v WHERE vec MATCH x'0000803f000000400000404000004040'"
        " ORDER BY distance LIMIT 1;\n",
    )
    r = subprocess.run(
        ["sqlcipher", str(db)], input=script.read_text(), capture_output=True, text=True, check=False
    )
    ok = r.returncode == 0 and "1|" in r.stdout
    print(f"coexistence: {'OK' if ok else 'FAIL'}  (rc={r.returncode})")
    if not ok:
        print(" stdout:", r.stdout.strip()[:300])
        print(" stderr:", r.stderr.strip()[:300])
    return ok


def check_delete_scrub() -> bool:
    """Insert a recognizable vector, delete the row, scan raw file bytes."""
    tmp = Path(tempfile.mkdtemp())
    db = tmp / "scrub.db"
    conn = sqlite3.connect(db)
    conn.execute("PRAGMA secure_delete=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    load_vec_extension(conn)

    # Distinctive float pattern that shouldn't appear anywhere else in the file.
    pattern = struct.pack("4f", 3.14159, 2.71828, 1.41421, 1.61803)
    conn.execute(
        "CREATE VIRTUAL TABLE v USING vec0(id integer primary key, vec float[4])"
    )
    conn.execute("INSERT INTO v(id, vec) VALUES (1, ?)", (pattern,))
    conn.commit()

    # §3.1 purge semantics: DELETE + wal_checkpoint(TRUNCATE).
    conn.execute("DELETE FROM v WHERE id = 1")
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()

    blob = db.read_bytes() + (db.with_suffix(db.suffix + "-wal").read_bytes()
                              if db.with_suffix(db.suffix + "-wal").exists() else b"")
    found = pattern in blob
    print(f"delete-scrub: {'FAIL' if found else 'OK'}"
          f"  (pattern {'STILL PRESENT' if found else 'absent'} from raw file)")
    return not found


if __name__ == "__main__":
    a = check_coexistence(VECDYLIB)
    b = check_delete_scrub()
    sys.exit(0 if a and b else 1)
