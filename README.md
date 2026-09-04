# <img src="assets/icon-180.png" alt="" width="40"> WallConnectorLog

[![CI](https://github.com/mews-se/wallconnectorlog/actions/workflows/ci.yml/badge.svg)](https://github.com/mews-se/wallconnectorlog/actions/workflows/ci.yml)
[![Image](https://github.com/mews-se/wallconnectorlog/actions/workflows/image.yml/badge.svg)](https://github.com/mews-se/wallconnectorlog/actions/workflows/image.yml)
![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)
![Python](https://img.shields.io/badge/Python_3.13-3776AB?logo=python&logoColor=white)
![SQLite](https://img.shields.io/badge/SQLite-003B57?logo=sqlite&logoColor=white)
![Grafana](https://img.shields.io/badge/Grafana-F46800?logo=grafana&logoColor=white)
[![license](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A self-hosted logger for the **Tesla Wall Connector Gen 3**. It polls the charger's local API,
stores the readings, and — the part nothing else does — derives **charge sessions** with real
start and end times.

The charger keeps no history: it reports what is happening right now, plus lifetime counters
that only go up. Phone apps read those counters when you happen to open them, so the numbers
land on the day you opened the app rather than the day you charged. Something that runs
continuously does not have that problem. Everything stays on your network — no account, no
cloud, no vendor API.

## What you get

- Live status, a power graph and the session table on a built-in web page
- A session log: start, end, duration, energy, peak power, peak handle temperature, average grid voltage
- Grafana with a ready-made dashboard — power, per-phase voltage and current, charger state,
  temperatures, WiFi signal and the lifetime counters — no login, up from the first start
- A JSON API and a Prometheus `/metrics` endpoint
- An iPhone app that reads all of it: [WallConnectorLog for iOS](https://apps.apple.com/app/wallconnectorlog/id6807546205), see below

Images are published on GHCR — **`ghcr.io/mews-se/wallconnectorlog`** and its companion
**`…/wallconnectorlog-grafana`** — and mirrored to Docker Hub under **`mewsse/`**. Same builds,
same tags, amd64 and arm64. `latest` follows the main branch; updating is three commands, see
below. To build from source instead, clone this repository and add `build: .` and
`build: grafana` to the two services.

## Quick start

Two files are all you need — no clone, no directories to prepare:

```bash
mkdir wallconnectorlog && cd wallconnectorlog
curl -O https://raw.githubusercontent.com/mews-se/wallconnectorlog/main/docker-compose.yml
curl -o .env https://raw.githubusercontent.com/mews-se/wallconnectorlog/main/.env.example
```

Set `WC_HOST` in `.env` to the charger's address (if `curl http://CHARGER-IP/api/1/version`
returns JSON, you have the right one), then:

```bash
docker compose up -d
```

Open `http://localhost:4680` for the app — the port is the Tesla cell format — and
`http://localhost:3399` for Grafana; a **Graphs in Grafana** link also appears on the main page
once it answers.

The database lands in `./data`, created and owned correctly on first start: the logger container
starts as root, fixes the bind mount that Docker creates root-owned, and drops to uid 1000
before running anything. Set `user:` on the service to skip that and manage the directory
yourself. To check the configuration without starting anything:

```bash
docker compose run --rm wallconnectorlog python wallconnectorlog.py check
```

### Updating

```bash
docker compose down
docker compose pull
docker compose up -d
```

When a release changes `docker-compose.yml`, fetch the new one first with the `curl -O` line
above. `.env` is yours and never gets overwritten.

### Grafana

Opens straight onto the dashboard: the datasource, the dashboard, the SQLite plugin and no-login
anonymous access are all baked into the `wallconnectorlog-grafana` image, and its own state
lives in a named volume. Visitors get the Viewer role — add
`GF_AUTH_ANONYMOUS_ORG_ROLE=Editor` under the grafana service's `environment:` to edit panels
from the browser (any `GF_*` setting can be overridden the same way). To run without Grafana,
start just the logger: `docker compose up -d wallconnectorlog`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `WC_HOST` | – | Charger address. Required. |
| `WC_DB` | `/data/wallconnectorlog.db` | SQLite file |
| `WC_PORT` | `4680` | Port to listen on. Under Docker, change the `ports:` line instead. |
| `WC_INTERVAL_CHARGING` | `5` | Poll seconds while charging |
| `WC_INTERVAL_CONNECTED` | `15` | Poll seconds while plugged in but idle |
| `WC_INTERVAL_IDLE` | `60` | Poll seconds while nothing is plugged in |
| `WC_RETAIN_DAYS` | `90` | How long raw samples are kept. Sessions are kept forever. |
| `WC_BACKUP_INTERVAL_H` | `24` | Hours between automatic backups. `0` turns them off. |
| `WC_BACKUP_KEEP` | `7` | How many backups to keep |
| `WC_BACKUP_DIR` | `<db folder>/backups` | Where backups are written |
| `WC_GRAFANA_URL` | `:3399` in the image | Where the Grafana link points. The default follows the host you browse from; set a full URL such as `https://grafana.example.com` in `.env` when Grafana has a reverse-proxy name of its own; empty hides the link. |
| `WC_GRAFANA_HEALTH` | `http://grafana:3000/api/health` | Checked before the link is shown |

## Backup and restore

Everything lives in one SQLite file, but **do not copy it while the service runs** — WAL mode
keeps recent writes in a separate `-wal` file, and a plain copy silently loses them. These take
consistent snapshots of the live database instead:

- Automatic: written to `data/backups/` every 24 hours, keeping the last 7
- On demand: `docker compose exec wallconnectorlog python wallconnectorlog.py backup`
- Over HTTP: `curl -OJ http://localhost:4680/api/backup` (`/api/backups` lists them)

Restore with the service stopped — it refuses a database that is in use, verifies the file
first, and saves the current database alongside before replacing it:

```bash
docker compose stop wallconnectorlog
docker compose run --rm wallconnectorlog python wallconnectorlog.py restore /data/backups/FILE.db
docker compose start wallconnectorlog
```

## Endpoints

| Path | Returns |
|---|---|
| `/` | The web page |
| `/api/live` | Latest reading, lifetime counters, device info, open session |
| `/api/sessions` | The most recent sessions, newest first. `limit` (default 200, max 1000) and `before=<id>` page further back |
| `/api/sessions/<id>/samples` | Every stored sample of one session, per-phase voltages and currents included. Empty once the samples have aged out of `WC_RETAIN_DAYS`; 404 for an unknown id |
| `/api/history?hours=24` | Raw samples for a period |
| `/api/wifi?hours=24` | Wi-Fi signal history for a period: RSSI, SNR, connected and internet flags, one row per slow poll |
| `/api/lifetime?days=30` | The charger's lifetime counters, the last reading of each UTC day, at most a year back |
| `/api/errors` | Recent failed polls |
| `/metrics` | Prometheus exposition |
| `/api/backup` | Downloads a consistent snapshot of the database |
| `/api/backups` | Lists stored backups |
| `/healthz` | 200 while the poll loop is alive, 503 if it has stalled. An unreachable charger is reported in the body, not treated as unhealthy. |

## The iPhone app

[WallConnectorLog for iOS](https://apps.apple.com/app/wallconnectorlog/id6807546205) is the
native companion to this server: live status, the session log with per-session curves and the
lifetime counters on the phone, reading the API above. It needs nothing more than the server's
address, on your own network or through a reverse proxy with a certificate. Its source lives in
[mews-se/wallconnectorlog-ios](https://github.com/mews-se/wallconnectorlog-ios) and its site at
[mews-se.github.io/wallconnectorlog-site](https://mews-se.github.io/wallconnectorlog-site/).

## What the charger reports, and what it means

Tesla documents none of the API. `evse_state` is translated using the mapping the Home Assistant
integration, the ioBroker adapter and the Wall Monitor app agree on — with one deliberate
difference: **state 7 is not an error.** Existing lists call it "error" or "finished charging",
but in captured charge sequences (`1 → 7 → 9 → 11 → 9 → 1`) it appears immediately after
plugging in, with no session energy and no current — a transient hand-shake, shown here as
"Starting up".

`evse_not_ready_reasons`, `config_status` and `current_alerts` are stored raw and never
interpreted: no published mapping reconciles with what real chargers emit, and the Wall Monitor
authors state outright that they do not know what the alert counter counts. A high `alert_count`
is normal, not a fault.

Two firmware differences are handled: older units report a single `relay_coil_v` where newer
ones report `relay_k1_v` and `relay_k2_v`, and `wifi_ssid` arrives base64-encoded and is decoded
before being stored.

## How sessions are derived

`vehicle_connected` going true opens a session; going false closes it. While it is open, the
charger's own `session_energy_wh` and `session_s` are tracked, along with peak power, peak
handle temperature and average grid voltage. Time with `contactor_closed` set is counted
separately, so a session records both how long the car was plugged in and how long it actually
drew current. Power is computed per phase as voltage times current, forced to zero while the
contactor is open — the charger reports a volt or two of noise on idle phases.

A note on the lifetime counters: `charge_starts` counts contactor cycles, not sessions — a
single plug-in usually produces several. Do not divide lifetime energy by it and call the result
an average session.

## Testing

`tests/` runs against a scripted stand-in charger, no hardware needed: session derivation from a
replayed charge, backup completeness from a running instance, restore safety, and a
demonstration of the WAL trap above. CI runs them on every push, plus lint and a container smoke
test.

```bash
python3 tests/test_session.py
python3 tests/test_backup.py
```

## License

MIT. See [LICENSE](LICENSE).

Not affiliated with, endorsed by, or supported by Tesla. "Tesla" and "Wall Connector" are
trademarks of Tesla, Inc., used here only to say what this talks to.
