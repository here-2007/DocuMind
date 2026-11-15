"""Measure greedy answer grounding on a frozen v3 or v4 synthetic split."""

import argparse
import importlib.util
import inspect
import json
import platform
from collections import Counter
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path
from threading import Event
from time import monotonic
from uuid import uuid4

from documind.reflection import digest, target_parts
from documind.reflection_corpus_v3 import CORPUS as V3
from documind.reflection_corpus_v4 import CORPUS as V4
from documind.reflection_data import episode_context, load_corpus
from documind.reflection_runtime import load
from documind.runtime_protocol import check_result, make_step


def conditional(bundle, expected_sha, corpus, split):
    """Score a selected immutable checkpoint without retraining or repacking it."""
    samples, corpus_sha = load_corpus(corpus)
    if json.loads((bundle / "manifest.json").read_bytes())["corpus_manifest_sha256"] != corpus_sha:
        raise ValueError("Conditional review corpus differs from the selected checkpoint")
    cap, decode = load(bundle, expected_sha, pilot=True)
    # Reuse the checked loader's exact model/tokenizer; the operator never rebuilds its adapter.
    resources = inspect.getclosurevars(inspect.unwrap(decode)).nonlocals
    trainer_path = Path(__file__).with_name("train_reflection.py")
    spec = importlib.util.spec_from_file_location("conditional_trainer", trainer_path)
    trainer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(trainer)
    tokenizer = resources["tokenizer"]
    encoded = trainer.encode(tokenizer, cap.token_ids, samples, cap.context_limit)
    predictions = trainer.score(
        resources["model"], encoded[split], cap.token_ids, tokenizer.pad_token_id, 1
    )
    assert predictions and all(row["split"] == split for row in predictions)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "bundle_manifest_sha256": expected_sha,
        "corpus_manifest_sha256": corpus_sha,
        "split": split,
        "method": "teacher-forced labels from a checked immutable checkpoint; no training",
        "evaluator_sha256": digest(Path(__file__).read_bytes()),
        "trainer_sha256": digest(trainer_path.read_bytes()),
        "runtime_capability": cap.model_dump(mode="json"),
        "predictions": predictions,
        "summary": trainer.metrics(predictions),
    }


def evaluate(bundle, expected_sha, corpus, split, *, runtime=None):
    started = monotonic()
    samples, corpus_sha = load_corpus(corpus)
    if json.loads((corpus / "manifest.json").read_bytes())["dataset"] not in {V3, V4}:
        raise ValueError("This grounding evaluator requires a v3 or v4 corpus")
    cap, decode = runtime if runtime is not None else load(bundle, expected_sha, pilot=True)
    if runtime is not None and cap.manifest_sha256 != expected_sha:
        raise ValueError("Shared runtime belongs to a different checkpoint")
    counts, episodes = Counter(), []
    for sample in (row for row in samples if row.split == split):
        anchors = {anchor.source_id: anchor for anchor in sample.source_anchors}
        supported = {
            anchors[ep.evidence[0]].quote: target_parts("CANDIDATE", True, ep.target)[1]
            for ep in sample.episodes
            if ep.state == "CANDIDATE" and "[IsSUP:Fully]" in ep.target
        }
        seen = set()
        for index, episode in enumerate(sample.episodes):
            if index != 0 and episode.state != "CANDIDATE":
                continue
            context = episode_context(sample, episode)
            quote = context["evidence"][0]["quote"] if episode.evidence else None
            if episode.state == "CANDIDATE":
                if quote in seen:
                    continue
                seen.add(quote)
            step = make_step(
                cap,
                uuid4(),
                episode.state,
                context,
                1 if episode.state == "DECIDE" else 32,
                datetime.now(UTC) + timedelta(seconds=45),
            )
            actual = decode(step, Event())
            labels = check_result(actual, step, cap)
            if episode.state == "DECIDE":
                category = "initial_" + sample.retrieval_policy
                expected = episode.target
                correct = labels.get("decision") == expected
            elif quote is None:
                category = "general_candidate"
                expected = target_parts("CANDIDATE", False, episode.target)[0]
                correct = actual.text == expected
            elif quote in supported:
                category = "supported_candidate"
                expected = supported[quote]
                correct = actual.text == expected and labels.get("support") == "[IsSUP:Fully]"
            else:
                category = "irrelevant_candidate"
                expected = "[IsREL:No]"
                correct = labels.get("relevance") == expected
            correct = bool(correct and actual.finish_reason == "complete")
            false_support = bool(
                quote is not None
                and labels.get("support") == "[IsSUP:Fully]"
                and actual.text != supported.get(quote)
            )
            counts[category, "n"] += 1
            counts[category, "correct"] += int(correct)
            counts[category, "false_full_support"] += int(false_support)
            episodes.append(
                {
                    "sample_id": sample.sample_id,
                    "episode": index,
                    "category": category,
                    "expected": expected,
                    "actual_text": actual.text,
                    "labels": labels,
                    "finish_reason": actual.finish_reason,
                    "correct": correct,
                    "false_full_support": false_support,
                }
            )
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "elapsed_seconds": monotonic() - started,
        "evaluator_sha256": digest(Path(__file__).read_bytes()),
        "bundle_manifest_sha256": expected_sha,
        "corpus_manifest_sha256": corpus_sha,
        "split": split,
        "method": "greedy episode decoding in gold contexts, duplicate evidence text decoded once",
        "summary": {
            category: {
                name: counts[category, name] for name in ("correct", "n", "false_full_support")
            }
            for category in sorted({category for category, _ in counts})
        },
        "episodes": episodes,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-bundle-sha", required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--split", choices=("validation", "test"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--conditional", action="store_true", help="Score gold-prefix label positions"
    )
    args = parser.parse_args()
    assert digest((args.bundle / "manifest.json").read_bytes()) == args.expected_bundle_sha
    operation = conditional if args.conditional else evaluate
    result = operation(args.bundle, args.expected_bundle_sha, args.corpus, args.split)
    import torch

    result["environment"] = {
        "gpu": torch.cuda.get_device_name() if torch.cuda.is_available() else None,
        "platform": platform.platform(),
        "packages": {name: version(name) for name in ("torch", "transformers", "peft")},
    }
    with args.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, indent=2)
        output.write("\n")
    print(json.dumps(result["summary"], indent=2), flush=True)
