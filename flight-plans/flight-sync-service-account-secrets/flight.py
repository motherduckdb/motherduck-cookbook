"""Copy DuckDB secrets into every service account in the organization.

MotherDuck secrets belong to the account that created them. This Flight runs as
an admin and, for each service account, mints a five-minute token, runs
CREATE OR REPLACE SECRET as that account, and revokes the token.

Each name in SECRETS is a Flight secret whose parameters are the CREATE SECRET
options, for example TYPE, KEY_ID, SECRET, REGION and SCOPE.
"""

import os

import duckdb
import httpx

API = "https://api.motherduck.com/v1"


def main() -> None:
    admin_token = os.environ["MOTHERDUCK_TOKEN"]
    statements = [create_secret_sql(name.strip()) for name in os.environ["SECRETS"].split(",")]

    failed = 0
    for username in service_accounts(admin_token):
        try:
            sync(admin_token, username, statements)
            print(f"synced {username}")
        except Exception as exc:
            failed += 1
            print(f"failed {username}: {exc}")
    if failed:
        raise SystemExit(f"{failed} service account(s) failed")


def create_secret_sql(name: str) -> str:
    # The runtime injects each Flight secret parameter as <secret name>_<KEY>.
    prefix = name + "_"
    options = [(key[len(prefix):], value) for key, value in os.environ.items() if key.startswith(prefix)]
    if not options:
        raise ValueError(f"Flight secret {name} is not attached to this Flight")
    body = ", ".join(f"{key} {literal(value)}" for key, value in options)
    return f"CREATE OR REPLACE SECRET {ident(name)} IN MOTHERDUCK ({body})"


def service_accounts(admin_token: str):
    params = {"is_service_account": "true", "is_deprovisioned": "false"}
    while True:
        page = api("GET", "/users", admin_token, params=params).json()
        yield from (user["username"] for user in page["users"])
        if not page.get("next_page_token"):
            return
        params["page_token"] = page["next_page_token"]


def sync(admin_token: str, username: str, statements: list[str]) -> None:
    token = api(
        "POST",
        f"/users/{username}/tokens",
        admin_token,
        json={"name": "secret-sync", "token_type": "read_write", "ttl": 300},
    ).json()
    try:
        with duckdb.connect(f"md:?motherduck_token={token['token']}") as con:
            for sql in statements:
                con.execute(sql)
    finally:
        api("DELETE", f"/users/{username}/tokens/{token['id']}", admin_token)


def api(method: str, path: str, token: str, **kwargs) -> httpx.Response:
    response = httpx.request(
        method, API + path, headers={"Authorization": f"Bearer {token}"}, timeout=30, **kwargs
    )
    response.raise_for_status()
    return response


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


if __name__ == "__main__":
    main()
