"""Check and summarize frozen Phase 8 result directories without inference."""

import argparse
import csv
import json
import os
import shutil
from collections import Counter, defaultdict
from math import log2, sqrt
from pathlib import Path
from random import Random
from statistics import mean, median

import pymupdf
from documind.reflection import canonical, digest
from documind.runtime_protocol import Capabilities, Result
from evaluate_phase8 import (
    BASELINE_PROMPT,
    RETRIEVAL_SCOPE,
    ROOT,
    RUN_SCHEMA,
    TOKEN_BUDGET,
    VARIANTS,
    check_profile,
    raw_candidate_correct,
    raw_candidates,
    score,
    source_hashes,
    summarize,
)
from phase8_dataset import load

ARMS = {"naive_rag", "trained_self_rag", "without_support_critic"}
HISTORICAL = ROOT / "docs/validation/phase8-benchmark-2026-11-15.json"


def proportion(successes, total):
    if not total:
        return {"numerator": successes, "denominator": 0, "value": None, "wilson_95": None}
    value = successes / total
    z = 1.959963984540054
    denominator = 1 + z * z / total
    center = (value + z * z / (2 * total)) / denominator
    width = z * sqrt(value * (1 - value) / total + z * z / (4 * total * total)) / denominator
    return {
        "numerator": successes,
        "denominator": total,
        "value": value,
        "wilson_95": [max(0, center - width), min(1, center + width)],
    }


def paired(rows, arm, reference):
    groups = defaultdict(list)
    counts = Counter()
    for row in rows:
        if arm not in row["arms"] or reference not in row["arms"]:
            continue
        a = int(row["arms"][arm]["correct"])
        b = int(row["arms"][reference]["correct"])
        groups[row["case"]["group"]].append(a - b)
        counts[a, b] += 1
    if not counts:
        return None
    clusters = list(groups.values())
    rng = Random(868267)
    differences = []
    for _ in range(4000):
        sample = rng.choices(clusters, k=len(clusters))
        differences.append(sum(map(sum, sample)) / sum(map(len, sample)))
    differences.sort()
    return {
        "paired_cases": sum(counts.values()),
        "groups": len(clusters),
        "both_correct": counts[1, 1],
        "both_incorrect": counts[0, 0],
        "variant_only_correct": counts[1, 0],
        "reference_only_correct": counts[0, 1],
        "accuracy_difference": (counts[1, 0] - counts[0, 1]) / sum(counts.values()),
        "group_bootstrap_95_interval": [differences[100], differences[3899]],
    }


def read_run(paths, manifest, *, source_root=ROOT, historical=False, require_cases=True):
    archive = json.loads(HISTORICAL.read_text()) if historical else None
    archived_runs = (
        {
            item["run"]: item
            for cohort in archive["cohorts"].values()
            for item in cohort["measurements"]
        }
        if archive
        else {}
    )
    expected_code = None if historical else source_hashes(source_root)
    _, documents, cases = load(manifest)
    expected = {case["id"]: case for case in cases}
    expected_sha = digest(manifest.read_bytes())
    loaded, measurements = {}, []
    run_identity = profile_identity = run_kind = None
    for path in paths:
        config_file = path / "run_config.json"
        raw_path = path / "results_raw.jsonl"
        config = json.loads(config_file.read_text())
        config_sha, raw_sha = digest(config_file.read_bytes()), digest(raw_path.read_bytes())
        if historical:
            known = archived_runs.get(path.name, {})
            if (config_sha, raw_sha) != (known.get("config_sha256"), known.get("raw_sha256")):
                raise ValueError("Run does not match the published historical artifact")
            kind, retrieval_scope = "paired", "historical_workspace_wide"
        else:
            kind, retrieval_scope = config.get("run_kind"), config.get("retrieval_scope")
            if (
                config.get("schema") != RUN_SCHEMA
                or kind not in {"paired", "retrieval_only"}
                or retrieval_scope != RETRIEVAL_SCOPE
            ):
                raise ValueError("Invalid run schema, kind or retrieval scope")
            if (
                config["source_sha256"] != expected_code
                or source_hashes(path / "source") != expected_code
            ):
                raise ValueError("Run source does not match the trusted implementation")
        identity = (
            kind,
            retrieval_scope,
            config["source_sha256"],
            config["retrieval"],
            config["token_budget_per_arm"],
            config["baseline_prompt"],
            config["temperature"],
            config["concurrency"],
            config["critic_ablation"],
        )
        if run_identity is not None and identity != run_identity:
            raise ValueError("Run shards used distinct implementation or controls")
        run_identity, run_kind = identity, kind
        if not historical and (
            config["token_budget_per_arm"] != TOKEN_BUDGET
            or config["baseline_prompt"] != BASELINE_PROMPT
            or config["temperature"] != 0
            or config["concurrency"] != 1
        ):
            raise ValueError("Invalid paired evaluation controls")
        case_ids = config["case_ids"]
        if not case_ids or len(case_ids) != len(set(case_ids)) or set(case_ids) - expected.keys():
            raise ValueError("Invalid declared case selection")
        if set(config["source_map"]) != set(documents) or len(
            set(config["source_map"].values())
        ) != len(documents):
            raise ValueError("Invalid frozen source map")
        if config["frozen_manifest_sha256"] != expected_sha:
            raise ValueError("Run has a different frozen manifest")
        if set(config["source_pdf_sha256"]) != set(documents):
            raise ValueError("Run used different PDF membership")
        for doc_id, sha in config["source_pdf_sha256"].items():
            pdf = path / "pdfs" / (doc_id + ".pdf")
            if digest(pdf.read_bytes()) != sha:
                raise ValueError("Run PDF digest differs from the frozen config")
            with pymupdf.open(pdf) as source:
                expected_lines = [page.splitlines() for page in documents[doc_id]["pages"]]
                if len(source) != len(expected_lines) or any(
                    [line for line in page.get_text().splitlines() if line.strip()]
                    != [line for line in expected if line.strip()]
                    for page, expected in zip(source, expected_lines, strict=True)
                ):
                    raise ValueError("Run PDF text differs from the frozen document")
        environment_path = path / "environment.json"
        cap = None
        if kind == "paired":
            if not environment_path.is_file():
                raise ValueError("Paired evaluation is missing runtime evidence")
            environment_bytes = environment_path.read_bytes()
            environment = json.loads(environment_bytes)
            if historical:
                if environment["profile"] != archive["runtime_profiles"][path.name]:
                    raise ValueError(
                        "Historical runtime profile differs from the published evidence"
                    )
            elif digest(environment_bytes) != config.get("environment_sha256"):
                raise ValueError("Environment digest differs from the run config")
            profile = environment["profile"]
            cap = Capabilities.model_validate(environment["capability"])
            check_profile(profile, cap)
            identity = (profile, cap.model_dump(mode="json", exclude={"runtime_instance_id"}))
            if profile_identity is not None and identity != profile_identity:
                raise ValueError("Model shards used distinct profiles or capabilities")
            profile_identity = identity
        elif environment_path.exists() or config.get("environment_sha256") is not None:
            raise ValueError("Retrieval-only run declares model runtime evidence")
        for line in raw_path.read_text().splitlines():
            row = json.loads(line)
            case = row["case"]
            key = case["id"]
            if key in loaded or case != expected.get(key) or key not in config["case_ids"]:
                raise ValueError("Run has a duplicate or modified case")
            if set(row["arms"]) != (ARMS if kind == "paired" else set()):
                raise ValueError("Model evaluation is missing an arm")
            if set(row.get("retrieval", {})) != (set(VARIANTS) if case["gold"] else set()):
                raise ValueError("Retrieval ablations do not match the annotated case")
            source = config["source_map"][case["gold"]["document"]] if case["gold"] else None
            if row["gold_document_id"] != source:
                raise ValueError("Gold document ID differs from the stored source map")
            if case["gold"] and not historical:
                scope = {config["source_map"][doc] for doc in case["scope"]}
                for variant in row["retrieval"].values():
                    hits = variant["hits"]
                    matches = [i for i, hit in enumerate(hits, 1) if hit["relevant"]]
                    if (
                        len(hits) != variant["returned"]
                        or len(hits) > 5
                        or variant["matches"] != len(matches)
                        or variant["rank"] != next(iter(matches), None)
                        or any(hit["document"] not in scope for hit in hits)
                        or any(
                            hit["relevant"]
                            and (hit["document"], hit["page"]) != (source, case["gold"]["page"])
                            for hit in hits
                        )
                    ):
                        raise ValueError("Retrieval evidence differs from the frozen scope or rank")
            for arm, result in row["arms"].items():
                if result["status"] not in {"ok", "error"}:
                    raise ValueError("Invalid model arm status")
                if arm != "naive_rag":
                    for step in result["wire"]:
                        wire = Result.model_validate(step["result"])
                        if (wire.runtime_instance_id, wire.manifest_sha256) != (
                            cap.runtime_instance_id,
                            cap.manifest_sha256,
                        ):
                            raise ValueError("Model step differs from the shard runtime identity")
                candidates = raw_candidates(
                    arm, result["wire"], result["trace"], complete=result["status"] == "ok"
                )
                if not historical and candidates != result["raw_candidates"]:
                    raise ValueError("Raw candidates differ from the recorded model outputs")
                result["raw_candidates"] = candidates
                if result["status"] != "ok":
                    if (
                        result.get("correct") is not False
                        or result.get("citation")
                        or not result.get("error")
                    ):
                        raise ValueError("A failed model arm claimed a verified answer")
                    continue
                citation = result["citation"]
                checked = score(
                    case, source, result["answer"], citation, result["citation_resolves"]
                )
                if checked != (result["correct"], result["citation_matches_gold"]):
                    raise ValueError("Stored scores differ from recomputed scores")
                raw_correct = raw_candidate_correct(case, candidates)
                if historical:
                    result["committed_text"] = (
                        result.pop("candidate") if arm != "naive_rag" else None
                    )
                    result["raw_candidate_correct"] = raw_correct
                elif raw_correct != result["raw_candidate_correct"]:
                    raise ValueError("Raw candidate score differs from the generated answer")
                if citation and citation["document_id"] not in {
                    config["source_map"][doc] for doc in case["scope"]
                }:
                    raise ValueError("A citation is outside the frozen case scope")
                # Confirm every baseline citation's text was visible in the one supplied prompt.
                if (
                    arm == "naive_rag"
                    and citation
                    and not any(
                        citation["quote"] in window for window in result["wire"][0]["evidence"]
                    )
                ):
                    raise ValueError("Baseline cited a span absent from its delivered prompt")
            row["source_names"] = {config["source_map"][doc]: doc + ".pdf" for doc in case["scope"]}
            loaded[key] = row
        measurements.append(
            {
                "run": path.name,
                "raw_sha256": raw_sha,
                "config_sha256": config_sha,
                "environment_sha256": digest(environment_path.read_bytes()) if cap else None,
            }
        )
    rows = [loaded[key] for key in expected if key in loaded]
    if not run_identity or (require_cases and not rows):
        raise ValueError("No measured cases to report")
    metrics = summarize(rows)
    metrics["run_kind"] = run_kind
    metrics["retrieval_scope"] = run_identity[1]
    metrics["source_sha256"] = run_identity[2]
    metrics["model_identity"] = profile_identity
    for arm, summary in metrics["qa"].items():
        arms = [(row["case"], row["arms"][arm]) for row in rows if arm in row["arms"]]
        valid = [result for _, result in arms if result["status"] == "ok"]
        summary["mandatory_n"] = sum(bool(case["scope"]) for case, _ in arms)
        summary["mandatory_zero_retrieval"] = sum(
            bool(case["scope"]) and not result["timings"].get("retrieval_calls")
            for case, result in arms
        )
        summary["eligible_correct_skips"] = sum(
            not case["scope"] and result["correct"] and not result["timings"].get("retrieval_calls")
            for case, result in arms
        )
        summary["rates"] = {
            "accuracy": proportion(summary["correct"], summary["n"]),
            "citation_precision": proportion(
                summary["citation_matches"], summary["citations_emitted"]
            ),
            "citation_recall": proportion(summary["citation_matches"], summary["gold_citations"]),
            "skip_accuracy": proportion(summary["correct_skips"], summary["retrieval_skips"]),
            "eligible_skip_rate": proportion(summary["eligible_skips"], summary["eligible_n"]),
            "abstention_accuracy": proportion(
                summary["abstentions_correct"], summary["unanswerable"]
            ),
        }
        decisions = summary["decision_confusion"]
        if decisions:
            decision = {
                "true_positive": decisions.get("required:[Retrieve]", 0),
                "false_negative": sum(
                    n
                    for label, n in decisions.items()
                    if label.startswith("required:") and label != "required:[Retrieve]"
                ),
                "true_negative": decisions.get("eligible:[NoRetrieve]", 0),
                "false_positive": decisions.get("eligible:[Retrieve]", 0),
                "ineligible_initial_actions": sum(
                    n
                    for label, n in decisions.items()
                    if label in {"eligible:[Finish]", "eligible:[Continue]"}
                ),
            }
            tp, fp, fn = (
                decision[name] for name in ("true_positive", "false_positive", "false_negative")
            )
            decision["precision"] = tp / (tp + fp) if tp + fp else None
            decision["recall"] = tp / (tp + fn) if tp + fn else None
            decision["f1"] = 2 * tp / (2 * tp + fp + fn) if 2 * tp + fp + fn else None
            decision["strict_action_accuracy"] = proportion(
                tp + decision["true_negative"], sum(decisions.values())
            )
            summary["raw_retrieval_decision"] = decision
        summary["policy_overrides"] = sum(
            event["type"] == "reflection_token" and event.get("policy_override_reason") is not None
            for result in valid
            for event in result["trace"]
        )
        if valid and arm != "without_support_critic":
            summary["latency_mean_seconds"] = mean(result["elapsed_seconds"] for result in valid)
            summary["model_median_seconds"] = median(
                result["timings"].get("model_seconds", 0) for result in valid
            )
            summary["retrieval_median_seconds"] = median(
                result["timings"].get("retrieval_seconds", 0) for result in valid
            )
    for variant, summary in metrics["retrieval"].items():
        ranks = [row["retrieval"][variant]["rank"] for row in rows if "retrieval" in row]
        summary["ndcg_at_5_one_gold_span"] = sum(
            1 / log2(rank + 1) for rank in ranks if rank
        ) / len(ranks)
    metrics["expected_cases"] = len(expected)
    metrics["paired_effects"] = {
        "trained_minus_baseline": paired(rows, "trained_self_rag", "naive_rag"),
        "trained_minus_no_support": paired(rows, "trained_self_rag", "without_support_critic"),
    }
    metrics["missing_case_ids"] = [key for key in expected if key not in loaded]
    metrics["frozen_manifest_sha256"] = expected_sha
    metrics["measurements"] = measurements
    return rows, metrics


def write_tables(output, results, rows):
    with (output / "results_summary.csv").open("x", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=(
                "arm",
                "n",
                "errors",
                "correct",
                "citations_emitted",
                "citation_matches",
                "gold_citations",
                "latency_median_seconds",
                "latency_p95_seconds",
                "eligible_n",
                "eligible_skips",
                "correct_skips",
            ),
            extrasaction="ignore",
        )
        writer.writeheader()
        for arm, metrics in results["qa"].items():
            writer.writerow({"arm": arm, **metrics})
    with (output / "answers.csv").open("x", newline="") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=(
                "case_id",
                "group",
                "category",
                "arm",
                "status",
                "correct",
                "answer",
                "committed_text",
                "raw_candidates",
                "raw_candidate_correct",
                "citation_matches_gold",
                "elapsed_seconds",
                "error",
            ),
            extrasaction="ignore",
        )
        writer.writeheader()
        for row in rows:
            case = row["case"]
            for arm, result in row["arms"].items():
                writer.writerow(
                    {
                        **case,
                        **result,
                        "case_id": case["id"],
                        "arm": arm,
                        "raw_candidates": canonical(result["raw_candidates"]),
                    }
                )


def review_packet(rows, count):
    """Stratify by case label and conceal primary arm order per case."""
    rng = Random(124840)
    categories = defaultdict(list)
    for row in rows:
        if row["arms"]:
            categories[row["case"]["category"]].append(row)
    if sum(map(len, categories.values())) < count:
        raise ValueError("Requested more review cases than paired outcomes")
    for samples in categories.values():
        rng.shuffle(samples)
    packet, key = [], {}
    while len(packet) < count:
        for category in sorted(categories):
            if not categories[category] or len(packet) == count:
                continue
            row = categories[category].pop()
            options = ["naive_rag", "trained_self_rag"]
            rng.shuffle(options)
            case = row["case"]
            key[case["id"]] = dict(zip(("A", "B"), options, strict=True))
            answers = []
            for label, arm in zip(("A", "B"), options, strict=True):
                result = row["arms"][arm]
                citation = result.get("citation")
                answers.append(
                    {
                        "label": label,
                        "text": result.get("answer"),
                        "citation": {
                            "file": row["source_names"][citation["document_id"]],
                            "page": citation["page_number"],
                            "quote": citation["quote"],
                        }
                        if citation
                        else None,
                        "status": result["status"],
                    }
                )
            packet.append(
                {
                    "id": case["id"],
                    "question": case["question"],
                    "sources": [identifier + ".pdf" for identifier in case["scope"]],
                    "answers": answers,
                }
            )
    return packet, key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--run", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--review-samples", type=int, default=0)
    parser.add_argument(
        "--source-root", type=Path, default=ROOT, help="Trusted source checkout for v2 runs"
    )
    parser.add_argument(
        "--historical", action="store_true", help="Rescore the published November 15 archives"
    )
    args = parser.parse_args()
    rows, result = read_run(
        args.run, args.manifest, source_root=args.source_root, historical=args.historical
    )
    os.umask(0o077)
    args.output.mkdir(mode=0o700)
    write_tables(args.output, result, rows)
    (args.output / "report.json").write_text(json.dumps(result, indent=2) + "\n")
    if args.review_samples > 0:
        packet, key = review_packet(rows, args.review_samples)
        (args.output / "blind-review.json").write_text(json.dumps(packet, indent=2) + "\n")
        (args.output / "blind-review-key.json").write_text(json.dumps(key, indent=2) + "\n")
        originals = args.output / "pdfs"
        originals.mkdir(mode=0o700)
        for name in sorted({name for case in packet for name in case["sources"]}):
            source = next(run / "pdfs" / name for run in args.run if (run / "pdfs" / name).exists())
            shutil.copyfile(source, originals / name)
        with (args.output / "review-verdicts.csv").open("x", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(["case_id", "reviewer", "A_verdict", "B_verdict", "note"])
            writer.writerows([case["id"], "", "", "", ""] for case in packet)
    print(f"Checked {result['cases']} of {result['expected_cases']} frozen cases")
    return 2 if result["missing_case_ids"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
