"""The private evaluation plan is frozen and PDF-backed before any GPU allocation."""

import importlib.util
import re
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pymupdf
import pytest
from documind import reflection_corpus_v4
from documind.reflection_corpus_v3 import export
from documind.reflection_data import load_corpus


@pytest.mark.parametrize("status", ["ready", "configured"])
def test_split_evaluation_validates_the_selected_connection(monkeypatch, status):
    path = Path(__file__).parents[2] / "scripts/evaluate_selected_workflow.py"
    spec = importlib.util.spec_from_file_location("selected_workflow", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    calls, processed = [], []
    ready = {"id": "selected-id", "status": status, "config_version": 1}

    def post(route, **kwargs):
        calls.append((route, kwargs))
        data = {"job_id": "validation-job"} if route.endswith("/validate") else ready
        return SimpleNamespace(status_code=202 if route.endswith("/validate") else 201,
                               json=lambda: data)

    client = SimpleNamespace(post=post, get=lambda route: SimpleNamespace(json=lambda: ready))
    phase = SimpleNamespace(cfg=SimpleNamespace(local_workspace_id="workspace",
                                                 local_chat_endpoint=""))
    monkeypatch.setattr(module, "process", processed.append)
    monkeypatch.setenv("DOCUMIND_SELECTED_GENERATOR_TOKEN", "private-test-token")
    options = {"generator_endpoint": "http://127.0.0.1:8011/v1/chat/completions",
               "generator_model": "selected-model"}
    if status == "ready":
        assert module.generation_connection(client, phase, options, monkeypatch) == ready
    else:
        with pytest.raises(AssertionError, match="real capability validation"):
            module.generation_connection(client, phase, options, monkeypatch)
    assert processed == ["validation-job"]
    assert calls[0][1]["json"]["model_id"] == "selected-model"
    assert calls[0][1]["json"]["api_key"] == "private-test-token"
    assert phase.cfg.local_chat_endpoint == options["generator_endpoint"]


@pytest.mark.parametrize("version", [3, 4])
def test_selected_application_plan_preserves_frozen_cases_and_pdf_values(tmp_path, version):
    path = Path(__file__).parents[2] / "scripts/evaluate_selected_workflow.py"
    spec = importlib.util.spec_from_file_location("selected_workflow", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    corpus = tmp_path / "corpus"
    (export if version == 3 else reflection_corpus_v4.export)(corpus)
    cases, sha = module.frozen_cases(corpus)
    assert len(sha) == 64
    counts = {
        "frozen_document": 24,
        "supplied_reference": 12,
        "long_passage": 2,
        "abstention": 5,
        "prompt_injection": 1,
        "general_arithmetic": 2,
    }
    if version == 4:
        counts.update(heldout_arithmetic=24, long_passage=4, abstention=6)
    assert Counter(case["category"] for case in cases) == counts
    for case in cases:
        if case["text"] is None:
            continue
        with pymupdf.open(stream=module.pdf_bytes(case), filetype="pdf") as pdf:
            if case["expected_grounding"] == "supported":
                assert case["expected_value"] in pdf[0].get_text()
            assert pdf.page_count == (2 if case.get("table") else 1)


def test_v4_addition_pairs_are_frozen_and_disjoint(tmp_path):
    corpus = tmp_path / "corpus"
    reflection_corpus_v4.export(corpus)
    manifest = Path(__file__).parent / "fixtures/phase6_reflection_corpus_v4_manifest.json"
    assert (corpus / "manifest.json").read_bytes() == manifest.read_bytes()
    samples, _ = load_corpus(corpus)
    pairs = {(1, 1), (14, 27)}
    counts = Counter()
    for sample in samples:
        match = re.fullmatch(r"What is (\d+) plus (\d+)\?", sample.question)
        if not match:
            continue
        a, b = map(int, match.groups())
        pair = tuple(sorted((a, b)))
        assert pair not in pairs
        assert sample.episodes[0].target == "[NoRetrieve]"
        assert sample.episodes[1].target == f"{a + b}[EndSegment][IsUSE:5]"
        assert sample.episodes[2].segments == [str(a + b)]
        pairs.add(pair)
        counts[sample.split] += 1
    assert counts == {"train": 64, "validation": 16, "test": 24}
