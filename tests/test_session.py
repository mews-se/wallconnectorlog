#!/usr/bin/env python3
"""End-to-end check: replay a charge through the stand-in charger and assert
that exactly one session is derived, with the expected energy and peak power."""
import json
import os
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.join(os.path.dirname(HERE), "wallconnectorlog.py")
DB = os.path.join(HERE, "test.db")

for suffix in ("", "-wal", "-shm"):
    if os.path.exists(DB + suffix):
        os.remove(DB + suffix)

env = dict(os.environ, WC_HOST="127.0.0.1:8250", WC_DB=DB, WC_PORT="8298",
           WC_INTERVAL_IDLE="1", WC_INTERVAL_CONNECTED="1", WC_INTERVAL_CHARGING="1")

fake = subprocess.Popen([sys.executable, os.path.join(HERE, "fake_charger.py")],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
mon = subprocess.Popen([sys.executable, APP], env=env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def get(path):
    return json.load(urllib.request.urlopen(f"http://127.0.0.1:8298{path}", timeout=5))


try:
    time.sleep(1.5)
    t0 = time.time()
    states = []
    while time.time() - t0 < 22:
        try:
            d = get("/api/live")
            v = d.get("vitals", {})
            state = ("charging" if v.get("contactor_closed")
                     else "connected" if v.get("vehicle_connected") else "idle")
            states.append(state)
            print(f"  t={time.time() - t0:4.1f}s  {state:9} "
                  f"{d.get('power_w', 0) / 1000:5.1f} kW  "
                  f"session={(v.get('session_energy_wh') or 0) / 1000:5.2f} kWh")
        except Exception as e:
            print(f"  t={time.time() - t0:4.1f}s  (no data: {e})")
        time.sleep(1.5)

    print("\nstates seen:", " -> ".join(dict.fromkeys(states)))

    sessions = get("/api/sessions")
    print(f"\nsessions derived: {len(sessions)}")
    print(json.dumps(sessions, indent=2))

    sid = sessions[0]["id"] if sessions else 0
    page_after = get(f"/api/sessions?before={sid + 1}&limit=1")
    page_before = get(f"/api/sessions?before={sid}")
    page_bad = get("/api/sessions?limit=abc&before=abc")
    samples = get(f"/api/sessions/{sid}/samples") if sessions else []
    wifi_api = get("/api/wifi?hours=1")
    lifetime_api = get("/api/lifetime?days=1")
    print(f"samples in session: {len(samples)}")
    try:
        get("/api/sessions/999/samples")
        missing_is_404 = False
    except urllib.error.HTTPError as e:
        missing_is_404 = e.code == 404

    metrics = urllib.request.urlopen("http://127.0.0.1:8298/metrics", timeout=5).read().decode()
    has_metrics = "wcl_sessions_total" in metrics and "wcl_lifetime_energy_wh_total" in metrics

    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    phase = db.execute("SELECT volt_a, amp_a, amp_n FROM sample "
                       "WHERE contactor_closed=1 ORDER BY ts DESC LIMIT 1").fetchone()
    wifi = db.execute("SELECT rssi, snr, internet FROM wifi "
                      "ORDER BY ts DESC LIMIT 1").fetchone()
    lt = db.execute("SELECT alert_count, cycles_loaded, uptime_s FROM lifetime "
                    "ORDER BY ts DESC LIMIT 1").fetchone()
    db.close()

    checks = {
        "saw all three states": set(states) >= {"idle", "connected", "charging"},
        "exactly one session": len(sessions) == 1,
        "session closed": bool(sessions) and sessions[0]["is_open"] == 0,
        "energy ~7 kWh": bool(sessions) and abs(sessions[0]["energy_wh"] - 7000) < 400,
        "duration >= 14 s": bool(sessions) and sessions[0]["duration_s"] >= 14,
        "peak power > 10 kW": bool(sessions) and sessions[0]["peak_power_w"] > 10000,
        "charging time counted": bool(sessions) and sessions[0]["charge_s"] >= 5,
        "prometheus metrics served": has_metrics,
        "phase columns stored": phase is not None and phase[0] == 230.0
                                and phase[1] == 16.0 and phase[2] == 0.1,
        "wifi history stored": wifi == (-72, 23, 1),
        "lifetime extras stored": lt == (56000, 34, 46000000),
        "session samples served": len(samples) >= 10
                                  and all(sessions[0]["started_at"] <= s["ts"]
                                          <= sessions[0]["ended_at"] for s in samples)
                                  and any(s["amp_a"] == 16.0 for s in samples),
        "unknown session is 404": missing_is_404,
        "sessions page by id": page_after == sessions and page_before == []
                               and page_bad == sessions,
        "wifi history served": bool(wifi_api) and wifi_api[-1]["rssi"] == -72
                               and wifi_api[-1]["snr"] == 23 and wifi_api[-1]["internet"] == 1,
        "lifetime history served": len(lifetime_api) == 1
                                   and lifetime_api[0]["energy_wh"] == 2734298
                                   and lifetime_api[0]["uptime_s"] == 46000000,
    }
    print()
    for name, passed in checks.items():
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")

    ok = all(checks.values())
    print("\nRESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)
finally:
    fake.terminate()
    mon.terminate()
    for suffix in ("", "-wal", "-shm"):
        if os.path.exists(DB + suffix):
            os.remove(DB + suffix)
