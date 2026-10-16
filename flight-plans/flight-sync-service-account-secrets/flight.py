"""Copy a set of DuckDB secrets into every service account in the organization.

MotherDuck secrets belong to the account that created them, so a service
account cannot use an admin's S3 secret. This Flight runs as an admin, mints a
five-minute token for each service account, and runs CREATE OR REPLACE SECRET
as that account, then revokes the token.

Each secret to sync is a Flight secret (type flights) whose parameters are the
CREATE SECRET options, for example TYPE, KEY_ID, SECRET, REGION and SCOPE. It
is created under the same name in every service account.
"""

import os
import re

import duckdb
import httpx

API_BASE = "https://api.motherduck.com"
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def main() -> None:
    admin_token = os.environ["MOTHERDUCK_TOKEN"]
    secret_names = env_list("SECRETS")
    only = set(env_list("SERVICE_ACCOUNTS"))
    dry_run = env_bool("DRY_RUN", True)
    if not secret_names:
        raise ValueError("Set SECRETS to the comma-separated names of the Flight secrets to sync")

    secrets = {name: secret_options(name) for name in secret_names}
    accounts = list_service_accounts(admin_token)
    if only:
        missing = only - set(accounts)
        if missing:
            print(f"not service accounts, skipped: {', '.join(sorted(missing))}")
        accounts = [a for a in accounts if a in only]

    mode = "dry run" if dry_run else "live"
    print(f"syncing {', '.join(secret_names)} into {len(accounts)} service account(s) ({mode})")

    failed = []
    for username in accounts:
        if dry_run:
            print(f"would sync {username}")
            continue
        try:
            sync_account(admin_token, username, secrets)
            print(f"synced {username}")
        except Exception as exc:
            failed.append(username)
            print(f"failed {username}: {redact(str(exc), secrets)}")

    if failed:
        raise SystemExit(f"{len(failed)} of {len(accounts)} service account(s) failed")


def secret_options(name: str) -> dict[str, str]:
    # The runtime injects each Flight secret parameter as <secret name>_<KEY>.
    validate_identifier("secret name", name)
    prefix = name + "_"
    options = {key[len(prefix):]: value for key, value in os.environ.items() if key.startswith(prefix)}
    if "TYPE" not in options:
        raise ValueError(f"Flight secret {name} is not attached to this Flight or has no TYPE parameter")
    for key in options:
        validate_identifier(f"parameter of {name}", key)
    return options


def list_service_accounts(token: str) -> list[str]:
    usernames = []
    params = {"is_service_account": "true", "is_deprovisioned": "false", "limit": 1000}
    while True:
        page = api("GET", "/v1/users", token, params=params)
        usernames += [user["username"] for user in page["users"]]
        if not page.get("next_page_token"):
            return usernames
        params["page_token"] = page["next_page_token"]


def sync_account(admin_token: str, username: str, secrets: dict[str, dict[str, str]]) -> None:
    # 300 seconds is the shortest lifetime the API allows.
    token = api(
        "POST",
        f"/v1/users/{username}/tokens",
        admin_token,
        json={"name": "secret-sync", "token_type": "read_write", "ttl": 300},
    )
    try:
        con = duckdb.connect(f"md:?motherduck_token={token['token']}")
        try:
            for name, options in secrets.items():
                con.execute(create_secret_sql(name, options))
        finally:
            con.close()
    finally:
        api("DELETE", f"/v1/users/{username}/tokens/{token['id']}", admin_token)


def create_secret_sql(name: str, options: dict[str, str]) -> str:
    body = ", ".join(f"{key} {literal(value)}" for key, value in options.items())
    return f"CREATE OR REPLACE SECRET {name} IN MOTHERDUCK ({body})"


def api(method: str, path: str, token: str, **kwargs) -> dict:
    response = httpx.request(
        method,
        API_BASE + path,
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
        **kwargs,
    )
    response.raise_for_status()
    return response.json()


def redact(message: str, secrets: dict[str, dict[str, str]]) -> str:
    # A SQL error can quote the statement, and with it the secret values.
    for options in secrets.values():
        for key, value in options.items():
            if key != "TYPE" and value:
                message = message.replace(literal(value), "'***'").replace(value, "***")
    return message


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def validate_identifier(what: str, value: str) -> None:
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{what} must be a plain SQL identifier, got {value!r}")


def env_list(name: str) -> list[str]:
    return [item.strip() for item in os.environ.get(name, "").split(",") if item.strip()]


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


if __name__ == "__main__":
    main()
