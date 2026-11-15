"""Exercise one private Kaggle GPU runtime through its authenticated Jupyter gateway."""

import argparse
import base64
import fcntl
import json
import os
import re
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from secrets import token_urlsafe
from time import monotonic, sleep
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from documind.kaggle_session import LIFETIME_SECONDS, SessionController, SessionError, expiry
from documind.reflection_review import checked_bundle
from documind.runtime_protocol import Capabilities, check_result, make_step

MODULES = (
    "reflection",
    "reflection_data",
    "reflection_model",
    "reflection_review",
    "reflection_runtime",
    "reflection_pilot",
    "runtime_protocol",
    "runtime_client",
    "runtime_probe",
    "chat",
    "errors",
    "reflection_context",
    "evidence",
    "runtime_lifetime",
)


class ProbeError(RuntimeError):
    pass


class Jupyter:
    def __init__(self, origin, session_id):
        parsed = urlsplit(origin)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "kkb-production.jupyter-proxy.kaggle.net"
            or parsed.port not in (None, 443)
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or not re.fullmatch(rf"/k/{session_id}/[A-Za-z0-9_.-]+/proxy", parsed.path)
        ):
            raise ProbeError("UNEXPECTED_JUPYTER_GATEWAY")
        self._origin = origin.rstrip("/")
        self._http = httpx.Client(timeout=60, follow_redirects=False)

    def close(self):
        self._http.close()

    def request(self, method, path, *, body=None, headers=None, timeout=60):
        try:
            deadline = monotonic() + timeout
            with self._http.stream(
                method,
                self._origin + path,
                json=body,
                headers=headers,
                timeout=timeout,
            ) as response:
                content = bytearray()
                if path != "/api/status":
                    for part in response.iter_bytes():
                        content.extend(part)
                        if monotonic() > deadline or len(content) > 2_000_000:
                            raise ProbeError("JUPYTER_RESPONSE_LIMIT")
                return httpx.Response(response.status_code, content=bytes(content))
        except httpx.HTTPError:
            raise ProbeError("JUPYTER_NETWORK_FAILURE") from None

    def put_file(self, path, data):
        parts = PurePosixPath(path).parts
        if not parts or any(part in {"..", "/"} for part in parts):
            raise ProbeError("INVALID_UPLOAD_PATH")
        for index in range(1, len(parts)):
            response = self.request(
                "PUT",
                "/api/contents/" + "/".join(parts[:index]),
                body={"type": "directory"},
            )
            if response.status_code not in {200, 201}:
                raise ProbeError("JUPYTER_DIRECTORY_FAILED")
        response = self.request(
            "PUT",
            "/api/contents/" + path,
            body={"type": "file", "format": "base64", "content": base64.b64encode(data).decode()},
        )
        if response.status_code not in {200, 201}:
            raise ProbeError("JUPYTER_UPLOAD_FAILED")

    def execute(self, code, timeout=600):
        import websocket

        response = self.request("GET", "/api/kernels")
        if response.status_code != 200 or len(response.json()) != 1:
            raise ProbeError("UNEXPECTED_KERNEL_COUNT")
        kernel = response.json()[0]["id"]
        identifier = uuid4().hex
        message = {
            "header": {
                "msg_id": identifier,
                "username": "documind",
                "session": uuid4().hex,
                "msg_type": "execute_request",
                "version": "5.3",
            },
            "parent_header": {},
            "metadata": {},
            "channel": "shell",
            "buffers": [],
            "content": {
                "code": code,
                "silent": False,
                "store_history": False,
                "user_expressions": {},
                "allow_stdin": False,
                "stop_on_error": True,
            },
        }
        url = "wss:" + self._origin[6:] + f"/api/kernels/{kernel}/channels"
        try:
            stream = websocket.create_connection(
                url,
                timeout=timeout,
                origin="https://kkb-production.jupyter-proxy.kaggle.net",
            )
            try:
                stream.send(json.dumps(message))
                deadline = monotonic() + timeout
                received = 0
                while monotonic() < deadline:
                    stream.settimeout(max(0.1, deadline - monotonic()))
                    raw = stream.recv()
                    received += len(raw)
                    if received > 2_000_000:
                        raise ProbeError("JUPYTER_OUTPUT_LIMIT")
                    item = json.loads(raw)
                    if item.get("parent_header", {}).get("msg_id") != identifier:
                        continue
                    content = item["content"]
                    if item["msg_type"] == "execute_reply":
                        if content["status"] != "ok":
                            raise ProbeError("REMOTE_BOOTSTRAP_FAILED")
                        return
                raise ProbeError("JUPYTER_EXECUTION_TIMEOUT")
            finally:
                stream.close()
        except (websocket.WebSocketException, OSError, ValueError):
            raise ProbeError("JUPYTER_EXECUTION_FAILED") from None


def bootstrap(directory, manifest_sha, token, expires_at=None, *, server_program=None):
    # Values enter Python only through JSON literals in the operator's private execution channel.
    root = json.dumps(directory)
    expires_at = expires_at or (datetime.now(UTC) + timedelta(seconds=LIFETIME_SECONDS)).isoformat()
    program = f"""
import os, pathlib, subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "transformers==5.22.2",
                "peft==0.19.1", "accelerate==1.13.0", "bitsandbytes==0.50.2",
                "fastapi", "uvicorn", "httpx", "pydantic>=2.7,<3"], check=True)
root = pathlib.Path({root})
sys.path.insert(0, str(root / "source"))
from documind.reflection_runtime import application, load
import uvicorn
cap, decode = load(root / "bundle", {json.dumps(manifest_sha)}, pilot=True)
uvicorn.run(application(cap, decode, os.environ["DOCUMIND_RUNTIME_TOKEN"]),
            host="127.0.0.1", port=8010, log_level="error", access_log=False)
"""
    if server_program is not None:
        program = server_program
    return f"""
import os, pathlib, subprocess, sys, time, urllib.request
from datetime import datetime, timezone
root = pathlib.Path({root}).resolve()
program = root / "serve.py"
program.write_text({json.dumps(program)})
program.chmod(0o600)
environment = os.environ | {{"PYTHONPATH": str(root / "source"),
    "DOCUMIND_RUNTIME_TOKEN": {json.dumps(token)}, "HF_HUB_DISABLE_PROGRESS_BARS": "1"}}
with (root / "runtime.log").open("w") as output:
    (root / "runtime.log").chmod(0o600)
    guardian = subprocess.Popen([sys.executable, "-m", "documind.runtime_lifetime",
        "--expires-at", {json.dumps(expires_at)}, sys.executable, str(program)],
        env=environment, stdout=output, stderr=subprocess.STDOUT, start_new_session=True)
deadline = min(time.time() + 600, datetime.fromisoformat({json.dumps(expires_at)}).timestamp())
ready = False
while time.time() < deadline and guardian.poll() is None:
    try:
        request = urllib.request.Request("http://127.0.0.1:8010/runtime/v1/capabilities",
            headers={{"Authorization": "Bearer " + environment["DOCUMIND_RUNTIME_TOKEN"]}})
        with urllib.request.urlopen(request, timeout=1) as response:
            ready = response.status == 200
        if ready: break
    except Exception:
        time.sleep(.25)
assert ready, "BOUNDED_RUNTIME_STARTUP_FAILED"
print("DOCUMIND_PRIVATE_RUNTIME_READY", flush=True)
"""


def check_capability(cap, manifest, ids, expected_sha):
    expected = {
        "manifest_sha256": expected_sha,
        "model_id": manifest["base_model"],
        "model_revision": manifest["base_revision"],
        "protocol_sha256": manifest["protocol_sha256"],
        "serializer_sha256": manifest["serializer_sha256"],
        "chat_template_sha256": manifest["chat_template_sha256"],
        "token_ids": ids,
        "context_limit": manifest["context_limit"],
        "supported_modes": ["reflection_pilot"],
    }
    if any(getattr(cap, name) != value for name, value in expected.items()):
        raise ProbeError("RUNTIME_IDENTITY_MISMATCH")


def deploy(jupyter, bundle, manifest_sha, expires_at=None):
    manifest, ids = checked_bundle(bundle, manifest_sha)
    remote = "documind-probe-" + uuid4().hex
    for name in MODULES:
        file = Path("backend/documind") / f"{name}.py"
        jupyter.put_file(f"{remote}/source/documind/{name}.py", file.read_bytes())
    for file in sorted(bundle.rglob("*")):
        if file.is_file():
            target = f"{remote}/bundle/{file.relative_to(bundle).as_posix()}"
            jupyter.put_file(target, file.read_bytes())
    token = token_urlsafe(32)
    print("Uploaded pinned source and adapter to the private session", flush=True)
    jupyter.execute(bootstrap(remote, manifest_sha, token, expires_at))
    path = "/proxy/8010/runtime/v1"
    unauthorized = jupyter.request("GET", path + "/capabilities")
    if unauthorized.status_code != 401:
        raise ProbeError("RUNTIME_AUTHENTICATION_FAILED")
    headers = {"Authorization": "Bearer " + token}
    response = jupyter.request("GET", path + "/capabilities", headers=headers)
    if response.status_code != 200:
        raise ProbeError("RUNTIME_GATEWAY_UNAVAILABLE")
    cap = Capabilities.model_validate(response.json())
    check_capability(cap, manifest, ids, manifest_sha)
    return cap, headers


def probe(jupyter, bundle, manifest_sha, expires_at=None):
    from documind.reflection_data import episode_context
    from documind.reflection_pilot import create_corpus

    cap, headers = deploy(jupyter, bundle, manifest_sha, expires_at)
    path = "/proxy/8010/runtime/v1"
    required, eligible, *_ = create_corpus()[1]
    rows = []
    for label, sample, index in (
        ("document_decision", required, 0),
        ("general_decision", eligible, 0),
        ("evidence_candidate", required, 1),
    ):
        episode = sample.episodes[index]
        step = make_step(
            cap,
            uuid4(),
            episode.state,
            episode_context(sample, episode),
            1 if episode.state == "DECIDE" else 32,
            datetime.now(UTC) + timedelta(seconds=45),
        )
        response = jupyter.request(
            "POST",
            path + "/steps",
            body=step.model_dump(mode="json"),
            headers=headers,
        )
        if response.status_code != 200:
            raise ProbeError("RUNTIME_STEP_FAILED")
        from documind.runtime_protocol import Result

        result = Result.model_validate(response.json())
        labels = check_result(result, step, cap)
        if result.finish_reason != "complete":
            raise ProbeError("RUNTIME_STEP_INCOMPLETE")
        replay = jupyter.request(
            "POST",
            path + "/steps",
            body=step.model_dump(mode="json"),
            headers=headers,
        )
        if replay.status_code != 200 or replay.json() != response.json():
            raise ProbeError("RUNTIME_REPLAY_FAILED")
        rows.append(
            {
                "case": label,
                "labels": labels,
                "finish_reason": result.finish_reason,
                "output_tokens": result.output_tokens,
            }
        )
    return {
        "manifest_sha256": manifest_sha,
        "model_id": cap.model_id,
        "mode": cap.supported_modes[0],
        "gateway_authentication": "private Kaggle Jupyter",
        "unauthenticated_runtime_status": 401,
        "step_replay_verified": True,
        "cases": rows,
    }


def template(state_path, owner):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", owner):
        raise ProbeError("INVALID_KAGGLE_OWNER")
    folder = state_path.parent / (state_path.stem + "-template")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    metadata = folder / "kernel-metadata.json"
    if not metadata.exists():
        slug = "documind-private-probe-" + uuid4().hex[:16]
        data = {
            "id": owner + "/" + slug,
            "title": slug,
            "code_file": "probe.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": True,
            "enable_gpu": True,
            "enable_internet": True,
        }
        metadata.write_text(json.dumps(data) + "\n")
        (folder / "probe.py").write_text('print("DocuMind private ML runtime probe")\n')
    elif json.loads(metadata.read_bytes())["id"].split("/")[0] != owner:
        raise ProbeError("KAGGLE_OWNER_CHANGED")
    return folder


def stop_and_observe(controller, jupyter=None, timeout=30):
    status = controller.stop()
    if status["phase"] == "access_revoked":
        return status
    owned = jupyter is None
    if owned:
        try:
            jupyter = Jupyter(controller.cleanup_url, status["session_id"])
        except SessionError:
            return status
    try:
        deadline = monotonic() + timeout
        while monotonic() < deadline:
            try:
                response = jupyter.request("GET", "/api/status", timeout=5)
                if response.status_code in {403, 404, 410}:
                    return controller.confirm_access_revoked(response.status_code)
            except ProbeError:
                pass
            sleep(2)
        return status
    finally:
        if owned:
            jupyter.close()


def watch_expiry(controller, expires_at, ready_path):
    """Independent local reaper survives the operator command exiting or being killed."""
    lock_path = controller.path.with_suffix(".watch.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProbeError("SESSION_WATCHER_ALREADY_RUNNING") from None
        ready_path.write_text(str(os.getpid()))
        ready_path.chmod(0o600)
        try:
            while datetime.now(UTC) < expires_at:
                if controller.path.exists():
                    with controller._locked():
                        state = controller._read()
                    if state["phase"] in {"stop_requested", "access_revoked"}:
                        return
                    if state.get("expires_at"):
                        expires_at = min(expires_at, expiry(state["expires_at"]))
                sleep(min(2, max(0, (expires_at - datetime.now(UTC)).total_seconds())))
            if controller.path.exists():
                with controller._locked():
                    state = controller._read()
                for delay in (0, 2, 5):
                    if delay:
                        sleep(delay)
                    try:
                        if not state.get("session_id"):
                            state = controller.reconcile()
                        stop_and_observe(controller)
                        break
                    except SessionError as error:
                        if str(error) != "KAGGLE_REQUEST_FAILED" or delay == 5:
                            raise
        except (SessionError, ProbeError) as error:
            if controller.path.exists():
                with controller._locked():
                    state = controller._read()
                    state["cleanup_error"] = str(error)
                    controller._write(state)
            raise
        finally:
            ready_path.unlink(missing_ok=True)


def launch_watcher(state, expires_at):
    state = state.resolve()
    state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    ready = state.with_name(state.name + ".watch-" + uuid4().hex + ".ready")
    log = state.with_suffix(".watch.log")
    fd = os.open(log, os.O_CREAT | os.O_APPEND | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as output:
        child = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "watch",
                "--state",
                str(state),
                "--expires-at",
                expires_at.isoformat(),
                "--ready",
                str(ready),
            ],
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    deadline = monotonic() + 15
    while child.poll() is None and monotonic() < deadline:
        if ready.exists() and ready.read_text() == str(child.pid):
            return child
        sleep(0.05)
    if child.poll() is None:
        child.terminate()
        child.wait(timeout=5)
    raise ProbeError("SESSION_WATCHER_UNAVAILABLE")


def run(args, controller):
    if args.output.exists():
        raise ProbeError("OUTPUT_ALREADY_EXISTS")
    checked_bundle(args.bundle, args.manifest_sha)
    expires_at = datetime.now(UTC) + timedelta(seconds=LIFETIME_SECONDS)
    launch_watcher(args.state, expires_at)
    result = {
        "created_at": datetime.now(UTC).isoformat(),
        "manifest_sha256": args.manifest_sha,
        "status": "failed",
    }
    jupyter = None
    try:
        status = controller.start(
            template(args.state, args.owner), expires_at=expires_at.isoformat()
        )
        deadline = monotonic() + 600
        while status["phase"] == "starting" and monotonic() < deadline:
            sleep(5)
            status = controller.poll()
        if status["phase"] != "ready":
            raise ProbeError("KAGGLE_SESSION_NOT_READY")
        jupyter = Jupyter(controller.jupyter_url, status["session_id"])
        result = probe(jupyter, args.bundle, args.manifest_sha, controller.deadline().isoformat())
        result.update(
            created_at=datetime.now(UTC).isoformat(),
            kernel=status["kernel"],
            session_id=status["session_id"],
            status="passed",
        )
    except Exception as error:
        result["error"] = (
            str(error) if isinstance(error, (SessionError, ProbeError)) else type(error).__name__
        )
        raise
    finally:
        try:
            result["cleanup"] = stop_and_observe(controller, jupyter)
        except (SessionError, ProbeError) as error:
            result["cleanup"] = {"phase": "unconfirmed", "error": str(error)}
            print("Session cleanup remains unconfirmed; retain the private state file", flush=True)
        finally:
            if jupyter:
                jupyter.close()
        if result.get("manifest_sha256"):
            fd = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as stream:
                json.dump(result, stream, indent=2)
                stream.write("\n")
    return result


@contextmanager
def exclusive_probe(state_path):
    state_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(
        state_path.with_suffix(".probe.lock"),
        os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW,
        0o600,
    )
    with os.fdopen(fd, "a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ProbeError("PROBE_ALREADY_RUNNING") from None
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("run", "status", "stop", "reconcile", "watch"):
        command = commands.add_parser(name)
        command.add_argument("--state", type=Path, required=True)
        if name == "reconcile":
            command.add_argument("--operation")
            command.add_argument("--confirm-kernel")
        if name == "watch":
            command.add_argument("--expires-at", required=True)
            command.add_argument("--ready", type=Path, required=True)
        if name == "run":
            command.add_argument("--owner", required=True)
            command.add_argument("--bundle", type=Path, required=True)
            command.add_argument("--manifest-sha", required=True)
            command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        from kaggle.api.kaggle_api_extended import KaggleApi

        api = KaggleApi()
        api.authenticate()
        controller = SessionController(api, args.state)
        if args.command == "run":
            with exclusive_probe(args.state):
                result = run(args, controller)
        elif args.command == "stop":
            result = stop_and_observe(controller)
        elif args.command == "reconcile":
            controller.reconcile(args.operation, kernel=args.confirm_kernel)
            result = stop_and_observe(controller)
        elif args.command == "watch":
            watch_expiry(controller, expiry(args.expires_at), args.ready)
            return
        else:
            result = controller.poll()
        print(json.dumps(result, indent=2), flush=True)
    except Exception as error:
        known = isinstance(error, (ProbeError, SessionError))
        message = str(error) if known else type(error).__name__
        parser.exit(1, f"Private Kaggle probe failed ({message})\n")


if __name__ == "__main__":
    main()
