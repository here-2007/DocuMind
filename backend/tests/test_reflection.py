"""Phase 5 contract tests with generated labels and a real pinned tokenizer."""

import json
import os
from pathlib import Path

import pytest
from documind import selfrag
from documind.reflection import (
    MASK,
    SYMBOLS,
    canonical,
    digest,
    encode_episode,
    pad_batch,
    protocol_identity,
    register_tokens,
    target_parts,
)
from documind.reflection_corpus import export
from documind.reflection_corpus_v3 import export as export_v3
from documind.reflection_data import (
    episode_context,
    load_corpus,
    model_episode,
    training_pair,
    validate,
    write_corpus,
)
from documind.reflection_pilot import create_corpus
from documind.reflection_review import (
    FAMILIES,
    LABEL_FAMILY,
    ManualReview,
    load_review,
    record_review,
    review_packet,
)


def test_corpus_reproduces_frozen_manifest_and_rejects_mutation(tmp_path):
    path = tmp_path / "corpus"
    write_corpus(path, *create_corpus())
    assert (path / "manifest.json").read_bytes() == (
        Path(__file__).parent / "fixtures/phase5_reflection_manifest.json"
    ).read_bytes()
    samples, _ = load_corpus(path)
    assert (
        sum(sample.split == "test" and sample.retrieval_policy == "required" for sample in samples)
        == 12
    )
    assert (
        sum(sample.split == "test" and sample.retrieval_policy == "eligible" for sample in samples)
        == 12
    )
    assert not any(sample.human_verified for sample in samples)
    with (path / "samples.jsonl").open("ab") as file:
        file.write(b"\n")
    with pytest.raises(ValueError, match="checksum"):
        load_corpus(path)


def test_split_and_anchor_validation():
    sources, samples = create_corpus()
    test = next(source for source in sources if source.source_id.startswith("test-"))
    test.template_id = sources[0].template_id
    with pytest.raises(ValueError, match="Cross-split template"):
        validate(sources, samples)
    test.template_id = "independent-template"
    samples[0].source_anchors[0].page_number = 2
    with pytest.raises(ValueError, match="Unresolved immutable source"):
        validate(sources, samples)
    sources[0].page_number = 2
    validate(sources, samples)
    samples[0].source_anchors[0].quote = "counterfeit"
    with pytest.raises(ValueError, match="Unresolved immutable source"):
        validate(sources, samples)


@pytest.mark.parametrize(
    "invalid", ["fabricated_segment", "unsupported_source", "reuse_scope", "duplicate_commit"]
)
def test_trajectory_rejects_unproven_commits_and_new_evidence(invalid):
    sources, samples = create_corpus()
    sample = next(s for s in samples if any(e.target == "[Continue]" for e in s.episodes))
    continuation = next(i for i, e in enumerate(sample.episodes) if e.target == "[Continue]")
    if invalid == "fabricated_segment":
        sample.episodes[continuation].segments = ["This answer never appeared in a candidate."]
    elif invalid == "unsupported_source":
        sample.episodes[continuation].evidence = sample.episodes[3].evidence
    elif invalid == "reuse_scope":
        unreturned = sample.episodes.pop(5).evidence
        sample.episodes[continuation].evidence = unreturned
    else:
        sample.episodes[continuation + 1].target = sample.episodes[1].target
        sample.episodes[-1].segments[-1] = sample.episodes[continuation].segments[-1]
    with pytest.raises(ValueError, match="trajectory"):
        validate(sources, samples)


def test_required_empty_retrieval_can_finish_without_a_claim():
    sources, samples = create_corpus()
    sample = samples[0]
    removed = {anchor.source_id for anchor in sample.source_anchors}
    sample.source_anchors = []
    sample.episodes = [sample.episodes[0], sample.episodes[-1]]
    sample.episodes[-1].evidence = []
    sample.episodes[-1].segments = []
    validate([source for source in sources if source.source_id not in removed], samples)
    assert model_episode(sample, sample.episodes[0])
    assert not model_episode(sample, sample.episodes[1])
    assert episode_context(sample, sample.episodes[0]) == episode_context(
        sample, sample.episodes[1]
    )


def test_expanded_corpus_matches_frozen_manifest_and_general_policy(tmp_path):
    directory = tmp_path / "corpus"
    export(directory)
    assert (directory / "manifest.json").read_bytes() == (
        Path(__file__).parent / "fixtures/phase6_reflection_corpus_manifest.json"
    ).read_bytes()
    samples, _ = load_corpus(directory)
    test = [row for row in samples if row.split == "test"]
    assert len(test) == 24
    assert sum(row.retrieval_policy == "required" for row in test) == 12
    assert all(selfrag.eligible(row.question) for row in test if row.retrieval_policy == "eligible")


def test_improved_corpus_freezes_opaque_anchors_and_changed_value_pairs(tmp_path):
    from uuid import UUID

    corpus = tmp_path / "v3"
    export_v3(corpus)
    assert (corpus / "manifest.json").read_bytes() == (
        Path(__file__).parent / "fixtures/phase6_reflection_corpus_v3_manifest.json"
    ).read_bytes()
    samples, _ = load_corpus(corpus)
    assert {
        split: sum(row.split == split for row in samples)
        for split in ("train", "validation", "test")
    } == {"train": 96, "validation": 24, "test": 36}
    pairs = {}
    for sample in samples:
        if sample.retrieval_policy != "required":
            continue
        for anchor in sample.source_anchors:
            assert str(UUID(anchor.source_id)) == anchor.source_id
        fully = next(ep for ep in sample.episodes if "[IsSUP:Fully]" in ep.target)
        negative = next(ep for ep in sample.episodes if "[IsSUP:No]" in ep.target)
        anchors = {anchor.source_id: anchor.quote for anchor in sample.source_anchors}
        assert anchors[fully.evidence[0]] == anchors[negative.evidence[0]]
        assert (
            target_parts(fully.state, True, fully.target)[1]
            != target_parts(negative.state, True, negative.target)[1]
        )
        pairs.setdefault(sample.source_group_id, []).append(fully.target)
    assert all(len(targets) == 2 and targets[0] != targets[1] for targets in pairs.values())


@pytest.fixture
def review_artifacts(tmp_path):
    corpus, bundle = tmp_path / "corpus", tmp_path / "bundle"
    export(corpus)
    samples, _ = load_corpus(corpus)
    ids = dict(zip(SYMBOLS, range(100, 100 + len(SYMBOLS)), strict=True))
    predictions = []
    for sample in (row for row in samples if row.split == "test"):
        for index, episode in enumerate(sample.episodes):
            if not model_episode(sample, episode):
                continue
            for part in target_parts(episode.state, bool(episode.evidence), episode.target):
                if part in LABEL_FAMILY:
                    predictions.append(
                        {
                            "split": "test",
                            "sample": sample.sample_id,
                            "episode": index,
                            "gold": part,
                            "family": LABEL_FAMILY[part],
                            "predicted": part,
                            "probabilities": {
                                label: int(label == part) for label in FAMILIES[LABEL_FAMILY[part]]
                            },
                            "raw_label": part,
                            "raw_token_id": ids[part],
                            "retrieval_policy": sample.retrieval_policy,
                        }
                    )
    files = {
        "adapter/adapter_model.safetensors": b"fixture only",
        "adapter/adapter_config.json": b"{}",
        "tokenizer/tokenizer.json": b"{}",
        "token_id_map.json": canonical(ids).encode(),
        "predictions.jsonl": b"\n".join(canonical(row).encode() for row in predictions),
    }
    for name, content in files.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    manifest = {
        **protocol_identity(),
        "corpus_manifest_sha256": digest((corpus / "manifest.json").read_bytes()),
        "base_model": "fixture",
        "base_revision": "fixture",
        "reload_verified": True,
        "files": {name: digest(content) for name, content in files.items()},
    }
    raw = canonical(manifest).encode()
    (bundle / "manifest.json").write_bytes(raw)
    sha = digest(raw)
    generations = []
    for sample in (row for row in samples if row.split == "test"):
        anchors = {anchor.source_id: anchor for anchor in sample.source_anchors}
        supported = {
            anchors[ep.evidence[0]].quote: target_parts("CANDIDATE", True, ep.target)[1]
            for ep in sample.episodes
            if ep.state == "CANDIDATE" and ep.evidence and "[IsSUP:Fully]" in ep.target
        }
        seen = set()
        for index, episode in enumerate(sample.episodes):
            quote = anchors[episode.evidence[0]].quote if episode.evidence else None
            if index != 0 and episode.state != "CANDIDATE":
                continue
            if episode.state == "CANDIDATE" and quote in seen:
                continue
            if episode.state == "CANDIDATE":
                seen.add(quote)
            if episode.state == "DECIDE":
                expected, text, labels = episode.target, "", {"decision": episode.target}
                category = "initial_" + sample.retrieval_policy
            elif not episode.evidence:
                category = "general_candidate"
                expected = text = target_parts("CANDIDATE", False, episode.target)[0]
                labels = {"utility": "[IsUSE:5]"}
            elif quote in supported:
                category = "supported_candidate"
                expected = text = supported[quote]
                labels = {
                    "relevance": "[IsREL:Yes]",
                    "support": "[IsSUP:Fully]",
                    "utility": "[IsUSE:5]",
                }
            else:
                category = "irrelevant_candidate"
                expected, text, labels = "[IsREL:No]", "", {"relevance": "[IsREL:No]"}
            generations.append(
                {
                    "sample_id": sample.sample_id,
                    "episode": index,
                    "actual_text": text,
                    "labels": labels,
                    "finish_reason": "complete",
                    "expected": expected,
                    "correct": True,
                    "false_full_support": False,
                    "category": category,
                }
            )
    generated = tmp_path / "generated-fixture.json"
    generated.write_text(
        canonical(
            {
                "bundle_manifest_sha256": sha,
                "corpus_manifest_sha256": manifest["corpus_manifest_sha256"],
                "split": "test",
                "episodes": generations,
            }
        )
    )
    return bundle, corpus, sha, generated, manifest, files


def test_manual_review_is_bound_to_intact_checkpoint_and_heldout_cases(review_artifacts):
    bundle, corpus, sha, generated, manifest, files = review_artifacts
    packet = review_packet(bundle, corpus, sha, generated)
    assert len(packet["cases"]) == 24
    assert packet["cases"][0]["episodes"][0]["predictions"][0]["raw_label"] == "[Retrieve]"
    empty = next(case for case in packet["cases"] if case["sample_id"] == "test-release-07")
    assert empty["episodes"][-1]["predictions"] == []

    submission = {
        "bundle_manifest_sha256": sha,
        "generated_evaluation_sha256": packet["generated_evaluation_sha256"],
        "reviewer": "test reviewer",
        "cases": [
            {"sample_id": case["sample_id"], "verdict": "accept", "note": "fixture"}
            for case in packet["cases"][:20]
        ],
    }
    review = ManualReview.model_validate(submission)
    assert record_review(packet, review)["accepted_cases"] == 20
    assert record_review(packet, review)["release_status"] == "experimental_unapproved"
    with pytest.raises(ValueError, match="different checkpoint"):
        record_review(packet, review.model_copy(update={"bundle_manifest_sha256": "different"}))
    with pytest.raises(ValueError, match="20 distinct"):
        record_review(packet, review.model_copy(update={"cases": review.cases[:19]}))
    with pytest.raises(ValueError, match="different generated"):
        record_review(packet, review.model_copy(update={"generated_evaluation_sha256": "0" * 64}))
    (bundle / "adapter/adapter_model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="integrity"):
        review_packet(bundle, corpus, sha, generated)
    (bundle / "adapter/adapter_model.safetensors").write_bytes(
        files["adapter/adapter_model.safetensors"]
    )
    path = bundle / "predictions.jsonl"
    path.write_bytes(b"\n".join(path.read_bytes().splitlines()[1:]))
    manifest["files"]["predictions.jsonl"] = digest(path.read_bytes())
    raw = canonical(manifest).encode()
    (bundle / "manifest.json").write_bytes(raw)
    artifact = json.loads(generated.read_bytes())
    artifact["bundle_manifest_sha256"] = digest(raw)
    generated.write_text(canonical(artifact))
    with pytest.raises(ValueError, match="inconsistent held-out predictions"):
        review_packet(bundle, corpus, digest(raw), generated)


@pytest.mark.parametrize(
    "change",
    [
        None,
        "bundle",
        "corpus",
        "split",
        "missing",
        "malformed",
        "conflicting",
        "negative",
        "wrong_winner",
        "non_finite",
        "unnormalized",
        "wrong_family",
    ],
)
def test_validation_selected_bundle_requires_bound_complete_test_scores(review_artifacts, change):
    bundle, corpus, _, generated, manifest, _ = review_artifacts
    predictions = [
        json.loads(line) for line in (bundle / "predictions.jsonl").read_text().splitlines()
    ]
    (bundle / "predictions.jsonl").write_text("")
    manifest["files"]["predictions.jsonl"] = digest(b"")
    manifest["scored_splits"] = (
        ["validation", "test"] if change == "conflicting" else ["validation"]
    )
    raw = canonical(manifest).encode()
    (bundle / "manifest.json").write_bytes(raw)
    sha = digest(raw)
    artifact = json.loads(generated.read_bytes())
    artifact["bundle_manifest_sha256"] = sha
    artifact["conditional_evaluation"] = {
        "bundle_manifest_sha256": "0" * 64 if change == "bundle" else sha,
        "corpus_manifest_sha256": (
            "0" * 64 if change == "corpus" else manifest["corpus_manifest_sha256"]
        ),
        "split": "validation" if change == "split" else "test",
        "predictions": predictions[1:] if change == "missing" else predictions,
    }
    first = predictions[0]
    probabilities = first["probabilities"]
    winner = first["predicted"]
    if change == "negative":
        probabilities[winner] = -0.5
    if change == "wrong_winner":
        first["predicted"] = next(label for label in probabilities if label != winner)
    if change == "non_finite":
        probabilities[winner] = float("inf")
    if change == "unnormalized":
        probabilities[winner] = 0.5
    if change == "wrong_family":
        probabilities["not-a-label"] = 0
    if change == "malformed":
        artifact["conditional_evaluation"]["predictions"][0] = {"split": "test"}
    generated.write_text(json.dumps(artifact) if change == "non_finite" else canonical(artifact))
    if change is not None:
        with pytest.raises(ValueError):
            review_packet(bundle, corpus, sha, generated)
    else:
        packet = review_packet(bundle, corpus, sha, generated)
        assert packet["conditional_prediction_source"] == "generated_evaluation"
        assert packet["cases"][0]["episodes"][0]["predictions"]
        assert packet["cases"][0]["episodes"][0]["generated"]
        artifact.pop("conditional_evaluation")
        generated.write_text(canonical(artifact))
        with pytest.raises(ValueError, match="inconsistent held-out predictions"):
            review_packet(bundle, corpus, sha, generated)


def test_recorded_cli_output_is_consumable_by_runtime_review_gate(review_artifacts, tmp_path):
    import subprocess
    import sys

    bundle, corpus, sha, generated, _, _ = review_artifacts
    packet = review_packet(bundle, corpus, sha, generated)
    submission = ManualReview(
        bundle_manifest_sha256=sha,
        generated_evaluation_sha256=packet["generated_evaluation_sha256"],
        reviewer="Synthetic unit-test reviewer",
        cases=[
            {"sample_id": case["sample_id"], "verdict": "accept", "note": "Fixture only"}
            for case in packet["cases"][:20]
        ],
    )
    submitted, recorded = tmp_path / "submission.json", tmp_path / "recorded.json"
    submitted.write_text(submission.model_dump_json())
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "documind.reflection_review",
            "--bundle",
            str(bundle),
            "--corpus",
            str(corpus),
            "--expected-bundle-sha",
            sha,
            "--generated-evaluation",
            str(generated),
            "--review",
            str(submitted),
            "--output",
            str(recorded),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert load_review(packet, recorded).accepted_cases == 20
    for field, value in (
        ("accepted_cases", 21),
        ("corpus_manifest_sha256", "0" * 64),
        ("generated_evaluation_sha256", "0" * 64),
    ):
        changed = json.loads(recorded.read_bytes())
        changed[field] = value
        submitted.write_text(canonical(changed))
        with pytest.raises(ValueError):
            load_review(packet, submitted)
    rejected = submission.model_copy(
        update={"cases": [row.model_copy(update={"verdict": "reject"}) for row in submission.cases]}
    )
    submitted.write_text(canonical(record_review(packet, rejected)))
    with pytest.raises(ValueError, match="rejected cases"):
        load_review(packet, submitted)


def test_packet_exposes_wrong_generated_answer_and_blocks_its_acceptance(review_artifacts):
    bundle, corpus, sha, generated, _, _ = review_artifacts
    artifact = json.loads(generated.read_bytes())
    row = next(
        row for row in artifact["episodes"] if row["labels"].get("support") == "[IsSUP:Fully]"
    )
    row.update(actual_text="INR 9999.00", correct=False, false_full_support=True)
    generated.write_text(canonical(artifact))
    packet = review_packet(bundle, corpus, sha, generated)
    case = next(case for case in packet["cases"] if case["sample_id"] == row["sample_id"])
    actual = case["episodes"][row["episode"]]["generated"]
    assert actual["actual_text"] == "INR 9999.00"
    assert actual["finish_reason"] == "complete" and actual["false_full_support"]
    review = ManualReview(
        bundle_manifest_sha256=sha,
        generated_evaluation_sha256=packet["generated_evaluation_sha256"],
        reviewer="Fixture",
        cases=[
            {"sample_id": case["sample_id"], "verdict": "accept", "note": "Fixture"}
            for case in packet["cases"]
        ],
    )
    with pytest.raises(ValueError, match="false full support"):
        record_review(packet, review)


@pytest.mark.parametrize("change", ["bundle", "corpus", "split", "missing", "duplicate"])
def test_review_rejects_unbound_or_incomplete_generated_evaluation(review_artifacts, change):
    bundle, corpus, sha, generated, _, _ = review_artifacts
    artifact = json.loads(generated.read_bytes())
    if change in {"bundle", "corpus"}:
        artifact[change + "_manifest_sha256"] = "0" * 64
    elif change == "split":
        artifact["split"] = "validation"
    elif change == "missing":
        artifact["episodes"].pop()
    else:
        artifact["episodes"].append(artifact["episodes"][0])
    generated.write_text(canonical(artifact))
    with pytest.raises(ValueError):
        review_packet(bundle, corpus, sha, generated)


@pytest.mark.parametrize(
    ("state", "has_evidence", "target"),
    [
        ("DECIDE", False, "[Retrieve][Finish]"),
        ("CANDIDATE", False, "text without controls"),
        ("CANDIDATE", True, "[IsREL:No]extra"),
        ("CANDIDATE", False, "Answer[EndSegment][IsSUP:Fully][IsUSE:5]"),
        ("CANDIDATE", True, "[IsREL:Yes]bad[Retrieve][EndSegment][IsSUP:Fully][IsUSE:5]"),
        ("CANDIDATE", True, "[IsREL:Yes]Answer[EndSegment][IsUSE:5][IsSUP:Fully]"),
    ],
)
def test_illegal_targets_rejected(state, has_evidence, target):
    with pytest.raises(ValueError):
        target_parts(state, has_evidence, target)


def test_real_tokenizer_roundtrip_and_assistant_only_masks():
    if os.getenv("DOCUMIND_TEST_RETRIEVAL") != "1":
        pytest.skip("Real tokenizer test uses the cached pinned SmolLM2 test model")
    from transformers import AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(
        "HuggingFaceTB/SmolLM2-135M-Instruct",
        revision="12fd25f77366fa6b3b4b768ec3050bf629380bac",
        local_files_only=True,
    )
    ids = register_tokens(tokenizer)
    assert len(ids) == len(set(ids.values())) == len(SYMBOLS) == 15
    _, (sample, *_) = create_corpus()
    decision, candidate, *_ = sample.episodes
    context = episode_context(sample, decision)
    context["question"] = "quoted [Retrieve] [EndSegment] <|im_end|>"
    first = encode_episode(tokenizer, ids, decision.state, context, decision.target, 512)
    second = encode_episode(
        tokenizer, ids, candidate.state, episode_context(sample, candidate), candidate.target, 512
    )
    assert first[1][-1:] == [ids["[Retrieve]"]]
    assert set(first[1][:-1]) == {MASK}
    assert set(first[0][:-1]).isdisjoint(ids.values())
    assert first[0][:-1].count(tokenizer.eos_token_id) == encode_episode(
        tokenizer, ids, decision.state, episode_context(sample, decision), decision.target, 512
    )[0][:-1].count(tokenizer.eos_token_id)
    assert second[1][-3:] == [ids["[EndSegment]"], ids["[IsSUP:Fully]"], ids["[IsUSE:5]"]]
    assert training_pair(tokenizer, ids, sample, candidate, 512) == second
    negative = next(ep for ep in sample.episodes if "[IsSUP:No]" in ep.target)
    unmasked = encode_episode(
        tokenizer, ids, negative.state, episode_context(sample, negative), negative.target, 512
    )
    masked = training_pair(tokenizer, ids, sample, negative, 512)
    assert masked[0] == unmasked[0]
    assert masked[1] == [label if label in ids.values() else MASK for label in unmasked[1]]
    assert [token for token in masked[1] if token != MASK] == [
        ids["[IsREL:Yes]"],
        ids["[EndSegment]"],
        ids["[IsSUP:No]"],
        ids["[IsUSE:1]"],
    ]
    assert len([label for label in unmasked[1] if label != MASK]) > 4
    batch = pad_batch([first, second], tokenizer.pad_token_id)
    assert any(0 in row for row in batch["attention_mask"])
    for labels, mask in zip(batch["labels"], batch["attention_mask"], strict=True):
        assert all(label == MASK for label, keep in zip(labels, mask, strict=True) if not keep)
    with pytest.raises(ValueError, match="context limit"):
        encode_episode(tokenizer, ids, decision.state, context, decision.target, 1)
    with pytest.raises(ValueError, match="segment tokens"):
        encode_episode(tokenizer, ids, "CANDIDATE", context, "<|im_end|>[EndSegment][IsUSE:1]", 512)
