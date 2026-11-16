"""supply-planner-write: the Flight the Dive runs once per save.

The request arrives in the Flight's config (environment variables):

  OP          the op: create_scenario, update_scenario, set_overrides, set_status,
              push_to_erp, delete_scenario (planner.Api.op_*)
  ARGS        the op's arguments as JSON
  ACTOR       who made the change (the Dive passes the viewer's MotherDuck user name)
  REQUEST_ID  a random id the Dive picks; the reply is stored under it

The reply, or the ApiError, goes to <PLANNER_DB>.supply.rpc_results; the Dive
reads it back through its share after the run, so CREATE SNAPSHOT closes every
run. build_flight.py inlines supply_engine.py and planner.py above this code.
"""
import json
import os
import re
import threading
import time
import traceback

from planner import Api, ApiError, Store, _clean_text, ensure_rpc_results, now_utc, SCHEMA

DB = os.environ.get("PLANNER_DB") or "scenario_planner"


class FlightStore(Store):
    """Store without setup: the database and tables already exist (setup.py),
    and a Flight should attach only the one database."""

    def __init__(self, db: str):
        import duckdb

        self.db = db
        self.lock = threading.RLock()
        self.con = duckdb.connect(f"md:{db}?attach_mode=single")
        self._init_session()


def main():
    t0 = time.time()
    request_id = os.environ.get("REQUEST_ID", "")
    op = os.environ.get("OP", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", request_id):
        raise SystemExit(f"bad REQUEST_ID {request_id!r}")
    actor = _clean_text(os.environ.get("ACTOR") or "", 40) or "anonymous"
    store = FlightStore(DB)
    ensure_rpc_results(store)
    ok, status, error, result = True, 200, None, None
    try:
        args = json.loads(os.environ.get("ARGS") or "{}")
        store.load_base()
        result = json.dumps(Api(store).call(op, args, actor), default=str)
    except json.JSONDecodeError:
        ok, status, error = False, 400, "ARGS is not valid JSON"
    except ApiError as e:
        ok, status, error = False, e.status, e.message
    except Exception as e:  # noqa: BLE001
        traceback.print_exc()
        ok, status, error = False, 500, f"server error: {type(e).__name__}"
    store.q("INSERT INTO rpc_results VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [request_id, now_utc(), op, actor, ok, status, error, result])
    store.q("DELETE FROM rpc_results WHERE ts < now() - INTERVAL 1 DAY")
    # The Dive reads through an automatically updated share: publish now so
    # its REFRESH DATABASE sees this run's writes.
    store.con.execute(f'CREATE SNAPSHOT OF "{DB}"')
    print(f"{op} by {actor} in {SCHEMA}: {'ok' if ok else f'{status} {error}'} in {time.time() - t0:.1f}s", flush=True)


if __name__ == "__main__":
    main()
