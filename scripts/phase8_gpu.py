"""Load the pinned base and adapter in one already allocated private Kaggle session."""

import argparse
import json
import logging
import os
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from secrets import token_urlsafe
from time import monotonic, sleep
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile

import kaggle_serving_probe as serving
from documind.kaggle_session import LIFETIME_SECONDS, SessionController, SessionError
from documind.reflection import canonical, digest
from documind.reflection_review import checked_bundle
from documind.runtime_protocol import Capabilities


def server_program(remote, manifest_sha):
    return f"""
import os, pathlib, subprocess, sys
subprocess.run([sys.executable, "-m", "pip", "install", "-q", "transformers==5.22.2",
    "peft==0.19.1", "accelerate==1.13.0", "bitsandbytes==0.50.2", "fastapi", "uvicorn",
    "httpx", "pydantic>=2.7,<3"], check=True)
root = pathlib.Path({json.dumps(remote)})
sys.path.insert(0, str(root / "source"))
import torch, transformers, bitsandbytes, uvicorn
from secrets import compare_digest
from fastapi import Header, HTTPException
from time import monotonic
from documind.generator_runtime import Completion
from documind.reflection_model import base_model
from documind.reflection_runtime import application, load
from transformers import AutoTokenizer
cap, decode = load(root / "bundle", {json.dumps(manifest_sha)}, pilot=True)
tokenizer = AutoTokenizer.from_pretrained(cap.model_id, revision=cap.model_revision,
                                         trust_remote_code=False)
model = base_model(cap.model_id, cap.model_revision, len(tokenizer), True, device="cuda").eval()
token = os.environ["DOCUMIND_RUNTIME_TOKEN"]
app = application(cap, decode, token)

def authorize(authorization):
    if not authorization or not compare_digest(authorization, "Bearer " + token):
        raise HTTPException(401, "UNAUTHORIZED")

@app.get("/phase8/profile")
def profile(authorization: str | None = Header(default=None)):
    authorize(authorization)
    return {{"model": cap.model_id, "revision": cap.model_revision,
             "inference_profile": cap.inference_profile, "context_limit": cap.context_limit,
             "gpu": torch.cuda.get_device_name(), "cuda": torch.version.cuda,
             "torch": torch.__version__, "transformers": transformers.__version__,
             "bitsandbytes": bitsandbytes.__version__,
             "base_adapter_loaded": hasattr(model, "peft_config"),
             "base_quantization": model.config.quantization_config.to_dict()[
                  "bnb_4bit_quant_type"]}}

@app.post("/phase8/complete")
@torch.inference_mode()
def complete(body: Completion, authorization: str | None = Header(default=None)):
    authorize(authorization)
    if body.model != cap.model_id:
        raise HTTPException(422, "MODEL_MISMATCH")
    start = monotonic()
    inputs = tokenizer.apply_chat_template([row.model_dump() for row in body.messages],
        tokenize=True, add_generation_prompt=True, return_dict=True, return_tensors="pt").to("cuda")
    size = inputs["input_ids"].shape[1]
    if size + body.max_tokens > cap.context_limit:
        raise HTTPException(422, "CONTEXT_LIMIT")
    result = model.generate(**inputs, max_new_tokens=body.max_tokens, do_sample=False,
        use_cache=True, max_time=30, pad_token_id=tokenizer.eos_token_id)
    text = tokenizer.decode(result[0, size:], skip_special_tokens=True).strip()
    return {{"text": text, "input_tokens": size, "output_tokens": result.shape[1] - size,
             "server_seconds": monotonic() - start}}

uvicorn.run(app, host="127.0.0.1", port=8010, access_log=False, log_level="error")
"""


def connect(state):
    from kaggle.api.kaggle_api_extended import KaggleApi

    logging.getLogger("httpx").setLevel(logging.WARNING)
    api = KaggleApi()
    api.authenticate()
    controller = SessionController(api, state)
    status = controller.poll()
    if status["phase"] != "ready":
        raise serving.ProbeError("PHASE8_GPU_SESSION_NOT_READY")
    return controller, serving.Jupyter(controller.jupyter_url, status["session_id"])


def allocate(state, owner):
    from kaggle.api.kaggle_api_extended import KaggleApi

    if state.exists():
        raise ValueError("Use a new state path for a new allocation")
    api = KaggleApi()
    api.authenticate()
    controller = SessionController(api, state)
    expires = datetime.now(UTC) + timedelta(seconds=LIFETIME_SECONDS)
    serving.launch_watcher(state, expires)
    try:
        status = controller.start(serving.template(state, owner), expires_at=expires.isoformat())
        until = monotonic() + 500
        while status["phase"] == "starting" and monotonic() < until:
            sleep(4)
            try:
                status = controller.poll()
            except SessionError as error:
                if str(error) != "KAGGLE_REQUEST_FAILED":
                    raise
        if status["phase"] != "ready":
            raise serving.ProbeError("PHASE8_GPU_ALLOCATION_TIMEOUT")
        return controller
    except BaseException:
        if state.exists():
            with suppress(SessionError, serving.ProbeError):
                serving.stop_and_observe(controller)
        raise


def upload_runtime(jupyter, remote, bundle):
    with BytesIO() as buffer:
        with ZipFile(buffer, "w", compression=ZIP_DEFLATED, compresslevel=1) as archive:
            for name in (*serving.MODULES, "generator_runtime"):
                archive.write(Path("backend/documind") / f"{name}.py", f"source/documind/{name}.py")
            for file in sorted(bundle.rglob("*")):
                if file.is_file():
                    archive.write(file, f"bundle/{file.relative_to(bundle).as_posix()}")
        data = buffer.getvalue()
    chunk_size = 4 * 1024 * 1024
    for index, offset in enumerate(range(0, len(data), chunk_size)):
        path = f"{remote}/archive/{index:04}"
        part = data[offset : offset + chunk_size]
        for attempt in range(3):
            try:
                jupyter.put_file(path, part)
                break
            except serving.ProbeError as error:
                if str(error) != "JUPYTER_NETWORK_FAILURE" or attempt == 2:
                    raise
                sleep(1 + 2 * attempt)
    jupyter.execute(
        f"""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile
base = Path({json.dumps(remote)})
parts = sorted((base / "archive").iterdir())
data = b"".join(path.read_bytes() for path in parts)
assert sha256(data).hexdigest() == {json.dumps(digest(data))}
with ZipFile(BytesIO(data)) as archive:
    archive.extractall(base)
for path in parts:
    path.unlink()
(base / "archive").rmdir()
del data
""",
        timeout=45,
    )


def deploy(state, bundle, manifest_sha, receipt):
    manifest, ids = checked_bundle(bundle, manifest_sha)
    controller, jupyter = connect(state)
    started = monotonic()
    try:
        print("Private GPU session ready; uploading the pinned runtime and adapter", flush=True)
        remote = "documind-phase8-" + uuid4().hex
        upload_runtime(jupyter, remote, bundle)
        token = token_urlsafe(32)
        print("Loading paired NF4 models inside the private session", flush=True)
        jupyter.execute(
            serving.bootstrap(
                remote,
                manifest_sha,
                token,
                controller.deadline().isoformat(),
                server_program=server_program(remote, manifest_sha),
            )
        )
        route = "/proxy/8010/runtime/v1/capabilities"
        if jupyter.request("GET", route).status_code != 401:
            raise serving.ProbeError("RUNTIME_AUTHENTICATION_FAILED")
        headers = {"Authorization": "Bearer " + token}
        response = jupyter.request("GET", route, headers=headers)
        if response.status_code != 200:
            raise serving.ProbeError("PHASE8_RUNTIME_PROFILE_UNAVAILABLE")
        cap = Capabilities.model_validate(response.json())
        serving.check_capability(cap, manifest, ids, manifest_sha)
        response = jupyter.request("GET", "/proxy/8010/phase8/profile", headers=headers)
        if response.status_code != 200:
            raise serving.ProbeError("PHASE8_RUNTIME_PROFILE_UNAVAILABLE")
        profile = response.json()
        if (
            profile["model"],
            profile["revision"],
            profile["inference_profile"],
            profile["base_quantization"],
            profile["base_adapter_loaded"],
        ) != (cap.model_id, cap.model_revision, "nf4-cuda", "nf4", False):
            raise serving.ProbeError("UNMATCHED_BASE_PROFILE")
        with receipt.open("x", encoding="utf-8") as stream:
            stream.write(
                canonical(
                    {
                        "token": token,
                        "capability": cap.model_dump(mode="json"),
                        "profile": profile,
                        "deployment_seconds": monotonic() - started,
                    }
                )
                + "\n"
            )
        receipt.chmod(0o600)
        print("Paired private GPU runtime ready", flush=True)
    finally:
        jupyter.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--manifest-sha", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--allocate-for-owner", help="Create one new bounded private GPU session")
    args = parser.parse_args()
    os.umask(0o077)
    state = args.state.resolve()
    with serving.exclusive_probe(state):
        controller = allocate(state, args.allocate_for_owner) if args.allocate_for_owner else None
        try:
            deploy(state, args.bundle, args.manifest_sha, args.receipt)
        except BaseException:
            if controller:
                with suppress(SessionError, serving.ProbeError):
                    serving.stop_and_observe(controller)
            raise


if __name__ == "__main__":
    main()
