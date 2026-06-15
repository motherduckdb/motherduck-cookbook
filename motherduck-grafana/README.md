---
title: Visualize MotherDuck Data in Grafana
id: motherduck-grafana
description: >-
  Runs a local Grafana instance in Docker with the MotherDuck DuckDB datasource
  plugin auto-provisioned, plus example dashboards that query MotherDuck with
  SQL. Use when you want to build Grafana dashboards or time-series panels on top
  of MotherDuck data.
type: example
features: []
tags: [grafana, docker]
---

# Visualize MotherDuck Data in Grafana

A local Grafana setup that connects to MotherDuck through the `motherduck-duckdb-datasource` plugin. The `setup.sh` script downloads the latest plugin release from `motherduckdb/grafana-duckdb-datasource`, starts a Grafana container, and mounts a `provisioning/` directory so the datasource and example dashboards are configured on boot. It shows the MotherDuck pattern of using an access token as a Grafana datasource credential, attaching a MotherDuck database (here the public `sample_data` share) on connect, and running DuckDB SQL directly in panel queries with results formatted as time series.

## How it works

- `setup.sh`:
  - Detects the OS and resolves the plugin directory. Windows (`CYGWIN`/`MINGW`/`MSYS`) and unknown systems exit early; macOS and Linux are supported.
  - Queries the GitHub API for the latest `motherduck-duckdb-datasource` release, downloads the `.zip`, and unzips it into `plugins/` (gitignored, so the plugin is fetched fresh and never committed).
  - Stops and removes any prior Grafana container on port 3000, then fails fast if the port is still in use:
    ```bash
    if lsof -Pi :3000 -sTCP:LISTEN -t >/dev/null; then
      echo "❌ Port 3000 is already in use. Please free up the port and try again."
      exit 1
    fi
    ```
  - Validates the token before starting the container, so a missing credential fails loudly instead of producing a half-broken datasource:
    ```bash
    if ! printenv motherduck_token >/dev/null 2>&1; then
      echo "❌ Required environment variable 'motherduck_token' is not set." >&2
      exit 1
    fi
    ```
  - Runs the container with the plugin and `provisioning/` mounted, allows the unsigned plugin via `GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS=motherduck-duckdb-datasource`, and passes the token through as an environment variable. It then waits up to 5 seconds for the container to report `running`.
- `provisioning/datasources/sample_data.yaml`: defines the datasource. `database` is blank (MotherDuck infers it), `initSql` attaches `md:sample_data` on connect, and the token is read at runtime from the `motherduck_token` environment variable via `$__env{motherduck_token}`, so no secret is written into the file.
- `provisioning/dashboards/dashboards.yaml`: registers the file provider that loads every dashboard from `/etc/grafana/provisioning/dashboards/json`.
- `provisioning/dashboards/json/`: holds the dashboard definitions. `nyc_services.json` groups `sample_data.nyc.service_requests` by `created_date`; `nyc_rideshare.json` groups `sample_data.nyc.rideshare` by `request_datetime`.

## Questions to answer

- Which MotherDuck database(s) and schema(s) should be attached (the `initSql` `ATTACH` target)?
- Which tables or queries power the dashboards, and what is the time column for time-series panels?
- Is a MotherDuck access token available, and should it be a read-scaling token (recommended for read-only dashboard traffic)?
- Should this stay a local Docker setup, or be adapted for a hosted Grafana deployment?
- Are there existing dashboard JSON exports to drop into `provisioning/dashboards/json/`?

## Caveats

- **Token in the environment, not the config.** The token is injected through `$__env{motherduck_token}` and the Docker `-e` flag at runtime. Do not hardcode it into `sample_data.yaml`, which is committed to the repo.
- **Token must be exported before `setup.sh`.** If `motherduck_token` is unset the script exits before starting Grafana. If you start Grafana some other way without the variable, the datasource provisions but every query fails to authenticate.
- **Windows is not supported by the script.** It exits on `CYGWIN`/`MINGW`/`MSYS`. On Windows, download and unzip the plugin into the Grafana plugin folder manually and run the container yourself.
- **Port 3000 must be free.** The script stops a prior Grafana container on that port, but if anything else is listening on 3000 it aborts. Change the `-p` mapping if 3000 is taken.
- **Unsigned plugin.** The datasource is loaded via `GF_PLUGINS_ALLOW_LOADING_UNSIGNED_PLUGINS`. Grafana will refuse to load it without that allow-list entry.
- **Time-series panels need a time column.** The panel format defaults to a table view; switch it to **time series** and make sure the query returns a timestamp/date column (`created_date`, `request_datetime`), or the panel renders nothing useful.
- **Builder mode does not write DuckDB SQL for you.** Switch the query editor to **code** mode to run DuckDB syntax; the visual builder will not produce the MotherDuck-specific queries.
- **`plugins/` is downloaded, not committed.** A fresh clone has no plugin until `setup.sh` runs and reaches GitHub; an offline machine cannot provision the datasource.
- **`sample_data` is a public MotherDuck share.** The example dashboards read from it. To query your own data, change the `initSql` `ATTACH` target and the panel `rawSql` to your database and tables.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `motherduck_token` env var | MotherDuck auth token passed to Grafana and the datasource. `setup.sh` requires it (it exits if unset) and the datasource reads it as `$__env{motherduck_token}` | `export motherduck_token=<your-token>` |
| `provisioning/datasources/sample_data.yaml` `initSql` | DuckDB SQL run when the datasource connects, used to attach databases | `ATTACH IF NOT EXISTS 'md:sample_data'`, or `ATTACH IF NOT EXISTS 'md:my_db'` |
| `provisioning/datasources/sample_data.yaml` `database` | Default database for the connection. Leave blank for MotherDuck | `""` |
| `provisioning/datasources/sample_data.yaml` `name` / `isDefault` | Datasource display name and default flag panels reference | `MotherDuck-Sample-Data`, `isDefault: true` |
| Panel `rawSql` in `provisioning/dashboards/json/*.json` | The DuckDB query each dashboard panel runs | `SELECT created_date, COUNT(status) FROM sample_data.nyc.service_requests GROUP BY created_date LIMIT 50` |
| `provisioning/dashboards/json/*.json` | Dashboard definitions auto-loaded from this folder | `nyc_services.json`, `nyc_rideshare.json`, or your own exported dashboard |
| `provisioning/dashboards/dashboards.yaml` `path` | Where Grafana looks for dashboard JSON inside the container | `/etc/grafana/provisioning/dashboards/json` |
| Grafana port mapping in `setup.sh` | Host port Grafana is published on | `-p 3000:3000` |
| Grafana image tag in `setup.sh` | Grafana version that runs in the container | `grafana/grafana:latest-ubuntu` |

## Run it

Prerequisites: Docker installed and running, plus a MotherDuck access token. macOS and Linux only (see Caveats for Windows).

```bash
export motherduck_token=<your-motherduck-token>
cd motherduck-grafana
./setup.sh
```

Then open Grafana at `http://localhost:3000` (default login `admin` / `admin`). The MotherDuck datasource and the example NYC dashboards (`NYC Services` and `NYC_rideshare`) are already provisioned.

When building a new panel:

1. Create a new dashboard and add a panel.
2. Select `MotherDuck-Sample-Data` as the data source.
3. Set the panel format to **time series** at the top of the query (panels expect a time column).
4. Switch the query editor from **builder** to **code** to enter DuckDB SQL manually, for example:

```sql
SELECT created_date, COUNT(status)
FROM sample_data.nyc.service_requests
GROUP BY created_date LIMIT 1000
```

`image.png` shows where to set the time-series format and the code-mode query editor.

To add your own dashboard, export it from the Grafana UI (the **Export** button, or copy the JSON model from dashboard settings under **JSON model**), save it as `provisioning/dashboards/json/<name>.json`, then commit it. It is auto-loaded on the next `setup.sh` run.

## Files

- [`setup.sh`](setup.sh) - the runner: detects the OS, downloads the latest MotherDuck DuckDB datasource plugin from GitHub, validates the `motherduck_token` env var, then starts a Grafana Docker container with the plugin and `provisioning/` mounted.
- [`provisioning/`](provisioning/) - the Grafana provisioning tree mounted into the container, holding the datasource and dashboard config:
  - [`provisioning/datasources/sample_data.yaml`](provisioning/datasources/sample_data.yaml) - defines the `MotherDuck-Sample-Data` datasource, attaching `md:sample_data` via `initSql` and reading the token from `$__env{motherduck_token}`.
  - [`provisioning/dashboards/dashboards.yaml`](provisioning/dashboards/dashboards.yaml) - registers the file provider that auto-loads every dashboard JSON from `/etc/grafana/provisioning/dashboards/json`.
  - [`provisioning/dashboards/json/`](provisioning/dashboards/json/) - the example dashboard definitions (2 files): `nyc_services.json` (NYC Services, service requests by `created_date`) and `nyc_rideshare.json` (NYC_rideshare, rideshare totals).
- [`image.png`](image.png) - screenshot showing where to set the time-series format and the code-mode query editor when building a panel.
- `.gitignore` - excludes the downloaded `plugins/` directory and `.DS_Store`, so the plugin is fetched fresh by `setup.sh` and never committed.

## Learn more

- For DuckDB SQL syntax, attaching databases, or read-scaling tokens, use the `ask_docs_question` MCP tool or the MotherDuck docs.
- MotherDuck DuckDB datasource plugin: `motherduckdb/grafana-duckdb-datasource` on GitHub.
