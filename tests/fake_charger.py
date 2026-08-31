#!/usr/bin/env python3
"""Scripted stand-in for a Wall Connector, so session detection can be tested
without hardware.

Timeline from start: idle, plugged in, charging with rising energy, charging
finished but still plugged, then unplugged.
"""
import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

T0 = time.time()
PLUG_IN, CHARGE_START, CHARGE_END, UNPLUG = 3, 6, 16, 19
PEAK_WH = 7000.0


def vitals():
    t = time.time() - T0
    connected = PLUG_IN <= t < UNPLUG
    charging = CHARGE_START <= t < CHARGE_END
    if t < CHARGE_START:
        wh = 0.0
    elif charging:
        wh = PEAK_WH * (t - CHARGE_START) / (CHARGE_END - CHARGE_START)
    else:
        wh = PEAK_WH
    amps = 16.0 if charging else 0.0
    return {
        "contactor_closed": charging,
        "vehicle_connected": connected,
        "session_s": int(t - PLUG_IN) if connected else 0,
        "session_energy_wh": wh if connected else 0.0,
        "grid_v": 229.0, "grid_hz": 49.95,
        "vehicle_current_a": amps,
        "currentA_a": amps, "currentB_a": amps, "currentC_a": amps,
        "voltageA_v": 230.0 if charging else 2.1,
        "voltageB_v": 230.0 if charging else 0.0,
        "voltageC_v": 230.0 if charging else 2.2,
        "handle_temp_c": 17 + (12 if charging else 0),
        "pcba_temp_c": 21.0, "mcu_temp_c": 29.0,
        "evse_state": 4 if charging else 1,
    }


BODIES = {
    "lifetime": lambda: {"energy_wh": 2734298, "charge_starts": 680,
                         "connector_cycles": 322, "charging_time_s": 1835002,
                         "contactor_cycles": 680, "thermal_foldbacks": 0},
    "version": lambda: {"firmware_version": "26.26.1", "part_number": "1529455-02-F"},
    "wifi_status": lambda: {"wifi_rssi": -72, "wifi_connected": True},
    "vitals": vitals,
}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_GET(self):
        key = self.path.rsplit("/", 1)[-1]
        if key in BODIES:
            body = json.dumps(BODIES[key]()).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(500)
            self.send_header("Content-Length", "0")
            self.end_headers()


if __name__ == "__main__":
    ThreadingHTTPServer(("127.0.0.1", 8250), Handler).serve_forever()
