"""Authenticated single-model process for bounded reflection-token decoding."""

import argparse
import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import compare_digest
from threading import Event, Lock
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from documind.reflection import END, MAX_SEGMENT, SYMBOLS, digest, prompt_ids, token_map
from documind.reflection_context import reserve
from documind.reflection_data import CORPUS
from documind.reflection_model import SMOKE, TARGET, base_model
from documind.reflection_review import (
    FAMILIES,
    checked_bundle,
    load_review,
    review_packet,
)
from documind.runtime_protocol import (
    MAX_BODY,
    MAX_SECONDS,
    PROTOCOL,
    Capabilities,
    Result,
    Step,
    check_result,
    make_step,
)

CACHE_LIMIT = 2048


def load(
    bundle,
    expected_sha,
    *,
    pilot,
    corpus=None,
    review=None,
    generated_evaluation=None,
    device="checkpoint",
):
    import torch
    from peft import PeftModel, get_peft_model_state_dict
    from safetensors.torch import load_file
    from transformers import AutoTokenizer

    manifest, ids = checked_bundle(bundle, expected_sha)
    model_identity = manifest["base_model"], manifest["base_revision"]
    precision = manifest["precision"]
    checkpoint_quantized = precision == "nf4-fp16-double-quant"
    quantized = checkpoint_quantized and device in {"checkpoint", "cuda"}
    if pilot:
        if model_identity not in {SMOKE, TARGET} or precision not in {
            "fp32",
            "nf4-fp16-double-quant",
        }:
            raise ValueError("Unsupported pilot model or precision")
    else:
        if (
            model_identity != TARGET
            or manifest.get("mode") != "qlora"
            or not checkpoint_quantized
            or corpus is None
            or review is None
            or generated_evaluation is None
        ):
            raise ValueError("Target model, NF4 and reviewed corpus are required for Self-RAG")
        if json.loads((corpus / "manifest.json").read_bytes()).get("dataset") == CORPUS:
            raise ValueError("The synthetic pilot corpus is ineligible for application release")
        packet = review_packet(bundle, corpus, expected_sha, generated_evaluation)
        try:
            load_review(packet, review)
        except ValidationError:
            raise ValueError("Invalid release review record") from None
    if manifest.get("trained_rows_checked") != len(SYMBOLS):
        raise ValueError("Missing trained reflection row verification")
    if quantized and not torch.cuda.is_available():
        raise ValueError("The NF4 release requires CUDA")
    if not quantized:
        torch.set_num_threads(min(4, torch.get_num_threads()))
    tokenizer = AutoTokenizer.from_pretrained(bundle / "tokenizer", local_files_only=True)
    if (
        token_map(tokenizer) != ids
        or digest(tokenizer.chat_template.encode()) != manifest["chat_template_sha256"]
    ):
        raise ValueError("Checkpoint tokenizer mismatch")
    weights = load_file(bundle / "adapter" / "adapter_model.safetensors")
    rows = {key: tensor for key, tensor in weights.items() if "trainable_tokens_delta" in key}
    if not rows:
        raise ValueError("Missing trained reflection rows in adapter")
    model = base_model(*model_identity, len(tokenizer), quantized, device=device)
    tied = model.config.tie_word_embeddings
    if (
        len(rows) != (1 if tied else 2)
        or any(row.shape != (len(SYMBOLS), model.config.hidden_size) for row in rows.values())
        or any(not torch.isfinite(weight).all() for weight in weights.values())
    ):
        raise ValueError("Incomplete or invalid trained reflection rows")
    model = PeftModel.from_pretrained(model, bundle / "adapter").eval()
    loaded = get_peft_model_state_dict(model, save_embedding_layers=False)
    if weights.keys() != loaded.keys() or any(
        not torch.equal(tensor, loaded[key].cpu()) for key, tensor in weights.items()
    ):
        raise ValueError("Adapter state differs after reload")
    cap = Capabilities(
        protocol=PROTOCOL,
        runtime_instance_id=uuid4(),
        manifest_sha256=expected_sha,
        protocol_sha256=manifest["protocol_sha256"],
        serializer_sha256=manifest["serializer_sha256"],
        chat_template_sha256=manifest["chat_template_sha256"],
        model_id=model_identity[0],
        model_revision=model_identity[1],
        supported_modes=["reflection_pilot" if pilot else "self_rag"],
        context_limit=min(manifest["context_limit"], model.config.max_position_embeddings),
        vocab_size=len(tokenizer),
        token_ids=ids,
        native_special_ids=sorted(set(tokenizer.all_special_ids).difference(ids.values())),
        cancellation=True,
        candidate_evidence_limit=2,
        candidate_scoring=True,
        inference_profile="nf4-cuda"
        if quantized
        else (
            "fp16-mps" if device == "mps" else f"fp32-{device if device != 'checkpoint' else 'cpu'}"
        ),
    )
    decode = decoder(model, tokenizer, cap)
    probe = make_step(
        cap,
        uuid4(),
        "DECIDE",
        {"question": "What is 1 plus 1?", "evidence": [], "segments": []},
        1,
        datetime.now(UTC) + timedelta(seconds=30),
    )
    if "decision" not in check_result(decode(probe, Event()), probe, cap):
        raise ValueError("Runtime decision compatibility probe failed")
    return cap, decode


def decoder(model, tokenizer, capability):
    import torch

    ids = capability.token_ids
    device = model.get_input_embeddings().weight.device
    forbidden = set(tokenizer.all_special_ids) | set(ids.values())
    context_limit = capability.context_limit

    @torch.inference_mode()
    def decode(step, cancel):
        prefix = prompt_ids(tokenizer, ids, step.state, step.context.model_dump())
        generated, ordinary, probabilities = [], [], {}
        finish = "complete"
        text = ""
        forced = 0

        def logits():
            nonlocal finish
            if cancel.is_set():
                finish = "cancelled"
            elif datetime.now(UTC) >= step.deadline:
                finish = "deadline"
            elif len(generated) >= step.remaining_tokens:
                finish = "budget"
            if finish != "complete":
                return None
            values = torch.tensor([prefix + generated], device=device)
            result = model(values, logits_to_keep=1).logits[0, -1].float()
            if cancel.is_set():
                finish = "cancelled"
            elif datetime.now(UTC) >= step.deadline:
                finish = "deadline"
            return result if finish == "complete" else None

        def family(name):
            scores = logits()
            if scores is None:
                return None
            names = FAMILIES[name]
            weights = scores[[ids[symbol] for symbol in names]].softmax(dim=-1).tolist()
            probabilities[name] = dict(zip(names, weights, strict=True))
            selected = max(names, key=probabilities[name].__getitem__)
            generated.append(ids[selected])
            return selected

        if len(prefix) + reserve(step.state, step.remaining_tokens) > context_limit:
            finish = "context_limit"
        elif step.state == "DECIDE":
            family("decision")
        else:
            has_source = bool(step.context.evidence)
            relevance = family("relevance") if has_source else None
            if finish == "complete" and relevance != "[IsREL:No]":
                if step.candidate_text is not None:
                    supplied = tokenizer.encode(step.candidate_text, add_special_tokens=False)
                    if (
                        not 1 <= len(supplied) <= MAX_SEGMENT
                        or forbidden.intersection(supplied)
                        or tokenizer.decode(supplied, skip_special_tokens=False)
                        != step.candidate_text
                    ):
                        finish = "invalid_token"
                    else:
                        for token in [*supplied, ids[END]]:
                            if logits() is None:
                                break
                            generated.append(token)
                            forced += 1
                        ordinary = supplied
                else:
                    while (scores := logits()) is not None:
                        token = int(scores.argmax())
                        generated.append(token)
                        if token == ids[END] and ordinary:
                            break
                        if token in forbidden or len(ordinary) >= MAX_SEGMENT:
                            finish = "invalid_token"
                            break
                        ordinary.append(token)
                if finish == "complete" and has_source:
                    family("support")
                if finish == "complete":
                    family("utility")
                if finish == "complete":
                    text = tokenizer.decode(ordinary, skip_special_tokens=False)
                    if not text.strip() or len(text) > 8192:
                        finish, text = "invalid_token", ""
        return Result(
            **step.model_dump(
                include={
                    "run_id",
                    "step_id",
                    "request_hash",
                    "runtime_instance_id",
                    "manifest_sha256",
                }
            ),
            tokens=generated,
            input_tokens=len(prefix),
            output_tokens=len(generated),
            forced_tokens=forced,
            text=text,
            probabilities=probabilities,
            finish_reason=finish,
        )

    return decode


@dataclass
class Record:
    request_hash: str
    deadline: datetime
    cancellation: Event = field(default_factory=Event)
    result: Result | None = None
    failed: bool = False


def application(capability, decode, secret):
    if len(secret) < 24 or not secret.isascii() or any(char.isspace() for char in secret):
        raise ValueError(
            "Runtime bearer token must contain at least 24 non-whitespace ASCII characters"
        )
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    lock = Lock()
    inference = Lock()
    records = {}

    def authorize(authorization: str | None = Header(default=None)):
        if not compare_digest((authorization or "").encode(), ("Bearer " + secret).encode()):
            raise HTTPException(401, "UNAUTHORIZED")

    @app.get("/runtime/v1/capabilities", dependencies=[Depends(authorize)])
    def capabilities():
        return capability

    def execute(step):
        if (
            step.runtime_instance_id != capability.runtime_instance_id
            or step.manifest_sha256 != capability.manifest_sha256
            or step.mode not in capability.supported_modes
        ):
            raise HTTPException(409, "RUNTIME_IDENTITY_CHANGED")
        with lock:
            current = datetime.now(UTC)
            for identifier in list(records):
                existing = records[identifier]
                if existing.deadline < current and (existing.result is not None or existing.failed):
                    del records[identifier]
            record = records.get(step.step_id)
            if record:
                if record.request_hash != step.request_hash:
                    raise HTTPException(409, "STEP_HASH_CONFLICT")
                if record.failed:
                    raise HTTPException(503, "STEP_FAILED")
                if record.result is None:
                    raise HTTPException(409, "STEP_IN_PROGRESS")
                return record.result
            if not 0 < (step.deadline - current).total_seconds() <= MAX_SECONDS:
                raise HTTPException(422, "INVALID_STEP_DEADLINE")
            if len(records) >= CACHE_LIMIT or not inference.acquire(blocking=False):
                raise HTTPException(429, "RUNTIME_BUSY")
            records[step.step_id] = record = Record(step.request_hash, step.deadline)
        try:
            result = decode(step, record.cancellation)
            check_result(result, step, capability)
            with lock:
                record.result = result
            return result
        except Exception as exc:
            with lock:
                record.failed = True
            raise HTTPException(503, "STEP_FAILED") from exc
        finally:
            inference.release()

    @app.post("/runtime/v1/steps", dependencies=[Depends(authorize)])
    async def step_route(request: Request):
        payload = bytearray()
        async for data in request.stream():
            if len(payload) + len(data) > MAX_BODY:
                raise HTTPException(413, "STEP_TOO_LARGE")
            payload.extend(data)
        try:
            step = Step.model_validate_json(payload)
        except (ValidationError, ValueError):
            raise HTTPException(422, "INVALID_STEP") from None
        if (
            step.state == "CANDIDATE"
            and len(step.context.evidence) > capability.candidate_evidence_limit
        ):
            raise HTTPException(422, "INVALID_STEP")
        if step.candidate_text is not None and not capability.candidate_scoring:
            raise HTTPException(422, "INVALID_STEP")
        return await run_in_threadpool(execute, step)

    @app.post("/runtime/v1/steps/{step_id}/cancel", dependencies=[Depends(authorize)])
    def cancel_step(step_id: str):
        try:
            identifier = UUID(step_id)
        except ValueError:
            raise HTTPException(404, "STEP_NOT_FOUND") from None
        with lock:
            record = records.get(identifier)
            if record is None:
                raise HTTPException(404, "STEP_NOT_FOUND")
            if record.result is not None or record.failed:
                return {"status": "finished"}
            record.cancellation.set()
        return {"status": "cancellation_requested"}

    return app


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-bundle-sha", required=True)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument(
        "--device", choices=("checkpoint", "cpu", "mps", "cuda"), default="checkpoint"
    )
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--generated-evaluation", type=Path)
    parser.add_argument("--host", choices=("127.0.0.1", "::1"), default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8010)
    args = parser.parse_args()
    secret = os.environ.get("DOCUMIND_RUNTIME_TOKEN")
    if not secret:
        parser.error("Set DOCUMIND_RUNTIME_TOKEN in the runtime environment")
    capability, decode = load(
        args.bundle,
        args.expected_bundle_sha,
        pilot=args.pilot,
        corpus=args.corpus,
        review=args.review,
        generated_evaluation=args.generated_evaluation,
        device=args.device,
    )
    uvicorn.run(
        application(capability, decode, secret), host=args.host, port=args.port, access_log=False
    )


if __name__ == "__main__":
    main()
