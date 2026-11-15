"""Freeze independent invoice, reference and addition trajectories."""

import argparse
import random
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from documind.reflection import VERSION
from documind.reflection_corpus_v3 import GROUPS, LAYOUTS, create_corpus
from documind.reflection_data import Sample, write_corpus
from documind.reflection_pilot import episode

CORPUS = "documind-reflection-synthetic-v4"
HEADINGS = {
    "train": "Billing archive",
    "validation": "Vendor payment docket",
    "test": "Receivables index",
}


def export(path):
    layouts = {
        split: tuple(f"{HEADINGS[split]}\n{layout}" for layout in split_layouts)
        for split, split_layouts in LAYOUTS.items()
    }
    sources, samples = create_corpus(CORPUS, layouts, seed=CORPUS)
    # Disjoint unordered operand pairs, with the two observed regressions reserved.
    seen = {(1, 1), (14, 27)}
    for split, groups in GROUPS.items():
        rng = random.Random(f"{CORPUS}/addition/{split}")
        for index in range(groups * 2):
            while True:
                upper = (9, 49, 99)[index % 3]
                a, b = rng.randint(0, upper), rng.randint(0, upper)
                pair = tuple(sorted((a, b)))
                if pair not in seen:
                    seen.add(pair)
                    break
            key = f"{CORPUS}/{split}/addition/{index}"
            identifier = str(uuid5(NAMESPACE_URL, key))
            answer = str(a + b)
            samples.append(
                Sample(
                    sample_id=identifier,
                    source_group_id=identifier,
                    split=split,
                    protocol_version=VERSION,
                    question=f"What is {a} plus {b}?",
                    query_type="GENERAL",
                    retrieval_policy="eligible",
                    source_anchors=[],
                    label_origin=CORPUS,
                    human_verified=False,
                    episodes=[
                        episode("DECIDE", "[NoRetrieve]"),
                        episode("CANDIDATE", answer + "[EndSegment][IsUSE:5]"),
                        episode("DECIDE", "[Finish]", segments=[answer]),
                    ],
                )
            )
    write_corpus(
        path,
        sources,
        samples,
        dataset=CORPUS,
        generation_method=(
            "fresh paired invoices, supplied references and split-exclusive addition pairs"
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    export(parser.parse_args().output)
