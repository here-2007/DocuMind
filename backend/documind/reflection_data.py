"""Frozen synthetic reflection corpus and strict JSONL export validation."""

import json
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from documind.reflection import (
    MASK,
    SUPPORT,
    VERSION,
    canonical,
    digest,
    encode_episode,
    protocol_identity,
    target_parts,
)

SPLITS = ("train", "validation", "test")
FILES = ("sources.jsonl", "samples.jsonl")
CORPUS = "synthetic-reflection-v1"


class Row(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Source(Row):
    source_id: str = Field(min_length=1)
    source_group_id: str = Field(min_length=1)
    template_id: str = Field(min_length=1)
    page_number: int = Field(default=1, ge=1)
    text: str = Field(min_length=1, max_length=6000)


class Anchor(Row):
    source_id: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_number: int = Field(ge=1)
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    quote: str = Field(min_length=1)


class Episode(Row):
    state: str
    evidence: list[str] = Field(max_length=5)
    segments: list[str] = Field(max_length=8)
    target: str = Field(min_length=1, max_length=2048)


class Sample(Row):
    sample_id: str = Field(min_length=1)
    source_group_id: str = Field(min_length=1)
    split: str
    protocol_version: str
    question: str = Field(min_length=1, max_length=1500)
    query_type: str
    retrieval_policy: str
    source_anchors: list[Anchor]
    label_origin: str
    human_verified: bool
    episodes: list[Episode] = Field(min_length=1)


def normalized(value):
    return " ".join(re.findall(r"\w+", value.casefold()))


def validate_trajectory(sample, anchors):
    required = sample.retrieval_policy == "required"
    first = sample.episodes[0]
    expected = "[Retrieve]" if required else "[NoRetrieve]"
    if first.state != "DECIDE" or first.evidence or first.segments or first.target != expected:
        raise ValueError("Missing initial adjudicated retrieval decision")

    committed, committed_sources = [], []
    retrieved, round_sources, promotable, accepted = set(), set(), set(), set()
    reuse = False
    for index, episode in enumerate(sample.episodes[1:], start=1):
        evidence = tuple(episode.evidence)
        if not set(evidence) <= anchors.keys() or len(set(evidence)) != len(evidence):
            raise ValueError("Episode evidence is outside its sample")
        parts = target_parts(episode.state, bool(evidence), episode.target)
        last = index == len(sample.episodes) - 1
        if episode.state == "CANDIDATE":
            if (
                last
                or len(evidence) != int(required)
                or episode.segments != committed
                or evidence in round_sources
                or len(round_sources) >= 5
                or (reuse and not set(evidence) <= retrieved)
            ):
                raise ValueError("Invalid candidate trajectory or evidence reuse")
            round_sources.add(evidence)
            retrieved.update(evidence)
            if not required or (len(parts) > 1 and parts[-2] == SUPPORT[0]):
                sources = tuple(dict.fromkeys([*committed_sources, *evidence]))
                promotable.add((parts[int(required)], sources))
            continue

        expected = "[Finish]" if last else "[Continue]"
        added = episode.segments[len(committed) :]
        promotion = (added[0], evidence) if len(added) == 1 else None
        promoted = promotion in promotable and promotion not in accepted
        unchanged = episode.segments == committed and evidence == tuple(committed_sources)
        empty_retrieval = index == 1 and required and not anchors
        if (
            episode.target != expected
            or episode.segments[: len(committed)] != committed
            or not (promoted or (last and unchanged))
            or not (round_sources or empty_retrieval)
            or (not last and (not required or not promoted))
        ):
            raise ValueError("Decision trajectory commits an unsupported segment or evidence")
        if promoted:
            accepted.add(promotion)
        committed, committed_sources = episode.segments, episode.evidence
        round_sources.clear()
        promotable.clear()
        reuse = True
    if sample.episodes[-1].target != "[Finish]":
        raise ValueError("Episode sequence must end with Finish")


def validate(sources, samples):
    by_source = {row.source_id: row for row in sources}
    if len(by_source) != len(sources) or len({s.sample_id for s in samples}) != len(samples):
        raise ValueError("Duplicate source or sample ID")
    assignments = {}
    questions = set()
    used = set()

    def assign(kind, value, split):
        if assignments.setdefault((kind, value), split) != split:
            raise ValueError(f"Cross-split {kind} leakage: {value}")

    for sample in samples:
        if (
            sample.split not in SPLITS
            or sample.protocol_version != VERSION
            or sample.retrieval_policy not in {"required", "eligible"}
            or sample.query_type
            != ("DOCUMENT_QA" if sample.retrieval_policy == "required" else "GENERAL")
            or not sample.label_origin.strip()
        ):
            raise ValueError("Invalid sample protocol, split, policy or label provenance")
        assign("group", sample.source_group_id, sample.split)
        question = normalized(sample.question)
        if not question or question in questions:
            raise ValueError("Duplicate or empty question")
        questions.add(question)
        anchors = {}
        for anchor in sample.source_anchors:
            source = by_source.get(anchor.source_id)
            if (
                source is None
                or source.source_group_id != sample.source_group_id
                or source.page_number != anchor.page_number
                or anchor.source_id in anchors
                or anchor.end_char > len(source.text)
                or anchor.end_char <= anchor.start_char
                or digest(source.text.encode()) != anchor.sha256
                or source.text[anchor.start_char : anchor.end_char] != anchor.quote
            ):
                raise ValueError(f"Unresolved immutable source anchor: {anchor.source_id}")
            assign("source", source.source_id, sample.split)
            assign("content", normalized(source.text), sample.split)
            assign("template", source.template_id, sample.split)
            anchors[anchor.source_id] = anchor
            used.add(source.source_id)
        if anchors and sample.retrieval_policy == "eligible":
            raise ValueError("Eligible samples cannot introduce private evidence")
        validate_trajectory(sample, anchors)
    if set(by_source) != used or {s.split for s in samples} != set(SPLITS):
        raise ValueError("Unreferenced sources or missing corpus split")


def episode_context(sample, episode):
    anchors = {row.source_id: row for row in sample.source_anchors}
    return {
        "question": sample.question,
        "segments": episode.segments,
        "evidence": [anchors[identifier].model_dump() for identifier in episode.evidence],
    }


def model_episode(sample, episode):
    # The application abstains here. The empty prompt is identical to initial DECIDE.
    return not (
        sample.retrieval_policy == "required"
        and episode.target == "[Finish]"
        and not episode.evidence
        and not episode.segments
    )


def training_pair(tokenizer, ids, sample, episode, limit):
    inputs, labels = encode_episode(
        tokenizer, ids, episode.state, episode_context(sample, episode), episode.target, limit
    )
    parts = target_parts(episode.state, bool(episode.evidence), episode.target)
    if (
        episode.state == "CANDIDATE"
        and episode.evidence
        and len(parts) > 1
        and parts[-2] != SUPPORT[0]
    ):
        # Unsupported answer words condition the critic but must not teach the generator.
        labels = [label if label in ids.values() else MASK for label in labels]
    return inputs, labels


def lines(rows):
    return "".join(canonical(row.model_dump(exclude_defaults=True)) + "\n" for row in rows).encode()


def write_corpus(
    path,
    sources,
    samples,
    *,
    dataset=CORPUS,
    generation_method="deterministic curated templates and explicit gold rules",
):
    validate(sources, samples)
    data = dict(zip(FILES, map(lines, (sources, samples)), strict=True))
    path.mkdir(parents=True, exist_ok=False)
    manifest = {
        "dataset": dataset,
        "source_type": "synthetic",
        "license": "project-authored",
        **protocol_identity(),
        "generation_method": generation_method,
        "files": {name: digest(content) for name, content in data.items()},
        "samples": {split: sum(s.split == split for s in samples) for split in SPLITS},
    }
    for name, content in data.items():
        (path / name).write_bytes(content)
    (path / "manifest.json").write_text(canonical(manifest) + "\n", encoding="utf-8")


def load_corpus(path: Path):
    raw = (path / "manifest.json").read_bytes()
    manifest = json.loads(raw)
    if any(manifest.get(key) != value for key, value in protocol_identity().items()) or set(
        manifest["files"]
    ) != set(FILES):
        raise ValueError("Invalid corpus manifest")
    data = []
    for filename, row_type in zip(FILES, (Source, Sample), strict=True):
        content = (path / filename).read_bytes()
        if digest(content) != manifest["files"][filename]:
            raise ValueError(f"Corpus checksum mismatch: {filename}")
        data.append([row_type.model_validate_json(line) for line in content.splitlines()])
    sources, samples = data
    validate(sources, samples)
    if manifest["samples"] != {split: sum(s.split == split for s in samples) for split in SPLITS}:
        raise ValueError("Corpus split counts do not match")
    return samples, digest(raw)
