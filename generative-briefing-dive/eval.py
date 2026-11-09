"""Score a recipe's planner (and optionally its narrator) on a fixed question set.

    python3 eval.py recipes/<name> [--narrate] [--model MODEL] [--only 1,4] [--baseline FILE] [--out FILE]

Reads recipes/<name>/questions.json:

    [{"q": "Which agencies take longest to close requests?",
      "expect": {"measures": ["requests.avg_days_to_close"], "dimensions": ["agency"], "types": ["table", "bar"]}}]

Each expect key lists acceptable answers; a plan scores 1 for a key when any block uses any
of them. "filters" lists dimensions a block should filter on. A question with
"off_topic": true passes when the run does not crash. The plan score is the share of keys hit,
times the share of planned blocks that compiled.

With --narrate it runs the full generation (probes, narrative, number check) and also scores
the share of numbers grounded in the data. Results print as a table, are written to --out as
JSON, and are stored in "<db>".main.evals so the Dive's Memory page can compare prompt versions
and models. Build the recipe first (python3 build.py recipes/<name>); this imports its
flight/main.py and uses MOTHERDUCK_TOKEN.
"""

import argparse
import datetime as dt
import importlib.util
import json
import pathlib
import time

ROOT = pathlib.Path(__file__).resolve().parent


def load_engine(recipe):
    path = recipe / "flight" / "main.py"
    if not path.exists():
        raise SystemExit(f"{path} is missing: run python3 build.py {recipe} first")
    spec = importlib.util.spec_from_file_location("flight_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def score_plan(blocks, plan, expect):
    used = {"measures": set(), "dimensions": set(), "types": set(), "filters": set()}
    for b in blocks:
        src = b.get("source") or {}
        used["measures"].update(src.get("measures") or [])
        used["types"].add("drivers" if b.get("drivers") else b.get("type"))
        for d in str(src.get("dimension") or "").replace(" → ", ",").replace(" within ", ",").split(","):
            used["dimensions"].add(d.strip())
        used["filters"].update(f["dimension"] for f in src.get("filters") or [])
    for pb in plan.get("blocks", []):
        if pb.get("series_dimension"):
            used["dimensions"].add(pb["series_dimension"])
    hits = {k: bool(set(v) & used[k]) for k, v in expect.items() if k in used and v}
    compiled = len(blocks) / max(1, len(plan.get("blocks", [])))
    score = (sum(hits.values()) / len(hits) if hits else 1.0) * min(1.0, compiled)
    return round(score, 3), hits, round(compiled, 2)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("recipe")
    ap.add_argument("--narrate", action="store_true", help="run the full generation and score grounded numbers")
    ap.add_argument("--model", help="prompt() model; default: the recipe's DEFAULT_MODEL")
    ap.add_argument("--only", help="comma-separated question numbers, 1-based")
    ap.add_argument("--baseline", help="an earlier --out file to compare against")
    ap.add_argument("--out", help="write the results here as JSON")
    a = ap.parse_args()

    recipe = pathlib.Path(a.recipe)
    recipe = recipe.resolve() if recipe.exists() else (ROOT / recipe).resolve()
    questions = json.loads((recipe / "questions.json").read_text())
    if a.only:
        keep = {int(i) for i in a.only.split(",")}
        questions = [q for i, q in enumerate(questions, 1) if i in keep]
    m = load_engine(recipe)
    model = a.model or m.DEFAULT_MODEL
    con = m.connect()
    m.build_marts(con, force=False, refresh_ok=False)
    con.execute(f'''CREATE TABLE IF NOT EXISTS "{m.DB}".main.evals (
        run_at TIMESTAMP, prompt_version VARCHAR, model VARCHAR, narrate BOOLEAN, question VARCHAR,
        plan_score DOUBLE, grounded DOUBLE, seconds DOUBLE, detail VARCHAR)''')
    as_of = m.resolve_as_of(con)
    results = []
    for i, item in enumerate(questions, 1):
        q, expect = item["q"], item.get("expect", {})
        run = m.Run(f"eval-{i}", model)
        t0 = time.time()
        row = {"q": q, "plan_score": None, "grounded": None, "error": None}
        try:
            if a.narrate:
                spec, plan, _, _, _, _ = m.generate(con, run, "ask", q, None, as_of)
                blocks = [b for b in spec["blocks"] if b["type"] != "callouts" and not b.get("probe")]
                checks = spec.get("checks") or {}
                row["grounded"] = round(checks["grounded"] / checks["checked"], 3) if checks.get("checked") else 1.0
                row["ungrounded"] = checks.get("ungrounded")
            else:
                memory = m.load_memory(con, as_of)
                guides_text, _ = m.read_guides(con)
                plan = m.make_plan(con, run, "ask", q, None, memory, guides_text, as_of)
                blocks, _ = m.compile_plan(m.Compiler(con, as_of), plan, "ask")
            if item.get("off_topic"):
                row["plan_score"] = 1.0
            else:
                row["plan_score"], row["hits"], row["compiled"] = score_plan(blocks, plan, expect)
            row["types"] = ["drivers" if b.get("drivers") else b["type"] for b in blocks]
        except Exception as e:
            row["error"] = str(e)[:300]
            row["plan_score"] = 0.0
        row["seconds"] = round(time.time() - t0, 1)
        results.append(row)
        con.execute(
            f'INSERT INTO "{m.DB}".main.evals VALUES (?::TIMESTAMP, ?, ?, ?, ?, ?, ?, ?, ?)',
            [dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"), m.PROMPT_VERSION, model, a.narrate, q,
             row["plan_score"], row["grounded"], row["seconds"], json.dumps({k: v for k, v in row.items() if k not in ("q",)}, default=str)],
        )
        flag = "" if row["plan_score"] == 1 else "  <-- " + (row["error"] or json.dumps(row.get("hits")))
        grounded = "" if row["grounded"] is None else f"{row['grounded']:.2f}  "
        print(f"{i:>2}. {row['plan_score']:.2f}  {grounded}{row['seconds']:>5}s  {q[:70]}{flag}")

    plan_avg = sum(r["plan_score"] for r in results) / len(results)
    grounded = [r["grounded"] for r in results if r["grounded"] is not None]
    summary = {"prompt_version": m.PROMPT_VERSION, "model": model, "narrate": a.narrate, "questions": len(results),
               "plan_score": round(plan_avg, 3), "grounded": round(sum(grounded) / len(grounded), 3) if grounded else None,
               "seconds": round(sum(r["seconds"] for r in results) / len(results), 1)}
    print(json.dumps(summary))
    if a.baseline:
        base = {r["q"]: r for r in json.loads(pathlib.Path(a.baseline).read_text())["results"]}
        for r in results:
            b = base.get(r["q"])
            if b and b["plan_score"] != r["plan_score"]:
                print(f"  changed {b['plan_score']:.2f} -> {r['plan_score']:.2f}: {r['q'][:70]}")
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps({"summary": summary, "results": results}, indent=2, default=str))


if __name__ == "__main__":
    main()
