#!/usr/bin/env python3
# /// script
# dependencies = ["pyyaml"]
# ///

"""Validate README front matter and build the examples catalog.

Every catalog entry is a README.md with YAML front matter holding exactly nine
keys: title, id, description, type, category, features, tags, prompt,
published_date. Running this script with no output path validates the front
matter across the repo (non-zero exit on the first problem). Pass --output to
also write catalog.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]

ALLOWED_TYPES = {"example", "template"}
ALLOWED_CATEGORIES = {
    "getting-started",
    "ingestion",
    "analytics",
    "integrations",
    "end-to-end",
    "automation",
}
ALLOWED_FEATURES = {
    "admin_api",
    "dives",
    "ducklake",
    "flights",
    "mcp",
    "pg_duckdb",
    "pg_endpoint",
    "shares",
    "wasm",
}
# Curated tag vocabulary. Tags name significant third-party tools, frameworks,
# languages, platforms, and libraries. They are NOT for datasets (nyc-taxi,
# tpc-h), generic concepts (sql, etl, serverless), redundant variants of an
# existing tag (cloudflare-workers when cloudflare exists), or anything already
# expressed as a feature (pg_duckdb, ducklake). Add a tag here only for a
# significant new third-party thing.
ALLOWED_TAGS = {
    # languages and runtimes
    "python",
    "nodejs",
    "typescript",
    "javascript",
    # data tooling and frameworks
    "dbt",
    "dlt",
    "sqlmesh",
    "metricflow",
    "connectorx",
    # libraries
    "pandas",
    "pyarrow",
    "scikit-learn",
    "node-postgres",
    "generic-pool",
    "d3",
    # platforms and infrastructure
    "cloudflare",
    "vercel",
    "nextjs",
    "durable-objects",
    "docker",
    "grafana",
    "s3",
    # messaging and alerting
    "slack",
    # crm and marketing
    "hubspot",
    # external databases
    "postgres",
    "sqlite",
    "snowflake",
    "bigquery",
    # SaaS / external data sources
    "google-sheets",
    # data movement patterns
    "ingest",
    "export",
    "migrate",
}
ALLOWED_KEYS = {
    "title",
    "id",
    "description",
    "prompt",
    "type",
    "category",
    "features",
    "tags",
    "published_date",
}
REQUIRED_KEYS = {
    "title",
    "id",
    "description",
    "prompt",
    "type",
    "category",
    "published_date",
}
FLIGHT_PLANS_DIR = "flight-plans"

SKIPPED_DIR_NAMES = {
    ".git",
    ".github",
    ".context",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}


class CatalogError(Exception):
    """Raised when README catalog front matter is invalid."""


def read_frontmatter(readme_path: Path) -> dict[str, Any] | None:
    text = readme_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None

    end_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        ),
        None,
    )
    if end_index is None:
        raise CatalogError(f"{readme_path}: front matter is missing a closing ---")

    raw_frontmatter = "\n".join(lines[1:end_index])
    data = yaml.safe_load(raw_frontmatter) or {}
    if not isinstance(data, dict):
        raise CatalogError(f"{readme_path}: front matter must be a YAML mapping")
    return data


def iter_readmes(repo_root: Path) -> list[Path]:
    readmes: list[Path] = []
    for path in repo_root.rglob("*"):
        if not path.is_file() or path.name.lower() != "readme.md":
            continue
        relative_parts = path.relative_to(repo_root).parts
        if any(
            part.startswith(".") or part in SKIPPED_DIR_NAMES for part in relative_parts
        ):
            continue
        readmes.append(path)
    return sorted(readmes)


def assert_string(data: dict[str, Any], readme_path: Path, key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CatalogError(f"{readme_path}: {key} must be a non-empty string")
    return value.strip()


def assert_slug_list(data: dict[str, Any], readme_path: Path, key: str) -> list[str]:
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise CatalogError(f"{readme_path}: {key} must be a list")
    if not all(isinstance(item, str) and item.strip() for item in value):
        raise CatalogError(f"{readme_path}: {key} must contain only non-empty strings")
    cleaned = [item.strip() for item in value]
    for item in cleaned:
        if item != item.lower() or " " in item:
            raise CatalogError(
                f"{readme_path}: {key} values must be lowercase slugs, got {item!r}"
            )
    return cleaned


def assert_date(data: dict[str, Any], readme_path: Path, key: str) -> str:
    """Return a YYYY-MM-DD string from a YAML date/datetime or ISO date string.

    PyYAML parses an unquoted ISO date in front matter as a datetime.date, so
    accept that as well as a quoted string and normalize both to a plain
    YYYY-MM-DD string.
    """
    value = data.get(key)
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()).isoformat()
        except ValueError as error:
            raise CatalogError(
                f"{readme_path}: {key} must be a YYYY-MM-DD date, got {value.strip()!r}"
            ) from error
    raise CatalogError(f"{readme_path}: {key} must be a YYYY-MM-DD date string")


def build_item(
    repo_root: Path, readme_path: Path, frontmatter: dict[str, Any], repo: str, ref: str
) -> dict[str, Any] | None:
    if frontmatter.get("catalog") is False:
        return None

    unknown_keys = set(frontmatter) - ALLOWED_KEYS - {"catalog"}
    if unknown_keys:
        raise CatalogError(
            f"{readme_path}: unknown front matter keys {sorted(unknown_keys)}; "
            f"allowed keys are {sorted(ALLOWED_KEYS)}"
        )
    missing = REQUIRED_KEYS - set(frontmatter)
    if missing:
        raise CatalogError(f"{readme_path}: missing required keys {sorted(missing)}")

    item_id = assert_string(frontmatter, readme_path, "id")
    folder_name = readme_path.parent.name
    if item_id != folder_name:
        raise CatalogError(
            f"{readme_path}: id {item_id!r} must match the folder name {folder_name!r}"
        )

    item_type = assert_string(frontmatter, readme_path, "type")
    if item_type not in ALLOWED_TYPES:
        raise CatalogError(
            f"{readme_path}: type must be one of {sorted(ALLOWED_TYPES)}"
        )

    category = assert_string(frontmatter, readme_path, "category")
    if category not in ALLOWED_CATEGORIES:
        raise CatalogError(
            f"{readme_path}: category must be one of {sorted(ALLOWED_CATEGORIES)}"
        )

    features = assert_slug_list(frontmatter, readme_path, "features")
    unknown_features = set(features) - ALLOWED_FEATURES
    if unknown_features:
        raise CatalogError(
            f"{readme_path}: unknown features {sorted(unknown_features)}; "
            f"allowed features are {sorted(ALLOWED_FEATURES)}"
        )

    tags = assert_slug_list(frontmatter, readme_path, "tags")
    unknown_tags = set(tags) - ALLOWED_TAGS
    if unknown_tags:
        raise CatalogError(
            f"{readme_path}: tags {sorted(unknown_tags)} are not in the curated tag list. "
            f"Tags are for significant third-party tools, not datasets, generic concepts, "
            f"redundant variants, or features. Add to ALLOWED_TAGS in build-catalog.py only "
            f"for a significant new tool. Allowed tags: {sorted(ALLOWED_TAGS)}"
        )

    prompt = assert_string(frontmatter, readme_path, "prompt")
    published_date = assert_date(frontmatter, readme_path, "published_date")

    relative_dir = readme_path.parent.relative_to(repo_root).as_posix()
    under_flight_plans = relative_dir == FLIGHT_PLANS_DIR or relative_dir.startswith(
        f"{FLIGHT_PLANS_DIR}/"
    )
    # flight-plans/ holds reusable, single-file Flight templates only. Concrete
    # examples (even ones that can deploy as a Flight) live at the repo root and
    # may carry the flights feature there.
    if under_flight_plans:
        if item_type != "template":
            raise CatalogError(
                f"{readme_path}: items under {FLIGHT_PLANS_DIR}/ must be type 'template' "
                f"(reusable Flight templates); concrete examples live at the repo root"
            )
        if "flights" not in features:
            raise CatalogError(
                f"{readme_path}: items under {FLIGHT_PLANS_DIR}/ must include the flights feature"
            )

    return {
        "id": item_id,
        "type": item_type,
        "title": assert_string(frontmatter, readme_path, "title"),
        "description": assert_string(frontmatter, readme_path, "description"),
        "prompt": prompt,
        "category": category,
        "features": features,
        "tags": tags,
        "published_date": published_date,
        "path": relative_dir,
        "urls": build_urls(repo=repo, ref=ref, path=relative_dir),
    }


def build_urls(repo: str, ref: str, path: str) -> dict[str, str]:
    quoted_ref = quote(ref, safe="")
    quoted_path = quote(path, safe="/")
    return {
        "github": f"https://github.com/{repo}/tree/{quoted_ref}/{quoted_path}",
        "raw_docs": f"https://raw.githubusercontent.com/{repo}/{quoted_ref}/{quoted_path}/README.md",
    }


def build_catalog(
    repo_root: Path = REPO_ROOT,
    *,
    repo: str = "motherduckdb/motherduck-cookbook",
    ref: str = "main",
    generated_at: str | None = None,
    schema_url: str = "catalog.schema.json",
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    generated_at = generated_at or datetime.now(UTC).replace(
        microsecond=0
    ).isoformat().replace("+00:00", "Z")

    items: list[dict[str, Any]] = []
    for readme_path in iter_readmes(repo_root):
        frontmatter = read_frontmatter(readme_path)
        if frontmatter is None:
            continue
        item = build_item(repo_root, readme_path, frontmatter, repo=repo, ref=ref)
        if item is not None:
            items.append(item)

    seen_ids: set[str] = set()
    for item in items:
        if item["id"] in seen_ids:
            raise CatalogError(f"duplicate id {item['id']!r}")
        seen_ids.add(item["id"])

    return {
        "$schema": schema_url,
        "schema_version": 1,
        "generated_at": generated_at,
        "source": {"repo": repo, "ref": ref},
        "items": sorted(items, key=lambda item: (item["type"], item["id"])),
    }


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate README front matter and build the examples catalog."
    )
    parser.add_argument(
        "--repo-root", type=Path, default=REPO_ROOT, help="Repository root to scan."
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write catalog JSON to this path. Omit to validate only.",
    )
    parser.add_argument(
        "--repo",
        default="motherduckdb/motherduck-cookbook",
        help="GitHub repository owner/name.",
    )
    parser.add_argument(
        "--ref", default="main", help="Git ref used for generated URLs."
    )
    parser.add_argument(
        "--schema-url",
        default="catalog.schema.json",
        help="Schema URL embedded in the catalog.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        catalog = build_catalog(
            args.repo_root, repo=args.repo, ref=args.ref, schema_url=args.schema_url
        )
    except CatalogError as error:
        print(error, file=sys.stderr)
        return 1

    if args.output:
        output = json.dumps(catalog, indent=2, sort_keys=False) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output, encoding="utf-8")
        print(f"Wrote {len(catalog['items'])} item(s) to {args.output}")
    else:
        print(f"Validated {len(catalog['items'])} catalog item(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
