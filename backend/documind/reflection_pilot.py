"""Export the project-authored reflection pilot as a checksummed corpus."""

import argparse
from pathlib import Path

from documind.reflection import VERSION, digest
from documind.reflection_data import Anchor, Episode, Sample, Source, write_corpus

# Each split uses a distinct family of fixtures and phrasings.
TEMPLATES = {
    "train": ("Invoice {ref}. Grand total: INR {amount}.", "What is the total on invoice {ref}?"),
    "validation": (
        "Payables docket {ref} records a liability of INR {amount}.",
        "Find the liability on payables docket {ref}.",
    ),
    "test": (
        "Dispatch register entry {ref} records INR {amount} in assessed charges.",
        "What charge was assessed in register entry {ref}?",
    ),
}
SIZES = {"train": 6, "validation": 3, "test": 12}
NEGATIVE_TEMPLATES = {
    "train": ("Invoice {ref}. Currency: INR.", "Canceled invoice {ref}Z is archived."),
    "validation": ("Docket {ref} was denominated in INR.", "Filed docket {ref}Z was moved."),
    "test": (
        "The register marks {ref} in the rupee column with the assessed figure blank.",
        "Dispatch {ref}Z has been logged in the outgoing tray.",
    ),
}


def episode(state, target, evidence=(), segments=()):
    return Episode(state=state, target=target, evidence=list(evidence), segments=list(segments))


def create_corpus():
    sources, samples = [], []
    for split, count in SIZES.items():
        template, phrasing = TEMPLATES[split]
        incomplete, absent = NEGATIVE_TEMPLATES[split]
        for i in range(count):
            key = f"{split}-{i:02d}"
            group = f"{split}-vendor-{i:02d}"
            reference = f"{split.upper()}-{400 + 3 * i}"
            amount = f"{950 + 31 * i}.00"
            answer = f"INR {amount}"
            truths = [
                ("exact", template.format(ref=reference, amount=amount), "[IsSUP:Fully][IsUSE:5]"),
                ("near", template.format(ref=reference + "X", amount=amount), None),
                (
                    "incomplete",
                    incomplete.format(ref=reference),
                    "[IsSUP:Partially][IsUSE:2]",
                ),
                (
                    "conflict",
                    template.format(ref=reference, amount=f"{11 + i}.00"),
                    "[IsSUP:No][IsUSE:1]",
                ),
                ("absent", absent.format(ref=reference), None),
            ]
            anchors, candidates = [], []
            for variant, text, critic in truths:
                source_id = key + "-" + variant
                sources.append(
                    Source(
                        source_id=source_id,
                        source_group_id=group,
                        template_id=f"{split}-{variant}",
                        text=text,
                    )
                )
                anchors.append(
                    Anchor(
                        source_id=source_id,
                        sha256=digest(text.encode()),
                        page_number=1,
                        start_char=0,
                        end_char=len(text),
                        quote=text,
                    )
                )
                target = f"[IsREL:Yes]{answer}[EndSegment]{critic}" if critic else "[IsREL:No]"
                candidates.append(episode("CANDIDATE", target, [source_id]))
            question = phrasing.format(ref=reference)
            evidence = [anchors[0].source_id]
            segments = [answer]
            if i % 2:
                question += " Give the currency code separately."
                candidates += [
                    episode("DECIDE", "[Continue]", evidence, segments),
                    episode(
                        "CANDIDATE",
                        "[IsREL:Yes]INR[EndSegment][IsSUP:Fully][IsUSE:5]",
                        evidence,
                        segments,
                    ),
                ]
                segments.append("INR")
            samples.append(
                Sample(
                    sample_id=key,
                    source_group_id=group,
                    split=split,
                    protocol_version=VERSION,
                    question=question,
                    query_type="DOCUMENT_QA",
                    retrieval_policy="required",
                    source_anchors=anchors,
                    label_origin="project_synthetic_gold_v1",
                    human_verified=False,
                    episodes=[
                        episode("DECIDE", "[Retrieve]"),
                        *candidates,
                        episode("DECIDE", "[Finish]", evidence, segments),
                    ],
                )
            )
            # Self-contained skip probes use different task families by split.
            supplied, question = {
                "train": (str(i + 2), f"What is {i + 1} plus one?"),
                "validation": (f"mint-{i}", f"The color code is mint-{i}. Repeat the color code."),
                "test": (f"TR-{721 + i}", f"Repeat this supplied reference: TR-{721 + i}."),
            }[split]
            samples.append(
                Sample(
                    sample_id=key + "-general",
                    source_group_id=key + "-general",
                    split=split,
                    protocol_version=VERSION,
                    question=question,
                    query_type="GENERAL",
                    retrieval_policy="eligible",
                    source_anchors=[],
                    label_origin="project_synthetic_gold_v1",
                    human_verified=False,
                    episodes=[
                        episode("DECIDE", "[NoRetrieve]"),
                        episode("CANDIDATE", supplied + "[EndSegment][IsUSE:5]"),
                        episode("DECIDE", "[Finish]", segments=[supplied]),
                    ],
                )
            )
    return sources, samples


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_corpus(args.output, *create_corpus())
