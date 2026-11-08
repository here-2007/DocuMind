# Self-RAG training and inference contract

## Status and provenance

The **source PDF** (pp. 1-4, 7) specifies fine-tuning `meta-llama/Llama-3.2-3B-Instruct` with 4-bit QLoRA and reflection families `[Retrieve]`, `[IsREL]`, `[IsSUP]`, `[IsUSE]`. It requires a runtime loop that stops on retrieval decisions, queries the retriever, injects evidence, continues generation and scores segments. It does **not** define a complete machine-readable token grammar, loss mask, decoding algorithm, critic label calibration or exact implementation of the Asai et al. paper. The protocol below is the accepted DocuMind `selfrag-v1` design (ADR-016), not a trained implementation. It deliberately adapts the paper; compare §3.2 (training), §3.3 and Appendix A.3 (inference) of https://arxiv.org/html/2310.11511v1. Section 4 covers experiments. No exact-paper-reproduction claim is made.

## 1. Three distinct states

1. `baseline_rag`: same base model (or documented baseline) with ordinary, forced retrieval and citation checking. No learned reflection tokens required.
2. `self_rag`: **actually fine-tuned** model with registered reflection tokens and a tested inference control loop; can make eligible retrieval decisions.
3. Unavailable capability: no usable checkpoint/tokenizer/runtime; API returns `MODEL_NOT_READY`. This is a capability/error state, not an additional result mode.

Heuristic rules or prompt-generated `"[Retrieve]"` strings may be useful during orchestration prototyping, but must be logged as `simulation`/`policy`, **not** results from a trained Self-RAG model.

### Serving and release identity

The adapter is fixed within an immutable release; uploads and questions only change retrieved context, never weights. Deliberate retraining publishes a new version. The complete release includes the exact base model, adapter, trained token rows, tokenizer/token map and protocol/runtime manifest described in [INFERENCE.md](INFERENCE.md).

Our trained generator can run behind an authenticated API on managed Kaggle, another approved remote runtime or a local machine. The application retains the state machine, evidence policy, retrieval and verification; the model service executes bounded DECIDE/CANDIDATE steps and returns token IDs and complete critic-family probabilities. FastAPI + Transformers/PEFT is the initial serving target. Ordinary external chat APIs provide baseline RAG; no local copy of Llama is required for that mode, and a separate local critic is outside this contract.

Remote transport does not change selfrag-v1 semantics or reset budgets. Pin the release/config version for each run. Conversion, quantization, merged weights and alternate serving engines require save/reload and protocol-equivalence probes before admission. A self-declared model name or tags in text cannot establish trained capability. Connection revocation and uncertain in-flight recovery follow INFERENCE.md and fail closed; no silent mode/provider fallback.

## 2. Frozen design grammar: `selfrag-v1`

Register each full symbol as one special token. The vocabulary is `[Retrieve]`, `[NoRetrieve]`, `[Continue]`, `[Finish]`, `[EndSegment]`, `[IsREL:Yes]`, `[IsREL:No]`, `[IsSUP:Fully]`, `[IsSUP:Partially]`, `[IsSUP:No]`, and `[IsUSE:1]` through `[IsUSE:5]` (15 symbols). Preserve ordinary model EOS separately.

| State | Legal generated sequence | Effect |
|---|---|---|
| DECIDE | One of Retrieve / NoRetrieve / Continue / Finish | Choose retrieval, ungrounded generation, existing-evidence reuse, or final verification |
| CANDIDATE with evidence | `[IsREL:No]`, or `[IsREL:Yes]` + answer segment + `[EndSegment]` + one support label + one utility label | Reject irrelevant passage, or score a completed candidate |
| CANDIDATE without evidence | Answer segment + `[EndSegment]` + one utility label | Only eligible general questions; no support label or citation fabricated |
| VERIFY | No model control output | Deterministic identity/span/claim checks and selection |

A segment is at most 128 ordinary model tokens and ends only at `[EndSegment]`, not punctuation. Cap runs at 8 committed segments, 2048 newly decoded tokens across **all** candidates/control tokens, 3 retrieval attempts and 120 seconds; whichever is first wins. The token cap is shared across rejected candidates. V1 evaluates candidates serially in stable retrieved order (up to 5); beam width is 1. These are the v1 maximum budgets, not performance claims. Deployment configuration may lower them; increasing them requires a new recorded protocol/config revision and validation. Native model context length is checked separately with full reserved output space; remove oldest unselected evidence first, never silently truncate the active source or committed claims. If it still does not fit, finish from already verified claims or abstain.

New token embeddings and output head rows must be trainable and saved with the adapter. Save `reflection_vocab_version`, `token_id_map.json`, tokenizer/base revisions and serializer hash. Test encode/decode and reload before accepting annotation production. Literal control strings in document/question data are escaped before tokenization; only decoder-origin token IDs drive transitions.

**Declared paper deviations:** the paper includes evidence reuse and probability-based segment scoring. V1 retains those ideas but uses custom token spellings/boundaries, serial candidates, greedy segment selection, an external evidence gate and mandatory private-document retrieval. It does not implement the paper's segment beam search. Portfolio/evaluation wording must say **trained Self-RAG-inspired adaptation** until a separately versioned fidelity implementation is validated.

## 3. Annotation data contract

JSONL contains `sample_id`, grouped `split`, `source_group_id`, `protocol_version`, question, query type, retrieval policy, immutable source anchors, label provenance, reviewed status, and an ordered `episodes` array. Each episode contains `state`, `context`, `target`, and source/segment IDs. Context is canonical JSON with sorted keys and escaped data, rendered inside the pinned base model's chat template; tokenizer/template hashes are part of the dataset manifest. Targets are generated control symbols and answer text, never arbitrary JSON concatenated as executable control.

Illustrative episodes (synthetic, not a dataset):

```json
{
  "sample_id":"ex_0001",
  "protocol_version":"selfrag-v1",
  "split":"train",
  "source_group_id":"synthetic_invoice_group_001",
  "retrieval_policy":"required",
  "episodes":[
    {"state":"DECIDE","context":{"question":"What is invoice INV-100 total?","segments":[],"evidence":[]},"target":"[Retrieve]"},
    {"state":"CANDIDATE","context":{"question":"What is invoice INV-100 total?","passage_id":"p1","text":"Invoice INV-100. Grand total INR 1,100.00"},"target":"[IsREL:Yes]The invoice total is INR 1,100.00.[EndSegment][IsSUP:Fully][IsUSE:5]"},
    {"state":"DECIDE","context":{"question":"What is invoice INV-100 total?","segments":["The invoice total is INR 1,100.00."],"evidence":["p1"]},"target":"[Finish]"}
  ],
  "label_origin":"teacher_critic_reviewed",
  "human_verified":true
}
```

The production serializer expands `p1` from a manifest to exact source content and immutable IDs; missing resolution rejects the sample. A retrieval result is an environment-supplied context turn, never a target for the model to invent. A candidate episode has one passage; multi-source calculations instead provide one validated evidence bundle containing all input anchors and its calculation record. The deterministic comparison engine creates this bundle.

**Loss mask:** all system/user/environment context, padding and template role headers have label `-100`; only the assistant target sequence (ordinary answer text and generated control/critic tokens) contributes to next-token loss. DECIDE has exactly one target control token. Candidate targets must end with the required critic sequence. Do not append a trainable EOS after every segment/decision; `[Finish]` is the run stop action. Failed/irrelevant candidates are represented as their own episodes, not silently dropped.

Label model-requested actions separately from policy-effective actions. Policy-forced context insertion is not a model-generated training target or a reflection-accuracy success. Train intended retrieval labels from adjudicated need; log raw predicted decisions before applying serving overrides. Freeze the serializer, masks, legal transition tests, token IDs and all tiny probe artifacts before bulk annotation. Any grammar/mask change increments the protocol/data version and invalidates incompatible annotations/adapters.

### Coverage

- General knowledge or self-contained queries where retrieval can safely be skipped.
- Questions explicitly asking facts from uploaded private documents, where retrieval is mandatory regardless of raw model preference.
- Relevant vs hard negative passages with nearly identical invoice IDs or dates.
- Full, partial, no and contradictory support cases.
- Multi-hop cross-document questions, missing records, ambiguous linkage and conflicting evidence.
- Arithmetic questions where correct answer requires `Decimal` or SQL verification.

### Splitting

Split by **source document group/vendor/template/contract family**, not simply random question row. Keep all near duplicate templates and synthetic variants within a split. Freeze test data and set seeds before prompt tuning/training.

### Annotation and privacy

The PDF proposes a strong LLM critic (GPT-4o-mini/Gemini Flash). Use such a provider only if documents are synthetic/public or an explicit data-handling approval exists. Store model versions, annotation prompts, sampled human disagreements, parse errors and label frequencies. Maintain a manual adjudication subset; don't equate synthetic label count with quality.

## 4. QLoRA starting experiment

- Research checkpoint target: `meta-llama/Llama-3.2-3B-Instruct` (subject to actual license/access).
- 4-bit NF4 quantization via bitsandbytes if supported by installed CUDA/GPU, PEFT LoRA, `transformers` and `trl`.
- PDF starting values: LoRA rank `r=16`, `3` epochs, about `5K` QA-passage pairs. Test additional hyperparameters on validation set only, document changes.
- Apply the mandatory episode loss mask above, verify special token embeddings/output rows receive gradients, and save adapter **and** token mapping/tokenizer revisions. Run a tiny overfit/save/reload probe before full training; it is a plumbing test, not evidence of generalization.
- Measure peak GPU VRAM, wall clock, tokens/s and data version. The PDF's T4 8-10h and GPU quota statements are not guaranteed.
- Save immutable training manifest: base model revision, code commit, data revision, special-token vocabulary hash, LoRA target modules, batch/accumulation, learning rate, precision, RNG seed, optimizer, checkpoint URI and metrics.

## 5. Policy gate before model decisions

| Query | Policy | Effective action |
|---|---|---|
| Explicit uploaded-document fact | `required` | Retrieve irrespective of `[NoRetrieve]` |
| Cross-document comparison | `required` | Retrieve + typed record comparison |
| Unsupported private source not selected | `needs_scope` | Ask user for documents or abstain, not guess |
| General knowledge without source dependency | `eligible` | Honor trained retrieval decision |
| Arithmetic on provided document values | `required` | Retrieve + deterministic validate |

Save `requested_model_action`, `effective_action`, `policy_override_reason`, `retrieval_calls`. **Skip-rate denominator** in eval must distinguish eligible and mandatory queries, or the metric becomes meaningless.

## 6. Inference engine control flow

Target interface:

```python
async def selfrag_generate(
    question: str,
    workspace_id: UUID,
    document_ids: list[UUID] | None,
    *,
    publication_id: UUID | None,
    query_run_id: UUID,
    max_retrievals: int = 3,
) -> GeneratedAnswer:
    """Return answer, verified citations, structured token log, trace ID."""
```

### Normative state machine (implementation and runtime tests pending)

State holds pinned workspace/publication/document IDs, `evidence_attempted`, `usable_evidence`, attempted retrieval signatures, retrieval count, committed verified segments and shared budgets. Initial state is DECIDE with empty evidence. Evaluate policy **once per decision**, then dispatch exactly one effective action.

For an eligible general question without a pinned publication, a model Retrieve request cannot silently search private workspaces or the internet. Return an insufficient_evidence answer with a needs_scope policy reason explaining that no corpus was selected. Selecting a corpus requires a new authorized query run.

1. DECIDE: decode one legal decision token and log the raw prediction. If private evidence is required and `evidence_attempted=false`, override any action other than Retrieve to Retrieve. This forces an evidence attempt before an early Finish/NoRetrieve, but does not force retrieval at every segment. For required questions after the attempt, NoRetrieve becomes Continue if usable evidence exists; otherwise abstain. Continue without evidence is invalid. Eligible NoRetrieve enters CANDIDATE without evidence; it cannot issue source citations. Finish goes to final verification.
2. RETRIEVE: increment the attempt counter **before** calling the retriever; failed attempts count. V1 uses the original question and pinned scope, with signature `(query, publication_id, document_ids)`. A repeated identical signature stops as a cycle; it does not waste calls fetching the same context. This intentionally limits v1 to one unique text retrieval; later query rewriting requires a new protocol. A different structured lookup is logged separately as an evidence operation. Neither operation may widen scope or reset the shared 3-attempt cap. Failure is explicit; no fabricated scores/context.
3. Set `evidence_attempted=true` on entering the attempt. Store returned passages/bundles in the query evidence ledger. Empty results abstain for required questions. Otherwise enter CANDIDATE, evaluating up to 5 passage-conditioned candidates in stable rank order. Continue reuses existing evidence without calling retrieval and enters the same candidate state.
4. CANDIDATE: generate until the complete grammar in §2 is emitted. IsREL:No rejects the candidate. An unsupported token, unexpected native EOS, missing EndSegment/critic or exhausted budget rejects that candidate; no partial segment is published. No implicit retrieval inside candidate text is allowed. Exhausting all candidates finalizes with verified partial content or abstention.
5. VERIFY: run EVIDENCE.md identity/span/support checks on every candidate claim. An independently unsupported/unknown claim cannot be promoted by critic confidence. Fully verified candidates are ranked as below, and one is committed. Repeat DECIDE with that segment and its evidence. Identical `(segment text, evidence IDs)` repetition ends the loop as a cycle.
6. FINALIZE: render only verified claims for document questions; unresolved requested parts give partially_supported or insufficient_evidence according to EVIDENCE.md. Eligible no-evidence output is unverified. Cancellation/deletion/deadlines follow API_CONTRACT.md and must not create multiple durable outcomes.

### Critic scoring: `critic-v1`

At each label position, compute probabilities by softmax of logits restricted to that label family, before greedy token selection. Store probabilities and selected label separately. Define:

```text
R = P(IsREL:Yes)
S = P(IsSUP:Fully) + 0.5 * P(IsSUP:Partially)
U = sum((i - 1) / 4 * P(IsUSE:i) for i in 1..5)
score = R + S + 0.5 * U
```

First apply the independent support/access gate. Reject IsREL:No and IsSUP:No; Partially-labeled candidates are not emitted in v1 even if another checker favors them. Rank surviving Fully-labeled, independently supported candidates by score descending, then retrieval rank, then chunk/bundle ID ascending. Missing probabilities are a model-interface error, never invented scores. This conservative rule can reduce recall and must be measured. Utility on the eligible no-evidence branch is recorded but does not establish factual support. All weights/rules are frozen before test evaluation; changes create a new scoring revision.

### Generation and training boundary

V1 stops at control boundaries and **restarts with reconstructed context**, using the same episode serializer as training. It does not claim preserved KV-cache pause/resume. The public trace records only actions, labels, scores and evidence IDs. Simulation fixtures can test the state machine before training but cannot enable `mode=self_rag` or supply benchmark results. The release capability requires a real loaded adapter, matching tokenizer/protocol hashes, and held-out reflection evaluation.

## 7. Structured trace requirements

Pin connection/config, base/adapter release and capability evidence at run level; each model-step result must match. Minimum fields per event: `query_run_id`, sequential `seq`, `event_type`, timestamp, `model_revision`, `requested_model_action`, `effective_action`, `retrieval_trigger`, source IDs, optional labels and scores. Stream the **decision log**, not internal hidden reasoning.

Example sequence: `policy_decision -> reflection_token -> retrieval_started -> passage_found -> passage_filtered -> candidate_scored -> citation_verified -> answer`.

Do not emit fake `reflection_token` events on baseline or heuristic routes. Separate scores from labels and ensure all displayed sources exist.

## 8. Verification gates

1. Tokenizer registers and roundtrips every exact control symbol; new embeddings survive saved/reloaded checkpoint.
2. Tests prove retriever invoked after model-generated retrieval symbol and context changes before final generation.
3. Tests prove eligible no-retrieval path calls retriever zero times.
4. Tests prove required document queries cannot bypass retrieval.
5. Tests prove shared attempt cap, cycle detection, timeouts, EOS handling, truncated labels, token/context limits and empty passage behavior; Continue makes no extra retrieval call.
6. Controlled relevant/irrelevant and fully/partially/unsupported examples have label confusion matrices.
7. Reference citations checked against real page/element spans regardless of critic verdict.
8. Baseline and trained Self-RAG comparisons record the same publication/retrieval profile. Raw model decisions and policy-effective actions are scored separately.
9. Tiny probe proves loss masks, gradients on new token rows, legal boundaries, deterministic serializer output and save/reload identity before bulk annotation.
10. Required-query Finish/NoRetrieve before evidence forces exactly one attempt; Continue after evidence does not force another. Repeated identical retrieval terminates without consuming fictitious calls.

## 9. Research references

Source: supplied PDF pp. 1-4 and p. 7. Primary research anchor supplied there: Asai et al., *Self-RAG: Learning to Retrieve, Generate, and Critique through Self-Reflection*, ICLR 2024, https://arxiv.org/abs/2310.11511. This handoff does not independently claim exact replication of the paper's full algorithm.
