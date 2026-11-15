"""Phase 2 integration uses a disposable database, private object root and Qdrant collection."""

import json
import math
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from uuid import UUID, uuid4

import pymupdf
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from documind import schema as t
from documind.chat import complete, endpoint_target, resolve
from documind.errors import AppError
from documind.evidence import field_question, verified_candidate
from documind.publications import retrieve, start_publication
from documind.repository import Repository
from documind.search_index import DenseIndex, chunks_for, sparse_tokens
from documind.search_models import search_models
from pydantic import SecretStr


def test_cleanup_retries_index_contention_without_waiting_for_lease(phase, pdf, monkeypatch):
    from documind.publications import index_lock

    workspace = phase.cfg.local_workspace_id
    doc = upload(phase, pdf, "Grand total: USD 12.00", "busy-index")
    publication = start_publication(phase, workspace, "busy-publication")
    deletion = phase.delete(workspace, doc)
    monkeypatch.setattr(phase.cfg, "job_lease_seconds", 3)
    monkeypatch.setattr(phase.cfg, "job_heartbeat_seconds", 1)
    with index_lock(phase, publication):
        started = time.monotonic()
        process(deletion)
        assert time.monotonic() - started < 2
        result = phase.job(workspace, deletion)
        assert result["status"] == "queued" and result["error_code"] == "PUBLICATION_BUSY"
    with phase.engine.begin() as c:
        c.execute(
            t.jobs.update().where(t.jobs.c.id == deletion).values(next_attempt_at=sa.func.now())
        )
    process(deletion)
    assert phase.job(workspace, deletion)["status"] == "succeeded"
    assert list(phase.objects.keys(f"{workspace}/{doc}/")) == []


def test_qdrant_requires_key_and_does_not_allow_browser_origins(repo):
    import httpx

    collection = "security_test_" + uuid4().hex
    url = repo.cfg.qdrant_url.rstrip("/") + "/collections/" + collection
    body = {"vectors": {"size": 2, "distance": "Cosine"}}
    headers = {"api-key": repo.cfg.qdrant_api_key.get_secret_value()}
    # DenseIndex opens a fresh connection for each request.
    with httpx.Client(
        timeout=10, trust_env=False, limits=httpx.Limits(max_keepalive_connections=0)
    ) as client:
        assert client.put(url, json=body, headers=headers).status_code == 200
        try:
            for method in ("GET", "PUT", "DELETE"):
                result = client.request(method, url, json=body if method == "PUT" else None)
                assert result.status_code in {401, 403}, (method, result.status_code)
            result = client.get(url, headers=headers | {"Origin": "https://audit.invalid"})
            assert result.status_code == 200
            assert "access-control-allow-origin" not in result.headers
            preflight = client.options(
                url,
                headers={
                    "Origin": "https://audit.invalid",
                    "Access-Control-Request-Method": "DELETE",
                    "Access-Control-Request-Headers": "api-key",
                },
            )
            assert "access-control-allow-origin" not in preflight.headers
            assert client.get(url, headers=headers).status_code == 200
        finally:
            assert client.delete(url, headers=headers).status_code == 200


@pytest.fixture
def phase(repo, monkeypatch):
    monkeypatch.setattr(repo.cfg, "indexing_enabled", True)
    monkeypatch.setattr(repo.cfg, "generation_enabled", True)
    monkeypatch.setattr(repo.cfg, "inference_secret_key", SecretStr(Fernet.generate_key().decode()))
    monkeypatch.setattr(repo.cfg, "qdrant_collection", "documind_test_" + uuid4().hex)
    yield repo
    DenseIndex(repo.cfg).call("DELETE", missing_ok=True)


@pytest.fixture
def models():
    if os.getenv("DOCUMIND_TEST_RETRIEVAL") != "1":
        pytest.skip("Set DOCUMIND_TEST_RETRIEVAL=1 after running scripts/setup_search.py")
    return search_models()


def path(repo):
    return f"/api/v1/workspaces/{repo.cfg.local_workspace_id}"


def process(identifier):
    from documind.worker import process_job

    process_job(UUID(str(identifier)))


def upload(repo, pdf, text, key):
    row = repo.upload(repo.cfg.local_workspace_id, pdf(text), f"{key}.pdf", key)
    process(row["job_id"])
    return row["document_id"]


def rebuild(client, repo, key):
    result = client.post(path(repo) + "/publications", headers={"Idempotency-Key": key})
    assert result.status_code == 202, result.json()
    return UUID(result.json()["publication_id"])


def run_query(client, repo, body, key, *, execute=True):
    result = client.post(path(repo) + "/queries", json=body, headers={"Idempotency-Key": key})
    assert result.status_code == 202, result.json()
    accepted = result.json()
    if execute:
        process(accepted["query_run_id"])
    return accepted


@pytest.mark.integration
def test_real_models_and_tail_offsets(models, repo):
    assert models.dense.vision_tower is None and models.dense.audio_tower is None
    text = "electroencephalographic " * 120 + "TAIL-9876"
    row = {
        "id": uuid4(),
        "workspace_id": repo.cfg.local_workspace_id,
        "document_id": uuid4(),
        "ingestion_version": "fixture",
        "page_id": uuid4(),
        "text_raw": text,
    }
    chunks = list(chunks_for(uuid4(), row, models.tokenizer))
    assert chunks[-1]["end_char"] == len(text) and chunks[-1]["text"].endswith("TAIL-9876")
    assert all(chunk["token_count"] <= 240 for chunk in chunks)
    for left, right in zip(chunks, chunks[1:], strict=False):
        overlap = text[right["start_char"] : left["end_char"]]
        assert len(models.tokenizer(overlap, add_special_tokens=False)["input_ids"]) <= 64
    vector = models.embed([chunks[-1]["text"]])[0]
    assert len(vector) == 768 and math.hypot(*vector) == pytest.approx(1)
    assert models.embed(["TAIL-9876"], query=True) != models.embed(["TAIL-9876"])
    assert len(models.rerank("Find TAIL-9876", [chunks[-1]["text"]])) == 1
    assert sparse_tokens("INV-100/2") == ["inv-100/2"]
    with pytest.raises(AppError, match="RETRIEVAL_INPUT_TOO_LONG"):
        models.validate_query("word " * 65)


@pytest.mark.integration
def test_real_retrieval_publish_query_and_delete(client, phase, pdf, models):
    repo = phase
    workspace = repo.cfg.local_workspace_id
    document = upload(repo, pdf, "Invoice number: INV-100\nGrand total: INR 1,100.00", "invoice")
    other = upload(repo, pdf, "Parrots flock near tropical rainforest canopies.", "other")
    third = upload(repo, pdf, "Marine turtles migrate across ocean currents.", "third")
    publication_id = rebuild(client, repo, "index")
    process(publication_id)
    job = repo.job(workspace, publication_id)
    assert job["status"] == "succeeded", job
    assert client.get("/api/v1/ready").json()["search"]
    assert repo.summary(workspace, document)["searchable"]
    exact = retrieve(repo, "INV-100", workspace_id=workspace, publication_id=publication_id)
    assert next(p for p in exact if p["document_id"] == document)["sparse_rank"] == 1
    semantic = retrieve(
        repo,
        "Where do jungle birds gather?",
        workspace_id=workspace,
        publication_id=publication_id,
    )
    assert semantic[0]["document_id"] == other
    assert semantic[0]["dense_rank"] == 1 and "sparse_rank" not in semantic[0]
    body = {"question": "What is the total on invoice INV-100?", "document_ids": [str(document)]}
    accepted = run_query(client, repo, body, "question")
    assert run_query(client, repo, body, "question")["query_run_id"] == accepted["query_run_id"]
    conflict = client.post(
        path(repo) + "/queries",
        json={**body, "question": "Currency?"},
        headers={"Idempotency-Key": "question"},
    )
    assert conflict.status_code == 409
    result = client.get(accepted["result_url"]).json()
    assert result["status"] == "succeeded", result
    assert result["grounding_status"] == "supported", result
    assert result["answer"] == "Grand total: INR 1,100.00"
    assert result["inference"] is None
    citation = result["citations"][0]
    assert citation["document_id"] == str(document)
    citation_url = path(repo) + "/citations/" + citation["citation_id"]
    assert client.get(citation_url).json()["quote"] == result["answer"]
    assert client.get(citation_url).json()["citation_id"] == citation["citation_id"]
    trace = client.get(accepted["events_url"]).json()["items"]
    assert [item["event_type"] for item in trace].count("answer") == 1
    for i, question in enumerate(
        (
            "Was invoice INV-100 approved?",
            "What is the due date on invoice INV-100?",
            "What is the total on invoice INV-200?",
        )
    ):
        refused = run_query(client, repo, {**body, "question": question}, f"refuse-{i}")
        row = client.get(refused["result_url"]).json()
        assert row["grounding_status"] == "insufficient_evidence" and row["citations"] == []
    unauthorized = Repository(
        repo.engine,
        repo.objects,
        repo.cfg.model_copy(update={"local_workspace_id": uuid4()}),
    )
    unauthorized.initialize()
    foreign_doc = upload(unauthorized, pdf, "Invoice number: INV-100\nGrand total: USD 99.00", "b")
    foreign_pub = start_publication(unauthorized, unauthorized.cfg.local_workspace_id, "b-index")
    process(foreign_pub)
    assert (
        retrieve(
            unauthorized,
            "INV-100",
            workspace_id=unauthorized.cfg.local_workspace_id,
            publication_id=foreign_pub,
        )[0]["document_id"]
        == foreign_doc
    )
    assert foreign_doc not in {
        row["document_id"]
        for row in retrieve(
            repo,
            "INV-100",
            workspace_id=workspace,
            publication_id=publication_id,
        )
    }
    with pytest.raises(AppError, match="NOT_FOUND"):
        retrieve(
            unauthorized,
            "INV-100",
            workspace_id=unauthorized.cfg.local_workspace_id,
            publication_id=publication_id,
        )
    with pytest.raises(AppError, match="INVALID_DOCUMENT_SCOPE"):
        retrieve(
            repo, "INV-100", workspace_id=workspace, publication_id=publication_id, document_ids=[]
        )
    scope = retrieve(
        repo,
        "INV-100",
        workspace_id=workspace,
        publication_id=publication_id,
        document_ids=[third],
    )
    assert {item["document_id"] for item in scope} <= {third}
    delete_id = repo.delete(workspace, document)
    assert client.get(accepted["result_url"]).status_code == 410
    assert client.get(accepted["events_url"]).status_code == 410
    assert client.get(citation_url).status_code in {404, 410}
    process(delete_id)
    assert repo.job(workspace, delete_id)["status"] == "succeeded"
    assert DenseIndex(repo.cfg).members(workspace, publication_id) == {}
    assert not repo.objects.path(f"index/{workspace}/{publication_id}.json").exists()


@pytest.mark.integration
def test_publication_atomicity_and_model_identity(client, phase, pdf, models, monkeypatch):
    repo = phase
    workspace = repo.cfg.local_workspace_id
    upload(repo, pdf, "Invoice number: INV-100\nGrand total: USD 123.00", "first")
    old = rebuild(client, repo, "original")
    process(old)
    assert repo.job(workspace, old)["status"] == "succeeded"
    fresh = rebuild(client, repo, "fresh")
    stale = rebuild(client, repo, "competing")
    original_put = repo.objects.put

    def broken_sparse(key, data, content_type):
        if key.endswith(f"{fresh}.json"):
            raise AppError("TEST_SPARSE_WRITE_FAILED", 503)
        return original_put(key, data, content_type)

    with monkeypatch.context() as scoped:
        scoped.setattr(repo.objects, "put", broken_sparse)
        process(fresh)
    assert repo.job(workspace, fresh)["status"] == "failed"
    assert DenseIndex(repo.cfg).members(workspace, fresh) == {}
    process(stale)
    assert repo.job(workspace, stale)["status"] == "succeeded"
    superseded = rebuild(client, repo, "superseded")
    winner = rebuild(client, repo, "winner")
    process(winner)
    process(superseded)
    assert repo.job(workspace, superseded)["error_code"] == "PUBLICATION_STALE"
    assert retrieve(repo, "INV-100", workspace_id=workspace, publication_id=old)
    with monkeypatch.context() as scoped:
        scoped.setitem(models.identity, "loader", "other-loader")
        with pytest.raises(AppError, match="INDEX_IDENTITY_MISMATCH"):
            retrieve(repo, "INV-100", workspace_id=workspace, publication_id=winner)
    sparse = repo.objects.path(f"index/{workspace}/{winner}.json")
    sparse.write_bytes(b"[]")
    with pytest.raises(AppError, match="INDEX_MEMBERSHIP_MISMATCH"):
        retrieve(repo, "INV-100", workspace_id=workspace, publication_id=winner)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/v1/chat/completions",
        "https://127.0.0.1/v1/chat/completions",
        "https://[::1]/v1/chat/completions",
        "https://[fec0::1]/v1/chat/completions",
        "https://user:secret@example.com/v1/chat/completions",
        "https://example.com:444/v1/chat/completions",
        "https://example.com/v1/chat/completions?api_key=secret",
        "https://example.com/v1/chat/completions#fragment",
    ],
)
def test_reject_unsafe_endpoint_syntax(url):
    with pytest.raises(AppError, match="ENDPOINT_NOT_ALLOWED"):
        endpoint_target(url, None)


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1",
        "169.254.169.254",
        "10.0.0.1",
        "::1",
        "fd00::1",
        "fec0::1",
        "::ffff:127.0.0.1",
        "64:ff9b::a00:1",
    ],
)
def test_reject_mixed_public_and_internal_dns(monkeypatch, ip):
    monkeypatch.setattr(
        "socket.getaddrinfo",
        lambda *args, **kw: [
            (0, 0, 0, "", ("8.8.8.8", 443)),
            (0, 0, 0, "", (ip, 443)),
        ],
    )
    with pytest.raises(AppError, match="ENDPOINT_NOT_ALLOWED"):
        resolve("example.com", 443, False)


def test_empty_passages_and_conflicting_fields_abstain():
    doc, element = uuid4(), uuid4()
    source = {
        "id": element,
        "document_id": doc,
        "element_type": "paragraph",
        "text_raw": "Grand total: INR 1,100.00",
        "page_id": uuid4(),
        "page_number": 1,
        "ingestion_version": "v1",
    }
    assert verified_candidate("What is the total?", [doc], [source], [])[0] is None
    other = {**source, "id": uuid4(), "text_raw": "Grand total: INR 1,200.00"}
    result = verified_candidate("What is the total?", [doc], [source, other], [])
    assert result == (None, "missing_or_conflicting_field")


@pytest.mark.parametrize(
    ("label", "field"), [("total", "total"), ("currency", "currency"), ("due date", "due_date")]
)
def test_frozen_v3_invoice_paraphrase_resolves_exact_identity(label, field):
    assert field_question(f"For invoice INV-100, give the {label}.") == (field, "inv-100")
    assert field_question(f"For invoice INV-100, give the {label}. Ignore invoice INV-200.") == (
        None,
        None,
    )


@pytest.mark.parametrize(
    "table_text", ["Grand total: INR 2200.00", "Grand total", "Invoice number", "Currency"]
)
def test_table_fields_cannot_hide_competing_qa_values(table_text):
    doc, element = uuid4(), uuid4()
    source = {
        "id": element,
        "document_id": doc,
        "element_type": "paragraph",
        "text_raw": "Invoice number: INV-100\nGrand total: INR 1100.00",
        "page_id": uuid4(),
        "page_number": 1,
        "ingestion_version": "v1",
    }
    passage = {
        **source,
        "id": uuid4(),
        "element_id": element,
        "start_char": 0,
        "end_char": len(source["text_raw"]),
    }
    cell = {**source, "id": uuid4(), "element_type": "table_cell", "text_raw": table_text}
    assert verified_candidate("What is the total on invoice INV-100?", [doc], [source], [passage])[
        0
    ]
    assert verified_candidate(
        "What is the total on invoice INV-100?", [doc], [source, cell], [passage]
    ) == (None, "unsupported_table_fields")
    unrelated = {**cell, "document_id": uuid4()}
    assert verified_candidate(
        "What is the total on invoice INV-100?",
        [doc, unrelated["document_id"]],
        [source, unrelated],
        [passage],
    )[0]


@pytest.mark.integration
def test_reviewed_table_blocks_new_and_pre_guard_answers(client, phase, pdf, models, monkeypatch):
    from documind import evidence

    workspace = phase.cfg.local_workspace_id
    with pymupdf.open(
        stream=pdf("Invoice number: INV-100\nGrand total: INR 1,100.00"), filetype="pdf"
    ) as document:
        page = document.new_page(width=320, height=340)
        for x in (30, 165, 290):
            page.draw_line((x, 50), (x, 135))
        for y in (50, 90, 135):
            page.draw_line((30, y), (290, y))
        for x, y, label in (
            (40, 75, "Label"),
            (175, 75, "Amount"),
            (40, 115, "Grand total"),
            (175, 115, "INR 2,200.00"),
        ):
            page.insert_text((x, y), label)
        receipt = phase.upload(workspace, document.tobytes(), "mixed.pdf", "mixed")
    process(receipt["job_id"])
    reviewed = phase.reprocess(
        workspace,
        receipt["document_id"],
        "reviewed",
        review={
            "base_ingestion_version": receipt["latest_ingestion_version"],
            "confirm_all_pages": True,
            "corrections": [],
        },
    )
    process(reviewed["job_id"])
    process(rebuild(client, phase, "index"))
    body = {
        "question": "What is the total on invoice INV-100?",
        "document_ids": [str(receipt["document_id"])],
    }
    with monkeypatch.context() as before:
        before.setattr(evidence, "TABLE_LABEL", re.compile(r"(?!)"))
        old = run_query(client, phase, body, "old-verifier")
        result = client.get(old["result_url"]).json()
        assert result["grounding_status"] == "supported", result
        citation_url = path(phase) + "/citations/" + result["citations"][0]["citation_id"]
        assert client.get(citation_url).status_code == 200
    for url in (old["result_url"], old["events_url"], citation_url):
        response = client.get(url)
        assert response.status_code == 409, response.json()
        assert response.json()["error"]["code"] == "QUERY_RECHECK_REQUIRED"
    current = run_query(client, phase, body, "guarded")
    result = client.get(current["result_url"]).json()
    assert result["grounding_status"] == "insufficient_evidence" and result["citations"] == []
    trace = client.get(current["events_url"]).json()["items"]
    assert any(row["payload"].get("decision") == "unsupported_table_fields" for row in trace)


@pytest.fixture
def chat_server():
    state = {"status": 200, "messages": [], "model": "phase2-test"}
    with ThreadingHTTPServer(("127.0.0.1", 0), ChatHandler) as server:
        server.state = state
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}/v1/chat/completions", state
        finally:
            server.shutdown()
            thread.join()


class ChatHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        state = self.server.state
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        state["messages"].append(body["messages"])
        state["authenticated"] = self.headers.get("Authorization") == "Bearer synthetic-token"
        status = state["status_for"](body["messages"]) if "status_for" in state else state["status"]
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        if status == 307:
            self.send_header("Location", "http://169.254.169.254/v1/chat/completions")
        self.end_headers()
        if status == 429 and state.get("busy"):
            self.wfile.write(b'{"detail":"GENERATOR_BUSY"}')
            return
        generated = state.get("generate", lambda _: "Unverified provider prose")(body["messages"])
        self.wfile.write(
            json.dumps(
                {
                    "model": state["model"],
                    "choices": [{"message": {"content": generated}}],
                }
            ).encode()
        )


def make_connection(client, repo, url, monkeypatch, *, model_id="phase2-test"):
    monkeypatch.setattr(repo.cfg, "local_chat_endpoint", url)
    body = {
        "display_name": "Local model",
        "endpoint_url": url,
        "model_id": model_id,
        "api_key": "synthetic-token",
    }
    route = path(repo) + "/inference-connections"
    response = client.post(route, json=body, headers={"Idempotency-Key": "connection"})
    assert response.status_code == 201, response.json()
    assert "synthetic-token" not in response.text
    connection = response.json()
    route += "/" + connection["id"]
    response = client.post(route + "/validate", headers={"Idempotency-Key": "validate"})
    assert response.status_code == 202, response.json()
    process(response.json()["job_id"])
    ready = client.get(route).json()
    assert ready["supported_modes"] == ["baseline_rag"], ready
    return route, ready, body


@pytest.mark.integration
def test_chat_redirect_and_credential_isolation(client, phase, chat_server, monkeypatch):
    url, state = chat_server
    route, item, body = make_connection(client, phase, url, monkeypatch)
    assert state["authenticated"] and len(state["messages"]) == 1
    assert "Reply with the word ready." in str(state["messages"][0])
    with phase.engine.connect() as c:
        cipher = c.scalar(sa.select(t.secrets.c.ciphertext))
        assert b"synthetic-token" not in cipher
    foreign = route.replace(str(phase.cfg.local_workspace_id), str(uuid4()))
    assert client.get(foreign).status_code == 404
    state["status"] = 307
    with pytest.raises(AppError, match="ENDPOINT_REDIRECT_REJECTED"):
        complete(url, "phase2-test", "synthetic-token", [], local_url=url)
    state["status"] = 429
    with pytest.raises(AppError, match="INFERENCE_QUOTA_EXCEEDED"):
        complete(url, "phase2-test", "synthetic-token", [], local_url=url)
    edit = {**body, "api_key": "rotated-token", "expected_config_version": item["config_version"]}
    response = client.patch(route, json=edit, headers={"Idempotency-Key": "rotate"})
    assert response.status_code == 200 and response.json()["status"] == "configured"
    assert "rotated-token" not in response.text
    replay = client.post(
        path(phase) + "/inference-connections",
        json=body,
        headers={"Idempotency-Key": "connection"},
    )
    assert replay.status_code == 201 and replay.json()["config_version"] == item["config_version"]
    assert replay.json()["status"] == "configured" and "synthetic-token" not in replay.text
    assert client.patch(route, json=edit, headers={"Idempotency-Key": "rotate"}).json() == (
        response.json()
    )
    assert client.delete(route).status_code == 204
    assert client.get(route).json()["status"] == "revoked"
    with phase.engine.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(t.secrets)) == 0


@pytest.mark.integration
def test_real_chat_baseline(client, phase, pdf, models, chat_server, monkeypatch):
    if os.getenv("DOCUMIND_TEST_CHAT_MODEL") != "1":
        pytest.skip("Set DOCUMIND_TEST_CHAT_MODEL=1 with the pinned synthetic-test model cached")
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    model = "HuggingFaceTB/SmolLM2-135M-Instruct"
    revision = "12fd25f77366fa6b3b4b768ec3050bf629380bac"
    kwargs = {"revision": revision, "local_files_only": True, "trust_remote_code": False}
    tokenizer = AutoTokenizer.from_pretrained(model, **kwargs)
    generator = AutoModelForCausalLM.from_pretrained(
        model,
        dtype=torch.float32,
        use_safetensors=True,
        **kwargs,
    ).eval()

    def generate(messages):
        inputs = tokenizer.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
            return_dict=True,
        )
        with torch.inference_mode():
            output = generator.generate(**inputs, max_new_tokens=20, do_sample=False)
        return tokenizer.decode(
            output[0, inputs["input_ids"].shape[-1] :], skip_special_tokens=True
        ).strip()

    url, state = chat_server
    state["model"] = model
    state["generate"] = generate
    repo = phase
    doc = upload(repo, pdf, "Invoice number: INV-100\nGrand total: INR 1,100.00", "invoice")
    pub = rebuild(client, repo, "index")
    process(pub)
    route, item, body = make_connection(client, repo, url, monkeypatch, model_id=model)
    request = {
        "question": "What is the total?",
        "document_ids": [str(doc)],
        "connection_id": item["id"],
        "expected_config_version": item["config_version"],
    }
    changed = client.post(
        path(repo) + "/queries",
        json={**request, "expected_config_version": item["config_version"] + 1},
        headers={"Idempotency-Key": "wrong-version"},
    )
    assert changed.status_code == 409 and changed.json()["error"]["code"] == (
        "CONNECTION_VERSION_CONFLICT"
    )
    accepted = run_query(client, repo, request, "real-model")
    result = client.get(accepted["result_url"]).json()
    assert result["grounding_status"] == "supported", result
    assert result["answer"] == "Grand total: INR 1,100.00"
    assert result["inference"]["generation_used"]
    assert result["inference"]["model_id"] == model
    assert len(state["messages"]) == 2
    assert (
        client.post(
            path(repo) + "/queries",
            json={**request, "mode": "self_rag"},
            headers={"Idempotency-Key": "unsupported"},
        ).status_code
        == 409
    )
    stale = run_query(client, repo, request, "old-config", execute=False)
    assert (
        client.patch(
            route,
            json={**body, "expected_config_version": item["config_version"]},
            headers={"Idempotency-Key": "rotate"},
        ).status_code
        == 200
    )
    process(stale["query_run_id"])
    result = client.get(stale["result_url"]).json()
    assert result["status"] == "failed" and result["error"]["code"] == "CONNECTION_UNAVAILABLE"
    assert len(state["messages"]) == 2


@pytest.mark.integration
def test_development_evaluation(client, phase, pdf, models):
    import hashlib
    import platform
    import resource
    import time
    from datetime import UTC, datetime
    from pathlib import Path

    if os.getenv("DOCUMIND_TEST_EVAL") != "1":
        pytest.skip("Set DOCUMIND_TEST_EVAL=1 for the 50-query real-model evaluation")
    root = Path(__file__).parent / "fixtures"
    data = (root / "phase2_development.json").read_bytes()
    holdout = (root / "phase2_held_out.json").read_bytes()
    corpus = json.loads(data)
    assert {doc["group"] for doc in corpus["documents"]}.isdisjoint(
        doc["group"] for doc in json.loads(holdout)["documents"]
    )
    repo = phase
    workspace = repo.cfg.local_workspace_id
    ids = {
        doc["invoice"]: upload(repo, pdf, doc["text"], doc["invoice"])
        for doc in corpus["documents"]
    }
    labels = {identifier: label for label, identifier in ids.items()}
    publication_id = rebuild(client, repo, "development-set")
    process(publication_id)
    manifest = client.get(path(repo) + f"/publications/{publication_id}").json()["manifest"]
    rows = []
    for document in corpus["documents"]:
        for case in document["questions"]:
            started = time.monotonic()
            hits = retrieve(
                repo, case["question"], workspace_id=workspace, publication_id=publication_id
            )
            elapsed = time.monotonic() - started
            relevant = [
                index
                for index, hit in enumerate(hits, 1)
                if hit["document_id"] == ids[document["invoice"]]
                and case["expected_quote"] in hit["text"]
            ]
            rows.append(
                {
                    **case,
                    "expected_source": document["invoice"],
                    "rank": relevant[0] if relevant else None,
                    "seconds": round(elapsed, 4),
                    "hits": [
                        {
                            "source": labels[hit["document_id"]],
                            "dense_rank": hit.get("dense_rank"),
                            "sparse_rank": hit.get("sparse_rank"),
                            "rerank_score": hit["rerank_score"],
                        }
                        for hit in hits
                    ],
                }
            )
    negatives = []
    for index, case in enumerate(corpus["negative_questions"]):
        request = {"question": case["question"], "document_ids": [str(ids[case["document"]])]}
        run = run_query(client, repo, request, f"dev-negative-{index}")
        result = client.get(run["result_url"]).json()
        assert result["grounding_status"] == "insufficient_evidence" and not result["citations"]
        negatives.append({**case, "grounding_status": result["grounding_status"]})
    ranks = [row["rank"] for row in rows if row["rank"] is not None]
    assert len(rows) == len(ranks) == 50
    evaluation = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "cpu_threads": 2,
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * (1 if platform.system() == "Darwin" else 1024),
        "model_identity": models.identity,
        "development_sha256": hashlib.sha256(data).hexdigest(),
        "held_out_sha256": hashlib.sha256(holdout).hexdigest(),
        "held_out_executed": False,
        "publication_manifest": manifest,
        "recall_at_5": len(ranks) / len(rows),
        "precision_at_5": len(ranks) / (5 * len(rows)),
        "mrr_at_5": sum(1 / rank for rank in ranks) / len(rows),
        "ndcg_at_5": sum(1 / math.log2(rank + 1) for rank in ranks) / len(rows),
        "questions": rows,
        "negatives": negatives,
    }
    output = os.getenv("DOCUMIND_EVAL_OUTPUT")
    if output:
        Path(output).write_text(json.dumps(evaluation, indent=2) + "\n")
    print(f"Phase 2 development recall@5: {len(ranks)}/{len(rows)}")


@pytest.mark.integration
def test_phase2_through_real_celery_delivery(client, phase, pdf, models, monkeypatch):
    import subprocess
    import sys
    import time
    from datetime import timedelta

    from documind.repository import now
    from documind.worker import dispatch_once

    repo = phase
    doc = upload(repo, pdf, "Invoice number: INV-200\nGrand total: USD 42.00", "real-queue")
    monkeypatch.setenv("INDEXING_ENABLED", "true")
    monkeypatch.setenv("QDRANT_COLLECTION", repo.cfg.qdrant_collection)
    monkeypatch.setenv("QDRANT_URL", repo.cfg.qdrant_url)
    flags = ["worker", "-P", "solo", "-l", "WARNING", "--without-gossip", "--without-mingle"]

    def launch(stalled=False):
        if stalled:
            # Stall the real Celery worker after the durable retrieval event.
            code = (
                "import sys,time; from documind import queries; "
                "from documind.worker import celery; "
                "queries.retrieve = lambda *a, **k: time.sleep(60); "
                "celery.worker_main(sys.argv[1:])"
            )
            argv = [sys.executable, "-c", code, *flags]
        else:
            argv = [
                sys.executable,
                "-m",
                "celery",
                "-A",
                "documind.worker:celery",
                *flags,
            ]
        return subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    worker = launch()
    workspace = repo.cfg.local_workspace_id

    def wait_for_job(identifier):
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            job = repo.job(workspace, UUID(str(identifier)))
            if job["status"] in {"succeeded", "failed"}:
                assert job["status"] == "succeeded", job
                return
            dispatch_once()
            time.sleep(0.2)
        pytest.fail("Celery job did not finish")

    try:
        publication_id = rebuild(client, repo, "queue-index")
        wait_for_job(publication_id)
        worker.terminate()
        worker.wait(timeout=10)
        worker = launch(stalled=True)
        accepted = run_query(
            client,
            repo,
            {"question": "What is the total?", "document_ids": [str(doc)]},
            "queue-query",
            execute=False,
        )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            dispatch_once()
            before = client.get(accepted["events_url"]).json()["items"]
            if any(item["event_type"] == "retrieval_started" for item in before):
                break
            time.sleep(0.2)
        else:
            pytest.fail("Stalled worker did not reach the retrieval stage")
        worker.kill()
        worker.wait(timeout=5)
        with repo.engine.begin() as c:
            c.execute(
                t.jobs.update()
                .where(t.jobs.c.id == UUID(accepted["query_run_id"]))
                .values(lease_expires_at=now() - timedelta(seconds=1))
            )
        worker = launch()
        wait_for_job(accepted["query_run_id"])
        result = client.get(accepted["result_url"]).json()
        assert result["answer"] == "Grand total: USD 42.00" and len(result["citations"]) == 1
        replay = sse_events(client, accepted, **{"Last-Event-ID": str(before[-1]["seq"])})
        assert [kind for kind, _ in replay].count("answer") == 1
        assert repo.job(workspace, UUID(accepted["query_run_id"]))["attempts"] == 2
    finally:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait(timeout=5)


@pytest.mark.integration
@pytest.mark.parametrize("operation", ["cancel", "delete", "revoke", "lease_expiry", "quota"])
def test_inflight_query_has_one_terminal_outcome(
    client,
    phase,
    pdf,
    models,
    chat_server,
    monkeypatch,
    operation,
):
    from datetime import timedelta

    from documind import connections, queries
    from documind.repository import now

    repo = phase
    workspace = repo.cfg.local_workspace_id
    document = upload(repo, pdf, "Grand total: USD 42.00", "inflight")
    process(rebuild(client, repo, "index"))
    url, state = chat_server
    _, item, _ = make_connection(client, repo, url, monkeypatch)
    accepted = run_query(
        client,
        repo,
        {
            "question": "What is the total?",
            "document_ids": [str(document)],
            "connection_id": item["id"],
        },
        "inflight",
        execute=False,
    )
    identifier = UUID(accepted["query_run_id"])

    def intercept(_):
        if operation == "cancel":
            queries.cancel(repo, workspace, identifier)
        elif operation == "delete":
            repo.delete(workspace, document)
        elif operation == "revoke":
            connections.revoke(repo, workspace, UUID(item["id"]))
        elif operation == "lease_expiry":
            with repo.engine.begin() as c:
                c.execute(
                    t.jobs.update()
                    .where(t.jobs.c.id == identifier)
                    .values(lease_expires_at=now() - timedelta(seconds=1))
                )
            repo.due_jobs()
        return "Grand total: USD 42.00"

    state["generate"] = intercept
    if operation == "quota":
        state["status"] = 429
    process(identifier)
    process(identifier)
    assert len(state["messages"]) == 2
    if operation == "delete":
        assert client.get(accepted["result_url"]).status_code == 410
        assert client.get(accepted["events_url"]).status_code == 410
        return
    code = {
        "cancel": "QUERY_CANCELLED",
        "revoke": "CONNECTION_UNAVAILABLE",
        "lease_expiry": "GENERATION_OUTCOME_UNKNOWN",
        "quota": "INFERENCE_QUOTA_EXCEEDED",
    }[operation]
    result = client.get(accepted["result_url"]).json()
    assert result["status"] == ("cancelled" if operation == "cancel" else "failed")
    assert result["error"]["code"] == code and "answer" not in result
    events = client.get(accepted["events_url"]).json()["items"]
    assert [
        event["event_type"] for event in events if event["event_type"] in ("answer", "error")
    ] == [
        "error",
    ]


def test_transport_timeout_and_malformed_response(chat_server):
    import time

    url, state = chat_server
    state["generate"] = lambda _: None
    with pytest.raises(AppError, match="INVALID_PROVIDER_RESPONSE"):
        complete(url, "phase2-test", None, [], local_url=url)
    state["generate"] = lambda _: time.sleep(0.35) or "late text"
    with pytest.raises(AppError, match="INFERENCE_TIMEOUT"):
        complete(url, "phase2-test", None, [], local_url=url, timeout=0.1)


def test_dns_wait_is_bounded(monkeypatch):
    import socket

    entered, release, finished = Event(), Event(), Event()

    def lookup(*args, **kwargs):
        entered.set()
        try:
            release.wait(5)
            raise OSError("simulated DNS delay")
        finally:
            finished.set()

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    try:
        with pytest.raises(AppError, match="INFERENCE_TIMEOUT"):
            complete(
                "https://example.com/v1/chat/completions", "synthetic", "fixture", [], timeout=0.15
            )
        assert entered.is_set() and not finished.is_set()
    finally:
        release.set()
        assert finished.wait(1)


@pytest.mark.integration
def test_temporary_local_generator_busy_preserves_ready_connection(
    client, phase, pdf, models, chat_server, monkeypatch
):
    url, state = chat_server
    endpoint, ready, _ = make_connection(client, phase, url, monkeypatch)
    document = upload(phase, pdf, "Grand total: INR 1,100.00", "model-busy")
    process(rebuild(client, phase, "busy-index"))
    body = {
        "question": "What is the grand total?",
        "document_ids": [str(document)],
        "connection_id": ready["id"],
        "expected_config_version": ready["config_version"],
    }
    state.update(status=429, busy=True)
    first = run_query(client, phase, body, "first-busy")
    assert client.get(first["result_url"]).json()["error"]["code"] == "GENERATOR_BUSY"
    assert client.get(endpoint).json()["status"] == "ready"
    state.update(status=200, generate=lambda _: "Grand total: INR 1,100.00")
    retry = run_query(client, phase, body, "retry-busy")
    assert client.get(retry["result_url"]).json()["answer"] == "Grand total: INR 1,100.00"


@pytest.mark.integration
@pytest.mark.parametrize(
    "text",
    [
        "Invoice number: INV-100\nInvoice number: INV-200\nGrand total: USD 42.00",
        "Grand total: INR 1,100.00\nGrand total: INR 1,23,456.00",
    ],
)
def test_ambiguous_total_abstains(client, phase, pdf, models, text):
    doc = upload(phase, pdf, text, "ambiguous")
    process(rebuild(client, phase, "index"))
    query = run_query(
        client,
        phase,
        {
            "question": "What is the grand total?",
            "document_ids": [str(doc)],
        },
        "ask",
    )
    result = client.get(query["result_url"]).json()
    assert result["grounding_status"] == "insufficient_evidence", (
        result["grounding_status"],
        result["answer"],
    )


@pytest.mark.integration
def test_replacement_keeps_previously_published_survivor(
    client,
    phase,
    pdf,
    models,
    table_pdf,
    monkeypatch,
):
    workspace = phase.cfg.local_workspace_id
    monkeypatch.setattr(phase.cfg, "tables_enabled", False)
    first = phase.upload(workspace, table_pdf(), "survivor.pdf", "survivor")
    survivor = first["document_id"]
    process(first["job_id"])
    removed = upload(phase, pdf, "Grand total: USD 42.00", "removed")
    published = rebuild(client, phase, "index")
    process(published)
    assert phase.summary(workspace, survivor)["searchable"]
    monkeypatch.setattr(phase.cfg, "tables_enabled", True)
    newer = phase.reprocess(workspace, survivor, "new-parser")
    process(newer["job_id"])
    preview = phase.summary(workspace, survivor)
    assert preview["extraction_status"] == "needs_review" and preview["searchable"], preview
    # Upgrading an existing Phase 2 database must recover the old published version.
    config = Config("alembic.ini")
    command.downgrade(config, "0004_phase2")
    command.upgrade(config, "head")
    process(phase.delete(workspace, removed))
    with phase.engine.connect() as db:
        replacement = db.scalar(
            sa.select(t.jobs.c.id).where(
                t.jobs.c.kind == "publication", t.jobs.c.status == "queued"
            )
        )
    assert replacement is not None
    process(replacement)
    assert phase.job(workspace, replacement)["status"] == "succeeded"
    summary = phase.summary(workspace, survivor)
    assert summary["searchable"], {
        key: summary[key]
        for key in ("extraction_status", "searchable", "published_ingestion_version")
    }


@pytest.mark.integration
def test_runbook_example_returns_supported_total(client, phase, pdf, models):
    doc = upload(phase, pdf, "Grand total: INR 1,100.00", "runbook")
    process(rebuild(client, phase, "index"))
    query = run_query(
        client,
        phase,
        {
            "question": "What is the invoice grand total?",
            "document_ids": [str(doc)],
        },
        "ask",
    )
    result = client.get(query["result_url"]).json()
    assert result["grounding_status"] == "supported", result["grounding_status"]


@pytest.mark.integration
def test_ready_is_false_for_a_corrupt_active_artifact(client, phase, pdf, models):
    from documind.publications import sparse_key

    doc = upload(phase, pdf, "Grand total: INR 1,100.00", "ready")
    published = rebuild(client, phase, "index")
    process(published)
    assert client.get("/api/v1/ready").json()["search"] is True
    phase.objects.path(sparse_key(phase.cfg.local_workspace_id, published)).write_text("[]")
    query = run_query(
        client,
        phase,
        {
            "question": "What is the grand total?",
            "document_ids": [str(doc)],
        },
        "ask",
    )
    result = client.get(query["result_url"]).json()
    assert result["error"]["code"] == "INDEX_MEMBERSHIP_MISMATCH", result
    assert client.get("/api/v1/ready").json()["search"] is False


@pytest.mark.integration
@pytest.mark.parametrize("deletion_wins", [True, False])
def test_source_deletion_orders_with_first_http_write(
    client, phase, pdf, models, chat_server, monkeypatch, deletion_wins
):
    workspace = phase.cfg.local_workspace_id
    doc = upload(phase, pdf, "Grand total: USD 42.00", "sensitive")
    process(rebuild(client, phase, "index"))
    url, state = chat_server
    _, connection, _ = make_connection(client, phase, url, monkeypatch)
    receipt = run_query(
        client,
        phase,
        {
            "question": "What is the total?",
            "document_ids": [str(doc)],
            "connection_id": connection["id"],
        },
        "first-write",
        execute=False,
    )
    entered, resume = Event(), Event()
    if deletion_wins:
        connect = HTTPConnection.connect

        def paused_connect(self):
            connect(self)
            entered.set()
            assert resume.wait(8)

        monkeypatch.setattr(HTTPConnection, "connect", paused_connect)
    else:
        request = HTTPConnection.request

        def paused_request(self, *args, **kwargs):
            entered.set()
            assert resume.wait(8)
            return request(self, *args, **kwargs)

        monkeypatch.setattr(HTTPConnection, "request", paused_request)
    with ThreadPoolExecutor(max_workers=2) as pool:
        query = pool.submit(process, receipt["query_run_id"])
        try:
            assert entered.wait(8)
            removal = pool.submit(phase.delete, workspace, doc)
            if deletion_wins:
                removal.result(timeout=3)
            else:
                with pytest.raises(TimeoutError):
                    removal.result(timeout=0.3)
                assert len(state["messages"]) == 1
        finally:
            resume.set()
        query.result(timeout=8)
        removal.result(timeout=8)
    assert client.get(receipt["result_url"]).status_code == 410
    assert len(state["messages"]) == (1 if deletion_wins else 2)


def sse_events(client, receipt, **headers):
    response = client.get(receipt["events_url"], headers={"Accept": "text/event-stream", **headers})
    assert response.status_code == 200 and response.headers["content-type"].startswith(
        "text/event-stream"
    )
    return [
        (lines[1].removeprefix("event: "), json.loads(lines[2].removeprefix("data: ")))
        for frame in response.text.strip().split("\n\n")
        if (lines := frame.splitlines()) and lines[0].startswith("id: ")
    ]


@pytest.mark.integration
def test_dispatcher_expires_queued_query_without_a_worker(client, phase, pdf, models):
    from datetime import timedelta

    from documind.repository import now

    doc = upload(phase, pdf, "Grand total: USD 42.00", "deadline")
    process(rebuild(client, phase, "deadline-index"))
    receipt = run_query(
        client,
        phase,
        {"question": "What is the total?", "document_ids": [str(doc)]},
        "deadline-query",
        execute=False,
    )
    identifier = UUID(receipt["query_run_id"])
    with phase.engine.begin() as c:
        c.execute(
            t.query_runs.update()
            .where(t.query_runs.c.id == identifier)
            .values(
                deadline=now() - timedelta(seconds=1),
            )
        )
    for _ in range(2):
        assert identifier not in phase.due_jobs()
    result = client.get(receipt["result_url"]).json()
    assert result["status"] == "failed" and result["error"]["code"] == "QUERY_TIMEOUT"
    events = sse_events(client, receipt)
    assert [kind for kind, _ in events].count("error") == 1


@pytest.mark.integration
def test_sse_replays_persisted_trace_and_cancel(client, phase, pdf, models):
    from datetime import timedelta

    from documind.repository import now

    workspace = phase.cfg.local_workspace_id
    doc = upload(phase, pdf, "Grand total: USD 42.00", "sse")
    process(rebuild(client, phase, "index"))
    body = {"question": "What is the total?", "document_ids": [str(doc)]}
    receipt = run_query(client, phase, body, "sse")
    accept = {"Accept": "text/event-stream"}
    original = sse_events(client, receipt)
    refused = client.get(
        receipt["events_url"], headers={"Accept": "text/event-stream;q=0, application/json"}
    )
    assert refused.headers["content-type"].startswith("application/json")
    assert original[-1][0] == "answer"
    assert original[-1][1]["result_url"] == receipt["result_url"]
    assert "answer" not in original[-1][1] and "citations" not in original[-1][1]
    assert {"passage_found", "retrieval_started", "citation_verified"}.issubset(
        {data.get("type") for _, data in original}
    )
    assert not any(data.get("type") == "reflection_token" for _, data in original)
    assert sse_events(client, receipt, **{"Last-Event-ID": "1"}) == original[1:]
    assert (
        client.get(
            receipt["events_url"] + "?after_seq=1",
            headers={
                **accept,
                "Last-Event-ID": "2",
            },
        ).status_code
        == 400
    )
    assert client.get(receipt["events_url"] + "?after_seq=-1", headers=accept).status_code == 400
    assert client.get(receipt["events_url"] + "?after_seq=9999", headers=accept).status_code == 400
    assert (
        client.get(
            receipt["events_url"],
            headers={
                **accept,
                "Last-Event-ID": str(original[-1][1]["seq"]),
            },
        ).status_code
        == 204
    )
    assert (
        client.get(
            receipt["events_url"].replace(str(workspace), str(uuid4())), headers=accept
        ).status_code
        == 404
    )
    with phase.engine.begin() as c:
        c.execute(
            t.jobs.update()
            .where(t.jobs.c.id == UUID(receipt["query_run_id"]))
            .values(finished_at=now() - timedelta(days=phase.cfg.query_event_retention_days + 1))
        )
    expired = client.get(receipt["events_url"], headers=accept)
    assert expired.status_code == 410
    assert expired.json()["error"]["details"]["result_url"] == receipt["result_url"]
    expired_json = client.get(receipt["events_url"], headers={"Accept": "application/json"})
    assert expired_json.status_code == 410
    assert expired_json.json()["error"]["details"]["result_url"] == receipt["result_url"]
    assert client.get(receipt["result_url"]).status_code == 200

    pending = run_query(client, phase, body, "sse-cancel", execute=False)
    for _ in range(2):
        response = client.post(pending["result_url"] + "/cancel")
        assert response.status_code == 200 and response.json()["status"] == "cancelled"
    process(pending["query_run_id"])
    events = sse_events(client, pending)
    assert [kind for kind, _ in events].count("error") == 1
    assert events[-1][1]["code"] == "QUERY_CANCELLED"

    phase.delete(workspace, doc)
    assert client.get(receipt["events_url"], headers=accept).json()["error"]["code"] == (
        "SOURCE_UNAVAILABLE"
    )
