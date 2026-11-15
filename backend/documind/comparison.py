"""Versioned extraction and exact arithmetic for the first comparison rule."""

import re
import unicodedata
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
from uuid import UUID

from documind.evidence import CURRENCIES, IDENTIFIER

RULE = "grand_total_match_v1"
MODE = "deterministic_comparison"
AMOUNT = re.compile(
    rf"(?:(?P<prefix>{CURRENCIES})[ \t]+)?"
    r"(?P<number>-?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+|[0-9]{1,2}(?:,[0-9]{2})+,[0-9]{3})"
    rf"(?:\.[0-9]{{1,6}})?)(?:[ \t]+(?P<suffix>{CURRENCIES}))?",
    re.I,
)
BASIS = r"tax[- ](?:inclusive|exclusive)"
IDENTITY = re.compile(IDENTIFIER, re.ASCII)
FIELDS = {
    "kind": r"document type",
    "invoice_number": r"invoice (?:number|no\.?|id|#)",
    "po_number": r"(?:purchase order|po) (?:number|no\.?|id|reference|ref\.?)",
    "vendor_id": r"(?:vendor|supplier) (?:id|identifier|number)",
    "currency": r"currency",
    "total_basis": r"(?:grand total|total) basis",
    "grand_total": r"grand total(?:[ \t]*\((?P<basis>[^()]*)\))?",
}
LABEL = re.compile(
    r"(?:" + "|".join(f"(?P<{key}>{label})" for key, label in FIELDS.items()) + r")"
    r"[ \t]*[:=][ \t]*(?P<value>.*)",
    re.I,
)
TABLE_LABEL = re.compile(r"(?:" + "|".join(FIELDS.values()) + r")[ \t]*(?:[:=].*)?", re.I)
QUALIFIER = re.compile(
    r"\b(?:amend(?:ed|ment|ments)|supersed(?:es|ed)|credit (?:note|memo|applied))\b"
    r"|^[ \t]*(?:credit|amendments?|revision)[ \t]*[:=]",
    re.I | re.M,
)


@dataclass(frozen=True)
class Anchor:
    page_id: UUID
    element_id: UUID
    start_char: int
    end_char: int
    quote: str


@dataclass
class Record:
    kind: str | None = None
    invoice_number: str | None = None
    po_number: str | None = None
    vendor_id: str | None = None
    currency: str | None = None
    total_basis: str | None = None
    grand_total: Decimal | None = None
    issue: str | None = None
    evidence: dict[str, Anchor] = field(default_factory=dict)


def document_kind(value):
    value = " ".join(value.casefold().split())
    return {"invoice": "invoice", "purchase order": "purchase_order"}.get(value)


def total_basis(value):
    value = value.strip().lower()
    return value.replace(" ", "-") if re.fullmatch(BASIS, value) else None


def money(value, separate_currency=None):
    match = AMOUNT.fullmatch(value)
    if match is None:
        return None, None
    codes = {code.upper() for code in (match["prefix"], match["suffix"], separate_currency) if code}
    if len(codes) != 1:
        return None, None
    currency = next(iter(codes))
    number = match["number"]
    integer = number.lstrip("-").partition(".")[0]
    if "," in integer and any(len(part) != 3 for part in integer.split(",")[1:]):
        if currency != "INR":
            return None, None
    if len(integer.replace(",", "").lstrip("0")) > 14:
        return None, None
    return Decimal(number.replace(",", "")), currency


def has_comparison_table_label(raw):
    return any(TABLE_LABEL.fullmatch(line.strip()) for line in raw.splitlines())


def extract_record(status, elements):
    if status != "complete":
        return Record(issue="SOURCE_REVIEW_REQUIRED")
    candidates = {}
    qualifier = False
    for element in elements:
        raw = element["text_raw"]
        if element["element_type"] == "table_cell" and has_comparison_table_label(raw):
            return Record(issue="COMPARISON_TABLE_FIELDS_UNSUPPORTED")
        qualifier |= bool(QUALIFIER.search(raw))
        if element["element_type"] != "paragraph":
            continue
        for line in re.finditer(r"[^\r\n]+", raw):
            quote = line[0].strip(" \t")
            start = line.start() + len(line[0]) - len(line[0].lstrip(" \t"))
            anchor = Anchor(element["page_id"], element["id"], start, start + len(quote), quote)
            match = LABEL.fullmatch(quote)
            if match:
                name = next(key for key in FIELDS if match[key] is not None)
                candidates.setdefault(name, []).append((match["value"], match["basis"], anchor))
            elif document_kind(quote):
                candidates.setdefault("kind", []).append((quote, None, anchor))

    record = Record(issue="AMENDMENT_OR_CREDIT" if qualifier else None)
    for name, parse in (
        ("kind", document_kind),
        ("invoice_number", normalize_id),
        ("po_number", normalize_id),
        ("vendor_id", normalize_id),
        ("currency", currency_code),
        ("total_basis", total_basis),
    ):
        matches = candidates.get(name, [])
        values = {parse(value) for value, _, _ in matches}
        if len(values) == 1 and None not in values and (len(matches) == 1 or name == "kind"):
            value = next(iter(values))
            setattr(record, name, value)
            record.evidence[name] = matches[0][2]

    if "kind" not in candidates and "invoice_number" in candidates:
        record.kind = "invoice"
    if "invoice_number" in candidates and record.invoice_number is None:
        record.issue = "INVOICE_IDENTIFIER_UNRESOLVED"
    totals = candidates.get("grand_total", [])
    if len(totals) != 1:
        return record
    value, inline_basis, anchor = totals[0]
    if "currency" not in candidates or record.currency:
        amount, currency = money(value, record.currency)
        if amount is not None:
            record.grand_total, record.currency = amount, currency
            record.evidence["grand_total"] = anchor
            record.evidence.setdefault("currency", anchor)
    if inline_basis is not None:
        basis = total_basis(inline_basis)
        if basis and "total_basis" not in candidates:
            record.total_basis = basis
            record.evidence["total_basis"] = anchor
        elif record.total_basis != basis:
            record.total_basis = None
            record.evidence.pop("total_basis", None)
    return record


def normalize_id(value):
    value = unicodedata.normalize("NFC", value.strip())
    return value if IDENTITY.fullmatch(value) else None


def currency_code(value):
    return value.upper() if re.fullmatch(CURRENCIES, value, re.I) else None


def supports_field(field, quote, record):
    match = LABEL.fullmatch(quote)
    if match is None:
        return False
    value = match["value"]
    if match["grand_total"] is not None:
        if field == "grand_total":
            return money(value, record["currency"]) == (record["grand_total"], record["currency"])
        if field == "currency":
            return money(value)[1] == record["currency"]
        return field == "total_basis" and total_basis(match["basis"] or "") == record[field]
    if match[field] is None:
        return False
    parse = {"currency": currency_code, "total_basis": total_basis}.get(field, normalize_id)
    return parse(value) == record[field]


@dataclass(frozen=True)
class Decision:
    type: str
    reason: str | None
    invoice: UUID | None = None
    purchase_order: UUID | None = None
    difference: Decimal | None = None

    @property
    def status(self):
        return "verified" if self.difference is not None else "needs_review"


def compare_records(records):
    if any(row["issue"] == "SOURCE_REVIEW_REQUIRED" for row in records):
        return Decision("UNVERIFIED_MATCH", "SOURCE_REVIEW_REQUIRED")
    if any(row["issue"] == "COMPARISON_TABLE_FIELDS_UNSUPPORTED" for row in records):
        return Decision("UNVERIFIED_TOTAL", "COMPARISON_TABLE_FIELDS_UNSUPPORTED")
    invoices = [row for row in records if row["kind"] == "invoice"]
    orders = [row for row in records if row["kind"] == "purchase_order"]
    if len(records) != 2 or len(invoices) != 1 or len(orders) != 1:
        return Decision("UNVERIFIED_MATCH", "DOCUMENT_ROLES_AMBIGUOUS")
    invoice, order = invoices[0], orders[0]
    if (
        order["invoice_number"]
        or any(row["issue"] == "INVOICE_IDENTIFIER_UNRESOLVED" for row in (invoice, order))
        or any(not row[key] for row in (invoice, order) for key in ("po_number", "vendor_id"))
    ):
        return Decision("UNVERIFIED_MATCH", "IDENTIFIERS_MISSING_OR_AMBIGUOUS")
    if any(invoice[key] != order[key] for key in ("po_number", "vendor_id")):
        return Decision("UNVERIFIED_MATCH", "IDENTIFIERS_DO_NOT_MATCH")
    if any(row["issue"] for row in (invoice, order)):
        return Decision("UNVERIFIED_TOTAL", "AMENDMENT_OR_CREDIT")
    if any(row["grand_total"] is None or row["currency"] is None for row in (invoice, order)):
        return Decision("UNVERIFIED_TOTAL", "TOTAL_OR_CURRENCY_UNRESOLVED")
    if invoice["currency"] != order["currency"]:
        return Decision("UNVERIFIED_TOTAL", "CURRENCY_MISMATCH")
    if not invoice["total_basis"] or invoice["total_basis"] != order["total_basis"]:
        return Decision("UNVERIFIED_TOTAL", "TOTAL_BASIS_UNRESOLVED")
    with localcontext() as context:
        context.prec = 21
        difference = invoice["grand_total"] - order["grand_total"]
    return Decision(
        "AMOUNT_MISMATCH" if difference else "AMOUNT_MATCH",
        None,
        invoice["document_id"],
        order["document_id"],
        difference,
    )


def decimal_string(value):
    integer, _, fraction = format(value, "f").partition(".")
    return integer + "." + fraction.rstrip("0").ljust(2, "0")
