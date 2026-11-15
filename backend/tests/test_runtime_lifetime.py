"""Real subprocess deadlines cover startup/descendants without any GPU fixture."""

import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta

import pytest
from documind.runtime_lifetime import bounded


def test_expired_lifetime_does_not_start_a_process(tmp_path):
    marker = tmp_path / "started"
    assert (
        bounded(
            [sys.executable, "-c", f"open({str(marker)!r},'w').close()"],
            datetime.now(UTC) - timedelta(seconds=1),
        )
        == 124
    )
    assert not marker.exists()


@pytest.mark.parametrize("exit_code", [0, 7])
def test_early_parent_exit_also_kills_descendants(tmp_path, exit_code):
    pulse = tmp_path / "pulse"
    descendant = tmp_path / "descendant.pid"
    beating = (
        f"import time; from pathlib import Path; p=Path({str(pulse)!r})\n"
        "while True: p.write_text(str(time.time())); time.sleep(.025)"
    )
    startup = (
        "import subprocess,sys,time; from pathlib import Path; "
        f"child=subprocess.Popen([sys.executable,'-c',{beating!r}]); "
        f"Path({str(descendant)!r}).write_text(str(child.pid)); "
        f"p=Path({str(pulse)!r})\n"
        "while not p.exists(): time.sleep(.01)\n"
        f"sys.exit({exit_code})"
    )
    try:
        assert (
            bounded([sys.executable, "-c", startup], datetime.now(UTC) + timedelta(seconds=5))
            == exit_code
        )
        assert pulse.exists()
        time.sleep(0.1)
        previous = pulse.read_text()
        time.sleep(0.15)
        assert pulse.read_text() == previous
    finally:
        if descendant.exists():
            try:
                os.kill(int(descendant.read_text()), signal.SIGKILL)
            except ProcessLookupError:
                pass


def test_detached_guardian_kills_startup_and_descendants_after_launcher_dies(tmp_path):
    pulse, guardian_pid = tmp_path / "pulse", tmp_path / "guardian.pid"
    beating = (
        f"import time; from pathlib import Path; p=Path({str(pulse)!r})\n"
        "while True: p.write_text(str(time.time())); time.sleep(.05)"
    )
    startup = (
        "import subprocess,sys,time; "
        f"subprocess.Popen([sys.executable,'-c',{beating!r}]); time.sleep(60)"
    )
    expiry = (datetime.now(UTC) + timedelta(seconds=2)).isoformat()
    command = [
        sys.executable,
        "-m",
        "documind.runtime_lifetime",
        "--expires-at",
        expiry,
        sys.executable,
        "-c",
        startup,
    ]
    launch = (
        "import subprocess,time; from pathlib import Path; "
        f"p=subprocess.Popen({command!r},start_new_session=True); "
        f"Path({str(guardian_pid)!r}).write_text(str(p.pid)); time.sleep(60)"
    )
    parent = subprocess.Popen([sys.executable, "-c", launch])
    try:
        deadline = time.monotonic() + 3
        while not pulse.exists() and time.monotonic() < deadline:
            time.sleep(0.025)
        assert pulse.exists()
        parent.kill()
        parent.wait(timeout=3)
        time.sleep(max(0, datetime.fromisoformat(expiry).timestamp() - time.time()) + 0.2)
        previous = pulse.read_text()
        time.sleep(0.2)
        assert pulse.read_text() == previous
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=3)
        if guardian_pid.exists():
            try:
                os.kill(int(guardian_pid.read_text()), signal.SIGTERM)
            except ProcessLookupError:
                pass


def test_bootstrap_keeps_secret_out_of_saved_model_program():
    from test_kaggle_serving_probe import probe

    token = "synthetic-secret-should-stay-in-memory"
    code = probe.bootstrap("managed", "a" * 64, token)
    import ast

    tree = ast.parse(code)
    saved = next(
        node.value.args[0].value
        for node in tree.body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Attribute)
        and node.value.func.attr == "write_text"
    )
    compile(saved, "<bounded-model-program>", "exec")
    assert token not in saved and "DOCUMIND_RUNTIME_TOKEN" in saved
    assert json.dumps(token) in code
