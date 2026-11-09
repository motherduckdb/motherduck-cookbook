"""Deploy a recipe's Flight and Dive to MotherDuck with the Flight and Dive SQL functions.

    MOTHERDUCK_TOKEN=... uv run --with duckdb python deploy.py recipes/nyc-311 [--dry-run] [--no-schedule]

1. Builds the recipe (build.py).
2. Creates the Flight, or updates it when a Flight with the same name exists (MD_LIST_FLIGHTS):
   source, requirements, the config keys the Dive sends, a daily 06:00 UTC schedule, the
   run chart, a 600-second cap.
3. Writes the Flight id into app.json and rebuilds, so the Dive runs that Flight.
4. Creates the Dive, or adds a version when a Dive with the same title exists (MD_LIST_DIVES).

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
CONFIG_KEYS = {"MODE": "briefing", "PROMPT": "", "REQUEST_ID": "", "PARENT": "", "AS_OF": "", "FEEDBACK": ""}


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
            con.execute(f"CALL MD_UPDATE_FLIGHT(flight_id := '{flight_id}', schedule_cron := '{schedule}', {args})")
        else:
            cron = f"schedule_cron := '{schedule}', " if schedule else ""
            flight_id = str(con.execute(f"SELECT flight_id FROM MD_CREATE_FLIGHT(name := '{flight_name}', {cron}{args})").fetchone()[0])
        if app.get("flightId") != flight_id:
            app["flightId"] = flight_id
            app_path.write_text(json.dumps(app, indent="\t", ensure_ascii=False) + "\n")
            build(recipe)
        print(f"  flight_id {flight_id}")

    title = json.loads((recipe / "dive" / "dive.metadata.json").read_text())["title"]
    content = (recipe / "dive" / "index.tsx").read_text()
    resources = app.get("requiredDatabases") or [{"type": "database", "path": f"md:{app['db']}", "alias": app["db"]}]
    res_sql = "[" + ", ".join(f"{{'url': '{r['path']}', 'alias': '{r['alias']}'}}" for r in resources) + "]"
    dive = con.execute("SELECT id FROM MD_LIST_DIVES() WHERE title = ?", [title]).fetchone()
    print(f"Dive {title!r}: {'new version of ' + str(dive[0]) if dive else 'create'}")
    if not a.dry_run:
        con.execute("SET VARIABLE dive_src = ?", [content])
        if dive:
            con.execute(f"SELECT * FROM MD_UPDATE_DIVE_CONTENT(id := '{dive[0]}', content := getvariable('dive_src'), required_resources := {res_sql})")
            dive_id = str(dive[0])
        else:
            desc = json.loads((recipe / "dive" / "dive.metadata.json").read_text()).get("description", "")
            con.execute("SET VARIABLE dive_desc = ?", [desc])
            dive_id = str(con.execute(f"SELECT id FROM MD_CREATE_DIVE(title := '{title.replace(chr(39), chr(39) * 2)}', content := getvariable('dive_src'), description := getvariable('dive_desc'), required_resources := {res_sql})").fetchone()[0])
        print(f"  https://app.motherduck.com/dives/{dive_id}")


if __name__ == "__main__":
    main()
