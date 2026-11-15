"""Access the operator-pinned reflection service through the bounded transport."""

from contextlib import nullcontext, suppress
from urllib.parse import urlsplit

from documind.chat import endpoint_target, json_request
from documind.errors import AppError
from documind.reflection_model import TARGET
from documind.runtime_protocol import Capabilities, Result, check_result

CAPABILITIES = "/runtime/v1/capabilities"
STEPS = "/runtime/v1/steps"


def endpoint(origin, path):
    if (
        not origin
        or urlsplit(origin).path
        or origin.endswith("/")
        or urlsplit(origin).hostname not in {"127.0.0.1", "::1"}
    ):
        raise AppError("ENDPOINT_NOT_ALLOWED", 422)
    url = origin + path
    endpoint_target(url, url, path)
    return url


def call(cfg, path, method, *, body=None, timeout=2, send_guard=nullcontext):
    url = endpoint(cfg.self_rag_runtime_origin, path)
    return json_request(
        url,
        method,
        cfg.self_rag_runtime_token.get_secret_value(),
        body,
        local_url=url,
        suffix=path,
        timeout=timeout,
        send_guard=send_guard,
    )


def admitted(cfg):
    if not cfg.self_rag_enabled:
        raise AppError("MODEL_NOT_READY", 409)
    try:
        cap = Capabilities.model_validate(call(cfg, CAPABILITIES, "GET"))
    except (AppError, ValueError) as exc:
        raise AppError("MODEL_NOT_READY", 409) from exc
    if (
        cap.manifest_sha256 != cfg.self_rag_manifest_sha256
        or (cap.model_id, cap.model_revision) != TARGET
        or cap.supported_modes != ["self_rag"]
        or (
            getattr(cfg, "self_rag_inference_profile", None) is not None
            and cap.inference_profile != cfg.self_rag_inference_profile
        )
    ):
        raise AppError("MODEL_NOT_READY", 409)
    from documind.reflection_context import tokenizer_for

    tokenizer_for(cap)
    return cap


def invoke(cfg, cap, step, timeout, send_guard):
    try:
        data = call(
            cfg,
            STEPS,
            "POST",
            body=step.model_dump(mode="json"),
            timeout=timeout,
            send_guard=send_guard,
        )
        result = Result.model_validate(data)
        return result, check_result(result, step, cap)
    except Exception as exc:
        with suppress(Exception):
            cancel(cfg, step.step_id)
        if isinstance(exc, ValueError):
            raise AppError("INVALID_RUNTIME_RESPONSE", 502) from exc
        raise


def cancel(cfg, step_id):
    call(cfg, f"{STEPS}/{step_id}/cancel", "POST")
