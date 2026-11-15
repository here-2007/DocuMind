"""Keep one private Kaggle model behind an authenticated local loopback bridge."""

import argparse
import importlib.util
import json
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from secrets import compare_digest, token_urlsafe
from threading import Thread
from time import monotonic, sleep

from documind.kaggle_session import LIFETIME_SECONDS, SessionController, SessionError
from documind.reflection_review import checked_bundle, load_review, review_packet
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

spec = importlib.util.spec_from_file_location(
    "private_serving", Path(__file__).with_name("kaggle_serving_probe.py")
)
serving = importlib.util.module_from_spec(spec)
spec.loader.exec_module(serving)


def application(jupyter, remote_headers, token, role):
    if len(token) < 24 or not token.isascii() or any(char.isspace() for char in token):
        raise ValueError("Use a private bridge token of at least 24 characters")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.api_route("/{path:path}", methods=["GET", "POST"])
    async def relay(path: str, request: Request, authorization: str | None = Header(default=None)):
        if not authorization or not compare_digest(authorization, "Bearer " + token):
            raise HTTPException(401, "UNAUTHORIZED")
        routes = (
            {
                ("GET", "runtime/v1/capabilities"),
                ("POST", "runtime/v1/steps"),
            }
            if role == "reflection"
            else {("POST", "v1/chat/completions"), ("GET", "health")}
        )
        cancellation = (
            role == "reflection"
            and request.method == "POST"
            and re.fullmatch(r"runtime/v1/steps/[a-f0-9-]{36}/cancel", path)
        )
        if (request.method, path) not in routes and not cancellation:
            raise HTTPException(404, "NOT_FOUND")
        data = bytearray()
        async for part in request.stream():
            if len(data) + len(part) > 65536:
                raise HTTPException(413, "REQUEST_TOO_LARGE")
            data.extend(part)
        try:
            body = json.loads(data) if data else None
        except ValueError:
            raise HTTPException(422, "INVALID_JSON") from None
        from starlette.concurrency import run_in_threadpool

        try:
            response = await run_in_threadpool(
                jupyter.request,
                request.method,
                "/proxy/8010/" + path,
                body=body,
                headers=remote_headers,
                timeout=30,
            )
            return JSONResponse(response.json(), status_code=response.status_code)
        except (serving.ProbeError, ValueError):
            raise HTTPException(503, "KAGGLE_MODEL_UNAVAILABLE") from None

    return app


def server_program(args, remote):
    root = f"pathlib.Path({json.dumps(remote)})"
    common = f"""
import os, pathlib, subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "-q",
 "transformers==5.22.2", "peft==0.19.1", "accelerate==1.13.0",
 "bitsandbytes==0.50.2", "fastapi", "uvicorn", "httpx", "pydantic>=2.7,<3"], check=True)
root = {root}
sys.path.insert(0, str(root / "source"))
import uvicorn
"""
    if args.role == "reflection":
        kwargs = (
            "pilot=True"
            if args.pilot
            else 'pilot=False, corpus=root / "corpus", review=root / "review.json", '
            'generated_evaluation=root / "generated.json"'
        )
        return (
            common
            + f"""
from documind.reflection_runtime import application, load
cap, decode = load(root / "bundle", {json.dumps(args.manifest_sha)}, {kwargs})
uvicorn.run(application(cap, decode, os.environ["DOCUMIND_RUNTIME_TOKEN"]),
 host="127.0.0.1", port=8010, access_log=False, log_level="error")
"""
        )
    return (
        common
        + f"""
from documind.generator_runtime import application, load
generate = load({json.dumps(args.model)}, {json.dumps(args.revision)}, "cuda")
uvicorn.run(application({json.dumps(args.model)}, generate, os.environ["DOCUMIND_RUNTIME_TOKEN"]),
 host="127.0.0.1", port=8010, access_log=False, log_level="error")
"""
    )


def run(args):
    import uvicorn
    from kaggle.api.kaggle_api_extended import KaggleApi

    token = os.environ.get("DOCUMIND_BRIDGE_TOKEN", "")
    if len(token) < 24 or not token.isascii() or any(c.isspace() for c in token):
        raise ValueError("Set DOCUMIND_BRIDGE_TOKEN to a private value of at least 24 characters")
    if args.role == "reflection":
        checked_bundle(args.bundle, args.manifest_sha)
        if not args.pilot:
            packet = review_packet(
                args.bundle, args.corpus, args.manifest_sha, args.generated_evaluation
            )
            load_review(packet, args.review)
    api = KaggleApi()
    api.authenticate()
    controller = SessionController(api, args.state)
    deadline = datetime.now(UTC) + timedelta(seconds=LIFETIME_SECONDS)
    serving.launch_watcher(args.state, deadline)
    jupyter = None
    try:
        status = controller.start(
            serving.template(args.state, args.owner), expires_at=deadline.isoformat()
        )
        until = monotonic() + 600
        while status["phase"] == "starting" and monotonic() < until:
            sleep(3)
            status = controller.poll()
        if status["phase"] != "ready":
            raise ValueError("Private GPU session did not become ready")
        jupyter = serving.Jupyter(controller.jupyter_url, status["session_id"])
        logging.getLogger("httpx").setLevel(logging.WARNING)
        remote = "documind-local-" + token_urlsafe(12).replace("-", "a").replace("_", "b")
        for name in (*serving.MODULES, "generator_runtime"):
            jupyter.put_file(
                f"{remote}/source/documind/{name}.py",
                (Path("backend/documind") / f"{name}.py").read_bytes(),
            )
        if args.role == "reflection":
            for file in sorted(args.bundle.rglob("*")):
                if file.is_file():
                    jupyter.put_file(
                        f"{remote}/bundle/{file.relative_to(args.bundle).as_posix()}",
                        file.read_bytes(),
                    )
            if not args.pilot:
                for name in ("manifest.json", "sources.jsonl", "samples.jsonl"):
                    jupyter.put_file(f"{remote}/corpus/{name}", (args.corpus / name).read_bytes())
                jupyter.put_file(f"{remote}/review.json", args.review.read_bytes())
                jupyter.put_file(f"{remote}/generated.json", args.generated_evaluation.read_bytes())
        remote_token = token_urlsafe(32)
        code = serving.bootstrap(
            remote,
            args.manifest_sha or "",
            remote_token,
            controller.deadline().isoformat(),
            server_program=server_program(args, remote),
        )
        if args.role == "generation":
            code = code.replace("/runtime/v1/capabilities", "/health")
        jupyter.execute(code)
        headers = {"Authorization": "Bearer " + remote_token}
        server = uvicorn.Server(
            uvicorn.Config(
                application(jupyter, headers, token, args.role),
                host="127.0.0.1",
                port=args.port,
                access_log=False,
                log_level="warning",
            )
        )

        def expire():
            while datetime.now(UTC) < controller.deadline() and not server.should_exit:
                sleep(1)
            server.should_exit = True

        Thread(target=expire, daemon=True).start()
        print(f"Private {args.role} model bridge ready at http://127.0.0.1:{args.port}", flush=True)
        server.run()
    finally:
        try:
            state = serving.stop_and_observe(controller, jupyter)
            print("Kaggle cleanup " + state["phase"], flush=True)
        except (SessionError, ValueError, serving.ProbeError):
            print("Kaggle cleanup needs reconciliation with the retained state file", flush=True)
        if jupyter:
            jupyter.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("reflection", "generation"), required=True)
    parser.add_argument("--owner", required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--manifest-sha")
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--generated-evaluation", type=Path)
    parser.add_argument("--model")
    parser.add_argument("--revision")
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("Choose a local port from 1024 through 65535")
    if args.role == "reflection" and (
        not args.bundle
        or not args.manifest_sha
        or (not args.pilot and not all((args.corpus, args.review, args.generated_evaluation)))
    ):
        parser.error("Reflection needs a checked bundle and review artifacts, or explicit --pilot")
    if args.role == "generation" and (
        not args.model or not args.revision or not re.fullmatch("[a-f0-9]{40}", args.revision)
    ):
        parser.error("Generation needs a model ID and pinned 40-character revision")
    try:
        with serving.exclusive_probe(args.state):
            run(args)
    except KeyboardInterrupt:
        return


if __name__ == "__main__":
    main()
