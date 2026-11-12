"""Build one recipe into a single-file Flight and a single-file Dive.

    python3 build.py recipes/<name> [--kit PATH]

Reads    template/engine.py, template/app.tsx, template/chart.mmd
         the component kit: --kit PATH, else $MOTHERDUCK_DIVE_KIT, else the
                                    motherduck-dive-components skill installed next to this one
         recipes/<name>/domain.py   sources, marts, catalog, briefing plan, context
         recipes/<name>/app.json    brand, theme, examples, database, Flight id
         recipes/<name>/components/*.tsx   optional custom components for this recipe
Writes   recipes/<name>/flight/main.py (+ requirements.txt, chart.mmd if missing)
         recipes/<name>/dive/index.tsx, and dive-<board>/index.tsx for every further board

Flights and Dives are single files with no local imports, so the template is
inlined, the shadcn way: copy source, don't depend on a package.
"""

import argparse
import importlib.util
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
T = ROOT / "template"
kit = None  # the component kit's assemble module, loaded in main()


def find_kit(explicit=None):
    """The directory holding assemble.py, components.json and kit/ from motherduck-dive-components."""
    candidates = [explicit, os.environ.get("MOTHERDUCK_DIVE_KIT"),
                  ROOT.parent.parent / "motherduck-dive-components" / "artifacts",  # skills installed side by side
                  ROOT / "dive-components",                                           # vendored next to build.py
                  ROOT.parent / "dive-components"]                                    # a sibling checkout
    for c in candidates:
        if c and (pathlib.Path(c) / "assemble.py").exists() and (pathlib.Path(c) / "components.json").exists():
            return pathlib.Path(c).resolve()
    raise SystemExit("Can't find the component kit. Pass --kit PATH to the motherduck-dive-components skill's artifacts/ directory, or set MOTHERDUCK_DIVE_KIT.")


def load_kit(path):
    spec = importlib.util.spec_from_file_location("assemble", path / "assemble.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def between(text, start, end):
    head, rest = text.split(start, 1)
    middle, tail = rest.split(end, 1)
    return head, middle, tail


def build_flight(recipe: pathlib.Path, app: dict):
    engine = (T / "engine.py").read_text()
    domain = (recipe / "domain.py").read_text()
    head, _, tail = between(engine, "# @domain: build.py inserts recipes/<name>/domain.py here", "# @domain-end")
    # The planner's component rules come from the component catalog, so the Flight
    # only plans components the Dive can render.
    comps = [{"type": c["type"], "plan_rule": c["plan_rule"]} for c in kit.manifest()["components"] if c.get("plan_rule")]
    catalog = "# ===== component catalog (from dive-components/components.json) =====\nCOMPONENTS = " + json.dumps(comps, indent=4, ensure_ascii=False) + "\n"
    out = head + catalog + f"# ===== recipe: {recipe.name} (from {recipe}/domain.py) =====\n" + domain.strip() + "\n# ===== end recipe =====" + tail
    fdir = recipe / "flight"
    fdir.mkdir(exist_ok=True)
    (fdir / "main.py").write_text(out)
    if not (fdir / "requirements.txt").exists():
        (fdir / "requirements.txt").write_text("duckdb==1.5.6\n")
    meta = fdir / "flight.metadata.json"
    if not meta.exists() and app.get("flight", {}).get("name"):  # push adds the Flight id here
        meta.write_text(json.dumps({"name": app["flight"]["name"]}, indent=2) + "\n")
    if not (fdir / "chart.mmd").exists():
        (fdir / "chart.mmd").write_text((T / "chart.mmd").read_text())
    compile(out, str(fdir / "main.py"), "exec")
    return len(out)


def board_dirs(app: dict):
    """(board, settings, dive directory name): the first board keeps dive/, the others get dive-<board>/."""
    boards = app.get("boards") or {}
    if not boards:
        return [("", {}, "dive")]
    return [(b, cfg, "dive" if i == 0 else f"dive-{b}") for i, (b, cfg) in enumerate(boards.items())]


def build_dive(recipe: pathlib.Path, app: dict, board: str = "", cfg: dict | None = None, dirname: str = "dive"):
    cfg = cfg or {}
    app = {**app, **{k: v for k, v in cfg.items() if k != "diveId"}}
    required = app.get("requiredDatabases") or [{"type": "database", "path": f"md:{app['db']}", "alias": app["db"]}]
    public = {k: app.get(k) for k in ("brand", "tagline", "placeholder", "theme", "themes", "db", "flightId", "examples")}
    public["board"] = board
    # Links to the recipe's other boards, once deploy.py has given them Dive ids.
    public["boards"] = [
        {"key": b, "label": c.get("label") or b, "url": f"https://app.motherduck.com/dives/{c['diveId']}" if c.get("diveId") else ""}
        for b, c in (app.get("boards") or {}).items()
    ]
    recipe_block = (
        f"export const REQUIRED_DATABASES = {json.dumps(required, indent=2)};\n\n"
        f"const APP: any = {json.dumps(public, indent=2, ensure_ascii=False)};\n"
    )
    app_text = (T / "app.tsx").read_text()
    head, _, tail = between(app_text, "// @recipe: build.py writes REQUIRED_DATABASES and APP from recipes/<name>/app.json here", "// @recipe-end")
    extras = sorted((recipe / "components").glob("*.tsx")) if (recipe / "components").is_dir() else []
    out = kit.assemble(
        head + recipe_block + tail,
        extras=extras,
        banner=f"// GENERATED by build.py for recipe '{recipe.name}'. Edit template/, the component kit or {recipe}/, then rebuild.",
    )
    ddir = recipe / dirname
    ddir.mkdir(exist_ok=True)
    (ddir / "index.tsx").write_text(out)
    meta = ddir / "dive.metadata.json"
    if not meta.exists():  # push adds the Dive id here after the first push
        meta.write_text(json.dumps({"title": f"{app['brand']} · {app.get('tagline', 'briefing')}", "description": app.get("description") or f"Generative briefing and question-to-page Dive for {app['brand']}."}, indent=2) + "\n")
    return len(out)


def main():
    global kit
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recipe", help="recipe directory with domain.py and app.json")
    ap.add_argument("--kit", help="motherduck-dive-components artifacts/ directory")
    a = ap.parse_args()
    kit = load_kit(find_kit(a.kit))
    recipe = pathlib.Path(a.recipe)
    if not recipe.is_absolute():
        recipe = recipe.resolve() if recipe.exists() else (ROOT / recipe).resolve()
    app = json.loads((recipe / "app.json").read_text())
    f = build_flight(recipe, app)
    sizes = [f"{name}/index.tsx {build_dive(recipe, app, b, cfg, name):,} chars" for b, cfg, name in board_dirs(app)]
    print(f"{recipe.name}: flight/main.py {f:,} chars, " + ", ".join(sizes))
    if not app.get("flightId"):
        print("  note: app.json has no flightId yet. Push the Flight, put its id in app.json, rebuild.")


if __name__ == "__main__":
    main()
