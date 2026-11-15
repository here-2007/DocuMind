"""Bounded wire contract for the reflection model service."""

from datetime import datetime
from math import isclose, isfinite
from typing import Annotated, Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from documind.reflection import END, MAX_SEGMENT, SYMBOLS, canonical, digest, protocol_identity
from documind.reflection_data import Anchor
from documind.reflection_review import FAMILIES

PROTOCOL = "documind-runtime-v1"
MAX_TOKENS = 2048
MAX_SECONDS = 120
MAX_BODY = 65536
Sha = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Mode = Literal["self_rag", "reflection_pilot"]
State = Literal["DECIDE", "CANDIDATE"]
Token = Annotated[int, Field(ge=0, lt=2**31, strict=True)]
Text = Annotated[str, Field(min_length=1, max_length=2048)]


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Capabilities(Wire):
    protocol: Literal["documind-runtime-v1"]
    runtime_instance_id: UUID
    manifest_sha256: Sha
    protocol_sha256: Sha
    serializer_sha256: Sha
    chat_template_sha256: Sha
    model_id: str = Field(min_length=1, max_length=255)
    model_revision: Sha | Annotated[str, Field(pattern=r"^[a-f0-9]{40}$")]
    supported_modes: list[Mode] = Field(min_length=1, max_length=1)
    context_limit: int = Field(ge=1)
    vocab_size: int = Field(ge=1)
    token_ids: dict[str, Token]
    native_special_ids: list[Token] = Field(max_length=512)
    cancellation: Literal[True]
    candidate_evidence_limit: Literal[1, 2] = 1
    candidate_scoring: bool = False
    inference_profile: str = "checkpoint"

    @model_validator(mode="after")
    def identity(self):
        local = protocol_identity()
        native = set(self.native_special_ids)
        if (
            set(self.token_ids) != set(SYMBOLS)
            or len(set(self.token_ids.values())) != len(SYMBOLS)
            or len(native) != len(self.native_special_ids)
            or native.intersection(self.token_ids.values())
            or any(token >= self.vocab_size for token in native.union(self.token_ids.values()))
            or self.protocol_sha256 != local["protocol_sha256"]
            or self.serializer_sha256 != local["serializer_sha256"]
        ):
            raise ValueError("Incompatible runtime protocol or token map")
        return self


class Context(Wire):
    question: Text
    segments: list[Text] = Field(max_length=8)
    evidence: list[Anchor] = Field(max_length=5)


class Step(Wire):
    run_id: UUID
    step_id: UUID
    request_hash: Sha
    runtime_instance_id: UUID
    manifest_sha256: Sha
    mode: Mode
    state: State
    context: Context
    candidate_text: Text | None = Field(default=None, exclude_if=lambda value: value is None)
    remaining_tokens: int = Field(ge=1, le=MAX_TOKENS, strict=True)
    deadline: Annotated[datetime, AwareDatetime]

    def fingerprint(self):
        return digest(
            canonical(
                self.model_dump(mode="json", exclude={"request_hash"}, exclude_none=True)
            ).encode()
        )

    @model_validator(mode="after")
    def consistency(self):
        if self.request_hash != self.fingerprint():
            raise ValueError("Step request hash mismatch")
        if self.candidate_text is not None and self.state != "CANDIDATE":
            raise ValueError("Only candidate steps can score supplied text")
        if self.state == "CANDIDATE" and len(self.context.evidence) > 1:
            if len(self.context.evidence) != 2:
                raise ValueError("Candidate takes at most two spans")
            first, second = self.context.evidence
            if (
                first.sha256 != second.sha256
                or first.page_number != second.page_number
                or first.source_id == second.source_id
                or any(
                    span.end_char - span.start_char != len(span.quote) for span in (first, second)
                )
                or not (first.end_char <= second.start_char or second.end_char <= first.start_char)
            ):
                raise ValueError("Candidate spans must be disjoint excerpts from one source")
        return self


def make_step(
    capability, run, state, context, tokens, deadline, *, step_id=None, candidate_text=None
):
    if candidate_text is not None and not capability.candidate_scoring:
        raise ValueError("Runtime does not support supplied candidate scoring")
    if state == "CANDIDATE" and len(context["evidence"]) > capability.candidate_evidence_limit:
        raise ValueError("Candidate exceeds the admitted evidence limit")
    payload = {
        "run_id": str(run),
        "step_id": str(step_id or uuid4()),
        "runtime_instance_id": str(capability.runtime_instance_id),
        "manifest_sha256": capability.manifest_sha256,
        "mode": capability.supported_modes[0],
        "state": state,
        "context": Context.model_validate(context).model_dump(mode="json"),
        "remaining_tokens": tokens,
        "deadline": deadline.isoformat().replace("+00:00", "Z"),
    }
    if candidate_text is not None:
        payload["candidate_text"] = candidate_text
    return Step.model_validate(payload | {"request_hash": digest(canonical(payload).encode())})


class Result(Wire):
    run_id: UUID
    step_id: UUID
    request_hash: Sha
    runtime_instance_id: UUID
    manifest_sha256: Sha
    tokens: list[Token] = Field(max_length=MAX_TOKENS)
    input_tokens: int = Field(ge=1, strict=True)
    output_tokens: int = Field(ge=0, le=MAX_TOKENS, strict=True)
    forced_tokens: int = Field(default=0, ge=0, le=MAX_SEGMENT + 1, strict=True)
    text: str = Field(default="", max_length=8192)
    probabilities: dict[str, dict[str, float]]
    finish_reason: Literal[
        "complete", "invalid_token", "budget", "context_limit", "deadline", "cancelled"
    ]


def check_result(result, step, capability):
    for field in ("run_id", "step_id", "request_hash", "runtime_instance_id", "manifest_sha256"):
        if getattr(result, field) != getattr(step, field):
            raise ValueError("Step result identity mismatch")
    if (
        result.output_tokens != len(result.tokens)
        or result.forced_tokens > result.output_tokens
        or result.output_tokens > step.remaining_tokens
        or any(token >= capability.vocab_size for token in result.tokens)
    ):
        raise ValueError("Invalid step token usage")
    if (
        result.finish_reason != "context_limit"
        and result.input_tokens + result.output_tokens > capability.context_limit
    ):
        raise ValueError("Invalid context usage")
    for name, probs in result.probabilities.items():
        if (
            name not in FAMILIES
            or set(probs) != set(FAMILIES[name])
            or any(not isfinite(p) or not 0 <= p <= 1 for p in probs.values())
            or not isclose(sum(probs.values()), 1, abs_tol=1e-5)
        ):
            raise ValueError("Incomplete or invalid reflection probabilities")
    if result.finish_reason != "complete":
        if result.text:
            raise ValueError("Failed step exposed a partial candidate")
        return {}
    ids = capability.token_ids
    choices = {
        name: max(FAMILIES[name], key=probs.__getitem__)
        for name, probs in result.probabilities.items()
    }
    if step.state == "DECIDE":
        if (
            choices.keys() != {"decision"}
            or result.tokens != [ids[choices["decision"]]]
            or result.text
            or result.forced_tokens
        ):
            raise ValueError("Invalid decision step")
        return choices
    if choices == {"relevance": "[IsREL:No]"} and step.context.evidence:
        if result.tokens != [ids["[IsREL:No]"]] or result.text or result.forced_tokens:
            raise ValueError("Invalid irrelevant candidate")
        return choices
    expected = {"relevance", "support", "utility"} if step.context.evidence else {"utility"}
    if choices.keys() != expected or not result.text.strip():
        raise ValueError("Invalid candidate labels")
    if step.candidate_text is not None and result.text != step.candidate_text:
        raise ValueError("Critic changed the supplied candidate")
    first = int(bool(step.context.evidence))
    trailer = [ids[END]] + [ids[choices[k]] for k in ("support", "utility") if k in choices]
    ordinary = result.tokens[first : -len(trailer)]
    if result.forced_tokens != (len(ordinary) + 1 if step.candidate_text is not None else 0):
        raise ValueError("Candidate conditioning token count differs")
    if (
        (
            first
            and (choices["relevance"] != "[IsREL:Yes]" or result.tokens[:1] != [ids["[IsREL:Yes]"]])
        )
        or result.tokens[-len(trailer) :] != trailer
        or not 1 <= len(ordinary) <= MAX_SEGMENT
        or set(ordinary).intersection(set(ids.values()) | set(capability.native_special_ids))
    ):
        raise ValueError("Invalid candidate token sequence")
    return choices
