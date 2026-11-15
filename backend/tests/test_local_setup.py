"""Fresh and existing private configuration must agree with the secured store."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from dotenv import dotenv_values


def test_init_generates_key_and_upgrade_preserves_existing_configuration(tmp_path):
    root = Path(__file__).resolve().parents[2]
    (tmp_path / "scripts").mkdir()
    script = tmp_path / "scripts/init_local.py"
    shutil.copy(root / "scripts/init_local.py", script)
    shutil.copy(root / ".env.example", tmp_path / ".env.example")
    command = [sys.executable, str(script)]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    target = tmp_path / ".env"
    fresh = dotenv_values(target)
    assert len(fresh["QDRANT_API_KEY"]) >= 32
    assert fresh["QDRANT_API_KEY"] not in result.stdout + result.stderr
    assert target.stat().st_mode & 0o077 == 0
    before = target.read_bytes()
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert target.read_bytes() == before
    subprocess.run(command + ["--secure-qdrant"], capture_output=True, check=True)
    assert target.read_bytes() == before

    legacy = b"# preserve operator values\nLOCAL_API_TOKEN=existing-secret\nQDRANT_API_KEY=\n"
    target.write_bytes(legacy)
    target.chmod(0o644)
    result = subprocess.run(
        command + ["--secure-qdrant"], capture_output=True, text=True, check=True
    )
    assert target.read_bytes().startswith(legacy)
    upgraded = dotenv_values(target)
    assert upgraded["LOCAL_API_TOKEN"] == "existing-secret"
    assert len(upgraded["QDRANT_API_KEY"]) >= 32
    assert upgraded["QDRANT_API_KEY"] not in result.stdout + result.stderr
    assert target.stat().st_mode & 0o077 == 0
    before = target.read_bytes()
    subprocess.run(command + ["--secure-qdrant"], capture_output=True, check=True)
    assert target.read_bytes() == before


@pytest.mark.parametrize("key", [None, "short"])
def test_indexing_requires_a_private_qdrant_key(monkeypatch, key):
    from documind.config import Settings

    for name in os.environ:
        if name.startswith(("SELF_RAG_", "INDEXING_", "QDRANT_")):
            monkeypatch.delenv(name)
    with pytest.raises(ValueError, match="QDRANT_API_KEY"):
        Settings(
            _env_file=None,
            local_api_token="x" * 32,
            database_url="postgresql://unused",
            indexing_enabled=True,
            qdrant_api_key=key,
        )
