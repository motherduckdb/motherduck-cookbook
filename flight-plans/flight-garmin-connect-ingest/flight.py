"""
Ingest Garmin Connect fitness data into MotherDuck on a schedule.

This Flight runs HEADLESS: there is no terminal on MotherDuck compute to type a
Garmin MFA code into, so it never logs in with a password. Instead it reads a
serialized Garmin OAuth token from a MotherDuck `TYPE flights` secret and passes
it straight to garminconnect. Mint that token once, locally, with the snippet in
the README ("Get a Garmin token") — Garmin's first login is always interactive
(password + MFA), but the cached DI refresh token then auto-renews for ~1 year.

Everything is driven by Flight config, so you adapt it by setting config values
rather than editing this file. A fresh deploy backfills the last BACKFILL_DAYS
and builds two tables in your own account:

  activities      one row per activity (get_activities_by_date). Distance,
                  duration, speeds, HR, elevation, cadence, and training effect
                  are kept as Garmin returns them; pace is derived later in SQL.
                  For activity types in HR_ZONES_FOR (default "running") it also
                  pulls per-activity HR time-in-zone so an easy-vs-hard split is
                  based on real zone seconds, not a guess.
  daily_metrics   one row per calendar day: steps + resting/min/max HR
                  (get_stats), VO2max (get_max_metrics), and optionally training
                  readiness / status / acute & chronic load / ACWR.

Load strategy (idempotent re-runs):
  - First run (destination absent) backfills BACKFILL_DAYS; later runs re-pull
    only the last INCREMENTAL_DAYS so late-syncing activities and lagging
    training-load values get corrected.
  - activities are append-only, anti-joined on activity_id (immutable once
    recorded).
  - daily_metrics is delete-then-insert over the pulled date range, because
    values like training load and VO2max backfill into earlier days.

Null-heavy columns (VO2max, elevation, and training load are null on rest days)
are read with sample_size=-1 so DuckDB scans every row before inferring types;
otherwise an all-null prefix gets mis-typed as JSON and breaks casts/ORDER BY.
"""

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone

import duckdb
from garminconnect import Garmin

IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
STAGE_DIR = os.environ.get("TMPDIR", "/tmp").rstrip("/")


def main() -> None:
    # Every knob is read from Flight config/env, so you adapt this template by
    # setting config values rather than editing code.
    database = validate_identifier("DESTINATION_DATABASE", env("DESTINATION_DATABASE", "garmin"))
    schema = validate_identifier("DESTINATION_SCHEMA", env("DESTINATION_SCHEMA", "main"))
    act_table = validate_identifier("ACTIVITIES_TABLE", env("ACTIVITIES_TABLE", "activities"))
    daily_table = validate_identifier("DAILY_METRICS_TABLE", env("DAILY_METRICS_TABLE", "daily_metrics"))

    backfill_days = env_int("BACKFILL_DAYS", 56)
    incremental_days = env_int("INCREMENTAL_DAYS", 3)
    force_backfill = env_int("FORCE_BACKFILL_DAYS", 0)
    pull_training_load = env_bool("PULL_TRAINING_LOAD", True)
    hr_zone_types = {t.strip() for t in env("HR_ZONES_FOR", "running").split(",") if t.strip()}

    activities_fqtn = f"{database}.{schema}.{act_table}"
    daily_fqtn = f"{database}.{schema}.{daily_table}"

    con = duckdb.connect("md:")
    con.execute("SET TimeZone = 'UTC'")  # determinism for any timestamp casts
    # The Flight creates its own destination, so it runs on the first deploy
    # without depending on a database or schema that already exists.
    con.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")

    table_exists = con.execute(
        """
        SELECT count(*) FROM information_schema.tables
        WHERE table_catalog = ? AND table_schema = ? AND table_name = ?
        """,
        [database, schema, act_table],
    ).fetchone()[0] > 0

    today = date.today()
    if force_backfill > 0:
        start = (today - timedelta(days=force_backfill)).isoformat()
        print(f"Forced backfill ({force_backfill}d): {start} .. {today.isoformat()}")
    elif table_exists:
        start = (today - timedelta(days=incremental_days)).isoformat()
        print(f"Incremental run: {start} .. {today.isoformat()}")
    else:
        start = (today - timedelta(days=backfill_days)).isoformat()
        print(f"Backfill run ({backfill_days}d): {start} .. {today.isoformat()}")
    end = today.isoformat()
    print(f"destination={database}.{schema}  pull_training_load={pull_training_load}")

    client = connect_garmin()
    now_iso = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    # Activities.
    print("Pulling activities...")
    act_rows = fetch_activities(client, start, end, hr_zone_types, now_iso)

    # Daily metrics — one row per calendar day in the window.
    print("Pulling daily metrics...")
    daily_rows = []
    d = date.fromisoformat(start)
    end_d = date.fromisoformat(end)
    first = not table_exists  # only debug-print payload shapes on the backfill run
    while d <= end_d:
        cdate = d.isoformat()
        daily_rows.append(
            fetch_daily(client, cdate, now_iso, debug=first, pull_training_load=pull_training_load)
        )
        first = False
        time.sleep(0.3)  # be gentle with Garmin's API
        d += timedelta(days=1)
    print(f"  built {len(daily_rows)} daily rows")

    # Load.
    if act_rows:
        load_activities(con, activities_fqtn, act_rows)
    else:
        print("  no activities to load")
    load_daily(con, daily_fqtn, daily_rows, start, end)

    con.close()
    print("Done.")


# --------------------------------------------------------------------------- #
#  Auth                                                                        #
# --------------------------------------------------------------------------- #
def connect_garmin() -> Garmin:
    token = resolve_secret_param("GARMIN_TOKEN")
    if not token:
        raise RuntimeError(
            "No Garmin token. Set GARMIN_TOKEN locally, or deploy a TYPE flights "
            "secret whose GARMIN_TOKEN param arrives as <secret_name>_GARMIN_TOKEN. "
            "Mint the token with the 'Get a Garmin token' snippet in the README."
        )
    client = Garmin()
    # garminconnect treats a >512-char tokenstore as a serialized token (it calls
    # client.loads) rather than a directory path, so the secret value goes
    # straight in — no local token file on the Flight. The DI refresh token
    # inside it auto-renews (~1yr); re-mint the secret if it ever expires.
    client.login(tokenstore=token)
    print(f"Garmin auth OK — {client.get_full_name()}")
    return client


# --------------------------------------------------------------------------- #
#  Activities                                                                  #
# --------------------------------------------------------------------------- #
def fetch_activities(client, start: str, end: str, hr_zone_types: set, now_iso: str) -> list:
    raw = retry(client.get_activities_by_date, start, end, label="get_activities_by_date") or []
    print(f"  fetched {len(raw)} activities {start}..{end}")
    rows = []
    for a in raw:
        atype = (a.get("activityType") or {}).get("typeKey")
        act_id = a.get("activityId")
        start_local = a.get("startTimeLocal") or ""
        row = {
            "activity_id": act_id,
            "activity_date": start_local[:10] if start_local else None,
            "activity_type": atype,
            "activity_name": a.get("activityName"),
            "start_time_local": start_local or None,
            "start_time_gmt": a.get("startTimeGMT"),
            "distance_m": fnum(a.get("distance")),
            "duration_s": fnum(a.get("duration")),
            "moving_duration_s": fnum(a.get("movingDuration")),
            "elapsed_duration_s": fnum(a.get("elapsedDuration")),
            "avg_speed_mps": fnum(a.get("averageSpeed")),
            "max_speed_mps": fnum(a.get("maxSpeed")),
            "avg_hr": fnum(a.get("averageHR")),
            "max_hr": fnum(a.get("maxHR")),
            "elevation_gain_m": fnum(a.get("elevationGain")),
            "elevation_loss_m": fnum(a.get("elevationLoss")),
            "calories": fnum(a.get("calories")),
            "avg_run_cadence_spm": fnum(a.get("averageRunningCadenceInStepsPerMinute")),
            "avg_swim_cadence_spm": fnum(a.get("averageSwimCadenceInStrokesPerMinute")),
            "avg_swolf": fnum(a.get("averageSwolf")),
            "pool_length_m": fnum(a.get("poolLength")),
            "aerobic_te": fnum(a.get("aerobicTrainingEffect")),
            "anaerobic_te": fnum(a.get("anaerobicTrainingEffect")),
            "te_label": a.get("trainingEffectLabel"),
            # HR time-in-zone (seconds), filled below for selected activity types.
            "hr_z1_s": None, "hr_z2_s": None, "hr_z3_s": None,
            "hr_z4_s": None, "hr_z5_s": None,
            "fetched_at": now_iso,
        }

        if atype in hr_zone_types and act_id is not None:
            zones = retry(client.get_activity_hr_in_timezones, str(act_id), label=f"hr_zones {act_id}")
            if isinstance(zones, list):
                for z in zones:
                    zn = z.get("zoneNumber")
                    secs = fnum(z.get("secsInZone"))
                    if zn in (1, 2, 3, 4, 5):
                        row[f"hr_z{zn}_s"] = secs
            time.sleep(0.3)

        rows.append(row)
    return rows


# --------------------------------------------------------------------------- #
#  Daily metrics                                                               #
# --------------------------------------------------------------------------- #
def fetch_daily(client, cdate: str, now_iso: str, debug: bool, pull_training_load: bool) -> dict:
    stats = retry(client.get_stats, cdate, label=f"get_stats {cdate}") or {}
    maxm = retry(client.get_max_metrics, cdate, label=f"get_max_metrics {cdate}")
    # Training Readiness/Status are empty on some devices/accounts and add 2 calls
    # per day; skip them when disabled (e.g. during a long backfill).
    readiness = (
        retry(client.get_training_readiness, cdate, label=f"get_training_readiness {cdate}")
        if pull_training_load else None
    )
    status = (
        retry(client.get_training_status, cdate, label=f"get_training_status {cdate}")
        if pull_training_load else None
    )

    if debug:
        # One-time shape check on the first backfill date (truncated) so the
        # deep_find paths can be confirmed against real payloads in the run logs.
        def head(x):
            s = json.dumps(x, default=str)
            return s[:600] + ("...(truncated)" if len(s) > 600 else "")
        print(f"  [debug {cdate}] max_metrics: {head(maxm)}")
        print(f"  [debug {cdate}] readiness:   {head(readiness)}")
        print(f"  [debug {cdate}] status:      {head(status)}")

    # VO2max: get_max_metrics is typically a 1-element list with generic/cycling.
    vo2_run = vo2_cycle = None
    mm = maxm[0] if isinstance(maxm, list) and maxm else (maxm or {})
    if isinstance(mm, dict):
        vo2_run = fnum((mm.get("generic") or {}).get("vo2MaxValue"))
        vo2_cycle = fnum((mm.get("cycling") or {}).get("vo2MaxValue"))

    # Training readiness: typically a 1-element list with a "score".
    readiness_score = None
    if isinstance(readiness, list) and readiness:
        readiness_score = fnum(readiness[0].get("score"))
    elif isinstance(readiness, dict):
        readiness_score = fnum(readiness.get("score"))

    return {
        "metric_date": cdate,
        "total_steps": fnum(stats.get("totalSteps")),
        "resting_hr": fnum(stats.get("restingHeartRate")),
        "min_hr": fnum(stats.get("minHeartRate")),
        "max_hr": fnum(stats.get("maxHeartRate")),
        "total_kcal": fnum(stats.get("totalKilocalories")),
        "vo2max_running": vo2_run,
        "vo2max_cycling": vo2_cycle,
        "training_readiness": readiness_score,
        "training_status": deep_find(status, "trainingStatus") if status else None,
        "acute_load": fnum(deep_find(status, "acuteTrainingLoad")) if status else None,
        "chronic_load": fnum(deep_find(status, "dailyTrainingLoadChronic")) if status else None,
        "acwr": fnum(deep_find(status, "acwrPercent")) if status else None,
        "fetched_at": now_iso,
    }


# --------------------------------------------------------------------------- #
#  Load                                                                        #
# --------------------------------------------------------------------------- #
def stage(rows: list, path: str) -> None:
    with open(path, "w") as f:
        json.dump(rows, f, default=str)


def load_activities(con, fqtn: str, rows: list) -> None:
    path = f"{STAGE_DIR}/garmin_activities.json"
    stage(rows, path)
    # read_json_auto(..., sample_size=-1): scan every row before inferring types so
    # null-heavy columns (cadence, HR zones) are not mis-typed from an all-null prefix.
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {fqtn} AS "
        "SELECT * FROM read_json_auto(?, sample_size=-1) WHERE false",
        [path],
    )
    # Append only activities we do not already have (immutable once recorded).
    con.execute(
        f"""
        INSERT INTO {fqtn} BY NAME
        SELECT s.* FROM read_json_auto(?, sample_size=-1) s
        LEFT JOIN {fqtn} t ON s.activity_id = t.activity_id
        WHERE t.activity_id IS NULL
        """,
        [path],
    )
    total = con.execute(f"SELECT count(*) FROM {fqtn}").fetchone()[0]
    print(f"  {fqtn}: {len(rows)} pulled, {total} total rows")


def load_daily(con, fqtn: str, rows: list, start: str, end: str) -> None:
    path = f"{STAGE_DIR}/garmin_daily.json"
    stage(rows, path)
    con.execute(
        f"CREATE TABLE IF NOT EXISTS {fqtn} AS "
        "SELECT * FROM read_json_auto(?, sample_size=-1) WHERE false",
        [path],
    )
    # Delete-then-insert the pulled range: training load / VO2max values backfill
    # into earlier days, so re-pulling the window keeps them current and is idempotent.
    con.execute(f"DELETE FROM {fqtn} WHERE metric_date BETWEEN ? AND ?", [start, end])
    con.execute(f"INSERT INTO {fqtn} BY NAME SELECT * FROM read_json_auto(?, sample_size=-1)", [path])
    total = con.execute(f"SELECT count(*) FROM {fqtn}").fetchone()[0]
    print(f"  {fqtn}: {len(rows)} pulled, {total} total rows")


# --------------------------------------------------------------------------- #
#  Helpers                                                                     #
# --------------------------------------------------------------------------- #
def env(name: str, default: str) -> str:
    value = os.environ.get(name, default).strip()
    return value or default


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw.lstrip("-").isdigit() else default


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def validate_identifier(name: str, value: str) -> str:
    # Database, schema, and table names flow into CREATE/DELETE/INSERT statements
    # that cannot be parameterized, so reject anything that is not a plain SQL
    # identifier before any SQL runs.
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{name} must be a simple SQL identifier, got {value!r}")
    return value


def resolve_secret_param(param: str) -> str:
    # Resolve a value that may come from a MotherDuck `TYPE flights` secret. A local
    # run can set the bare env var (e.g. GARMIN_TOKEN). Deployed as a Flight, the
    # secret injects each param under `<secret_name>_<PARAM>`, NOT the bare name: a
    # secret `garmin_auth` with a GARMIN_TOKEN param arrives as
    # `garmin_auth_GARMIN_TOKEN`. Accept both — the exact name first (local), then
    # any var ending in `_<PARAM>` (the secret, whatever you named it).
    direct = os.environ.get(param, "").strip()
    if direct:
        return direct
    suffix = f"_{param}"
    for key, value in os.environ.items():
        if key.endswith(suffix) and value.strip():
            return value.strip()
    return ""


def retry(fn, *args, tries: int = 3, base: float = 1.0, label: str = ""):
    """Call fn(*args) with simple exponential backoff; return None on failure."""
    for i in range(tries):
        try:
            return fn(*args)
        except Exception as e:  # noqa: BLE001 - garminconnect raises broad types
            wait = base * (2 ** i)
            print(f"  {label}: attempt {i + 1}/{tries} failed ({e}); retry in {wait:.0f}s")
            time.sleep(wait)
    print(f"  {label}: giving up after {tries} attempts")
    return None


def fnum(v):
    """Coerce to float or None (Garmin sometimes returns '' or odd types)."""
    try:
        if v is None or v == "":
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def deep_find(obj, key):
    """First value for `key` anywhere in a nested dict/list, else None.

    Training-status payloads are deviceId-keyed and deeply nested, so a fixed path
    is brittle; a recursive search for the leaf key is robust across devices.
    """
    if isinstance(obj, dict):
        if key in obj and not isinstance(obj[key], (dict, list)):
            return obj[key]
        for v in obj.values():
            found = deep_find(v, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = deep_find(item, key)
            if found is not None:
                return found
    return None


if __name__ == "__main__":
    main()
