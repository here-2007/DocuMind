"""Independent generators and trained critics keep local access and evidence boundaries."""

import importlib.util
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from documind import runtime_client
from documind import schema as t
from documind.generator_runtime import application as generator_app
from documind.generator_runtime import load as load_generator
from documind.reflection import END, digest
from documind.reflection_model import SMOKE
from documind.reflection_runtime import application, decoder, load
from documind.runtime_protocol import check_result, make_step
from fastapi.testclient import TestClient
from pydantic import SecretStr
from test_generation_context import generation as generation
from test_phase2 import chat_server as chat_server
from test_phase2 import make_connection, process, rebuild, run_query, upload
from test_phase2 import models as models
from test_phase2 import phase as phase
from test_reflection_runtime import SECRET, capability, completed
from test_reflection_runtime import scripted_context_tokenizer as scripted_context_tokenizer


def test_local_generator_auth_and_budgets():
    calls = []
    client = TestClient(generator_app("local-test", lambda body: calls.append(body) or "2", SECRET))
    body = {"model": "local-test", "messages": [{"role": "user", "content": "1 plus 1"}]}
    assert client.post("/v1/chat/completions", json=body).status_code == 401
    client.headers["Authorization"] = "Bearer " + SECRET
    assert (
        client.post("/v1/chat/completions", json=body).json()["choices"][0]["message"]["content"]
        == "2"
    )
    assert client.post("/v1/chat/completions", json=body | {"model": "wrong"}).status_code == 422
    assert client.post("/v1/chat/completions", json=body | {"max_tokens": 129}).status_code == 422
    assert client.post("/v1/chat/completions", json=body | {"stream": True}).status_code == 422
    assert len(calls) == 1


def test_reflection_uses_a_device_service_or_local_private_bridge():
    from documind.errors import AppError

    assert runtime_client.endpoint("http://127.0.0.1:8010", runtime_client.CAPABILITIES).endswith(
        runtime_client.CAPABILITIES
    )
    with pytest.raises(AppError, match="ENDPOINT_NOT_ALLOWED"):
        runtime_client.endpoint("https://remote.example", runtime_client.CAPABILITIES)


def test_private_bridge_exposes_only_selected_model_routes():
    spec = importlib.util.spec_from_file_location(
        "local_model_bridge", Path(__file__).parents[2] / "scripts/run_kaggle_model.py"
    )
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)
    calls = []

    def request(method, path, **kwargs):
        calls.append((method, path, kwargs["headers"]))
        return httpx.Response(200, json={"model": "private-generator"})

    gateway = SimpleNamespace(request=request)
    client = TestClient(
        bridge.application(
            gateway, {"Authorization": "Bearer private-remote-token"}, SECRET, "generation"
        )
    )
    assert client.get("/health").status_code == 401
    client.headers["Authorization"] = "Bearer " + SECRET
    assert client.get("/health").json() == {"model": "private-generator"}
    assert client.post("/api/kernels").status_code == 404
    assert client.post("/runtime/v1/steps").status_code == 404
    assert client.post("/v1/chat/completions", content=b"x" * 65537).status_code == 413
    assert calls == [
        ("GET", "/proxy/8010/health", {"Authorization": "Bearer private-remote-token"})
    ]


def test_supplied_candidate_is_scored_without_rewriting(generation):
    import torch

    tokenizer, cap = generation
    cap = cap.model_copy(update={"candidate_scoring": True})
    text = "INR 1100.00"
    observed = []

    class ScoringModel:
        def get_input_embeddings(self):
            return SimpleNamespace(weight=torch.empty(0))

        def __call__(self, tokens, **kwargs):
            observed.append(tokens[0].tolist())
            logits = torch.full((1, 1, len(tokenizer)), -100.0)
            # Full support is selected only after the exact supplied number and EndSegment.
            for name in ("[IsREL:Yes]", "[IsSUP:Fully]", "[IsUSE:5]"):
                logits[0, 0, cap.token_ids[name]] = 100
            return SimpleNamespace(logits=logits)

    anchor = {
        "source_id": str(uuid4()),
        "sha256": "a" * 64,
        "page_number": 1,
        "start_char": 0,
        "end_char": 27,
        "quote": "Grand total: INR 1100.00",
    }
    step = make_step(
        cap,
        uuid4(),
        "CANDIDATE",
        {
            "question": "What is the total?",
            "segments": [],
            "evidence": [anchor],
        },
        200,
        datetime.now(UTC) + timedelta(seconds=30),
        candidate_text=text,
    )
    result = decoder(ScoringModel(), tokenizer, cap)(step, Event())
    assert result.forced_tokens == len(tokenizer.encode(text, add_special_tokens=False)) + 1
    assert result.text == text and check_result(result, step, cap)["support"] == "[IsSUP:Fully]"
    candidate_tokens = tokenizer.encode(text, add_special_tokens=False) + [cap.token_ids[END]]
    assert any(row[-len(candidate_tokens) :] == candidate_tokens for row in observed)
    changed = result.model_copy(update={"text": "INR 1200.00"})
    with pytest.raises(ValueError, match="changed the supplied"):
        check_result(changed, step, cap)
    with pytest.raises(ValueError, match="does not support"):
        make_step(
            cap.model_copy(update={"candidate_scoring": False}),
            uuid4(),
            "CANDIDATE",
            step.context.model_dump(),
            200,
            step.deadline,
            candidate_text=text,
        )


@pytest.mark.integration
@pytest.mark.parametrize("outcome", ["valid", "wrong_value", "revoked", "profile_changed"])
def test_split_query_pins_both_models_and_checks_exact_external_text(
    client, phase, models, pdf, chat_server, monkeypatch, outcome
):
    url, provider = chat_server
    route, connection, _ = make_connection(client, phase, url, monkeypatch)
    document = upload(phase, pdf, "Invoice number: INV-100\nGrand total: INR 1,100.00", "split")
    process(rebuild(client, phase, "split-index"))
    cap = capability("self_rag").model_copy(update={"candidate_scoring": True})
    cfg = phase.cfg
    for key, value in {
        "self_rag_enabled": True,
        "self_rag_runtime_origin": "http://127.0.0.1:8010",
        "self_rag_runtime_token": SecretStr(SECRET),
        "self_rag_manifest_sha256": cap.manifest_sha256,
    }.items():
        monkeypatch.setattr(cfg, key, value)
    contexts = []

    def decode(step, cancel):
        contexts.append(step)
        if step.state == "DECIDE":
            return completed(
                cap, step, {"decision": "[Finish]" if step.context.segments else "[Retrieve]"}
            )
        assert step.candidate_text in {"INR 1,100.00", "INR 9.00"}
        return completed(
            cap,
            step,
            {
                "relevance": "[IsREL:Yes]",
                "support": "[IsSUP:Fully]",
                "utility": "[IsUSE:5]",
            },
            step.candidate_text,
        )

    service = TestClient(
        application(cap, decode, SECRET), headers={"Authorization": "Bearer " + SECRET}
    )

    def relay(cfg, route, method, *, body=None, timeout=2, send_guard=None):
        if send_guard:
            with send_guard():
                pass
        response = service.request(method, route, json=body)
        assert response.status_code == 200
        return response.json()

    monkeypatch.setattr(runtime_client, "call", relay)
    provider["generate"] = lambda messages: (
        "INR 9.00" if outcome == "wrong_value" else "INR 1,100.00"
    )
    body = {
        "mode": "self_rag",
        "question": "What is the total on invoice INV-100?",
        "document_ids": [str(document)],
        "connection_id": connection["id"],
        "expected_config_version": connection["config_version"],
    }
    receipt = run_query(client, phase, body, "split-query", execute=False)
    if outcome == "revoked":
        assert client.delete(route).status_code == 204
    elif outcome == "profile_changed":
        monkeypatch.setattr(cfg, "self_rag_inference_profile", "different-profile")
    process(receipt["query_run_id"])
    result = client.get(receipt["result_url"]).json()
    if outcome in {"revoked", "profile_changed"}:
        assert result["status"] == "failed" and not contexts
        return
    assert result["status"] == "succeeded", result
    assert result["inference"]["strategy"] == "split_reflection"
    assert result["inference"]["generator_connection_id"] == connection["id"]
    assert result["admitted_connection"]["config_version"] == connection["config_version"]
    assert result["grounding_status"] == (
        "supported" if outcome == "valid" else "insufficient_evidence"
    )
    assert any(row.candidate_text for row in contexts)
    with phase.engine.begin() as c:
        assert (
            c.scalar(
                sa.select(t.query_runs.c.runtime_progress).where(
                    t.query_runs.c.id == receipt["query_run_id"]
                )
            )
            is None
        )


def test_real_local_critic_load_and_score():
    selected = os.getenv("DOCUMIND_TEST_REFLECTION_BUNDLE")
    if not selected:
        pytest.skip("Set DOCUMIND_TEST_REFLECTION_BUNDLE for actual CPU checkpoint execution")
    bundle = Path(selected)
    cap, decode = load(
        bundle, digest((bundle / "manifest.json").read_bytes()), pilot=True, device="cpu"
    )
    step = make_step(
        cap,
        uuid4(),
        "CANDIDATE",
        {
            "question": "What is 1 plus 1?",
            "segments": [],
            "evidence": [],
        },
        40,
        datetime.now(UTC) + timedelta(seconds=30),
        candidate_text="2",
    )
    result = decode(step, Event())
    assert cap.inference_profile == "fp32-cpu"
    assert result.text == "2" and check_result(result, step, cap)["utility"].startswith("[IsUSE:")


def test_real_local_generator_serves_chat_completion():
    if os.getenv("DOCUMIND_TEST_CHAT_MODEL") != "1":
        pytest.skip("Enable DOCUMIND_TEST_CHAT_MODEL for the actual CPU generator")
    generate = load_generator(*SMOKE, "cpu")
    client = TestClient(generator_app(SMOKE[0], generate, SECRET))
    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer " + SECRET},
        json={
            "model": SMOKE[0],
            "max_tokens": 16,
            "messages": [{"role": "user", "content": "Repeat the word invoice."}],
        },
    )
    assert response.status_code == 200
    assert response.json()["model"] == SMOKE[0]
    assert response.json()["choices"][0]["message"]["content"].strip()


def test_checkpoint_validation_accepts_only_regular_pinned_safetensors(tmp_path, monkeypatch):
    import json
    import sys
    from types import SimpleNamespace

    from documind.reflection_model import SMOKE, checked_checkpoint

    snapshot = tmp_path / "models--example" / "snapshots" / SMOKE[1]
    snapshot.mkdir(parents=True)
    index = snapshot / "model.safetensors.index.json"
    shard = snapshot / "model-00001-of-00001.safetensors"
    (snapshot / "config.json").write_text("{}")
    index.write_text(json.dumps({"weight_map": {"weight": shard.name}}))
    shard.touch()

    def cached(repo, name, *, revision, **options):
        assert (repo, revision) == SMOKE
        path = snapshot / name
        return str(path) if path.exists() else None

    monkeypatch.setitem(sys.modules, "transformers.utils.hub", SimpleNamespace(cached_file=cached))
    assert checked_checkpoint(*SMOKE) == snapshot
    with pytest.raises(ValueError, match="immutable revision"):
        checked_checkpoint(SMOKE[0], "main")
    index.write_text(json.dumps({"weight_map": {"weight": "other.bin"}}))
    with pytest.raises(ValueError, match="flat safetensors"):
        checked_checkpoint(*SMOKE)
    index.write_text(json.dumps({"weight_map": {"weight": shard.name}}))
    shard.unlink()
    shard.mkdir()
    with pytest.raises(ValueError, match="not regular"):
        checked_checkpoint(*SMOKE)
