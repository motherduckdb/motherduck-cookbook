# MotherDuck query benchmarking kit: instructions for the assistant

You're helping a MotherDuck customer benchmark their own queries with read scaling. By the end, the customer should
have:

- a serial baseline run;
- one or more concurrency runs;
- a **MotherDuck query benchmarking** Dive in their MotherDuck account that shows the results.

Read `README.md` for what the kit does. This file is the procedure to follow.

## Ground rules

- **Ask one question at a time.** Offer a sensible default the user can accept. Don't ask for anything you can work
  out by running a command.
- **Never ask for a token in the chat. Never print, `cat`, or echo the contents of `.env`.** Tokens live in `.env`,
  which the user edits themselves. To check whether a token is set, use only the presence check in step 2.
- **Confirm before anything that uses compute or changes their account.** That means every benchmark run and creating
  the Dive. State what will run: the queries CSV, the concurrency level, rounds, and session mode.
- **Don't give time or cost estimates before a run.** Report measured results after each run instead: E2E time,
  Ducklings used, and the price the script calculates.
- **Report numbers, not commentary.** After a run, give only the measured results: what ran and the numbers from the
  script's summary. Don't add interpretation, likely causes, or caveats, such as "no speedup yet", "connections shared
  Ducklings so some queries waited", or "the first queries were probably warming the cache". If the user asks what
  the results mean, answer that question then.
- **Run benchmarks in the background.** Write the output to a log in `runs/`, and report progress as it runs.
- **Don't edit `md_bench.py` or `dive/benchmark_dive.tsx`** unless the user asks you to. Don't commit anything.
- **Be plain about failures.** If a step fails, show the error line and the fix. Don't skip the step.
- Use `.venv/bin/python` for every command once the virtual environment exists.

## Step 1: Set up Python

```bash
python3 --version          # needs 3.10 or later
python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
```

If Python is older than 3.10 or missing, tell the user how to install a current version, then stop until it's
installed.

## Step 2: Tokens

1. If `.env` doesn't exist, run `cp .env.example .env`.
2. Ask the user to open `.env` in their editor and paste two tokens. Both come from **Settings > Access Tokens** in
   the MotherDuck UI:
   - `MOTHERDUCK_READ_SCALING_TOKEN`: a token of type **Read Scaling**.
   - `MOTHERDUCK_TOKEN`: a **Read/Write** token for an **organization Admin** in the same organization. Only Admins
     can read `md_information_schema.query_history`.
3. Check that both are set, without showing the values:

   ```bash
   .venv/bin/python -c "import md_bench as m; m.load_env_file(); import os; [print(k, 'set' if os.environ.get(k) else 'MISSING') for k in ('MOTHERDUCK_READ_SCALING_TOKEN','MOTHERDUCK_TOKEN')]"
   ```

## Step 3: Region and database

1. Find the organization's region with `SELECT region FROM md_user_info();` (through the MotherDuck MCP server if
   it's connected, or ask the user to run it in the MotherDuck UI). If neither works, ask which region they picked
   when they created the organization. MotherDuck runs in `us-east-1`, `us-west-2`, `eu-central-1`, `eu-west-1`,
   `ap-northeast-1`, and `ap-southeast-2`; see https://motherduck.com/docs/about-motherduck/cloud-regions/. Unless
   it's `us-east-1`, add `--region <region>` to every command from here on. The region sets the Postgres endpoint
   host, `pg.<region>-aws.motherduck.com`.
2. Run the setup check. `my_db` is a placeholder; any database they can read works for the check:

   ```bash
   .venv/bin/python md_bench.py --check --database my_db --region <region>
   ```

   Every line should show ✓. The check reads the region with `md_user_info()` and marks ✗ if `--region` doesn't
   match; rerun with the region it names. If anything else fails, see Troubleshooting. The output ends with the
   databases they can query.
3. Ask which database the benchmark queries run against. Table names without a database prefix resolve against it.
   Pass it as `--database` from here on.

## Step 4: Build the queries CSV

Ask how they want to start:

- **A. They have SQL already.** For example, the queries behind a dashboard, an app endpoint, or a BI tool. Help them
  write `queries.csv` with the columns `query_name` and `query_text`. Write the file with a CSV writer so that
  multi-line SQL is quoted correctly. Use short `snake_case` names that say what each query does.
- **B. Draft it from their query history.** Run:

  ```bash
  .venv/bin/python md_bench.py --suggest-queries 20 --since-days 7 --database <db> --out queries.csv
  ```

  Add `--rank total_time` to favor heavy queries over frequent ones, and `--user-name <name>` to limit the history to
  one user, such as the service account an app uses. Show them the list. Ask which queries to keep. Rename the
  `q01`-style names to meaningful ones.
- **C. Try the kit first.** Use `queries.example.csv`, which reads the `sample_data` share. Then come back to A or B.

Then ask: **Does it matter to them that each end user's or tenant's queries keep going to the same Duckling?** That's
user-to-Duckling affinity. It matters when their application sends a stable `session_name` per user or tenant, or
when they plan to. If yes:

- Add a third column, `session_name`, holding the identifier their application would send, such as a user, tenant, or
  customer ID. Rows that belong to the same user get the same value. Leave it empty for queries that don't belong to
  one user, such as an admin query across all tenants.
- When the queries already filter on that ID, for example `WHERE tenant_id = 17`, fill the column from the filter
  value. Show them the mapping before you write it.
- Values may contain only letters, digits, `-`, `_`, `.`, and `:`.
- Plan to run with `--session-mode csv`. A useful comparison is the same concurrency level with and without it.

If no, leave the column out and keep the default `--session-mode none`.

**Always confirm affinity when the CSV has a `session_name` column, even if the user supplied the CSV with the column
already filled in.** A filled-in column isn't a decision. Ask: "Your CSV has session_name values. Do you want each
session's queries kept on one Duckling (user-to-Duckling affinity), or should they be spread round robin?" Never add
`--session-mode csv` without a clear yes. Then pass the choice explicitly on every run, as `--session-mode csv` or
`--session-mode none`. The script refuses to run a CSV that has session names until one of the two is given.

Check the CSV before you go on, and fix problems with the user:

- Every query is read-only: `SELECT`, `WITH`, or `FROM`. Read scaling tokens reject writes.
- There are no parameter placeholders such as `$1` or `?`, no local file paths, and no AI functions such as `prompt()`
  or `embedding()`.
- Names are unique. Line breaks are real newlines, not the two characters `\n`. If they're the literal characters,
  any `--` comment comments out the rest of the query.
- The queries' tables exist in the chosen database. If the MotherDuck MCP server is connected to the same account,
  you can check table names with it.

Then run `.venv/bin/python md_bench.py --queries queries.csv --concurrency 1 --validate-only` and show the first
tagged query. Add `--session-mode csv` if they chose affinity; the tags then include a `-- session_name:` line.

## Step 5: Plan the runs

Explain what you'll suggest, then ask the user to confirm or change it:

- **Concurrency levels.** Start with `1` as the serial baseline, then `4` and `8`. A read scaling pool has 4 Ducklings
  by default, with a soft limit of 16, so levels above the pool size share Ducklings. Add `16` if they expect that
  many users at the same time.
- **Rounds.** Keep `queries × rounds` at least twice the highest concurrency level, so the pool stays full.
- **Session mode.** Use `--session-mode csv` only if they confirmed affinity in step 4. If the CSV has session names
  and they didn't, pass `--session-mode none`. With
  affinity, suggest running their main concurrency level twice, once without and once with `--session-mode csv`, so
  the Dive can compare the two.

Show the plan as a numbered list with one line per run, for example:

```text
1. Serial baseline: --concurrency 1, 1 round, session mode none
2. Concurrency 4, 1 round, session mode none
3. Concurrency 8, 1 round, session mode none
```

Then ask whether to run **the whole plan** or **only some of it**. For example: "Should I run all three, or only some?
You can name the numbers, such as just 1, or 2 and 3." Run only what they choose. Once those runs finish, report the
results and ask whether to run any of the remaining items. Never start a run that wasn't chosen.

## Step 6: Run the benchmark

Run the chosen runs one at a time, in the order of the plan, in the background. Wait for each one to finish:

```bash
.venv/bin/python md_bench.py --queries queries.csv --database <db> --concurrency 1 > runs/c1.log 2>&1
.venv/bin/python md_bench.py --queries queries.csv --database <db> --concurrency 4 > runs/c4.log 2>&1
```

Create `runs/` first with `mkdir -p runs`. Each run ends with a summary table and `Results saved to md_benchmarks`.
After each run, report only these values from its summary: the run (concurrency, rounds, session mode), E2E time,
queries, errors, Ducklings used, Duckling size, and price. Prices use the Business plan rates for the organization's
region, which the summary names. If the summary says `us-east-1 assumed`, tell the user the region couldn't be read.

If the output says the run's queries aren't in `query_history` yet, run
`.venv/bin/python md_bench.py --collect <run id>` later; the run ID is in the message. Nothing is lost, because the
run's details are saved in `runs/<run id>.json`.

## Step 7: Save the Dive

Customers usually send the Dive to stakeholders, so it reads from a **share** of the results database rather than from
the personal database. Ask who will view it:

- **People in their MotherDuck organization.** This is the default, `--share-access organization`.
- **People outside the organization.** Use `--share-access unrestricted`. Tell the user that anyone with the share link
  can then read the results, including the SQL of every benchmarked query, and confirm before you use it.

After the user confirms, create the Dive once:

```bash
.venv/bin/python md_bench.py --dive-only --create-dive --database <db>
```

The script creates or reuses `md_benchmarks_share`, with automatic updates, and points the Dive at it. It stores the
Dive ID in `.md_bench_dive_id`, so running the same command again updates that Dive instead of creating a second one. New runs show up in the Dive automatically, so it doesn't need saving again after each run.
The script prints the Dive's link, `https://app.motherduck.com/dives/<dive id>`. Give the user that full link.

If the command fails because `MD_CREATE_DIVE` isn't available and the MotherDuck MCP server is connected to the same
account, save the contents of `dive/benchmark_dive.tsx` with the MCP `save_dive` tool. Use the title
`MotherDuck query benchmarking`. First create the share with
`CREATE SHARE IF NOT EXISTS md_benchmarks_share FROM md_benchmarks (ACCESS ORGANIZATION, VISIBILITY DISCOVERABLE, UPDATE AUTOMATIC)`.
Then replace the Dive's `REQUIRED_DATABASES` entry with `{ type: "share", path: "<share url>", alias: "md_benchmarks" }`.

## Step 8: Report the results

When the chosen runs are done, show one table with a row per run and these columns: run, E2E time, queries, errors,
Ducklings used, and price. For affinity runs, add how many sessions stayed on one Duckling. Follow it with one line
giving the Dive's full link, `https://app.motherduck.com/dives/<dive id>`. Use the ID in `.md_bench_dive_id`, or the
link the script printed. Don't add analysis or
explanations unless the user asks.

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `read scaling token type: read_write`, or the tokens are identical | Create a separate token of type **Read Scaling**. |
| `can't read md_information_schema.query_history` | The read/write token must belong to an organization Admin. |
| `no database/share named "<db>"` when connecting | That account can't see the database. Use a database they own, or attach the share first in the MotherDuck UI. |
| Queries fail with `read-only` or permission errors | A query writes data. Remove it from the CSV. |
| `None of the benchmark queries ran on a read scaling Duckling` | The first token isn't a read scaling token, or read scaling isn't on their plan. |
| `Confirm with the user whether to use user-to-Duckling affinity` | The CSV has `session_name` values and no mode was given. Ask the user, then rerun with `--session-mode csv` or `--session-mode none`. |
| A run times out waiting for `query_history` | Run `--collect <run id>` later. |
| Stakeholders see an empty Dive | They need access to `md_benchmarks_share`. Check its access in the MotherDuck UI. New runs reach the share automatically. |
| A syntax error on every query | The CSV probably has literal `\n` characters. Rewrite it with real newlines. |
| `organization region is <region>; rerun with --region <region>` | The endpoint host doesn't match the organization's region. Add `--region <region>` to every command. |
| Price shows `n/a` with a known Duckling size | The region isn't in the script's rate table. Check https://motherduck.com/docs/about-motherduck/billing/pricing/#compute-pricing. |
