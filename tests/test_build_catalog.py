from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "build-catalog.py"
SCHEMA_PATH = Path(__file__).resolve().parents[1] / "catalog.schema.json"
spec = importlib.util.spec_from_file_location("build_catalog", SCRIPT_PATH)
assert spec is not None
build_catalog_module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(build_catalog_module)


def write_readme(path: Path, frontmatter: str, body: str = "# Example\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter.strip()}\n---\n\n{body}", encoding="utf-8")


def test_build_catalog_includes_frontmatter_readmes(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "python-ingestion" / "README.md",
        """
title: Ingest API data into MotherDuck with Python
id: python-ingestion
description: Load API data into MotherDuck from Python.
type: example
category: ingestion
features: []
tags:
  - python
  - pyarrow
""",
    )
    write_readme(
        repo_root / ".context" / "ignored" / "README.md",
        """
title: Ignored
id: ignored
description: Hidden workspaces should not be indexed.
type: example
""",
    )
    (repo_root / "README.md").write_text(
        "# Root README without catalog metadata\n", encoding="utf-8"
    )

    catalog = build_catalog_module.build_catalog(
        repo_root,
        repo="motherduckdb/motherduck-examples",
        ref="main",
        generated_at="2026-06-03T10:00:00Z",
    )

    assert catalog["schema_version"] == 1
    assert catalog["generated_at"] == "2026-06-03T10:00:00Z"
    assert catalog["source"] == {
        "repo": "motherduckdb/motherduck-examples",
        "ref": "main",
    }
    assert catalog["items"] == [
        {
            "id": "python-ingestion",
            "type": "example",
            "title": "Ingest API data into MotherDuck with Python",
            "description": "Load API data into MotherDuck from Python.",
            "category": "ingestion",
            "features": [],
            "tags": ["python", "pyarrow"],
            "path": "python-ingestion",
            "urls": {
                "github": "https://github.com/motherduckdb/motherduck-examples/tree/main/python-ingestion",
                "raw_docs": "https://raw.githubusercontent.com/motherduckdb/motherduck-examples/main/python-ingestion/README.md",
            },
        }
    ]


def test_catalog_false_skips_readme_before_required_field_validation(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "draft" / "README.md",
        """
catalog: false
""",
    )

    catalog = build_catalog_module.build_catalog(
        repo_root, generated_at="2026-06-03T10:00:00Z"
    )

    assert catalog["items"] == []


def test_duplicate_ids_fail(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    for folder in ("examples/duplicate", "templates/duplicate"):
        write_readme(
            repo_root / folder / "README.md",
            """
title: Duplicate
id: duplicate
description: Duplicate IDs should not be allowed.
type: example
category: integrations
""",
        )

    with pytest.raises(
        build_catalog_module.CatalogError, match="duplicate id 'duplicate'"
    ):
        build_catalog_module.build_catalog(repo_root)


def test_unknown_frontmatter_keys_fail(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "python-ingestion" / "README.md",
        """
title: Ingest API data into MotherDuck with Python
id: python-ingestion
description: Load API data into MotherDuck from Python.
type: example
category: ingestion
categories:
  - ingestion
""",
    )

    with pytest.raises(
        build_catalog_module.CatalogError, match="unknown front matter keys"
    ):
        build_catalog_module.build_catalog(repo_root)


def test_flights_feature_allowed_at_root(tmp_path: Path) -> None:
    # Standalone examples that can also deploy as a Flight live at the repo root
    # and may carry the flights feature there.
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "dbt-ingestion-s3" / "README.md",
        """
title: Build Hacker News Models From S3 With dbt
id: dbt-ingestion-s3
description: A dbt example that can deploy as a Flight.
type: example
category: ingestion
features:
  - flights
tags:
  - dbt
""",
    )

    catalog = build_catalog_module.build_catalog(repo_root)
    assert catalog["items"][0]["features"] == ["flights"]


def test_flight_plans_requires_template_type(tmp_path: Path) -> None:
    # flight-plans/ is only for reusable Flight templates; an example there fails.
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "flight-plans" / "dbt-ingestion-s3" / "README.md",
        """
title: Build Hacker News Models From S3 With dbt
id: dbt-ingestion-s3
description: A concrete example should not live under flight-plans.
type: example
category: ingestion
features:
  - flights
tags:
  - dbt
""",
    )

    with pytest.raises(
        build_catalog_module.CatalogError,
        match="must be type 'template'",
    ):
        build_catalog_module.build_catalog(repo_root)


def test_generated_catalog_matches_schema(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "flight-plans" / "dbt-runner" / "README.md",
        """
title: Run Any dbt Project as a MotherDuck Flight
id: dbt-runner
description: Run dbt as a MotherDuck Flight.
type: template
category: automation
features:
  - flights
tags:
  - dbt
""",
    )

    catalog = build_catalog_module.build_catalog(
        repo_root, generated_at="2026-06-03T10:00:00Z"
    )
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(catalog)


def test_missing_category_fails(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "python-ingestion" / "README.md",
        """
title: Ingest API data into MotherDuck with Python
id: python-ingestion
description: Load API data into MotherDuck from Python.
type: example
features: []
tags: [python]
""",
    )

    with pytest.raises(
        build_catalog_module.CatalogError, match="missing required keys .*category"
    ):
        build_catalog_module.build_catalog(repo_root)


def test_unknown_category_fails(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "python-ingestion" / "README.md",
        """
title: Ingest API data into MotherDuck with Python
id: python-ingestion
description: Load API data into MotherDuck from Python.
type: example
category: etl
features: []
tags: [python]
""",
    )

    with pytest.raises(
        build_catalog_module.CatalogError, match="category must be one of"
    ):
        build_catalog_module.build_catalog(repo_root)
