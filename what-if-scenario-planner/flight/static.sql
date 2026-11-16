-- Static (read-only) planning data for the Supply Chain Scenario Modeling Dive.
--
-- Synthetic apparel retailer: 4 regions x 6 categories. A demand model with
-- seasonality (winter, holiday peak, back-to-school, January) gives a 26-week
-- baseline forecast (2026-11-09 .. 2027-05-03) that spans the holiday peak, and
-- a supply plan of flat weekly receipts at 97% of average demand plus one week
-- of cover on hand: slow autumn weeks build stock, the December peak draws it
-- down, and any demand upside runs into the supply constraint.
--
-- setup.py runs this in the database's `main` schema. Every statement is
-- CREATE OR REPLACE, so it is safe to re-run; it never touches the writeback
-- tables in `supply`.
--
-- Calendar: the demand model below runs on its original calendar (plan start
-- 2026-09-28); every date written to a table is then moved 42 days (6 weeks)
-- later, so the plan starts on 2026-11-09 with the same numbers.

SELECT setseed(0.42);

CREATE OR REPLACE TABLE categories AS
SELECT * FROM (VALUES
  --  category      price  cost  elasticity base  winter holiday bts   jan  skus order
  ('Outerwear',    129.0, 58.0, -1.6,       900,  0.55,  0.35,  0.00, 0.00, 25, 1),
  ('Knitwear',      64.0, 26.0, -1.3,      1100,  0.35,  0.45,  0.00, 0.00, 30, 2),
  ('Denim',         78.0, 31.0, -1.1,      1500,  0.00,  0.25,  0.35, 0.00, 35, 3),
  ('Footwear',      96.0, 44.0, -1.2,      1300,  0.10,  0.40,  0.20, 0.00, 35, 4),
  ('Activewear',    52.0, 20.0, -1.5,      1400,  0.00,  0.20,  0.00, 0.60, 35, 5),
  ('Accessories',   28.0,  9.0, -0.9,      2000,  0.00,  0.90,  0.00, 0.00, 40, 6)
) t(category, unit_price, unit_cost, price_elasticity, base_units,
    winter_amp, holiday_amp, bts_amp, jan_amp, sku_count, sort_order);

CREATE OR REPLACE TABLE regions AS
SELECT * FROM (VALUES
  ('Northeast', 1.15, 1.30, 28, 1),
  ('Southeast', 0.90, 0.40, 22, 2),
  ('Central',   0.95, 1.10, 23, 3),
  ('West',      1.10, 0.70, 27, 4)
) t(region, volume_mult, winter_sensitivity, store_count, sort_order);

CREATE OR REPLACE TEMP TABLE _weeks AS
SELECT
  w::DATE AS week,
  dayofyear(w::DATE + 3) AS doy,
  date_diff('day', DATE '2024-09-30', w::DATE) / 365.25 AS years_in
FROM generate_series(TIMESTAMP '2025-05-05', TIMESTAMP '2027-03-22', INTERVAL 7 DAY) g(w);

-- The demand model: expected weekly units per region x category.
CREATE OR REPLACE TEMP TABLE _model AS
SELECT
  w.week,
  r.region,
  c.category,
  c.base_units * r.volume_mult
    * (1 + 0.06 * w.years_in)
    * (1
       + c.winter_amp * r.winter_sensitivity * cos(2 * pi() * (w.doy - 15) / 365.25)
       + c.holiday_amp * exp(-power(least(abs(w.doy - 352), 365 - abs(w.doy - 352)), 2) / (2 * 10 * 10))
       + c.bts_amp * exp(-power(w.doy - 225, 2) / (2 * 14 * 14))
       + c.jan_amp * exp(-power(least(abs(w.doy - 8), 365 - abs(w.doy - 8)), 2) / (2 * 12 * 12)))
    -- Black Friday week (the Monday before Thanksgiving) and a July summer sale
    * CASE WHEN w.week IN (DATE '2025-11-24', DATE '2026-11-23') THEN 1 + 0.6 * c.holiday_amp
           WHEN w.week = DATE '2025-07-07' THEN 1.25
           ELSE 1 END
    AS expected_units
FROM _weeks w CROSS JOIN regions r CROSS JOIN categories c;

CREATE OR REPLACE TABLE baseline_forecast AS
SELECT
  m.week + 42 AS week,
  (date_diff('day', DATE '2026-09-28', m.week) // 7)::INTEGER AS week_idx,
  region, category,
  round(expected_units)::INTEGER AS units
FROM _model m
WHERE m.week >= DATE '2026-09-28'
ORDER BY week, region, category;

-- Supply: flat weekly receipts at 97% of the 26-week average demand, plus one
-- week of cover on hand. Slow autumn weeks build stock and the December peak
-- draws it down, so the baseline loses ~1% of demand at peak and any growth
-- scenario runs into the supply constraint.
CREATE OR REPLACE TABLE supply_plan AS
WITH avg_demand AS (
  SELECT region, category, avg(units) AS avg_units
  FROM baseline_forecast GROUP BY ALL
)
SELECT region, category,
       round(avg_units * 0.97)::INTEGER AS weekly_receipts,
       round(avg_units)::INTEGER AS on_hand_start
FROM avg_demand
ORDER BY region, category;

DROP TABLE _model;
DROP TABLE _weeks;
