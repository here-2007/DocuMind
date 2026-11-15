"""Download the exact hybrid-v3 revisions into the local Hugging Face cache."""

import argparse

from documind.reflection_model import TARGET
from documind.search_models import EMBED_REVISION, EMBEDDER, RERANK_REVISION, RERANKER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true", help="Run real CPU embedding and reranking")
    parser.add_argument(
        "--reflection-tokenizer",
        action="store_true",
        help="Also cache the pinned generator tokenizer for Self-RAG preflight",
    )
    args = parser.parse_args()
    from huggingface_hub import snapshot_download

    for model, revision in ((EMBEDDER, EMBED_REVISION), (RERANKER, RERANK_REVISION)):
        snapshot_download(
            model,
            revision=revision,
            max_workers=2,
            allow_patterns=[
                "config.json",
                "tokenizer*",
                "special_tokens_map.json",
                "vocab.txt",
                "model.safetensors",
            ],
        )
        print(f"Cached {model}@{revision}")
    if args.reflection_tokenizer:
        from transformers import AutoTokenizer

        AutoTokenizer.from_pretrained(TARGET[0], revision=TARGET[1], trust_remote_code=False)
        print(f"Cached generation tokenizer {TARGET[0]}@{TARGET[1]}")
    if args.probe:
        from documind.search_models import search_models

        models = search_models()
        vector = models.embed(["Grand total: INR 1,100.00"])[0]
        scores = models.rerank("What is the invoice total?", ["Grand total: INR 1,100.00"])
        print(f"CPU inference verified: {len(vector)} dimensions and {len(scores)} rerank score")


if __name__ == "__main__":
    main()
