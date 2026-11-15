"""Application-owned reflection policy and conservative candidate selection."""

import re

from documind.runtime_protocol import MAX_TOKENS

ADDITION = re.compile(r"what is ([0-9]{1,6}) plus ([0-9]{1,6})\?", re.I)
REFERENCE = re.compile(r"repeat this supplied reference: ([A-Za-z0-9-]{1,64})\.", re.I | re.ASCII)
MAX_RETRIEVALS = 3
MAX_SEGMENTS = 8


def eligible(question):
    return bool(ADDITION.fullmatch(question.strip()) or REFERENCE.fullmatch(question.strip()))


def addition_answer(question):
    match = ADDITION.fullmatch(question.strip())
    return str(int(match[1]) + int(match[2])) if match else None


def critic_score(probabilities, *, use_support=True):
    relevance = probabilities["relevance"]["[IsREL:Yes]"]
    support = probabilities["support"]
    utility = sum((i - 1) / 4 * probabilities["utility"][f"[IsUSE:{i}]"] for i in range(1, 6))
    support_score = (
        support["[IsSUP:Fully]"] + 0.5 * support["[IsSUP:Partially]"] if use_support else 0
    )
    return relevance + support_score + 0.5 * utility


def run(
    question, required, step, retrieve, verify, trace, *, use_support=True, token_budget=MAX_TOKENS
):
    evidence, committed, segments, citations = [], {}, [], []
    seen = set()
    budget = token_budget
    retrievals = 0
    expected_sum = addition_answer(question) if not required else None
    reference = REFERENCE.fullmatch(question.strip()) if not required else None

    def model_step(state, sources):
        nonlocal budget
        context = {"question": question, "segments": segments, "evidence": sources}
        result, labels = step(state, context, budget)
        budget -= result.output_tokens
        return result, labels

    while len(segments) < MAX_SEGMENTS and budget:
        result, labels = model_step("DECIDE", list(committed.values()))
        if result.finish_reason != "complete":
            trace("warning", reason=result.finish_reason)
            break
        requested = labels["decision"]
        effective = requested
        reason = None
        if required and not retrievals and requested != "[Retrieve]":
            effective, reason = "[Retrieve]", "required_evidence"
        elif required and requested == "[NoRetrieve]":
            effective, reason = "[Continue]", "reuse_required_evidence"
        elif expected_sum is not None and not segments and requested == "[Finish]":
            effective, reason = "[NoRetrieve]", "answerable_addition"
        trace(
            "reflection_token",
            requested_model_action=requested,
            effective_action=effective,
            policy_override_reason=reason,
            retrieval_trigger=("policy" if reason else "model")
            if effective == "[Retrieve]"
            else None,
            probabilities=result.probabilities,
        )

        if effective == "[Finish]":
            break
        if effective == "[Retrieve]":
            if not required:
                trace("warning", reason="needs_scope")
                break
            if retrievals >= MAX_RETRIEVALS:
                break
            if retrievals:
                trace("warning", reason="retrieval_cycle")
                break
            retrievals += 1
            evidence = retrieve()[:5]
            if not evidence:
                trace("warning", reason="empty_retrieval")
                break
        if effective == "[Continue]" and not evidence:
            trace("warning", reason="no_evidence_to_reuse")
            break

        choices = []
        for rank, (visible, source) in enumerate(
            evidence if effective != "[NoRetrieve]" else [([], None)]
        ):
            if not budget:
                break
            result, labels = model_step("CANDIDATE", visible)
            source_id = source["source_id"] if source else None
            if result.finish_reason != "complete" or labels.get("relevance") == "[IsREL:No]":
                trace(
                    "passage_filtered",
                    source_id=source_id,
                    decision=labels.get("relevance", result.finish_reason),
                )
                continue
            if expected_sum is not None and result.text.strip() != expected_sum:
                trace("passage_filtered", source_id=None, decision="incorrect_arithmetic")
                continue
            if reference and result.text.strip() != reference[1]:
                trace("passage_filtered", source_id=None, decision="incorrect_reference")
                continue
            citation = None
            score = 0.0
            if source:
                if use_support and labels["support"] != "[IsSUP:Fully]":
                    trace("passage_filtered", source_id=source_id, decision=labels["support"])
                    continue
                citation = verify(result.text, source)
                trace(
                    "citation_verified",
                    source_id=source_id,
                    decision="verified" if citation else "unsupported",
                )
                if not citation:
                    continue
                score = critic_score(result.probabilities, use_support=use_support)
            trace(
                "candidate_scored",
                source_id=source_id,
                labels=labels,
                probabilities=result.probabilities,
                score=score if source else None,
            )
            choices.append((-score, rank, source_id or "", result.text.strip(), citation, source))
        if not choices:
            break
        _, _, source_id, text, citation, source = min(choices, key=lambda row: row[:3])
        key = text, source_id
        if key in seen:
            trace("warning", reason="segment_cycle")
            break
        seen.add(key)
        segments.append(text)
        if source:
            committed[source_id] = source
        if citation:
            citations.append(citation)

    return {"segments": segments, "citations": citations, "retrieval_calls": retrievals}
