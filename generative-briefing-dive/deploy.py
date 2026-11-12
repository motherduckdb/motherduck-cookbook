"""Deploy a recipe's Flight and Dive to MotherDuck with the Flight and Dive SQL functions.

    MOTHERDUCK_TOKEN=... uv run --with duckdb==1.5.6 python deploy.py recipes/nyc-311 [--dry-run] [--no-schedule]

1. Builds the recipe (build.py).
2. Creates the Flight, or updates it when a Flight with the same name exists (MD_LIST_FLIGHTS):
   source, requirements, the config keys the Dive sends, a daily 06:00 UTC schedule, the
   run chart, a 600-second cap.
3. Writes the Flight id into app.json and rebuilds, so the Dive runs that Flight.
4. Creates each board's Dive, or adds a version when a Dive with the same title exists
   (MD_LIST_DIVES). A recipe with boards gets one Dive per board; their ids go into
   app.json, and the Dives are rebuilt once more so each links to the others.

The Dive reads only the recipe's output database. Run the briefing once
(SELECT * FROM MD_RUN_FLIGHT(flight_id := '<id>')) so it has a page to show.
With --dry-run it builds and prints what it would create or update, without writing.
"""

import argparse
import json
import pathlib
import re
import subprocess
import sys

import duckdb

ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_KEYS = {"MODE": "briefing", "PROMPT": "", "REQUEST_ID": "", "PARENT": "", "AS_OF": "", "FEEDBACK": "", "BOARD": ""}


def build(recipe):
    subprocess.run([sys.executable, str(ROOT / "build.py"), str(recipe)], check=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recipe")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-schedule", action="store_true", help="create the Flight without the daily schedule")
    a = ap.parse_args()
    recipe = (ROOT / a.recipe).resolve() if not pathlib.Path(a.recipe).is_absolute() else pathlib.Path(a.recipe)
    app_path = recipe / "app.json"
    app = json.loads(app_path.read_text())
    flight_name = app["flight"]["name"]
    build(recipe)
    con = duckdb.connect("md:")

    source = (recipe / "flight" / "main.py").read_text()
    requirements = (recipe / "flight" / "requirements.txt").read_text()
    chart = (recipe / "flight" / "chart.mmd").read_text()
    model = re.search(r'^DEFAULT_MODEL\s*=\s*"([^"]+)"', (recipe / "domain.py").read_text(), re.M)
    config = dict(CONFIG_KEYS, MODEL=model.group(1) if model else "")
    existing = con.execute("SELECT flight_id FROM MD_LIST_FLIGHTS(owner_only := true) WHERE flight_name = ?", [flight_name]).fetchone()
    print(f"Flight {flight_name!r}: {'update ' + str(existing[0]) if existing else 'create'}")
    if not a.dry_run:
        for k, v in {"src": source, "req": requirements, "chart": chart}.items():
            con.execute(f"SET VARIABLE {k} = ?", [v])
        con.execute("SET VARIABLE cfg = MAP(?, ?)", [list(config), list(config.values())])
        schedule = "" if a.no_schedule else app["flight"].get("schedule", "0 6 * * *")
        args = (
            "source_code := getvariable('src'), requirements_txt := getvariable('req'), config := getvariable('cfg'), "
            f"max_runtime_sec := {int(app['flight'].get('maxRuntimeSec', 600))}, metadata := MAP {{'chart': getvariable('chart')}}"
        )
        if existing:
            flight_id = str(existing[0])
            cron = f"schedule_cron := '{schedule}', " if schedule else ""  # an empty cron on a Flight without a schedule is an error
            con.execute(f"CALL MD_UPDATE_FLIGHT(flight_id := '{flight_id}', {cron}{args})")
        else:
            cron = f"schedule_cron := '{schedule}', " if schedule else ""
            flight_id = str(con.execute(f"SELECT flight_id FROM MD_CREATE_FLIGHT(name := '{flight_name}', {cron}{args})").fetchone()[0])
        if app.get("flightId") != flight_id:
            app["flightId"] = flight_id
            app_path.write_text(json.dumps(app, indent="\t", ensure_ascii=False) + "\n")
            build(recipe)
        print(f"  flight_id {flight_id}")

    dirs = [("", "dive")] if not app.get("boards") else [(b, "dive" if i == 0 else f"dive-{b}") for i, b in enumerate(app["boards"])]
    created = deploy_dives(con, recipe, app, dirs, a.dry_run)
    if created and not a.dry_run:
        # New Dive ids: write them to app.json and rebuild, so every board links to the others.
        app = json.loads(app_path.read_text())
        for board, dive_id in created.items():
            app["boards"][board]["diveId"] = dive_id
        app_path.write_text(json.dumps(app, indent="\t", ensure_ascii=False) + "\n")
        build(recipe)
        deploy_dives(con, recipe, app, dirs, False)


def deploy_dives(con, recipe, app, dirs, dry_run):
    """Create or update each board's Dive. Returns {board: id} for Dives created now."""
    created = {}
    for board, dirname in dirs:
        meta = json.loads((recipe / dirname / "dive.metadata.json").read_text())
        title = meta["title"]
        content = (recipe / dirname / "index.tsx").read_text()
        resources = app.get("requiredDatabases") or [{"type": "database", "path": f"md:{app['db']}", "alias": app["db"]}]
        res_sql = "[" + ", ".join(f"{{'url': '{r['path']}', 'alias': '{r['alias']}'}}" for r in resources) + "]"
        dive = con.execute("SELECT id FROM MD_LIST_DIVES() WHERE title = ?", [title]).fetchone()
        print(f"Dive {title!r}{' (board ' + board + ')' if board else ''}: {'new version of ' + str(dive[0]) if dive else 'create'}")
        if dry_run:
            continue
        con.execute("SET VARIABLE dive_src = ?", [content])
        if dive:
            con.execute(f"SELECT * FROM MD_UPDATE_DIVE_CONTENT(id := '{dive[0]}', content := getvariable('dive_src'), required_resources := {res_sql})")
            dive_id = str(dive[0])
        else:
            con.execute("SET VARIABLE dive_desc = ?", [meta.get("description", "")])
            dive_id = str(con.execute(f"SELECT id FROM MD_CREATE_DIVE(title := '{title.replace(chr(39), chr(39) * 2)}', content := getvariable('dive_src'), description := getvariable('dive_desc'), required_resources := {res_sql})").fetchone()[0])
        if board and app["boards"][board].get("diveId") != dive_id:
            created[board] = dive_id
        print(f"  https://app.motherduck.com/dives/{dive_id}")
    return created


if __name__ == "__main__":
    main()
