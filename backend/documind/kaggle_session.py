"""Operator-only ownership and recovery checks for one private Kaggle ML session."""

import fcntl
import hashlib
import json
import os
import re
import tempfile
from contextlib import contextmanager
from copy import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

LIFETIME_SECONDS = 1200


def expiry(value):
    timestamp = datetime.fromisoformat(value)
    if timestamp.tzinfo is None:
        raise SessionError("INVALID_SESSION_EXPIRY")
    return timestamp


class SessionError(ValueError):
    """A bounded error code that never includes SDK URLs or credentials."""


def _request(name, **fields):
    from kagglesdk.kernels.types import kernels_api_service

    request = getattr(kernels_api_service, name)()
    for key, value in fields.items():
        setattr(request, key, value)
    return request


class SessionController:
    def __init__(self, api, state_path: Path):
        self.api = copy(api)
        self._build_client = api.build_kaggle_client
        self.api.build_kaggle_client = self._client
        self.path = Path(state_path)

    @contextmanager
    def _client(self):
        with self._build_client() as client:
            # Kaggle 2.2.5 exposes no timeout option. Scope this wrapper to one SDK session.
            session = client._http_client._session
            send = session.send

            def bounded_send(request, **settings):
                settings["timeout"] = (5, 15)
                return send(request, **settings)

            session.send = bounded_send
            try:
                yield client
            finally:
                session.send = send

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(self.path.with_suffix(".lock"), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            except SessionError:
                raise
            except Exception:
                raise SessionError("KAGGLE_REQUEST_FAILED") from None

    def _read(self):
        if self.path.is_symlink() or self.path.stat().st_mode & 0o077:
            raise SessionError("PRIVATE_STATE_REQUIRED")
        return json.loads(self.path.read_text())

    def _write(self, state):
        fd, name = tempfile.mkstemp(dir=self.path.parent, prefix=".session-")
        try:
            with os.fdopen(fd, "w") as output:
                json.dump(state, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(name, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    @staticmethod
    def _public(state):
        return {key: state[key] for key in ("kernel", "phase", "session_id") if key in state}

    def _owned(self, client, state):
        owner, slug = state["kernel"].split("/")
        record = client.kernels.kernels_api_client.get_kernel(
            _request("ApiGetKernelRequest", user_name=owner, kernel_slug=slug)
        )
        metadata = record.metadata
        source = record.blob.source if record.blob else None
        if (
            metadata.ref != state["kernel"]
            or metadata.is_private is not True
            or not isinstance(source, str)
            or hashlib.sha256(source.encode()).hexdigest() != state["source_sha256"]
            or state.get("version", metadata.current_version_number)
            != metadata.current_version_number
        ):
            raise SessionError("KAGGLE_OWNERSHIP_CHANGED")
        return metadata

    def start(self, folder: Path, *, expires_at=None):
        folder = Path(folder).resolve()
        metadata = json.loads((folder / "kernel-metadata.json").read_text())
        kernel = metadata.get("id", "")
        code = folder / metadata.get("code_file", "")
        if (
            not re.fullmatch(r"[A-Za-z0-9_-]+/documind-[a-z0-9-]+", kernel)
            or metadata.get("is_private") is not True
            or metadata.get("language") != "python"
            or metadata.get("kernel_type") != "script"
            or code.is_symlink()
            or not code.resolve().is_relative_to(folder)
            or not code.is_file()
        ):
            raise SessionError("PRIVATE_OWNED_TEMPLATE_REQUIRED")
        source_sha = hashlib.sha256(code.read_bytes()).hexdigest()
        with self._locked(), self.api.build_kaggle_client() as client:
            if self.path.exists():
                state = self._read()
                if state["kernel"] != kernel or state["source_sha256"] != source_sha:
                    raise SessionError("SESSION_TEMPLATE_CONFLICT")
            else:
                owner, slug = kernel.split("/")
                try:
                    client.kernels.kernels_api_client.get_kernel(
                        _request("ApiGetKernelRequest", user_name=owner, kernel_slug=slug)
                    )
                except Exception as exc:
                    status = getattr(getattr(exc, "response", None), "status_code", None)
                    if status == 403:
                        username = self.api.config_values.get("username")
                        if not isinstance(username, str) or username.casefold() != owner.casefold():
                            raise SessionError("KAGGLE_OWNERSHIP_UNCONFIRMED") from None
                        matches = self.api.kernels_list(mine=True, search=slug)
                        if matches is None:
                            raise SessionError("KAGGLE_OWNERSHIP_UNCONFIRMED") from None
                        if any(item is not None and item.ref == kernel for item in matches):
                            raise SessionError("KAGGLE_RESOURCE_EXISTS") from None
                        if matches:
                            raise SessionError("KAGGLE_OWNERSHIP_UNCONFIRMED") from None
                    elif status != 404:
                        raise SessionError("KAGGLE_OWNERSHIP_UNCONFIRMED") from None
                else:
                    raise SessionError("KAGGLE_RESOURCE_EXISTS")
                state = {"kernel": kernel, "source_sha256": source_sha, "phase": "save_pending"}
                self._write(state)
                response = self.api.kernels_push(str(folder), no_run=True)
                if response.error:
                    raise SessionError("KAGGLE_SAVE_REJECTED")
            owned = self._owned(client, state)
            if "expires_at" not in state:
                deadline = (
                    expiry(expires_at)
                    if expires_at
                    else datetime.now(UTC) + timedelta(seconds=LIFETIME_SECONDS)
                )
                if not 0 < (deadline - datetime.now(UTC)).total_seconds() <= LIFETIME_SECONDS:
                    raise SessionError("INVALID_SESSION_EXPIRY")
                state["expires_at"] = deadline.isoformat()
                self._write(state)
            if expiry(state["expires_at"]) <= datetime.now(UTC):
                raise SessionError("KAGGLE_SESSION_EXPIRED")
            if state["phase"] == "save_pending":
                state.update(phase="saved", version=owned.current_version_number)
                self._write(state)
            if state["phase"] == "saved":
                state["phase"] = "create_pending"
                self._write(state)
                operation = client.kernels.kernels_api_client.create_kernel_session(
                    _request(
                        "ApiCreateKernelSessionRequest",
                        slug=kernel,
                        language="python",
                        kernel_type="script",
                        machine_shape="NvidiaTeslaT4",
                        enable_internet=True,
                    )
                )
                # Retain the server's operation before parsing metadata or promoting readiness.
                state["operation"] = operation.name
                self._write(state)
                match = re.fullmatch(
                    r"operations/create-kernel-session/([1-9][0-9]*)", operation.name
                )
                if match:
                    state.update(session_id=int(match[1]), phase="starting")
                    self._write(state)
                session = (operation.metadata or {}).get("kernelSessionId")
                if (
                    not str(session).isdigit()
                    or int(session) <= 0
                    or operation.name != f"operations/create-kernel-session/{session}"
                ):
                    state["phase"] = "reconcile_required"
                    self._write(state)
                    raise SessionError("KAGGLE_SESSION_ID_UNAVAILABLE")
                state.update(operation=operation.name, session_id=int(session), phase="starting")
                self._write(state)
            elif state["phase"] in {"create_pending", "reconcile_required"}:
                raise SessionError("KAGGLE_CREATE_OUTCOME_UNKNOWN")
            return self._public(state)

    def reconcile(self, operation=None, *, kernel=None):
        """Bind a retained response or an explicitly operator-confirmed recovery receipt."""
        with self._locked(), self.api.build_kaggle_client() as client:
            state = self._read()
            owner = state["kernel"].split("/")[0]
            username = self.api.config_values.get("username")
            if not isinstance(username, str) or username.casefold() != owner.casefold():
                raise SessionError("KAGGLE_OWNERSHIP_UNCONFIRMED")
            if operation is not None and kernel != state["kernel"]:
                raise SessionError("KAGGLE_RECOVERY_CONFIRMATION_REQUIRED")
            if state["phase"] in {"save_pending", "saved"}:
                raise SessionError("KAGGLE_CREATE_NOT_REQUESTED")
            if state["phase"] in {"stop_requested", "access_revoked"}:
                return self._public(state)
            if operation is not None and state.get("operation") and operation != state["operation"]:
                raise SessionError("KAGGLE_SESSION_IDENTITY_MISMATCH")
            name = operation if operation is not None else state.get("operation", "")
            match = re.fullmatch(r"operations/create-kernel-session/([1-9][0-9]*)", name)
            if not match:
                raise SessionError("KAGGLE_CREATE_OUTCOME_UNKNOWN")
            if state.get("session_id") and state["session_id"] != int(match[1]):
                raise SessionError("KAGGLE_SESSION_IDENTITY_MISMATCH")
            result = client.common.operations_client.get_operation(name=name)
            metadata_id = (result.metadata or {}).get("kernelSessionId")
            if result.name != name or (metadata_id is not None and str(metadata_id) != match[1]):
                raise SessionError("KAGGLE_SESSION_IDENTITY_MISMATCH")
            state.update(
                operation=name,
                session_id=int(match[1]),
                phase="starting",
                identity_source="operator_confirmation"
                if operation is not None
                else "create_response",
            )
            self._write(state)
            return self._public(state)

    def deadline(self):
        with self._locked():
            state = self._read()
            return expiry(state["expires_at"]) if state.get("expires_at") else datetime.now(UTC)

    def poll(self):
        if self.deadline() <= datetime.now(UTC):
            return self.stop()
        with self._locked(), self.api.build_kaggle_client() as client:
            state = self._read()
            self._owned(client, state)
            if state["phase"] == "starting":
                operation = client.common.operations_client.get_operation(name=state["operation"])
                if operation.error:
                    state["phase"] = "failed"
                elif operation.done:
                    url = (operation.response or {}).get("jupyterUrl", "")
                    target = urlsplit(url)
                    if (
                        target.scheme != "https"
                        or target.hostname != "kkb-production.jupyter-proxy.kaggle.net"
                        or target.port not in (None, 443)
                        or target.username
                        or target.password
                        or target.query
                        or target.fragment
                        or not re.fullmatch(
                            rf"/k/{state['session_id']}/[A-Za-z0-9_.-]+/proxy", target.path
                        )
                    ):
                        raise SessionError("KAGGLE_GATEWAY_IDENTITY_MISMATCH")
                    state.update(jupyter_url=url, phase="ready")
                self._write(state)
            return self._public(state)

    @property
    def jupyter_url(self):
        with self._locked():
            state = self._read()
            if state["phase"] != "ready":
                raise SessionError("KAGGLE_SESSION_NOT_READY")
            return state["jupyter_url"]

    @property
    def cleanup_url(self):
        with self._locked():
            state = self._read()
            if state["phase"] not in {"ready", "stop_pending", "stop_requested"} or not state.get(
                "jupyter_url"
            ):
                raise SessionError("KAGGLE_SESSION_NOT_READY")
            return state["jupyter_url"]

    def stop(self):
        with self._locked(), self.api.build_kaggle_client() as client:
            state = self._read()
            # Cleanup uses the recorded creation, independent of later notebook edits/deletion.
            owner = state["kernel"].split("/")[0]
            username = self.api.config_values.get("username")
            if not isinstance(username, str) or username.casefold() != owner.casefold():
                raise SessionError("KAGGLE_OWNERSHIP_UNCONFIRMED")
            if type(state.get("session_id")) is not int or state["session_id"] <= 0:
                raise SessionError("KAGGLE_SESSION_ID_UNAVAILABLE")
            if state.get("operation") != f"operations/create-kernel-session/{state['session_id']}":
                raise SessionError("KAGGLE_SESSION_IDENTITY_MISMATCH")
            if state["phase"] in {"stop_requested", "access_revoked"}:
                return self._public(state)
            # Persist intent first so an uncertain outcome remains visible for reconciliation.
            state["phase"] = "stop_pending"
            self._write(state)
            response = client.kernels.kernels_api_client.cancel_kernel_session(
                _request("ApiCancelKernelSessionRequest", kernel_session_id=state["session_id"])
            )
            if response.error_message:
                raise SessionError("KAGGLE_STOP_REJECTED")
            state["phase"] = "stop_requested"
            self._write(state)
            return self._public(state)

    def confirm_access_revoked(self, status_code: int):
        """Record an observed gateway revocation without claiming GPU cleanup completed."""
        with self._locked():
            state = self._read()
            if state["phase"] == "access_revoked" and status_code in {403, 404, 410}:
                return self._public(state)
            if state["phase"] != "stop_requested" or status_code not in {403, 404, 410}:
                raise SessionError("KAGGLE_CLEANUP_UNCONFIRMED")
            state.update(phase="access_revoked", gateway_status=status_code)
            state.pop("jupyter_url", None)
            self._write(state)
            return self._public(state)
