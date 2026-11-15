"""Private-session state tests use explicit SDK-shaped fixtures without credentials."""

import hashlib
import json
import os
from contextlib import nullcontext
from types import SimpleNamespace

import pytest
from documind import kaggle_session
from documind.kaggle_session import SessionController, SessionError


class NotFound(Exception):
    response = SimpleNamespace(status_code=404)


@pytest.fixture
def provision(tmp_path, monkeypatch):
    monkeypatch.setattr(
        kaggle_session, "_request", lambda name, **fields: SimpleNamespace(**fields)
    )
    folder = tmp_path / "template"
    folder.mkdir()
    metadata = {
        "id": "owner/documind-test-123",
        "is_private": True,
        "code_file": "serve.py",
        "language": "python",
        "kernel_type": "script",
    }
    source = 'print("synthetic model probe")\n'
    (folder / "kernel-metadata.json").write_text(json.dumps(metadata))
    (folder / "serve.py").write_text(source)
    record = SimpleNamespace(
        metadata=SimpleNamespace(ref=metadata["id"], is_private=True, current_version_number=1),
        blob=SimpleNamespace(source=source),
    )
    fixture = SimpleNamespace(
        saved=False,
        saves=0,
        creates=0,
        cancels=[],
        lose_create=False,
        record=record,
        done=False,
        gateway="https://kkb-production.jupyter-proxy.kaggle.net/k/987/test-secret/proxy",
        missing_status=404,
        matches=[],
        listings=[],
        metadata_id=987,
    )

    def get_kernel(request):
        if not fixture.saved:
            error = NotFound()
            error.response = SimpleNamespace(status_code=fixture.missing_status)
            raise error
        return record

    def push(path, no_run):
        assert no_run is True
        fixture.saved = True
        fixture.saves += 1
        return SimpleNamespace(error=None, ref="")

    def create(request):
        assert request.slug == metadata["id"] and request.machine_shape == "NvidiaTeslaT4"
        fixture.creates += 1
        if fixture.lose_create:
            raise TimeoutError("private URL and token must not be logged")
        return SimpleNamespace(
            name="operations/create-kernel-session/987",
            metadata={"kernelSessionId": fixture.metadata_id},
        )

    def operation(name):
        assert name == "operations/create-kernel-session/987"
        return SimpleNamespace(
            name=name,
            done=fixture.done,
            metadata={"kernelSessionId": 987},
            error=None,
            response={"jupyterUrl": fixture.gateway},
        )

    def cancel(request):
        fixture.cancels.append(request.kernel_session_id)
        return SimpleNamespace(error_message=None)

    kernels = SimpleNamespace(
        get_kernel=get_kernel, create_kernel_session=create, cancel_kernel_session=cancel
    )
    client = SimpleNamespace(
        kernels=SimpleNamespace(kernels_api_client=kernels),
        common=SimpleNamespace(operations_client=SimpleNamespace(get_operation=operation)),
        _http_client=SimpleNamespace(_session=SimpleNamespace(send=lambda *args, **kwargs: None)),
    )

    def listing(**filters):
        fixture.listings.append(filters)
        return fixture.matches

    api = SimpleNamespace(
        build_kaggle_client=lambda: nullcontext(client),
        kernels_push=push,
        kernels_list=listing,
        config_values={"username": "owner"},
    )
    controller = SessionController(api, tmp_path / "private-state.json")
    return controller, folder, fixture


def test_start_resume_and_private_gateway(provision):
    controller, folder, fixture = provision
    assert controller.start(folder) == {
        "kernel": "owner/documind-test-123",
        "phase": "starting",
        "session_id": 987,
    }
    assert controller.path.stat().st_mode & 0o777 == 0o600
    assert controller.start(folder)["phase"] == "starting"
    assert (fixture.saves, fixture.creates) == (1, 1)
    assert controller.poll()["phase"] == "starting"
    fixture.done = True
    public = controller.poll()
    assert public["session_id"] == 987 and public["phase"] == "ready"
    assert "test-secret" not in json.dumps(public)
    assert controller.jupyter_url == fixture.gateway


def test_lost_create_response_cannot_start_duplicate_gpu(provision):
    controller, folder, fixture = provision
    fixture.lose_create = True
    with pytest.raises(SessionError, match="^KAGGLE_REQUEST_FAILED$"):
        controller.start(folder)
    with pytest.raises(SessionError, match="^KAGGLE_CREATE_OUTCOME_UNKNOWN$"):
        controller.start(folder)
    assert fixture.creates == 1


def test_creation_receipt_is_durable_before_metadata_validation(provision):
    controller, folder, fixture = provision
    fixture.metadata_id = None
    with pytest.raises(SessionError, match="KAGGLE_SESSION_ID_UNAVAILABLE"):
        controller.start(folder)
    state = controller._read()
    assert state["operation"] == "operations/create-kernel-session/987"
    assert state["session_id"] == 987 and state["phase"] == "reconcile_required"
    controller.reconcile()
    assert controller.stop()["phase"] == "stop_requested" and fixture.cancels == [987]
    assert fixture.creates == 1


def test_unknown_create_requires_explicit_operator_receipt_without_reallocation(provision):
    controller, folder, fixture = provision
    fixture.lose_create = True
    with pytest.raises(SessionError):
        controller.start(folder)
    with pytest.raises(SessionError, match="KAGGLE_CREATE_OUTCOME_UNKNOWN"):
        controller.reconcile()
    with pytest.raises(SessionError, match="KAGGLE_RECOVERY_CONFIRMATION_REQUIRED"):
        controller.reconcile("operations/create-kernel-session/987", kernel="owner/another")
    controller.reconcile("operations/create-kernel-session/987", kernel="owner/documind-test-123")
    assert controller._read()["identity_source"] == "operator_confirmation"
    assert controller.stop()["phase"] == "stop_requested"
    assert fixture.creates == 1 and fixture.cancels == [987]


def test_resume_keeps_the_original_allocation_deadline(provision):
    controller, folder, fixture = provision
    controller.start(folder)
    deadline = controller.deadline()
    controller.start(folder)
    assert controller.deadline() == deadline and fixture.creates == 1


def test_status_after_expiry_reconciles_cleanup_before_notebook_checks(provision):
    from datetime import UTC, datetime, timedelta

    controller, folder, fixture = provision
    controller.start(folder)
    state = controller._read()
    state["expires_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    controller._write(state)
    fixture.record.metadata.current_version_number += 1
    assert controller.poll()["phase"] == "stop_requested"
    assert fixture.cancels == [987] and fixture.creates == 1


def test_reconcile_saved_notebook_without_repeating_save(provision):
    controller, folder, fixture = provision
    fixture.saved = True
    controller._write(
        {
            "kernel": "owner/documind-test-123",
            "phase": "save_pending",
            "source_sha256": hashlib.sha256((folder / "serve.py").read_bytes()).hexdigest(),
        }
    )
    assert controller.start(folder)["phase"] == "starting"
    assert fixture.saves == 0 and fixture.creates == 1


def test_unrelated_existing_notebook_is_never_overwritten(provision):
    controller, folder, fixture = provision
    fixture.saved = True
    with pytest.raises(SessionError, match="KAGGLE_RESOURCE_EXISTS"):
        controller.start(folder)
    assert fixture.saves == fixture.creates == 0


def test_missing_private_kernel_403_requires_owner_and_empty_owned_listing(provision):
    controller, folder, fixture = provision
    fixture.missing_status = 403
    assert controller.start(folder)["phase"] == "starting"
    assert fixture.listings == [{"mine": True, "search": "documind-test-123"}]
    assert fixture.saves == fixture.creates == 1


def test_missing_private_kernel_403_rejects_wrong_configured_account(provision):
    controller, folder, fixture = provision
    fixture.missing_status = 403
    controller.api.config_values["username"] = "different-owner"
    with pytest.raises(SessionError, match="KAGGLE_OWNERSHIP_UNCONFIRMED"):
        controller.start(folder)
    assert fixture.listings == [] and fixture.saves == fixture.creates == 0


def test_missing_private_kernel_403_rejects_existing_owned_notebook(provision):
    controller, folder, fixture = provision
    fixture.missing_status = 403
    fixture.matches = [SimpleNamespace(ref="owner/documind-test-123")]
    with pytest.raises(SessionError, match="KAGGLE_RESOURCE_EXISTS"):
        controller.start(folder)
    assert fixture.saves == fixture.creates == 0


@pytest.mark.parametrize("matches", [None, [SimpleNamespace(ref="owner/other-notebook")]])
def test_403_with_inconclusive_owned_listing_never_saves(provision, matches):
    controller, folder, fixture = provision
    fixture.missing_status = 403
    fixture.matches = matches
    with pytest.raises(SessionError, match="KAGGLE_OWNERSHIP_UNCONFIRMED"):
        controller.start(folder)
    assert fixture.saves == fixture.creates == 0


@pytest.mark.parametrize("change", ["private", "source", "version"])
def test_editing_created_notebook_does_not_prevent_exact_session_stop(provision, change):
    controller, folder, fixture = provision
    controller.start(folder)
    if change == "private":
        fixture.record.metadata.is_private = False
    elif change == "source":
        fixture.record.blob.source = "unrelated content"
    else:
        fixture.record.metadata.current_version_number = 2
    with pytest.raises(SessionError, match="KAGGLE_OWNERSHIP_CHANGED"):
        controller.poll()
    assert controller.stop()["phase"] == "stop_requested"
    assert controller.stop()["phase"] == "stop_requested"
    assert fixture.cancels == [987]


@pytest.mark.parametrize(
    "gateway",
    [
        "http://kkb-production.jupyter-proxy.kaggle.net/k/987/secret/proxy",
        "https://attacker.invalid/k/987/secret/proxy",
        "https://kkb-production.jupyter-proxy.kaggle.net/k/988/secret/proxy",
        "https://kkb-production.jupyter-proxy.kaggle.net/k/987/secret/proxy?token=secret",
    ],
)
def test_gateway_identity_must_match_created_session(provision, gateway):
    controller, folder, fixture = provision
    controller.start(folder)
    fixture.done = True
    fixture.gateway = gateway
    with pytest.raises(SessionError, match="KAGGLE_GATEWAY_IDENTITY_MISMATCH"):
        controller.poll()


def test_stop_acceptance_and_gateway_revocation_are_separate(provision):
    controller, folder, fixture = provision
    controller.start(folder)
    fixture.done = True
    controller.poll()
    assert controller.stop()["phase"] == "stop_requested"
    assert controller.cleanup_url == fixture.gateway
    with pytest.raises(SessionError, match="KAGGLE_SESSION_NOT_READY"):
        _ = controller.jupyter_url
    assert controller.stop()["phase"] == "stop_requested"
    assert fixture.cancels == [987]
    with pytest.raises(SessionError, match="KAGGLE_CLEANUP_UNCONFIRMED"):
        controller.confirm_access_revoked(200)
    assert controller.confirm_access_revoked(410)["phase"] == "access_revoked"
    assert "jupyter_url" not in json.loads(controller.path.read_text())
    with pytest.raises(SessionError, match="KAGGLE_SESSION_NOT_READY"):
        _ = controller.jupyter_url
    with pytest.raises(SessionError, match="KAGGLE_SESSION_NOT_READY"):
        _ = controller.cleanup_url


def test_state_permissions_and_template_changes_are_rejected(provision):
    controller, folder, fixture = provision
    controller.start(folder)
    os.chmod(controller.path, 0o644)
    with pytest.raises(SessionError, match="PRIVATE_STATE_REQUIRED"):
        controller.poll()
    os.chmod(controller.path, 0o600)
    (folder / "serve.py").write_text("changed template")
    with pytest.raises(SessionError, match="SESSION_TEMPLATE_CONFLICT"):
        controller.start(folder)
    assert fixture.creates == 1


def test_stop_rejects_session_id_that_is_not_bound_to_operation(provision):
    controller, folder, fixture = provision
    controller.start(folder)
    state = controller._read()
    state["session_id"] = 1
    controller._write(state)
    with pytest.raises(SessionError, match="KAGGLE_SESSION_IDENTITY_MISMATCH"):
        controller.stop()
    assert fixture.cancels == []


def test_stop_rejects_another_authenticated_account(provision):
    controller, folder, fixture = provision
    controller.start(folder)
    controller.api.config_values["username"] = "different-owner"
    with pytest.raises(SessionError, match="KAGGLE_OWNERSHIP_UNCONFIRMED"):
        controller.stop()
    assert fixture.cancels == []


def test_deleted_notebook_does_not_prevent_exact_session_stop(provision):
    controller, folder, fixture = provision
    controller.start(folder)
    fixture.saved = False
    assert controller.stop()["phase"] == "stop_requested"
    assert fixture.cancels == [987]


def test_timeout_scopes_every_nested_sdk_client_without_mutating_original_api(tmp_path):
    calls = []

    def send(request, **settings):
        calls.append(settings)
        return "response"

    session = SimpleNamespace(send=send)
    client = SimpleNamespace(_http_client=SimpleNamespace(_session=session))

    class Api:
        def build_kaggle_client(self):
            return nullcontext(client)

        def kernels_push(self):
            with self.build_kaggle_client() as opened:
                return opened._http_client._session.send(object())

        def kernels_list(self):
            with self.build_kaggle_client() as opened:
                return opened._http_client._session.send(object(), timeout=None)

    api = Api()
    controller = SessionController(api, tmp_path / "state.json")
    assert controller.api is not api
    assert controller.api.kernels_push() == "response"
    assert controller.api.kernels_list() == "response"
    assert calls == [{"timeout": (5, 15)}, {"timeout": (5, 15)}]
    assert "build_kaggle_client" not in api.__dict__
    assert session.send is send
    with controller.api.build_kaggle_client() as opened:
        opened._http_client._session.send(object(), timeout=300)
    assert calls[-1] == {"timeout": (5, 15)}
    assert session.send is send
