"""Flight 1: statsbomb-raw-load.

Downloads the statsbomb/open-data tarball, extracts the data/ subtree, and
loads it AS-IS into raw.* tables: one row per top-level source-array element,
contents untouched (a single JSON `record` column). The only derived column is
match_id: from the filename for per-match files, from the record itself for
matches. All typing, unnesting, and interpretation happens downstream in core
(flight 2). Stages into a local DuckDB, then copies raw.* to the MotherDuck
database `statsbomb`.

Runtime config (env vars):
  MATCH_LIMIT       optional int, cap number of matches loaded (smoke tests)
  COMPETITION_IDS   optional comma-separated competition_id filter
  SB_TARGET         optional duckdb path override (local runs/tests); default md:
  DATA_DIR          optional pre-extracted open-data 'data' dir (skips download)
"""
from __future__ import annotations

import os
import re
import shutil
import tarfile
import urllib.request
from pathlib import Path

import duckdb

TARBALL = "https://github.com/statsbomb/open-data/archive/refs/heads/master.tar.gz"
TMP = Path("/tmp")

RAW_TABLES = ("competitions", "matches", "lineups", "events", "three_sixty")

# Explicit schemas: every raw table is the source file's own top-level array
# mapped to rows, one JSON record per row, plus the match_id provenance key.
RAW_SCHEMAS = {
    "competitions": "CREATE OR REPLACE TABLE raw.competitions (record JSON)",
    "matches": "CREATE OR REPLACE TABLE raw.matches (match_id BIGINT, record JSON)",
    "events": "CREATE OR REPLACE TABLE raw.events (match_id BIGINT, record JSON)",
    "lineups": "CREATE OR REPLACE TABLE raw.lineups (match_id BIGINT, record JSON)",
    "three_sixty": "CREATE OR REPLACE TABLE raw.three_sixty (match_id BIGINT, record JSON)",
}

# columns={'json': 'JSON'} keeps records verbatim: letting read_json infer a
# STRUCT across heterogeneous files is lossy (missing keys become NULLs and
# rare keys inflate the schema).
_READ_JSON = (
    "read_json(__FILES__, format='array', records=false, "
    "columns={'json': 'JSON'}, filename=true, maximum_object_size=104857600)"
)

# Per-match files (events/lineups/three-sixty): match_id lives only in the
# filename, so this regex is the single place it is derived.
INSERT_PER_MATCH = f"""
INSERT INTO raw.__TABLE__
SELECT regexp_extract(filename, '(\\d+)\\.json$', 1)::BIGINT AS match_id,
       json AS record
FROM {_READ_JSON}
"""

# Match files are per competition/season; match_id comes from the record.
INSERT_MATCHES = f"""
INSERT INTO raw.matches
SELECT (json->>'$.match_id')::BIGINT AS match_id, json AS record
FROM {_READ_JSON}
"""


def create_raw_tables(con: duckdb.DuckDBPyConnection) -> None:
    """The only DDL site for the raw layer."""
    con.execute("CREATE SCHEMA IF NOT EXISTS raw")
    for ddl in RAW_SCHEMAS.values():
        con.execute(ddl)


_MALFORMED = re.compile(r'Malformed JSON in file "([^"]+)"')


def _load_json_files(
    con: duckdb.DuckDBPyConnection, sql: str, files: list[str], max_skips: int = 5
) -> None:
    """Run a read_json load (sql contains __FILES__), dropping files DuckDB
    reports as malformed and retrying.

    The open-data repo occasionally ships a corrupt file: three-sixty/
    3845506.json has a 16KiB run of NUL bytes mid-array (verified identical
    on GitHub master), and one bad file must not sink the whole load.
    """
    files = list(files)
    skipped = 0
    while True:
        try:
            con.execute(sql.replace("__FILES__", str(files)))
            return
        except duckdb.InvalidInputException as e:
            m = _MALFORMED.search(str(e))
            if not m or m.group(1) not in files or skipped >= max_skips:
                raise
            files.remove(m.group(1))
            skipped += 1
            print(f"WARNING: skipping malformed file {m.group(1)}", flush=True)


def run(con: duckdb.DuckDBPyConnection, data_dir: Path) -> None:
    match_limit = os.environ.get("MATCH_LIMIT")
    comp_ids = os.environ.get("COMPETITION_IDS")

    create_raw_tables(con)

    _load_json_files(
        con, "INSERT INTO raw.competitions SELECT json AS record FROM " + _READ_JSON,
        [str(data_dir / "competitions.json")],
    )

    # MATCH_LIMIT / COMPETITION_IDS are scope selection (which matches enter
    # the warehouse at all), not content transformation.
    comp_filter = ""
    if comp_ids:
        wanted = ",".join(str(int(c)) for c in comp_ids.split(","))
        comp_filter = f"WHERE (json->>'$.competition.competition_id')::INT IN ({wanted})"
    limit = f"LIMIT {int(match_limit)}" if match_limit is not None else ""
    _load_json_files(
        con, f"{INSERT_MATCHES} {comp_filter} ORDER BY 1 {limit}",
        [str(p) for p in sorted((data_dir / "matches").glob("*/*.json"))],
    )

    ids = [r[0] for r in con.sql("SELECT match_id FROM raw.matches ORDER BY match_id").fetchall()]
    for table, sub in (("events", "events"), ("lineups", "lineups"), ("three_sixty", "three-sixty")):
        files = [str(p) for m in ids if (p := data_dir / sub / f"{m}.json").exists()]
        if files:
            _load_json_files(con, INSERT_PER_MATCH.replace("__TABLE__", table), files)

    for t in RAW_TABLES:
        n = con.sql(f"SELECT count(*) FROM raw.{t}").fetchone()[0]
        print(f"raw.{t}: {n} rows", flush=True)


def download_data(tmp: Path = TMP) -> Path:
    """Fetch the open-data tarball (cached) and stream-extract the data/
    subtree; return the extracted data dir."""
    tgz = tmp / "open-data.tar.gz"
    if not tgz.exists():
        print(f"downloading {TARBALL} ...", flush=True)
        part = tgz.with_suffix(".partial")
        urllib.request.urlretrieve(TARBALL, part)  # noqa: S310 (trusted host)
        part.rename(tgz)

    extract_root = tmp / "open-data"
    data_dir = extract_root / "open-data-master" / "data"
    marker = extract_root / ".extract_complete"
    if not marker.exists():
        if extract_root.exists():
            shutil.rmtree(extract_root)
        print("extracting (streaming) ...", flush=True)
        n = 0
        with open(tgz, "rb") as raw_fh:
            with tarfile.open(fileobj=raw_fh, mode="r|gz") as tar:
                for member in tar:
                    if not member.isfile() or not member.name.startswith(
                        "open-data-master/data/"
                    ):
                        continue
                    dest = extract_root / member.name
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    src = tar.extractfile(member)
                    with open(dest, "wb") as f:
                        while chunk := src.read(1 << 20):
                            f.write(chunk)
                        f.flush()
                        # page cache is charged to the flight cgroup; keep
                        # memory.current flat during the ~15GB extraction
                        os.fsync(f.fileno())
                        os.posix_fadvise(f.fileno(), 0, 0, os.POSIX_FADV_DONTNEED)
                    n += 1
                    if n % 2000 == 0:
                        os.posix_fadvise(raw_fh.fileno(), 0, raw_fh.tell(), os.POSIX_FADV_DONTNEED)
                        print(f"  extracted {n} files", flush=True)
        marker.touch()
        print(f"extracted {n} files", flush=True)
    return data_dir


def main() -> None:
    target = os.environ.get("SB_TARGET", "md:")
    data_dir_env = os.environ.get("DATA_DIR")
    data_dir = Path(data_dir_env) if data_dir_env else download_data(TMP)

    if target != "md:":
        con = duckdb.connect(target)
        run(con, data_dir)
        con.close()
        print("done")
        return

    # Stage locally, then copy raw.* to MotherDuck in one transaction
    # (per-row INSERTs over the wire are far slower than a bulk table copy).
    stage_path = "/tmp/stage.db"
    Path(stage_path).unlink(missing_ok=True)
    local = duckdb.connect(stage_path)
    # Flight DuckDB defaults are cgroup-aware (memory_limit 12.7GiB, threads 2;
    # verified empirically), so no explicit memory_limit.
    # Skipping insertion-order preservation keeps the bulk JSON load lean.
    local.execute("SET preserve_insertion_order = false")
    run(local, data_dir)
    local.close()

    md = duckdb.connect("md:")
    md.execute("CREATE DATABASE IF NOT EXISTS statsbomb")
    md.execute("USE statsbomb")
    md.execute(f"ATTACH '{stage_path}' AS stage (READ_ONLY)")
    md.execute("CREATE SCHEMA IF NOT EXISTS raw")
    md.execute("BEGIN")
    try:
        for t in RAW_TABLES:
            print(f"copying raw.{t} to MotherDuck ...", flush=True)
            md.execute(f"CREATE OR REPLACE TABLE raw.{t} AS FROM stage.raw.{t}")
        md.execute("COMMIT")
    except Exception:
        md.execute("ROLLBACK")
        raise
    finally:
        md.close()
    print("done")


if __name__ == "__main__":
    main()
