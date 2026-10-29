# SPDX-License-Identifier: Apache-2.0
"""Offline comparison of advisory Decisions reports with independent labels."""
from __future__ import annotations

import argparse
from pathlib import Path

from sprite_gen.qa.decisions import CHOICES, number, read_json, validate_sources


def _metrics(rows, key):
    matrix = {truth: {prediction: 0 for prediction in CHOICES} for truth in CHOICES}
    for row in rows:
        matrix[row["expected"]][row[key]] += 1
    defects = sum(matrix["defect"].values())
    clears = sum(matrix["clear"].values())
    decided = sum(row[key] != "unverified" for row in rows)
    return {"checks": len(rows), "confusion": matrix,
            "defect_miss_rate": matrix["defect"]["clear"] / defects if defects else None,
            "false_alarm_rate": matrix["clear"]["defect"] / clears if clears else None,
            "abstention_rate": 1 - decided / len(rows) if rows else None,
            "defect_detection_rate": matrix["defect"]["defect"] / defects if defects else None,
            "unsupported_clear_count": matrix["unverified"]["clear"]}


def evaluate(dataset, base):
    if dataset.get("kind") != "sprite-gen-decision-eval" or dataset.get("version") != 1:
        raise ValueError("expected sprite-gen-decision-eval version 1")
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ValueError("evaluation needs nonempty labeled cases")
    rows, groups, seen_cases, seen_reports = [], {}, set(), set()
    timings, baseline_timings, tokens = [], [], []
    baseline_coverage = True
    settings = set()
    for case in cases:
        cid, character, split = case["id"], case["character"], case["split"]
        if not all(isinstance(v, str) and v.strip() for v in (cid, character, case.get("labeler"))):
            raise ValueError("case id, character and independent labeler are required")
        if cid in seen_cases or split not in ("calibration", "test"):
            raise ValueError("duplicate case or invalid split")
        seen_cases.add(cid)
        if character in groups and groups[character] != split:
            raise ValueError("a character cannot appear in both calibration and test splits")
        groups[character] = split
        report_path = (base / case["report"]).resolve()
        report = read_json(report_path)
        if report.get("kind") != "sprite-gen-decision-review" or report.get("status") not in ("completed", "unverified"):
            raise ValueError("evaluate only completed or failed API reviews, never prepared requests")
        binding = report["binding"]
        settings.add((binding["model"], binding["rubric_version"], binding["threshold"]))
        if len(settings) > 1:
            raise ValueError("evaluate one model, rubric and threshold at a time")
        validate_sources(binding)
        if case.get("request_sha256") != binding["request_sha256"]:
            raise ValueError("labels are not bound to this request; copy its request_sha256 after independent labeling")
        if binding["request_sha256"] in seen_reports:
            raise ValueError("duplicate request evidence in evaluation")
        seen_reports.add(binding["request_sha256"])
        expected = case.get("expected")
        if not isinstance(expected, dict) or not expected or not set(expected) <= set(report["checks"]):
            raise ValueError("labels must identify existing report questions")
        baseline = case.get("baseline", {})
        for name, truth in expected.items():
            prediction = report["checks"][name]["status"]
            if truth not in CHOICES or prediction not in CHOICES:
                raise ValueError("labels and results must be clear, defect or unverified")
            row = {"case": cid, "character": character, "split": split, "check": name,
                   "expected": truth, "decisions": prediction}
            if name in baseline:
                if baseline[name] not in CHOICES:
                    raise ValueError("invalid baseline label")
                row["baseline"] = baseline[name]
            else:
                baseline_coverage = False
            rows.append(row)
        if split == "test":
            if report.get("elapsed_seconds") is not None:
                timings.append(number(report["elapsed_seconds"], "elapsed_seconds"))
            if case.get("baseline_review_seconds") is not None:
                baseline_timings.append(number(case["baseline_review_seconds"], "baseline_review_seconds"))
            usage = report.get("usage")
            if isinstance(usage, dict) and usage.get("input_tokens") is not None:
                tokens.append(number(usage["input_tokens"], "input_tokens"))
    results = {}
    for split in ("calibration", "test"):
        subset = [r for r in rows if r["split"] == split]
        results[split] = {"decisions": _metrics(subset, "decisions")}
        if subset and all("baseline" in r for r in subset):
            results[split]["baseline"] = _metrics(subset, "baseline")
            results[split]["defect_miss_rate_delta"] = (
                results[split]["decisions"]["defect_miss_rate"] - results[split]["baseline"]["defect_miss_rate"]
                if results[split]["decisions"]["defect_miss_rate"] is not None else None)
    test_cases = sum(c["split"] == "test" for c in cases)
    return {"kind": "sprite-gen-decision-evaluation", "version": 1, "cases": len(cases),
            "independent_labels_are_user_supplied": True, "baseline_complete": baseline_coverage,
            "results": results, "rows": rows,
            "review_timing": {"decisions_seconds": sum(timings) if len(timings) == test_cases and test_cases else None,
                              "baseline_seconds": sum(baseline_timings) if len(baseline_timings) == test_cases and test_cases else None,
                              "scope": "recorded review calls only; excludes preparation, generation, repair and human review"},
            "test_input_tokens": sum(tokens) if len(tokens) == test_cases and test_cases else None,
            "end_to_end_improvement": "unmeasured; requires a separate matched production experiment",
            "limitations": "Small, correlated frame samples are not independent animation trials. Synthetic fixture scores validate plumbing only. No automatic thresholds, approvals or retries."}


def add_arguments(parser: argparse.ArgumentParser):
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="new evaluation JSON; never overwrites")


def run(*, dataset, output):
    try:
        path, destination = Path(dataset).resolve(), Path(output).resolve()
        result = evaluate(read_json(path), path.parent)
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive create avoids replacing labels, evidence or an earlier evaluation.
        import json
        with destination.open("x", encoding="utf-8") as handle:
            json.dump(result, handle, ensure_ascii=False, allow_nan=False, indent=2)
            handle.write("\n")
        print(str(destination))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        raise SystemExit(f"evaluate-decisions: {exc}") from None
