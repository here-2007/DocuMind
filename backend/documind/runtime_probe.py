"""Compare an authenticated reflection deployment to the same local checkpoint."""

import argparse
import json
import os
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from math import isclose
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from uuid import uuid4

from pydantic import SecretStr

from documind import runtime_client
from documind.reflection_data import episode_context
from documind.reflection_pilot import create_corpus
from documind.reflection_runtime import load
from documind.runtime_protocol import Capabilities, check_result, make_step

TOLERANCE = 0.001


def compare(cfg, local, decode):
    remote = Capabilities.model_validate(
        runtime_client.call(cfg, runtime_client.CAPABILITIES, "GET")
    )
    if remote.model_dump(exclude={"runtime_instance_id"}) != local.model_dump(
        exclude={"runtime_instance_id"}
    ):
        raise ValueError("Runtime differs from the pinned local checkpoint")
    sample, general, *_ = create_corpus()[1]
    rows = []
    for name, item, index in (
        ("document_decision", sample, 0),
        ("general_decision", general, 0),
        ("evidence_candidate", sample, 1),
    ):
        episode = item.episodes[index]
        step = make_step(
            remote,
            uuid4(),
            episode.state,
            episode_context(item, episode),
            1 if episode.state == "DECIDE" else 24,
            datetime.now(UTC) + timedelta(seconds=60),
        )
        actual, labels = runtime_client.invoke(cfg, remote, step, 30, nullcontext)
        expected = decode(step, Event())
        expected_labels = check_result(expected, step, local)
        if (
            actual.tokens != expected.tokens
            or actual.text != expected.text
            or actual.finish_reason != expected.finish_reason
            or actual.input_tokens != expected.input_tokens
            or labels != expected_labels
            or actual.probabilities.keys() != expected.probabilities.keys()
            or any(
                not isclose(p, expected.probabilities[family][label], abs_tol=TOLERANCE)
                for family, distribution in actual.probabilities.items()
                for label, p in distribution.items()
            )
        ):
            raise ValueError(f"Remote inference diverges on {name}")
        rows.append(
            {
                "case": name,
                "labels": labels,
                "input_tokens": actual.input_tokens,
                "output_tokens": actual.output_tokens,
                "finish_reason": actual.finish_reason,
            }
        )
    return {
        "model_id": remote.model_id,
        "model_revision": remote.model_revision,
        "manifest_sha256": remote.manifest_sha256,
        "runtime_instance_id": str(remote.runtime_instance_id),
        "mode": remote.supported_modes[0],
        "probability_tolerance": TOLERANCE,
        "cases": rows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--expected-bundle-sha", required=True)
    parser.add_argument("--pilot", action="store_true")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--review", type=Path)
    parser.add_argument("--generated-evaluation", type=Path)
    args = parser.parse_args()
    secret = os.environ.get("DOCUMIND_RUNTIME_TOKEN")
    if not secret:
        parser.error("Set DOCUMIND_RUNTIME_TOKEN in the probe environment")
    runtime_client.endpoint(args.origin, runtime_client.CAPABILITIES)
    local, decode = load(
        args.bundle,
        args.expected_bundle_sha,
        pilot=args.pilot,
        corpus=args.corpus,
        review=args.review,
        generated_evaluation=args.generated_evaluation,
    )
    cfg = SimpleNamespace(
        self_rag_runtime_origin=args.origin,
        self_rag_runtime_token=SecretStr(secret),
    )
    print(json.dumps(compare(cfg, local, decode), indent=2))


if __name__ == "__main__":
    main()
