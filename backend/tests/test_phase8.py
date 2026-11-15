"""Deterministic checks for the offline Phase 8 research controls."""

from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pymupdf
import pytest
from documind import reflection_corpus_v4, selfrag
from documind.reflection import canonical, digest
from documind.runtime_protocol import FAMILIES
from documind.search_index import rank_passages


@pytest.fixture
def research(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).parents[2] / "scripts"))
    import evaluate_phase8
    import phase8_dataset
    import phase8_gpu
    import phase8_report

    return SimpleNamespace(
        eval=evaluate_phase8, data=phase8_dataset, gpu=phase8_gpu, report=phase8_report
    )


def test_manifest_and_original_gold_spans_are_frozen(research):
    raw = research.data.MANIFEST.read_bytes()
    assert digest(raw) == "6bf099104bb6211a35f5f30497a0f0b4c5c53a8e3e50851f7c76ebe73e443c3b"
    assert raw == "".join(canonical(row) + "\n" for row in research.data.build()).encode()
    metadata, documents, cases = research.data.load(research.data.MANIFEST)
    assert metadata["human_adjudication"] is False
    assert len({(case["question"], tuple(case["scope"])) for case in cases}) == 200
    assert Counter(Counter(case["group"] for case in cases).values()) == {5: 40}
    assert Counter((bool(case["scope"]), bool(case["gold"])) for case in cases) == {
        (False, False): 40,
        (True, True): 120,
        (True, False): 40,
    }
    for case in cases:
        if gold := case["gold"]:
            doc = documents[gold["document"]]
            pdf = research.eval.pdf_bytes(doc)
            assert pdf == research.eval.pdf_bytes(doc)
            with pymupdf.open(stream=pdf) as original:
                assert gold["quote"] in original[gold["page"] - 1].get_text().splitlines()


def test_pretraining_holdout_keeps_original_identity(research, tmp_path):
    corpus = tmp_path / "corpus"
    reflection_corpus_v4.export(corpus)
    dataset = tmp_path / "manifest.jsonl"
    dataset.write_text(
        "".join(canonical(row) + "\n" for row in research.data.pretraining_holdout(corpus))
    )
    metadata, _, cases = research.data.load(dataset)
    assert metadata["corpus_sha256"] == research.data.V4_CORPUS_SHA256
    assert Counter(case["retrieval_policy"] for case in cases) == {"eligible": 36, "required": 24}
    assert set(case["category"] for case in cases) == {"frozen_document", "frozen_general"}


def test_retrieval_variants_disable_only_the_named_components():
    chunks = [{"id": "a", "text": "exact invoice"}, {"id": "b", "text": "semantic billing"}]
    sparse = [{"id": "a", "tokens": ["exact", "invoice"]}, {"id": "b", "tokens": ["semantic"]}]
    dense_hits = [{"id": "b"}, {"id": "a"}]
    calls = []

    def rerank(question, texts):
        calls.append((question, texts))
        return [0.2, 0.8]

    models = SimpleNamespace(rerank=rerank)
    dense = rank_passages("invoice", chunks, [], dense_hits, models, rerank=False)
    sparse = rank_passages("invoice", chunks, sparse, [], models, rerank=False)
    blend = rank_passages(
        "invoice",
        chunks,
        sparse=[{"id": "a", "tokens": ["invoice"]}],
        dense_hits=dense_hits,
        models=models,
        rerank=False,
    )
    assert [row["id"] for row in dense] == ["b", "a"] and "sparse_rank" not in dense[0]
    assert [row["id"] for row in sparse] == ["a"] and "dense_rank" not in sparse[0]
    assert [row["id"] for row in blend] == ["a", "b"] and not calls
    full = rank_passages(
        "invoice", chunks, [{"id": "a", "tokens": ["invoice"]}], dense_hits, models
    )
    assert [row["id"] for row in full] == ["b", "a"] and len(calls) == 1


@pytest.mark.parametrize("is_verifiable", [True, False])
def test_without_support_keeps_independent_verifier_and_budget(is_verifiable):
    source = {"source_id": "source"}
    budgets = []

    def run(use_support):
        def step(state, context, budget):
            budgets.append(budget)
            if state == "DECIDE":
                labels = {"decision": "[Finish]" if context["segments"] else "[Retrieve]"}
            else:
                labels = {
                    "relevance": "[IsREL:Yes]",
                    "support": "[IsSUP:No]",
                    "utility": "[IsUSE:5]",
                }
            probabilities = {
                family: {token: float(token == label) for token in FAMILIES[family]}
                for family, label in labels.items()
            }
            return SimpleNamespace(
                finish_reason="complete", output_tokens=1, text="exact", probabilities=probabilities
            ), labels

        return selfrag.run(
            "Question?",
            True,
            step,
            lambda: [([source], source)],
            lambda text, anchor: {"quote": text} if is_verifiable else None,
            lambda *a, **k: None,
            use_support=use_support,
            token_budget=12,
        )

    assert run(True)["citations"] == []
    assert run(False)["citations"] == ([{"quote": "exact"}] if is_verifiable else [])
    assert budgets[:2] == [12, 11]


def test_score_and_paired_analysis_distinguish_evidence_from_answer(research):
    case = {
        "gold": {"document": "record", "page": 2, "quote": "Currency: JPY"},
        "gold_value": "JPY",
    }
    citation = {"document_id": "uuid", "page_number": 2, "quote": "Currency: JPY"}
    assert research.eval.score(case, "uuid", "JPY", citation, False) == (True, False)
    assert research.eval.score(case, "uuid", "JPY", citation, True) == (True, True)
    rows = [
        {"case": {"group": group}, "arms": {"trained": {"correct": a}, "base": {"correct": b}}}
        for group, a, b in [("one", True, True), ("one", True, False), ("two", False, True)]
    ]
    report = research.report.paired(rows, "trained", "base")
    assert report["paired_cases"] == 3 and report["groups"] == 2
    assert report["accuracy_difference"] == 0
    assert report["variant_only_correct"] == report["reference_only_correct"] == 1


def test_private_paired_runtime_program_is_valid_python(research):
    source = research.gpu.server_program("private-bundle", "a" * 64)
    compile(source, "serve.py", "exec")
    assert 'host="127.0.0.1"' in source and 'device="cuda"' in source


def test_paired_runtime_archive_retries_same_part_without_changing_bytes(
    research, tmp_path, monkeypatch
):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_bytes(b"fixture")
    remote = tmp_path / "remote"
    attempts = []

    def put(path, data):
        attempts.append((path, digest(data)))
        if len(attempts) == 1:
            raise research.gpu.serving.ProbeError("JUPYTER_NETWORK_FAILURE")
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_bytes(data)

    def unpack(code, timeout):
        assert timeout == 45
        exec(compile(code, "private_archive.py", "exec"), {})

    monkeypatch.setattr(research.gpu, "sleep", lambda _: None)
    research.gpu.upload_runtime(SimpleNamespace(put_file=put, execute=unpack), str(remote), bundle)
    assert attempts[0] == attempts[1]
    assert (remote / "bundle/manifest.json").read_bytes() == b"fixture"
    assert (remote / "source/documind/reflection_runtime.py").is_file()
    assert not (remote / "archive").exists()


@pytest.mark.parametrize(
    "corruption",
    [
        None,
        "score",
        "raw",
        "duplicate",
        "pdf",
        "missing_arms",
        "missing_environment",
        "invalid_code",
        "source_snapshot",
        "capability",
        "outside_scope",
        "unknown_run_kind",
        "retrieval_only",
        "failed_arm",
    ],
)
def test_report_recomputes_scores_and_checks_frozen_artifacts(research, tmp_path, corruption):
    document = {"kind": "document", "id": "one", "group": "group", "pages": ["Currency: JPY"]}
    case = {
        "kind": "case",
        "id": "qa",
        "group": "group",
        "scope": ["one"],
        "question": "What currency is used?",
        "category": "currency",
        "gold_value": "JPY",
        "gold": {"document": "one", "page": 1, "quote": "Currency: JPY"},
        "retrieval_policy": "required",
    }
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        "".join(
            canonical(row) + "\n"
            for row in (
                {"kind": "metadata"},
                document,
                case,
            )
        )
    )
    run = tmp_path / "run"
    (run / "pdfs").mkdir(parents=True)
    raw = research.eval.pdf_bytes(document)
    (run / "pdfs/one.pdf").write_bytes(raw)
    from shutil import copyfile

    from documind.reflection_model import TARGET
    from test_reflection_runtime import capability

    scope_root = run / "source"
    for name in research.eval.SOURCE_FILES:
        out = scope_root / name
        out.parent.mkdir(parents=True, exist_ok=True)
        copyfile(research.eval.ROOT / name, out)
    cap = capability().model_copy(
        update={"inference_profile": "nf4-cuda", "model_id": TARGET[0], "model_revision": TARGET[1]}
    )
    profile = {
        "model": TARGET[0],
        "revision": TARGET[1],
        "context_limit": cap.context_limit,
        "inference_profile": cap.inference_profile,
        "base_quantization": "nf4",
        "base_adapter_loaded": False,
        "gpu": "test-gpu",
        "cuda": "test-cuda",
        "torch": "test-torch",
        "transformers": "test-transformers",
        "bitsandbytes": "test-bnb",
    }
    env = canonical({"profile": profile, "capability": cap.model_dump(mode="json")}).encode()
    if corruption != "missing_environment" and corruption != "retrieval_only":
        (run / "environment.json").write_bytes(env)
    config = {
        "schema": research.eval.RUN_SCHEMA,
        "run_kind": "retrieval_only" if corruption == "retrieval_only" else "paired",
        "retrieval_scope": research.eval.RETRIEVAL_SCOPE,
        "environment_sha256": None if corruption == "retrieval_only" else digest(env),
        "source_sha256": research.eval.source_hashes(research.eval.ROOT),
        "retrieval": {"strategy": "test"},
        "token_budget_per_arm": research.eval.TOKEN_BUDGET,
        "temperature": 0,
        "concurrency": 1,
        "baseline_prompt": research.eval.BASELINE_PROMPT,
        "critic_ablation": "test",
        "frozen_manifest_sha256": digest(manifest.read_bytes()),
        "source_pdf_sha256": {"one": digest(raw)},
        "case_ids": ["qa"],
        "source_map": {"one": "uuid"},
    }
    citation = {"document_id": "uuid", "page_number": 1, "quote": "Currency: JPY"}
    arm = {
        "status": "ok",
        "correct": True,
        "committed_text": None,
        "raw_candidates": [{"text": "JPY"}],
        "answer": "Currency: JPY",
        "raw_candidate_correct": True,
        "citation_matches_gold": True,
        "citation_resolves": True,
        "citation": citation,
        "elapsed_seconds": 1.0,
        "wire": [{"evidence": ["Currency: JPY"], "text": "JPY"}],
        "timings": {"retrieval_calls": 1},
        "trace": [],
    }
    retrieval = {
        "rank": 1,
        "matches": 1,
        "returned": 1,
        "seconds": 0.1,
        "hits": [{"document": "uuid", "page": 1, "chunk": "chunk", "relevant": True}],
    }
    row = {
        "case": case,
        "gold_document_id": "uuid",
        "arms": {name: deepcopy(arm) for name in research.report.ARMS},
        "retrieval": {name: deepcopy(retrieval) for name in research.eval.VARIANTS},
    }
    for name in research.report.ARMS - {"naive_rag"}:
        generated = row["arms"][name]
        generated["committed_text"] = "JPY"
        generated["raw_candidates"] = [
            {
                "text": "JPY",
                "finish_reason": "complete",
                "source_id": "chunk",
                "disposition": "eligible_for_selection",
            }
        ]
        generated["wire"] = [
            {
                "state": "CANDIDATE",
                "result": {
                    "run_id": str(uuid4()),
                    "step_id": str(uuid4()),
                    "request_hash": "a" * 64,
                    "runtime_instance_id": str(cap.runtime_instance_id),
                    "manifest_sha256": cap.manifest_sha256,
                    "tokens": [],
                    "input_tokens": 1,
                    "output_tokens": 0,
                    "probabilities": {},
                    "finish_reason": "complete",
                    "text": "JPY",
                },
            }
        ]
        generated["trace"] = [
            {
                "type": "reflection_token",
                "requested_model_action": "[Retrieve]",
                "policy_override_reason": None,
            },
            {"type": "candidate_scored", "source_id": "chunk"},
        ]
    if corruption == "score":
        row["arms"]["naive_rag"]["correct"] = False
    elif corruption == "raw":
        row["arms"]["naive_rag"]["raw_candidates"][0]["text"] = "USD"
    elif corruption == "pdf":
        changed = research.eval.pdf_bytes({**document, "pages": ["Currency: USD"]})
        (run / "pdfs/one.pdf").write_bytes(changed)
        config["source_pdf_sha256"]["one"] = digest(changed)
    elif corruption in {"missing_arms", "retrieval_only"}:
        row["arms"] = {}
    elif corruption == "invalid_code":
        config["source_sha256"] = dict.fromkeys(config["source_sha256"], "0" * 64)
    elif corruption == "source_snapshot":
        (scope_root / "scripts/phase8_gpu.py").write_text("Changed implementation")
    elif corruption == "capability":
        row["arms"]["trained_self_rag"]["wire"][0]["result"]["runtime_instance_id"] = str(uuid4())
    elif corruption == "outside_scope":
        row["retrieval"]["dense_only"]["hits"][0]["document"] = "foreign"
    elif corruption == "unknown_run_kind":
        config["run_kind"] = "unknown"
    elif corruption == "failed_arm":
        row["arms"]["naive_rag"] = {
            "status": "error",
            "error": "timeout",
            "correct": False,
            "raw_candidates": [],
            "wire": [],
            "trace": [],
            "timings": {},
        }
    (run / "run_config.json").write_text(canonical(config))
    (run / "results_raw.jsonl").write_text(
        (canonical(row) + "\n") * (2 if corruption == "duplicate" else 1)
    )
    if corruption not in {None, "retrieval_only", "failed_arm"}:
        with pytest.raises(ValueError):
            research.report.read_run([run], manifest)
    else:
        rows, metrics = research.report.read_run([run], manifest)
        assert len(rows) == metrics["cases"] == metrics["expected_cases"] == 1
        if corruption == "retrieval_only":
            assert metrics["qa"] == {}
            return
        assert metrics["qa"]["naive_rag"]["errors"] == (corruption == "failed_arm")
        assert metrics["qa"]["naive_rag"]["rates"]["skip_accuracy"]["value"] is None
        assert metrics["qa"]["trained_self_rag"]["rates"]["accuracy"]["value"] == 1
        assert metrics["qa"]["trained_self_rag"]["raw_retrieval_decision"]["f1"] == 1


def test_raw_scoring_checks_first_generated_text_even_when_rejected(research):
    wire = [
        {"state": "CANDIDATE", "result": {"text": "", "finish_reason": "complete"}},
        {"state": "CANDIDATE", "result": {"text": "EUR", "finish_reason": "complete"}},
        {"state": "CANDIDATE", "result": {"text": "JPY", "finish_reason": "complete"}},
    ]
    trace = [
        {"type": "passage_filtered", "decision": "[IsREL:No]", "source_id": "a"},
        {"type": "citation_verified", "decision": "unsupported", "source_id": "b"},
        {"type": "citation_verified", "decision": "verified", "source_id": "c"},
        {"type": "candidate_scored", "source_id": "c"},
    ]
    candidates = research.eval.raw_candidates("trained_self_rag", wire, trace, complete=True)
    assert [item["disposition"] for item in candidates] == [
        "[IsREL:No]",
        "unsupported",
        "eligible_for_selection",
    ]
    assert not research.eval.raw_candidate_correct({"gold_value": "JPY", "gold": None}, candidates)
    assert research.eval.raw_candidate_correct({"gold_value": "EUR", "gold": None}, candidates)


def test_retrieval_ablation_filters_dense_and_sparse_to_frozen_scope(research):
    case = {"question": "What is the balance due?", "gold": {"quote": "USD 32", "page": 1}}
    expected = {
        "id": "pinned",
        "document_id": "selected",
        "page_number": 1,
        "text": "Balance: USD 32",
    }
    distractor = {
        **expected,
        "id": "other",
        "document_id": "excluded",
        "text": "Balance due: USD 5",
    }

    def search(workspace, pub, ids, vector):
        assert ids == ["selected"]
        return [{"id": "pinned"}]

    report = research.eval.retrieval_row(
        SimpleNamespace(embed=lambda *args, **kwargs: [[0.1]], rerank=lambda *args: [1.0]),
        "workspace",
        "publication",
        (
            [expected, distractor],
            [{"id": row["id"], "tokens": ["balance"]} for row in (expected, distractor)],
            SimpleNamespace(search=search),
        ),
        case,
        ["selected"],
        "selected",
    )
    assert all(variant["rank"] == 1 and variant["returned"] == 1 for variant in report.values())
