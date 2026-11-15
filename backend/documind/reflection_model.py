"""Shared identities and PEFT base initialization for training and serving."""

import json
import re
import stat
from pathlib import Path

TARGET = ("Qwen/Qwen3-4B-Instruct-2507", "cdbee75f17c01a7cc42f958dc650907174af0554")
SMOKE = ("HuggingFaceTB/SmolLM2-135M-Instruct", "12fd25f77366fa6b3b4b768ec3050bf629380bac")


def checkpoint_shards(index):
    if index.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("Checkpoint index is too large")
    weights = json.loads(index.read_text()).get("weight_map")
    if not isinstance(weights, dict) or not weights:
        raise ValueError("Invalid checkpoint index")
    if not all(
        isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9_-]+[.]safetensors", name)
        for name in weights.values()
    ):
        raise ValueError("Checkpoint shards must be flat safetensors filenames")
    shards = sorted(set(weights.values()))
    if len(shards) > 4096:
        raise ValueError("Too many checkpoint shards")
    return shards


def checked_checkpoint(repo, revision):
    """Resolve only pinned Hub weights and validate them before model loading."""
    from transformers.utils.hub import cached_file

    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo)
        or not re.fullmatch(r"[a-f0-9]{40}", revision)
        or Path(repo).is_dir()
    ):
        raise ValueError("Use a Hub model ID and an immutable revision")

    def fetch(name, *, optional=False):
        path = cached_file(
            repo, name, revision=revision, _raise_exceptions_for_missing_entries=not optional
        )
        if path is None:
            return None
        path = Path(path)
        target = path.resolve(strict=True)
        snapshot = path.parent
        if (
            snapshot.name != revision
            or snapshot.parent.name != "snapshots"
            or not stat.S_ISREG(target.stat().st_mode)
            or not (
                target.is_relative_to(snapshot.resolve())
                or target.is_relative_to((snapshot.parent.parent / "blobs").resolve())
                or (
                    target.is_relative_to((snapshot.parents[2] / "blobs").resolve())
                    and re.fullmatch(r"[a-f0-9]{64}", target.name)
                )
            )
        ):
            raise ValueError("Checkpoint file is outside its pinned Hub cache or not regular")
        return path

    config = fetch("config.json")
    fetch("generation_config.json", optional=True)
    # Transformers prefers a single safe weight file when both layouts exist.
    single = fetch("model.safetensors", optional=True)
    if not single:
        index = fetch("model.safetensors.index.json")
        for name in checkpoint_shards(index):
            fetch(name)
    return config.parent


def base_model(repo, revision, size, quantized, *, training=False, device="checkpoint"):
    import torch
    from peft import prepare_model_for_kbit_training
    from transformers import AutoModelForCausalLM, BitsAndBytesConfig

    if device not in {"checkpoint", "cpu", "mps", "cuda"}:
        raise ValueError("Unknown model device")
    if device == "mps" and not torch.backends.mps.is_available():
        raise ValueError("MPS is unavailable")
    if device == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA is unavailable")
    kwargs = {
        "local_files_only": True,
        "trust_remote_code": False,
        "use_safetensors": True,
        "dtype": torch.float32,
    }
    if device == "mps" and not quantized:
        kwargs["dtype"] = torch.float16
    if quantized:
        kwargs |= {
            "device_map": {"": 0},
            "dtype": torch.float16,
            "quantization_config": BitsAndBytesConfig(
                load_in_4bit=True,
                bnb_4bit_quant_type="nf4",
                bnb_4bit_use_double_quant=True,
                bnb_4bit_compute_dtype=torch.float16,
                llm_int8_skip_modules=["lm_head"],
            ),
        }
    model = AutoModelForCausalLM.from_pretrained(checked_checkpoint(repo, revision), **kwargs)
    model.resize_token_embeddings(size, mean_resizing=False)
    model.config.use_cache = False
    if quantized:
        # Reload needs the same FP32 unquantized embeddings and head as training.
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=training)
    elif device in {"mps", "cuda"}:
        model = model.to(device)
    return model
