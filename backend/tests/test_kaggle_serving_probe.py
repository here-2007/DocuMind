"""The operator probe never trusts arbitrary Jupyter destinations or exposes credentials."""

import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_kaggle_session import provision as provision

spec = importlib.util.spec_from_file_location(
    "kaggle_serving_probe", Path(__file__).parents[2] / "scripts/kaggle_serving_probe.py"
)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)

ORIGIN = "https://kkb-production.jupyter-proxy.kaggle.net/k/123/private-credential/proxy"


@pytest.mark.parametrize(
    "origin",
    [
        ORIGIN.replace("https:", "http:"),
        ORIGIN.replace(".kaggle.net", ".kaggle.net.attacker.example"),
        ORIGIN.replace("/123/", "/456/"),
        ORIGIN + "?token=secret",
        ORIGIN.replace("https://", "https://user:password@"),
    ],
)
def test_gateway_is_bound_to_exact_kaggle_session(origin):
    with pytest.raises(probe.ProbeError, match="UNEXPECTED_JUPYTER_GATEWAY"):
        probe.Jupyter(origin, 123)


def test_file_upload_uses_private_gateway_and_rejects_path_escape():
    client = probe.Jupyter(ORIGIN, 123)
    client.close()
    requests = []

    def reply(request):
        requests.append((request.url.path, json.loads(request.content)))
        return httpx.Response(201, json={})

    client._http = httpx.Client(transport=httpx.MockTransport(reply))
    try:
        client.put_file("managed/source.py", b"print(1)\n")
        assert [row[0] for row in requests] == [
            "/k/123/private-credential/proxy/api/contents/managed",
            "/k/123/private-credential/proxy/api/contents/managed/source.py",
        ]
        assert requests[-1][1] == {
            "type": "file",
            "format": "base64",
            "content": "cHJpbnQoMSkK",
        }
        with pytest.raises(probe.ProbeError, match="INVALID_UPLOAD_PATH"):
            client.put_file("managed/../source.py", b"secret")
        with pytest.raises(probe.ProbeError, match="INVALID_UPLOAD_PATH"):
            client.put_file("/absolute/source.py", b"secret")
        assert len(requests) == 2
    finally:
        client.close()


def test_network_failures_never_echo_gateway_credential():
    client = probe.Jupyter(ORIGIN, 123)
    client.close()

    def fail(request):
        raise httpx.ConnectError(str(request.url), request=request)

    client._http = httpx.Client(transport=httpx.MockTransport(fail))
    try:
        with pytest.raises(probe.ProbeError) as error:
            client.request("GET", "/api")
        assert str(error.value) == "JUPYTER_NETWORK_FAILURE"
    finally:
        client.close()


def test_bootstrap_quotes_token_and_starts_loopback_with_expiry():
    code = probe.bootstrap("managed", "a" * 64, "token'\nquote")
    compile(code, "<private-bootstrap>", "exec")
    assert 'host=\\"127.0.0.1\\"' in code
    assert "documind.runtime_lifetime" in code and "--expires-at" in code
    assert "pilot=True" in code
    assert "access_log=False" in code


def test_run_cancels_owned_session_after_inference_failure(monkeypatch, tmp_path):
    actions = []
    controller = SimpleNamespace(
        start=lambda folder, **kwargs: {"phase": "ready", "session_id": 123},
        jupyter_url=ORIGIN,
        deadline=lambda: __import__("datetime").datetime.now(__import__("datetime").UTC),
    )
    client = SimpleNamespace(close=lambda: actions.append("close"))
    monkeypatch.setattr(probe, "checked_bundle", lambda *args: None)
    monkeypatch.setattr(probe, "launch_watcher", lambda *args: None)
    monkeypatch.setattr(probe, "Jupyter", lambda *args: client)
    monkeypatch.setattr(probe, "stop_and_observe", lambda *args: actions.append("stop"))

    def fail(*args):
        raise probe.ProbeError("RUNTIME_STEP_FAILED")

    monkeypatch.setattr(probe, "probe", fail)
    args = SimpleNamespace(
        state=tmp_path / "state.json",
        owner="owner",
        bundle=tmp_path,
        manifest_sha="a" * 64,
        output=tmp_path / "result.json",
    )
    with pytest.raises(probe.ProbeError, match="RUNTIME_STEP_FAILED"):
        probe.run(args, controller)
    assert actions == ["stop", "close"]
    assert json.loads(args.output.read_bytes())["status"] == "failed"


def test_creation_failure_still_cancels_exact_resource_and_records_failure(
    monkeypatch, provision, tmp_path
):
    controller, folder, fixture = provision
    fixture.metadata_id = None
    monkeypatch.setattr(probe, "checked_bundle", lambda *args: None)
    monkeypatch.setattr(probe, "launch_watcher", lambda *args: None)
    monkeypatch.setattr(probe, "template", lambda *args: folder)
    args = SimpleNamespace(
        state=controller.path,
        owner="owner",
        bundle=tmp_path,
        manifest_sha="a" * 64,
        output=tmp_path / "failure.json",
    )
    with pytest.raises(probe.SessionError, match="KAGGLE_SESSION_ID_UNAVAILABLE"):
        probe.run(args, controller)
    assert fixture.cancels == [987]
    assert json.loads(args.output.read_bytes())["cleanup"]["phase"] == "stop_requested"


def test_expired_watcher_cancels_persisted_exact_session(provision, tmp_path):
    from datetime import UTC, datetime, timedelta

    controller, folder, fixture = provision
    controller.start(folder)
    ready = tmp_path / "watch.ready"
    probe.watch_expiry(controller, datetime.now(UTC) - timedelta(seconds=1), ready)
    assert fixture.cancels == [987] and not ready.exists()


@pytest.mark.parametrize("recover", [True, False])
def test_watcher_retries_transient_failure_with_a_bound(provision, tmp_path, monkeypatch, recover):
    from datetime import UTC, datetime, timedelta

    controller, folder, fixture = provision
    controller.start(folder)
    retained_expiry = controller.deadline()
    ready = tmp_path / "watch.ready"
    delays = []
    monkeypatch.setattr(probe, "sleep", delays.append)
    stop = probe.stop_and_observe
    calls = []

    def intermittent(current):
        calls.append(current)
        if not recover or len(calls) == 1:
            raise probe.SessionError("KAGGLE_REQUEST_FAILED")
        return stop(current, timeout=0)

    monkeypatch.setattr(probe, "stop_and_observe", intermittent)
    expired = datetime.now(UTC) - timedelta(seconds=1)
    if recover:
        probe.watch_expiry(controller, expired, ready)
        assert fixture.cancels == [987] and delays == [2]
    else:
        with pytest.raises(probe.SessionError, match="KAGGLE_REQUEST_FAILED"):
            probe.watch_expiry(controller, expired, ready)
        assert len(calls) == 3 and delays == [2, 5]
        assert controller._read()["cleanup_error"] == "KAGGLE_REQUEST_FAILED"
    assert all(current is controller for current in calls)
    assert controller.deadline() == retained_expiry and not ready.exists()


def test_stop_request_without_observed_revocation_stays_pending():
    controller = SimpleNamespace(stop=lambda: {"phase": "stop_requested"})
    assert probe.stop_and_observe(controller, object(), timeout=0) == {"phase": "stop_requested"}


def test_preexisting_output_does_not_start_a_gpu_session(tmp_path):
    output = tmp_path / "result.json"
    output.write_text("previous result")
    with pytest.raises(probe.ProbeError, match="OUTPUT_ALREADY_EXISTS"):
        probe.run(SimpleNamespace(output=output), object())


def test_same_state_probe_cannot_execute_concurrently(tmp_path):
    state = tmp_path / "state.json"
    with probe.exclusive_probe(state):
        with pytest.raises(probe.ProbeError, match="PROBE_ALREADY_RUNNING"):
            with probe.exclusive_probe(state):
                pytest.fail("The second probe acquired a live session")
    with probe.exclusive_probe(state):
        pass


def test_stop_can_observe_gateway_revocation_on_second_call():
    attempts = []
    controller = SimpleNamespace(
        cleanup_url=ORIGIN,
        stop=lambda: {"phase": "stop_requested", "session_id": 123},
        confirm_access_revoked=lambda code: attempts.append(code) or {"phase": "access_revoked"},
    )
    client = probe.Jupyter(ORIGIN, 123)
    client.close()
    client._http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(410)),
    )
    original = probe.Jupyter
    try:
        probe.Jupyter = lambda *args: client
        assert probe.stop_and_observe(controller) == {"phase": "access_revoked"}
        assert attempts == [410]
    finally:
        probe.Jupyter = original


def test_status_observation_does_not_wait_for_streaming_body():
    class UnendingBody(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("Status confirmation tried to consume an unbounded response body")
            yield b""

    client = probe.Jupyter(ORIGIN, 123)
    client.close()
    client._http = httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(410, stream=UnendingBody())),
    )
    try:
        assert client.request("GET", "/api/status").status_code == 410
    finally:
        client.close()


@pytest.mark.parametrize(
    "changed",
    [
        "model_id",
        "model_revision",
        "protocol_sha256",
        "serializer_sha256",
        "token_ids",
        "context_limit",
        "chat_template_sha256",
    ],
)
def test_capabilities_must_match_pinned_artifact_beyond_advertised_digest(changed):
    manifest = {
        "base_model": "Qwen/base",
        "base_revision": "pinned",
        "protocol_sha256": "b" * 64,
        "serializer_sha256": "c" * 64,
        "context_limit": 512,
        "chat_template_sha256": "d" * 64,
    }
    fields = {
        "manifest_sha256": "a" * 64,
        "model_id": "Qwen/base",
        "model_revision": "pinned",
        "protocol_sha256": "b" * 64,
        "serializer_sha256": "c" * 64,
        "chat_template_sha256": "d" * 64,
        "token_ids": {"[Retrieve]": 20},
        "context_limit": 512,
        "supported_modes": ["reflection_pilot"],
    }
    probe.check_capability(SimpleNamespace(**fields), manifest, {"[Retrieve]": 20}, "a" * 64)
    fields[changed] = "changed"
    with pytest.raises(probe.ProbeError, match="RUNTIME_IDENTITY_MISMATCH"):
        probe.check_capability(SimpleNamespace(**fields), manifest, {"[Retrieve]": 20}, "a" * 64)


@pytest.mark.parametrize("change", ["version", "source", "private", "deleted"])
@pytest.mark.parametrize("ready", [False, True])
def test_stop_command_cancels_created_session_after_notebook_changes(provision, change, ready):
    controller, folder, fixture = provision
    controller.start(folder)
    if ready:
        fixture.done = True
        controller.poll()
    if change == "version":
        fixture.record.metadata.current_version_number += 1
    elif change == "source":
        fixture.record.blob.source = "Edited notebook"
    elif change == "private":
        fixture.record.metadata.is_private = False
    else:
        fixture.saved = False
    assert probe.stop_and_observe(controller, timeout=0)["phase"] == "stop_requested"
    assert probe.stop_and_observe(controller, timeout=0)["phase"] == "stop_requested"
    assert fixture.cancels == [987]
