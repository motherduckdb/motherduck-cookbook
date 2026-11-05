-- Set md_opt_marker on the SAME connection before executing this file:
-- SET VARIABLE md_opt_marker = 'md_opt_run:<run_id from run.json>';
-- This SELECT can be executed again later if query history has not caught up.
SELECT
    query_id,
    query_text,
    user_name,
    connection_id,
    duckdb_id,
    duckdb_version,
    duckling_id,
    instance_type,
    query_type,
    start_time,
    end_time,
    execution_seconds: epoch(execution_time),
    wait_seconds: epoch(wait_time),
    total_elapsed_seconds: epoch(total_elapsed_time),
    bytes_spilled_to_disk,
    bytes_uploaded,
    bytes_downloaded,
    error_type,
    error_message
FROM md_information_schema.query_history
WHERE contains(query_text, getvariable('md_opt_marker'))
    AND contains(query_text, 'md_opt_phase:measure')
    AND NOT contains(query_text, 'md_information_schema.query_history')
ORDER BY start_time, query_id;
