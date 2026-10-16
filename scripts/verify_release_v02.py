#!/usr/bin/env python3
"""End-to-end release verification for IWTT v0.2 (Recall & Graph Intelligence).

``scripts/verify_release.py`` proves the v0.1 contract still holds.  This script
proves the v0.2 contract on top of it, in one isolated temporary vault:

  1. canonical data survives the complete deletion of every derived artefact;
  2. recall works again after a rebuild, with the same ranking it had before;
  3. a genuine v0.1-format database migrates without a canonical rewrite;
  4. ranking is deterministic across repeats, rebuilds and restarts;
  5. nothing in v0.2 needs AI, embeddings, or a network;
  6. the HTTP surface answers, in camelCase, on both the v0.1 and v0.2 routes;
  7. export/import still speaks schemaVersion 1;
  8. ambiguity is refused rather than guessed, and absence is reported as absence;
  9. relationship provenance survives everything the derived layer does;
 10. query-outcome memory is derived, inspectable and resettable;
 11. the external-knowledge foundation is documentation and interfaces only.

Every number in the output was produced by this run.  Exit status is non-zero if
any check fails, so this can be used as a gate rather than a report.

    python3 scripts/verify_release_v02.py
    python3 scripts/verify_release_v02.py --json-only
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from iwtt import server as iwtt_server                      # noqa: E402
from iwtt.ai import NoAIProvider, provider_from_environment  # noqa: E402
from iwtt.recall.config import DEFAULT_CONFIG                # noqa: E402
from iwtt.recall.external import (                           # noqa: E402
    CAPABILITY_MANIFEST,
    CROSS_DOMAIN_DESIGN,
    EXTERNAL_ENTITY_TYPES,
)
from iwtt.recall.index import RECALL_DERIVED_TABLES          # noqa: E402
from iwtt.store import EXPORT_SCHEMA_VERSION, Vault          # noqa: E402

# The migration fixture lives in the test suite on purpose: there must be exactly
# one definition of "a v0.1-format file" in this repository, and the release gate
# verifies against that same fixture instead of a second copy that could drift.
sys.path.insert(0, str(REPO_ROOT / "tests"))
from test_migration_v01_to_v02 import raw_snapshot, v01_vault  # noqa: E402


def _strip_timings(payload: dict) -> dict:
    """Remove wall-clock fields so two payloads can be compared byte for byte."""
    cleaned = json.loads(json.dumps(payload, sort_keys=True))

    # Wall-clock fields.  ``timingsMs`` is a whole subtree of stage durations, so
    # it is dropped outright; the rest are scalar stamps.  Everything else in a
    # recall payload is derived from the vault and the query alone, so two runs
    # over an unchanged vault must produce identical bytes.
    subtrees = {"timingsMs"}
    scalars = {"generatedAt", "builtAt", "lastSyncAt", "indexedAt", "queriedAt",
               "durationMs", "resetAt", "migratedAt", "learningResetAt", "checkedAt",
               "exportedAt", "lastReconcileAt"}

    def walk(node):
        if isinstance(node, dict):
            for key in list(node):
                if key in subtrees:
                    node.pop(key)
                elif key in scalars and not isinstance(node[key], (dict, list)):
                    node.pop(key)
                else:
                    walk(node[key])
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(cleaned)
    return cleaned


def _http(port: int, path: str, method: str = "GET", body: dict | None = None):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        method=method,
        data=json.dumps(body).encode("utf-8") if body is not None else None,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            raw = response.read().decode("utf-8")
            return response.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as error:
        raw = error.read().decode("utf-8")
        try:
            return error.code, json.loads(raw)
        except json.JSONDecodeError:
            return error.code, raw


#: The one definition of the convention, identical to the regex in
#: ``tests/test_layering.py``: a FIELD NAME is snake_case when it is lowercase
#: words joined by underscores.  SCREAMING_SNAKE_CASE keys are not field names —
#: they are the closed domain vocabulary (``THOUGHT_ENTITY``, ``USER_CONFIRMED``,
#: ``GRAVEYARD``) used as map keys, exactly as v0.1 uses them as values.
_SNAKE_CASE_FIELD = re.compile(r"^[a-z]+(_[a-z0-9]+)+$")


def _snake_case_keys(node, trail="") -> list[str]:
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{trail}.{key}" if trail else str(key)
            if _SNAKE_CASE_FIELD.match(str(key)):
                found.append(here)
            found.extend(_snake_case_keys(value, here))
    elif isinstance(node, list):
        for index, item in enumerate(node):
            found.extend(_snake_case_keys(item, f"{trail}[{index}]"))
    return found


def _third_party_imports() -> list[str]:
    """Compact standalone check; tests/test_layering.py holds the full classifier."""
    internal = {"iwtt"}
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "iwtt").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                root = name.split(".")[0]
                if root in internal or root in sys.stdlib_module_names:
                    continue
                offenders.append(f"{path.relative_to(REPO_ROOT)}: {name}")
    return offenders


def build_corpus(vault: Vault) -> dict[str, str]:
    """A small vault with the properties every v0.2 check needs."""
    ids: dict[str, str] = {}
    ids["enemies"] = vault.create_thought(
        "Could game enemies remember how the player fights and change their "
        "strategy later? The zebra quartz marker lives only here.",
        "THOUGHT", "Enemy memory", ["combat", "enemy ai"],
    )["id"]
    ids["dungeons"] = vault.create_thought(
        "Procedural dungeons should feel authored, not random.",
        "EXPERIMENT", "Authored procedural", ["procedural"],
    )["id"]
    ids["combat"] = vault.create_thought(
        "Turn based combat was abandoned because it slowed the loop down.",
        "THOUGHT", "Abandoned turn based combat", ["combat", "balance"],
    )["id"]
    ids["save"] = vault.create_thought(
        "The save system must survive a schema migration without rewriting "
        "anything the player wrote.",
        "PROJECT", "Save migration", ["architecture"],
    )["id"]
    vault.move_to_graveyard(ids["combat"])
    # v0.1 derives status from provenance: USER_CONFIRMED lands CONFIRMED,
    # AI_SUGGESTED lands SUGGESTED and stays there until a human confirms it.
    vault.create_relationship(ids["enemies"], ids["combat"], "INSPIRED_BY",
                              provenance="USER_CONFIRMED")
    vault.create_relationship(ids["dungeons"], ids["enemies"], "RELATED_TO",
                              provenance="AI_SUGGESTED")
    vault.rebuild_recall_indexes(full=True)
    return ids


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json-only", action="store_true", help="print only the JSON report")
    args = parser.parse_args()

    report: dict[str, object] = {"tool": "verify_release_v02", "appVersion": ""}
    failures: list[str] = []

    def record(name: str, value: object) -> None:
        report[name] = value
        if not args.json_only:
            print(f"  {name}: {value}")

    def require(condition: bool, name: str, detail: str = "") -> None:
        if not condition:
            failures.append(f"{name}{': ' + detail if detail else ''}")

    with tempfile.TemporaryDirectory(prefix="iwtt-v02-release-") as temp:
        root = Path(temp)
        live = root / "live" / "thought-vault.sqlite3"
        vault = Vault(live, ai_provider=NoAIProvider())
        record("startedEmpty", vault.stats()["totalThoughts"] == 0)
        record("appVersion", vault.schema_versions()["appVersion"])

        ids = build_corpus(vault)
        canonical_before = vault.canonical_fingerprint()
        snapshot_before = raw_snapshot(live)["canonical"]
        ranking_before = [
            (result.thought_id, round(result.score, 6), result.state)
            for result in vault.recall("enemies remember how the player fights").results
        ]

        # --- 1 & 2: delete every derived artefact, then rebuild -----------------
        deleted = vault.delete_all_derived_intelligence()
        record("derivedDeletion", {
            "canonicalTablesTouched": deleted["canonicalTablesTouched"],
            "recallTablesDeleted": len(deleted["recallTablesDeleted"]),
            "recallRowsDeleted": deleted["recallRowsDeleted"],
        })
        with sqlite3.connect(live) as conn:
            leftover = {
                table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in sorted(RECALL_DERIVED_TABLES)
            }
        record("derivedTablesAfterDeletion", leftover)
        require(sum(leftover.values()) == 0, "derivedTablesEmpty", str(leftover))
        require(vault.canonical_fingerprint() == canonical_before,
                "canonicalFingerprintAfterDeletion")
        require(raw_snapshot(live)["canonical"] == snapshot_before, "canonicalRowsAfterDeletion")
        record("canonicalFingerprintPreserved",
               vault.canonical_fingerprint() == canonical_before)
        record("canonicalRowsByteIdentical", raw_snapshot(live)["canonical"] == snapshot_before)

        rebuilt = vault.rebuild_recall_indexes(full=True)
        ranking_after = [
            (result.thought_id, round(result.score, 6), result.state)
            for result in vault.recall("enemies remember how the player fights").results
        ]
        record("recallAfterRebuild", {
            "documents": rebuilt.get("documents"),
            "sameRankingAsBeforeDeletion": ranking_after == ranking_before,
            "topResult": ranking_after[0][0] == ids["enemies"] if ranking_after else False,
        })
        require(ranking_after == ranking_before, "rankingSurvivesDerivedDeletion")

        # --- 4: determinism ----------------------------------------------------
        payloads = []
        for _ in range(3):
            payloads.append(json.dumps(
                _strip_timings(vault.find_my_thought("enemy adapting to player")),
                sort_keys=True,
            ))
        restart = Vault(live, ai_provider=NoAIProvider())
        payloads.append(json.dumps(
            _strip_timings(restart.find_my_thought("enemy adapting to player")),
            sort_keys=True,
        ))
        record("determinism", {
            "identicalAcrossRepeats": len(set(payloads[:3])) == 1,
            "identicalAcrossRestart": len(set(payloads)) == 1,
            "tieBreakPublished": bool(
                vault.recall("enemies remember how the player fights").results[0].tie_break
            ),
        })
        require(len(set(payloads)) == 1, "deterministicRanking")

        # --- 8: ambiguity refusal and honest absence ---------------------------
        ambiguous = vault.recall("the").to_dict()
        absent = vault.recall("quantum chromodynamics renormalisation").to_dict()
        record("ambiguityRefusal", {
            "state": ambiguous["state"],
            "resultCount": ambiguous["resultCount"],
            "message": ambiguous["message"],
            "ambiguity": ambiguous["ambiguity"],
            # The refusal message names the number of EQUALLY LIKELY candidates,
            # which is the honest number: the third result is a weaker rival, not a
            # co-winner.  So compare against the tie count, not the result count.
            "messageNamesTheTiedCount":
                str(ambiguous["ambiguity"]["tiedCount"]) in ambiguous["message"],
            "orderingIsTieBreakNotJudgement":
                ambiguous["ambiguity"]["orderingIsTieBreakNotJudgement"],
            "refusal": ambiguous["ambiguity"]["refusal"],
        })
        record("honestAbsence", {
            "state": absent["state"],
            "resultCount": absent["resultCount"],
            "message": absent["message"],
            "honesty": {key: absent["honesty"][key] for key in
                        ("embeddings", "semanticUnderstanding", "ai", "network",
                         "probabilisticConfidence")},
        })
        require(absent["state"] == "NO_MATCH" and absent["resultCount"] == 0,
                "absenceIsNotGuessed")
        require(ambiguous["state"] == "AMBIGUOUS" and ambiguous["resultCount"] > 1,
                "ambiguityIsNotCollapsedToOneAnswer", ambiguous["state"])
        require(str(ambiguous["ambiguity"]["tiedCount"]) in ambiguous["message"],
                "refusalNamesTheTiedCount", ambiguous["message"])

        # --- 9: provenance -----------------------------------------------------
        relationships = vault.list_relationships("ALL")
        confirmed = [r for r in relationships if r["provenance"] == "USER_CONFIRMED"]
        suggested = [r for r in relationships if r["provenance"] == "AI_SUGGESTED"]
        path = vault.find_path(ids["dungeons"], ids["save"])
        record("provenancePreserved", {
            "userConfirmed": len(confirmed),
            "aiSuggested": len(suggested),
            "suggestedStillSuggested": all(r["status"] == "SUGGESTED" for r in suggested),
            "suggestedNeverBecameConfirmedByUse":
                all(r["confirmedBy"] is None for r in suggested),
        })
        record("pathHonesty", {
            "found": path["found"],
            "reason": path.get("reason", ""),
            "narrative": path.get("narrative", ""),
            "nodes": len(path.get("nodes", [])),
            "edges": len(path.get("edges", [])),
        })
        require(all(r["status"] == "SUGGESTED" for r in suggested),
                "searchUseNeverConfirmsASuggestion")
        require((not path["found"] and bool(path.get("reason")))
                or path["found"], "absentPathIsReportedNotInvented")

        # --- 10: query memory is derived --------------------------------------
        vault.record_recall_feedback(query="enemy adapting to player",
                                     thought_id=ids["enemies"], feedback="USEFUL",
                                     rank=1, score=12.5)
        learned = vault.recall_learning_stats()
        history = vault.recall_learning_history(limit=50)
        learned_queries = vault.learned_queries(limit=10)
        memory_before = vault.canonical_fingerprint()
        rows_before = vault.stats()["recall"]
        reset = vault.reset_recall_learning()
        record("queryMemory", {
            "available": learned["available"],
            "events": learned["events"],
            "halfLifeDays": learned["halfLifeDays"],
            "corroborationThreshold": learned.get("minCorroboration"),
            "inspectableHistoryRows": len(history),
            "inspectableLearnedQueries": len(learned_queries),
            "historyNamesTheThought": bool(history) and bool(history[0].get("displayTitle")),
            "resetDeletedEvents": reset["deletedEvents"],
            "historyEmptyAfterReset": len(vault.recall_learning_history(limit=50)) == 0,
            "recallStatsBefore": rows_before,
            "recallStatsAfterReset": vault.stats()["recall"],
            "canonicalUntouchedByLearning": vault.canonical_fingerprint() == memory_before,
        })
        require(vault.canonical_fingerprint() == memory_before, "learningNeverCanonical")
        require(len(history) >= 1 and reset["deletedEvents"] >= 1
                and len(vault.recall_learning_history(limit=50)) == 0,
                "queryMemoryIsInspectableAndResettable")

        # --- 12: graph view boundary ------------------------------------------
        model = vault.graph_view_model(max_nodes=50)
        record("graphViewModel", {
            "nodeCount": model["nodeCount"],
            "entityTypes": sorted({node["entityType"] for node in model["nodes"]}),
            "layoutPersisted": model["layout"]["persisted"],
            "coordinatesIncluded": model["layout"]["coordinatesIncluded"],
            "canonicalFieldsDeclared": bool(model.get("canonicalFields")),
            "derivedFieldsDeclared": bool(model.get("derivedFields")),
        })
        require(model["layout"]["persisted"] is False, "noPersistedLayout")

        # --- 7: export / import still speaks v1 --------------------------------
        exported = vault.export_vault()
        record("exportContract", {
            "format": exported["format"],
            "schemaVersion": exported["schemaVersion"],
            "matchesV01Constant": exported["schemaVersion"] == EXPORT_SCHEMA_VERSION,
            "includesDerivedByDefault": "derived" in exported,
            "topLevelKeys": sorted(exported),
            "thoughts": len(exported["canonical"]["thoughts"]),
            "relationships": len(exported["canonical"]["relationships"]),
            "revisions": len(exported["canonical"]["revisions"]),
        })
        require(exported["schemaVersion"] == EXPORT_SCHEMA_VERSION == 1,
                "canonicalExportSchemaUnchanged")
        clone_path = root / "clone" / "thought-vault.sqlite3"
        clone = Vault(clone_path, ai_provider=NoAIProvider())
        clone.import_vault(vault.export_vault(include_derived=False))
        record("importRoundTrip", {
            "canonicalFingerprintMatches": clone.canonical_fingerprint() == canonical_before,
            "recallWorksOnImportedVault":
                bool(clone.recall("enemies remember how the player fights").results),
        })
        require(clone.canonical_fingerprint() == canonical_before, "importPreservesCanonical")

        # --- 3: migration from a genuine v0.1 file -----------------------------
        v01_path = root / "migrated" / "thought-vault.sqlite3"
        v01_path.parent.mkdir(parents=True, exist_ok=True)
        expected = v01_vault(v01_path)
        with sqlite3.connect(v01_path) as conn:
            tables_before = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
        no_recall_tables = not any(name.startswith("recall_") for name in tables_before)
        require(no_recall_tables, "v01FileHasNoRecallTables")
        before = raw_snapshot(v01_path)["canonical"]
        migrated = Vault(v01_path)
        after = raw_snapshot(v01_path)["canonical"]
        versions = migrated.schema_versions()
        migrated_rows = {
            thought["id"]: thought for thought in migrated.list_thoughts(status="ALL")
        }
        outcome = migrated.recall("enemies remember how the player fights")
        record("migrationFromV01", {
            "v01FileHadNoRecallTables": no_recall_tables,
            "canonicalRowsUnchanged": before == after,
            "idsPreserved": all(
                thought_id in migrated_rows for thought_id in expected["thoughtIds"]
            ),
            "originalTextPreserved": all(
                migrated_rows[thought_id]["originalText"] == original
                for thought_id, original in zip(expected["thoughtIds"], expected["originals"])
            ),
            "graveyardStatePreserved":
                migrated_rows[expected["thoughtIds"][2]]["status"] == "GRAVEYARD",
            "canonicalSchemaVersion": versions["canonicalSchemaVersion"],
            "appVersion": versions["appVersion"],
            "migratedFrom": versions["migratedFromAppVersion"],
            "schemaInfo": migrated.schema_info_rows(),
            "stats": {key: migrated.stats()[key] for key in
                      ("totalThoughts", "revisions", "relationships",
                       "suggestedRelationships", "events", "tags")},
            "recallWorksOnMigratedData": bool(outcome.results),
            "recallTopResultIsTheV01Thought":
                bool(outcome.results) and outcome.results[0].thought_id == expected["thoughtIds"][0],
            "suggestionStillUnconfirmed": all(
                relationship["status"] == "SUGGESTED"
                and relationship["confirmedBy"] is None
                for relationship in migrated.list_relationships("ALL")
                if relationship["provenance"] == "AI_SUGGESTED"
            ),
        })
        require(before == after, "migrationDoesNotRewriteCanonicalRows")
        require(versions["canonicalSchemaVersion"] == "1", "canonicalSchemaVersionStillOne")
        require(bool(outcome.results)
                and outcome.results[0].thought_id == expected["thoughtIds"][0],
                "recallWorksOnMigratedData")
        Vault(v01_path)  # opening again must not change anything
        require(raw_snapshot(v01_path)["canonical"] == after, "migrationIsIdempotent")

        # --- 5: offline, no AI, no embeddings, stdlib only ---------------------
        os.environ.pop("IWTT_AI_BASE_URL", None)
        provider = provider_from_environment()
        honesty = vault.recall("enemy").to_dict()["honesty"]
        record("offlineNoAI", {
            "provider": type(provider).__name__,
            "aiProviderOnVault": type(vault.ai_provider).__name__,
            "embeddingsEnabled": DEFAULT_CONFIG.embeddings_enabled,
            "aiRequired": DEFAULT_CONFIG.ai_required,
            "networkRequired": DEFAULT_CONFIG.network_required,
            "thirdPartyImports": _third_party_imports(),
            "honestyStatements": {
                key: honesty[key] for key in
                ("embeddings", "semanticUnderstanding", "ai", "network")
            },
        })
        require(type(provider) is NoAIProvider, "defaultProviderIsNoAI")
        require(not _third_party_imports(), "standardLibraryOnly")
        require(DEFAULT_CONFIG.embeddings_enabled is False
                and DEFAULT_CONFIG.network_required is False, "noEmbeddingsNoNetwork")

        # --- 11: external foundation is documentation only ---------------------
        foundation = vault.external_foundation()
        record("externalFoundation", {
            "implemented": foundation["implemented"],
            "canonical": foundation["canonical"],
            "persisted": foundation["persisted"],
            "storageTablesCreated": foundation["storageCreated"],
            "entityKinds": foundation["entityKinds"],
            "externalEntityTypes": len(foundation["externalEntityTypes"]),
            "matchesReservedVocabulary":
                set(foundation["externalEntityTypes"]) == set(EXTERNAL_ENTITY_TYPES),
            "externalProvenance": foundation["externalProvenance"],
            "crossDomainSegments": len(foundation["crossDomainDesign"]),
            "implementableSegments": sum(
                1 for item in foundation["crossDomainDesign"] if item["implementable"]
            ),
            "deferredSegmentsNameABlocker": all(
                item.get("blocker") for item in CROSS_DOMAIN_DESIGN if not item["implementable"]
            ),
            "guards": foundation["guards"],
            "explicitlyNotImplementedInV02":
                CAPABILITY_MANIFEST["explicitlyNotImplementedInV02"],
            "canonicalSchemaChanged": CAPABILITY_MANIFEST["canonicalSchemaChanged"],
            "newCanonicalTables": CAPABILITY_MANIFEST["newCanonicalTables"],
            "newCanonicalColumns": CAPABILITY_MANIFEST["newCanonicalColumns"],
            "invariant": CAPABILITY_MANIFEST["invariant"],
            "note": foundation["note"],
        })
        require(foundation["persisted"] is False and foundation["canonical"] is False
                and foundation["storageCreated"] == [],
                "externalEntitiesAreNotPersisted")
        require(foundation["implemented"] is False, "noExternalIngestionIsImplemented")
        require(CAPABILITY_MANIFEST["canonicalSchemaChanged"] is False
                and CAPABILITY_MANIFEST["newCanonicalTables"] == []
                and CAPABILITY_MANIFEST["newCanonicalColumns"] == [],
                "externalFoundationChangedNoCanonicalSchema")

        # --- 6: HTTP surface ---------------------------------------------------
        httpd = iwtt_server.IWTTHTTPServer(("127.0.0.1", 0), iwtt_server.RequestHandler, vault)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            routes = [
                ("GET", "/api/health"),
                ("GET", "/api/stats"),
                ("GET", "/api/thoughts"),
                ("GET", "/api/search?q=enemy"),
                ("GET", "/api/recall?q=enemies+remember+fights"),
                ("GET", "/api/recall/find?q=enemy+adapting"),
                ("GET", "/api/recall/config"),
                ("GET", "/api/recall/index"),
                ("GET", "/api/recall/learning"),
                ("GET", "/api/clusters"),
                ("GET", "/api/graph/view"),
                ("GET", f"/api/graph/neighbourhood/{ids['enemies']}"),
                ("GET", f"/api/graph/lineage/{ids['enemies']}"),
                ("GET", f"/api/graph/path?source={ids['enemies']}&target={ids['combat']}"),
                ("GET", "/api/external/foundation"),
                ("GET", "/api/data/export.json"),
            ]
            statuses: dict[str, int] = {}
            writes: dict[str, int] = {}
            snake_leaks: list[str] = []
            for method, path in routes:
                status, payload = _http(port, path, method)
                statuses[path] = status
                if status == 200 and isinstance(payload, (dict, list)):
                    snake_leaks.extend(_snake_case_keys(payload)[:3])
            post_status, _ = _http(port, "/api/recall", "POST", {"q": "procedural dungeons"})
            writes["POST /api/recall"] = post_status
            feedback_status, _ = _http(port, "/api/recall/feedback", "POST", {
                "query": "procedural dungeons", "thoughtId": ids["dungeons"],
                "feedback": "USEFUL",
            })
            writes["POST /api/recall/feedback"] = feedback_status
            rebuild_status, _ = _http(port, "/api/recall/index/rebuild", "POST", {})
            writes["POST /api/recall/index/rebuild"] = rebuild_status
            missing_status, missing_payload = _http(
                port, "/api/recall/diagnostics?q=enemy&thought_id=nope"
            )
            writes["GET /api/recall/diagnostics (unknown id)"] = missing_status
            # An unknown id must not produce a breakdown that pretends to be about
            # a real thought.  200 with an explicit "not found" result is honest;
            # silently returning an empty scoring table would not be.
            missing_result = (missing_payload or {}).get("result", {})
            record("httpSurface", {
                "port": port,
                "getRoutes": statuses,
                "writeRoutes": writes,
                "allGetRoutesOk": all(value == 200 for value in statuses.values()),
                "diagnosticsForUnknownId": {
                    "status": missing_status,
                    "result": missing_result,
                    "statesAnExplicitNotFound": bool(
                        missing_result.get("found") is False
                        or missing_result.get("note")
                        or missing_result.get("reason")
                    ),
                },
                "snakeCaseLeaks": snake_leaks[:5],
            })
            require(all(value == 200 for value in statuses.values()), "httpRoutesAnswer")
            require(post_status == 200 and feedback_status in (200, 201)
                    and rebuild_status == 200, "httpWriteRoutesAnswer")
            require(not snake_leaks, "publicPayloadsAreCamelCase", str(snake_leaks[:5]))
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)

        # --- final canonical assertion ----------------------------------------
        record("canonicalFingerprintUnchangedAtEnd",
               vault.canonical_fingerprint() == canonical_before)
        require(vault.canonical_fingerprint() == canonical_before,
                "canonicalFingerprintUnchangedAtEnd")
        record("finalStats", {
            key: vault.stats()[key]
            for key in ("totalThoughts", "revisions", "relationships", "events", "tags")
        })

    report["checks"] = len(report) - 2
    report["failures"] = failures
    report["result"] = "PASS" if not failures else "FAIL"
    print(json.dumps(report, indent=2, default=str))
    return 0 if not failures else 1


if __name__ == "__main__":
    started = time.perf_counter()
    status = main()
    if status == 0:
        print(f"PASS in {time.perf_counter() - started:.1f}s")
    else:
        print(f"FAIL in {time.perf_counter() - started:.1f}s", file=sys.stderr)
    raise SystemExit(status)
