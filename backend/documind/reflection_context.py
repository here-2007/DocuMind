"""Exact generation-token budgets and explicit, source-preserving evidence windows."""

from functools import lru_cache

from documind.errors import AppError
from documind.evidence import field_question, field_spans
from documind.reflection import MAX_SEGMENT, digest, prompt_ids, register_tokens
from documind.reflection_model import SMOKE, TARGET


@lru_cache(maxsize=4)
def _tokenizer(model, revision, template_sha, ids, vocab_size, native_ids):
    from transformers import AutoTokenizer

    if (model, revision) not in {TARGET, SMOKE}:
        raise ValueError("Unsupported generation tokenizer")
    tokenizer = AutoTokenizer.from_pretrained(model, revision=revision, local_files_only=True)
    if (
        register_tokens(tokenizer) != dict(ids)
        or len(tokenizer) != vocab_size
        or digest(tokenizer.chat_template.encode()) != template_sha
        or set(tokenizer.all_special_ids) - set(dict(ids).values()) != set(native_ids)
    ):
        raise ValueError("Generation tokenizer differs from the admitted runtime")
    return tokenizer


def tokenizer_for(cap):
    try:
        return _tokenizer(
            cap.model_id,
            cap.model_revision,
            cap.chat_template_sha256,
            tuple(sorted(cap.token_ids.items())),
            cap.vocab_size,
            tuple(cap.native_special_ids),
        )
    except (ImportError, OSError, ValueError) as exc:
        raise AppError("RUNTIME_TOKENIZER_NOT_READY", 409) from exc


def reserve(state, budget):
    return min(budget, 1 if state == "DECIDE" else MAX_SEGMENT + 4)


def input_tokens(tokenizer, cap, state, context):
    return len(prompt_ids(tokenizer, cap.token_ids, state, context))


def candidate_fits(tokenizer, cap, question, evidence):
    context = {"question": question, "segments": [], "evidence": evidence}
    return (
        input_tokens(tokenizer, cap, "CANDIDATE", context) + reserve("CANDIDATE", 2048)
        <= cap.context_limit
    )


def evidence_window(tokenizer, cap, question, anchor):
    def fits(source):
        return candidate_fits(tokenizer, cap, question, [source])

    if fits(anchor):
        return anchor
    field, _ = field_question(question)
    if field is None:
        return None
    spans = list(field_spans({"text_raw": anchor["quote"]}, field))
    if not spans:
        return None
    _, start, end, _ = spans[0]

    def window(radius):
        left, right = max(0, start - radius), min(len(anchor["quote"]), end + radius)
        return {
            **anchor,
            "quote": anchor["quote"][left:right],
            "start_char": anchor["start_char"] + left,
            "end_char": anchor["start_char"] + right,
        }

    result = window(0)
    if not fits(result):
        return None
    low, high = 0, len(anchor["quote"])
    while low + 1 < high:
        middle = (low + high) // 2
        candidate = window(middle)
        if fits(candidate):
            low, result = middle, candidate
        else:
            high = middle
    return result


def candidate_evidence(tokenizer, cap, question, anchor, element):
    window = evidence_window(tokenizer, cap, question, anchor)
    fallback = ([window], window) if window else ([], None)
    field, invoice = field_question(question)
    if cap.candidate_evidence_limit < 2 or not invoice or element["element_type"] != "paragraph":
        return fallback
    identities = list(field_spans(element, "invoice_number"))
    if len(identities) != 1:
        return fallback
    value, start, end, quote = identities[0]
    if (
        not value
        or value.casefold() != invoice
        or (window and window["start_char"] <= start < end <= window["end_char"])
    ):
        return fallback
    fields = [
        span
        for span in field_spans(element, field)
        if anchor["start_char"] <= span[1] < span[2] <= anchor["end_char"]
    ]
    if len(fields) != 1:
        return fallback
    value, left, right, field_quote = fields[0]
    if value is None or not (end <= left or right <= start):
        return fallback
    identity = {
        **anchor,
        "source_id": str(element["id"]),
        "start_char": start,
        "end_char": end,
        "quote": quote,
    }
    target = {**anchor, "start_char": left, "end_char": right, "quote": field_quote}
    if not candidate_fits(tokenizer, cap, question, [identity, target]):
        return fallback
    return [identity, target], target
