"""Create private development configuration; never overwrite an existing .env."""

import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
values = {
    "POSTGRES_PASSWORD": secrets.token_hex(24),
    "LOCAL_API_TOKEN": secrets.token_urlsafe(32),
    "REDIS_URL": "redis://127.0.0.1:56379/0",
    "QDRANT_URL": "http://127.0.0.1:56333",
}
values["DATABASE_URL"] = (
    "postgresql+psycopg://documind:" + values["POSTGRES_PASSWORD"] + "@127.0.0.1:55432/documind"
)
lines = []
for line in (root / ".env.example").read_text().splitlines():
    key = line.split("=", 1)[0]
    lines.append(f"{key}={values.pop(key)}" if key in values else line)
lines += [f"{key}={value}" for key, value in values.items()]
path = root / ".env"
with open(path, "x", opener=lambda p, f: os.open(p, f, 0o600)) as f:
    f.write("\n".join(lines) + "\n")
print("Created private .env. Credentials were not printed.")
