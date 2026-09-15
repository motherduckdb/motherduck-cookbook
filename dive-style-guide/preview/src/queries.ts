/**
 * The five style variants all read the same three queries, so any difference in
 * the screenshots comes from the Guide and not from the data.
 *
 * The `-- @fixture` tag is what the local shim keys on. In a saved Dive it is
 * an ordinary SQL comment and the query runs against the `sample_data` share.
 */

export const SUMMARY_SQL = `
  -- @fixture summary
  SELECT
    count(*) AS trips,
    round(sum(total_amount)) AS revenue,
    round(avg(total_amount), 2) AS avg_fare,
    round(100.0 * sum(tip_amount) / sum(fare_amount), 1) AS tip_rate,
    round(avg(trip_distance), 2) AS avg_miles
  FROM "sample_data"."nyc"."taxi"
  WHERE tpep_pickup_datetime >= '2022-11-01'
    AND tpep_pickup_datetime < '2022-12-01'
`;

export const DAILY_SQL = `
  -- @fixture daily
  SELECT
    strftime(tpep_pickup_datetime, '%Y-%m-%d') AS day,
    count(*) AS trips,
    round(sum(total_amount)) AS revenue
  FROM "sample_data"."nyc"."taxi"
  WHERE tpep_pickup_datetime >= '2022-11-01'
    AND tpep_pickup_datetime < '2022-12-01'
  GROUP BY 1
  ORDER BY 1
`;

export const PAYMENTS_SQL = `
  -- @fixture payments
  SELECT
    CASE payment_type
      WHEN 1 THEN 'Credit card'
      WHEN 2 THEN 'Cash'
      WHEN 3 THEN 'No charge'
      WHEN 4 THEN 'Dispute'
      ELSE 'Unknown'
    END AS payment_method,
    count(*) AS trips,
    round(sum(total_amount)) AS revenue,
    round(100.0 * sum(total_amount) / sum(sum(total_amount)) OVER (), 1) AS revenue_share,
    round(avg(total_amount), 2) AS avg_fare,
    round(100.0 * sum(tip_amount) / sum(fare_amount), 1) AS tip_rate
  FROM "sample_data"."nyc"."taxi"
  WHERE tpep_pickup_datetime >= '2022-11-01'
    AND tpep_pickup_datetime < '2022-12-01'
  GROUP BY 1
  ORDER BY revenue DESC
`;
