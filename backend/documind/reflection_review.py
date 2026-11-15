"""Prepare and validate manual Phase 5 review evidence for an immutable bundle."""

import argparse
import json
from collections import defaultdict
from math import isclose, isfinite
from pathlib import Path
from typing import Literal

from pydantic import Field, ValidationError

from documind.reflection import (
    DECISIONS,
    RELEVANCE,
    SUPPORT,
    SYMBOLS,
    UTILITY,
    canonical,
    digest,
    protocol_identity,
    target_parts,
)
from documind.reflection_data import Row, episode_context, load_corpus, model_episode

FAMILIES = {"decision": DECISIONS, "relevance": RELEVANCE, "support": SUPPORT, "utility": UTILITY}
LABEL_FAMILY = {label: family for family, labels in FAMILIES.items() for label in labels}
MIN_CASES = 20
BUNDLE_FILES = {
    "adapter/adapter_model.safetensors",
    "adapter/adapter_config.json",
    "tokenizer/tokenizer.json",
    "token_id_map.json",
    "predictions.jsonl",
}


def valid_prediction(row):
    family = FAMILIES[row["family"]]
    probabilities = row.get("probabilities")
    return (
        isinstance(probabilities, dict)
        and set(probabilities) == set(family)
        and all(
            type(value) in (int, float) and 0 <= value <= 1 and isfinite(value)
            for value in probabilities.values()
        )
        and isclose(sum(probabilities.values()), 1, abs_tol=1e-5)
        and row["predicted"] == max(family, key=probabilities.__getitem__)
    )


class CaseReview(Row):
    sample_id: str
    verdict: Literal["accept", "reject"]
    note: str = Field(min_length=1)


class ManualReview(Row):
    bundle_manifest_sha256: str
    generated_evaluation_sha256: str
    reviewer: str = Field(min_length=1)
    cases: list[CaseReview]


class RecordedReview(ManualReview):
    corpus_manifest_sha256: str
    accepted_cases: int = Field(ge=0)
    rejected_cases: int = Field(ge=0)
    release_status: Literal["experimental_unapproved"]


class GeneratedEpisode(Row):
    sample_id: str
    episode: int = Field(ge=0)
    category: Literal[
        "initial_required",
        "initial_eligible",
        "general_candidate",
        "supported_candidate",
        "irrelevant_candidate",
    ]
    expected: str
    actual_text: str = Field(max_length=8192)
    labels: dict[str, str]
    finish_reason: Literal[
        "complete", "invalid_token", "budget", "context_limit", "deadline", "cancelled"
    ]
    correct: bool
    false_full_support: bool


def generations(path, bundle_sha, corpus_sha, samples):
    raw = path.read_bytes()
    artifact = json.loads(raw)
    if (
        artifact["bundle_manifest_sha256"] != bundle_sha
        or artifact["corpus_manifest_sha256"] != corpus_sha
        or artifact["split"] != "test"
    ):
        raise ValueError("Generated evaluation belongs to a different checkpoint, corpus or split")
    rows = {}
    for raw_row in artifact["episodes"]:
        try:
            row = GeneratedEpisode.model_validate(raw_row).model_dump()
        except ValidationError:
            raise ValueError("Invalid generated evaluation episode") from None
        key = (row["sample_id"], row["episode"])
        if key in rows:
            raise ValueError("Duplicate generated evaluation episode")
        rows[key] = row
    outputs = {}
    for sample in (sample for sample in samples if sample.split == "test"):
        seen = set()
        anchors = {anchor.source_id: anchor for anchor in sample.source_anchors}
        supported = {
            anchors[ep.evidence[0]].quote: target_parts("CANDIDATE", True, ep.target)[1]
            for ep in sample.episodes
            if ep.state == "CANDIDATE" and ep.evidence and "[IsSUP:Fully]" in ep.target
        }
        for index, episode in enumerate(sample.episodes):
            if index != 0 and episode.state != "CANDIDATE":
                continue
            quote = anchors[episode.evidence[0]].quote if episode.evidence else None
            if episode.state == "CANDIDATE" and quote in seen:
                continue
            if episode.state == "CANDIDATE":
                seen.add(quote)
            row = rows.pop((sample.sample_id, index), None)
            if row is None:
                raise ValueError("Missing generated held-out episode")
            labels = row["labels"]
            if any(
                name not in FAMILIES or label not in FAMILIES[name]
                for name, label in labels.items()
            ):
                raise ValueError("Invalid generated output")
            if episode.state == "DECIDE":
                category = "initial_" + sample.retrieval_policy
                expected = episode.target
                correct = labels.get("decision") == expected
            elif not episode.evidence:
                category = "general_candidate"
                expected = target_parts("CANDIDATE", False, episode.target)[0]
                correct = row["actual_text"] == expected
            elif quote in supported:
                category = "supported_candidate"
                expected = supported[quote]
                correct = (
                    row["actual_text"] == expected and labels.get("support") == "[IsSUP:Fully]"
                )
            else:
                category = "irrelevant_candidate"
                expected = "[IsREL:No]"
                correct = labels.get("relevance") == expected
            false_support = bool(
                episode.evidence
                and labels.get("support") == "[IsSUP:Fully]"
                and row["actual_text"] != supported.get(quote)
            )
            if (
                row["expected"] != expected
                or row["category"] != category
                or row["correct"] != bool(correct and row["finish_reason"] == "complete")
                or row["false_full_support"] != false_support
            ):
                raise ValueError("Generated evaluation outcome differs from the frozen case")
            outputs[sample.sample_id, index] = row
    if rows:
        raise ValueError("Generated evaluation contains episodes outside the frozen holdout")
    return outputs, digest(raw), artifact.get("method", "unspecified")


def checked_bundle(bundle, expected_sha):
    raw = (bundle / "manifest.json").read_bytes()
    if digest(raw) != expected_sha:
        raise ValueError("Checkpoint manifest does not match its expected SHA-256")
    manifest = json.loads(raw)
    if any(manifest.get(k) != v for k, v in protocol_identity().items()) or not manifest.get(
        "reload_verified"
    ):
        raise ValueError("Checkpoint and review corpus or protocol differ")
    files = {}
    for path in bundle.rglob("*"):
        if path.is_symlink():
            raise ValueError("Symlinks are not permitted inside a checkpoint bundle")
        if path.is_file() and path != bundle / "manifest.json":
            files[path.relative_to(bundle).as_posix()] = digest(path.read_bytes())
    if files != manifest["files"] or not BUNDLE_FILES <= files.keys():
        raise ValueError("Checkpoint file integrity check failed")
    ids = json.loads((bundle / "token_id_map.json").read_bytes())
    if (
        set(ids) != set(SYMBOLS)
        or any(type(value) is not int or value < 0 for value in ids.values())
        or len(set(ids.values())) != len(SYMBOLS)
    ):
        raise ValueError("Invalid reflection token map")
    return manifest, ids


def review_packet(bundle, corpus, expected_sha, generated_evaluation):
    manifest, ids = checked_bundle(bundle, expected_sha)
    samples, corpus_sha = load_corpus(corpus)
    if manifest["corpus_manifest_sha256"] != corpus_sha:
        raise ValueError("Checkpoint and review corpus differ")
    generated, generated_sha, method = generations(
        generated_evaluation, expected_sha, corpus_sha, samples
    )
    labels = {number: symbol for symbol, number in ids.items()}

    prediction_rows = [
        json.loads(line) for line in (bundle / "predictions.jsonl").read_bytes().splitlines()
    ]
    supplemental = json.loads(generated_evaluation.read_bytes()).get("conditional_evaluation")
    if supplemental is not None:
        required = {
            "sample",
            "split",
            "episode",
            "gold",
            "family",
            "predicted",
            "raw_label",
            "raw_token_id",
            "retrieval_policy",
            "probabilities",
        }
        if (
            not isinstance(supplemental, dict)
            or supplemental.get("bundle_manifest_sha256") != expected_sha
            or supplemental.get("corpus_manifest_sha256") != corpus_sha
            or supplemental.get("split") != "test"
            or not isinstance(supplemental.get("predictions"), list)
            or any(
                not isinstance(row, dict)
                or not required <= row.keys()
                or row.get("split") != "test"
                for row in supplemental["predictions"]
            )
            or "test" in manifest.get("scored_splits", ["test"])
            or any(row["split"] == "test" for row in prediction_rows)
        ):
            raise ValueError("Invalid or conflicting supplemental conditional evaluation")
        prediction_rows.extend(supplemental["predictions"])

    predictions = defaultdict(list)
    for row in prediction_rows:
        if row["split"] == "test":
            predictions[row["sample"], row["episode"]].append(row)

    cases = []
    for sample in (row for row in samples if row.split == "test"):
        episodes = []
        for index, episode in enumerate(sample.episodes):
            parts = target_parts(episode.state, bool(episode.evidence), episode.target)
            gold = [
                (part, LABEL_FAMILY[part])
                for part in parts
                if model_episode(sample, episode) and part in LABEL_FAMILY
            ]
            rows = predictions.pop((sample.sample_id, index), [])
            if [(row["gold"], row["family"]) for row in rows] != gold or any(
                row["retrieval_policy"] != sample.retrieval_policy
                or row["raw_label"] != labels.get(row["raw_token_id"])
                or not valid_prediction(row)
                for row in rows
            ):
                raise ValueError(
                    f"Missing or inconsistent held-out predictions: {sample.sample_id}"
                )
            episodes.append(
                {
                    "state": episode.state,
                    "context": episode_context(sample, episode),
                    "target": episode.target,
                    "predictions": rows,
                    "generated": generated.get((sample.sample_id, index)),
                    "generation_status": "evaluated"
                    if (sample.sample_id, index) in generated
                    else "not_evaluated",
                }
            )
        cases.append(
            {
                "sample_id": sample.sample_id,
                "retrieval_policy": sample.retrieval_policy,
                "episodes": episodes,
            }
        )
    if predictions:
        raise ValueError("Checkpoint contains predictions outside the frozen test cases")
    return {
        "bundle_manifest_sha256": expected_sha,
        "corpus_manifest_sha256": corpus_sha,
        "generated_evaluation_sha256": generated_sha,
        "generation_method": method,
        "conditional_prediction_source": "generated_evaluation" if supplemental else "bundle",
        "base_model": manifest["base_model"],
        "base_revision": manifest["base_revision"],
        "cases": cases,
    }


def record_review(packet, review):
    if review.bundle_manifest_sha256 != packet["bundle_manifest_sha256"]:
        raise ValueError("Review refers to a different checkpoint")
    if review.generated_evaluation_sha256 != packet["generated_evaluation_sha256"]:
        raise ValueError("Review refers to different generated outputs")
    policies = {row["sample_id"]: row["retrieval_policy"] for row in packet["cases"]}
    ids = {row.sample_id for row in review.cases}
    if (
        not review.reviewer.strip()
        or len(ids) != len(review.cases)
        or not ids <= policies.keys()
        or any(not row.note.strip() for row in review.cases)
    ):
        raise ValueError("Review requires a reviewer, unique held-out cases and written notes")
    if len(ids) < MIN_CASES or {policies[identifier] for identifier in ids} != {
        "required",
        "eligible",
    }:
        raise ValueError("Review requires 20 distinct cases covering both retrieval policies")
    unsafe = {
        case["sample_id"]
        for case in packet["cases"]
        if any(ep["generated"] and ep["generated"]["false_full_support"] for ep in case["episodes"])
    }
    if any(row.verdict == "accept" and row.sample_id in unsafe for row in review.cases):
        raise ValueError("Accepted case contains false full support")
    return {
        **review.model_dump(),
        "corpus_manifest_sha256": packet["corpus_manifest_sha256"],
        "accepted_cases": sum(row.verdict == "accept" for row in review.cases),
        "rejected_cases": sum(row.verdict == "reject" for row in review.cases),
        "release_status": "experimental_unapproved",
    }


def load_review(packet, path):
    recorded = RecordedReview.model_validate_json(path.read_bytes())
    submission = ManualReview.model_validate(
        recorded.model_dump(include=set(ManualReview.model_fields))
    )
    if record_review(packet, submission) != recorded.model_dump():
        raise ValueError("Recorded review differs from its verified outputs or derived counts")
    if recorded.rejected_cases:
        raise ValueError("The release review includes rejected cases")
    return recorded


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--expected-bundle-sha", required=True)
    parser.add_argument("--generated-evaluation", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--review", type=Path, help="Record completed human review instead of exporting cases"
    )
    args = parser.parse_args()
    try:
        result = review_packet(
            args.bundle, args.corpus, args.expected_bundle_sha, args.generated_evaluation
        )
        if args.review:
            result = record_review(
                result, ManualReview.model_validate_json(args.review.read_bytes())
            )
        with args.output.open("x", encoding="utf-8") as output:
            output.write(canonical(result) + "\n")
    except (ValueError, OSError, KeyError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
