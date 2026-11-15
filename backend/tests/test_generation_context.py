"""Generation budgets use the pinned Qwen tokenizer, without loading its weights."""

import os
from datetime import UTC, datetime, timedelta
from textwrap import fill
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest
from documind import reflection_context, runtime_client
from documind.errors import AppError
from documind.reflection import END, SYMBOLS, digest, protocol_identity, register_tokens
from documind.reflection_model import TARGET
from documind.reflection_runtime import application, decoder
from documind.runtime_protocol import PROTOCOL, Capabilities, check_result, make_step
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_phase2 import models as models
from test_phase2 import path, process, rebuild, run_query
from test_phase2 import phase as phase
from test_reflection_runtime import SECRET, completed


@pytest.fixture
def generation():
    if os.getenv("DOCUMIND_TEST_RETRIEVAL") != "1":
        pytest.skip("Enable DOCUMIND_TEST_RETRIEVAL after caching the generation tokenizer")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(TARGET[0], revision=TARGET[1], local_files_only=True)
    ids = register_tokens(tokenizer)
    identity = protocol_identity()
    cap = Capabilities(
        protocol=PROTOCOL,
        runtime_instance_id=uuid4(),
        manifest_sha256="a" * 64,
        protocol_sha256=identity["protocol_sha256"],
        serializer_sha256=identity["serializer_sha256"],
        chat_template_sha256=digest(tokenizer.chat_template.encode()),
        model_id=TARGET[0],
        model_revision=TARGET[1],
        supported_modes=["self_rag"],
        context_limit=512,
        vocab_size=len(tokenizer),
        token_ids=ids,
        native_special_ids=sorted(set(tokenizer.all_special_ids) - set(ids.values())),
        cancellation=True,
        candidate_evidence_limit=2,
    )
    return tokenizer, cap


@pytest.mark.parametrize("filler", ["document processing ", "1234567890 "])
@pytest.mark.parametrize("position", ["head", "tail"])
def test_real_generation_budget_windows_valid_retriever_chunks(
    generation, models, filler, position
):
    import torch

    tokenizer, cap = generation
    field, padding = "Grand total: INR 1100.00", ""

    def passage():
        body = field + "\n" + padding if position == "head" else padding + "\n" + field
        return "Invoice number: INV-100\n" + body

    while len(models.tokenizer(passage() + filler, add_special_tokens=False)["input_ids"]) <= 238:
        padding += filler
    quote = passage()
    assert len(models.tokenizer(quote, add_special_tokens=False)["input_ids"]) <= 240
    raw = "Earlier text\n" + quote
    start = len("Earlier text\n")
    anchor = {
        "source_id": str(uuid4()),
        "sha256": digest(raw.encode()),
        "page_number": 1,
        "start_char": start,
        "end_char": len(raw),
        "quote": quote,
    }
    question = "What is the total on invoice INV-100?"
    context = {"question": question, "segments": [], "evidence": [anchor]}
    initial_count = reflection_context.input_tokens(tokenizer, cap, "CANDIDATE", context)
    assert initial_count + 132 > cap.context_limit

    class ScriptedModel:
        def __init__(self, sequence):
            self.sequence = iter(sequence)

        def get_input_embeddings(self):
            return SimpleNamespace(weight=torch.empty(0))

        def __call__(self, tokens, **kwargs):
            assert tokens.shape[1] <= cap.context_limit
            logits = torch.full((1, 1, len(tokenizer)), -100.0)
            logits[0, 0, next(self.sequence)] = 100
            return SimpleNamespace(logits=logits)

    def request(ctx):
        return make_step(
            cap, uuid4(), "CANDIDATE", ctx, 2048, datetime.now(UTC) + timedelta(seconds=30)
        )

    rejected = decoder(ScriptedModel([]), tokenizer, cap)(request(context), Event())
    assert rejected.finish_reason == "context_limit" and rejected.output_tokens == 0
    window = reflection_context.evidence_window(tokenizer, cap, question, anchor)
    assert window is not None and field in window["quote"]
    assert window["quote"] == raw[window["start_char"] : window["end_char"]]
    assert window["sha256"] == anchor["sha256"] and window["source_id"] == anchor["source_id"]
    context["evidence"] = [window]
    assert reflection_context.input_tokens(tokenizer, cap, "CANDIDATE", context) + 132 <= 512
    answer = "INR 1100.00"
    sequence = [cap.token_ids["[IsREL:Yes]"]] + tokenizer.encode(answer, add_special_tokens=False)
    sequence += [cap.token_ids[symbol] for symbol in (END, "[IsSUP:Fully]", "[IsUSE:5]")]
    step = request(context)
    accepted = decoder(ScriptedModel(sequence), tokenizer, cap)(step, Event())
    assert accepted.text == answer and accepted.finish_reason == "complete"
    assert check_result(accepted, step, cap)["support"] == "[IsSUP:Fully]"


def test_exact_tokenizer_identity_and_unfittable_context_fail_closed(generation):
    tokenizer, cap = generation
    assert reflection_context.tokenizer_for(cap).get_vocab() == tokenizer.get_vocab()
    with pytest.raises(AppError, match="RUNTIME_TOKENIZER_NOT_READY"):
        reflection_context.tokenizer_for(cap.model_copy(update={"chat_template_sha256": "0" * 64}))
    quote = "Grand total: INR 1100.00"
    anchor = {
        "source_id": str(uuid4()),
        "sha256": digest(quote.encode()),
        "page_number": 1,
        "start_char": 0,
        "end_char": len(quote),
        "quote": quote,
    }
    question = "What is the total?"
    assert reflection_context.evidence_window(tokenizer, cap, question, anchor) is anchor
    assert reflection_context.evidence_window(tokenizer, cap, question * 100, anchor) is None
    no_field = {**anchor, "quote": "東京 " * 500, "end_char": 1500}
    assert reflection_context.evidence_window(tokenizer, cap, question, no_field) is None
    assert set(cap.token_ids) == set(SYMBOLS)


def test_paired_candidate_requires_disjoint_spans_from_one_source(generation):
    _, cap = generation
    first = {
        "source_id": str(uuid4()),
        "sha256": "a" * 64,
        "page_number": 1,
        "start_char": 0,
        "end_char": 3,
        "quote": "ABC",
    }
    second = {**first, "source_id": str(uuid4()), "start_char": 7, "end_char": 10}

    def request(runtime, spans):
        return make_step(
            runtime,
            uuid4(),
            "CANDIDATE",
            {"question": "A question", "segments": [], "evidence": spans},
            2048,
            datetime.now(UTC) + timedelta(seconds=30),
        )

    assert len(request(cap, [first, second]).context.evidence) == 2
    with pytest.raises(ValueError, match="admitted evidence limit"):
        request(cap.model_copy(update={"candidate_evidence_limit": 1}), [first, second])
    for invalid in (
        {**second, "sha256": "b" * 64},
        {**second, "page_number": 2},
        {**second, "start_char": 1, "end_char": 4},
    ):
        with pytest.raises(ValueError, match="disjoint excerpts"):
            request(cap, [first, invalid])


@pytest.mark.integration
@pytest.mark.parametrize(
    "case", ["supported", "tail", "tail_wrong_id", "table", "too_small", "wrong_token_count"]
)
def test_real_pdf_retrieval_windows_reach_scoped_query_and_table_verifier(
    client,
    phase,
    models,
    generation,
    monkeypatch,
    case,
):
    tokenizer, cap = generation
    contradictory = case == "table"
    if case == "too_small":
        cap = cap.model_copy(update={"context_limit": 1})
    contexts = []

    def scripted_decode(step, cancel):
        contexts.append(step.context.model_dump())
        count = reflection_context.input_tokens(tokenizer, cap, step.state, contexts[-1])
        assert count + reflection_context.reserve(step.state, step.remaining_tokens) <= 512
        labels = {"decision": "[Finish]" if step.context.segments else "[Retrieve]"}
        if step.state == "CANDIDATE":
            labels = {
                "relevance": "[IsREL:Yes]",
                "support": "[IsSUP:Fully]",
                "utility": "[IsUSE:5]",
            }
            if case == "tail":
                quotes = [span.quote for span in step.context.evidence]
                if any("Grand total" in quote for quote in quotes):
                    assert quotes == ["Invoice number: INV-100", "Grand total: INR 1100.00"]
                else:
                    labels = {"relevance": "[IsREL:No]"}
        result = completed(cap, step, labels, "INR 1100.00" if "support" in labels else "")
        return result.model_copy(update={"input_tokens": count + int(case == "wrong_token_count")})

    service = TestClient(
        application(cap, scripted_decode, SECRET),
        headers={
            "Authorization": "Bearer " + SECRET,
        },
    )

    def relay(cfg, route, method, *, body=None, timeout=2, send_guard=None):
        if send_guard:
            with send_guard():
                pass
        response = service.request(method, route, json=body)
        assert response.status_code == 200, response.text
        return response.json()

    monkeypatch.setattr(runtime_client, "call", relay)
    monkeypatch.setattr(phase.cfg, "self_rag_enabled", True)
    monkeypatch.setattr(phase.cfg, "self_rag_runtime_origin", "http://127.0.0.1:8010")
    monkeypatch.setattr(phase.cfg, "self_rag_runtime_token", SecretStr(SECRET))
    monkeypatch.setattr(phase.cfg, "self_rag_manifest_sha256", cap.manifest_sha256)
    if case in {"too_small", "wrong_token_count"}:
        query = run_query(
            client, phase, {"mode": "self_rag", "question": "What is 1 plus 1?"}, case
        )
        result = client.get(query["result_url"]).json()
        if case == "too_small":
            assert not contexts and result["grounding_status"] == "insufficient_evidence"
            trace = client.get(query["events_url"]).json()["items"]
            assert any(row["payload"].get("reason") == "context_limit" for row in trace)
        else:
            assert len(contexts) == 1 and result["error"]["code"] == "INVALID_RUNTIME_RESPONSE"
        return
    with pymupdf.open() as document:
        page = document.new_page(width=600, height=1000)
        identity = "INV-100X" if case == "tail_wrong_id" else "INV-100"
        field = "Grand total: INR 1100.00"
        padding = fill("document processing " * 160, width=90)
        middle = f"{padding}\n{field}" if case.startswith("tail") else f"{field}\n{padding}"
        body = f"Invoice number: {identity}\n{middle}"
        page.insert_text((40, 50), body, fontsize=9)
        if contradictory:
            page = document.new_page(width=320, height=340)
            for x in (30, 165, 290):
                page.draw_line((x, 50), (x, 135))
            for y in (50, 90, 135):
                page.draw_line((30, y), (290, y))
            for x, y, label in (
                (40, 75, "Label"),
                (175, 75, "Amount"),
                (40, 115, "Grand total"),
                (175, 115, "INR 2200.00"),
            ):
                page.insert_text((x, y), label)
        receipt = phase.upload(phase.cfg.local_workspace_id, document.tobytes(), "long.pdf", "long")
    process(receipt["job_id"])
    if contradictory:
        reviewed = phase.reprocess(
            phase.cfg.local_workspace_id,
            receipt["document_id"],
            "review",
            review={
                "base_ingestion_version": receipt["latest_ingestion_version"],
                "confirm_all_pages": True,
                "corrections": [],
            },
        )
        process(reviewed["job_id"])
    process(rebuild(client, phase, "index"))
    query = run_query(
        client,
        phase,
        {
            "mode": "self_rag",
            "question": "What is the total on invoice INV-100?",
            "document_ids": [str(receipt["document_id"])],
        },
        "long-query",
    )
    result = client.get(query["result_url"]).json()
    assert result["status"] == "succeeded", result
    abstain = contradictory or case == "tail_wrong_id"
    assert result["grounding_status"] == ("insufficient_evidence" if abstain else "supported")
    events = client.get(query["events_url"]).json()["items"]
    assert any(row["event_type"] == "evidence_windowed" for row in events)
    candidates = [context for context in contexts if context["evidence"]]
    assert any(field in anchor["quote"] for context in candidates for anchor in context["evidence"])
    assert any(row["event_type"] == "identity_evidence" for row in events) == (case == "tail")
    if abstain:
        assert result["citations"] == []
    else:
        citation = result["citations"][0]
        assert citation["quote"] == "Grand total: INR 1100.00"
        source = client.get(path(phase) + "/citations/" + citation["citation_id"])
        assert source.status_code == 200 and source.json()["quote"] == citation["quote"]
