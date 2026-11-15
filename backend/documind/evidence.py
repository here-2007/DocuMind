"""Evidence-v1 supports only explicitly labelled fields on one resolved source."""

import re
from datetime import date
from decimal import Decimal

CURRENCIES = "USD|EUR|INR|GBP|CAD|AUD|JPY|CHF|SGD|AED|NZD|CNY"
MONEY = r"-?(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,6})?"
IDENTIFIER = r"[A-Za-z0-9][A-Za-z0-9./_-]{0,63}"
FIELD_LABELS = {
    "total": r"(?:grand total|invoice total|total)",
    "amount_due": r"(?:amount due|balance due)",
    "invoice_number": r"(?:invoice (?:number|no\.?|id)|invoice #)",
    "po_number": r"(?:purchase order (?:number|no\.?|id)|po (?:number|no\.?|id))",
    "invoice_date": r"(?:invoice date|issue date)",
    "due_date": r"(?:due date|payment due date)",
    "currency": r"currency",
}
TABLE_LABEL = re.compile(
    r"^[ \t]*(?:" + "|".join(FIELD_LABELS.values()) + r")(?=[ \t:=]|$)", re.I | re.M
)
QUESTIONS = {
    "total": (
        r"(?:what is the (?:invoice )?(?:grand )?total|what is the total amount"
        r"|what is the full billed amount)"
    ),
    "amount_due": r"(?:what is the (?:amount|balance) due|how much is due)",
    "invoice_number": r"what is the invoice (?:number|id)",
    "po_number": r"what is the (?:purchase order|po) (?:number|id)",
    "invoice_date": r"(?:what is the (?:invoice|issue) date|when was the invoice issued)",
    "due_date": r"(?:what is the (?:payment )?due date|when is payment due)",
    "currency": r"what (?:is the currency|currency is used)",
}


def field_question(question):
    question = " ".join(question.strip().split()).removesuffix("?")
    paraphrase = re.fullmatch(
        rf"for invoice ({IDENTIFIER}), give the (total|currency|due date)\.", question, re.I
    )
    if paraphrase:
        field = paraphrase[2].casefold().replace(" ", "_")
        return field, paraphrase[1].casefold()
    for field, pattern in QUESTIONS.items():
        match = re.fullmatch(
            rf"{pattern}(?: (?:on|for|of) (?:invoice )?({IDENTIFIER}))?",
            question,
            re.I,
        )
        if match:
            return field, match[1].casefold() if match[1] else None
    return None, None


def field_spans(element, field):
    raw = element["text_raw"]
    value_pattern = {
        "total": rf"({CURRENCIES})[ \t]+({MONEY})",
        "amount_due": rf"({CURRENCIES})[ \t]+({MONEY})",
        "invoice_number": f"({IDENTIFIER})",
        "po_number": f"({IDENTIFIER})",
        "invoice_date": r"([0-9]{4}-[0-9]{2}-[0-9]{2})",
        "due_date": r"([0-9]{4}-[0-9]{2}-[0-9]{2})",
        "currency": rf"({CURRENCIES})",
    }[field]
    label = FIELD_LABELS[field]
    pattern = rf"^[ \t]*({label}[ \t]*[:=][ \t]*([^\r\n]*?))[ \t]*\r?$"
    for match in re.finditer(pattern, raw, re.I | re.M):
        parsed = re.fullmatch(value_pattern, match[2], re.I)
        value = parsed[1] if parsed else None
        if parsed and field in {"total", "amount_due"}:
            amount = parsed[2].replace(",", "")
            value = (
                (value.upper(), Decimal(amount))
                if len(amount.lstrip("-").split(".", 1)[0]) <= 14
                else None
            )
        if parsed and field in {"invoice_date", "due_date"}:
            try:
                value = date.fromisoformat(value)
            except ValueError:
                value = None
        yield value, match.start(1), match.end(1), match[1]


def verified_candidate(question, document_ids, elements, passages):
    field, invoice = field_question(question)
    if field is None:
        return None, "unsupported_question"
    table_documents = {
        row["document_id"]
        for row in elements
        if row["element_type"] == "table_cell" and TABLE_LABEL.search(row["text_raw"])
    }
    # A label and its value can be in different cells, even after extraction review.
    elements = [row for row in elements if row["element_type"] == "paragraph"]
    eligible = set(document_ids)
    identities = {}
    for row in elements:
        if row["document_id"] in eligible:
            ids = identities.setdefault(row["document_id"], set())
            ids.update(
                value.casefold() if value else None
                for value, *_ in field_spans(row, "invoice_number")
            )
    if invoice is not None:
        matches = {doc for doc, ids in identities.items() if invoice in ids}
        eligible &= matches
    if len(eligible) != 1:
        return None, "ambiguous_document"
    if eligible & table_documents:
        return None, "unsupported_table_fields"
    ids = identities.get(next(iter(eligible)), set())
    if len(ids) > 1 or None in ids:
        return None, "ambiguous_document"
    sources = [
        (row, value, a, b, quote)
        for row in elements
        if row["document_id"] in eligible
        for value, a, b, quote in field_spans(row, field)
    ]
    values = {item[1] for item in sources}
    if len(values) != 1 or None in values:
        return None, "missing_or_conflicting_field"
    for passage in passages:
        for row, _, start, end, quote in sources:
            if (
                passage["element_id"] == row["id"]
                and passage["start_char"] <= start
                and end <= passage["end_char"]
                and passage["page_id"] == row["page_id"]
                and passage["ingestion_version"] == row["ingestion_version"]
                and passage["document_id"] == row["document_id"]
            ):
                return {
                    "chunk_id": str(passage["id"]),
                    "element_id": str(row["id"]),
                    "document_id": str(row["document_id"]),
                    "page_number": row["page_number"],
                    "ingestion_version": row["ingestion_version"],
                    "start_char": start,
                    "end_char": end,
                    "quote": quote,
                    "verification_status": "supported",
                }, "verified"
    return None, "field_not_in_retrieved_evidence"
