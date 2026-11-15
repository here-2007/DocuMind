"""Train, score and reload an immutable selfrag-v1 PEFT experiment."""

import argparse
import gc
import json
import math
import platform
import random
import re
import tempfile
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path

import torch
from documind import reflection_data
from documind.reflection import (
    DECISIONS,
    MASK,
    RELEVANCE,
    SUPPORT,
    UTILITY,
    canonical,
    digest,
    pad_batch,
    protocol_identity,
    register_tokens,
    token_map,
)
from documind.reflection_model import SMOKE, TARGET, base_model
from peft import (
    LoraConfig,
    PeftModel,
    get_peft_model,
    get_peft_model_state_dict,
)
from torch.nn import functional as F
from transformers import AutoTokenizer, GenerationConfig

MODULES = ["q_proj", "v_proj"]


def trainable_rows(model, indices):
    return [
        layer.weight.detach()[indices].float().cpu().clone()
        for layer in (model.get_input_embeddings(), model.get_output_embeddings())
    ]


def tensors(batch, pad_id, device):
    return {
        name: torch.tensor(value, device=device) for name, value in pad_batch(batch, pad_id).items()
    }


def masked_logits(model, batch):
    labels = batch["labels"][:, 1:]
    positions = (labels != MASK).any(dim=0).nonzero().flatten()
    logits = model(
        input_ids=batch["input_ids"],
        attention_mask=batch["attention_mask"],
        logits_to_keep=positions,
    ).logits
    return logits, labels[:, positions], positions


def encode(tokenizer, ids, samples, limit):
    rows = defaultdict(list)
    for sample in samples:
        for index, episode in enumerate(sample.episodes):
            if not reflection_data.model_episode(sample, episode):
                continue
            pair = reflection_data.training_pair(tokenizer, ids, sample, episode, limit)
            rows[sample.split].append((sample, index, pair))
    return rows


def train(model, examples, pad_id, args):
    optimizer = torch.optim.AdamW(
        [p for p in model.parameters() if p.requires_grad], lr=args.learning_rate
    )
    rng = random.Random(args.seed)
    losses, row_gradients = [], {}
    supervised_tokens = 0
    model.train()
    device = model.get_input_embeddings().weight.device
    for _ in range(args.epochs):
        order = rng.sample(examples, len(examples))
        for offset in range(0, len(order), args.batch_size):
            rows = order[offset : offset + args.batch_size]
            batch = tensors(rows, pad_id, device)
            supervised_tokens += sum(len(labels) - labels.count(MASK) for _, labels in rows)
            optimizer.zero_grad(set_to_none=True)
            logits, labels, _ = masked_logits(model, batch)
            loss = F.cross_entropy(
                logits.flatten(0, 1).float(), labels.flatten(), ignore_index=MASK
            )
            if not torch.isfinite(loss):
                raise RuntimeError("Training loss is non-finite")
            loss.backward()
            for name, parameter in model.named_parameters():
                if "trainable_tokens_delta" not in name or parameter.grad is None:
                    continue
                gradient = parameter.grad.detach().float().norm(dim=-1).cpu()
                row_gradients[name] = torch.maximum(
                    row_gradients.get(name, torch.zeros_like(gradient)), gradient
                )
            torch.nn.utils.clip_grad_norm_(
                [p for p in model.parameters() if p.requires_grad], 1.0, error_if_nonfinite=True
            )
            optimizer.step()
            losses.append(float(loss.detach()))
            if len(losses) % 10 == 0:
                print(f"step={len(losses)} loss={losses[-1]:.4f}", flush=True)
            if args.max_steps and len(losses) >= args.max_steps:
                return losses, row_gradients, supervised_tokens
    return losses, row_gradients, supervised_tokens


def score(model, rows, ids, pad_id, batch_size):
    model.eval()
    token_names = {number: label for label, number in ids.items()}
    families = {
        "decision": DECISIONS,
        "relevance": RELEVANCE,
        "support": SUPPORT,
        "utility": UTILITY,
    }
    token_family = {label: family for family, labels in families.items() for label in labels}
    predictions = []
    device = model.get_input_embeddings().weight.device
    with torch.inference_mode():
        for offset in range(0, len(rows), batch_size):
            chunk = rows[offset : offset + batch_size]
            batch = tensors([pair for _, _, pair in chunk], pad_id, device)
            logits, _, positions = masked_logits(model, batch)
            position_index = {position: i for i, position in enumerate(positions.tolist())}
            for i, (sample, episode, (_, labels)) in enumerate(chunk):
                for j, token in enumerate(labels):
                    gold = token_names.get(token)
                    if gold not in token_family:
                        continue
                    family = token_family[gold]
                    choices = families[family]
                    scores = logits[i, position_index[j - 1]].float()
                    raw = int(scores.argmax())
                    probabilities = (
                        scores[[ids[label] for label in choices]].softmax(dim=-1).tolist()
                    )
                    if not all(map(math.isfinite, probabilities)):
                        raise RuntimeError("Non-finite evaluation probabilities")
                    predicted = choices[max(range(len(choices)), key=probabilities.__getitem__)]
                    predictions.append(
                        {
                            "sample": sample.sample_id,
                            "split": sample.split,
                            "episode": episode,
                            "token_position": j,
                            "family": family,
                            "gold": gold,
                            "predicted": predicted,
                            "raw_token_id": raw,
                            "raw_label": token_names.get(raw),
                            "probabilities": dict(zip(choices, probabilities, strict=True)),
                            "retrieval_policy": sample.retrieval_policy,
                        }
                    )
    return predictions


def metrics(predictions):
    counts = defaultdict(Counter)
    for row in predictions:
        key = row["split"] + "/" + row["family"]
        counts[key][(row["gold"], row["predicted"])] += 1
        if row["family"] == "decision" and row["episode"] == 0:
            counts[row["split"] + "/initial_retrieval"][(row["gold"], row["predicted"])] += 1
            counts[row["split"] + "/initial_retrieval_raw"][
                (row["gold"], row["raw_label"] or "[non-reflection]")
            ] += 1
    return {
        key: {
            "n": sum(matrix.values()),
            "correct": sum(n for (gold, predicted), n in matrix.items() if gold == predicted),
            "confusion": [
                {"gold": gold, "predicted": predicted, "count": n}
                for (gold, predicted), n in sorted(matrix.items())
            ],
        }
        for key, matrix in sorted(counts.items())
    }


def file_hashes(directory):
    return {
        file.relative_to(directory).as_posix(): digest(file.read_bytes())
        for file in sorted(directory.rglob("*"))
        if file.is_file()
    }


def next_logits(model, probe):
    ids, labels = probe
    stop = next(i for i, label in enumerate(labels) if label != MASK)
    model.eval()
    with torch.inference_mode():
        device = model.get_input_embeddings().weight.device
        return (
            model(torch.tensor([ids[:stop]], device=device), logits_to_keep=1)
            .logits[0, -1]
            .float()
            .cpu()
        )


def generate_decision_probe(model, probe, pad_id):
    inputs, labels = probe
    end = next(i for i, label in enumerate(labels) if label != MASK)
    device = model.get_input_embeddings().weight.device
    prefix = torch.tensor([inputs[:end]], device=device)
    with torch.inference_mode():
        output = model.generate(
            prefix,
            attention_mask=torch.ones_like(prefix),
            generation_config=GenerationConfig(
                max_new_tokens=1,
                do_sample=False,
                pad_token_id=pad_id,
                use_cache=False,
            ),
        )
    return int(output[0, end])


def run(args):
    quantized = args.mode != "smoke"
    if quantized and not torch.cuda.is_available():
        raise SystemExit("QLoRA requires CUDA and bitsandbytes. The CPU smoke is a separate mode.")
    if args.output.exists():
        raise SystemExit("Choose a new output directory for this immutable experiment.")
    if args.mode == "smoke":
        torch.set_num_threads(min(4, torch.get_num_threads()))
    repo, revision = TARGET if args.mode == "qlora" else SMOKE
    samples, corpus_sha = reflection_data.load_corpus(args.corpus)
    if not re.fullmatch("[0-9a-f]{64}", args.expected_corpus_sha) or (
        corpus_sha != args.expected_corpus_sha
    ):
        raise SystemExit("Corpus differs from the independently frozen manifest SHA-256.")
    torch.manual_seed(args.seed)
    tokenizer = AutoTokenizer.from_pretrained(repo, revision=revision, trust_remote_code=False)
    ids = register_tokens(tokenizer)
    indices = list(ids.values())
    pad_id = (
        tokenizer.pad_token_id if tokenizer.pad_token_id is not None else tokenizer.eos_token_id
    )
    model = base_model(repo, revision, len(tokenizer), quantized, training=True)
    limit = min(model.config.max_position_embeddings, args.context_limit)
    encoded = encode(tokenizer, ids, samples, limit)
    before = trainable_rows(model, indices)
    if quantized:
        torch.cuda.reset_peak_memory_stats()
    peft_config = LoraConfig(
        task_type="CAUSAL_LM",
        target_modules=MODULES,
        r=args.rank,
        lora_alpha=2 * args.rank,
        trainable_token_indices={"embed_tokens": indices, "lm_head": indices},
        revision=revision,
    )
    model = get_peft_model(model, peft_config)
    print(f"training {repo} on {model.get_input_embeddings().weight.device}", flush=True)
    start = time.monotonic()
    losses, gradients, supervised_tokens = train(
        model, [pair for _, _, pair in encoded["train"]], pad_id, args
    )
    elapsed = time.monotonic() - start
    peak = torch.cuda.max_memory_allocated() if quantized else None
    after = trainable_rows(model, indices)
    if not gradients or any((g <= 0).any() or not g.isfinite().all() for g in gradients.values()):
        raise RuntimeError("Reflection token rows did not all receive finite nonzero gradients")
    if any((a == b).all(dim=1).any() for a, b in zip(before, after, strict=True)):
        raise RuntimeError("Every reflection row must change in both input and output embeddings")
    probe = encoded["train"][0][2]
    expected = next_logits(model, probe)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent) as scratch:
        path = Path(scratch) / "bundle"
        model.save_pretrained(path / "adapter", save_embedding_layers=False)
        tokenizer.save_pretrained(path / "tokenizer")
        (path / "token_id_map.json").write_text(canonical(ids) + "\n", encoding="utf-8")
        state = {
            key: tensor.detach().cpu()
            for key, tensor in get_peft_model_state_dict(
                model,
                save_embedding_layers=False,
            ).items()
        }
        del model
        gc.collect()
        if quantized:
            torch.cuda.empty_cache()
        reload_tokenizer = AutoTokenizer.from_pretrained(path / "tokenizer", local_files_only=True)
        if (
            token_map(reload_tokenizer) != ids
            or reload_tokenizer.chat_template != tokenizer.chat_template
        ):
            raise RuntimeError("Saved tokenizer or chat template differs")
        model = PeftModel.from_pretrained(
            base_model(repo, revision, len(tokenizer), quantized), path / "adapter"
        )
        loaded = get_peft_model_state_dict(model, save_embedding_layers=False)
        if state.keys() != loaded.keys() or any(
            not torch.equal(tensor, loaded[key].cpu()) for key, tensor in state.items()
        ):
            raise RuntimeError("Saved adapter parameters differ after reload")
        if any(
            not torch.equal(a, b)
            for a, b in zip(after, trainable_rows(model, indices), strict=True)
        ):
            raise RuntimeError("Saved embedding/output token rows differ after reload")
        torch.testing.assert_close(next_logits(model, probe), expected, atol=1e-3, rtol=1e-3)
        generated_token = generate_decision_probe(model, probe, pad_id)
        if generated_token != int(expected.argmax()):
            raise RuntimeError("Reloaded greedy decision differs from the trained probe logits")
        scored_splits = ["validation", "test"] if args.evaluate_test else ["validation"]
        rows = [row for split in scored_splits for row in encoded[split]]
        predictions = score(model, rows, ids, pad_id, args.batch_size)
        (path / "predictions.jsonl").write_text(
            "".join(canonical(row) + "\n" for row in predictions), encoding="utf-8"
        )
        metrics_by_family = metrics(predictions)
        (path / "metrics.json").write_text(canonical(metrics_by_family) + "\n", encoding="utf-8")
        probe_choices = [ids[label] for label in DECISIONS]
        manifest = {
            **protocol_identity(),
            "release_status": "experimental_unapproved",
            "mode": args.mode,
            "created_at": datetime.now(UTC).isoformat(),
            "base_model": repo,
            "base_revision": revision,
            "corpus_manifest_sha256": corpus_sha,
            "corpus_validator_sha256": digest(Path(reflection_data.__file__).read_bytes()),
            "trainer_sha256": digest(Path(__file__).read_bytes()),
            "chat_template_sha256": digest(tokenizer.chat_template.encode()),
            "context_limit": limit,
            "lora_modules": MODULES,
            "lora_rank": args.rank,
            "epochs_limit": args.epochs,
            "batch_size": args.batch_size,
            "seed": args.seed,
            "learning_rate": args.learning_rate,
            "optimizer": "torch.optim.AdamW",
            "precision": "nf4-fp16-double-quant" if quantized else "fp32",
            "packages": {
                name: version(name)
                for name in ("torch", "transformers", "peft", "accelerate")
                + (("bitsandbytes",) if quantized else ())
            },
            "gpu": torch.cuda.get_device_name() if quantized else None,
            "cuda": torch.version.cuda if quantized else None,
            "platform": platform.platform(),
            "peak_gpu_bytes": peak,
            "training_seconds": elapsed,
            "supervised_tokens": supervised_tokens,
            "supervised_tokens_per_second": supervised_tokens / elapsed,
            "losses": losses,
            "minimum_row_gradient": min(float(g.min()) for g in gradients.values()),
            "probe_raw_token_id": int(expected.argmax()),
            "probe_generated_token_id": generated_token,
            "probe_gold_token_id": probe[1][-1],
            "probe_decision_token_id": probe_choices[int(expected[probe_choices].argmax())],
            "trained_rows_checked": len(indices),
            "reload_verified": True,
            "metrics_kind": "teacher_forced_family_constrained",
            "loss_policy": "unsupported_answer_words_masked_v1",
            "scored_splits": scored_splits,
            "files": file_hashes(path),
        }
        (path / "manifest.json").write_text(canonical(manifest) + "\n", encoding="utf-8")
        path.rename(args.output)
    print(
        json.dumps(
            {"bundle": str(args.output), "steps": len(losses), "metrics": metrics_by_family},
            indent=2,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("smoke", "qlora-smoke", "qlora"), required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--expected-corpus-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--rank", type=int, default=16)
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--max-steps", type=int)
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--context-limit", type=int, default=512)
    parser.add_argument("--learning-rate", type=float, default=5e-4)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument(
        "--evaluate-test",
        action="store_true",
        help="Score the frozen test split on the selected checkpoint. Tuning uses validation only.",
    )
    args = parser.parse_args()
    if (
        min(args.rank, args.epochs, args.batch_size, args.context_limit) < 1
        or (args.max_steps is not None and args.max_steps < 1)
        or not math.isfinite(args.learning_rate)
        or args.learning_rate <= 0
    ):
        parser.error("Training dimensions and learning rate must be positive")
    run(args)


if __name__ == "__main__":
    main()
