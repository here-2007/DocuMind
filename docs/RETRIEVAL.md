# Hybrid retrieval technical specification

The hybrid ranking structure comes from source PDF pp. 2, 4. EmbeddingGemma 2 replaces its MiniLM embedder under ADR-020; SQL lookup and document-aware constraints are DocuMind additions.

## 1. Versioned defaults: `hybrid-v3`

This file owns retrieval budgets and tokenization. The selected production profile is **hybrid-v3**, using `google/embeddinggemma-2` with **240 reranker-tokenized content tokens and 64 overlap**. ADR-020 supersedes the hybrid-v2 MiniLM default without increasing passage size. Model/tokenizer revisions and compatible library versions must be pinned before indexing; model names alone are not reproducible revisions. No runtime or quality result is claimed.

| Parameter | Starting value | Source |
|---|---:|---|
| Dense embedder | `google/embeddinggemma-2`, text-only | User decision; ADR-020 |
| Dense output | 768 dimensions, L2-normalized; cosine search | hybrid-v3 fixed configuration |
| Vector store | Qdrant | PDF p. 2 |
| Sparse retriever | `rank_bm25` | PDF p. 2 |
| Text chunk target / maximum | 240 content tokens under the reranker tokenizer | Keeps reranking inputs bounded |
| Chunk overlap | 64 **reranker tokens** | Preserves source overlap budget |
| Dense encoded input limit | 8192, including formatting and special tokens | Embedder ceiling; assert at runtime |
| Query maximum | 64 content tokens under both retrieval tokenizers | Reject excess; never silently truncate |
| Reranker pair limit | 512, including query and special tokens | Pinned tokenizer limit; assert at runtime |
| Candidates per retrieval branch | 50 | DocuMind starting budget |
| RRF `k` | 60 | PDF p. 4 |
| Cross encoder | `cross-encoder/ms-marco-MiniLM-L6-v2` | PDF pp. 2, 4 |
| Rerank candidates | top 20 | PDF p. 4 |
| Final passages | top 5 | PDF p. 4 |
| Developer sanity evaluation | 50 questions inspected at top 5 | PDF p. 4 |

Use config constants, record revisions and make benchmark alternatives explicit; do not silently change them to improve numbers.

### Tokenization and overflow

- Use the pinned reranker's tokenizer with offsets and `add_special_tokens=False` to construct chunks. Preserve a mapping from normalized offsets to immutable raw source ranges. Count tokens again on the exact final chunk text, including repeated table headers if any.
- Prefer element/row boundaries within the 240-token maximum. Oversized elements are split with source offsets; never discard their tail. Overlap is at most 64 tokens and must be smaller than the preceding chunk so splitting always advances.
- Independently tokenize the exact formatted embedding input with the pinned Gemma tokenizer, truncation disabled, and assert it fits 8192 including special tokens. The larger dense limit does not increase the chunk budget. Fail indexing on incompatible tokenizers/configuration instead of truncating or silently changing limits.
- Validate query length using both tokenizers. Rerank the actual `(query, chunk)` pair without truncation and check the pinned [reranker tokenizer's 512-token limit](https://huggingface.co/cross-encoder/ms-marco-MiniLM-L6-v2/blob/main/tokenizer_config.json). An unexpected overflow is `RETRIEVAL_INPUT_TOO_LONG`, not a scored truncated passage.
- Historical `hybrid-v2` used `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions, its own 240/64 tokenizer budget and a 256-token encoded cap. It is superseded, not an automatic fallback.
- `source-512-v1` retains that MiniLM embedder as an opt-in historical experiment with 512 content tokens / 64 overlap. It is never the application default. Its manifest explicitly records dense truncation to 256 and pair truncation to 512, affected chunk IDs and token counts. Results are labeled as a truncating source-baseline adaptation, not equivalent to `hybrid-v3`. Compare baseline RAG and Self-RAG within the same profile.

### Embedding input and index identity

- Load only the text encoder; with SentenceTransformers, disable vision/audio via `config_kwargs={"vision_config": None, "audio_config": None}`. Pin and test the actual loader before use.
- Fix query formatting to `task: search result | query: {query}` (SearchQuery) and corpus formatting to `title: none | text: {chunk}` (Document). Apply each prefix exactly once. This profile deliberately omits optional titles. Format metadata is not a source excerpt and must never become a citation. BM25 and reranking receive the unprefixed query/chunk text.
- Require finite, nonzero, 768-dimensional vectors; L2-normalize queries and documents and use cosine search. Other dimensions/modalities or prompt changes require another recorded profile.
- Persist model/tokenizer revisions, loader/library versions, modality configuration, prompt strings/hash, normalization, dimension and chunker revision in the publication manifest. Query encoding must match the pinned publication; reject a mismatch even when dimensions agree.
- Build `documind_chunks_v3` and re-embed every chunk for any existing corpus; publish only with a matching BM25 snapshot. Never mix MiniLM and Gemma vectors or reuse an old embedding cache. The native backend is implemented, but no runtime search index is built yet.

Model capabilities and formatting reference: [Google EmbeddingGemma 2 model card](https://huggingface.co/google/embeddinggemma-2). Hardware, loader compatibility and retrieval accuracy remain untested.

## 2. Retrieve API

```python
async def retrieve(
    query: str,
    *,
    workspace_id: UUID,
    document_ids: list[UUID] | None = None,
    publication_id: UUID,
    k: int = 5,
    retrieval_version: str = "hybrid-v3",
) -> list[Passage]: ...
```

Always restrict to authorized workspace and document IDs. Reject empty queries, unavailable indexes and unauthorized documents explicitly. `None` means all searchable authorized documents resolved once at query creation; `[]` is invalid, never an instruction to search everything. The caller pins a publication and effective document set according to [DATA_MODEL.md](DATA_MODEL.md). Default v3 requires both branches and the reranker; outages return an explicit dependency error, not an automatic degraded experiment.

## 3. Algorithm

1. Normalize user query lightly without destroying invoice numbers, dates, punctuation-sensitive IDs or currency symbols. Preserve raw query in trace.
2. Search Qdrant dense vectors with **workspace + publication + effective document filters**, taking up to 50 candidates.
3. Search the matching workspace/publication BM25 snapshot over identical chunk IDs. Apply document eligibility before top-50 selection; preserve numeric identifier tokens. Snapshot IDF is computed over that workspace publication and recorded, not recomputed silently for selected subsets.
4. Merge candidates on `chunk_id`; track both rank positions; absent rank means no contribution.
5. For each candidate `d`, compute reciprocal rank fusion:

   `RRF(d) = sum_{r in [dense,sparse] if d present} 1 / (60 + rank_r(d))`

   Rank is **1-based**. RRF is rank based, not mixing cosine similarity and BM25 score numerically.
6. Sort by descending `rrf_score`, then deterministic tie break by `chunk_id`/source order; take up to 20.
7. Cross encode each `(query, candidate.text)` with `cross-encoder/ms-marco-MiniLM-L6-v2`. Sort by cross encoder score and stable tie break. Return up to `k=5`.
8. Include source anchors and stage scores/ranks in `Passage` and trace. Never invent scores for unavailable branches.

## 4. Document structure constraints

- The `hybrid-v3` profile protects table rows and source anchors within its input budget. If splitting a table is unavoidable, carry table ID, headers, row/column IDs into each text fragment and count that added text against the budget.
- A chunk must not merge content from distinct documents or incompatible extraction versions.
- Preserve exact identifiers in metadata and text. Consider a separate field lookup path for `invoice_no`, `po_number`, dates and currencies.
- Image-only figure text may be unavailable; represent unsupported modalities honestly and add OCR/vision components only after evaluation.

## 5. Structured lookup branch (DocuMind addition)

`structured_retrieve(workspace_id, document_ids, entity_ids, fields, filters)` looks up **typed extracted records** in PostgreSQL. It serves exact PO number matching, invoice totals, dates and table line items. The query router can merge structured evidence anchors with text chunks into a single cited context. Structured SQL values are not a substitute for original document evidence.

Record linkage policy:
1. Match exact normalized identifiers and legal entity IDs where present.
2. Apply configurable candidate matching only when exact identifiers missing.
3. Mark uncertain/ambiguous matches for review; never auto-assert equivalence at an arbitrary embedding score.
4. Require original evidence for each linked record and field.

## 6. Failure modes and explicit behavior

| Condition | Required behavior |
|---|---|
| Dense index unavailable | Return dependency error; sparse-only is an explicitly selected evaluation variant |
| BM25 snapshot stale | Rebuild/reject; never compare different corpora without flagging mismatch |
| Zero hits or nonempty but irrelevant hits | Apply the evidence gate in EVIDENCE.md; passage count is not proof of support |
| OCR low confidence | Propagate confidence and potentially require review |
| Query contains exact invoice ID | Ensure normalization doesn't erase ID; exercise keyword path |
| One chunk appears in both branches | Deduplicate using `chunk_id`; retain both rank contributions |
| Cross-encoder model unavailable | Dependency error in v3; an explicit no-reranker evaluation variant records the omission |
| Workspace filter missing | Treat as security failure, block query |

## 7. Evaluation and testing

Track Recall@5, Precision@5, MRR, NDCG@5, passage-level source hit accuracy, per-stage latency and retrieval coverage by query type. Run ablations dense-only, sparse-only, hybrid without rerank, full reranked. The original PDF asks 50-question manual top-5 checks; add a machine-labeled test corpus and retain manual review notes. For source-specific queries, query-to-doc grounding matters more than generic semantic similarity.
