import hashlib
import json
import os
import re

import duckdb

UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
SQL_START_RE = re.compile(r"^\s*(?:--[^\n]*\n\s*)*(?:select|with|from)\b", re.I)
PARAM_RE = re.compile(r"\$\{(?:[^{}]|\{[^{}]*\})*\}")
URL_RE = re.compile(r"^md:[A-Za-z0-9_./-]+$")


def env(name, default=None):
    return os.environ.get(name, "").strip() or default


def ident(value, key):
    if not IDENT_RE.match(value):
        raise SystemExit(f"{key}={value!r} must be a plain SQL identifier")
    return value


def quote(name):
    return '"' + name.replace('"', '""') + '"'


def slug(text):
    s = re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48]
    return s if s[:1].isalpha() else f"d_{s}"


def main():
    dive_ref = env("DIVE")
    if not dive_ref:
        raise SystemExit("Set DIVE to a Dive title, id, or URL")
    cache_db = ident(env("CACHE_DATABASE", "dive_cache"), "CACHE_DATABASE")
    share_access = env("SHARE_ACCESS", "ORGANIZATION").upper()
    if share_access not in ("ORGANIZATION", "UNRESTRICTED", "NONE"):
        raise SystemExit("SHARE_ACCESS must be ORGANIZATION, UNRESTRICTED, or NONE")
    mirror_tables = env("MIRROR_TABLES", "true").lower() == "true"

    con = duckdb.connect("md:")
    dive = resolve_dive(con, dive_ref)
    schema = ident(env("CACHE_SCHEMA", slug(dive["title"])), "CACHE_SCHEMA")
    cached_title = env("CACHED_DIVE_TITLE", f"{dive['title']} (cached)")
    print(f"Source Dive: {dive['title']} ({dive['id']})")

    aliases = attach_resources(con, dive["resources"])
    con.execute(f"CREATE DATABASE IF NOT EXISTS {cache_db}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {cache_db}.{schema}")

    content, built, edits, live = dive["content"], set(), [], 0
    for start, end, text, encoding in js_strings(content):
        if not SQL_START_RE.match(text):
            continue
        dynamic = "${" in text
        ast = parse_select(con, PARAM_RE.sub("__dive_param__", text))
        if ast is None or not has_source(ast):
            continue
        if not dynamic:
            name = "q_" + hashlib.md5(text.encode()).hexdigest()[:12]
            if name not in built and not build(con, cache_db, schema, name, text.strip().rstrip(";")):
                live += 1
                continue
            built.add(name)
            edits.append((start, end, encode(f"SELECT * FROM {cache_db}.{schema}.{name}", encoding)))
            continue
        refs = table_refs(ast, aliases)
        if not mirror_tables or not refs:
            live += 1
            continue
        new_text = text
        for parts, (alias, src_schema, table) in refs:
            name = slug(f"{alias}__{src_schema}__{table}")
            source = ".".join(quote(p) for p in (alias, src_schema, table))
            if name not in built and not build(con, cache_db, schema, name, f"FROM {source}"):
                break
            built.add(name)
            new_text = qualified_name_re(parts).sub(f"{cache_db}.{schema}.{name}", new_text)
        else:
            edits.append((start, end, encode(new_text, encoding)))
            continue
        live += 1

    for start, end, replacement in sorted(edits, reverse=True):
        content = content[:start] + replacement + content[end:]
    print(f"Cached {len(built)} tables; rewrote {len(edits)} queries; {live} queries stay live")

    resources = [r for r in dive["resources"] if references_alias(content, r["alias"])]
    resources.append(cache_resource(con, cache_db, share_access))
    content = set_required_databases(content, resources)
    publish_dive(con, cached_title, dive, content, resources)
    drop_stale(con, cache_db, schema, built)


def resolve_dive(con, ref):
    match = UUID_RE.search(ref)
    if match:
        dive_id = match.group(0)
    else:
        rows = con.execute(
            "SELECT id::VARCHAR FROM MD_LIST_DIVES(include_org_shares := true) "
            "WHERE lower(title) = lower(?) ORDER BY updated_at DESC",
            [ref.strip()],
        ).fetchall()
        if len(rows) != 1:
            raise SystemExit(f"Expected one Dive titled {ref!r}, found {len(rows)}; use its id or URL")
        dive_id = rows[0][0]
    title, content, api_version, resources = con.execute(
        "SELECT title, content, version_api_version, version_required_resources "
        "FROM MD_GET_DIVE(id := ?::UUID)",
        [dive_id],
    ).fetchone()
    return {"id": dive_id, "title": title, "content": content, "api_version": api_version or 1,
            "resources": [{"url": r["url"], "alias": r["alias"]} for r in resources or []]}


def attach_resources(con, resources):
    attached = {r[0].lower() for r in con.execute("SELECT database_name FROM duckdb_databases()").fetchall()}
    for r in resources:
        if r["alias"].lower() not in attached:
            if not URL_RE.match(r["url"]):
                raise SystemExit(f"Unexpected resource URL {r['url']!r}")
            con.execute(f"ATTACH '{r['url']}' AS {quote(r['alias'])}")
    return {r["alias"].lower() for r in resources}


def js_strings(src):
    out = []
    scan_code(src, 0, out, top=True)
    return out


def scan_code(src, i, out, top):
    n, depth, prev = len(src), 0, ""
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            i = src.find("\n", i)
            i = n if i < 0 else i
            continue
        if src.startswith("/*", i):
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c == "/" and not (prev.isalnum() or prev in ")]_$<>"):
            i = skip_regex(src, i)
            prev = "/"
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] not in (c, "\n"):
                j += 2 if src[j] == "\\" else 1
            # A quote that never closes on its line is JSX text, not a string.
            if j < n and src[j] == c:
                if top:
                    raw = src[i + 1:j]
                    text = json.loads(f'"{raw}"') if c == '"' and "\\" in raw else raw
                    if c == '"' or "\\" not in raw:
                        out.append((i + 1, j, text, "json" if c == '"' else "raw"))
                i, prev = j + 1, c
                continue
        if c == "`":
            j = scan_template(src, i + 1, out)
            if top and "\\" not in src[i + 1:j]:
                out.append((i + 1, j, src[i + 1:j], "raw"))
            i, prev = j + 1, c
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            if not top and depth == 0:
                return i + 1
            depth -= 1
        if not c.isspace():
            prev = c
        i += 1
    return n


def scan_template(src, i, out):
    while i < len(src):
        if src[i] == "\\":
            i += 2
        elif src[i] == "`":
            return i
        elif src.startswith("${", i):
            i = scan_code(src, i + 2, out, top=False)
        else:
            i += 1
    return len(src)


def skip_regex(src, i):
    j, in_class = i + 1, False
    while j < len(src) and src[j] != "\n":
        if src[j] == "\\":
            j += 1
        elif src[j] == "[":
            in_class = True
        elif src[j] == "]":
            in_class = False
        elif src[j] == "/" and not in_class:
            return j + 1
        j += 1
    return i + 1


def encode(text, encoding):
    return json.dumps(text, ensure_ascii=False)[1:-1] if encoding == "json" else text


def parse_select(con, sql):
    ast = json.loads(con.execute("SELECT json_serialize_sql(?)", [sql]).fetchone()[0])
    return None if ast.get("error") or len(ast.get("statements", [])) != 1 else ast


def iter_nodes(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from iter_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_nodes(value)


def has_source(ast):
    return any(n.get("type") in ("BASE_TABLE", "TABLE_FUNCTION") for n in iter_nodes(ast))


def table_refs(ast, aliases):
    refs = {}
    for n in iter_nodes(ast):
        if n.get("type") != "BASE_TABLE":
            continue
        catalog, schema, table = n.get("catalog_name", ""), n.get("schema_name", ""), n["table_name"]
        if catalog.lower() in aliases:
            refs[(catalog, schema, table)] = (catalog, schema, table)
        elif not catalog and schema.lower() in aliases:
            refs[(schema, table)] = (schema, "main", table)
    return list(refs.items())


def qualified_name_re(parts):
    body = r"\s*\.\s*".join(f'"?{re.escape(p)}"?' for p in parts)
    return re.compile(rf'(?<![\w."]){body}(?![\w"])', re.I)


def references_alias(content, alias):
    pattern = re.compile(rf'(?<![\w."])"?{re.escape(alias)}"?\s*\.\s*["\w]', re.I)
    return bool(pattern.search(strip_required_databases(content)))


def build(con, cache_db, schema, name, select_sql):
    try:
        con.execute(f"CREATE OR REPLACE TABLE {cache_db}.{schema}.{name} AS {select_sql}")
        return True
    except duckdb.Error as exc:
        print(f"Left live (could not cache {name}): {str(exc).splitlines()[0]}")
        return False


def cache_resource(con, cache_db, share_access):
    if share_access == "NONE":
        return {"url": f"md:{cache_db}", "alias": cache_db}
    con.execute(
        f"CREATE SHARE IF NOT EXISTS {cache_db} FROM {cache_db} "
        f"(ACCESS {share_access}, VISIBILITY HIDDEN, UPDATE AUTOMATIC)"
    )
    url = con.execute("SELECT url FROM MD_LIST_DATABASE_SHARES() WHERE name = ?", [cache_db]).fetchone()[0]
    return {"url": url, "alias": cache_db}


REQUIRED_RE = re.compile(r"export\s+const\s+REQUIRED_DATABASES\s*=\s*\[")


def required_span(content):
    match = REQUIRED_RE.search(content)
    if not match:
        return None
    depth, i = 0, match.end() - 1
    while i < len(content):
        c = content[i]
        if c in "'\"`":
            i = content.index(c, i + 1)
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return match.start(), i + 1
        i += 1
    return None


def strip_required_databases(content):
    span = required_span(content)
    return content if span is None else content[:span[0]] + content[span[1]:]


def set_required_databases(content, resources):
    entries = [
        {"type": "share" if "_share/" in r["url"] else "database", "path": r["url"], "alias": r["alias"]}
        for r in resources
    ]
    block = "export const REQUIRED_DATABASES = " + json.dumps(entries, indent=2)
    span = required_span(content)
    if span is None:
        return block + ";\n\n" + content
    return content[:span[0]] + block + content[span[1]:]


def publish_dive(con, title, dive, content, resources):
    existing = con.execute(
        "SELECT id::VARCHAR FROM MD_LIST_DIVES() WHERE title = ? ORDER BY updated_at DESC LIMIT 1", [title]
    ).fetchone()
    if existing is None:
        dive_id = con.execute(
            "SELECT id::VARCHAR FROM MD_CREATE_DIVE(title := ?, content := ?, description := ?, "
            "api_version := ?, required_resources := ?)",
            [title, content, f"Cached copy of {dive['title']}", dive["api_version"], resources],
        ).fetchone()[0]
        print(f"Created cached Dive {title!r} ({dive_id})")
        return
    dive_id = existing[0]
    current = con.execute("SELECT content FROM MD_GET_DIVE(id := ?::UUID)", [dive_id]).fetchone()[0]
    if current == content:
        print(f"Cached Dive {title!r} ({dive_id}) is already current")
        return
    con.execute(
        "SELECT * FROM MD_UPDATE_DIVE_CONTENT(id := ?::UUID, content := ?, api_version := ?, required_resources := ?)",
        [dive_id, content, dive["api_version"], resources],
    )
    print(f"Updated cached Dive {title!r} ({dive_id})")


def drop_stale(con, cache_db, schema, built):
    tables = con.execute(
        "SELECT table_name FROM duckdb_tables() WHERE database_name = ? AND schema_name = ?", [cache_db, schema]
    ).fetchall()
    for (table,) in tables:
        if table not in built:
            con.execute(f"DROP TABLE {cache_db}.{schema}.{quote(table)}")
            print(f"Dropped stale cache table {table}")


if __name__ == "__main__":
    main()
