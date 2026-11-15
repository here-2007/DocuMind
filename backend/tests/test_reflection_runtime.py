"""Tests keep synthetic protocol responders distinct from the actual pilot checkpoint."""

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from socket import socket
from threading import Event, Thread
from time import monotonic, sleep
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import uvicorn
from documind import runtime_client, selfrag
from documind import schema as t
from documind.errors import AppError
from documind.reflection import END, SYMBOLS, canonical, digest, protocol_identity
from documind.reflection_data import episode_context
from documind.reflection_model import SMOKE, TARGET
from documind.reflection_pilot import create_corpus
from documind.reflection_review import FAMILIES
from documind.reflection_runtime import application, load
from documind.repository import now
from documind.runtime_probe import compare
from documind.runtime_protocol import PROTOCOL, Capabilities, Result, make_step
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_phase2 import chat_server as chat_server
from test_phase2 import make_connection, path, process, rebuild, run_query, upload
from test_phase2 import models as models
from test_phase2 import phase as phase

SECRET = "unit-test-runtime-secret-0001"


@contextmanager
def loopback(app):
    with socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", access_log=False))
        runner = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        runner.start()
        try:
            stop = monotonic() + 8
            while not server.started and monotonic() < stop:
                sleep(0.025)
            assert server.started
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            runner.join(timeout=8)


def capability(mode="reflection_pilot"):
    base = TARGET if mode == "self_rag" else SMOKE
    ids = dict(zip(SYMBOLS, range(100, 115), strict=True))
    identity = protocol_identity()
    return Capabilities(
        protocol=PROTOCOL,
        runtime_instance_id=uuid4(),
        manifest_sha256="a" * 64,
        model_id=base[0],
        model_revision=base[1],
        protocol_sha256=identity["protocol_sha256"],
        serializer_sha256=identity["serializer_sha256"],
        chat_template_sha256=digest(b"scripted tokenizer fixture"),
        context_limit=512,
        vocab_size=128,
        supported_modes=[mode],
        token_ids=ids,
        native_special_ids=[0, 1, 2],
        cancellation=True,
    )


@pytest.fixture(autouse=True)
def scripted_context_tokenizer(monkeypatch):
    """Scripted protocol responders do not claim actual model token counts."""
    from types import SimpleNamespace

    monkeypatch.setattr(
        "documind.reflection_context.tokenizer_for",
        lambda cap: SimpleNamespace(
            apply_chat_template=lambda *args, **kwargs: [3] * 30,
        ),
    )


def completed(cap, step, choices, text="", finish="complete"):
    ids = cap.token_ids
    if finish != "complete":
        tokens = []
    elif "decision" in choices:
        tokens = [ids[choices["decision"]]]
    elif choices.get("relevance") == "[IsREL:No]":
        tokens = [ids["[IsREL:No]"]]
    else:
        tokens = (
            ([ids["[IsREL:Yes]"]] if "relevance" in choices else [])
            + [10, ids[END]]
            + [ids[choices[k]] for k in ("support", "utility") if k in choices]
        )
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
        tokens=tokens,
        input_tokens=30,
        output_tokens=len(tokens),
        forced_tokens=(2 if step.candidate_text and "utility" in choices else 0),
        text=text,
        probabilities={
            family: {symbol: float(symbol == choice) for symbol in FAMILIES[family]}
            for family, choice in choices.items()
        },
        finish_reason=finish,
    )


def build_step(cap, *, context=None):
    return make_step(
        cap,
        uuid4(),
        "DECIDE",
        context
        or {
            "question": "What is 1 plus 1?",
            "segments": [],
            "evidence": [],
        },
        20,
        datetime.now(UTC) + timedelta(seconds=20),
    )


def test_runtime_replay_identity_size_and_cancellation():
    cap = capability()
    entered = Event()
    calls = []

    def decode(step, cancel):
        calls.append(step.step_id)
        entered.set()
        assert cancel.wait(5)
        return completed(cap, step, {}, finish="cancelled")

    api = TestClient(application(cap, decode, SECRET))
    assert api.get("/runtime/v1/capabilities").status_code == 401
    api.headers["Authorization"] = "Bearer " + SECRET
    assert api.get("/runtime/v1/capabilities").json()["supported_modes"] == ["reflection_pilot"]
    step = build_step(cap)
    body = step.model_dump(mode="json")
    endpoint = "/runtime/v1/steps"
    assert api.post(endpoint, content=b"x" * 65537).status_code == 413
    assert api.post(endpoint, json=body | {"remaining_tokens": 19}).status_code == 422
    with ThreadPoolExecutor() as pool:
        future = pool.submit(api.post, endpoint, json=body)
        assert entered.wait(5)
        assert api.post(endpoint, json=body).json()["detail"] == "STEP_IN_PROGRESS"
        conflict = body | {"remaining_tokens": 19}
        conflict["request_hash"] = digest(
            canonical({k: v for k, v in conflict.items() if k != "request_hash"}).encode()
        )
        assert api.post(endpoint, json=conflict).json()["detail"] == "STEP_HASH_CONFLICT"
        assert api.post(endpoint + f"/{step.step_id}/cancel").status_code == 200
        result = future.result(timeout=5)
    assert result.json()["finish_reason"] == "cancelled"
    assert api.post(endpoint, json=body).json() == result.json()
    assert calls == [step.step_id]
    newer = TestClient(application(capability(), decode, SECRET), headers=api.headers)
    assert newer.post(endpoint, json=body).json()["detail"] == "RUNTIME_IDENTITY_CHANGED"


def test_control_loop_reuses_evidence_and_rejects_unsupported_claim():
    from types import SimpleNamespace

    source = {"source_id": "chunk-1"}
    actions = iter(["[Finish]", "[NoRetrieve]", "[Finish]"])
    retrievals, contexts, traces = [], [], []
    texts = iter(["valid", "unsupported"])

    def step(state, context, budget):
        contexts.append((state, list(context["segments"]), list(context["evidence"]), budget))
        labels = (
            {"decision": next(actions)}
            if state == "DECIDE"
            else {
                "relevance": "[IsREL:Yes]",
                "support": "[IsSUP:Fully]",
                "utility": "[IsUSE:5]",
            }
        )
        result = SimpleNamespace(
            output_tokens=1,
            finish_reason="complete",
            text="" if state == "DECIDE" else next(texts),
            probabilities={
                key: {name: float(name == value) for name in FAMILIES[key]}
                for key, value in labels.items()
            },
        )
        return result, labels

    def fetch():
        retrievals.append(True)
        return [([source], source)]

    result = selfrag.run(
        "Question",
        True,
        step,
        fetch,
        lambda text, _source: {"quote": "valid"} if text == "valid" else None,
        lambda kind, **data: traces.append((kind, data)),
    )
    assert result == {
        "segments": ["valid"],
        "citations": [{"quote": "valid"}],
        "retrieval_calls": 1,
    }
    assert retrievals == [True]
    assert contexts[0][2] == [] and contexts[1][2] == [source]
    assert contexts[2][1:3] == (["valid"], [source])
    assert all(a[3] > b[3] for a, b in zip(contexts, contexts[1:], strict=False))
    assert [data["effective_action"] for kind, data in traces if kind == "reflection_token"] == [
        "[Retrieve]",
        "[Continue]",
    ]


def test_control_loop_bounds_shared_tokens_and_stops_retrieval_cycle():
    from types import SimpleNamespace

    source = {"source_id": "source"}
    for cost, expected in ((2047, 2), (1, 3)):
        calls = []

        def step(state, context, budget, cost=cost, calls=calls):
            calls.append((state, budget))
            labels = (
                {"decision": "[Retrieve]"}
                if state == "DECIDE"
                else {
                    "relevance": "[IsREL:Yes]",
                    "support": "[IsSUP:Fully]",
                    "utility": "[IsUSE:5]",
                }
            )
            return SimpleNamespace(
                output_tokens=1 if state == "DECIDE" else cost,
                finish_reason="complete",
                text="supported",
                probabilities={
                    key: {name: float(name == value) for name in FAMILIES[key]}
                    for key, value in labels.items()
                },
            ), labels

        result = selfrag.run(
            "Question",
            True,
            step,
            lambda: [([source], source)],
            lambda text, source: {"quote": text},
            lambda *args, **kwargs: None,
        )
        assert result["retrieval_calls"] == 1 and len(calls) == expected


@pytest.mark.parametrize("candidate,expected", [("41", ["41"]), ("42", [])])
@pytest.mark.parametrize("initial", ["[Finish]", "[NoRetrieve]"])
def test_general_addition_checks_model_answer_without_retrieval(candidate, expected, initial):
    from types import SimpleNamespace

    def step(state, context, budget):
        assert context["evidence"] == []
        if state == "CANDIDATE":
            return SimpleNamespace(
                output_tokens=3, finish_reason="complete", text=candidate, probabilities={}
            ), {}
        decision = "[Finish]" if context["segments"] else initial
        return SimpleNamespace(output_tokens=1, finish_reason="complete", probabilities={}), {
            "decision": decision,
        }

    def unexpected(*args):
        pytest.fail("General arithmetic must not retrieve or cite document evidence")

    trace = []
    result = selfrag.run(
        "What is 27 plus 14?",
        False,
        step,
        unexpected,
        unexpected,
        lambda kind, **payload: trace.append((kind, payload)),
    )
    assert result == {"segments": expected, "citations": [], "retrieval_calls": 0}
    kind, action = trace[0]
    assert kind == "reflection_token"
    assert action["requested_model_action"] == initial
    assert action["effective_action"] == "[NoRetrieve]"
    assert action["policy_override_reason"] == (
        "answerable_addition" if initial == "[Finish]" else None
    )
    assert any(row.get("decision") == "incorrect_arithmetic" for _, row in trace) == (not expected)


@pytest.mark.parametrize("answer", ["AB-123", "AB-999", "ab-123"])
def test_general_reference_checks_exact_value_without_retrieval(answer):
    from types import SimpleNamespace

    def step(state, context, budget):
        assert not context["evidence"]
        result = SimpleNamespace(output_tokens=1, finish_reason="complete", probabilities={})
        if state == "CANDIDATE":
            result.text = answer
            return result, {"utility": "[IsUSE:5]"}
        return result, {"decision": "[Finish]" if context["segments"] else "[NoRetrieve]"}

    def unexpected(*args):
        pytest.fail("Repeating a supplied reference must not retrieve or cite sources")

    trace = []
    result = selfrag.run(
        "Repeat this supplied reference: AB-123.",
        False,
        step,
        unexpected,
        unexpected,
        lambda _, **row: trace.append(row),
    )
    assert result == {
        "segments": ["AB-123"] if answer == "AB-123" else [],
        "citations": [],
        "retrieval_calls": 0,
    }
    assert any(row.get("decision") == "incorrect_reference" for row in trace) == (
        answer != "AB-123"
    )


@pytest.mark.integration
def test_durable_trained_query_uses_real_retriever_and_source_guard(
    client, phase, models, pdf, monkeypatch
):
    cap = capability("self_rag")
    decision = iter(["[NoRetrieve]", "[Finish]", "[Finish]", "[Finish]", "[Retrieve]"])
    contexts = []
    delete_after_decision = False

    def decode(step, cancel):
        contexts.append(step.context.model_dump())
        if step.state == "DECIDE":
            return completed(cap, step, {"decision": next(decision)})
        if not step.context.evidence:
            return completed(cap, step, {"utility": "[IsUSE:5]"}, "2")
        return completed(
            cap,
            step,
            {
                "relevance": "[IsREL:Yes]",
                "support": "[IsSUP:Fully]",
                "utility": "[IsUSE:5]",
            },
            "INR 1,100.00",
        )

    service = TestClient(
        application(cap, decode, SECRET),
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
        if delete_after_decision and route == runtime_client.STEPS:
            assert client.delete(path(phase) + f"/documents/{doc}").status_code == 202
        return response.json()

    cfg = phase.cfg
    monkeypatch.setattr(runtime_client, "call", relay)
    monkeypatch.setattr(cfg, "self_rag_enabled", True)
    monkeypatch.setattr(cfg, "self_rag_runtime_origin", "http://127.0.0.1:8010")
    monkeypatch.setattr(cfg, "self_rag_runtime_token", SecretStr(SECRET))
    monkeypatch.setattr(cfg, "self_rag_manifest_sha256", cap.manifest_sha256)
    assert client.get("/api/v1/ready").json()["self_rag"]
    doc = upload(phase, pdf, "Invoice number: INV-100\nGrand total: INR 1,100.00", "invoice")
    process(rebuild(client, phase, "reflection-index"))
    question = {"mode": "self_rag", "question": "What is the total on invoice INV-100?"}
    no_scope = client.post(
        path(phase) + "/queries", json=question, headers={"Idempotency-Key": "unscoped"}
    )
    assert no_scope.json()["error"]["code"] == "SOURCE_SCOPE_REQUIRED"
    query = run_query(client, phase, question | {"document_ids": [str(doc)]}, "reflection")
    row = client.get(query["result_url"]).json()
    assert row["status"] == "succeeded", row
    assert row["mode"] == "self_rag" and row["retrieval_calls"] == 1
    assert row["grounding_status"] == "supported" and len(row["citations"]) == 1
    assert row["answer"] == "Grand total: INR 1,100.00"
    citation = row["citations"][0]
    assert (
        client.get(path(phase) + "/citations/" + citation["citation_id"]).json()["quote"]
        == row["answer"]
    )
    assert contexts[0]["evidence"] == [] and contexts[1]["evidence"]
    trace = client.get(query["events_url"]).json()["items"]
    policy = next(item["payload"] for item in trace if item["event_type"] == "reflection_token")
    assert policy["requested_model_action"] == "[NoRetrieve]"
    assert policy["effective_action"] == "[Retrieve]"
    assert sum(item["event_type"] == "runtime_step_completed" for item in trace) == 3
    assert sum(item["event_type"] == "answer" for item in trace) == 1

    general = run_query(
        client, phase, {"mode": "self_rag", "question": "What is 1 plus 1?"}, "general"
    )
    row = client.get(general["result_url"]).json()
    assert row["status"] == "succeeded", row
    assert row["grounding_status"] == "unverified" and row["retrieval_calls"] == 0
    assert row["publication_id"] is None and row["citations"] == []
    assert row["answer"] == "2"
    assert row["tokens_log"][0]["requested_model_action"] == "[Finish]"
    assert row["tokens_log"][0]["effective_action"] == "[NoRetrieve]"
    assert row["tokens_log"][0]["policy_override_reason"] == "answerable_addition"
    assert contexts[-2]["evidence"] == []
    delete_after_decision = True
    before = len(contexts)
    revoked = run_query(
        client, phase, question | {"document_ids": [str(doc)]}, "delete-during-decision"
    )
    assert len(contexts) == before + 1
    assert client.get(revoked["result_url"]).status_code == 410
    assert client.get(query["result_url"]).status_code == 410
    assert client.get(general["result_url"]).status_code == 200


@pytest.mark.integration
@pytest.mark.parametrize("confirmed", [True, False])
@pytest.mark.parametrize("split", [True, False])
def test_real_worker_exit_replays_confirmed_steps_only(
    client, phase, models, pdf, monkeypatch, confirmed, split, chat_server
):
    from documind import reflection_queries

    cap = capability("self_rag").model_copy(update={"candidate_scoring": split})
    url, provider = chat_server
    connection = None
    if split:
        _, connection, _ = make_connection(client, phase, url, monkeypatch)
        provider["messages"].clear()
        provider["generate"] = lambda _: "INR 1,100.00"
    calls = []

    def decode(step, cancel):
        calls.append(step)
        if step.state == "DECIDE":
            return completed(
                cap,
                step,
                {
                    "decision": "[Finish]" if step.context.segments else "[Retrieve]",
                },
            )
        return completed(
            cap,
            step,
            {
                "relevance": "[IsREL:Yes]",
                "support": "[IsSUP:Fully]",
                "utility": "[IsUSE:5]",
            },
            "INR 1,100.00",
        )

    with loopback(application(cap, decode, SECRET)) as origin:
        cfg = phase.cfg
        monkeypatch.setattr(cfg, "self_rag_enabled", True)
        monkeypatch.setattr(cfg, "self_rag_runtime_origin", origin)
        monkeypatch.setattr(cfg, "self_rag_runtime_token", SecretStr(SECRET))
        monkeypatch.setattr(cfg, "self_rag_manifest_sha256", cap.manifest_sha256)
        doc = upload(phase, pdf, "Invoice number: INV-100\nGrand total: INR 1,100.00", "replay")
        process(rebuild(client, phase, "replay-index"))
        query = run_query(
            client,
            phase,
            {
                "mode": "self_rag",
                "question": "What is the total on invoice INV-100?",
                "document_ids": [str(doc)],
                **(
                    {
                        "connection_id": connection["id"],
                        "expected_config_version": connection["config_version"],
                    }
                    if connection
                    else {}
                ),
            },
            "interrupted",
            execute=False,
        )
        identifier = UUID(query["query_run_id"])
        code = """
import os
import sys
from documind import reflection_context, reflection_queries, runtime_client
from types import SimpleNamespace
from documind.worker import process_job
from uuid import UUID
# Match the parent's explicit synthetic tokenizer fixture in this separate process.
reflection_context.tokenizer_for = lambda cap: SimpleNamespace(
    apply_chat_template=lambda *args, **kwargs: [3] * 30,
)
if sys.argv[2] == "confirmed":
    reflection_queries.verified_candidate = lambda *args: os._exit(77)
else:
    invoke = runtime_client.invoke
    def lose_response(*args):
        invoke(*args)
        os._exit(77)
    runtime_client.invoke = lose_response
process_job(UUID(sys.argv[1]))
"""
        child = subprocess.run(
            [sys.executable, "-c", code, str(identifier), "confirmed" if confirmed else "unknown"],
            env=os.environ
            | {
                "SELF_RAG_ENABLED": "true",
                "SELF_RAG_RUNTIME_ORIGIN": origin,
                "SELF_RAG_RUNTIME_TOKEN": SECRET,
                "SELF_RAG_MANIFEST_SHA256": cap.manifest_sha256,
                "INDEXING_ENABLED": "true",
                "QDRANT_COLLECTION": cfg.qdrant_collection,
                "GENERATION_ENABLED": "true",
                "LOCAL_CHAT_ENDPOINT": url,
                "INFERENCE_SECRET_KEY": cfg.inference_secret_key.get_secret_value(),
            },
            timeout=40,
            check=False,
            capture_output=True,
        )
        assert child.returncode == 77, child.stderr.decode(errors="replace")
        prefix = client.get(query["events_url"]).json()["items"]
        with phase.engine.begin() as c:
            progress = c.scalar(
                sa.select(t.query_runs.c.runtime_progress).where(t.query_runs.c.id == identifier)
            )
            c.execute(
                t.jobs.update()
                .where(t.jobs.c.id == identifier)
                .values(lease_expires_at=now() - timedelta(seconds=1))
            )
        assert len(progress["steps"]) == (2 if confirmed else 1)
        assert len(calls) == (2 if confirmed else 1)
        if confirmed:
            assert progress["retrieval"] and progress["steps"][-1]["result"]["text"]

            def repeated_search(*args, **kwargs):
                pytest.fail("A confirmed search must replay its stored passage IDs")

            monkeypatch.setattr(reflection_queries, "retrieve", repeated_search)
            assert identifier in phase.due_jobs()
            process(identifier)
            result = client.get(query["result_url"]).json()
            assert result["status"] == "succeeded", result
            assert result["grounding_status"] == "supported" and len(result["citations"]) == 1
            assert result["retrieval_calls"] == 1 and len(calls) == 3
            assert calls[-1].context.segments == ["INR 1,100.00"]
            assert calls[-1].remaining_tokens == calls[0].remaining_tokens - 6
            if split:
                assert len(provider["messages"]) == 1
                assert progress["steps"][-1]["candidate_text"] == "INR 1,100.00"
        else:
            assert progress["steps"][0]["result"] is None
            assert identifier not in phase.due_jobs()
            process(identifier)
            result = client.get(query["result_url"]).json()
            assert result["error"]["code"] == "GENERATION_OUTCOME_UNKNOWN"
            assert len(calls) == 1

        trace = client.get(query["events_url"]).json()["items"]
        assert trace[: len(prefix)] == prefix
        assert sum(row["event_type"] in {"answer", "error"} for row in trace) == 1
        with phase.engine.connect() as c:
            assert (
                c.scalar(
                    sa.select(t.query_runs.c.runtime_progress).where(
                        t.query_runs.c.id == identifier
                    )
                )
                is None
            )


@pytest.mark.parametrize("failure", ["timeout", "bad_response", "cancel_failure"])
def test_runtime_transport_cancels_failures_without_masking_original(monkeypatch, failure):
    from contextlib import nullcontext

    cap = capability()
    step = build_step(cap)
    calls = []

    def transport(cfg, route, method, **kwargs):
        calls.append((route, method))
        if route.endswith("/cancel"):
            if failure == "cancel_failure":
                raise AppError("CANCELLATION_UNAVAILABLE", 503)
            return {}
        if failure == "bad_response":
            return {"invalid": "response"}
        raise AppError("INFERENCE_TIMEOUT", 504)

    monkeypatch.setattr(runtime_client, "call", transport)
    expected = "INVALID_RUNTIME_RESPONSE" if failure == "bad_response" else "INFERENCE_TIMEOUT"
    with pytest.raises(AppError, match=expected):
        runtime_client.invoke(None, cap, step, 1, nullcontext)
    assert calls == [
        (runtime_client.STEPS, "POST"),
        (f"{runtime_client.STEPS}/{step.step_id}/cancel", "POST"),
    ]


def test_transport_timeout_cancels_active_step_and_releases_runtime(client, repo, monkeypatch):
    cap = capability("self_rag")
    entered, released, emergency = Event(), Event(), Event()
    steps, cancellations = [], []

    def decode(step, cancel):
        steps.append(step)
        cancellations.append(cancel)
        if len(steps) == 1:
            entered.set()
            while not cancel.wait(0.01) and not emergency.is_set():
                pass
            released.set()
            return completed(cap, step, {}, finish="cancelled")
        if step.state == "DECIDE":
            decision = "[NoRetrieve]" if not step.context.segments else "[Finish]"
            return completed(cap, step, {"decision": decision})
        return completed(cap, step, {"utility": "[IsUSE:5]"}, "2")

    with loopback(application(cap, decode, SECRET)) as origin:
        for name, value in {
            "self_rag_enabled": True,
            "self_rag_runtime_origin": origin,
            "self_rag_runtime_token": SecretStr(SECRET),
            "self_rag_manifest_sha256": cap.manifest_sha256,
            "inference_timeout_seconds": 1,
        }.items():
            monkeypatch.setattr(repo.cfg, name, value)
        try:
            before = now()
            query = run_query(
                client,
                repo,
                {
                    "mode": "self_rag",
                    "question": "What is 1 plus 1?",
                },
                "timeout-cancel",
            )
            result = client.get(query["result_url"]).json()
            assert result["status"] == "failed" and result["error"]["code"] == "INFERENCE_TIMEOUT"
            assert entered.is_set() and released.wait(1) and cancellations[0].is_set()
            assert 0 < (steps[0].deadline - before).total_seconds() < 1.5
            following = run_query(
                client,
                repo,
                {
                    "mode": "self_rag",
                    "question": "What is 1 plus 1?",
                },
                "after-timeout",
            )
            assert client.get(following["result_url"]).json()["status"] == "succeeded"
        finally:
            emergency.set()


def test_real_pilot_bundle_over_loopback(tmp_path):
    local = os.getenv("DOCUMIND_TEST_REFLECTION_BUNDLE")
    if not local:
        if os.getenv("DOCUMIND_REQUIRE_REFLECTION_BUNDLE") == "1":
            pytest.fail("CI requires a trained reflection bundle; the runtime check cannot skip")
        pytest.skip("Set DOCUMIND_TEST_REFLECTION_BUNDLE to a trained FP32 pilot bundle")
    bundle = Path(local)
    expected = digest((bundle / "manifest.json").read_bytes())
    cap, decode = load(bundle, expected, pilot=True)
    with pytest.raises(ValueError, match="manifest does not match"):
        load(bundle, "0" * 64, pilot=True)
    with pytest.raises(ValueError, match="Target model"):
        load(bundle, expected, pilot=False)
    sample = create_corpus()[1][0]
    from socket import socket
    from time import monotonic, sleep
    from types import SimpleNamespace

    with socket() as listener:
        listener.bind(("127.0.0.1", 0))
        server = uvicorn.Server(
            uvicorn.Config(application(cap, decode, SECRET), log_level="error", access_log=False)
        )
        runner = Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
        runner.start()
        try:
            stop = monotonic() + 8
            while not server.started and monotonic() < stop:
                sleep(0.025)
            assert server.started
            cfg = SimpleNamespace(
                self_rag_enabled=True,
                self_rag_runtime_origin=f"http://127.0.0.1:{listener.getsockname()[1]}",
                self_rag_runtime_token=SecretStr(SECRET),
                self_rag_manifest_sha256=expected,
            )
            assert runtime_client.call(cfg, runtime_client.CAPABILITIES, "GET")[
                "supported_modes"
            ] == ["reflection_pilot"]
            with pytest.raises(AppError, match="MODEL_NOT_READY"):
                runtime_client.admitted(cfg)
            from contextlib import nullcontext

            request = build_step(cap, context=episode_context(sample, sample.episodes[0]))
            decision, labels = runtime_client.invoke(cfg, cap, request, 15, nullcontext)
            assert labels == {"decision": "[Retrieve]"}
            assert decision.output_tokens == 1
            assert decision == decode(request, Event())
            assert runtime_client.invoke(cfg, cap, request, 15, nullcontext)[0] == decision
            candidate = make_step(
                cap,
                request.run_id,
                "CANDIDATE",
                episode_context(sample, sample.episodes[1]),
                20,
                datetime.now(UTC) + timedelta(seconds=30),
            )
            scored, labels = runtime_client.invoke(cfg, cap, candidate, 30, nullcontext)
            assert scored.input_tokens > decision.input_tokens
            assert set(scored.probabilities["relevance"]) == set(FAMILIES["relevance"])
            assert len(compare(cfg, cap, decode)["cases"]) == 3
        finally:
            server.should_exit = True
            runner.join(timeout=8)

    import json
    from shutil import copytree

    from safetensors.torch import load_file, save_file

    changed = tmp_path / "missing-rows"
    copytree(bundle, changed)
    adapter = changed / "adapter" / "adapter_model.safetensors"
    weights = load_file(adapter)
    save_file(
        {key: value for key, value in weights.items() if "trainable_tokens_delta" not in key},
        adapter,
    )
    manifest_file = changed / "manifest.json"
    manifest = json.loads(manifest_file.read_bytes())
    manifest["files"]["adapter/adapter_model.safetensors"] = digest(adapter.read_bytes())
    raw = canonical(manifest).encode()
    manifest_file.write_bytes(raw)
    with pytest.raises(ValueError, match="Missing trained reflection rows"):
        load(changed, digest(raw), pilot=True)
