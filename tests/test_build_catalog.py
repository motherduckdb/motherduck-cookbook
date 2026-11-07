from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
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
prompt: Adapt this Python API ingestion recipe to my own data and use case.
published_date: 2025-03-14
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
        repo="motherduckdb/motherduck-cookbook",
        ref="main",
        generated_at="2026-06-03T10:00:00Z",
    )

    assert catalog["schema_version"] == 1
    assert catalog["generated_at"] == "2026-06-03T10:00:00Z"
    assert catalog["source"] == {
        "repo": "motherduckdb/motherduck-cookbook",
        "ref": "main",
    }
    assert catalog["items"] == [
        {
            "id": "python-ingestion",
            "type": "example",
            "title": "Ingest API data into MotherDuck with Python",
            "description": "Load API data into MotherDuck from Python.",
            "prompt": "Adapt this Python API ingestion recipe to my own data and use case.",
            "category": "ingestion",
            "features": [],
            "tags": ["python", "pyarrow"],
            "published_date": "2025-03-14",
            "featured": False,
            "path": "python-ingestion",
            "urls": {
                "github": "https://github.com/motherduckdb/motherduck-cookbook/tree/main/python-ingestion",
                "raw_docs": "https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/python-ingestion/README.md",
            },
            "files": [
                {
                    "path": "README.md",
                    "bytes": (repo_root / "python-ingestion" / "README.md").stat().st_size,
                }
            ],
        }
    ]


FILES_FRONTMATTER = """
title: Ingest API data into MotherDuck with Python
id: python-ingestion
description: Load API data into MotherDuck from Python.
type: example
category: ingestion
features: []
tags: []
prompt: Adapt this Python API ingestion recipe to my own data and use case.
published_date: 2025-03-14
"""


def test_files_manifest_walks_folder_outside_git(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    item_dir = repo_root / "python-ingestion"
    write_readme(item_dir / "README.md", FILES_FRONTMATTER)
    (item_dir / "ingest.py").write_text("print('hi')\n", encoding="utf-8")
    (item_dir / "data").mkdir()
    (item_dir / "data" / ".gitkeep").write_text("", encoding="utf-8")
    (item_dir / "data" / "sample.csv").write_text("a,b\n1,2\n", encoding="utf-8")
    (item_dir / "node_modules" / "dep").mkdir(parents=True)
    (item_dir / "node_modules" / "dep" / "index.js").write_text("", encoding="utf-8")
    (item_dir / ".venv").mkdir()
    (item_dir / ".venv" / "pyvenv.cfg").write_text("", encoding="utf-8")

    catalog = build_catalog_module.build_catalog(repo_root)

    assert catalog["items"][0]["files"] == [
        {"path": "README.md", "bytes": (item_dir / "README.md").stat().st_size},
        {"path": "data/sample.csv", "bytes": 8},
        {"path": "ingest.py", "bytes": 12},
    ]


def test_files_manifest_follows_git_when_available(tmp_path: Path) -> None:
    git = shutil.which("git")
    if git is None:
        pytest.skip("git is not installed")

    repo_root = tmp_path / "repo"
    item_dir = repo_root / "python-ingestion"
    write_readme(item_dir / "README.md", FILES_FRONTMATTER)
    (item_dir / "tracked.py").write_text("tracked\n", encoding="utf-8")
    (repo_root / ".gitignore").write_text("*.log\n", encoding="utf-8")
    subprocess.run([git, "init", "-q"], cwd=repo_root, check=True)
    subprocess.run([git, "add", "."], cwd=repo_root, check=True)

    (item_dir / "untracked.py").write_text("untracked\n", encoding="utf-8")
    (item_dir / "debug.log").write_text("ignored\n", encoding="utf-8")

    catalog = build_catalog_module.build_catalog(repo_root)

    assert [entry["path"] for entry in catalog["items"][0]["files"]] == [
        "README.md",
        "tracked.py",
        "untracked.py",
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
prompt: Adapt this recipe to my own data and use case.
published_date: 2025-01-01
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
prompt: Adapt this dbt-on-S3 recipe to my own data and use case.
published_date: 2025-02-02
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
prompt: Adapt this recipe to my own data and use case.
published_date: 2025-02-02
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
prompt: Adapt this dbt Flight recipe to my own data and use case.
published_date: 2025-02-02
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
prompt: Adapt this recipe to my own data and use case.
published_date: 2025-01-01
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
prompt: Adapt this recipe to my own data and use case.
published_date: 2025-01-01
""",
    )

    with pytest.raises(
        build_catalog_module.CatalogError, match="category must be one of"
    ):
        build_catalog_module.build_catalog(repo_root)


def test_missing_prompt_fails(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "a-recipe" / "README.md",
        """
title: A recipe
id: a-recipe
description: A recipe without a prompt.
type: example
category: ingestion
published_date: 2025-07-09
""",
    )

    with pytest.raises(
        build_catalog_module.CatalogError, match="missing required keys .*prompt"
    ):
        build_catalog_module.build_catalog(repo_root)


def test_published_date_normalizes_yaml_date_and_string(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    # An unquoted ISO date is parsed by PyYAML as a datetime.date.
    write_readme(
        repo_root / "a-recipe" / "README.md",
        """
title: A recipe
id: a-recipe
description: Publish date written as an unquoted YAML date.
type: example
category: ingestion
prompt: Adapt this recipe to my own data and use case.
published_date: 2025-07-09
""",
    )
    # A quoted date is parsed as a string.
    write_readme(
        repo_root / "b-recipe" / "README.md",
        '''
title: B recipe
id: b-recipe
description: Publish date written as a quoted string.
type: example
category: ingestion
prompt: Adapt this recipe to my own data and use case.
published_date: "2025-07-10"
''',
    )

    catalog = build_catalog_module.build_catalog(repo_root)
    dates = {item["id"]: item["published_date"] for item in catalog["items"]}

    assert dates == {"a-recipe": "2025-07-09", "b-recipe": "2025-07-10"}
    assert all(isinstance(item["published_date"], str) for item in catalog["items"])


def test_invalid_published_date_fails(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "a-recipe" / "README.md",
        """
title: A recipe
id: a-recipe
description: Publish date that is not a valid ISO date.
type: example
category: ingestion
prompt: Adapt this recipe to my own data and use case.
published_date: not-a-date
""",
    )

    with pytest.raises(
        build_catalog_module.CatalogError, match="must be a YYYY-MM-DD date"
    ):
        build_catalog_module.build_catalog(repo_root)


def test_featured_flag_is_emitted(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "featured-recipe" / "README.md",
        """
title: Featured
id: featured-recipe
description: A featured recipe.
type: example
category: analytics
prompt: Adapt this recipe to my own data and use case.
published_date: 2025-01-01
featured: true
""",
    )

    catalog = build_catalog_module.build_catalog(repo_root)

    assert catalog["items"][0]["featured"] is True


def test_featured_must_be_boolean(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    write_readme(
        repo_root / "featured-recipe" / "README.md",
        """
title: Featured
id: featured-recipe
description: A featured recipe.
type: example
category: analytics
prompt: Adapt this recipe to my own data and use case.
published_date: 2025-01-01
featured: "yes"
""",
    )

    with pytest.raises(
        build_catalog_module.CatalogError, match="featured must be true or false"
    ):
        build_catalog_module.build_catalog(repo_root)


REFS_FRONTMATTER = FILES_FRONTMATTER + """
refs:
  - kind: dataset
    database: sample_data
    tables: [hn.hacker_news]
    title: Hacker News
    docs: /getting-started/sample-data-queries/hacker-news
  - kind: doc
    path: /key-tasks/flights
  - kind: url
    url: https://dlthub.com/docs/intro
    title: dlt docs
  - kind: skill
    id: motherduck-create-flight
  - kind: recipe
    id: python-ingestion
"""


def test_declared_refs_are_emitted_and_match_schema(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    write_readme(repo_root / "python-ingestion" / "README.md", REFS_FRONTMATTER)

    catalog = build_catalog_module.build_catalog(repo_root)

    assert catalog["items"][0]["refs"] == [
        {
            "kind": "dataset",
            "database": "sample_data",
            "tables": ["hn.hacker_news"],
            "title": "Hacker News",
            "docs": "/getting-started/sample-data-queries/hacker-news",
        },
        {"kind": "doc", "path": "/key-tasks/flights"},
        {"kind": "url", "url": "https://dlthub.com/docs/intro", "title": "dlt docs"},
        {"kind": "skill", "id": "motherduck-create-flight"},
        {"kind": "recipe", "id": "python-ingestion"},
    ]
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(catalog)


def test_refs_are_omitted_when_none_are_declared(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    write_readme(repo_root / "python-ingestion" / "README.md", FILES_FRONTMATTER)

    catalog = build_catalog_module.build_catalog(repo_root)

    assert "refs" not in catalog["items"][0]


@pytest.mark.parametrize(
    ("refs_yaml", "message"),
    [
        ("refs: sample_data", "refs must be a list"),
        ("refs:\n  - sample_data", r"refs\[0\] must be a mapping"),
        ("refs:\n  - kind: dive\n    id: d1", "kind must be one of"),
        ("refs:\n  - kind: doc", r"needs \['path'\]"),
        ("refs:\n  - kind: doc\n    path: /x\n    href: /y", "unknown keys"),
        ("refs:\n  - kind: dataset\n    database: sample_data", "needs docs"),
        ("refs:\n  - kind: dataset\n    database: x\n    url: ftp://x", "url must start with"),
        ("refs:\n  - kind: dataset\n    database: x\n    docs: /d\n    tables: x", "tables must be a list"),
        ("refs:\n  - kind: recipe\n    id: not-in-catalog", "not in the catalog"),
    ],
)
def test_invalid_refs_fail(tmp_path: Path, refs_yaml: str, message: str) -> None:
    repo_root = tmp_path / "repo"
    write_readme(repo_root / "python-ingestion" / "README.md", FILES_FRONTMATTER + refs_yaml)

    with pytest.raises(build_catalog_module.CatalogError, match=message):
        build_catalog_module.build_catalog(repo_root)
