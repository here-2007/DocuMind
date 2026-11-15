"""Raw-span chunking, Qdrant transport and matching BM25 ranking."""

import re
from uuid import uuid5

import httpx
from rank_bm25 import BM25Okapi

from documind.errors import AppError
from documind.parser import fingerprint
from documind.search_models import CHUNK_TOKENS, DIMENSION, OVERLAP

BRANCH_LIMIT = 50
RERANK_LIMIT = 20
RESULT_LIMIT = 5
RRF_K = 60


def sparse_tokens(text):
    return re.findall(r"\w+(?:[-./]\w+)*|[^\w\s]", text.casefold())


def chunks_for(publication, element, tokenizer, *, regions=None):
    raw = element["text_raw"]
    regions = regions if regions is not None else [(0, len(raw), "", None)]
    for start, end, header, coordinates in regions:
        text = raw[start:end]
        encoding = tokenizer(
            text, add_special_tokens=False, truncation=False, return_offsets_mapping=True
        )
        offsets = encoding["offset_mapping"]
        if not offsets:
            continue
        index = 0
        while index < len(offsets):
            stop = min(index + CHUNK_TOKENS, len(offsets))
            left = offsets[index][0] if index else 0
            while stop > index:
                right = offsets[stop - 1][1] if stop < len(offsets) else len(text)
                passage = header + text[left:right]
                tokens = tokenizer(passage, add_special_tokens=False, truncation=False)["input_ids"]
                if right > left and len(tokens) <= CHUNK_TOKENS:
                    break
                stop -= 1
            if stop == index:
                raise AppError("RETRIEVAL_INPUT_TOO_LONG", 422)
            a, b = start + left, start + right
            yield {
                "id": uuid5(publication, f"{element['id']}:{a}:{b}"),
                "workspace_id": element["workspace_id"],
                "publication_id": publication,
                "document_id": element["document_id"],
                "ingestion_version": element["ingestion_version"],
                "page_id": element["page_id"],
                "element_id": element["id"],
                "start_char": a,
                "end_char": b,
                "text": passage,
                "token_count": len(tokens),
                "table_coordinates": coordinates,
            }
            if stop == len(offsets):
                break
            index = max(index + 1, stop - OVERLAP)
            # A WordPiece suffix can tokenize differently at the start of a new chunk.
            while index < stop:
                overlap = text[offsets[index][0] : right]
                if len(tokenizer(overlap, add_special_tokens=False)["input_ids"]) <= OVERLAP:
                    break
                index += 1


def point_payload(chunk):
    return {
        **{key: str(chunk[key]) for key in ("workspace_id", "publication_id", "document_id")},
        "ingestion_version": chunk["ingestion_version"],
        "text_hash": fingerprint({"text": chunk["text"]}),
    }


class DenseIndex:
    def __init__(self, cfg):
        self.url = cfg.qdrant_url.rstrip("/")
        self.path = "/collections/" + cfg.qdrant_collection
        self.headers = (
            {"api-key": cfg.qdrant_api_key.get_secret_value()} if cfg.qdrant_api_key else {}
        )

    def call(self, method, path="", payload=None, *, missing_ok=False):
        try:
            with httpx.Client(timeout=10, trust_env=False, headers=self.headers) as client:
                response = client.request(method, self.url + self.path + path, json=payload)
            if missing_ok and response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()["result"]
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise AppError("DENSE_INDEX_UNAVAILABLE", 503) from exc

    def ensure(self):
        info = self.call("GET", missing_ok=True)
        if info is None:
            try:
                self.call("PUT", payload={"vectors": {"size": DIMENSION, "distance": "Cosine"}})
            except AppError:
                # Another workspace can create the shared collection first.
                if self.call("GET", missing_ok=True) is None:
                    raise
            info = self.call("GET")
            for field in ("workspace_id", "publication_id", "document_id"):
                self.call(
                    "PUT",
                    "/index?wait=true",
                    {"field_name": field, "field_schema": "keyword"},
                )
        vectors = info["config"]["params"]["vectors"]
        if vectors.get("size") != DIMENSION or vectors.get("distance") != "Cosine":
            raise AppError("DENSE_INDEX_IDENTITY_MISMATCH", 503)

    @staticmethod
    def scope(workspace, publication, documents=None):
        conditions = [
            {"key": "workspace_id", "match": {"value": str(workspace)}},
            {"key": "publication_id", "match": {"value": str(publication)}},
        ]
        if documents is not None:
            if not documents:
                raise AppError("INVALID_DOCUMENT_SCOPE", 422)
            conditions.append({"key": "document_id", "match": {"any": list(map(str, documents))}})
        return {"must": conditions}

    def write(self, chunks, vectors):
        points = [
            {"id": str(chunk["id"]), "vector": vector, "payload": point_payload(chunk)}
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        self.call("PUT", "/points?wait=true&ordering=strong", {"points": points})

    def members(self, workspace, publication):
        members = {}
        offset = None
        while True:
            batch = self.call(
                "POST",
                "/points/scroll",
                {
                    "filter": self.scope(workspace, publication),
                    "limit": 256,
                    "with_payload": True,
                    "with_vector": False,
                    "offset": offset,
                },
            )
            members.update({p["id"]: p["payload"] for p in batch["points"]})
            offset = batch["next_page_offset"]
            if offset is None:
                return members

    def search(self, workspace, publication, documents, vector):
        hits = self.call(
            "POST",
            "/points/search",
            {
                "filter": self.scope(workspace, publication, documents),
                "vector": vector,
                "limit": BRANCH_LIMIT,
                "with_payload": False,
                "params": {"exact": True},
            },
        )
        return sorted(hits, key=lambda hit: (-hit["score"], hit["id"]))

    def remove(self, workspace, publication):
        self.call(
            "POST",
            "/points/delete?wait=true&ordering=strong",
            {"filter": self.scope(workspace, publication)},
            missing_ok=True,
        )


def rank_passages(query, chunks, sparse, dense_hits, models, *, rerank=True):
    eligible = {str(chunk["id"]): chunk for chunk in chunks}
    if not eligible:
        return []
    tokens = sparse_tokens(query)
    corpus = [entry["tokens"] for entry in sparse]
    scores = BM25Okapi(corpus).get_scores(tokens) if corpus else []
    # Match eligibility before taking top-k. Unmatched items have no sparse rank.
    sparse_hits = sorted(
        (
            (entry["id"], float(score))
            for entry, score in zip(sparse, scores, strict=True)
            if entry["id"] in eligible and set(entry["tokens"]).intersection(tokens)
        ),
        key=lambda item: (-item[1], item[0]),
    )[:BRANCH_LIMIT]
    ranks = {}
    for branch, ids in (
        ("dense_rank", [item["id"] for item in dense_hits]),
        ("sparse_rank", [item[0] for item in sparse_hits]),
    ):
        for rank, identifier in enumerate(ids, 1):
            if identifier not in eligible:
                raise AppError("INDEX_MEMBERSHIP_MISMATCH", 503)
            ranks.setdefault(identifier, {})[branch] = rank
    candidates = []
    for identifier, rank in ranks.items():
        candidates.append(
            {
                **eligible[identifier],
                **rank,
                "rrf_score": sum(1 / (RRF_K + value) for value in rank.values()),
            }
        )
    candidates.sort(key=lambda item: (-item["rrf_score"], str(item["id"])))
    candidates = candidates[:RERANK_LIMIT]
    if not candidates or not rerank:
        return candidates[:RESULT_LIMIT]
    scores = models.rerank(query, [item["text"] for item in candidates])
    for item, score in zip(candidates, scores, strict=True):
        item["rerank_score"] = score
    return sorted(candidates, key=lambda item: (-item["rerank_score"], str(item["id"])))[
        :RESULT_LIMIT
    ]
