#!/usr/bin/env python3
"""Bounded recall performance check for IWTT v0.2.

Mission requirement: measure recall at roughly 1,000 and 10,000 thoughts, and
prove that IWTT never needs the whole canonical graph in memory to answer.

Everything here is measured, never estimated.  The script prints a JSON report and
exits non-zero if a published budget is exceeded, so it can be used as a release
gate rather than a curiosity.

    python3 scripts/benchmark_recall.py --count 1000
    python3 scripts/benchmark_recall.py --count 10000 --queries 300
    python3 scripts/benchmark_recall.py --count 1000 --data-dir /tmp/keep --keep

The synthetic corpus is generated from a fixed seed, so two runs on the same
machine produce the same vault and the same query list.  That matters: a benchmark
whose input changes between runs cannot be compared with itself.

No network access, no AI provider, no third-party package.  Standard library only,
like the rest of IWTT.
"""
from __future__ import annotations

import argparse
import gc
import json
import random
import resource
import shutil
import statistics
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from iwtt.recall.config import DEFAULT_CONFIG          # noqa: E402
from iwtt.store import Vault                           # noqa: E402

#: Published budgets.  Exceeding one fails the run rather than being reported
#: politely, because a recall feature that degrades silently stops being a way
#: back to a memory and starts being a reason to give up.
BUDGETS = {
    1_000: {"buildSeconds": 60.0, "recallP95Ms": 250.0, "recallMaxMs": 1500.0,
            "peakRssMb": 600.0},
    10_000: {"buildSeconds": 300.0, "recallP95Ms": 600.0, "recallMaxMs": 4000.0,
             "peakRssMb": 1200.0},
}
FALLBACK_BUDGET = {"buildSeconds": 600.0, "recallP95Ms": 1500.0, "recallMaxMs": 8000.0,
                   "peakRssMb": 2000.0}

SUBJECTS = [
    "the enemy", "the player", "a dungeon", "the boss", "an npc", "the save file",
    "a weapon", "the camera", "a puzzle", "the inventory", "a merchant", "the map",
    "a companion", "the ending", "a trap", "the difficulty", "a seed", "the UI",
]
VERBS = [
    "remembers", "adapts to", "punishes", "teaches", "hides", "reveals", "tracks",
    "rewrites", "borrows", "anticipates", "escalates", "softens", "mirrors", "delays",
]
OBJECTS = [
    "how the player fights", "the last mistake", "a repeated pattern", "the opening loop",
    "a chosen weapon", "the difficulty curve", "an abandoned idea", "the turn order",
    "a procedural floor", "the respawn timer", "a saved state", "the fog of war",
    "an item combination", "the boss phase", "a dialogue branch", "the economy",
]
QUALIFIERS = [
    "and that should stay deterministic", "which felt unfair in testing",
    "so the run never repeats itself", "and it must survive a restart",
    "but only after the tutorial", "which contradicts the earlier note",
    "and needs a telegraph first", "so the player can learn it",
    "recorded before the refactor", "still unverified",
]
TYPES = ["THOUGHT", "EXPERIMENT", "INVENTION", "PROJECT"]
TAG_POOL = [
    "combat", "enemy ai", "balance", "procedural", "recall", "architecture",
    "save system", "onboarding", "difficulty", "narrative", "tooling", "performance",
]


def make_thought(index: int, rng: random.Random) -> tuple[str, str, list[str]]:
    """One synthetic memory: a sentence a person might actually have written."""
    subject = rng.choice(SUBJECTS)
    verb = rng.choice(VERBS)
    obj = rng.choice(OBJECTS)
    text = f"{subject} {verb} {obj}".capitalize()
    if rng.random() < 0.7:
        text += f", {rng.choice(QUALIFIERS)}"
    if rng.random() < 0.35:
        text += f" (note {index})"
    thought_type = rng.choice(TYPES) if rng.random() < 0.25 else "THOUGHT"
    tags = rng.sample(TAG_POOL, k=rng.choice([0, 1, 1, 2]))
    return text, thought_type, tags


def make_queries(rng: random.Random, count: int) -> list[str]:
    """A realistic query mix: exact, paraphrased, misspelled, vague, and hopeless."""
    queries: list[str] = []
    while len(queries) < count:
        kind = rng.choice(["exact", "partial", "typo", "vague", "absent", "phrase"])
        subject, verb, obj = rng.choice(SUBJECTS), rng.choice(VERBS), rng.choice(OBJECTS)
        if kind == "exact":
            queries.append(f"{subject} {verb} {obj}")
        elif kind == "partial":
            queries.append(f"{subject} {obj}")
        elif kind == "typo":
            blob = f"{subject} {verb} {obj}"
            position = rng.randrange(3, len(blob))
            queries.append(blob[:position] + blob[position:].replace(blob[position], "z", 1))
        elif kind == "vague":
            queries.append(rng.choice([
                "something about difficulty feeling unfair",
                "that note about remembering mistakes",
                "what did I write about determinism",
                "the procedural thing from before",
                "an abandoned idea about turns",
            ]))
        elif kind == "absent":
            queries.append(rng.choice([
                "quantum chromodynamics renormalisation",
                "medieval tax law in Burgundy",
                "sourdough hydration percentages",
            ]))
        else:
            queries.append(f'"{subject} {verb}"')
    return queries


def peak_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Linux reports kilobytes, macOS bytes.  Normalise so the number means MB.
    return usage / 1024.0 if sys.platform != "darwin" else usage / (1024.0 * 1024.0)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = min(len(ordered) - 1, max(0, int(round(pct / 100.0 * (len(ordered) - 1)))))
    return ordered[position]


def build_vault(data_dir: Path, count: int, seed: int) -> tuple[Vault, dict]:
    rng = random.Random(seed)
    vault = Vault(data_dir / "thought-vault.sqlite3")
    vault.initialize()
    started = time.perf_counter()
    created = 0
    previous_id = ""
    for index in range(count):
        text, thought_type, tags = make_thought(index, rng)
        thought = vault.create_thought(text, thought_type, f"Note {index:06d}", tags)
        created += 1
        # A sparse relationship web: roughly one edge per four thoughts, so the
        # graph is real without becoming a hairball that flatters the numbers.
        # Ids are tracked locally — re-listing the vault per edge would make the
        # insertion time quadratic and measure the harness instead of IWTT.
        if previous_id and index % 4 == 0:
            vault.create_relationship(previous_id, thought["id"], "RELATED_TO")
        previous_id = thought["id"]
    inserted = time.perf_counter() - started

    started = time.perf_counter()
    build_report = vault.rebuild_recall_indexes(full=True)
    indexed = time.perf_counter() - started
    return vault, {
        "thoughts": created,
        "insertSeconds": round(inserted, 3),
        "indexBuildSeconds": round(indexed, 3),
        "indexReport": {
            key: build_report[key]
            for key in ("scope", "documents", "tokenRows", "trigramRows", "terms",
                        "clusters", "durationMs")
            if key in build_report
        },
    }


def run_benchmark(count: int, query_count: int, data_dir: Path, seed: int) -> dict:
    if data_dir.exists():
        shutil.rmtree(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    gc.collect()
    rss_before = peak_rss_mb()
    vault, build = build_vault(data_dir, count, seed)
    rss_after_build = peak_rss_mb()

    stats = vault.stats()
    rng = random.Random(seed + 1)
    queries = make_queries(rng, query_count)

    latencies: list[float] = []
    candidates: list[int] = []
    states: dict[str, int] = {}
    result_counts: list[int] = []
    first_rankings: dict[str, list[str]] = {}
    graph_timings: list[float] = []

    for query in queries:
        started = time.perf_counter()
        outcome = vault.recall(query)
        latencies.append((time.perf_counter() - started) * 1000)
        payload = outcome.to_dict()
        candidates.append(int(payload.get("candidatesExamined", 0)))
        states[outcome.state] = states.get(outcome.state, 0) + 1
        result_counts.append(len(outcome.results))
        first_rankings[query] = [r.thought_id for r in outcome.results]
        graph = payload.get("timingsMs", {}).get("graphExpansion")
        if graph is not None:
            graph_timings.append(float(graph))

    # Determinism at scale: the same query must give the same order, twice.
    mismatches = 0
    for query in queries[: min(50, len(queries))]:
        again = [r.thought_id for r in vault.recall(query).results]
        if again != first_rankings[query]:
            mismatches += 1

    # Incremental cost: adding more memories must not require a full rebuild.
    started = time.perf_counter()
    for index in range(50):
        text, thought_type, tags = make_thought(count + index, rng)
        vault.create_thought(text, thought_type, f"Extra {index:04d}", tags)
    vault.recall("the enemy remembers the last mistake")
    incremental_seconds = time.perf_counter() - started

    started = time.perf_counter()
    clusters = vault.clusters()
    cluster_seconds = time.perf_counter() - started

    started = time.perf_counter()
    model = vault.graph_view_model(max_nodes=200)
    view_seconds = time.perf_counter() - started

    rss_peak = peak_rss_mb()
    index_stats = vault.recall_index_stats()
    budget = BUDGETS.get(count, FALLBACK_BUDGET)

    report = {
        "count": count,
        "seed": seed,
        "queries": len(queries),
        "vault": {
            "thoughts": stats["totalThoughts"],
            "relationships": stats["relationships"],
            "tags": stats["tags"],
            "revisions": stats["revisions"],
            "events": stats["events"],
            "fileBytes": (data_dir / "thought-vault.sqlite3").stat().st_size,
        },
        "build": build,
        "recall": {
            "p50Ms": round(percentile(latencies, 50), 2),
            "p95Ms": round(percentile(latencies, 95), 2),
            "p99Ms": round(percentile(latencies, 99), 2),
            "maxMs": round(max(latencies), 2),
            "meanMs": round(statistics.fmean(latencies), 2),
            "totalSeconds": round(sum(latencies) / 1000.0, 3),
        },
        "work": {
            "candidatesExaminedMean": round(statistics.fmean(candidates), 1),
            "candidatesExaminedMax": max(candidates),
            "candidatesAsFractionOfVaultMax": round(max(candidates) / max(1, count), 4),
            "graphExpansionP95Ms": round(percentile(graph_timings, 95), 3),
            "resultCountMean": round(statistics.fmean(result_counts), 2),
            "states": states,
        },
        "derived": {
            "indexRows": {
                "tokens": index_stats["tokenRows"],
                "trigrams": index_stats["trigramRows"],
                "terms": index_stats["terms"],
                "documents": index_stats["documents"],
            },
            "clusters": len(clusters),
            "clusterSeconds": round(cluster_seconds, 3),
            "graphViewNodes": model["nodeCount"],
            "graphViewSeconds": round(view_seconds, 3),
            "graphViewTruncated": model["bounds"]["truncated"],
        },
        "incremental": {
            "addedThoughts": 50,
            "secondsIncludingOneRecall": round(incremental_seconds, 3),
        },
        "determinism": {
            "queriesRechecked": min(50, len(queries)),
            "rankingMismatches": mismatches,
        },
        "memory": {
            "rssBeforeMb": round(rss_before, 1),
            "rssAfterBuildMb": round(rss_after_build, 1),
            "rssPeakMb": round(rss_peak, 1),
            "growthMb": round(rss_peak - rss_before, 1),
            "note": "ru_maxrss for the whole process, not the graph alone",
        },
        "budget": budget,
        "config": {
            "tokenCandidateCap": DEFAULT_CONFIG.token_candidate_cap,
            "trigramCandidateCap": DEFAULT_CONFIG.trigram_candidate_cap,
            "fuzzyScoringCap": DEFAULT_CONFIG.fuzzy_scoring_cap,
            "graphMaxNodes": DEFAULT_CONFIG.graph_max_nodes,
            "graphMaxDepth": DEFAULT_CONFIG.graph_max_depth,
            "embeddingsEnabled": DEFAULT_CONFIG.embeddings_enabled,
            "networkRequired": DEFAULT_CONFIG.network_required,
        },
    }

    failures = []
    if build["indexBuildSeconds"] > budget["buildSeconds"]:
        failures.append(f"index build {build['indexBuildSeconds']}s > {budget['buildSeconds']}s")
    if report["recall"]["p95Ms"] > budget["recallP95Ms"]:
        failures.append(f"recall p95 {report['recall']['p95Ms']}ms > {budget['recallP95Ms']}ms")
    if report["recall"]["maxMs"] > budget["recallMaxMs"]:
        failures.append(f"recall max {report['recall']['maxMs']}ms > {budget['recallMaxMs']}ms")
    if report["memory"]["rssPeakMb"] > budget["peakRssMb"]:
        failures.append(f"peak RSS {report['memory']['rssPeakMb']}MB > {budget['peakRssMb']}MB")
    if mismatches:
        failures.append(f"{mismatches} ranking mismatches on repeat queries")
    # The core architectural claim: candidate generation is bounded by an ABSOLUTE
    # cap, not by the size of the vault.  The fraction of the vault that cap
    # represents is therefore large in a small vault and small in a big one — that
    # is the expected shape, not a defect.  What would be a defect is the cap
    # failing, or the fraction growing with the vault (which is what a disguised
    # corpus scan looks like).  ``compare_scaling`` checks the second half across
    # two sizes.
    absolute_cap = (DEFAULT_CONFIG.token_candidate_cap + DEFAULT_CONFIG.trigram_candidate_cap)
    if report["work"]["candidatesExaminedMax"] > absolute_cap:
        failures.append(
            f"examined {report['work']['candidatesExaminedMax']} candidates, above the "
            f"absolute cap of {absolute_cap} — recall is scanning the corpus"
        )
    report["budgetFailures"] = failures
    report["result"] = "PASS" if not failures else "FAIL"
    return report


def compare_scaling(reports: list[dict]) -> None:
    """Fail if per-query work grows with the vault instead of staying capped.

    Comparing two sizes is the only way to distinguish "bounded candidate
    generation" from "a full scan that happened to be fast on a small vault".
    """
    ordered = sorted(reports, key=lambda report: report["count"])
    small, large = ordered[0], ordered[-1]
    if small["count"] == large["count"]:
        return
    growth = large["count"] / small["count"]
    candidate_growth = (
        max(1, large["work"]["candidatesExaminedMax"])
        / max(1, small["work"]["candidatesExaminedMax"])
    )
    fraction_small = small["work"]["candidatesAsFractionOfVaultMax"]
    fraction_large = large["work"]["candidatesAsFractionOfVaultMax"]
    scaling = {
        "vaultGrowth": round(growth, 2),
        "candidateGrowth": round(candidate_growth, 2),
        "candidateFractionSmallVault": fraction_small,
        "candidateFractionLargeVault": fraction_large,
        "sublinear": candidate_growth < growth,
    }
    for report in ordered:
        report["scaling"] = scaling
    if not scaling["sublinear"]:
        report["budgetFailures"].append(
            f"candidates grew {candidate_growth:.2f}x while the vault grew {growth:.2f}x"
        )
        report["result"] = "FAIL"
    if fraction_large >= fraction_small:
        report["budgetFailures"].append(
            "the examined fraction of the vault did not shrink as the vault grew"
        )
        report["result"] = "FAIL"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=1000, help="thoughts to generate")
    parser.add_argument("--queries", type=int, default=200, help="queries to run")
    parser.add_argument("--seed", type=int, default=20261016, help="corpus seed")
    parser.add_argument("--data-dir", default="", help="where to build the vault")
    parser.add_argument("--keep", action="store_true", help="keep the vault afterwards")
    parser.add_argument("--suite", action="store_true",
                        help="run the mission's 1k and 10k checks together")
    args = parser.parse_args()

    counts = [1_000, 10_000] if args.suite else [args.count]
    reports = []
    for count in counts:
        data_dir = (
            Path(args.data_dir) if args.data_dir and not args.suite
            else Path(f"/tmp/iwtt-benchmark-{count}")
        )
        report = run_benchmark(count, args.queries, data_dir, args.seed)
        report["dataDir"] = str(data_dir)
        reports.append(report)
        if not args.keep:
            shutil.rmtree(data_dir, ignore_errors=True)

    if len(reports) > 1:
        compare_scaling(reports)
    print(json.dumps(reports[0] if len(reports) == 1 else reports, indent=2))
    failed = [report for report in reports if report["result"] != "PASS"]
    if failed:
        for report in failed:
            print(f"BUDGET EXCEEDED at {report['count']} thoughts: "
                  f"{'; '.join(report['budgetFailures'])}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
