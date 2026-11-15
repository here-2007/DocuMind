"""Build the post-training synthetic Phase 8 benchmark without model output."""

import argparse
import json
from datetime import date, timedelta
from pathlib import Path
from random import Random

from documind.evidence import field_question, field_spans
from documind.reflection import canonical, target_parts
from documind.reflection_data import load_corpus

SEED = 868267
MANIFEST = Path("evaluation/datasets/phase8-v1.jsonl")
V4_CORPUS_SHA256 = "0f3864f7040332dbeefd6d5bdd7dc82685bcadf513d74c67617e897f13321beb"


def build():
    rng = Random(SEED)
    yield {
        "kind": "metadata",
        "dataset": "phase8-v1",
        "seed": SEED,
        "split": "test",
        "source": "project-owned synthetic PDFs created after checkpoint selection",
        "human_adjudication": False,
    }
    for number in range(40):
        group = f"P8-{number + 1:03}"
        main, neighbor = f"{group}-invoice", f"{group}-neighbor"
        identity = f"EV8-{rng.randrange(100000, 999999)}-{number + 1:02}"
        near_identity = identity + "/R"
        currency = ("CHF", "JPY", "AED", "GBP", "CAD", "EUR", "INR", "USD")[number % 8]
        total = f"{currency} {rng.randrange(250, 99999):,}.{rng.randrange(100):02}"
        due = str(date(2027, 1, 1) + timedelta(days=rng.randrange(300)))
        issued = str(date(2026, 9, 1) + timedelta(days=number))
        balance = f"{currency} {rng.randrange(12, 200)}.50"
        label, amount = [("Invoice total", total), ("Grand total", total), ("Total", total)][
            number % 3
        ]
        keep_due = number % 5 != 0
        fields = [
            ("Invoice number", identity),
            ("Currency", currency),
            (label, amount),
            ("Invoice date", issued),
            ("Balance due", balance),
        ]
        if keep_due:
            fields.append(("Payment due date", due))
        # Split identity and payment fields on some records, with believable but non-answer totals.
        title = (
            "Settlement statement",
            "Wholesale invoice",
            "Commercial billing advice",
            "Service payment notice",
            "Dispatch account",
            "Monthly charge summary",
        )[number % 6]
        town = ("Pune", "Porto", "Osaka", "Halifax", "Bristol")[number % 5]
        lines = [f"{name}: {value}" for name, value in fields]
        notes = [
            f"{title} / {town}",
            "Accounts payable has recorded receipt of the billing notice.",
        ]
        identity_page = 1
        field_page = 1
        if number % 4 == 0:
            field_page = 2
            pages = [
                "\n".join([*notes, lines[0], "Details of the billed services follow."]),
                "\n".join(["Account settlement figures", *lines[1:], "Tax is included."]),
            ]
        elif number % 4 == 1:
            pages = ["\n".join([*notes, *reversed(lines), "Archive: finance copy."])]
        elif number % 4 == 2:
            pages = ["\n\n".join(["\n".join(notes), "\n".join(lines[:3]), "\n".join(lines[3:])])]
        else:
            pages = [
                "\n".join([*notes, *lines, "Please quote the invoice number in correspondence."])
            ]
        yield {"kind": "document", "id": main, "group": group, "pages": pages}
        distraction = (
            f"{title} / Revised charge schedule\nInvoice number: {near_identity}\n"
            f"Currency: EUR\nGrand total: EUR {rng.randrange(1000, 9000)}.99\n"
            "Invoice date: 2026-10-15\nBalance due: EUR 293.10\nPayment due date: 2027-02-02"
        )
        yield {"kind": "document", "id": neighbor, "group": group, "pages": [distraction]}

        def case(
            index,
            category,
            question,
            scope,
            value=None,
            quote=None,
            page=None,
            *,
            group=group,
            main=main,
            field_page=field_page,
        ):
            return {
                "kind": "case",
                "id": f"{group}-{index}",
                "group": group,
                "category": category,
                "question": question,
                "scope": scope,
                "gold_value": value,
                "gold": {"document": main, "page": page or field_page, "quote": quote}
                if quote
                else None,
                "retrieval_policy": "required" if scope else "eligible",
            }

        yield case(
            1,
            "invoice_total",
            f"What is the total amount for invoice {identity}?",
            [main],
            total,
            f"{label}: {total}",
        )
        choice = number % 4
        secondary = [
            ("What is the balance due?", balance, f"Balance due: {balance}", field_page),
            ("When was the invoice issued?", issued, f"Invoice date: {issued}", field_page),
            ("What currency is used?", currency, f"Currency: {currency}", field_page),
            ("What is the invoice number?", identity, f"Invoice number: {identity}", identity_page),
        ][choice]
        question, value, quote, page = secondary
        yield case(2, "secondary_field", question, [main], value, quote, page)
        if keep_due:
            yield case(
                3,
                "cross_document_scope",
                f"When is payment due for invoice {identity}?",
                [main, neighbor],
                due,
                f"Payment due date: {due}",
            )
            yield case(4, "ambiguous_scope", "What is the invoice total?", [main, neighbor])
        else:
            yield case(
                3,
                "cross_document_scope",
                f"What currency is used for invoice {identity}?",
                [main, neighbor],
                currency,
                f"Currency: {currency}",
            )
            yield case(4, "missing_field", "What is the due date?", [main])
        if number % 2:
            left, right = rng.randrange(25, 500), rng.randrange(25, 500)
            yield case(
                5, "eligible_addition", f"What is {left} plus {right}?", [], str(left + right)
            )
        else:
            reference = f"P8Q-{rng.randrange(100000, 999999)}-{number + 1}"
            yield case(
                5,
                "eligible_reference",
                f"Repeat this supplied reference: {reference}.",
                [],
                reference,
            )


def pretraining_holdout(corpus):
    samples, corpus_sha = load_corpus(corpus)
    if corpus_sha != V4_CORPUS_SHA256:
        raise ValueError("Expected the previously frozen v4 corpus")
    yield {
        "kind": "metadata",
        "dataset": "phase8-v4-pretraining",
        "split": "test",
        "source": "v4 pre-training test split; previously used for Phase 6 validation",
        "corpus_sha256": corpus_sha,
        "human_adjudication": False,
    }
    seen = set()
    for sample in samples:
        if sample.split != "test":
            continue
        required = sample.retrieval_policy == "required"
        episode = next(
            ep
            for ep in sample.episodes
            if ep.state == "CANDIDATE" and (not required or "[IsSUP:Fully]" in ep.target)
        )
        value = target_parts("CANDIDATE", required, episode.target)[int(required)]
        scope, gold = [], None
        if required:
            anchor = next(
                anchor
                for anchor in sample.source_anchors
                if anchor.source_id == episode.evidence[0]
            )
            doc = anchor.source_id
            if doc not in seen:
                yield {
                    "kind": "document",
                    "id": doc,
                    "group": sample.source_group_id,
                    "pages": [anchor.quote],
                }
                seen.add(doc)
            field, _ = field_question(sample.question)
            quote = next(field_spans({"text_raw": anchor.quote}, field))[3]
            scope, gold = [doc], {"document": doc, "page": 1, "quote": quote}
        yield {
            "kind": "case",
            "id": sample.sample_id,
            "group": sample.source_group_id,
            "category": "frozen_document" if required else "frozen_general",
            "question": sample.question,
            "scope": scope,
            "gold_value": value,
            "gold": gold,
            "retrieval_policy": sample.retrieval_policy,
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=MANIFEST)
    parser.add_argument("--pretraining-corpus", type=Path)
    args = parser.parse_args()
    with args.output.open("x", encoding="utf-8") as stream:
        rows = pretraining_holdout(args.pretraining_corpus) if args.pretraining_corpus else build()
        for row in rows:
            stream.write(canonical(row) + "\n")


def load(path):
    metadata, *rows = [json.loads(line) for line in path.read_text().splitlines()]
    documents = {row["id"]: row for row in rows if row["kind"] == "document"}
    cases = [row for row in rows if row["kind"] == "case"]
    if metadata["kind"] != "metadata" or len(documents) + len(cases) != len(rows):
        raise ValueError("Invalid dataset rows")
    if len({row["id"] for row in cases}) != len(cases):
        raise ValueError("Duplicate case ID")
    for row in cases:
        if len(set(row["scope"])) != len(row["scope"]) or any(
            documents[doc]["group"] != row["group"] for doc in row["scope"]
        ):
            raise ValueError("A case crosses document groups or repeats a source")
        if (row["retrieval_policy"] == "required") != bool(row["scope"]):
            raise ValueError("Inconsistent retrieval policy")
        if (gold := row["gold"]) is not None:
            if (
                gold["page"] < 1
                or gold["document"] not in row["scope"]
                or gold["quote"]
                not in documents[gold["document"]]["pages"][gold["page"] - 1].splitlines()
            ):
                raise ValueError("Gold span is outside the declared source")
    return metadata, documents, cases


if __name__ == "__main__":
    main()
