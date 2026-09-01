#!/usr/bin/env python3
"""Checks that a backup taken from a running instance is complete and restorable,
including that a plain file copy is not (the WAL trap this guards against)."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "wallconnectorlog.py")
DB = os.path.join(HERE, "backup-test.db")
BACKUPS = os.path.join(HERE, "backup-test-backups")


def cleanup():
    for suffix in ("", "-wal", "-shm"):
        for f in (DB, DB + suffix):
            if os.path.exists(f):
                os.remove(f)
    for f in os.listdir(HERE):
        if f.startswith("backup-test.db.replaced-"):
            os.remove(os.path.join(HERE, f))
    if os.path.exists(DB + ".heartbeat"):
        os.remove(DB + ".heartbeat")
    shutil.rmtree(BACKUPS, ignore_errors=True)


cleanup()
env = dict(os.environ, WC_HOST="127.0.0.1:8250", WC_DB=DB, WC_PORT="8297",
           WC_BACKUP_DIR=BACKUPS, WC_BACKUP_INTERVAL_H="0",
           WC_INTERVAL_IDLE="1", WC_INTERVAL_CONNECTED="1", WC_INTERVAL_CHARGING="1")


def run_cli(*args):
    return subprocess.run([sys.executable, APP, *args],
                          env=env, capture_output=True, text=True)


def rows(path, table):
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        db.close()


checks = {}
fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_charger.py")],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
mon = subprocess.Popen([sys.executable, APP], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(12)  # let it collect samples and open a session

    live_samples = rows(DB, "sample")
    print(f"collected while running: {live_samples} samples")
    checks["collected some samples"] = live_samples >= 5

    # A backup over HTTP, taken while the service is writing.
    dl = os.path.join(HERE, "http-backup.db")
    with urllib.request.urlopen("http://127.0.0.1:8297/api/backup", timeout=20) as r:
        with open(dl, "wb") as fh:
            fh.write(r.read())
    http_samples = rows(dl, "sample")
    print(f"HTTP backup holds:       {http_samples} samples")
    checks["http backup is complete"] = http_samples >= live_samples

    listing = json.load(urllib.request.urlopen("http://127.0.0.1:8297/api/backups", timeout=10))
    checks["backups endpoint answers"] = "directory" in listing

    # The trap this all exists for: copying just the .db loses the WAL contents.
    naive = os.path.join(HERE, "naive-copy.db")
    shutil.copy(DB, naive)
    naive_samples = rows(naive, "sample")
    print(f"naive .db-only copy:     {naive_samples} samples  (this is why we use the backup API)")
    checks["naive copy loses data"] = naive_samples < http_samples

    # Restore must refuse while the service holds the database.
    refused = run_cli("restore", dl)
    checks["restore refuses while running"] = (refused.returncode == 1
                                               and "still writing" in refused.stdout)

    # An unreachable charger must not make the running logger look dead: kill
    # the stand-in, let a few failing polls pass, and restore must still refuse.
    fake.terminate()
    time.sleep(4)
    refused = run_cli("restore", dl)
    checks["refuses while running, charger down"] = (refused.returncode == 1
                                                     and "still writing" in refused.stdout)
finally:
    mon.terminate()
    fake.terminate()
    time.sleep(1.5)

# Now that it is stopped, a CLI backup and a real restore.
cli = run_cli("backup", os.path.join(BACKUPS, "manual.db"))
checks["cli backup works"] = cli.returncode == 0 and os.path.exists(
    os.path.join(BACKUPS, "manual.db"))

bad = os.path.join(HERE, "not-a-database.db")
with open(bad, "wb") as fh:
    fh.write(b"definitely not sqlite")
rejected = run_cli("restore", bad)
checks["restore rejects junk"] = rejected.returncode == 1

for suffix in ("", "-wal", "-shm"):
    if os.path.exists(DB + suffix):
        os.remove(DB + suffix)
restored = run_cli("restore", dl)
print(restored.stdout.strip())
checks["restore succeeds"] = restored.returncode == 0
checks["restored data matches"] = (os.path.exists(DB)
                                   and rows(DB, "sample") == rows(dl, "sample"))

# A database created before the phase columns must be altered in place,
# keeping its rows. This is what upgrading a live deployment does.
old = os.path.join(HERE, "old-schema.db")
db = sqlite3.connect(old)
db.execute("CREATE TABLE sample (ts INTEGER PRIMARY KEY, grid_v REAL, "
           "grid_hz REAL, current_a REAL, power_w REAL, "
           "vehicle_connected INTEGER, contactor_closed INTEGER, "
           "session_s INTEGER, session_wh REAL, handle_c REAL, pcba_c REAL, "
           "mcu_c REAL, evse_state INTEGER)")
db.execute("INSERT INTO sample (ts, power_w) VALUES (1, 42)")
db.commit()
db.close()
mig = subprocess.Popen([sys.executable, APP], env=dict(env, WC_DB=old, WC_PORT="8296"),
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(3)
mig.terminate()
time.sleep(1)
db = sqlite3.connect(f"file:{old}?mode=ro", uri=True)
cols = {r[1] for r in db.execute("PRAGMA table_info(sample)")}
kept = db.execute("SELECT power_w FROM sample WHERE ts=1").fetchone()
db.close()
checks["old database migrated"] = {"volt_a", "amp_n"} <= cols
checks["migration keeps rows"] = kept is not None and kept[0] == 42
for suffix in ("", "-wal", "-shm", ".heartbeat"):
    if os.path.exists(old + suffix):
        os.remove(old + suffix)

print()
for name, passed in checks.items():
    print(f"  {'PASS' if passed else 'FAIL'}  {name}")
ok = all(checks.values())
print("\nRESULT:", "PASS" if ok else "FAIL")

for f in (dl, naive, bad):
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(f + suffix):
            os.remove(f + suffix)
cleanup()
sys.exit(0 if ok else 1)
