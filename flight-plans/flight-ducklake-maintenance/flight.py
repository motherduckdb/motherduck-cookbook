"""DuckLake maintenance flight.

This runs the operations that make up a checkpoint, individually, in checkpoint order.

Config (env vars):
  DUCKLAKE_DATABASE         (required) DuckLake database to maintain
  EXPIRE_OLDER_THAN         snapshot retention interval (default '7 days')
  CLEANUP_OLDER_THAN        retention for files pending deletion (default '7 days')
  ORPHAN_OLDER_THAN         retention for unreferenced data files (default '7 days')
  TARGET_FILE_SIZE          e.g. '512MB'; persisted catalog option, controls merge_adjacent_files
  REWRITE_DELETE_THRESHOLD  e.g. '0.5'; persisted catalog option, controls rewrite_data_files
  DRY_RUN                   'true' = only report what expire/cleanup/orphan would remove
"""

import logging
import os

import duckdb
import pytz  # lets the duckdb client return ducklake_expire_snapshots' TIMESTAMPTZ columns

log = logging.getLogger("ducklake_maintenance")


def run(con, name, sql, params):
    rows = con.execute(sql, params).fetchall()
    log.info("%s: %d row(s)", name, len(rows))
    for row in rows:
        log.info("  %s", row)


def main():
    db = os.environ["DUCKLAKE_DATABASE"]
    expire = os.environ.get("EXPIRE_OLDER_THAN", "7 days")
    cleanup = os.environ.get("CLEANUP_OLDER_THAN", "7 days")
    orphan = os.environ.get("ORPHAN_OLDER_THAN", "7 days")
    dry_run = os.environ.get("DRY_RUN", "false").lower() == "true"

    con = duckdb.connect("md:")
    # The runtime may have no system timezone (DuckDB reports 'Etc/Unknown'); pin it
    # so pytz can localize expire_snapshots' TIMESTAMP WITH TIME ZONE columns.
    con.execute("SET TimeZone = 'UTC'")

    # set_option persists on the catalog; the db name must be an identifier, not a `?`.
    for option, value in [
        ("target_file_size", os.environ.get("TARGET_FILE_SIZE")),
        ("rewrite_delete_threshold", os.environ.get("REWRITE_DELETE_THRESHOLD")),
    ]:
        if value:
            con.execute(f"CALL \"{db}\".set_option('{option}', ?)", [value])
            log.info("set_option %s = %s", option, value)

    run(con, "flush_inlined_data",
        "CALL ducklake_flush_inlined_data(?)", [db])
    run(con, "expire_snapshots",
        "CALL ducklake_expire_snapshots(?, older_than => now() - ?::INTERVAL, dry_run => ?)",
        [db, expire, dry_run])
    run(con, "merge_adjacent_files",
        "CALL ducklake_merge_adjacent_files(?)", [db])
    run(con, "rewrite_data_files",
        "CALL ducklake_rewrite_data_files(?)", [db])
    run(con, "cleanup_old_files",
        "CALL ducklake_cleanup_old_files(?, older_than => now() - ?::INTERVAL, dry_run => ?)",
        [db, cleanup, dry_run])
    run(con, "delete_orphaned_files",
        "CALL ducklake_delete_orphaned_files(?, older_than => now() - ?::INTERVAL, dry_run => ?)",
        [db, orphan, dry_run])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    main()
