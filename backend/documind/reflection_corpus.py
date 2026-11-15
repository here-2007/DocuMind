"""Generate the expanded synthetic business-document reflection corpus."""

import argparse
from pathlib import Path

from documind.reflection import VERSION, digest
from documind.reflection_data import Anchor, Sample, Source, write_corpus
from documind.reflection_pilot import episode

CORPUS = "documind-reflection-synthetic-v2"
GROUPS = {"train": 27, "validation": 8, "test": 12}
LAYOUTS = {
    "train": "Accounts payable entry\nInvoice number: {ref}\n{field}: {value}",
    "validation": "Supplier remittance ledger\n{field}: {value}\nInvoice number: {ref}",
    "test": (
        "Dispatch settlement statement\nInvoice number: {ref}\n{field}: {value}\nEnd of record."
    ),
}
FIELDS = (
    ("Grand total", "the total", lambda i: f"INR {675 + 37 * i}.00"),
    ("Due date", "the due date", lambda i: f"2026-12-{i % 27 + 1:02}"),
    ("Invoice date", "the invoice date", lambda i: f"2026-10-{i % 27 + 1:02}"),
    ("Currency", "the currency", lambda i: ("USD", "EUR", "INR")[i % 3]),
    ("Amount due", "the amount due", lambda i: f"USD {310 + 23 * i}.00"),
)


def create_corpus():
    sources, samples = [], []
    for split, count in GROUPS.items():
        for i in range(count):
            key = f"{split}-release-{i:02}"
            number = {"train": 140, "validation": 420, "test": 740}[split] + i
            ref = f"{split[:2].upper()}-{number}"
            field, phrase, value_for = FIELDS[i % len(FIELDS)]
            value = value_for(number)
            question = f"What is {phrase} on invoice {ref}?"
            records = [
                ("exact", ref, value, "[IsSUP:Fully][IsUSE:5]"),
                ("near", ref + "X", value, None),
                ("conflict", ref, value_for(number + 1), "[IsSUP:No][IsUSE:1]"),
            ]
            offset = i % len(records)
            anchors, candidates = [], []
            for variant, record_ref, record_value, critic in records[offset:] + records[:offset]:
                source_id = f"{key}-{variant}"
                text = LAYOUTS[split].format(ref=record_ref, field=field, value=record_value)
                sources.append(
                    Source(
                        source_id=source_id,
                        source_group_id=key,
                        template_id=f"{split}-settlement",
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
                target = f"[IsREL:Yes]{value}[EndSegment]{critic}" if critic else "[IsREL:No]"
                candidates.append(episode("CANDIDATE", target, [source_id]))
            exact = [f"{key}-exact"]
            segments = [value]
            if i % 10 == 0:
                question = f"{question} Give the currency code separately."
                currency = value.split()[0]
                candidates.extend(
                    [
                        episode("DECIDE", "[Continue]", exact, segments),
                        episode(
                            "CANDIDATE",
                            f"[IsREL:Yes]{currency}[EndSegment][IsSUP:Fully][IsUSE:5]",
                            exact,
                            segments,
                        ),
                    ]
                )
                segments.append(currency)
            if i % 11 == 7:
                missing = {row.source_id for row in anchors}
                sources[:] = [row for row in sources if row.source_id not in missing]
                anchors, candidates, exact, segments = [], [], [], []
            samples.append(
                Sample(
                    sample_id=key,
                    source_group_id=key,
                    split=split,
                    protocol_version=VERSION,
                    question=question,
                    query_type="DOCUMENT_QA",
                    retrieval_policy="required",
                    source_anchors=anchors,
                    label_origin=CORPUS,
                    human_verified=False,
                    episodes=[
                        episode("DECIDE", "[Retrieve]"),
                        *candidates,
                        episode("DECIDE", "[Finish]", exact, segments),
                    ],
                )
            )

            key += "-general"
            if i % 2:
                supplied = f"REF-{number}"
                question = f"Repeat this supplied reference: {supplied}."
            else:
                supplied = str(number + i + 2)
                question = f"What is {number} plus {i + 2}?"
            samples.append(
                Sample(
                    sample_id=key,
                    source_group_id=key,
                    split=split,
                    protocol_version=VERSION,
                    question=question,
                    query_type="GENERAL",
                    retrieval_policy="eligible",
                    source_anchors=[],
                    label_origin=CORPUS,
                    human_verified=False,
                    episodes=[
                        episode("DECIDE", "[NoRetrieve]"),
                        episode("CANDIDATE", supplied + "[EndSegment][IsUSE:5]"),
                        episode("DECIDE", "[Finish]", segments=[supplied]),
                    ],
                )
            )
    return sources, samples


def export(path):
    write_corpus(
        path,
        *create_corpus(),
        dataset=CORPUS,
        generation_method=(
            "split-specific settlement layouts, exact field rules, and controlled negatives"
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    export(parser.parse_args().output)


if __name__ == "__main__":
    main()
