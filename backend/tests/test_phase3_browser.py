"""Opt-in browser check using real local API, dispatcher, worker and disposable storage."""

import os
import secrets
import signal
import socket
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pymupdf
import pytest
from cryptography.fernet import Fernet
from documind.search_index import DenseIndex
from test_comparison import INVOICE, ORDER
from test_phase2 import chat_server as chat_server
from test_phase2 import models as models
from test_phase2 import phase as phase
from test_phase2 import process, rebuild, run_query, upload

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "frontend"


def available_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def service(command, env, log, cwd=ROOT):
    with log.open("w") as output:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdout=output,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            yield process
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=5)


def wait_http(url, process, log):
    deadline = time.monotonic() + 25
    while time.monotonic() < deadline and process.poll() is None:
        try:
            if httpx.get(url, timeout=1, trust_env=False).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.2)
    pytest.fail(f"Service did not start. See {log}")


@pytest.mark.integration
def test_live_sse_disconnect_heartbeat_and_revocation(client, phase, pdf, models, tmp_path):
    from uuid import UUID

    repo = phase
    workspace = repo.cfg.local_workspace_id
    doc = upload(repo, pdf, "Grand total: USD 42.00", "live-events")
    process(rebuild(client, repo, "index"))
    receipt = run_query(
        client,
        repo,
        {
            "question": "What is the total?",
            "document_ids": [str(doc)],
        },
        "live",
        execute=False,
    )
    port = available_port()
    url = f"http://127.0.0.1:{port}"
    log = tmp_path / "sse.log"
    # Short intervals exercise heartbeats without making the suite wait ten seconds.
    code = (
        "import uvicorn; from documind import query_stream; "
        "query_stream.POLL_SECONDS=.05; query_stream.HEARTBEAT_SECONDS=.1; "
        f"uvicorn.run('documind.api:app',host='127.0.0.1',port={port},access_log=False)"
    )
    with service([sys.executable, "-c", code], os.environ.copy(), log) as server:
        wait_http(url + "/api/v1/health", server, log)
        with httpx.Client(
            base_url=url,
            trust_env=False,
            timeout=5,
            headers={
                "Authorization": "Bearer " + repo.cfg.local_api_token.get_secret_value(),
                "Accept": "text/event-stream",
            },
        ) as http:
            with http.stream("GET", receipt["events_url"]) as response:
                assert response.status_code == 200
                assert next(response.iter_lines()) == "id: 1"
            assert repo.job(workspace, UUID(receipt["query_run_id"]))["status"] == "queued"
            with http.stream(
                "GET", receipt["events_url"], headers={"Last-Event-ID": "1"}
            ) as response:
                lines = response.iter_lines()
                assert next(lines) == ": heartbeat"
                http.post(receipt["result_url"] + "/cancel").raise_for_status()
                rest = "\n".join(lines)
                assert rest.count("event: error") == 1 and "QUERY_CANCELLED" in rest

            second = run_query(
                client,
                repo,
                {
                    "question": "What is the total?",
                    "document_ids": [str(doc)],
                },
                "live-delete",
                execute=False,
            )
            with http.stream("GET", second["events_url"]) as response:
                lines = response.iter_lines()
                assert next(lines) == "id: 1"
                repo.delete(workspace, doc)
                rest = "\n".join(lines)
                assert rest.count("event: stream_error") == 1 and "SOURCE_UNAVAILABLE" in rest
                assert "Grand total" not in rest and "event: error\n" not in rest
            assert http.get(second["events_url"]).status_code == 410


@pytest.mark.integration
@pytest.mark.parametrize("comparison_only", [False, True], ids=["workspace", "phase7"])
def test_next_workspace_in_browser(request, tmp_path, chat_server, comparison_only):
    if os.getenv("DOCUMIND_TEST_BROWSER") != "1":
        pytest.skip("Set DOCUMIND_TEST_BROWSER=1 after building Next.js and installing Chromium")
    repo = request.getfixturevalue("repo")
    port, web_port = available_port(), available_port()
    api_url, web_url = f"http://127.0.0.1:{port}", f"http://127.0.0.1:{web_port}"
    token = secrets.token_urlsafe(32)
    endpoint, chat = chat_server
    fixture = tmp_path / "browser-invoice.pdf"
    with pymupdf.open() as pdf:
        pdf.new_page().insert_text((40, 60), "DocuMind browser integration fixture")
        pdf.new_page().insert_text((40, 60), "Invoice number: INV-830\nGrand total: INR 1,100.00")
        pdf.save(fixture)
    comparison_invoice = tmp_path / "comparison-invoice.pdf"
    comparison_order = tmp_path / "purchase-order.pdf"
    for filename, raw in ((comparison_invoice, INVOICE), (comparison_order, ORDER)):
        with pymupdf.open() as pdf:
            pdf.new_page().insert_text((40, 60), raw)
            pdf.save(filename)

    def synthetic_chat(messages):
        if len(messages) > 1:
            time.sleep(1.75)
        return "Grand total: INR 1,100.00"

    chat["generate"] = synthetic_chat
    chat["status_for"] = lambda messages: (
        429
        if any("What is the full billed amount?" in message["content"] for message in messages)
        else 200
    )
    qdrant_collection = "documind_browser_" + uuid4().hex
    env = dict(
        os.environ,
        LOCAL_API_TOKEN=token,
        INDEXING_ENABLED=str(not comparison_only).lower(),
        OCR_ENABLED="false",
        GENERATION_ENABLED=str(not comparison_only).lower(),
        SELF_RAG_ENABLED="false",
        INFERENCE_SECRET_KEY=Fernet.generate_key().decode(),
        LOCAL_CHAT_ENDPOINT=endpoint,
        QDRANT_COLLECTION=qdrant_collection,
        DISPATCH_POLL_SECONDS="1",
        DOCUMIND_API_URL=api_url,
        DOCUMIND_API_TOKEN=token,
        DOCUMIND_TEST_WEB_URL=web_url,
        DOCUMIND_TEST_PDF=str(fixture),
        DOCUMIND_TEST_COMPARISON_INVOICE=str(comparison_invoice),
        DOCUMIND_TEST_COMPARISON_ORDER=str(comparison_order),
        DOCUMIND_TEST_CHAT_URL=endpoint,
        DOCUMIND_TEST_SCREENSHOT=str(ROOT / ".data" / "phase3-workspace.png"),
    )
    playwright = ["npm", "run", "test:e2e"]
    worker_args = ["worker", "--pool=solo", "--loglevel=WARNING"]
    worker_cmd = [sys.executable, "-m", "celery", "-A", "documind.worker:celery", *worker_args]
    if comparison_only:
        env.update(
            MAX_WORKSPACE_DOCUMENTS="2", DOCUMIND_TEST_COMPARISON_ONLY="1", JOB_MAX_ATTEMPTS="1"
        )
        playwright.extend(["--", "--grep", "compares two extracted PDFs"])
        worker_cmd = [
            sys.executable,
            "-c",
            """
from documind import publications, worker
from documind.errors import AppError

original = publications.purge_indexes
first = True
def intermittent(repo, job):
    global first
    if first:
        first = False
        raise AppError('DENSE_INDEX_UNAVAILABLE', 503)
    return original(repo, job)

publications.purge_indexes = intermittent
worker.celery.worker_main(__import__('sys').argv[1:])
""",
            *worker_args,
        ]
    else:
        playwright.extend(["--", "--grep-invert", "compares two extracted PDFs"])
    api_log, web_log = tmp_path / "api.log", tmp_path / "next.log"
    try:
        with ExitStack() as stack:
            api = stack.enter_context(
                service(
                    [
                        sys.executable,
                        "-m",
                        "uvicorn",
                        "documind.api:app",
                        "--port",
                        str(port),
                        "--host",
                        "127.0.0.1",
                        "--no-access-log",
                    ],
                    env,
                    api_log,
                )
            )
            web = stack.enter_context(
                service(
                    [
                        "node",
                        "node_modules/next/dist/bin/next",
                        "start",
                        "--hostname",
                        "127.0.0.1",
                        "--port",
                        str(web_port),
                    ],
                    env,
                    web_log,
                    cwd=WEB,
                )
            )
            stack.enter_context(
                service(
                    worker_cmd,
                    env,
                    tmp_path / "worker.log",
                )
            )
            stack.enter_context(
                service(
                    [
                        sys.executable,
                        "-m",
                        "documind.cli",
                        "dispatch",
                    ],
                    env,
                    tmp_path / "dispatcher.log",
                )
            )
            wait_http(api_url + "/api/v1/health", api, api_log)
            wait_http(web_url, web, web_log)
            result = subprocess.run(
                playwright,
                cwd=WEB,
                env=env,
                capture_output=True,
                text=True,
                timeout=200,
                check=False,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            if comparison_only:
                assert not chat["messages"]
            else:
                assert len(chat["messages"]) >= 2 and chat["authenticated"]
    finally:
        if not comparison_only:
            DenseIndex(repo.cfg.model_copy(update={"qdrant_collection": qdrant_collection})).call(
                "DELETE", missing_ok=True
            )
