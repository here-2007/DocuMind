"""Export paired synthetic records with opaque anchors and critic counterexamples."""

import argparse
import random
from datetime import date, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from documind.reflection import VERSION, digest
from documind.reflection_data import Anchor, Sample, Source, write_corpus
from documind.reflection_pilot import episode

CORPUS = "documind-reflection-synthetic-v3"
GROUPS = {"train": 32, "validation": 8, "test": 12}
LAYOUTS = {
    "train": (
        "Invoice number: {ref}\nGrand total: {total}\nDue date: {due}\nCurrency: {currency}",
        "Accounts payable entry\nCurrency: {currency}\nInvoice number: {ref}\n"
        "Due date: {due}\nGrand total: {total}",
        "Invoice number: {ref}\nDue date: {due}\nGrand total: {total}\n"
        "Currency: {currency}\nEnd of invoice.",
    ),
    "validation": (
        "Supplier ledger\nDue date: {due}\nInvoice number: {ref}\nCurrency: {currency}\n"
        "Grand total: {total}\nApproved entry.",
    ),
    "test": (
        "Dispatch register\nCurrency: {currency}\nGrand total: {total}\nInvoice number: {ref}\n"
        "Due date: {due}\nRecord closed.",
    ),
}


def create_corpus(dataset=CORPUS, layouts=LAYOUTS, *, seed="reflection-v3"):
    def opaque(value):
        return str(uuid5(NAMESPACE_URL, dataset + "/" + value))

    sources, samples = [], []
    for split, count in GROUPS.items():
        rng = random.Random(seed + "/" + split)
        for index in range(count):
            group = opaque(f"{split}/{index}")
            ref = f"DM-{rng.randrange(100000, 999999)}"
            field = ("total", "due date", "currency")[index % 3]
            currency_index = rng.randrange(4)
            day = rng.randrange(349)
            for variant in range(2):
                currency = ("USD", "EUR", "INR", "GBP")[(currency_index + variant) % 4]
                total = f"{currency} {rng.randrange(100, 99999)}.{rng.randrange(100):02}"
                due = (date(2027, 1, 1) + timedelta(days=day + variant)).isoformat()
                answer = {"total": total, "due date": due, "currency": currency}[field]
                wrong = {
                    "total": f"{currency} {rng.randrange(100000, 999999)}.99",
                    "due date": (date.fromisoformat(due) + timedelta(days=1)).isoformat(),
                    "currency": "JPY",
                }[field]
                layout = layouts[split][rng.randrange(len(layouts[split]))]
                text = layout.format(ref=ref, total=total, due=due, currency=currency)
                unrelated = layout.format(ref=ref + "X", total=total, due=due, currency=currency)
                key = f"{split}/{index}/{variant}"
                # Identical evidence can receive different critic labels for different candidates.
                records = [
                    (text, f"[IsREL:Yes]{answer}[EndSegment][IsSUP:Fully][IsUSE:5]"),
                    (text, f"[IsREL:Yes]{wrong}[EndSegment][IsSUP:No][IsUSE:1]"),
                    (unrelated, "[IsREL:No]"),
                ]
                rng.shuffle(records)
                anchors, candidates = [], []
                exact = None
                for ordinal, (quote, target) in enumerate(records):
                    source_id = opaque(f"{key}/{ordinal}")
                    sources.append(
                        Source(
                            source_id=source_id,
                            source_group_id=group,
                            template_id=f"{split}/layout/{layouts[split].index(layout)}",
                            text=quote,
                        )
                    )
                    anchors.append(
                        Anchor(
                            source_id=source_id,
                            sha256=digest(quote.encode()),
                            page_number=1,
                            start_char=0,
                            end_char=len(quote),
                            quote=quote,
                        )
                    )
                    candidates.append(episode("CANDIDATE", target, [source_id]))
                    if "[IsSUP:Fully]" in target:
                        exact = source_id
                question = (
                    f"What is the {field} on invoice {ref}?"
                    if variant == 0
                    else f"For invoice {ref}, give the {field}."
                )
                samples.append(
                    Sample(
                        sample_id=opaque(key),
                        source_group_id=group,
                        split=split,
                        protocol_version=VERSION,
                        question=question,
                        query_type="DOCUMENT_QA",
                        retrieval_policy="required",
                        source_anchors=anchors,
                        label_origin=dataset,
                        human_verified=False,
                        episodes=[
                            episode("DECIDE", "[Retrieve]"),
                            *candidates,
                            episode("DECIDE", "[Finish]", [exact], [answer]),
                        ],
                    )
                )
            reference = f"SUP-{rng.randrange(10000000, 99999999)}"
            samples.append(
                Sample(
                    sample_id=opaque(f"{split}/{index}/general"),
                    source_group_id=group,
                    split=split,
                    protocol_version=VERSION,
                    question=f"Repeat this supplied reference: {reference}.",
                    query_type="GENERAL",
                    retrieval_policy="eligible",
                    source_anchors=[],
                    label_origin=dataset,
                    human_verified=False,
                    episodes=[
                        episode("DECIDE", "[NoRetrieve]"),
                        episode("CANDIDATE", reference + "[EndSegment][IsUSE:5]"),
                        episode("DECIDE", "[Finish]", segments=[reference]),
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
            "paired changed-value records, opaque IDs, distinct split layouts "
            "and corrupted candidate text"
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    export(parser.parse_args().output)
