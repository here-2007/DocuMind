"""Create private configuration, or add only a missing Qdrant key to an existing .env."""

import argparse
import base64
import fcntl
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "--secure-qdrant",
    action="store_true",
    help="Add a missing private Qdrant key without changing existing settings",
)
args = parser.parse_args()
if args.secure_qdrant:
    from dotenv import dotenv_values

    with open(root / ".env", "r+") as existing:
        fcntl.flock(existing, fcntl.LOCK_EX)
        content = dotenv_values(stream=existing)
        if not content.get("QDRANT_API_KEY"):
            os.fchmod(existing.fileno(), 0o600)
            existing.seek(0, os.SEEK_END)
            existing.write("\nQDRANT_API_KEY=" + secrets.token_urlsafe(32) + "\n")
            existing.flush()
            os.fsync(existing.fileno())
    print("Private Qdrant key configured. Existing settings preserved; no credentials printed.")
    raise SystemExit(0)
values = {
    "POSTGRES_PASSWORD": secrets.token_hex(24),
    "LOCAL_API_TOKEN": secrets.token_urlsafe(32),
    "QDRANT_API_KEY": secrets.token_urlsafe(32),
    "INFERENCE_SECRET_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode(),
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
