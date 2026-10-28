"""Interview insights: qualitative analysis of conversation transcripts with MotherDuck AI SQL.

load -> describe (prompt) -> discover a codebook (prompt) -> edit the codebook -> classify (prompt_jev).
Run with PHASE = load | describe | discover | edit | classify | reset.

To use your own data, replace load_turns() below; everything after it works on the
turns table it produces. Config keys: DATABASE, PHASE, MODEL, SUMMARY_MODEL,
SAMPLE_PER_COHORT, MIN_CHARS, LIMIT_TURNS, CONF, SENTIMENT_QUESTION, plus EDITS,
CONTEXT and SUMMARY, which the Dive sends.
"""

# @flight
#   flowchart TD
#     phase{Phase}:::switch
#     phase -->|load| load[Load turns from the source]:::load
#     load --> split[Build transcripts]:::transform
#     split --> describe[Describe the corpus with prompt]:::transform
#     phase -->|describe| describe
#     phase -->|reset| reset[Delete all pipeline data]:::load
#     phase -->|discover| sample[Stratified sample]:::query
#     sample --> extract[Extract insights and quotes with prompt]:::transform
#     extract --> codebook[Consolidate codebook]:::transform
#     phase -->|classify| themes[Assign themes with prompt_jev]:::transform
#     themes --> insights[Match insights and score quotes]:::transform
#     insights --> summaries[Summarize themes]:::transform
#     phase -->|edit| edit[Apply codebook edits]:::load
# @end-flight

import json
import os
import re
import time
from datetime import datetime, timezone

import duckdb

DB = (os.environ.get("DATABASE") or "interview_insights").strip()
if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", DB):
    raise SystemExit(f"DATABASE must be a plain identifier, got {DB!r}")
MODELS = {
    "gpt-6-luna", "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-4o", "gpt-4o-mini",
    "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
}
ALL_NODES = ["load", "split", "sample", "extract", "codebook", "themes", "insights", "summaries", "edit", "describe", "reset"]


def T(name):
    return f'"{DB}"."main"."{name}"'


def q(value):
    return "'" + str(value).replace("'", "''") + "'"


def schema_arg(name, schema):
    # prompt() wants OpenAI's response_format wrapper; a bare schema returns NULL silently.
    return q(json.dumps({"name": name, "strict": True, "schema": schema}))


def now():
    return datetime.now(timezone.utc).isoformat()


def ev(node, state, **fields):
    parts = [f"@flight-run {node} {state}", f"ts={now()}"]
    for key, value in fields.items():
        value = str(value)
        if any(ch in value for ch in ' ="'):
            value = '"' + value.replace('"', "'") + '"'
        parts.append(f"{key}={value}")
    print(" ".join(parts), flush=True)


class step:
    def __init__(self, node):
        self.node = node
        self.fields = {}

    def __enter__(self):
        ev(self.node, "running")
        self.t0 = time.time()
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type:
            ev(self.node, "failed", error=str(exc)[:300])
            return False
        ev(self.node, "ok", **self.fields)
        print(f"{self.node}: {time.time() - self.t0:.1f}s", flush=True)
        return False


def env_int(name, default, lo, hi):
    raw = (os.environ.get(name) or str(default)).strip()
    if not raw.isdigit():
        raise SystemExit(f"{name} must be a whole number, got {raw!r}")
    value = int(raw)
    if not lo <= value <= hi:
        raise SystemExit(f"{name} must be between {lo} and {hi}, got {value}")
    return value


def env_float(name, default, lo, hi):
    raw = (os.environ.get(name) or str(default)).strip()
    try:
        value = float(raw)
    except ValueError:
        raise SystemExit(f"{name} must be a number, got {raw!r}")
    if not lo <= value <= hi:
        raise SystemExit(f"{name} must be between {lo} and {hi}, got {value}")
    return value


def env_text(name, default, max_len):
    value = (os.environ.get(name) or default).strip()
    if len(value) > max_len:
        raise SystemExit(f"{name} must be at most {max_len} characters")
    return value


def env_model(name, default):
    value = (os.environ.get(name) or default).strip()
    if value not in MODELS:
        raise SystemExit(f"{name} must be one of {sorted(MODELS)}, got {value!r}")
    return value


def count(con, table, where="true"):
    return con.execute(f"SELECT count(*) FROM {T(table)} WHERE {where}").fetchone()[0]


def ensure_schema(con):
    con.execute(f'CREATE DATABASE IF NOT EXISTS "{DB}"')
    ddl = {
        "transcripts": "cohort VARCHAR, transcript_id VARCHAR, text VARCHAR",
        "turns": "turn_id VARCHAR, cohort VARCHAR, transcript_id VARCHAR, turn_no BIGINT, "
                 "speaker VARCHAR, text VARCHAR, prev_text VARCHAR",
        "sample": "cohort VARCHAR, transcript_id VARCHAR, text VARCHAR",
        "observations": "cohort VARCHAR, transcript_id VARCHAR, insight VARCHAR, quote VARCHAR, "
                        "sentiment VARCHAR, verbatim BOOLEAN",
        "groups": "group_id INTEGER, group_name VARCHAR, description VARCHAR",
        "themes": "theme_id INTEGER, group_id INTEGER, theme_name VARCHAR, description VARCHAR, "
                  "importance INTEGER DEFAULT 2",
        "insights": "insight_id INTEGER, theme_id INTEGER, insight VARCHAR",
        "turn_themes": "turn_id VARCHAR, theme_id INTEGER, theme_name VARCHAR, theme_conf DOUBLE, "
                       "sentiment DOUBLE, runner_up_id INTEGER, runner_up_prob DOUBLE",
        "turn_insights": "turn_id VARCHAR, theme_id INTEGER, insight_id INTEGER, insight_conf DOUBLE, "
                         "quotability DOUBLE",
        "theme_summaries": "theme_id INTEGER, summary VARCHAR",
        "pipeline_runs": "phase VARCHAR, finished_at TIMESTAMP, details VARCHAR",
        "corpus": "id INTEGER, summary VARCHAR, context VARCHAR, suggested VARCHAR, updated_at TIMESTAMP",
    }
    for table, cols in ddl.items():
        con.execute(f"CREATE TABLE IF NOT EXISTS {T(table)} ({cols})")
    con.execute(f"ALTER TABLE {T('themes')} ADD COLUMN IF NOT EXISTS importance INTEGER DEFAULT 2")
    for col in ("runner_up_id INTEGER", "runner_up_prob DOUBLE"):
        con.execute(f"ALTER TABLE {T('turn_themes')} ADD COLUMN IF NOT EXISTS {col}")


def log_run(con, phase, details):
    con.execute(
        f"INSERT INTO {T('pipeline_runs')} VALUES (?, now()::TIMESTAMP, ?)",
        [phase, json.dumps(details)],
    )


# ---------------------------------------------------------------- load: adapt this to your data

# Shown in the Dive. Name the source of your conversations and link to it.
SOURCE_NAME = "Anthropic Interviewer"
SOURCE_URL = "https://huggingface.co/datasets/Anthropic/AnthropicInterviewer"


def load_turns(con):
    """Return a SELECT producing one row per turn, with these columns:

    cohort         a group to sample and filter by (team, segment, survey wave, ...)
    transcript_id  one id per conversation
    turn_no        the order of the turn within its conversation
    speaker        'participant' for the people you study, 'interviewer' for everyone else
    text           what was said

    This version reads the Anthropic Interviewer CSVs from Hugging Face, where each
    transcript is one text with "User:" and "AI:" prefixes.
    """
    hf = "hf://datasets/Anthropic/AnthropicInterviewer/interview_transcripts"
    union = " UNION ALL ".join(
        f"SELECT '{c}' AS cohort, transcript_id, text FROM read_csv('{hf}/{c}_transcripts.csv')"
        for c in ("workforce", "creatives", "scientists")
    )
    return rf"""
        WITH raw AS ({union}),
        parts AS (
          SELECT cohort, transcript_id,
            string_split(regexp_replace(text, '\n\n(User|AI|Assistant): ', chr(30) || '\1: ', 'g'), chr(30)) AS l
          FROM raw
        ), flat AS (
          SELECT cohort, transcript_id, unnest(l) AS t, generate_subscripts(l, 1) AS turn_no FROM parts
        )
        SELECT cohort, transcript_id, turn_no,
          CASE regexp_extract(t, '^(User|AI|Assistant): ', 1) WHEN 'User' THEN 'participant' ELSE 'interviewer' END AS speaker,
          trim(regexp_replace(t, '^(User|AI|Assistant): ', '')) AS text
        FROM flat
    """


def load(con):
    with step("load") as s:
        con.execute(f"""
            CREATE OR REPLACE TABLE {T('turns')} AS
            SELECT transcript_id || '_' || lpad(turn_no::VARCHAR, 4, '0') AS turn_id,
              cohort, transcript_id, turn_no, speaker, text,
              lag(text) OVER (PARTITION BY transcript_id ORDER BY turn_no) AS prev_text
            FROM ({load_turns(con)})
            WHERE speaker IN ('participant', 'interviewer') AND coalesce(trim(text), '') <> ''
        """)
        if count(con, "turns", "speaker = 'participant'") == 0:
            raise SystemExit("load_turns() returned no participant turns.")
        s.fields = {"rows": count(con, "turns"), "effect": f"table:{DB}.main.turns"}

    # Discover and describe read whole conversations, rendered with speaker labels.
    with step("split") as s:
        con.execute(f"""
            CREATE OR REPLACE TABLE {T('transcripts')} AS
            SELECT cohort, transcript_id,
              string_agg(CASE speaker WHEN 'participant' THEN 'Participant: ' ELSE 'Interviewer: ' END || text,
                         chr(10) || chr(10) ORDER BY turn_no) AS text
            FROM {T('turns')} GROUP BY 1, 2
        """)
        s.fields = {"rows": count(con, "transcripts"), "effect": f"table:{DB}.main.transcripts"}

    log_run(con, "load", {
        "source": SOURCE_NAME, "url": SOURCE_URL,
        "transcripts": count(con, "transcripts"),
        "participant_turns": count(con, "turns", "speaker = 'participant'"),
    })


# ---------------------------------------------------------------- describe

# Context the researcher confirms about the corpus; it is prepended to every prompt.
CONTEXT_LABELS = {
    "source": "Source",
    "domain": "Industry or domain",
    "period": "Period",
    "participants": "Participants",
    "question": "Research question",
}

DESCRIBE_PROMPT = """You describe a corpus of conversation transcripts (interviews, calls or meetings) for researchers who are about to analyse it.
Below are corpus statistics, context the researcher confirmed (may be empty) and a random sample of participant answers.
Write a summary of 2 to 4 sentences: what the corpus is, who the participants are, what they talk about, and anything a researcher should know before coding it. Do not state facts that neither the context nor the answers support.
Also fill in each context field with your best inference from the material, under 15 words, or null when the material does not say.
"""

DESCRIBE_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}, **{k: {"type": ["string", "null"]} for k in CONTEXT_LABELS}},
    "required": ["summary", *CONTEXT_LABELS],
    "additionalProperties": False,
}


def parse_context(raw):
    try:
        ctx = json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit("CONTEXT must be a JSON object")
    if not isinstance(ctx, dict) or set(ctx) - set(CONTEXT_LABELS):
        raise SystemExit(f"CONTEXT keys must be among {', '.join(CONTEXT_LABELS)}")
    out = {}
    for key, value in ctx.items():
        if not isinstance(value, str) or len(value.strip()) > 300:
            raise SystemExit(f"CONTEXT {key} must be text of at most 300 characters")
        if value.strip():
            out[key] = value.strip()
    return out


def load_corpus(con):
    row = con.execute(f"SELECT context, summary, suggested FROM {T('corpus')} WHERE id = 1").fetchone()
    if not row:
        return {}, "", {}
    return json.loads(row[0] or "{}"), row[1] or "", json.loads(row[2] or "{}")


def context_text(ctx, summary=""):
    lines = [f"- {label}: {ctx[key]}" for key, label in CONTEXT_LABELS.items() if ctx.get(key)]
    parts = []
    if summary:
        parts.append(f"Summary of this corpus:\n{summary}")
    if lines:
        parts.append("Context about this corpus, confirmed by the researcher:\n" + "\n".join(lines))
    return "\n\n".join(parts) + "\n\n" if parts else ""


def describe(con, model):
    with step("describe") as s:
        old_ctx, _, suggested = load_corpus(con)
        raw_ctx = (os.environ.get("CONTEXT") or "").strip()
        ctx = parse_context(raw_ctx) if raw_ctx else old_ctx
        # A summary written by the researcher is stored as is; otherwise the model writes one.
        summary = (os.environ.get("SUMMARY") or "").strip()
        if len(summary) > 2000:
            raise SystemExit("SUMMARY must be at most 2000 characters")
        if summary:
            s.fields = {"rows": 1, "effect": f"table:{DB}.main.corpus"}
            save_corpus(con, summary, ctx, suggested)
            log_run(con, "describe", {"summary_by": "researcher", "context_fields": len(ctx)})
            return
        cohorts = con.execute(f"SELECT cohort, count(*) FROM {T('transcripts')} GROUP BY 1 ORDER BY 1").fetchall()
        answers = con.execute(f"""
            SELECT cohort, left(text, 400) FROM {T('turns')}
            WHERE speaker = 'participant' AND length(text) >= 80
            ORDER BY hash(turn_id || 'describe') LIMIT 40
        """).fetchall()
        n_answers = count(con, "turns", "speaker = 'participant'")
        text = (
            DESCRIBE_PROMPT
            + "\nSTATISTICS:\n" + "\n".join(f"- cohort {c}: {n} transcripts" for c, n in cohorts)
            + f"\n- {n_answers} participant answers in total\n\n"
            + (context_text(ctx) or "Context about this corpus: none given\n\n")
            + "SAMPLE ANSWERS:\n" + "\n".join(f"- [{c}] {t}" for c, t in answers)
        )
        raw = con.execute(
            f"SELECT prompt(?, model := {q(model)}, json_schema := {schema_arg('corpus', DESCRIBE_SCHEMA)})::VARCHAR",
            [text],
        ).fetchone()[0]
        if raw is None:
            raise SystemExit("prompt() returned NULL for the corpus summary.")
        out = json.loads(raw)
        suggested = {key: out[key] for key in CONTEXT_LABELS if out.get(key)}
        save_corpus(con, out["summary"], ctx, suggested)
        s.fields = {"rows": 1, "effect": f"table:{DB}.main.corpus"}
    log_run(con, "describe", {"summary_by": model, "context_fields": len(ctx), "suggested_fields": len(suggested)})


def save_corpus(con, summary, ctx, suggested):
    con.execute(f"DELETE FROM {T('corpus')}")
    con.execute(
        f"INSERT INTO {T('corpus')} VALUES (1, ?, ?, ?, now()::TIMESTAMP)",
        [summary, json.dumps(ctx), json.dumps(suggested)],
    )


# ---------------------------------------------------------------- discover

OBS_PROMPT = """You are a qualitative researcher analysing a conversation between an interviewer and a participant.
Extract 3 to 6 observations about the PARTICIPANT (lines starting with "Participant:"), not the interviewer.
Each observation has:
- insight: one sentence stating a generalisable finding in neutral language (what this person experiences, believes, needs or does).
- quote: an EXACT verbatim excerpt from the participant's own words that supports the insight. Copy characters exactly; do not paraphrase or merge sentences.
- sentiment: the participant's attitude in this observation.
Prefer tensions, frictions, motivations, and concrete practices over generic statements."""

OBS_SCHEMA = {
    "type": "object",
    "properties": {
        "observations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "insight": {"type": "string"},
                    "quote": {"type": "string"},
                    "sentiment": {"type": "string", "enum": ["positive", "mixed", "negative", "neutral"]},
                },
                "required": ["insight", "quote", "sentiment"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["observations"],
    "additionalProperties": False,
}

CB_PROMPT = """You are building a codebook for qualitative analysis of the conversations described above.
Below are observations extracted from a sample of conversations, each tagged with cohort and sentiment.
Consolidate them into a hierarchy:
- 4 to 6 top-level groups (broad areas, e.g. "Onboarding and setup").
- 3 to 5 themes per group. A theme is a specific, mutually distinguishable pattern. Give it a short title (max 6 words) and a one-sentence description precise enough for a classifier to decide whether a new participant answer belongs to it.
- 2 to 4 insights per theme: one-sentence findings phrased as claims (e.g. "Unclear pricing makes teams delay a purchase decision").
Theme titles must be unique across the whole codebook. Use neutral, specific language. Cover the observations broadly; do not invent topics that are absent.

OBSERVATIONS:
"""

CB_SCHEMA = {
    "type": "object",
    "properties": {
        "groups": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "themes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "description": {"type": "string"},
                                "insights": {"type": "array", "items": {"type": "string"}},
                            },
                            "required": ["name", "description", "insights"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "description", "themes"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["groups"],
    "additionalProperties": False,
}


def discover(con, per_cohort, model):
    ctx = context_text(*load_corpus(con)[:2])
    if count(con, "transcripts") == 0:
        raise SystemExit("No transcripts loaded yet. Run PHASE=load first.")

    with step("sample") as s:
        con.execute(f"""
            CREATE OR REPLACE TABLE {T('sample')} AS
            SELECT cohort, transcript_id, text FROM (
              SELECT *, row_number() OVER (
                PARTITION BY cohort ORDER BY hash(transcript_id || 'interview-insights')
              ) AS rn
              FROM {T('transcripts')}
            ) WHERE rn <= {per_cohort}
        """)
        s.fields = {"rows": count(con, "sample"), "effect": f"table:{DB}.main.sample"}

    with step("extract") as s:
        con.execute(f"""
            CREATE OR REPLACE TABLE {T('observations')} AS
            WITH r AS MATERIALIZED (
              SELECT cohort, transcript_id, text,
                prompt({q(ctx + OBS_PROMPT)} || chr(10) || chr(10) || 'TRANSCRIPT:' || chr(10) || text,
                       model := {q(model)}, json_schema := {schema_arg("observations", OBS_SCHEMA)})::JSON AS j
              FROM {T('sample')}
            ), u AS (
              SELECT cohort, transcript_id, text,
                unnest(from_json(j -> 'observations',
                  '[{{"insight":"VARCHAR","quote":"VARCHAR","sentiment":"VARCHAR"}}]')) AS o
              FROM r
            )
            SELECT cohort, transcript_id, o.insight AS insight, o.quote AS quote,
              o.sentiment AS sentiment, contains(text, o.quote) AS verbatim
            FROM u
        """)
        n_obs = count(con, "observations")
        if n_obs == 0:
            raise SystemExit("prompt() returned no observations; check the model and json_schema.")
        s.fields = {"rows": n_obs, "effect": f"table:{DB}.main.observations"}

    with step("codebook") as s:
        obs = con.execute(
            f"SELECT cohort, sentiment, insight FROM {T('observations')} ORDER BY transcript_id"
        ).fetchall()
        lines = "\n".join(f"- [{c}, {snt}] {ins}" for c, snt, ins in obs)
        # A large model on the full observation list can take minutes and sometimes returns NULL.
        for attempt in range(1, 4):
            raw = con.execute(
                f"SELECT prompt(?, model := {q(model)}, json_schema := {schema_arg('codebook', CB_SCHEMA)})::VARCHAR",
                [ctx + CB_PROMPT + lines],
            ).fetchone()[0]
            if raw is not None:
                break
            print(f"codebook attempt {attempt} returned NULL", flush=True)
        if raw is None:
            raise SystemExit("prompt() returned NULL for the codebook three times.")
        codebook = json.loads(raw)

        groups, themes, insights = [], [], []
        seen = set()
        insight_id = 0
        for gi, g in enumerate(codebook["groups"], start=1):
            groups.append({"group_id": gi, "group_name": g["name"], "description": g["description"]})
            for ti, t in enumerate(g["themes"], start=1):
                name = t["name"].strip()
                while name.lower() in seen:
                    name += " (2)"
                seen.add(name.lower())
                theme_id = gi * 100 + ti
                themes.append({"theme_id": theme_id, "group_id": gi,
                               "theme_name": name, "description": t["description"]})
                for text in t["insights"]:
                    insight_id += 1
                    insights.append({"insight_id": insight_id, "theme_id": theme_id, "insight": text.strip()})

        for table, rows in (("groups", groups), ("themes", themes), ("insights", insights)):
            path = f"/tmp/{table}.json"
            with open(path, "w") as f:
                json.dump(rows, f)
            con.execute(f"DELETE FROM {T(table)}")
            con.execute(f"INSERT INTO {T(table)} BY NAME SELECT * FROM read_json_auto('{path}')")

        # A new codebook invalidates earlier classifications.
        for table in ("turn_themes", "turn_insights", "theme_summaries"):
            con.execute(f"DELETE FROM {T(table)}")

        s.fields = {"rows": len(themes), "effect": f"table:{DB}.main.themes"}

    verbatim = con.execute(f"SELECT avg(verbatim::INT) FROM {T('observations')}").fetchone()[0] or 0
    log_run(con, "discover", {
        "model": model, "conversations": count(con, "sample"), "observations": len(obs),
        "verbatim_rate": round(verbatim, 3), "groups": len(groups),
        "themes": len(themes), "insights": len(insights),
    })


# ---------------------------------------------------------------- classify

# prompt_jev needs constant instructions, so the question is a config value rather than a parameter.
DEFAULT_SENTIMENT_QUESTION = "How does the participant feel about the subject of the conversation, based on this answer?"

def choice_literal(items, other_label, other_desc):
    parts = [f"{{label: {q(label)}, description: {q(desc) if desc else 'NULL'}}}" for label, desc in items]
    parts.append(f"{{label: {q(other_label)}, description: {q(other_desc)}}}")
    return "[" + ", ".join(parts) + "]"


def classify(con, min_chars, limit, conf, summary_model, sentiment_question):
    ctx = context_text(*load_corpus(con)[:2])
    themes = con.execute(
        f"SELECT theme_id, theme_name, description FROM {T('themes')} ORDER BY theme_id"
    ).fetchall()
    if not themes:
        raise SystemExit("No codebook yet. Run PHASE=discover first.")

    limit_sql = f"ORDER BY hash(turn_id) LIMIT {limit}" if limit > 0 else ""
    base = f"""
        SELECT turn_id, text,
          coalesce('Interviewer asked: ' || left(prev_text, 400) || chr(10), '')
            || 'Participant answered: ' || text AS ctx
        FROM {T('turns')}
        WHERE speaker = 'participant' AND length(text) >= {min_chars}
        {limit_sql}
    """
    for table in ("turn_themes", "turn_insights", "theme_summaries"):
        con.execute(f"DELETE FROM {T(table)}")

    with step("themes") as s:
        choices = choice_literal(
            [(name, desc) for _, name, desc in themes],
            "other", "Small talk, logistics, or a topic none of the other labels covers",
        )
        # The runner-up label shows which themes the classifier confuses, for merge decisions.
        con.execute(f"""
            INSERT INTO {T('turn_themes')}
              (turn_id, theme_id, theme_name, theme_conf, sentiment, runner_up_id, runner_up_prob)
            WITH base AS ({base}),
            c AS MATERIALIZED (
              SELECT turn_id,
                prompt_jev(ctx, 'Which theme does the participant''s answer mainly express?',
                           choice := {choices}) AS th,
                prompt_jev(ctx, {q(sentiment_question)},
                           score := ['very negative', 'negative', 'neutral', 'positive', 'very positive']) AS se
              FROM base
            ), r AS (
              SELECT *, list_reverse_sort(list_transform(
                  list_filter(th.probabilities, lambda p: p.value <> th.choice),
                  lambda p: {{'p': p.probability, 'v': p.value}}))[1] AS ru
              FROM c
            )
            SELECT r.turn_id, t.theme_id, r.th.choice, r.th.confidence, (r.se.score - 2) / 2,
              t2.theme_id, r.ru.p
            FROM r
            LEFT JOIN {T('themes')} t ON t.theme_name = r.th.choice
            LEFT JOIN {T('themes')} t2 ON t2.theme_name = r.ru.v
        """)
        s.fields = {"rows": count(con, "turn_themes"), "effect": f"table:{DB}.main.turn_themes"}

    with step("insights") as s:
        ins = con.execute(
            f"SELECT theme_id, insight_id, insight FROM {T('insights')} ORDER BY theme_id, insight_id"
        ).fetchall()
        by_theme = {}
        for theme_id, insight_id, text in ins:
            by_theme.setdefault(theme_id, []).append((insight_id, text))

        selects = []
        for theme_id, items in by_theme.items():
            choices = choice_literal(
                [(text, None) for _, text in items],
                "none", "The answer does not support any of these statements",
            )
            selects.append(f"""
              SELECT b.turn_id, {theme_id} AS theme_id,
                prompt_jev(b.ctx, 'Which statement does this answer support most directly?',
                           choice := {choices}) AS m,
                prompt_jev(b.text, 'This is a vivid, specific, self-contained statement that would work as a quote in a research report.') AS quotability
              FROM base b JOIN {T('turn_themes')} tt USING (turn_id)
              WHERE tt.theme_id = {theme_id} AND tt.theme_conf >= {conf}
            """)
        con.execute(f"""
            INSERT INTO {T('turn_insights')}
            WITH base AS ({base}),
            c AS MATERIALIZED ({' UNION ALL '.join(selects)})
            SELECT c.turn_id, c.theme_id, i.insight_id, c.m.confidence, c.quotability
            FROM c LEFT JOIN {T('insights')} i
              ON i.theme_id = c.theme_id AND i.insight = c.m.choice
        """)
        s.fields = {"rows": count(con, "turn_insights"), "effect": f"table:{DB}.main.turn_insights"}

    with step("summaries") as s:
        con.execute(f"""
            INSERT INTO {T('theme_summaries')}
            WITH top AS (
              SELECT ti.theme_id, tu.text,
                row_number() OVER (PARTITION BY ti.theme_id
                                   ORDER BY ti.quotability * ti.insight_conf DESC) AS rn
              FROM {T('turn_insights')} ti JOIN {T('turns')} tu USING (turn_id)
              WHERE ti.insight_id IS NOT NULL
            ), agg AS (
              SELECT theme_id, string_agg('- ' || left(text, 500), chr(10)) AS quotes
              FROM top WHERE rn <= 15 GROUP BY 1
            )
            SELECT a.theme_id,
              prompt({q(ctx)} || 'Write a neutral 3-sentence summary of what participants say about the theme "'
                     || t.theme_name || '" (' || t.description || '). '
                     || 'Base it only on the excerpts below. No preamble, no bullet points.'
                     || chr(10) || a.quotes, model := {q(summary_model)})
            FROM agg a JOIN {T('themes')} t USING (theme_id)
        """)
        s.fields = {"rows": count(con, "theme_summaries"), "effect": f"table:{DB}.main.theme_summaries"}

    log_run(con, "classify", {
        "turns": count(con, "turn_themes"),
        "other_share": round(con.execute(
            f"SELECT avg((theme_id IS NULL)::INT) FROM {T('turn_themes')}").fetchone()[0] or 0, 3),
        "matched_insights": count(con, "turn_insights", "insight_id IS NOT NULL"),
        "limit": limit, "min_chars": min_chars, "conf": conf, "summary_model": summary_model,
    })


# ---------------------------------------------------------------- edit

# EDITS is a JSON list applied in one transaction. Themes added in the same batch
# carry a negative temporary id that later edits can reference.
#   {"op": "add", "id": -1, "group": 4, "name": "...", "description": "...", "importance": 2}
#   {"op": "update", "id": 101, "name": "...", "description": "...", "importance": 3}
#   {"op": "delete", "id": 101}
#   {"op": "merge", "id": 101, "target": 102}
MAX_EDITS = 100


def int_field(i, e, key, lo, hi):
    value = e.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        raise SystemExit(f"Edit {i}: {key} must be a whole number between {lo} and {hi}")
    return value


def text_field(i, e, key, max_len):
    value = e.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"Edit {i}: {key} is required")
    if len(value.strip()) > max_len:
        raise SystemExit(f"Edit {i}: {key} must be at most {max_len} characters")
    return value.strip()


def check_name(con, i, name, theme_id=None):
    # classify maps prompt_jev labels back to themes by exact name, so names must be unique.
    if name.lower() == "other":
        raise SystemExit(f'Edit {i}: "other" is reserved for answers that fit no theme')
    clash = con.execute(
        f"SELECT theme_id FROM {T('themes')} WHERE lower(theme_name) = lower(?) AND theme_id IS DISTINCT FROM ?",
        [name, theme_id],
    ).fetchone()
    if clash:
        raise SystemExit(f"Edit {i}: theme {clash[0]} already uses the name {name!r}")


def theme_ref(con, i, e, key, new_ids):
    theme_id = int_field(i, e, key, -MAX_EDITS, 99999)
    if theme_id < 0:
        if theme_id not in new_ids:
            raise SystemExit(f"Edit {i}: {key} {theme_id} refers to a theme not added earlier in this batch")
        theme_id = new_ids[theme_id]
    if not con.execute(f"SELECT 1 FROM {T('themes')} WHERE theme_id = ?", [theme_id]).fetchone():
        raise SystemExit(f"Edit {i}: theme {theme_id} does not exist")
    return theme_id


def apply_edit(con, i, e, new_ids):
    if not isinstance(e, dict):
        raise SystemExit(f"Edit {i}: must be an object")
    op = e.get("op")
    if op == "add":
        ref = int_field(i, e, "id", -MAX_EDITS, -1)
        if ref in new_ids:
            raise SystemExit(f"Edit {i}: temporary id {ref} is used twice")
        group_id = int_field(i, e, "group", 1, 99)
        if not con.execute(f"SELECT 1 FROM {T('groups')} WHERE group_id = ?", [group_id]).fetchone():
            raise SystemExit(f"Edit {i}: group {group_id} does not exist")
        name = text_field(i, e, "name", 80)
        check_name(con, i, name)
        theme_id = con.execute(
            f"SELECT coalesce(max(theme_id), ? * 100) + 1 FROM {T('themes')} WHERE group_id = ?",
            [group_id, group_id],
        ).fetchone()[0]
        con.execute(
            f"INSERT INTO {T('themes')} (theme_id, group_id, theme_name, description, importance) "
            "VALUES (?, ?, ?, ?, ?)",
            [theme_id, group_id, name, text_field(i, e, "description", 500), int_field(i, e, "importance", 1, 3)],
        )
        new_ids[ref] = theme_id
    elif op == "update":
        theme_id = theme_ref(con, i, e, "id", new_ids)
        name = text_field(i, e, "name", 80)
        check_name(con, i, name, theme_id)
        con.execute(
            f"UPDATE {T('themes')} SET theme_name = ?, description = ?, importance = ? WHERE theme_id = ?",
            [name, text_field(i, e, "description", 500), int_field(i, e, "importance", 1, 3), theme_id],
        )
    elif op in ("delete", "merge"):
        theme_id = theme_ref(con, i, e, "id", new_ids)
        if op == "merge":
            target = theme_ref(con, i, e, "target", new_ids)
            if target == theme_id:
                raise SystemExit(f"Edit {i}: cannot merge a theme into itself")
            con.execute(f"UPDATE {T('insights')} SET theme_id = ? WHERE theme_id = ?", [target, theme_id])
        else:
            con.execute(f"DELETE FROM {T('insights')} WHERE theme_id = ?", [theme_id])
        con.execute(f"DELETE FROM {T('themes')} WHERE theme_id = ?", [theme_id])
    else:
        raise SystemExit(f"Edit {i}: op must be add, update, delete or merge, got {op!r}")


def edit(con):
    raw = (os.environ.get("EDITS") or "").strip()
    try:
        edits = json.loads(raw)
    except json.JSONDecodeError:
        raise SystemExit("EDITS must be a JSON list of edits")
    if not isinstance(edits, list) or not 1 <= len(edits) <= MAX_EDITS:
        raise SystemExit(f"EDITS must be a JSON list of 1 to {MAX_EDITS} edits")

    with step("edit") as s:
        new_ids = {}
        con.execute("BEGIN TRANSACTION")
        try:
            for i, e in enumerate(edits, start=1):
                apply_edit(con, i, e, new_ids)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        s.fields = {"rows": len(edits), "effect": f"table:{DB}.main.themes"}

    ops = {}
    for e in edits:
        ops[e["op"]] = ops.get(e["op"], 0) + 1
    log_run(con, "edit", {"edits": len(edits), **ops, "added_ids": sorted(new_ids.values())})


# ---------------------------------------------------------------- reset

# Every table the pipeline writes; reset empties them so a demo can start again from Load.
PIPELINE_TABLES = (
    "transcripts", "turns", "sample", "observations", "groups", "themes", "insights",
    "turn_themes", "turn_insights", "theme_summaries", "corpus", "pipeline_runs",
)


def reset(con):
    with step("reset") as s:
        for table in PIPELINE_TABLES:
            con.execute(f"DELETE FROM {T(table)}")
        s.fields = {"rows": len(PIPELINE_TABLES), "effect": f"table:{DB}.main.pipeline_runs"}
    log_run(con, "reset", {"tables_emptied": len(PIPELINE_TABLES)})


# ---------------------------------------------------------------- main

def main():
    phase = (os.environ.get("PHASE") or "load").strip()
    if phase not in ("load", "describe", "discover", "classify", "edit", "reset"):
        raise SystemExit(f"PHASE must be load, describe, discover, classify, edit or reset, got {phase!r}")
    print(f"phase={phase}", flush=True)
    ev("@flight", "running")
    ev("phase", "ok", branch=phase)

    # A Dive waits on edit runs, so attach only this database for those.
    con = duckdb.connect(f"md:{DB}?attach_mode=single" if phase == "edit" else "md:")
    ensure_schema(con)

    ran = {"load": ["load", "split", "describe"], "describe": ["describe"], "discover": ["sample", "extract", "codebook"],
           "classify": ["themes", "insights", "summaries"], "edit": ["edit"], "reset": ["reset"]}[phase]
    for node in ALL_NODES:
        if node not in ran:
            ev(node, "skipped")

    try:
        if phase == "load":
            load(con)
            describe(con, env_model("MODEL", "gpt-6-luna"))
        elif phase == "describe":
            describe(con, env_model("MODEL", "gpt-6-luna"))
        elif phase == "discover":
            discover(con, env_int("SAMPLE_PER_COHORT", 20, 1, 500), env_model("MODEL", "gpt-6-luna"))
        elif phase == "edit":
            edit(con)
        elif phase == "reset":
            reset(con)
        else:
            classify(
                con,
                env_int("MIN_CHARS", 80, 0, 5000),
                env_int("LIMIT_TURNS", 0, 0, 100000),
                env_float("CONF", 0.4, 0.0, 1.0),
                env_model("SUMMARY_MODEL", "gpt-5-mini"),
                env_text("SENTIMENT_QUESTION", DEFAULT_SENTIMENT_QUESTION, 300),
            )
        # The Dive reads this database through a share; publish the writes so it sees them at once.
        con.execute(f'CREATE SNAPSHOT OF "{DB}"')
    except BaseException as exc:
        ev("@flight", "failed", error=str(exc)[:300])
        raise
    ev("@flight", "ok")


if __name__ == "__main__":
    main()
