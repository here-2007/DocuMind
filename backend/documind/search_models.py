"""Pinned, offline text inference for the hybrid-v3 profile."""

import math
from functools import lru_cache
from importlib.metadata import version
from importlib.util import find_spec

from documind.errors import AppError

PROFILE = "hybrid-v3"
EMBEDDER = "google/embeddinggemma-2"
EMBED_REVISION = "f9972ac22e696a105c482202646979e33e4b5a1e"
RERANKER = "cross-encoder/ms-marco-MiniLM-L6-v2"
RERANK_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
QUERY_PREFIX = "task: search result | query: "
DOCUMENT_PREFIX = "title: none | text: "
DIMENSION = 768
CHUNK_TOKENS = 240
OVERLAP = 64
QUERY_TOKENS = 64
DENSE_LIMIT = 8192
PAIR_LIMIT = 512


@lru_cache(maxsize=1)
def query_tokenizers():
    try:
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer

        return tuple(
            Tokenizer.from_file(
                hf_hub_download(
                    model,
                    "tokenizer.json",
                    revision=revision,
                    local_files_only=True,
                )
            )
            for model, revision in ((EMBEDDER, EMBED_REVISION), (RERANKER, RERANK_REVISION))
        )
    except Exception as exc:
        raise AppError("SEARCH_MODELS_UNAVAILABLE", 503) from exc


def search_artifacts_ready():
    from huggingface_hub import hf_hub_download

    query_tokenizers()
    if find_spec("torch") is None or find_spec("transformers") is None:
        return False
    for model, revision in ((EMBEDDER, EMBED_REVISION), (RERANKER, RERANK_REVISION)):
        hf_hub_download(model, "model.safetensors", revision=revision, local_files_only=True)
    return True


def validate_question(question):
    for tokenizer in query_tokenizers():
        if not 0 < len(tokenizer.encode(question, add_special_tokens=False).ids) <= QUERY_TOKENS:
            raise AppError("RETRIEVAL_INPUT_TOO_LONG", 422)


@lru_cache(maxsize=1)
def search_models():
    try:
        import torch
        from transformers import (
            AutoConfig,
            AutoModel,
            AutoModelForSequenceClassification,
            AutoTokenizer,
        )

        torch.set_num_threads(2)
        args = {"local_files_only": True, "trust_remote_code": False}
        config = AutoConfig.from_pretrained(EMBEDDER, revision=EMBED_REVISION, **args)
        config.vision_config = None
        config.audio_config = None
        dense, loading = AutoModel.from_pretrained(
            EMBEDDER,
            revision=EMBED_REVISION,
            config=config,
            dtype=torch.float32,
            use_safetensors=True,
            output_loading_info=True,
            **args,
        )
        if loading["missing_keys"] or loading["mismatched_keys"]:
            raise ValueError("Incomplete text weights")
        dense.eval()
        if dense.vision_tower is not None or dense.audio_tower is not None:
            raise ValueError("Unexpected modality encoders")
        dense_tokenizer = AutoTokenizer.from_pretrained(EMBEDDER, revision=EMBED_REVISION, **args)
        tokenizer = AutoTokenizer.from_pretrained(RERANKER, revision=RERANK_REVISION, **args)
        rank = AutoModelForSequenceClassification.from_pretrained(
            RERANKER,
            revision=RERANK_REVISION,
            use_safetensors=True,
            **args,
        ).eval()
        return SearchModels(torch, dense, dense_tokenizer, tokenizer, rank)
    except Exception as exc:
        raise AppError("SEARCH_MODELS_UNAVAILABLE", 503) from exc


def retrieval_identity():
    return {
        "profile": PROFILE,
        "embedder": EMBEDDER,
        "embedding_revision": EMBED_REVISION,
        "reranker": RERANKER,
        "reranker_revision": RERANK_REVISION,
        "library_versions": {
            name: version(name)
            for name in (
                "transformers",
                "torch",
                "tokenizers",
                "rank-bm25",
            )
        },
        "modalities": ["text"],
        "loader": "transformers-masked-mean-including-prompt-v1",
        "dtype": "float32",
        "dimension": DIMENSION,
        "normalization": "l2",
        "query_prefix": QUERY_PREFIX,
        "document_prefix": DOCUMENT_PREFIX,
        "chunker": "raw-spans-v2",
        "sparse_tokenizer": "casefold-ids-v1",
        "budgets": [CHUNK_TOKENS, OVERLAP, QUERY_TOKENS, DENSE_LIMIT, PAIR_LIMIT],
    }


class SearchModels:
    def __init__(self, torch, dense, dense_tokenizer, tokenizer, rank):
        self.torch = torch
        self.dense = dense
        self.tokenizer = tokenizer
        self.rank = rank
        self.dense_tokenizer = dense_tokenizer
        if not self.tokenizer.is_fast or not dense_tokenizer.is_fast:
            raise ValueError("Incompatible retrieval models")
        self.identity = retrieval_identity()

    def validate_query(self, query):
        for tokenizer in (self.tokenizer, self.dense_tokenizer):
            ids = tokenizer(query, add_special_tokens=False, truncation=False)["input_ids"]
            if not ids or len(ids) > QUERY_TOKENS:
                raise AppError("RETRIEVAL_INPUT_TOO_LONG", 422)

    def embed(self, texts, *, query=False):
        prefix = QUERY_PREFIX if query else DOCUMENT_PREFIX
        formatted = [prefix + text for text in texts]
        encoded = self.dense_tokenizer(
            formatted, truncation=False, padding=True, return_tensors="pt"
        )
        if encoded["input_ids"].shape[1] > DENSE_LIMIT:
            raise AppError("RETRIEVAL_INPUT_TOO_LONG", 422)
        with self.torch.inference_mode():
            hidden = self.dense(**encoded).last_hidden_state
            mask = encoded["attention_mask"].unsqueeze(-1)
            embeddings = (hidden * mask).sum(dim=1) / mask.sum(dim=1)
        return [normalized(vector.tolist()) for vector in embeddings]

    def rerank(self, query, texts):
        encoded = self.tokenizer(
            [query] * len(texts), texts, truncation=False, padding=True, return_tensors="pt"
        )
        if encoded["input_ids"].shape[1] > PAIR_LIMIT:
            raise AppError("RETRIEVAL_INPUT_TOO_LONG", 422)
        with self.torch.inference_mode():
            scores = self.rank(**encoded).logits.flatten().tolist()
        if len(scores) != len(texts) or any(not math.isfinite(score) for score in scores):
            raise AppError("INVALID_RERANKER_OUTPUT", 503)
        return scores


def normalized(vector):
    if len(vector) != DIMENSION or any(not math.isfinite(x) for x in vector):
        raise AppError("INVALID_EMBEDDING", 503)
    norm = math.hypot(*vector)
    if not math.isfinite(norm) or norm <= 0:
        raise AppError("INVALID_EMBEDDING", 503)
    return [x / norm for x in vector]
