MODEL (
  name interim.stock_history,
  kind INCREMENTAL_BY_TIME_RANGE (
    time_column trade_date
  ),
  start '2023-01-01',
  audits (
    UNIQUE_COMBINATION_OF_COLUMNS(columns := (symbol, trade_date)),
    NOT_NULL(columns = (
      symbol
    ))
  ),
  cron '@daily'
);

SELECT
  date::DATE AS trade_date,
  open::DOUBLE AS open,
  high::DOUBLE AS high,
  low::DOUBLE AS low,
  close::DOUBLE AS close,
  volume::BIGINT AS volume,
  symbol::TEXT AS symbol
FROM stock_data.stock_history
WHERE
  trade_date BETWEEN @start_ts AND @end_ts