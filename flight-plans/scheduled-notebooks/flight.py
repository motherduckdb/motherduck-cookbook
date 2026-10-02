import base64
import json
import os
import urllib.request
import uuid

import duckdb

TOKEN = os.environ["MOTHERDUCK_TOKEN"]
CLAIMS = TOKEN.split(".")[1]
CLOUD, REGION = json.loads(base64.urlsafe_b64decode(CLAIMS + "=" * (-len(CLAIMS) % 4)))["mdRegion"].split("-", 1)
API = f"https://api.{REGION}-{CLOUD}.motherduck.com/mom/notebooks"


def get(url):
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(request, timeout=30) as resp:
        return json.load(resp)


ref = os.environ["NOTEBOOK"]
try:
    notebook_id = uuid.UUID(ref)
except ValueError:
    ids = [n["id"] for n in get(API)["notebooks"] if n["title"] == ref]
    if len(ids) != 1:
        raise SystemExit(f"Expected 1 notebook titled {ref!r}, found {len(ids)}")
    notebook_id = ids[0]

cells = json.loads(get(f"{API}/{notebook_id}")["notebook"]["json"])["cells"]
con = duckdb.connect("md:")
for cell in cells:
    sql, db = cell.get("query") or "", cell.get("useDatabase")
    print(sql, flush=True)
    if db:
        con.execute('USE "' + db.replace('"', '""') + '"')
    if (result := con.sql(sql)) is not None:
        while result.fetchmany(100_000):
            pass
