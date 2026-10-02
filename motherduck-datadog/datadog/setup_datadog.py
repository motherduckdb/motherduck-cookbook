#!/usr/bin/env python3
"""Create or update the MotherDuck dashboard and monitors in Datadog.

Idempotent: the dashboard is matched by title and monitors by name, so re-running
after editing the JSON files updates them in place instead of creating copies.

Usage:
    export DD_API_KEY=...   # API key
    export DD_APP_KEY=...   # application key with dashboards_write + monitors_write
    export DD_SITE=datadoghq.com   # or datadoghq.eu, us3.datadoghq.com, us5.datadoghq.com, ap1.datadoghq.com
    uv run --with httpx datadog/setup_datadog.py                 # dashboard + monitors
    uv run --with httpx datadog/setup_datadog.py --no-monitors   # dashboard only
    uv run --with httpx datadog/setup_datadog.py --prefix md_prod  # if METRIC_PREFIX was changed
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
DEFAULT_PREFIX = "motherduck"


def client() -> httpx.Client:
    api_key, app_key = os.environ.get("DD_API_KEY"), os.environ.get("DD_APP_KEY")
    if not api_key or not app_key:
        raise SystemExit("Set DD_API_KEY and DD_APP_KEY (the app key needs dashboards_write and monitors_write).")
    site = os.environ.get("DD_SITE", "datadoghq.com")
    return httpx.Client(
        base_url=f"https://api.{site}",
        headers={"DD-API-KEY": api_key, "DD-APPLICATION-KEY": app_key, "Content-Type": "application/json"},
        timeout=60,
    )


def check(response: httpx.Response) -> dict:
    if response.status_code >= 400:
        raise SystemExit(f"{response.request.method} {response.request.url} -> {response.status_code}: {response.text[:500]}")
    return response.json()


def load(path: Path, prefix: str):
    text = path.read_text()
    if prefix != DEFAULT_PREFIX:
        text = text.replace(f"{DEFAULT_PREFIX}.", f"{prefix}.")
    return json.loads(text)


def upsert_dashboard(dd: httpx.Client, dashboard: dict) -> str:
    existing = [d for d in check(dd.get("/api/v1/dashboard"))["dashboards"] if d["title"] == dashboard["title"]]
    if len(existing) > 1:
        raise SystemExit(f"{len(existing)} dashboards are titled {dashboard['title']!r}; rename or delete the extras first.")
    if existing:
        result = check(dd.put(f"/api/v1/dashboard/{existing[0]['id']}", json=dashboard))
        action = "updated"
    else:
        result = check(dd.post("/api/v1/dashboard", json=dashboard))
        action = "created"
    url = f"https://app.{os.environ.get('DD_SITE', 'datadoghq.com')}{result['url']}"
    print(f"{action} dashboard {dashboard['title']!r}: {url}")
    return url


def upsert_monitors(dd: httpx.Client, monitors: list[dict]) -> None:
    for monitor in monitors:
        matches = [m for m in check(dd.get("/api/v1/monitor", params={"name": monitor["name"]}))
                   if m["name"] == monitor["name"]]
        if len(matches) > 1:
            raise SystemExit(f"{len(matches)} monitors are named {monitor['name']!r}; delete the extras first.")
        if matches:
            result = check(dd.put(f"/api/v1/monitor/{matches[0]['id']}", json=monitor))
            action = "updated"
        else:
            result = check(dd.post("/api/v1/monitor", json=monitor))
            action = "created"
        print(f"{action} monitor {monitor['name']!r} (id {result['id']})")


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dashboard", default=HERE / "dashboard.json", type=Path)
    parser.add_argument("--monitors", default=HERE / "monitors.json", type=Path)
    parser.add_argument("--no-dashboard", action="store_true")
    parser.add_argument("--no-monitors", action="store_true")
    parser.add_argument("--prefix", default=DEFAULT_PREFIX, help="METRIC_PREFIX used by the Flight")
    args = parser.parse_args(argv)

    with client() as dd:
        if not args.no_dashboard:
            upsert_dashboard(dd, load(args.dashboard, args.prefix))
        if not args.no_monitors:
            upsert_monitors(dd, load(args.monitors, args.prefix))


if __name__ == "__main__":
    main(sys.argv[1:])
