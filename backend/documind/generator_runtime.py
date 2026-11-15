"""One authenticated OpenAI-compatible generator on the operator's device."""

import argparse
import os
from secrets import compare_digest
from threading import Lock
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator

from documind.reflection_model import checked_checkpoint


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=16384)


class Completion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str
    messages: list[Message] = Field(min_length=1, max_length=16)
    max_tokens: int = Field(default=48, ge=1, le=128)
    temperature: Literal[0] = 0
    stream: Literal[False] = False

    @model_validator(mode="after")
    def bounded(self):
        if sum(len(row.content) for row in self.messages) > 24576:
            raise ValueError("Messages exceed the local generation budget")
        return self


def application(model_id, generate, token):
    if len(token) < 24 or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError("Use a private generator token of at least 24 characters")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    active = Lock()

    def authorize(authorization: str | None = Header(default=None)):
        if not authorization or not compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "UNAUTHORIZED")

    @app.get("/health", dependencies=[Depends(authorize)])
    def health():
        return {"model": model_id, "status": "ready"}

    @app.post("/v1/chat/completions", dependencies=[Depends(authorize)])
    def complete(body: Completion):
        if body.model != model_id:
            raise HTTPException(422, "MODEL_MISMATCH")
        if not active.acquire(blocking=False):
            raise HTTPException(429, "GENERATOR_BUSY")
        try:
            text = generate(body)
            if not text.strip() or len(text) > 8192:
                raise HTTPException(502, "INVALID_GENERATION")
            return {
                "model": model_id,
                "choices": [{"message": {"role": "assistant", "content": text}}],
            }
        finally:
            active.release()

    return app


def load(model_id, revision, device):
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if device == "auto":
        device = (
            "cuda"
            if torch.cuda.is_available()
            else ("mps" if torch.backends.mps.is_available() else "cpu")
        )
    if device not in {"cpu", "mps", "cuda"}:
        raise ValueError("Unsupported generator device")
    snapshot = checked_checkpoint(model_id, revision)
    tokenizer = AutoTokenizer.from_pretrained(model_id, revision=revision, trust_remote_code=False)
    model = (
        AutoModelForCausalLM.from_pretrained(
            snapshot,
            local_files_only=True,
            use_safetensors=True,
            trust_remote_code=False,
            dtype=torch.float32 if device == "cpu" else torch.float16,
        )
        .to(device)
        .eval()
    )
    limit = min(model.config.max_position_embeddings, 8192)
    if device == "cpu":
        torch.set_num_threads(min(4, torch.get_num_threads()))

    @torch.inference_mode()
    def generate(body):
        inputs = tokenizer.apply_chat_template(
            [row.model_dump() for row in body.messages],
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        ).to(device)
        size = inputs["input_ids"].shape[1]
        if size + body.max_tokens > limit:
            raise HTTPException(422, "CONTEXT_LIMIT")
        output = model.generate(
            **inputs,
            max_new_tokens=body.max_tokens,
            do_sample=False,
            max_time=30,
            pad_token_id=tokenizer.eos_token_id,
        )
        return tokenizer.decode(output[0, size:], skip_special_tokens=True).strip()

    return generate


def main():
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--device", choices=("auto", "cpu", "mps", "cuda"), default="auto")
    parser.add_argument("--port", type=int, default=8011)
    args = parser.parse_args()
    token = os.getenv("DOCUMIND_GENERATOR_TOKEN", "")
    if len(token) < 24:
        parser.error("Set DOCUMIND_GENERATOR_TOKEN before loading weights")
    generate = load(args.model, args.revision, args.device)
    uvicorn.run(
        application(args.model, generate, token), host="127.0.0.1", port=args.port, access_log=False
    )


if __name__ == "__main__":
    main()
