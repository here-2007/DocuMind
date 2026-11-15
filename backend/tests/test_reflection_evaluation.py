"""The synthetic evaluator rejects fabricated full-support claims."""

import importlib.util
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from documind.reflection_corpus_v3 import export


def test_conditional_scores_do_not_modify_the_selected_real_bundle(tmp_path):
    selected = os.getenv("DOCUMIND_TEST_REFLECTION_BUNDLE")
    if not selected:
        if os.getenv("DOCUMIND_REQUIRE_REFLECTION_BUNDLE") == "1":
            pytest.fail("The conditional evaluation requires the trained CI pilot")
        pytest.skip("Set DOCUMIND_TEST_REFLECTION_BUNDLE for actual conditional scoring")
    from documind.reflection import digest
    from documind.reflection_data import load_corpus, write_corpus
    from documind.reflection_pilot import create_corpus
    from documind.reflection_review import checked_bundle

    bundle, corpus = Path(selected), tmp_path / "corpus"
    write_corpus(corpus, *create_corpus())
    sha = digest((bundle / "manifest.json").read_bytes())
    before = checked_bundle(bundle, sha)
    script = Path(__file__).parents[2] / "scripts/evaluate_reflection_v3.py"
    spec = importlib.util.spec_from_file_location("conditional_evaluation", script)
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    result = evaluator.conditional(bundle, sha, corpus, "test")
    samples, corpus_sha = load_corpus(corpus)
    assert {row["sample"] for row in result["predictions"]} == {
        sample.sample_id for sample in samples if sample.split == "test"
    }
    assert all(row["split"] == "test" for row in result["predictions"])
    assert result["bundle_manifest_sha256"] == sha
    assert result["corpus_manifest_sha256"] == corpus_sha
    assert result["runtime_capability"]["supported_modes"] == ["reflection_pilot"]
    assert checked_bundle(bundle, sha) == before
    manifest = json.loads((corpus / "manifest.json").read_bytes())
    manifest["generation_method"] = "different corpus identity"
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="differs from the selected checkpoint"):
        evaluator.conditional(bundle, sha, corpus, "test")


@pytest.mark.parametrize("shared_runtime", [False, True])
def test_evaluator_counts_wrong_values_and_irrelevant_full_support(
    tmp_path, monkeypatch, shared_runtime
):
    script = Path(__file__).parents[2] / "scripts/evaluate_reflection_v3.py"
    spec = importlib.util.spec_from_file_location("reflection_evaluation", script)
    evaluator = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(evaluator)
    corpus = tmp_path / "corpus"
    export(corpus)

    def decode(step, cancellation):
        context = step.context
        if step.state == "DECIDE":
            required = not context["question"].startswith("Repeat this")
            labels = {"decision": "[Retrieve]" if required else "[NoRetrieve]"}
            text = ""
        elif not context["evidence"]:
            text = context["question"].removeprefix("Repeat this supplied reference: ").rstrip(".")
            labels = {"utility": "[IsUSE:5]"}
        else:
            quote = context["evidence"][0]["quote"]
            field = next(
                field for field in ("due date", "currency", "total") if field in context["question"]
            )
            label = "Grand total" if field == "total" else field.capitalize()
            text = re.search(rf"^{label}: (.+)$", quote, re.M)[1]
            if field == "currency":
                text = "JPY"
            labels = {"relevance": "[IsREL:Yes]", "support": "[IsSUP:Fully]"}
        return SimpleNamespace(text=text, finish_reason="complete", labels=labels)

    monkeypatch.setattr(evaluator, "load", lambda *a, **kw: (None, decode))
    monkeypatch.setattr(
        evaluator,
        "make_step",
        lambda cap, run, state, context, budget, deadline: SimpleNamespace(
            state=state, context=context
        ),
    )
    monkeypatch.setattr(evaluator, "check_result", lambda actual, *args: actual.labels)
    if shared_runtime:
        def unavailable(*args, **kwargs):
            raise AssertionError("A shared loaded runtime must not load another model")
        monkeypatch.setattr(evaluator, "load", unavailable)
    result = evaluator.evaluate(
        tmp_path, "unused-model-in-unit-test", corpus, "test",
        runtime=(SimpleNamespace(manifest_sha256="unused-model-in-unit-test"), decode)
        if shared_runtime else None,
    )
    assert result["summary"]["supported_candidate"] == {
        "correct": 16,
        "n": 24,
        "false_full_support": 8,
    }
    assert result["summary"]["irrelevant_candidate"] == {
        "correct": 0,
        "n": 24,
        "false_full_support": 24,
    }
    assert result["summary"]["general_candidate"]["correct"] == 12
    assert result["summary"]["initial_required"]["correct"] == 24
    if shared_runtime:
        with pytest.raises(ValueError, match="different checkpoint"):
            evaluator.evaluate(
                tmp_path, "another-checkpoint", corpus, "test",
                runtime=(SimpleNamespace(manifest_sha256="unused-model-in-unit-test"), decode),
            )
