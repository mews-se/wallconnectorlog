#!/usr/bin/env python3
"""WallConnectorLog — a local logger for the Tesla Wall Connector Gen 3.

The charger exposes instantaneous readings and monotonic lifetime counters, but
keeps no history of its own. This polls it, stores samples in SQLite and derives
charge sessions from the connect/contactor transitions.
"""

import base64
import http.client
import json
import os
import signal
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Tesla documents none of this. The mapping below is the intersection of the Home
# Assistant integration, the ioBroker adapter and a list derived from the Wall
# Monitor app — they agree on every entry except 7.
#
# State 7 is labelled "error" by Home Assistant and "finished charging" by the
# Wall Monitor-derived list. Both look wrong: in captured charge sequences
# (1 -> 7 -> 9 -> 11 -> 9 -> 1) state 7 appears immediately after plugging in,
# with zero session energy and zero current. It is a transient hand-shake state,
# so it is not reported as a fault here.
EVSE_STATE = {
    0: "Booting",
    1: "No vehicle connected",
    2: "Connected, not ready",
    4: "Connected, ready",
    6: "Negotiating",
    7: "Starting up",
    8: "Charging finished",
    9: "Waiting for vehicle",
    10: "Charging at reduced power",
    11: "Charging",
}

# evse_not_ready_reasons, config_status and current_alerts are deliberately NOT
# decoded. The only published mapping for the not-ready reasons (Tesla's own
# protobuf) does not reconcile with observed values, nobody has decoded
# config_status, and the Wall Monitor authors state plainly that they do not know
# what the alert counter means either. Store the raw values, show the raw values.

HOST = os.environ.get("WC_HOST", "10.0.1.249").strip()
# Accept a pasted URL as well as a bare address.
for _prefix in ("http://", "https://"):
    HOST = HOST.removeprefix(_prefix)
HOST = HOST.rstrip("/")
DB_PATH = os.environ.get("WC_DB", "/data/wallconnectorlog.db")
PORT = int(os.environ.get("WC_PORT", "4680"))
RETAIN_DAYS = int(os.environ.get("WC_RETAIN_DAYS", "90"))

# Poll fast while current is flowing; the charger is often idle for days.
INTERVAL_CHARGING = int(os.environ.get("WC_INTERVAL_CHARGING", "5"))
INTERVAL_CONNECTED = int(os.environ.get("WC_INTERVAL_CONNECTED", "15"))
INTERVAL_IDLE = int(os.environ.get("WC_INTERVAL_IDLE", "60"))

# Where the browser should go for Grafana, and where this process can check
# whether it is actually up. The link is only shown once the check succeeds, so
# nothing dangles when Grafana is not running.
GRAFANA_URL = os.environ.get("WC_GRAFANA_URL", "")
GRAFANA_HEALTH = os.environ.get("WC_GRAFANA_HEALTH", "http://grafana:3000/api/health")

# The logo, small enough to inline so the single file stays self-contained and
# works from any directory. Full-size artwork lives in assets/.
ICON = "data:image/png;base64,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"  # noqa: E501

BACKUP_DIR = os.environ.get("WC_BACKUP_DIR", "")
BACKUP_INTERVAL_H = int(os.environ.get("WC_BACKUP_INTERVAL_H", "24"))
BACKUP_KEEP = int(os.environ.get("WC_BACKUP_KEEP", "7"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS sample (
    ts INTEGER PRIMARY KEY,
    grid_v REAL, grid_hz REAL, current_a REAL, power_w REAL,
    vehicle_connected INTEGER, contactor_closed INTEGER,
    session_s INTEGER, session_wh REAL,
    handle_c REAL, pcba_c REAL, mcu_c REAL, evse_state INTEGER,
    volt_a REAL, volt_b REAL, volt_c REAL,
    amp_a REAL, amp_b REAL, amp_c REAL, amp_n REAL
);
CREATE INDEX IF NOT EXISTS sample_ts ON sample(ts);

CREATE TABLE IF NOT EXISTS session (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at INTEGER NOT NULL,
    ended_at INTEGER,
    energy_wh REAL DEFAULT 0,
    duration_s INTEGER DEFAULT 0,
    charge_s INTEGER DEFAULT 0,
    peak_power_w REAL DEFAULT 0,
    peak_handle_c REAL,
    grid_v_sum REAL DEFAULT 0,
    grid_v_n INTEGER DEFAULT 0,
    is_open INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS lifetime (
    ts INTEGER PRIMARY KEY,
    energy_wh REAL, charge_starts INTEGER, connector_cycles INTEGER,
    charging_time_s INTEGER, contactor_cycles INTEGER, thermal_foldbacks INTEGER,
    alert_count INTEGER, cycles_loaded INTEGER, uptime_s INTEGER, startup_temp REAL
);

CREATE TABLE IF NOT EXISTS wifi (
    ts INTEGER PRIMARY KEY, rssi INTEGER, snr INTEGER,
    connected INTEGER, internet INTEGER
);

CREATE TABLE IF NOT EXISTS device (k TEXT PRIMARY KEY, v TEXT, ts INTEGER);

CREATE TABLE IF NOT EXISTS poll_error (ts INTEGER PRIMARY KEY, detail TEXT);
"""

# Databases created before these columns existed are altered in place.
# "duplicate column name" is SQLite's way of saying the work is already done.
MIGRATIONS = (
    "ALTER TABLE sample ADD COLUMN volt_a REAL",
    "ALTER TABLE sample ADD COLUMN volt_b REAL",
    "ALTER TABLE sample ADD COLUMN volt_c REAL",
    "ALTER TABLE sample ADD COLUMN amp_a REAL",
    "ALTER TABLE sample ADD COLUMN amp_b REAL",
    "ALTER TABLE sample ADD COLUMN amp_c REAL",
    "ALTER TABLE sample ADD COLUMN amp_n REAL",
    "ALTER TABLE lifetime ADD COLUMN alert_count INTEGER",
    "ALTER TABLE lifetime ADD COLUMN cycles_loaded INTEGER",
    "ALTER TABLE lifetime ADD COLUMN uptime_s INTEGER",
    "ALTER TABLE lifetime ADD COLUMN startup_temp REAL",
)


def ensure_schema(db):
    db.executescript(SCHEMA)
    for stmt in MIGRATIONS:
        try:
            db.execute(stmt)
        except sqlite3.OperationalError:
            pass
    db.commit()


def connect():
    db = sqlite3.connect(DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def fetch(path, timeout=8):
    url = f"http://{HOST}/api/1/{path}"
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode())


def phase_power(v):
    """The charger reports per-phase volts and amps; idle phases read a volt or two."""
    total = 0.0
    for p in ("A", "B", "C"):
        total += float(v.get(f"voltage{p}_v", 0) or 0) * float(v.get(f"current{p}_a", 0) or 0)
    return total


def relay_volts(v):
    """Older firmware reports a single relay_coil_v instead of relay_k1_v/k2_v."""
    if v.get("relay_k1_v") is not None:
        return v.get("relay_k1_v")
    return v.get("relay_coil_v")


def decode_ssid(value):
    """wifi_ssid comes back base64-encoded."""
    if not isinstance(value, str):
        return value
    try:
        return base64.b64decode(value, validate=True).decode("utf-8")
    except Exception:
        return value


class Poller(threading.Thread):
    daemon = True

    def __init__(self):
        super().__init__()
        self.latest = {}
        self.grafana_up = False
        self.lock = threading.Lock()

    def run(self):
        db = connect()
        ensure_schema(db)
        last_meta = 0
        last_prune = 0
        last_grafana = 0
        while True:
            interval = INTERVAL_IDLE
            try:
                vitals = fetch("vitals")
                now = int(time.time())
                charging = bool(vitals.get("contactor_closed"))
                connected = bool(vitals.get("vehicle_connected"))
                power = phase_power(vitals) if charging else 0.0

                db.execute(
                    "INSERT OR REPLACE INTO sample (ts, grid_v, grid_hz, "
                    "current_a, power_w, vehicle_connected, contactor_closed, "
                    "session_s, session_wh, handle_c, pcba_c, mcu_c, evse_state, "
                    "volt_a, volt_b, volt_c, amp_a, amp_b, amp_c, amp_n) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (now, vitals.get("grid_v"), vitals.get("grid_hz"),
                     vitals.get("vehicle_current_a"), power,
                     int(connected), int(charging),
                     vitals.get("session_s"), vitals.get("session_energy_wh"),
                     vitals.get("handle_temp_c"), vitals.get("pcba_temp_c"),
                     vitals.get("mcu_temp_c"), vitals.get("evse_state"),
                     vitals.get("voltageA_v"), vitals.get("voltageB_v"),
                     vitals.get("voltageC_v"), vitals.get("currentA_a"),
                     vitals.get("currentB_a"), vitals.get("currentC_a"),
                     vitals.get("currentN_a")),
                )
                self.update_session(db, now, vitals, connected, charging, power)

                if now - last_meta > 300:
                    self.update_slow(db, now)
                    last_meta = now
                if now - last_prune > 3600:
                    cutoff = now - RETAIN_DAYS * 86400
                    db.execute("DELETE FROM sample WHERE ts < ?", (cutoff,))
                    db.execute("DELETE FROM wifi WHERE ts < ?", (cutoff,))
                    db.execute("DELETE FROM poll_error WHERE ts < ?", (cutoff,))
                    last_prune = now
                db.commit()

                with self.lock:
                    self.latest = {"ts": now, "vitals": vitals, "power_w": power, "ok": True}
                interval = (INTERVAL_CHARGING if charging
                            else INTERVAL_CONNECTED if connected else INTERVAL_IDLE)
            except (urllib.error.URLError, http.client.HTTPException,
                    OSError, ValueError, TimeoutError) as e:
                now = int(time.time())
                try:
                    db.execute("INSERT OR REPLACE INTO poll_error VALUES (?,?)", (now, str(e)))
                    db.commit()
                except sqlite3.Error:
                    pass
                with self.lock:
                    self.latest = {"ts": now, "ok": False, "error": str(e)}

            # Runs whether the poll succeeded or not. The heartbeat says "a
            # logger is alive and holds this database" — an unreachable charger
            # must not make a running logger look dead, or a restore could pull
            # the files out from under it. The Grafana probe retries quickly
            # while down so the link appears soon after Grafana starts.
            now = int(time.time())
            if GRAFANA_URL and now - last_grafana > (300 if self.grafana_up else 20):
                self.check_grafana()
                last_grafana = now
            beat()
            time.sleep(interval)

    def update_session(self, db, now, vitals, connected, charging, power):
        row = db.execute("SELECT * FROM session WHERE is_open=1 "
                         "ORDER BY id DESC LIMIT 1").fetchone()
        if connected:
            if row is None:
                db.execute("INSERT INTO session (started_at) VALUES (?)", (now,))
                row = db.execute("SELECT * FROM session WHERE is_open=1 "
                                 "ORDER BY id DESC LIMIT 1").fetchone()
            energy = float(vitals.get("session_energy_wh") or 0)
            db.execute(
                "UPDATE session SET energy_wh=?, duration_s=?, charge_s=charge_s+?, "
                "peak_power_w=MAX(peak_power_w,?), "
                "peak_handle_c=MAX(COALESCE(peak_handle_c,-99),?), "
                "grid_v_sum=grid_v_sum+?, grid_v_n=grid_v_n+1 WHERE id=?",
                (energy, now - row["started_at"], INTERVAL_CHARGING if charging else 0,
                 power, vitals.get("handle_temp_c") or -99,
                 vitals.get("grid_v") or 0, row["id"]),
            )
        elif row is not None:
            db.execute("UPDATE session SET is_open=0, ended_at=? WHERE id=?", (now, row["id"]))

    def check_grafana(self):
        try:
            with urllib.request.urlopen(GRAFANA_HEALTH, timeout=4) as r:
                self.grafana_up = r.status == 200
        except Exception:
            self.grafana_up = False

    def update_slow(self, db, now):
        try:
            lt = fetch("lifetime")
            db.execute("INSERT OR REPLACE INTO lifetime (ts, energy_wh, "
                       "charge_starts, connector_cycles, charging_time_s, "
                       "contactor_cycles, thermal_foldbacks, alert_count, "
                       "cycles_loaded, uptime_s, startup_temp) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                       (now, lt.get("energy_wh"), lt.get("charge_starts"),
                        lt.get("connector_cycles"), lt.get("charging_time_s"),
                        lt.get("contactor_cycles"), lt.get("thermal_foldbacks"),
                        lt.get("alert_count"), lt.get("contactor_cycles_loaded"),
                        lt.get("uptime_s"), lt.get("avg_startup_temp")))
        except Exception:
            pass
        for path in ("version", "wifi_status"):
            try:
                info = fetch(path)
                for k, v in info.items():
                    if k == "wifi_ssid":
                        v = decode_ssid(v)
                    db.execute("INSERT OR REPLACE INTO device VALUES (?,?,?)",
                               (k, json.dumps(v), now))
                if path == "wifi_status":
                    # Keep NULL where the firmware does not report a field, so
                    # absent is distinguishable from down.
                    flags = [None if info.get(k) is None else int(bool(info[k]))
                             for k in ("wifi_connected", "internet")]
                    db.execute("INSERT OR REPLACE INTO wifi "
                               "(ts, rssi, snr, connected, internet) "
                               "VALUES (?,?,?,?,?)",
                               (now, info.get("wifi_rssi"), info.get("wifi_snr"),
                                flags[0], flags[1]))
            except Exception:
                pass


poller = Poller()


def query_params(raw_path):
    """The query string as single values; the last of a repeated key wins."""
    parsed = urllib.parse.parse_qs(urllib.parse.urlsplit(raw_path).query)
    return {k: v[-1] for k, v in parsed.items()}


def int_param(value, default):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def query(sql, args=()):
    db = connect()
    try:
        return [dict(r) for r in db.execute(sql, args).fetchall()]
    finally:
        db.close()


HEARTBEAT = DB_PATH + ".heartbeat"
# Anything older than this means no logger is running. It has to clear the
# longest poll interval with room to spare, including a raised WC_INTERVAL_IDLE.
HEARTBEAT_STALE_S = max(180, INTERVAL_IDLE * 3)


def beat():
    """Touch a file each poll, so restore can tell whether a logger is live.

    A lock on the database itself is no good: in WAL mode the poller only holds
    one for the few milliseconds it takes to write, so between polls the
    database looks free even though the service is running.
    """
    try:
        with open(HEARTBEAT, "w") as fh:
            fh.write(str(os.getpid()))
    except OSError:
        pass


def clear_heartbeat(*_):
    """Removed on a clean stop, so a restore right afterwards is not held up."""
    try:
        os.remove(HEARTBEAT)
    except OSError:
        pass
    raise SystemExit(0)


def logger_is_running():
    try:
        return time.time() - os.stat(HEARTBEAT).st_mtime < HEARTBEAT_STALE_S
    except OSError:
        return False


def backup_dir():
    return BACKUP_DIR or os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")


def snapshot(dest):
    """Write a consistent copy of the database.

    Uses SQLite's online backup API, which is safe while the poller is writing.
    Copying the .db file on its own is NOT safe: in WAL mode the recent writes
    live in the -wal file and a plain copy silently loses them.
    """
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    src = sqlite3.connect(DB_PATH, timeout=30)
    try:
        dst = sqlite3.connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def rotate_backups():
    d = backup_dir()
    if not os.path.isdir(d):
        return
    kept = sorted(f for f in os.listdir(d)
                  if f.startswith("wallconnectorlog-") and f.endswith(".db"))
    for stale in kept[:-BACKUP_KEEP] if BACKUP_KEEP > 0 else []:
        try:
            os.remove(os.path.join(d, stale))
        except OSError:
            pass


def auto_backup():
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(backup_dir(), f"wallconnectorlog-{stamp}.db")
    snapshot(path)
    rotate_backups()
    return path


class Backups(threading.Thread):
    daemon = True

    def run(self):
        if BACKUP_INTERVAL_H <= 0:
            return
        while True:
            time.sleep(BACKUP_INTERVAL_H * 3600)
            try:
                print(f"backup written: {auto_backup()}", flush=True)
            except Exception as e:
                print(f"backup failed: {e}", flush=True)


def verify_db(path):
    """Check that a file really is one of our databases before trusting it."""
    if not os.path.isfile(path):
        return f"no such file: {path}"
    try:
        db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error as e:
        return f"cannot open: {e}"
    try:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return "integrity check failed"
        have = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        missing = {"sample", "session", "lifetime", "device"} - have
        if missing:
            return f"not a WallConnectorLog database (missing {', '.join(sorted(missing))})"
        counts = {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("sample", "session")}
        return None, counts
    except sqlite3.Error as e:
        return f"unreadable: {e}"
    finally:
        db.close()


def restore(path):
    """Replace the live database with a backup. Refuses while the service holds it."""
    checked = verify_db(path)
    if isinstance(checked, str):
        print(f"refusing to restore: {checked}")
        return 1
    _, counts = checked
    print(f"source looks good: {counts['sample']} samples, {counts['session']} sessions")

    if logger_is_running():
        print("refusing to restore: a logger is still writing to this database.")
        print("Stop it first:  docker compose stop wallconnectorlog")
        print(f"(If it crashed rather than stopped, delete {HEARTBEAT} or wait "
              f"{HEARTBEAT_STALE_S}s.)")
        return 1

    if os.path.exists(DB_PATH):
        safety = f"{DB_PATH}.replaced-{time.strftime('%Y%m%d-%H%M%S')}"
        snapshot(safety)
        print(f"current database saved as {safety}")

    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(DB_PATH + suffix):
            os.remove(DB_PATH + suffix)
    src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        dst = sqlite3.connect(DB_PATH)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    print(f"restored {path} -> {DB_PATH}")
    return 0


def metrics_text():
    """Prometheus exposition, so this can also feed an existing Prometheus setup."""
    with poller.lock:
        live = dict(poller.latest)
    out = []

    def add(name, help_text, kind, value, labels=""):
        out.append(f"# HELP {name} {help_text}")
        out.append(f"# TYPE {name} {kind}")
        out.append(f"{name}{labels} {value}")

    add("wcl_up", "1 when the last poll of the charger succeeded", "gauge",
        1 if live.get("ok") else 0)
    v = live.get("vitals") or {}
    if v:
        gauges = [
            ("wcl_grid_volts", "Grid voltage", v.get("grid_v")),
            ("wcl_grid_hertz", "Grid frequency", v.get("grid_hz")),
            ("wcl_vehicle_amps", "Vehicle current", v.get("vehicle_current_a")),
            ("wcl_power_watts", "Delivered power", live.get("power_w")),
            ("wcl_handle_celsius", "Handle temperature", v.get("handle_temp_c")),
            ("wcl_pcba_celsius", "Board temperature", v.get("pcba_temp_c")),
            ("wcl_mcu_celsius", "MCU temperature", v.get("mcu_temp_c")),
            ("wcl_session_energy_wh", "Energy of the current session", v.get("session_energy_wh")),
            ("wcl_session_seconds", "Length of the current session", v.get("session_s")),
            ("wcl_vehicle_connected", "1 when a vehicle is plugged in",
             int(bool(v.get("vehicle_connected")))),
            ("wcl_contactor_closed", "1 when the contactor is closed",
             int(bool(v.get("contactor_closed")))),
            ("wcl_evse_state", "Raw EVSE state code", v.get("evse_state")),
            ("wcl_uptime_seconds", "Charger uptime", v.get("uptime_s")),
            ("wcl_phase_a_volts", "Phase A voltage", v.get("voltageA_v")),
            ("wcl_phase_b_volts", "Phase B voltage", v.get("voltageB_v")),
            ("wcl_phase_c_volts", "Phase C voltage", v.get("voltageC_v")),
            ("wcl_phase_a_amps", "Phase A current", v.get("currentA_a")),
            ("wcl_phase_b_amps", "Phase B current", v.get("currentB_a")),
            ("wcl_phase_c_amps", "Phase C current", v.get("currentC_a")),
            ("wcl_neutral_amps", "Neutral current", v.get("currentN_a")),
        ]
        for name, help_text, value in gauges:
            if value is not None:
                add(name, help_text, "gauge", value)

    lt = query("SELECT * FROM lifetime ORDER BY ts DESC LIMIT 1")
    if lt:
        counters = [
            ("wcl_lifetime_energy_wh_total", "Lifetime energy delivered", lt[0]["energy_wh"]),
            ("wcl_lifetime_charge_starts_total", "Lifetime charge starts", lt[0]["charge_starts"]),
            ("wcl_lifetime_connector_cycles_total", "Lifetime connector cycles",
             lt[0]["connector_cycles"]),
            ("wcl_lifetime_charging_seconds_total", "Lifetime charging time",
             lt[0]["charging_time_s"]),
            ("wcl_lifetime_thermal_foldbacks_total", "Lifetime thermal foldbacks",
             lt[0]["thermal_foldbacks"]),
            ("wcl_lifetime_alerts_total", "Lifetime alert counter (undocumented, raw)",
             lt[0]["alert_count"]),
            ("wcl_lifetime_contactor_cycles_loaded_total",
             "Lifetime contactor cycles under load", lt[0]["cycles_loaded"]),
        ]
        for name, help_text, value in counters:
            if value is not None:
                add(name, help_text, "counter", value)

    wf = query("SELECT * FROM wifi ORDER BY ts DESC LIMIT 1")
    if wf:
        wifi_gauges = [
            ("wcl_wifi_rssi_dbm", "WiFi signal strength", wf[0]["rssi"]),
            ("wcl_wifi_snr_db", "WiFi signal-to-noise ratio", wf[0]["snr"]),
            ("wcl_internet_up", "1 when the charger reports internet access",
             wf[0]["internet"]),
        ]
        for name, help_text, value in wifi_gauges:
            if value is not None:
                add(name, help_text, "gauge", value)

    done = query("SELECT COUNT(*) n, COALESCE(SUM(energy_wh),0) wh FROM session WHERE is_open=0")
    add("wcl_sessions_total", "Charge sessions recorded by this logger", "counter", done[0]["n"])
    add("wcl_sessions_energy_wh_total", "Energy across recorded sessions", "counter",
        done[0]["wh"])
    return "\n".join(out) + "\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def respond(self, body, content_type, code=200):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, code=200):
        self.respond(json.dumps(obj, default=str), "application/json", code)

    def do_GET(self):
        path = self.path.split("?")[0]
        try:
            if path == "/":
                self.respond(PAGE, "text/html; charset=utf-8")
            elif path == "/metrics":
                self.respond(metrics_text(), "text/plain; version=0.0.4; charset=utf-8")
            elif path == "/api/live":
                with poller.lock:
                    live = dict(poller.latest)
                lt = query("SELECT * FROM lifetime ORDER BY ts DESC LIMIT 1")
                live["lifetime"] = lt[0] if lt else None
                live["device"] = {r["k"]: json.loads(r["v"]) for r in query("SELECT * FROM device")}
                live["open_session"] = (query("SELECT * FROM session WHERE is_open=1 "
                                              "ORDER BY id DESC LIMIT 1") or [None])[0]
                state = (live.get("vitals") or {}).get("evse_state")
                live["evse_state_text"] = EVSE_STATE.get(state)
                live["relay_v"] = relay_volts(live.get("vitals") or {})
                live["grafana"] = ({"url": GRAFANA_URL, "up": poller.grafana_up}
                                   if GRAFANA_URL else None)
                self.send_json(live)
            elif path == "/api/sessions":
                # Newest first. `before` takes a session id, so a client walks
                # the whole history by passing the last id it has seen.
                q = query_params(self.path)
                limit = max(1, min(1000, int_param(q.get("limit"), 200)))
                before = int_param(q.get("before"), None)
                sql = ("SELECT id, started_at, ended_at, energy_wh, duration_s, charge_s, "
                       "peak_power_w, peak_handle_c, is_open, "
                       "CASE WHEN grid_v_n>0 THEN grid_v_sum/grid_v_n END AS avg_grid_v "
                       "FROM session")
                args = []
                if before is not None:
                    sql += " WHERE id<?"
                    args.append(before)
                sql += " ORDER BY id DESC LIMIT ?"
                args.append(limit)
                self.send_json(query(sql, args))
            elif path.startswith("/api/sessions/") and path.endswith("/samples"):
                # Every stored sample of one session, phase columns included.
                # Samples are pruned after WC_RETAIN_DAYS, so an old session
                # answers with an empty list rather than an error.
                sid = path[len("/api/sessions/"):-len("/samples")]
                row = (query("SELECT started_at, ended_at FROM session WHERE id=?",
                             (int(sid),)) if sid.isdigit() else None)
                if not row:
                    self.send_json({"error": "not found"}, 404)
                else:
                    end = row[0]["ended_at"] or int(time.time())
                    self.send_json(query(
                        "SELECT * FROM sample WHERE ts BETWEEN ? AND ? ORDER BY ts",
                        (row[0]["started_at"], end)))
            elif path == "/api/history":
                hours = int_param(query_params(self.path).get("hours"), 24)
                hours = max(1, min(720, hours))
                since = int(time.time()) - hours * 3600
                self.send_json(query(
                    "SELECT ts, grid_v, grid_hz, power_w, current_a, handle_c, pcba_c, mcu_c, "
                    "contactor_closed FROM sample WHERE ts>=? ORDER BY ts", (since,)))
            elif path == "/api/errors":
                self.send_json(query("SELECT * FROM poll_error ORDER BY ts DESC LIMIT 50"))
            elif path == "/api/backup":
                tmp = os.path.join(backup_dir(), f".download-{os.getpid()}.db")
                try:
                    snapshot(tmp)
                    with open(tmp, "rb") as fh:
                        body = fh.read()
                finally:
                    if os.path.exists(tmp):
                        os.remove(tmp)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="wallconnectorlog-{stamp}.db"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif path == "/api/backups":
                d = backup_dir()
                files = []
                if os.path.isdir(d):
                    for name in sorted(os.listdir(d), reverse=True):
                        if name.startswith("wallconnectorlog-") and name.endswith(".db"):
                            st = os.stat(os.path.join(d, name))
                            files.append({"name": name, "bytes": st.st_size,
                                          "modified": int(st.st_mtime)})
                self.send_json({"directory": d, "keep": BACKUP_KEEP,
                                "interval_hours": BACKUP_INTERVAL_H, "backups": files})
            elif path == "/healthz":
                # Health means "the poll loop is alive", not "the charger
                # answers" — latest.ts advances on failed polls too, so a stale
                # timestamp is a dead poller. An unreachable charger is an
                # expected condition (weak WiFi) and is reported, not fatal.
                with poller.lock:
                    live = dict(poller.latest)
                fresh = bool(live.get("ts")) and time.time() - live["ts"] < HEARTBEAT_STALE_S
                self.send_json({"ok": fresh, "charger_ok": bool(live.get("ok"))},
                               200 if fresh else 503)
            else:
                self.send_json({"error": "not found"}, 404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self.send_json({"error": str(e)}, 500)
            except Exception:
                pass


PAGE_TEMPLATE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>WallConnectorLog</title>
<link rel="icon" href="__ICON__">
<link rel="apple-touch-icon" href="__ICON__">
<style>
:root{--bg:#0f1216;--card:#171b21;--line:#252b34;--fg:#e6eaf0;--dim:#8b96a5;
--accent:#4da3ff;--good:#3fb950;--warn:#d29922}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:24px 18px 60px}
h1{font-size:19px;margin:0 0 2px;font-weight:600}
.sub{color:var(--dim);font-size:13px;margin-bottom:22px}
.grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));margin-bottom:26px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}
.k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.05em}
.v{font-size:23px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.v small{font-size:13px;color:var(--dim);font-weight:400;margin-left:3px}
.state{display:inline-block;padding:3px 10px;border-radius:999px;font-size:12px;font-weight:600}
.s-charge{background:rgba(63,185,80,.15);color:var(--good)}
.s-conn{background:rgba(210,153,34,.15);color:var(--warn)}
.s-idle{background:rgba(139,150,165,.15);color:var(--dim)}
h2{font-size:14px;margin:26px 0 10px;color:var(--dim);font-weight:600;
text-transform:uppercase;letter-spacing:.05em}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th{text-align:left;color:var(--dim);font-weight:500;font-size:11px;
text-transform:uppercase;letter-spacing:.04em;padding:8px 10px;border-bottom:1px solid var(--line)}
td{padding:9px 10px;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
tr:last-child td{border-bottom:0}
.tbl{background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:auto}
canvas{width:100%;height:190px;background:var(--card);
border:1px solid var(--line);border-radius:10px}
.err{color:#f85149}
.head{display:flex;align-items:center;justify-content:space-between;gap:16px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:11px}
.brand img{width:34px;height:34px;border-radius:8px;display:block}
.glink{color:var(--accent);text-decoration:none;font-size:13px;font-weight:600;
border:1px solid var(--line);border-radius:8px;padding:6px 12px;white-space:nowrap}
.glink:hover{border-color:var(--accent)}
@media(prefers-color-scheme:light){:root{--bg:#f6f7f9;--card:#fff;--line:#e3e6ea;
--fg:#1a1f26;--dim:#68727f}}
</style></head><body><div class="wrap">
<div class="head">
<div class="brand"><img src="__ICON__" alt=""><h1>WallConnectorLog</h1></div>
<span id="grafana"></span></div>
<div class="sub" id="sub">connecting…</div>
<div class="grid" id="live"></div>
<h2>Power, last 24 hours</h2><canvas id="chart"></canvas>
<h2>Lifetime</h2><div class="grid" id="lt"></div>
<h2>Charge sessions</h2><div class="tbl"><table id="sess">
<thead><tr><th>Started</th><th>Duration</th><th>Energy</th><th>Peak power</th>
<th>Peak handle</th><th>Avg grid</th></tr></thead><tbody></tbody></table></div>
</div><script>
const f=(n,d=1)=>n==null?"–":Number(n).toFixed(d);
const dur=s=>{if(s==null)return"–";s=Math.round(s);const h=Math.floor(s/3600),
m=Math.floor(s%3600/60);return h?`${h} h ${m} min`:`${m} min`};
const dt=t=>t?new Date(t*1000).toLocaleString(undefined,{dateStyle:"short",timeStyle:"short"}):"–";
function tile(k,v,u,small){return `<div class="card"><div class="k">${k}</div>
<div class="v"${small?' style="font-size:17px"':""}>${v}${u?`<small>${u}</small>`:""}</div></div>`}
// A bare port (":3399") is resolved against whatever host the page was opened
// from, so the link works from any machine, not just the server itself.
function grafanaHref(u){
 if(/^https?:\/\//i.test(u))return u;
 const m=String(u).match(/^:?(\d+)$/);
 return m?`${location.protocol}//${location.hostname}:${m[1]}`:u;
}

async function load(){
 let d;
 try{d=await (await fetch("/api/live")).json()}catch(e){
  document.getElementById("sub").innerHTML=
   '<span class="err">cannot reach the service</span>';return}
 const sub=document.getElementById("sub");
 // textContent, not innerHTML: the error string can contain angle brackets
 // ("<urlopen error timed out>") that innerHTML would swallow as a tag.
 if(!d.ok){sub.textContent="charger not responding: "+(d.error||"");sub.className="sub err"}
 else{sub.className="sub";const dv=d.device||{};
  sub.textContent=`${dv.part_number||""} · firmware ${dv.firmware_version||"?"} · `+
   `wifi ${dv.wifi_rssi??"?"} dBm · updated ${new Date(d.ts*1000).toLocaleTimeString()}`}
 const g=d.grafana;
 document.getElementById("grafana").innerHTML=(g&&g.up)?
  `<a class="glink" href="${grafanaHref(g.url)}" target="_blank" rel="noopener">`+
  `Graphs in Grafana →</a>`:"";
 const v=d.vitals||{};
 const st=v.contactor_closed?['s-charge','Charging']:
   v.vehicle_connected?['s-conn','Connected, not charging']:['s-idle','Idle'];
 document.getElementById("live").innerHTML=
  `<div class="card"><div class="k">Status</div>
   <div class="v" style="font-size:16px;margin-top:8px">
   <span class="state ${st[0]}">${st[1]}</span></div>
   <div class="k" style="margin-top:6px;text-transform:none;letter-spacing:0">
   ${d.evse_state_text||("state "+(v.evse_state??"?"))}</div></div>`+
  tile("Power",f(d.power_w/1000,2),"kW")+
  tile("Current",f(v.vehicle_current_a),"A")+
  tile("Grid voltage",f(v.grid_v),"V")+
  tile("Frequency",f(v.grid_hz,3),"Hz")+
  tile("Phase voltage",[v.voltageA_v,v.voltageB_v,v.voltageC_v].map(x=>f(x,0)).join(" / "),"V",1)+
  tile("Phase current",[v.currentA_a,v.currentB_a,v.currentC_a].map(x=>f(x)).join(" / "),"A",1)+
  tile("Handle",f(v.handle_temp_c),"°C")+
  tile("Board",f(v.pcba_temp_c),"°C")+
  (d.open_session?tile("In progress",f(d.open_session.energy_wh/1000,2),"kWh"):"");
 const lt=d.lifetime;
 document.getElementById("lt").innerHTML=lt?
  tile("Delivered",f(lt.energy_wh/1000,0),"kWh")+
  tile("Charge starts",lt.charge_starts??"–")+
  tile("Connector cycles",lt.connector_cycles??"–")+
  tile("Cycles under load",lt.cycles_loaded??"–")+
  tile("Charging time",f(lt.charging_time_s/3600,0),"h")+
  tile("Uptime",lt.uptime_s!=null?f(lt.uptime_s/86400,0):"–",lt.uptime_s!=null?"days":"")+
  tile("Alert counter",lt.alert_count??"–")+
  tile("Thermal foldbacks",lt.thermal_foldbacks??"–"):
  '<div class="card"><div class="k">Lifetime</div><div class="v">–</div></div>';

 const ss=await (await fetch("/api/sessions")).json();
 document.querySelector("#sess tbody").innerHTML=ss.length?ss.map(s=>
  `<tr><td>${dt(s.started_at)}${s.is_open?' <span class="state s-charge">open</span>':""}</td>
   <td>${dur(s.duration_s)}</td><td>${f(s.energy_wh/1000,2)} kWh</td>
   <td>${f(s.peak_power_w/1000,2)} kW</td>
   <td>${s.peak_handle_c>-99?f(s.peak_handle_c)+" °C":"–"}</td>
   <td>${f(s.avg_grid_v)} V</td></tr>`).join(""):
  '<tr><td colspan="6" style="color:var(--dim)">No sessions yet — one is recorded '+
  'when a vehicle is plugged in.</td></tr>';
 draw(await (await fetch("/api/history?hours=24")).json());
}
function draw(h){
 const c=document.getElementById("chart"),x=c.getContext("2d"),d=devicePixelRatio;
 const w=c.width=c.clientWidth*d,ht=c.height=190*d;
 x.clearRect(0,0,w,ht);
 const cs=getComputedStyle(document.documentElement);
 if(!h.length){x.fillStyle=cs.getPropertyValue("--dim");
  x.font=`${13*d}px sans-serif`;
  x.fillText("no data yet",12*d,24*d);return}
 const t0=h[0].ts,t1=h[h.length-1].ts||t0+1;
 const max=Math.max(1000,...h.map(p=>p.power_w||0));
 const py=v=>ht-12*d-v/max*(ht-30*d);
 // Gridlines on a 1/2/5 step, so the curve can be read without opening Grafana.
 const base=Math.pow(10,Math.floor(Math.log10(max/4)));
 const step=[1,2,5,10].map(m=>m*base).find(s=>max/s<=6);
 x.strokeStyle=cs.getPropertyValue("--line");x.lineWidth=d;
 x.fillStyle=cs.getPropertyValue("--dim");x.font=`${11*d}px sans-serif`;
 for(let v=step;v<max;v+=step){
  x.beginPath();x.moveTo(10*d,py(v));x.lineTo(w-10*d,py(v));x.stroke();
  x.fillText((v/1000).toFixed(step<1000?1:0)+" kW",10*d,py(v)-3*d);
 }
 x.strokeStyle=cs.getPropertyValue("--accent");x.lineWidth=2*d;
 x.beginPath();
 h.forEach((p,i)=>{const px=(p.ts-t0)/(t1-t0||1)*(w-20*d)+10*d;
  i?x.lineTo(px,py(p.power_w||0)):x.moveTo(px,py(p.power_w||0))});
 x.stroke();
 x.fillStyle=cs.getPropertyValue("--dim");
 x.fillText((max/1000).toFixed(1)+" kW",10*d,14*d);
}
load();setInterval(load,5000);
</script></body></html>"""

PAGE = PAGE_TEMPLATE.replace("__ICON__", ICON)


USAGE = """WallConnectorLog

  wallconnectorlog.py                run the logger and web interface
  wallconnectorlog.py backup [FILE]  write a consistent snapshot (safe while running)
  wallconnectorlog.py restore FILE   replace the database from a snapshot
  wallconnectorlog.py check          check the charger address and the database

Configuration is by environment variable; WC_HOST is the only required one.
See README.md for the full list.
"""


def check():
    print(f"charger  {HOST}")
    try:
        ver = fetch("version", timeout=6)
        print(f"         reachable — {ver.get('part_number')}, "
              f"firmware {ver.get('firmware_version')}")
    except Exception as e:
        print(f"         NOT reachable: {e}")
        print("         Set WC_HOST to your Wall Connector's address, then try again.")
        return 1
    print(f"database {DB_PATH}")
    if os.path.exists(DB_PATH):
        checked = verify_db(DB_PATH)
        if isinstance(checked, str):
            print(f"         problem: {checked}")
            return 1
        _, counts = checked
        print(f"         ok — {counts['sample']} samples, {counts['session']} sessions")
    else:
        print("         not created yet (it appears on first run)")
    print(f"backups  {backup_dir()} — every {BACKUP_INTERVAL_H} h, keeping {BACKUP_KEEP}")
    return 0


def main(argv):
    cmd = argv[1] if len(argv) > 1 else "run"

    if cmd in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    if cmd == "backup":
        if not os.path.exists(DB_PATH):
            print(f"nothing to back up: {DB_PATH} does not exist")
            return 1
        dest = argv[2] if len(argv) > 2 else os.path.join(
            backup_dir(), f"wallconnectorlog-{time.strftime('%Y%m%d-%H%M%S')}.db")
        print(snapshot(dest))
        return 0
    if cmd == "restore":
        if len(argv) < 3:
            print("usage: wallconnectorlog.py restore FILE")
            return 2
        return restore(argv[2])
    if cmd == "check":
        return check()
    if cmd != "run":
        print(USAGE)
        return 2

    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    db = connect()
    ensure_schema(db)
    db.close()
    signal.signal(signal.SIGTERM, clear_heartbeat)
    signal.signal(signal.SIGINT, clear_heartbeat)
    poller.start()
    Backups().start()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"WallConnectorLog: charger {HOST}, listening on :{PORT}, db {DB_PATH}", flush=True)
    srv.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
