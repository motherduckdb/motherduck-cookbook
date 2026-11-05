from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


WORKFLOW_PATH = (
    Path(__file__).resolve().parents[1] / ".github" / "workflows" / "catalog.yml"
)


def load_workflow() -> dict[str, Any]:
    return yaml.load(WORKFLOW_PATH.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)


def test_main_push_publishes_catalog_release_instead_of_pages() -> None:
    workflow_text = WORKFLOW_PATH.read_text(encoding="utf-8")
    workflow = load_workflow()

    for pages_action in (
        "actions/configure-pages",
        "actions/upload-pages-artifact",
        "actions/deploy-pages",
    ):
        assert pages_action not in workflow_text

    # Every item's `files` manifest changes with any file edit, so the release
    # must rebuild on every push to main rather than only on README changes.
    push_trigger = workflow["on"]["push"]
    assert push_trigger == {"branches": ["main"]}

    jobs = workflow["jobs"]
    release = jobs["release"]
    assert release["name"] == "Publish catalog release"
    assert release["needs"] == "validate"
    assert release["if"] == (
        "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )
    assert release["permissions"] == {"contents": "write"}

    steps_by_name = {step["name"]: step for step in release["steps"]}

    build_step = steps_by_name["Build release catalog"]
    assert "--output .catalog-release/catalog.json" in build_step["run"]
    assert '--ref "${GITHUB_SHA}"' in build_step["run"]

    validate_step = steps_by_name["Validate release catalog schema"]
    assert ".catalog-release/catalog.json" in validate_step["run"]

    publish_step = steps_by_name["Create catalog release"]
    assert publish_step["env"]["GH_TOKEN"] == "${{ github.token }}"
    assert "gh release create" in publish_step["run"]
    assert '"./.catalog-release/catalog.json#catalog.json"' in publish_step["run"]
    assert '--target "${GITHUB_SHA}"' in publish_step["run"]
    assert "--latest" in publish_step["run"]
