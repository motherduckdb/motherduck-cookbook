"""Render a MotherDuck Dive in headless Chromium and deliver the PNG + PDF.

Dives have no native PDF export yet, so this Flight mints a Dive embed session,
opens it in headless Chromium on MotherDuck compute, and captures a PNG and a
single-page PDF. The renditions are stored in MotherDuck as BLOBs and handed to
whichever delivery targets `DELIVERY` names.

Every knob is a config value or an env var; see the README "What you'll adjust"
table. Credentials arrive as Flight secret params under their bare names.
"""

import os
import re
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import quote

import duckdb
import httpx

# The REST API is scoped to your organization's region, so one hostname works
# for every region. Override API_BASE only for a non-production environment.
API_BASE = os.environ.get("API_BASE", "https://api.motherduck.com").rstrip("/")
SANDBOX_BASE = "https://embed-motherduck.com/sandbox/#session="

IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
MIME_TYPES = {"png": "image/png", "pdf": "application/pdf"}

SLACK_API = "https://slack.com/api"
GRAPH_API = "https://graph.microsoft.com/v1.0"
ENTRA_LOGIN = "https://login.microsoftonline.com"
# A Graph upload session wants every chunk except the last to be a multiple of
# 320 KiB. 5 MiB is 16 of those blocks.
GRAPH_CHUNK_BYTES = 5 * 1024 * 1024


@dataclass
class Rendition:
    """One captured file: what it is and the bytes themselves."""

    kind: str
    filename: str
    mime: str
    content: bytes


@dataclass
class Export:
    """One capture run: the renditions plus the text that describes them."""

    label: str
    source_url: str
    captured_at: datetime
    title: str
    message: str
    renditions: list[Rendition]


def log(*parts: object) -> None:
    # Flight logs are line-buffered per write, so flush as we go: a run that
    # fails halfway still shows how far it got.
    print(*parts, flush=True)


def main() -> None:
    token = env("MOTHERDUCK_TOKEN")
    dive_id = env("DIVE_ID")
    service_account = env("SERVICE_ACCOUNT")
    shot_url = env("SHOT_URL")
    report_name = env("REPORT_NAME", "Dive export")
    store_table = env("STORE_TABLE", "flights_demo.main.dive_exports")
    kinds = env_list("ATTACH", "pdf,png")
    targets = env_list("DELIVERY", "")
    dry_run = env("DRY_RUN", "false").lower() == "true"
    wait_ms = env_int("WAIT_MS", 15000)
    scale = float(env("SCALE", "2"))
    width, height = parse_viewport(env("VIEWPORT", "1440x2600"))

    unknown = [kind for kind in kinds if kind not in MIME_TYPES]
    if unknown or not kinds:
        raise ValueError(f"ATTACH must be a subset of png,pdf; got {kinds or ['']}")
    # Fail on a typo or a missing credential now, not after a 90-second render.
    check_delivery_config(targets)

    if shot_url:
        # Debugging path: shoot any URL, no Dive and no service account needed.
        url, label = shot_url, env("LABEL", "adhoc")
    elif dive_id and service_account:
        url, label = mint_embed_session(dive_id, service_account, token), dive_id
    else:
        raise ValueError("set DIVE_ID + SERVICE_ACCOUNT, or SHOT_URL for a debug run")

    install_chromium()
    captured_at = datetime.now(timezone.utc)
    shots = capture(url, wait_ms, (width, height), scale)

    export = Export(
        label=label,
        source_url=url.split("#")[0],  # the fragment carries the session token
        captured_at=captured_at,
        title=report_name,
        message=env("MESSAGE") or default_message(report_name, captured_at),
        renditions=[
            Rendition(
                kind=kind,
                filename=f"{slugify(report_name)}-{captured_at:%Y%m%d-%H%M}.{kind}",
                mime=MIME_TYPES[kind],
                content=shots[kind],
            )
            for kind in kinds
        ],
    )

    if store_table:
        store(store_table, export)
    else:
        log("STORE_TABLE is empty; skipping the MotherDuck copy.")

    # Deliver after the store, so a broken delivery target still leaves the
    # rendered file somewhere you can get at it.
    if not targets:
        log("DELIVERY is empty; nothing to send.")
    for target in targets:
        DELIVERY_TARGETS[target]["deliver"](export, dry_run)


def mint_embed_session(dive_id: str, username: str, token: str) -> str:
    """Trade an admin token for a 24h embed session URL for one Dive.

    The Dive is rendered as `username`, which must be a service account: the
    endpoint rejects a regular user account with a generic 404.
    """
    if not token:
        raise RuntimeError(
            "No MOTHERDUCK_TOKEN in the environment. A deployed Flight is given "
            "one automatically; export it yourself for a local run."
        )

    response = httpx.post(
        f"{API_BASE}/v1/dives/{dive_id}/embed-session",
        headers={"Authorization": f"Bearer {token}"},
        json={"username": username},
        timeout=30,
    )
    if response.status_code == 404:
        raise RuntimeError(
            f"embed-session returned 404 for dive {dive_id} as {username!r}. "
            "Check the Dive id, and that the username is a service account."
        )
    response.raise_for_status()
    session = response.json()["session"]
    log(f"embed session minted ({len(session)} chars)")
    return SANDBOX_BASE + session


def install_chromium() -> None:
    """Download the Chromium build Playwright expects, into the Flight container.

    The container starts without a browser, so the first run pays for the
    download (roughly a minute). `--with-deps` needs root, which the Flight
    runtime gives us; the plain install is the fallback for anywhere else.
    """
    for args in (
        ["playwright", "install", "--with-deps", "chromium"],
        ["playwright", "install", "chromium"],
    ):
        started = time.time()
        proc = subprocess.run(args, capture_output=True, text=True)
        log(f"{' '.join(args)} -> rc={proc.returncode} in {time.time() - started:.0f}s")
        if proc.returncode == 0:
            return
        log("stderr tail:", proc.stderr[-1500:])
    raise RuntimeError("could not install chromium")


def capture(
    url: str, wait_ms: int, viewport: tuple[int, int], scale: float
) -> dict[str, bytes]:
    """Open the URL and return {"png": ..., "pdf": ...}."""
    from playwright.sync_api import sync_playwright

    width, height = viewport
    with sync_playwright() as play:
        browser = play.chromium.launch(
            args=[
                # No sandbox and no /dev/shm: the container is unprivileged and
                # its shared memory is too small for Chromium's default.
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--font-render-hinting=none",
            ]
        )
        page = browser.new_page(
            viewport={"width": width, "height": height},
            device_scale_factor=scale,
        )
        page.on("console", lambda msg: log(f"[console:{msg.type}] {msg.text[:300]}"))
        page.on("pageerror", lambda err: log(f"[pageerror] {str(err)[:300]}"))

        log("navigating...")
        # Do not wait for networkidle: the wasm client holds a connection open,
        # so it never fires. A fixed settle window is what works.
        page.goto(url, wait_until="domcontentloaded", timeout=90_000)
        log(f"loaded, settling {wait_ms}ms so the Dive's queries can finish")
        page.wait_for_timeout(wait_ms)
        log("title:", page.title())

        png = page.screenshot(full_page=True)
        log(f"png: {len(png)} bytes")

        # One page sized to the content, so charts are never split in half.
        content_height = page.evaluate(
            "() => Math.max(document.body.scrollHeight,"
            " document.documentElement.scrollHeight)"
        )
        pdf = page.pdf(
            width=f"{width}px",
            height=f"{int(content_height) + 40}px",
            print_background=True,
            margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
        )
        log(f"pdf: {len(pdf)} bytes (content height {content_height}px)")

        browser.close()
    return {"png": png, "pdf": pdf}


def store(store_table: str, export: Export) -> None:
    """Append one row per rendition to the export table, creating it if needed."""
    database, schema, _table = split_table(store_table)
    con = duckdb.connect("md:")
    con.execute(f"CREATE DATABASE IF NOT EXISTS {database}")
    con.execute(f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")
    con.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {store_table} (
            captured_at TIMESTAMPTZ,
            label       VARCHAR,
            source_url  VARCHAR,
            kind        VARCHAR,
            filename    VARCHAR,
            mime        VARCHAR,
            byte_count  BIGINT,
            content     BLOB
        )
        """
    )
    row_sql = "(?, ?, ?, ?, ?, ?, ?, ?)"
    params: list = []
    for rendition in export.renditions:
        params.extend(
            [
                export.captured_at,
                export.label,
                export.source_url,
                rendition.kind,
                rendition.filename,
                rendition.mime,
                len(rendition.content),
                rendition.content,
            ]
        )
    con.execute(
        f"INSERT INTO {store_table} VALUES "
        + ", ".join([row_sql] * len(export.renditions)),
        params,
    )
    con.close()
    log(f"stored {len(export.renditions)} row(s) in {store_table}")


def check_delivery_config(targets: list[str]) -> None:
    """Reject unknown targets and missing credentials before anything renders."""
    unknown = [target for target in targets if target not in DELIVERY_TARGETS]
    if unknown:
        raise ValueError(
            f"DELIVERY names unknown target(s) {unknown}; "
            f"choose from {sorted(DELIVERY_TARGETS)}"
        )
    missing = [
        name
        for target in targets
        for name in DELIVERY_TARGETS[target]["requires"]
        if not env(name)
    ]
    if missing:
        raise RuntimeError(
            f"DELIVERY={','.join(targets)} needs {missing} in the environment. "
            "Attach them as Flight secret params, or export them for a local run."
        )


def deliver_slack(export: Export, dry_run: bool) -> None:
    """Upload each rendition to a Slack channel as a real file.

    `files.upload` is retired, so this is the three-step external upload flow:
    reserve a URL, POST the bytes to it, then complete the upload and share the
    files into the channel with one comment.
    """
    token = env("SLACK_BOT_TOKEN")
    channel = env("SLACK_CHANNEL_ID")
    headers = {"Authorization": f"Bearer {token}"}

    if dry_run:
        log(f"[dry run] slack: would upload {filenames(export)} to {channel}")
        return

    uploaded = []
    for rendition in export.renditions:
        reserved = slack_api(
            httpx.post(
                f"{SLACK_API}/files.getUploadURLExternal",
                headers=headers,
                data={
                    "filename": rendition.filename,
                    "length": len(rendition.content),
                },
                timeout=30,
            )
        )
        # The upload URL is pre-authorized: no bearer token on this request.
        response = httpx.post(
            reserved["upload_url"],
            files={"file": (rendition.filename, rendition.content, rendition.mime)},
            timeout=300,
        )
        response.raise_for_status()
        uploaded.append({"id": reserved["file_id"], "title": rendition.filename})

    slack_api(
        httpx.post(
            f"{SLACK_API}/files.completeUploadExternal",
            headers=headers,
            json={
                "files": uploaded,
                "channel_id": channel,
                "initial_comment": f"*{export.title}*\n{export.message}",
            },
            timeout=30,
        )
    )
    log(f"slack: uploaded {filenames(export)} to {channel}")


def deliver_teams(export: Export, dry_run: bool) -> None:
    """Put each rendition in the channel's Files tab, then announce it.

    Teams has no file-carrying webhook either, and posting a channel message
    with an app-only token is restricted to migration scenarios. So the file
    goes to the SharePoint folder behind the channel through Microsoft Graph
    (where it shows up in the channel's Files tab), and the optional
    `TEAMS_WEBHOOK_URL` posts an Adaptive Card linking to it.
    """
    team_id = env("TEAMS_TEAM_ID")
    channel_id = env("TEAMS_CHANNEL_ID")
    webhook = env("TEAMS_WEBHOOK_URL")

    if dry_run:
        log(
            f"[dry run] teams: would upload {filenames(export)} to the Files tab "
            f"of channel {channel_id}"
            + (" and post a card" if webhook else " (no webhook, no card)")
        )
        return

    token = graph_token(
        env("TEAMS_TENANT_ID"), env("TEAMS_CLIENT_ID"), env("TEAMS_CLIENT_SECRET")
    )
    drive_id, folder_id = teams_files_folder(token, team_id, channel_id)
    links = [
        (rendition.filename, graph_upload(token, drive_id, folder_id, rendition))
        for rendition in export.renditions
    ]
    log(f"teams: uploaded {filenames(export)} to the Files tab of {channel_id}")

    if not webhook:
        log("teams: TEAMS_WEBHOOK_URL is unset; skipping the channel card.")
        return
    post_teams_card(webhook, export, links)
    log("teams: posted the channel card")


def graph_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    """Client-credentials token for Microsoft Graph (no user is signed in)."""
    response = httpx.post(
        f"{ENTRA_LOGIN}/{tenant_id}/oauth2/v2.0/token",
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        # The body names the missing consent or the wrong tenant, and carries no
        # secret of ours, so it is worth surfacing.
        raise RuntimeError(f"Entra token request failed: {response.text[:500]}")
    return response.json()["access_token"]


def teams_files_folder(token: str, team_id: str, channel_id: str) -> tuple[str, str]:
    """Resolve the drive and folder that back a channel's Files tab."""
    response = httpx.get(
        f"{GRAPH_API}/teams/{team_id}/channels/{channel_id}/filesFolder",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    graph_ok(response, "resolve the channel Files folder")
    folder = response.json()
    return folder["parentReference"]["driveId"], folder["id"]


def graph_upload(
    token: str, drive_id: str, folder_id: str, rendition: Rendition
) -> str:
    """Upload one rendition through an upload session; return its webUrl.

    An upload session handles any size, so there is no 4 MB simple-upload cliff
    to fall off when a PNG grows.
    """
    session = httpx.post(
        f"{GRAPH_API}/drives/{drive_id}/items/{folder_id}:/{quote(rendition.filename)}:"
        "/createUploadSession",
        headers={"Authorization": f"Bearer {token}"},
        json={"item": {"@microsoft.graph.conflictBehavior": "replace"}},
        timeout=60,
    )
    graph_ok(session, f"create an upload session for {rendition.filename}")
    upload_url = session.json()["uploadUrl"]

    total = len(rendition.content)
    response = None
    for start in range(0, total, GRAPH_CHUNK_BYTES):
        chunk = rendition.content[start : start + GRAPH_CHUNK_BYTES]
        end = start + len(chunk) - 1
        # The upload URL carries its own authorization; adding ours breaks it.
        response = httpx.put(
            upload_url,
            content=chunk,
            headers={"Content-Range": f"bytes {start}-{end}/{total}"},
            timeout=300,
        )
        graph_ok(response, f"upload bytes {start}-{end} of {rendition.filename}")
    # The response to the final chunk is the created driveItem.
    return (response.json() or {}).get("webUrl", "")


def post_teams_card(webhook: str, export: Export, links: list[tuple[str, str]]) -> None:
    """Announce the upload in the channel with an Adaptive Card."""
    body = [
        {
            "type": "TextBlock",
            "text": export.title,
            "weight": "Bolder",
            "size": "Medium",
            "wrap": True,
        },
        {"type": "TextBlock", "text": export.message, "wrap": True},
    ]
    actions = [
        {"type": "Action.OpenUrl", "title": f"Open {name}", "url": url}
        for name, url in links
        if url
    ]
    response = httpx.post(
        webhook,
        json={
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "content": {
                        "type": "AdaptiveCard",
                        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                        "version": "1.4",
                        "body": body,
                        "actions": actions,
                    },
                }
            ],
        },
        timeout=30,
    )
    response.raise_for_status()


def graph_ok(response: httpx.Response, what: str) -> None:
    """Raise with the Graph error body, which says which permission is missing."""
    if response.status_code >= 400:
        raise RuntimeError(
            f"Microsoft Graph could not {what}: "
            f"HTTP {response.status_code} {response.text[:500]}"
        )


def slack_api(response: httpx.Response) -> dict:
    """Slack answers 200 OK with `ok: false` on failure, so check both."""
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(f"Slack API error: {payload.get('error') or payload}")
    return payload


def filenames(export: Export) -> str:
    return ", ".join(rendition.filename for rendition in export.renditions)


def default_message(report_name: str, captured_at: datetime) -> str:
    return f"{report_name} captured {captured_at:%Y-%m-%d %H:%M} UTC."


def slugify(value: str) -> str:
    """Turn a report name into a filename-safe slug."""
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return slug or "dive-export"


def parse_viewport(value: str) -> tuple[int, int]:
    match = re.fullmatch(r"\s*(\d+)\s*[xX]\s*(\d+)\s*", value)
    if not match:
        raise ValueError(f"VIEWPORT must look like 1440x2600, got {value!r}")
    return int(match.group(1)), int(match.group(2))


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def env_int(name: str, default: int) -> int:
    value = env(name)
    return int(value) if value else default


def env_list(name: str, default: str) -> list[str]:
    parts = env(name, default).split(",")
    return [part.strip().lower() for part in parts if part.strip()]


def split_table(value: str) -> tuple[str, str, str]:
    """Validate a database.schema.table name before it is interpolated into SQL.

    Table names cannot be bound as parameters, so each part has to be a plain
    SQL identifier for the CREATE and INSERT statements to be safe.
    """
    parts = value.split(".")
    if len(parts) != 3:
        raise ValueError(f"STORE_TABLE must be 'database.schema.table', got {value!r}")
    for part in parts:
        if not IDENTIFIER_RE.fullmatch(part):
            raise ValueError(f"STORE_TABLE part must be an identifier, got {part!r}")
    return parts[0], parts[1], parts[2]


# Delivery targets, keyed by the names DELIVERY accepts. `requires` is checked
# before the render so a missing secret fails the run in seconds.
DELIVERY_TARGETS = {
    "slack": {
        "deliver": deliver_slack,
        "requires": ("SLACK_BOT_TOKEN", "SLACK_CHANNEL_ID"),
    },
    "teams": {
        "deliver": deliver_teams,
        # TEAMS_WEBHOOK_URL is optional: without it the file still lands in the
        # channel's Files tab, it is just not announced.
        "requires": (
            "TEAMS_TENANT_ID",
            "TEAMS_CLIENT_ID",
            "TEAMS_CLIENT_SECRET",
            "TEAMS_TEAM_ID",
            "TEAMS_CHANNEL_ID",
        ),
    },
}


if __name__ == "__main__":
    main()
