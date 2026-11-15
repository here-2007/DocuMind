"""Owner-scoped immutable endpoint versions and encrypted local secrets."""

import base64
import hmac
import json
from contextlib import contextmanager
from uuid import uuid4

import sqlalchemy as sa
from cryptography.fernet import Fernet, InvalidToken
from fastapi.encoders import jsonable_encoder

from documind import schema as t
from documind.chat import PROTOCOL, complete, endpoint_target
from documind.errors import AppError
from documind.parser import fingerprint
from documind.phase_jobs import lock_workspace, queue, record_request, request, terminal
from documind.repository import now


def secret_box(cfg):
    try:
        key = cfg.inference_secret_key.get_secret_value().encode()
        return key, Fernet(key)
    except (AttributeError, ValueError) as exc:
        raise AppError("INFERENCE_SECRET_STORE_UNAVAILABLE", 503) from exc


def connection(c, workspace, identifier):
    item = (
        c.execute(
            sa.select(t.connections).where(
                t.connections.c.workspace_id == workspace,
                t.connections.c.owner_principal_id == workspace,
                t.connections.c.id == identifier,
            )
        )
        .mappings()
        .first()
    )
    if not item:
        raise AppError("NOT_FOUND", 404)
    return item


def configuration(c, item):
    return (
        c.execute(
            sa.select(t.connection_versions).where(
                t.connection_versions.c.workspace_id == item["workspace_id"],
                t.connection_versions.c.connection_id == item["id"],
                t.connection_versions.c.config_version == item["config_version"],
            )
        )
        .mappings()
        .one()
    )


def projection(c, item):
    config = configuration(c, item)
    capability = None
    if item["status"] == "ready":
        capability = c.scalar(
            sa.select(t.capability_checks.c.result).where(
                t.capability_checks.c.workspace_id == item["workspace_id"],
                t.capability_checks.c.id == item["check_id"],
            )
        )
    return {
        **dict(item),
        "kind": "custom_endpoint",
        "protocol": PROTOCOL,
        "model_id": config["model_id"],
        "endpoint_url": config["endpoint_url"],
        "supported_modes": ["baseline_rag"] if capability else [],
        "capability": capability,
    }


def write_connection(repo, workspace, body, key, identifier=None):
    repo.authorize(workspace)
    if not repo.cfg.generation_enabled:
        raise AppError("GENERATION_DISABLED", 409)
    raw_key, box = secret_box(repo.cfg)
    digest = hmac.digest(
        hmac.digest(base64.urlsafe_b64decode(raw_key), b"documind:request:v1", "sha256"),
        json.dumps({"id": str(identifier), **body}, sort_keys=True).encode(),
        "sha256",
    ).hex()
    operation = "connection_patch" if identifier else "connection_create"
    with repo.engine.begin() as c:
        previous = request(c, workspace, operation, key, digest)
        if previous:
            return previous["response"]
        _, _, _, local = endpoint_target(body["endpoint_url"], repo.cfg.local_chat_endpoint)
        credential = body["api_key"]
        if not credential and not local:
            raise AppError("ENDPOINT_CREDENTIAL_REQUIRED", 422)
        if identifier:
            item = connection(c, workspace, identifier)
            if item["status"] == "revoked":
                raise AppError("CONNECTION_UNAVAILABLE", 409)
            if item["config_version"] != body["expected_config_version"]:
                raise AppError("CONNECTION_VERSION_CONFLICT", 409)
            config_version = item["config_version"] + 1
            erase_secrets(c, workspace, identifier)
            c.execute(
                t.connections.update()
                .where(
                    t.connections.c.workspace_id == workspace,
                    t.connections.c.id == identifier,
                )
                .values(
                    display_name=body["display_name"],
                    config_version=config_version,
                    status="configured",
                    check_id=None,
                    error_code=None,
                )
            )
        else:
            identifier, config_version = uuid4(), 1
            c.execute(
                t.connections.insert().values(
                    workspace_id=workspace,
                    id=identifier,
                    owner_principal_id=workspace,
                    display_name=body["display_name"],
                    config_version=config_version,
                )
            )
        secret_id = None
        if credential:
            secret_id = uuid4()
            # Bind ciphertext to its immutable owner and configuration.
            value = json.dumps([str(workspace), str(identifier), config_version, credential])
            c.execute(
                t.secrets.insert().values(
                    workspace_id=workspace,
                    id=secret_id,
                    ciphertext=box.encrypt(value.encode()),
                )
            )
        c.execute(
            t.connection_versions.insert().values(
                workspace_id=workspace,
                connection_id=identifier,
                config_version=config_version,
                endpoint_url=body["endpoint_url"],
                model_id=body["model_id"],
                secret_id=secret_id,
            )
        )
        result = jsonable_encoder(projection(c, connection(c, workspace, identifier)))
        record_request(c, workspace, operation, key, digest, identifier, response=result)
        return result


def erase_secrets(c, workspace, identifier):
    where = (
        t.connection_versions.c.workspace_id == workspace,
        t.connection_versions.c.connection_id == identifier,
        t.connection_versions.c.secret_id.is_not(None),
    )
    ids = c.scalars(sa.select(t.connection_versions.c.secret_id).where(*where)).all()
    c.execute(t.connection_versions.update().where(*where).values(secret_id=None))
    c.execute(
        t.secrets.delete().where(t.secrets.c.workspace_id == workspace, t.secrets.c.id.in_(ids))
    )


def revoke(repo, workspace, identifier):
    repo.authorize(workspace)
    with repo.engine.begin() as c:
        lock_workspace(c, workspace)
        connection(c, workspace, identifier)
        erase_secrets(c, workspace, identifier)
        c.execute(
            t.connections.update()
            .where(
                t.connections.c.workspace_id == workspace,
                t.connections.c.id == identifier,
            )
            .values(status="revoked", check_id=None, error_code=None)
        )


def credential_for(c, cfg, config):
    if not config["secret_id"]:
        return None
    ciphertext = c.scalar(
        sa.select(t.secrets.c.ciphertext).where(
            t.secrets.c.workspace_id == config["workspace_id"],
            t.secrets.c.id == config["secret_id"],
        )
    )
    _, box = secret_box(cfg)
    try:
        binding = json.loads(box.decrypt(ciphertext))
        if binding[:3] != [
            str(config["workspace_id"]),
            str(config["connection_id"]),
            config["config_version"],
        ]:
            raise ValueError("Secret binding mismatch")
        return binding[3]
    except (InvalidToken, TypeError, ValueError, IndexError) as exc:
        raise AppError("INFERENCE_SECRET_STORE_UNAVAILABLE", 503) from exc


def queue_validation(repo, workspace, identifier, key):
    repo.authorize(workspace)
    if not repo.cfg.generation_enabled:
        raise AppError("GENERATION_DISABLED", 409)
    digest = fingerprint({"id": str(identifier)})
    with repo.engine.begin() as c:
        previous = request(c, workspace, "validate", key, digest)
        item = connection(c, workspace, identifier)
        if item["status"] == "revoked":
            raise AppError("CONNECTION_UNAVAILABLE", 409)
        if previous:
            return previous["job_id"]
        job_id = queue(c, workspace, "validate")
        c.execute(
            t.capability_checks.insert().values(
                workspace_id=workspace,
                id=job_id,
                connection_id=identifier,
                config_version=item["config_version"],
            )
        )
        c.execute(
            t.connections.update()
            .where(
                t.connections.c.workspace_id == workspace,
                t.connections.c.id == identifier,
            )
            .values(status="configured", check_id=job_id, error_code=None)
        )
        record_request(c, workspace, "validate", key, digest, identifier, job_id)
        return job_id


def ready_connection(c, workspace, identifier, version=None, check_id=None):
    item = connection(c, workspace, identifier)
    if item["status"] != "ready" or (
        version is not None and (item["config_version"] != version or item["check_id"] != check_id)
    ):
        raise AppError("CONNECTION_UNAVAILABLE", 409)
    return item


def validate_job(repo, job):
    workspace, identifier = job["workspace_id"], job["id"]
    with repo.engine.begin() as c:
        lock_workspace(c, workspace)
        repo._fence(c, job)
        check = (
            c.execute(
                sa.select(t.capability_checks).where(
                    t.capability_checks.c.workspace_id == workspace,
                    t.capability_checks.c.id == identifier,
                )
            )
            .mappings()
            .one()
        )
        item = connection(c, workspace, check["connection_id"])
        if item["check_id"] != identifier or item["status"] != "configured":
            raise AppError("CONNECTION_UNAVAILABLE", 409)
        config = configuration(c, item)
        secret = credential_for(c, repo.cfg, config)
    if not repo.cfg.generation_enabled:
        raise AppError("GENERATION_DISABLED", 409)

    @contextmanager
    def send_guard():
        with repo.engine.begin() as c:
            lock_workspace(c, workspace)
            repo._fence(c, job)
            current = connection(c, workspace, check["connection_id"])
            if current["check_id"] != identifier or current["status"] != "configured":
                raise AppError("CONNECTION_UNAVAILABLE", 409)
            yield

    reported, _ = complete(
        config["endpoint_url"],
        config["model_id"],
        secret,
        [{"role": "user", "content": "Reply with the word ready."}],
        local_url=repo.cfg.local_chat_endpoint,
        timeout=repo.cfg.inference_timeout_seconds,
        send_guard=send_guard,
    )
    with repo.engine.begin() as c:
        lock_workspace(c, workspace)
        repo._fence(c, job)
        item = connection(c, workspace, check["connection_id"])
        if item["check_id"] != identifier or item["status"] != "configured":
            raise AppError("CONNECTION_UNAVAILABLE", 409)
        c.execute(
            t.capability_checks.update()
            .where(
                t.capability_checks.c.workspace_id == workspace,
                t.capability_checks.c.id == identifier,
            )
            .values(
                result={
                    "reported_model_id": reported,
                    "requested_model_id": config["model_id"],
                    "revision_kind": "provider_managed",
                    "context_window": None,
                    "streaming": False,
                    "upstream_cancellation": False,
                    "verified_at": now().isoformat(),
                }
            )
        )
        c.execute(
            t.connections.update()
            .where(
                t.connections.c.workspace_id == workspace,
                t.connections.c.id == item["id"],
            )
            .values(status="ready")
        )
        terminal(c, job, "succeeded")
