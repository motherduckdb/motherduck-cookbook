---
title: Transform Stock Data with SQLMesh on MotherDuck
id: sqlmesh-demo
description: >-
  Loads Yahoo Finance stock data into MotherDuck with dlt, then transforms it
  through interim, conformed, and mart layers with SQLMesh (incremental,
  SCD type 2, audits, virtual data environments). Use when you want a SQLMesh
  project on MotherDuck, or a dlt-to-SQLMesh ELT pipeline to adapt.
type: example
features: []
tags: [sqlmesh, dlt]
---

# Transform Stock Data with SQLMesh on MotherDuck

This example loads daily stock prices, company info, and option chains from Yahoo Finance into a MotherDuck database with [dlt](https://dlthub.com/), then transforms the raw `dlt` tables into analytics models with [SQLMesh](https://sqlmesh.readthedocs.io/en/stable/). It is a re-implementation of the `matsonj/stocks` dbt demo on SQLMesh, and it shows the MotherDuck pattern of pointing a SQLMesh `motherduck` gateway at a cloud database and using SQLMesh model kinds (incremental by time range, SCD type 2, full, view) plus column-level audits to build a layered warehouse, all running against MotherDuck compute.

The data flows in three stages: `dlt` writes raw tables into the `stock_data` schema, SQLMesh declares those raw tables as external models, then SQLMesh builds `interim` (typed and cleaned), `conformed` (business-ready), and `mart` (joined analytics) layers on top.

## How it works

### dlt load

`load/stock_data_pipeline.py` runs three dlt resources, each with `write_disposition="replace"`:

- `stock_info_resource` (`stock_info`, primary key `Symbol`): per-ticker company info from `yfinance`. dlt also splits the nested `companyOfficers` list into a child table, `stock_info__company_officers`.
- `stock_options_resource` (`stock_options`, composite key on symbol, expiration, strike, type, contract symbol): the full call and put option chain for every expiration date.
- `stock_history_resource` (`stock_history`, key `Symbol` + `Date`): daily OHLCV history for the trailing 360 days.

Symbols are read from `symbols.txt` and validated before fetch:

```python
def validate_symbol(symbol: str) -> bool:
    """Validates a symbol using yfinance."""
    try:
        stock = yf.Ticker(symbol)
        return not stock.history(period="1d").empty
    except Exception as e:
        print(f"Error validating symbol {symbol}: {e}")
        return False
```

The history loader coerces each OHLCV value defensively because `yfinance` may return a scalar or a one-element `pd.Series` depending on the call:

```python
"Close": float(row["Close"].iloc[0])
    if isinstance(row["Close"], pd.Series)
    else float(row["Close"]),
```

### SQLMesh transform

- `transform/external_models.yaml`: the contract for the upstream dlt tables. It declares `stock_history`, `stock_info`, `stock_info__company_officers`, `stock_options`, and `_dlt_loads` with their column types so SQLMesh knows the raw schema without managing it. Regenerate it with `sqlmesh create_external_models` whenever the raw schema changes.
- `transform/models/interim/`: typed, cleaned models over the raw dlt tables. `stock_history` is `INCREMENTAL_BY_TIME_RANGE` on `trade_date`; `stock_info`, `stock_options`, and `stock_info__company_officers` are `SCD_TYPE_2_BY_TIME` so they track changes to company info and option chains over time. The SCD models derive their `updated_at` column from the dlt load id:

  ```sql
  TO_TIMESTAMP(_dlt_load_id::DOUBLE) AS _dlt_load_time
  ```

  The incremental history model filters on the SQLMesh time macros so each run only processes its slice:

  ```sql
  FROM stock_data.stock_history
  WHERE trade_date BETWEEN @start_ts AND @end_ts
  ```

- `transform/models/conformed/`: business-ready models. `price_history` is a `VIEW` over interim history; `company_info` is a `FULL` model that keeps only the current SCD row (`WHERE valid_to IS NULL`).
- `transform/models/mart/stock_price_by_day.sql`: a `VIEW` that joins shares outstanding to daily close for a market-cap time series:

  ```sql
  SELECT
    c.symbol AS stock_symbol,
    c.shares_outstanding,
    sp.close,
    sp.trade_date,
    ROUND(c.shares_outstanding::REAL * sp.close::REAL, 0) AS market_cap
  FROM conformed.company_info AS c
  LEFT JOIN conformed.price_history AS sp
    ON c.symbol = sp.symbol
  ```

- Audits run at execution time: `UNIQUE_COMBINATION_OF_COLUMNS`, `NOT_NULL`, and `UNIQUE_VALUES`. A failed audit blocks the model from being promoted, which is how data quality is enforced rather than just reported.

## Questions to answer

- Which tickers or source dataset should the pipeline load (replace `symbols.txt`, or swap dlt for a different source)?
- What MotherDuck database and schema are the target? Set both the dlt `destination` database and `transform/config.yaml` `database`, and keep them in sync.
- Full refresh or incremental, and what is the backfill start date for time-based models?
- How often should models run (the `@daily` cron, or a different cadence)?
- Where will the MotherDuck token come from (the `MOTHERDUCK_TOKEN` env var and `.dlt/secrets.toml`)?

## Caveats

- **Keep the two database names in sync.** SQLMesh reads the raw tables from the same MotherDuck database dlt wrote to. If `transform/config.yaml` `database` does not match the dlt destination database (`dlt_test_db` by default), `sqlmesh plan` will fail to resolve `stock_data.*`.
- **Token must be in the right place for the right tool.** dlt reads the token from `.dlt/secrets.toml`; SQLMesh reads it from `MOTHERDUCK_TOKEN`. Setting only one will make the other step fail. Do not commit the token; keep it in `.dlt/secrets.toml` (gitignored) and your shell env, not in `config.yaml`.
- **Regenerate external models after schema changes.** `external_models.yaml` is a static snapshot of the dlt output columns. `yfinance` periodically adds or renames `stock_info` fields, so a new load can drift from the declared schema. Re-run `sqlmesh create_external_models` after loads that change the raw shape, or SQLMesh will reference columns that no longer match.
- **`write_disposition="replace"` is a full reload.** Every `dlt` run truncates and reloads the raw tables. The SCD type 2 history in SQLMesh comes from the `_dlt_load_time` snapshots, not from dlt itself, so you only capture change history if you load on a schedule (each load is one snapshot in time).
- **`yfinance` is unofficial and rate-limited.** Symbols are validated with a 1-day history probe before fetching, and resources swallow per-symbol exceptions and print to stdout rather than failing the run. A symbol that returns no data is skipped silently, so check the load output if a ticker is missing downstream. Large symbol lists, especially option chains, can be slow and may hit Yahoo throttling.
- **History defaults to a 360-day window.** `stock_history_resource` only pulls the trailing 360 days, while the incremental model's `start` is `'2023-01-01'`. Backfilling earlier than what dlt loaded produces empty slices, not older data; widen the dlt window first.
- **The interim layer is hand-maintained typed SQL.** Each interim model casts every column explicitly (for example `close::DOUBLE`, `symbol::TEXT`). If you add tickers with new `stock_info` fields you want downstream, you must add the casts to the interim model yourself; the raw-to-interim mapping is not automatic.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `load/symbols.txt` | The tickers the dlt pipeline fetches, one per line | `MSFT`, `AAPL`, `NVDA`, ... swap for your own list (ships with 11 large-cap symbols) |
| `dataset_name` in `load/stock_data_pipeline.py` | Schema the raw dlt tables land in | `"stock_data"` (referenced by SQLMesh models as `stock_data.<table>`) |
| `destination` in `load/stock_data_pipeline.py` | Where dlt writes the raw data | `"motherduck"` (configured via `.dlt/secrets.toml`) |
| `start_date` window in `stock_history_resource` | How far back history is pulled | `datetime.now() - timedelta(days=360)` |
| `gateways.local.connection.database` in `transform/config.yaml` | MotherDuck database SQLMesh reads and writes | `dlt_test_db` (must match the dlt destination database) |
| `model_defaults.start` in `transform/config.yaml` | Default backfill start for time-based models | `2024-12-08` |
| `start` in `transform/models/interim/stock_history.sql` | Backfill start for the incremental price model | `'2023-01-01'` |
| Model `kind` per model | Materialization strategy | `INCREMENTAL_BY_TIME_RANGE` (history), `SCD_TYPE_2_BY_TIME` (info/options/officers), `FULL`, `VIEW` |
| `cron` per model | How often SQLMesh refreshes the model | `'@daily'` on the interim, conformed, and full models |
| `audits (...)` per model | Data quality checks enforced at run time | `UNIQUE_COMBINATION_OF_COLUMNS`, `NOT_NULL`, `UNIQUE_VALUES` |
| `transform/external_models.yaml` | Declares the raw dlt tables (and their columns) as external sources | regenerate with `sqlmesh create_external_models` after a load |

## Run it

Prerequisites: a [MotherDuck account](https://app.motherduck.com/) and a service token, plus [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
# from sqlmesh-demo/
uv sync                       # create the venv and install dlt + sqlmesh + yfinance

# point dlt at MotherDuck (token goes in .dlt/secrets.toml; see dlt docs link below)
# then load the raw stock data into MotherDuck:
uv run python load/stock_data_pipeline.py

# transform with SQLMesh (run from the transform/ dir, or pass -p transform)
export MOTHERDUCK_TOKEN=<your-token>
uv run sqlmesh -p transform info     # verify the connection and project state
uv run sqlmesh -p transform plan     # preview and apply; type 'y' to push the changes

# optional: open the SQLMesh web UI (the [web] extra is installed)
uv run sqlmesh -p transform ui
```

If SQLMesh cannot find your token during `info`/`plan`, make sure `MOTHERDUCK_TOKEN` is exported in the same shell (the web UI also picks it up).

### Connection details

- `dlt` writes via the `motherduck` destination. The database name and token go in `.dlt/secrets.toml` and are documented in the [dlt MotherDuck destination guide](https://dlthub.com/docs/dlt-ecosystem/destinations/motherduck#setup-guide). The default `pipeline_name` and `dataset_name` are both `stock_data`.
- SQLMesh connects with a `motherduck` gateway in `transform/config.yaml` (`type: motherduck`, `database: dlt_test_db`), authenticated by `MOTHERDUCK_TOKEN`. The dlt destination database and the SQLMesh `database` must point at the same MotherDuck database, or SQLMesh will not find the raw `stock_data` tables.

## Files

- [`load/stock_data_pipeline.py`](load/stock_data_pipeline.py) - the dlt load: fetches info, options, and history from yfinance for each ticker and writes raw tables into the `stock_data` schema on MotherDuck.
- [`load/symbols.txt`](load/symbols.txt) - the ticker list, one symbol per line (ships with 11 large-cap names: MSFT, AAPL, NVDA, ...). Edit to load your own.
- [`transform/config.yaml`](transform/config.yaml) - the SQLMesh project config: defines the `motherduck` gateway, target database (`dlt_test_db`), DuckDB dialect, and default backfill start.
- [`transform/external_models.yaml`](transform/external_models.yaml) - declares the raw dlt tables and their columns as external sources so SQLMesh knows the upstream schema. Regenerate with `sqlmesh create_external_models`.
- [`transform/models/`](transform/models/) - the SQLMesh models in three layers: `interim/` (typed and cleaned, incremental and SCD type 2 over the raw dlt tables), `conformed/` (business-ready view and full models), and `mart/` (the joined market-cap analytics view).
- [`transform/`](transform/) - the SQLMesh root, also holding empty scaffold dirs (`audits/`, `macros/`, `seeds/`, `tests/`) for project growth.
- [`pyproject.toml`](pyproject.toml) - the uv project definition: pins dlt, duckdb, sqlmesh (with the web UI extra), and yfinance.
- [`uv.lock`](uv.lock) - the pinned dependency lockfile for reproducible `uv sync`.

## Learn more

- SQLMesh concepts used here (virtual data environments, model kinds, audits, cron): see the [SQLMesh docs](https://sqlmesh.readthedocs.io/en/stable/).
- dlt MotherDuck destination setup (the `.dlt/secrets.toml` database and token): the [dlt MotherDuck destination guide](https://dlthub.com/docs/dlt-ecosystem/destinations/motherduck#setup-guide).
- Deeper MotherDuck or DuckDB SQL questions: run the `ask_docs_question` MCP tool or check the [MotherDuck docs](https://motherduck.com/docs/).
