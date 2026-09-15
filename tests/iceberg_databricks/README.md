---
catalog: false
---

# Iceberg Flight integration tests

The default catalog CI runs the offline regressions. The optional local test
uses a real Iceberg REST catalog, MinIO object storage, and DuckDB 1.5.5. It
checks both templates' transformation and transaction code against actual
Iceberg metadata and data files. Each test creates and removes a unique namespace.

## Run locally

Create `tests/iceberg_databricks/.env` with two disposable, random development
credentials named `DEV_ACCESS_KEY` and `DEV_SECRET_KEY`. The file is ignored by
Git. Use a secret of at least eight characters. These are local fixture
credentials, not production AWS credentials.

From the repository root:

```sh
docker compose -p cookbook-pr157 -f tests/iceberg_databricks/compose.yaml up -d
uv run --with duckdb==1.5.5 --with pytest --with boto3 --with python-dotenv \
  python tests/iceberg_databricks/run_local.py
```

Wait for both containers to be ready before running the tests. Ports 18181 and
19000 bind only to localhost. Stop the fixture when finished:

```sh
docker compose -p cookbook-pr157 -f tests/iceberg_databricks/compose.yaml down
```

## Managed Flight validation

Local DuckDB tests do not prove that the managed runtime works. For that check,
make the disposable catalog and storage reachable from MotherDuck, configure
named Iceberg and S3 secrets, and create an Iceberg database using the REST
endpoint. Keep credentials in secrets. Do not expose a production catalog as
part of this fixture.

Use a unique staging database and output tables. Create both Flights without
schedules, with the exact template source and requirements. Check terminal run
status and logs, then independently read the Iceberg outputs with PyIceberg.
Exercise initial runs, repeats, an empty source, `PUBLISH_TO_ICEBERG=false`, and
a source containing a customer ID that cannot be cast to BIGINT. The last case
must fail while preserving the previous output. For stage-only mode, change the
input and verify that the MotherDuck table changes while the Iceberg snapshot
ID stays the same.

The review of PR #157 exercised this path using the local fixture and real
managed Flights. Databricks authentication, permissions, and credential vending
were not tested because no Databricks development workspace was available.


## Results recorded on 2026-09-15

- Both template entrypoints ran twice locally against MotherDuck compute and the
  local Iceberg REST/MinIO fixture. Eight synthetic input rows produced six exact
  groups, covering duplicates, null keys/timestamps, UTC boundaries, non-query
  events, and a customer ID of 4294967296.
- Independent PyIceberg reads confirmed the Iceberg output rows and counts.
- Nine managed Flight runs exercised the exact template source: initial and
  repeated direct/staged publication, staging only, invalid input for each
  template, and empty input for each template. Seven succeeded. The two invalid
  inputs failed as expected and preserved the previous Iceberg snapshots.
- Explicit publication errors after DELETE preserved the previous snapshot for
  both helpers. The original direct template was also tested on a disposable
  target: its INTEGER overflow erased the previous result, leaving zero rows.
- The local Docker integration suite passed all 18 cases. Default CI runs 16
  Iceberg regressions and skips the two cases that require the fixture.

This validates the transform and publication behavior. It does not validate
Databricks-specific authentication or credential vending, and it does not test
concurrent writers or sorted/partitioned target variations.
