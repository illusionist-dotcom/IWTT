"""Layering — canonical vs derived, module boundaries, and the external-entity wall.

Three separate boundaries are checked here, because three separate failures are
possible:

1. **Storage.** The canonical schema is byte-for-byte the v0.1 schema.  v0.2 adds
   only ``recall_*`` derived tables, every one of which cascades from ``thoughts``.
   No derived table holds a verbatim copy of what the user wrote.
2. **Modules.** ``store.py`` never imports ``iwtt.recall`` at module level (so the
   canonical layer stays usable on its own, and there is no import cycle), and the
   recall layer never writes to a canonical table.
3. **Entities.** A Thought and a future external-knowledge record are different
   kinds of thing, with a closed vocabulary on both sides and guards that actually
   refuse a crossing.  v0.2 ships the interface and the documentation only.
"""
from __future__ import annotations

import ast
import re
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from iwtt import __file__ as PACKAGE_INIT
from iwtt.recall.config import DEFAULT_CONFIG
from iwtt.recall.external import (
    CAPABILITY_MANIFEST,
    CROSS_DOMAIN_DESIGN,
    ENTITY_EXTERNAL,
    ENTITY_KINDS,
    ENTITY_THOUGHT,
    EXTERNAL_ENTITY_TYPES,
    EXTERNAL_PROVENANCE,
    EntityBoundaryError,
    ExternalEntityRef,
    ThoughtRef,
    assert_not_thought,
    assert_thought,
    capability_manifest,
    cross_domain_design,
    entity_kind_of,
    validate_candidate_entity,
)
from iwtt.recall.index import (
    RECALL_DERIVED_TABLES,
    RECALL_GLOBAL_TABLES,
    RECALL_PER_THOUGHT_TABLES,
)
from iwtt.store import Vault

REPO_ROOT = Path(__file__).resolve().parent.parent
RECALL_DIR = REPO_ROOT / "iwtt" / "recall"

#: The v0.1 canonical schema, pinned.  Any drift here is a v0.2 regression.
CANONICAL_TABLES = frozenset({
    "schema_info", "thoughts", "revisions", "tags", "thought_tags", "relationships", "events",
})
V01_DERIVED_TABLES = frozenset({"derived_artifacts", "search_documents"})
CANONICAL_COLUMNS = {
    "thoughts": ["id", "original_text", "current_text", "title", "type", "status", "created_at",
                 "updated_at", "graveyard_at", "resurrected_at", "is_demo"],
    "revisions": ["id", "thought_id", "revision_number", "text", "title", "type", "created_at",
                  "change_reason"],
    "tags": ["id", "name", "normalized_name", "created_at", "is_demo"],
    "thought_tags": ["thought_id", "tag_id", "created_at"],
    "relationships": ["id", "source_id", "target_id", "type", "provenance", "status", "created_at",
                      "updated_at", "confirmed_at", "confirmed_by", "metadata_json", "is_demo"],
    "events": ["id", "target_id", "target_type", "event_type", "timestamp", "metadata_json",
               "is_demo"],
    "schema_info": ["key", "value"],
}
V01_DERIVED_COLUMNS = {
    "derived_artifacts": ["id", "thought_id", "artifact_type", "payload_json",
                          "source_thought_ids_json", "provider", "model", "generated_at"],
    "search_documents": ["thought_id", "content", "indexed_at"],
}
V01_INDEXES = {
    "derived_artifacts_thought_idx", "events_target_idx", "events_timestamp_idx",
    "relationships_source_idx", "relationships_target_idx", "revisions_thought_idx",
    "thought_tags_tag_idx", "thoughts_status_created_idx", "thoughts_type_idx",
}
FORBIDDEN_CANONICAL_COLUMNS = {"community", "norm_label", "cluster_id", "cluster", "embedding",
                              "embeddings", "degree", "hub_factor", "importance", "query_norm"}


#: Packages that would break the local-first, offline, stdlib-only contract.
FORBIDDEN_PACKAGES = frozenset({
    "networkx", "numpy", "scipy", "pandas", "graspologic", "leidenalg", "igraph",
    "matplotlib", "sklearn", "torch", "tensorflow", "openai", "anthropic",
    "sentence_transformers", "faiss", "chromadb", "requests", "httpx", "boto3",
    "pydantic", "flask", "fastapi", "django",
})


def repo_module_names(root: Path) -> set[str]:
    """Every dotted module and package name this repository provides.

    Discovered from the filesystem rather than written down, so the classifier
    cannot drift: adding a module can never make it look third-party, and deleting
    one can never hide a real dependency.
    """
    names: set[str] = set()
    for path in root.rglob("*.py"):
        parts = list(path.relative_to(root).parts)
        parts[-1] = "" if parts[-1] == "__init__.py" else parts[-1][:-3]
        parts = [part for part in parts if part]
        for depth in range(1, len(parts) + 1):
            names.add(".".join(parts[:depth]))
    return names


def absolute_import_name(node: ast.ImportFrom, module_name: str) -> str:
    """Resolve a possibly-relative ``ImportFrom`` to an absolute dotted name.

    ``from .config import X`` inside ``iwtt.recall.analysis`` is
    ``iwtt.recall.config``; ``from ..store import Vault`` is ``iwtt.store``.
    Treating the bare ``node.module`` as a top-level package name is what makes a
    relative import look like a third-party dependency, so it is resolved here.
    """
    if node.level == 0:
        return node.module or ""
    parent = module_name.split(".")[:-1]
    anchor = parent[: len(parent) - (node.level - 1)]
    return ".".join([*anchor, node.module] if node.module else anchor)


def module_imports(tree: ast.Module, module_name: str, internal: set[str]) -> set[str]:
    """Absolute dotted names of everything ``tree`` imports, at any nesting level.

    ``from . import querymemory`` names a package *and* pulls in a submodule whose
    name only appears in the alias list, so each alias is also tried as a submodule
    of the resolved name.  An alias that is not one of this repository's modules
    (``from pandas import DataFrame``) contributes nothing extra, which keeps a
    third-party import classified by its package rather than by its symbols.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            resolved = absolute_import_name(node, module_name)
            if resolved:
                names.add(resolved)
            for alias in node.names:
                candidate = f"{resolved}.{alias.name}" if resolved else alias.name
                if candidate in internal:
                    names.add(candidate)
    names.discard("")
    return names


def classify_imports(names: set[str], internal: set[str]) -> dict[str, list[str]]:
    """Sort import names into the three categories the dependency contract cares about.

    A. **stdlib** — the root is in ``sys.stdlib_module_names`` (the interpreter's
       own authoritative list, not a hand-maintained one).
    B. **internal** — the name, or its root package, is provided by this repository.
    C. **thirdParty** — everything else.  Only this category breaches the contract.
    """
    report: dict[str, list[str]] = {"stdlib": [], "internal": [], "thirdParty": []}
    for name in sorted(names):
        root = name.split(".")[0]
        if name in internal or root in internal:
            report["internal"].append(name)
        elif root in sys.stdlib_module_names or root == "__future__":
            report["stdlib"].append(name)
        else:
            report["thirdParty"].append(name)
    return report


def dotted_name(path: Path, root: Path) -> str:
    parts = list(path.relative_to(root).parts)
    parts[-1] = "" if parts[-1] == "__init__.py" else parts[-1][:-3]
    return ".".join(part for part in parts if part)


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def all_tables(conn: sqlite3.Connection) -> list[str]:
    return [
        row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
            "ORDER BY name"
        )
    ]


class StorageBoundaryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "thought-vault.sqlite3"
        self.vault = Vault(self.path)
        self.vault.initialize()
        self.text = (
            "The player should feel the world remembering their mistakes, "
            "and the ending phrase is utterly distinctive zebra quartz."
        )
        self.thought = self.vault.create_thought(self.text, "THOUGHT", "Enemy memory", ["combat"])
        self.other = self.vault.create_thought(
            "Procedural dungeons need a stable seed.", "INVENTION", "Seeded dungeons",
        )
        self.vault.create_relationship(self.thought["id"], self.other["id"], "LED_TO")
        self.vault.rebuild_recall_indexes(full=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_canonical_schema_is_unchanged_from_v01(self) -> None:
        with sqlite3.connect(self.path) as conn:
            for table, expected in CANONICAL_COLUMNS.items():
                self.assertEqual(columns(conn, table), expected, f"{table} drifted")
            for table, expected in V01_DERIVED_COLUMNS.items():
                self.assertEqual(columns(conn, table), expected, f"{table} drifted")
            indexes = {
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
                )
            }
            self.assertTrue(V01_INDEXES <= indexes, "every v0.1 index must still exist")
            triggers = [
                row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'")
            ]
            self.assertIn("preserve_original_thought_text", triggers)

    def test_every_table_is_either_canonical_or_known_derived(self) -> None:
        with sqlite3.connect(self.path) as conn:
            tables = set(all_tables(conn))
        expected_derived = V01_DERIVED_TABLES | set(RECALL_DERIVED_TABLES)
        self.assertEqual(
            tables, CANONICAL_TABLES | expected_derived,
            "an unclassified table means the canonical/derived boundary is no longer checkable",
        )
        self.assertFalse(tables & CANONICAL_TABLES & expected_derived)

    def test_canonical_schema_version_is_still_one(self) -> None:
        versions = self.vault.schema_versions()
        self.assertEqual(versions["canonicalSchemaVersion"], "1",
                         "v0.2 adds derived tables only; it does not version the canonical schema up")
        self.assertEqual(versions["appVersion"], "0.2.0")
        self.assertTrue(versions["recallSchemaVersion"])

    def test_no_canonical_table_gains_a_derived_column(self) -> None:
        with sqlite3.connect(self.path) as conn:
            for table in sorted(CANONICAL_TABLES):
                present = set(columns(conn, table))
                self.assertFalse(
                    present & FORBIDDEN_CANONICAL_COLUMNS,
                    f"{table} must not carry derived or external columns: {present & FORBIDDEN_CANONICAL_COLUMNS}",
                )

    def test_per_thought_derived_tables_cascade_from_thoughts(self) -> None:
        """Deleting a thought must take its own derived intelligence with it.

        These tables describe ONE thought, so each carries
        ``thought_id REFERENCES thoughts(id) ON DELETE CASCADE``.
        """
        self.assertTrue(RECALL_PER_THOUGHT_TABLES)
        with sqlite3.connect(self.path) as conn:
            for table in sorted(RECALL_PER_THOUGHT_TABLES):
                with self.subTest(table=table):
                    foreign_keys = list(conn.execute(f"PRAGMA foreign_key_list({table})"))
                    thought_fk = [fk for fk in foreign_keys if fk[2] == "thoughts"]
                    self.assertTrue(thought_fk, f"{table} does not reference thoughts(id)")
                    self.assertTrue(
                        all(fk[6].upper() == "CASCADE" for fk in thought_fk),
                        f"{table} must cascade when a thought is deleted",
                    )
                    self.assertIn("thought_id", columns(conn, table))

    def test_global_aggregate_tables_are_derived_but_not_thought_owned(self) -> None:
        """Corpus-wide state has a different integrity contract, and it is enforced.

        ``recall_term_stats`` holds a document frequency per term: a property of the
        whole corpus, not of one thought.  A ``thought_id`` foreign key there would
        be architecturally wrong — the frequency of "enemy" is not owned by any
        single memory.  Instead such a table must be (a) declared derived, (b) wiped
        by ``clear_derived_data()``, (c) free of canonical content, and (d)
        recomputed from canonical data after any change, so a deleted thought cannot
        leave a stale frequency behind.
        """
        self.assertEqual(
            set(RECALL_GLOBAL_TABLES), {"recall_term_stats", "recall_meta", "recall_build_log"}
        )
        self.assertEqual(
            set(RECALL_DERIVED_TABLES),
            set(RECALL_PER_THOUGHT_TABLES) | set(RECALL_GLOBAL_TABLES),
            "the two groups must partition the derived layer exactly",
        )
        with sqlite3.connect(self.path) as conn:
            for table in sorted(RECALL_GLOBAL_TABLES):
                with self.subTest(table=table):
                    self.assertNotIn("thought_id", columns(conn, table),
                                     "a global aggregate is not owned by one thought")
                    self.assertFalse(
                        [fk for fk in conn.execute(f"PRAGMA foreign_key_list({table})")
                         if fk[2] == "thoughts"],
                        f"{table} must not claim a per-thought foreign key",
                    )

        # (a)+(b) declared derived, and wiped together with everything else
        report = self.vault.clear_derived_data()
        self.assertEqual(
            sorted(report["recallTablesDeleted"]), sorted(RECALL_DERIVED_TABLES),
            "clear_derived_data must name every derived table it wiped",
        )
        self.assertGreater(report["recallRowsDeleted"], 0)
        self.assertEqual(report["canonicalTablesTouched"], [])
        with sqlite3.connect(self.path) as conn:
            for table in sorted(RECALL_DERIVED_TABLES):
                self.assertEqual(
                    conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0,
                    f"{table} survived clear_derived_data()",
                )

        # (d) recomputed from canonical data: the corpus statistics come back,
        # and they come back describing only what still exists
        self.vault.rebuild_recall_indexes(full=True)
        with sqlite3.connect(self.path) as conn:
            stats = {
                row[0]: row[1] for row in conn.execute("SELECT term, df FROM recall_term_stats")
            }
        self.assertGreater(stats.get("enemy", 0), 0)
        self.assertEqual(stats.get("procedural", 0), 1)

    # IWTT deliberately offers no user-facing "delete a thought": a memory is
    # buried in the Graveyard, not destroyed.  The two deletion paths that do exist
    # are ``remove_demo()`` (production) and a direct canonical DELETE (what a
    # future purge, or a hand-edited file, would do).  Both are exercised here,
    # because a foreign key that only works on one path is not a guarantee.

    def _canonical_delete(self, thought_id: str) -> None:
        """Delete a canonical row the way a purge (or a hand-edited file) would.

        Committed explicitly: ``sqlite3`` would otherwise roll the DELETE back when
        the connection closes, and a test that silently deletes nothing proves
        nothing at all.
        """
        conn = sqlite3.connect(self.path)
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            cursor = conn.execute("DELETE FROM thoughts WHERE id=?", (thought_id,))
            conn.commit()
            self.assertEqual(cursor.rowcount, 1, "the canonical row must actually be deleted")
        finally:
            conn.close()

    def _derived_rows_for(self, thought_id: str) -> dict[str, int]:
        with sqlite3.connect(self.path) as conn:
            return {
                table: conn.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE thought_id=?", (thought_id,)
                ).fetchone()[0]
                for table in sorted(RECALL_PER_THOUGHT_TABLES)
            }

    def test_deleting_a_thought_deletes_its_own_derived_rows(self) -> None:
        thought_id = self.thought["id"]
        before = self._derived_rows_for(thought_id)
        indexed = ("recall_tokens", "recall_trigrams", "recall_docs", "recall_graph_cache",
                   "recall_clusters")
        for table in indexed:
            self.assertGreater(before[table], 0, f"a live indexed thought must have {table} rows")
        self.assertEqual(before["recall_query_memory"], 0,
                         "query memory holds nothing until the user gives feedback")

        self._canonical_delete(thought_id)
        after = self._derived_rows_for(thought_id)
        self.assertEqual(sum(after.values()), 0, f"orphaned derived rows: {after}")

    def test_removing_demo_data_removes_its_derived_intelligence_too(self) -> None:
        """The production deletion path, end to end."""
        demo = self.vault.seed_demo()
        self.assertEqual(demo["thoughts"], 7, "v0.1's demo corpus is pinned at seven thoughts")
        self.vault.rebuild_recall_indexes(full=True)
        demo_ids = [thought["id"] for thought in self.vault.list_thoughts(status="ALL")
                    if thought["isDemo"]]
        self.assertTrue(demo_ids)
        self.assertTrue(any(sum(self._derived_rows_for(tid).values()) for tid in demo_ids),
                        "demo thoughts must be indexed like any other")

        report = self.vault.remove_demo()
        self.assertEqual(report["thoughtsRemoved"], len(demo_ids))
        for thought_id in demo_ids:
            self.assertEqual(sum(self._derived_rows_for(thought_id).values()), 0,
                             f"demo thought {thought_id} left derived rows behind")

    def test_deleting_a_thought_cannot_leave_stale_corpus_statistics(self) -> None:
        """The harmful-contamination case for a global aggregate.

        After a thought is deleted, its unique term must stop being reported as
        present in the corpus the next time the index is consulted — without the
        caller having to ask for a rebuild.
        """
        unique = self.vault.create_thought(
            "Xylophone wombat kumquat, three words found nowhere else in this vault.",
            "THOUGHT", "Unique words",
        )
        self.vault.rebuild_recall_indexes(full=True)
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT df FROM recall_term_stats WHERE term='xylophone'"
                ).fetchone()[0],
                1,
            )

        # a second thought that shares a term with the first, so the delete below
        # has to move a frequency DOWN rather than merely remove an orphaned row
        sharer = self.vault.create_thought(
            "Combat telegraphs tell the player what the enemy will do next.",
            "THOUGHT", "Combat telegraph",
        )
        self.vault.rebuild_recall_indexes(full=True)

        def df(term: str) -> int | None:
            with sqlite3.connect(self.path) as conn:
                row = conn.execute(
                    "SELECT df FROM recall_term_stats WHERE term=?", (term,)
                ).fetchone()
            return None if row is None else int(row[0])

        def orphans() -> int:
            with sqlite3.connect(self.path) as conn:
                return int(conn.execute(
                    "SELECT COUNT(*) FROM recall_term_stats ts "
                    "LEFT JOIN (SELECT DISTINCT token FROM recall_tokens) t ON t.token = ts.term "
                    "WHERE t.token IS NULL"
                ).fetchone()[0])

        self.assertEqual(df("xylophone"), 1)
        self.assertEqual(df("wombat"), 1)
        self.assertEqual(df("combat"), 2, "'combat' is a tag on one thought and a word in another")

        self._canonical_delete(unique["id"])   # takes an orphan-able term with it
        self.vault.recall("enemy")             # consults the index, which self-heals
        self.assertTrue(df("xylophone") in (None, 0),
                        "a deleted thought left its unique term statistics behind")
        self.assertEqual(orphans(), 0, "no term may outlive every thought that contained it")
        self.assertEqual(
            len(self.vault.recall("xylophone wombat kumquat").results), 0,
            "recall must not resurrect a deleted thought through a stale index",
        )

        self._canonical_delete(sharer["id"])   # takes one of two 'combat' documents
        self.vault.recall("enemy")
        self.assertEqual(df("combat"), 1,
                         "a shared term's document frequency must fall when a document goes")
        self.assertEqual(orphans(), 0)

        survivors = [t["id"] for t in self.vault.list_thoughts(status="ALL")]
        self.assertNotIn(unique["id"], survivors)
        self.assertNotIn(sharer["id"], survivors)
        self.assertIn(self.thought["id"], survivors)
        self.assertFalse(self.vault.recall_index_stats()["drift"]["stale"])
        # "combat" is still a tag on the surviving fixture thought, so it must still
        # be findable — while the deleted thought must not be findable at all.
        combat = self.vault.recall("combat telegraph").results
        self.assertEqual([result.thought_id for result in combat], [self.thought["id"]])
        self.assertIn("Tag match", combat[0].match_reason + " " + combat[0].why())
        self.assertGreater(len(self.vault.recall("enemy adapt player").results), 0,
                           "the surviving thought is still findable after two deletions")

    def test_no_derived_table_holds_a_verbatim_copy_of_the_original_text(self) -> None:
        """The index may store tokens and counts; it may not store the memory itself.

        A derived copy of the original text would be a second source of truth and
        a second thing to leak.
        """
        distinctive = "utterly distinctive zebra quartz"
        self.assertIn(distinctive, self.thought["originalText"])
        with sqlite3.connect(self.path) as conn:
            for table in sorted(RECALL_DERIVED_TABLES):
                text_columns = [
                    name for name in columns(conn, table)
                    if name not in ("thought_id", "id", "cluster_id", "query_norm")
                ]
                for column in text_columns:
                    rows = conn.execute(
                        f"SELECT {column} FROM {table} WHERE typeof({column})='text'"
                    ).fetchall()
                    for (value,) in rows:
                        self.assertNotIn(distinctive, value or "",
                                         f"{table}.{column} holds a verbatim fragment")
                        self.assertNotIn(self.text, value or "",
                                         f"{table}.{column} holds the original text")

    def test_tokens_are_stored_not_sentences(self) -> None:
        with sqlite3.connect(self.path) as conn:
            tokens = [
                row[0] for row in conn.execute(
                    "SELECT token FROM recall_tokens WHERE thought_id=?", (self.thought["id"],)
                )
            ]
        self.assertTrue(tokens)
        self.assertTrue(all(" " not in token for token in tokens), "a token is a word, not a phrase")
        self.assertTrue(all(token == token.casefold() for token in tokens), "tokens are normalized")
        self.assertIn("zebra", tokens)
        self.assertIn("combat", tokens)
        fields = {
            row[0] for row in conn.execute(
                "SELECT DISTINCT field FROM recall_tokens WHERE thought_id=?", (self.thought["id"],)
            )
        }
        self.assertEqual(fields, {"title", "body", "tag"})


class ModuleBoundaryTestCase(unittest.TestCase):
    def parse(self, path: Path) -> ast.Module:
        return ast.parse(path.read_text(), filename=str(path))

    def imported_names(self, tree: ast.Module, *, top_level_only: bool) -> set[str]:
        names: set[str] = set()
        for node in tree.body if top_level_only else ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module)
        return names

    def test_store_never_imports_recall_at_module_level(self) -> None:
        """The canonical layer must stand alone, and there must be no import cycle.

        ``store.py`` reaches the recall layer only from inside its own delegate
        methods, so importing ``iwtt.store`` never pulls in the derived layer and a
        corrupt or absent index cannot prevent the vault from opening.
        """
        tree = self.parse(REPO_ROOT / "iwtt" / "store.py")
        top_level = self.imported_names(tree, top_level_only=True)
        self.assertFalse(
            any(name.startswith("iwtt.recall") or name == "recall" for name in top_level),
            f"store.py imports recall at module level: {sorted(top_level)}",
        )
        self.assertNotIn("recall", {
            node.module.split(".")[0] for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        })
        nested = [
            name for name in self.imported_names(tree, top_level_only=False)
            if "recall" in name
        ]
        self.assertTrue(nested, "the delegates must still be able to reach the recall layer lazily")

    def test_recall_modules_may_import_store_but_never_the_server_or_web(self) -> None:
        for path in sorted(RECALL_DIR.glob("*.py")):
            with self.subTest(module=path.name):
                names = self.imported_names(self.parse(path), top_level_only=False)
                self.assertFalse(
                    any(name.endswith("server") or ".web" in name or name == "web" for name in names),
                    f"{path.name} must not depend on the HTTP layer",
                )

    def test_recall_never_writes_to_a_canonical_table(self) -> None:
        """The derived layer reads canonical data; it must never modify it."""
        pattern = re.compile(
            r"\b(INSERT\s+INTO|REPLACE\s+INTO|UPDATE|DELETE\s+FROM)\s+"
            r"(thoughts|revisions|tags|thought_tags|relationships|events|schema_info)\b",
            re.IGNORECASE,
        )
        for path in sorted(RECALL_DIR.glob("*.py")):
            with self.subTest(module=path.name):
                source = path.read_text()
                self.assertIsNone(
                    pattern.search(source),
                    f"{path.name} contains a write to a canonical table: {pattern.search(source)}",
                )

    def test_recall_only_touches_the_vault_through_its_documented_connection_api(self) -> None:
        allowed = {"_connection", "_transaction"}
        for path in sorted(RECALL_DIR.glob("*.py")):
            with self.subTest(module=path.name):
                tree = self.parse(path)
                for node in ast.walk(tree):
                    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                        if node.value.id in ("vault", "self_vault") and node.attr.startswith("_"):
                            self.assertIn(
                                node.attr, allowed,
                                f"{path.name} reaches into vault.{node.attr}",
                            )

    def test_no_third_party_dependency_was_added(self) -> None:
        """Local-first, standard library only — the v0.1 promise, still true in v0.2.

        Every module under ``iwtt/`` is classified: its imports must be either the
        standard library or IWTT's own modules.  A third category would be a
        third-party dependency, and there must be none.
        """
        internal = repo_module_names(REPO_ROOT)
        # the classifier must actually know this repository's own modules
        for expected in ("iwtt", "iwtt.store", "iwtt.server", "iwtt.ai",
                         "iwtt.recall", "iwtt.recall.engine", "iwtt.recall.scoring"):
            self.assertIn(expected, internal, f"{expected} was not discovered as internal")

        checked = 0
        for path in sorted((REPO_ROOT / "iwtt").rglob("*.py")):
            module_name = dotted_name(path, REPO_ROOT)
            with self.subTest(module=module_name):
                imported = module_imports(self.parse(path), module_name, internal)
                report = classify_imports(imported, internal)
                self.assertEqual(
                    report["thirdParty"], [],
                    f"{module_name} depends on third-party packages: {report['thirdParty']}",
                )
                if imported:
                    self.assertEqual(
                        sorted(report["stdlib"] + report["internal"]), sorted(imported),
                        "every import must land in exactly one known category",
                    )
                checked += 1
        self.assertGreaterEqual(checked, 18, "every runtime module must be classified")

        requirements = (REPO_ROOT / "requirements.txt").read_text().lower()
        for name in FORBIDDEN_PACKAGES:
            self.assertNotIn(name, requirements,
                             f"requirements.txt must stay empty of {name}")

    def test_the_dependency_classifier_still_rejects_a_real_third_party_import(self) -> None:
        """The guard above is only meaningful if it can still fail.

        A synthetic module importing real third-party packages, IWTT internals by
        absolute and relative path, and the standard library must be classified
        into exactly those three buckets — with the third-party names named.
        """
        source = (
            "import sqlite3\n"
            "import networkx as nx\n"
            "from pandas import DataFrame\n"
            "import numpy.linalg\n"
            "from openai import OpenAI\n"
            "from iwtt.recall.config import DEFAULT_CONFIG\n"
            "from .scoring import score_candidate\n"
            "from ..store import Vault\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            synthetic = Path(tmp) / "synthetic.py"
            synthetic.write_text(source)
            tree = ast.parse(source, filename=str(synthetic))
            internal = repo_module_names(REPO_ROOT)
            report = classify_imports(
                module_imports(tree, "iwtt.recall.synthetic", internal), internal
            )

        self.assertEqual(
            report["thirdParty"], ["networkx", "numpy.linalg", "openai", "pandas"],
            "a real third-party import must still be reported, not silently absorbed",
        )
        self.assertEqual(report["stdlib"], ["sqlite3"])
        self.assertEqual(
            report["internal"],
            ["iwtt.recall.config", "iwtt.recall.scoring", "iwtt.store"],
            "relative imports must resolve to IWTT's own modules, not be mistaken for packages",
        )

    def test_relative_import_resolution_is_exact(self) -> None:
        cases = [
            ("from .config import X", "iwtt.recall.analysis", "iwtt.recall.config"),
            ("from ..store import Vault", "iwtt.recall.analysis", "iwtt.store"),
            ("from . import querymemory", "iwtt.recall.engine", "iwtt.recall.querymemory"),
            ("from .recall.index import X", "iwtt.store", "iwtt.recall.index"),
            ("import sqlite3", "iwtt.recall.engine", "sqlite3"),
        ]
        for source, module_name, expected in cases:
            with self.subTest(source=source):
                tree = ast.parse(source)
                node = next(
                    n for n in ast.walk(tree)
                    if isinstance(n, (ast.Import, ast.ImportFrom))
                )
                resolved = sorted(module_imports(
                    tree, module_name, repo_module_names(REPO_ROOT)
                ))
                self.assertIn(expected, resolved)


class LocalFirstTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.vault = Vault(Path(self._tmp.name) / "thought-vault.sqlite3")
        self.vault.initialize()
        self.vault.create_thought("Enemies adapt to the player.", "THOUGHT", "Enemy memory")
        self.vault.rebuild_recall_indexes(full=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_recall_needs_no_ai_no_network_and_no_embeddings(self) -> None:
        config = self.vault.recall_config()
        self.assertFalse(config["embeddingsEnabled"])
        self.assertFalse(config["aiRequired"])
        self.assertFalse(config["networkRequired"])
        self.assertFalse(DEFAULT_CONFIG.embeddings_enabled)
        self.assertFalse(DEFAULT_CONFIG.ai_required)
        self.assertFalse(DEFAULT_CONFIG.network_required)

        outcome = self.vault.recall("enemy adaptation")
        honesty = outcome.honesty
        self.assertIn("NOT IMPLEMENTED", honesty["embeddings"])
        self.assertIn("no semantic stage", honesty["embeddings"])
        self.assertIn("None claimed", honesty["semanticUnderstanding"],
                      "IWTT must not claim to understand meaning it does not model")
        self.assertIn("Not used", honesty["ai"])
        self.assertIn("offline", honesty["ai"])
        self.assertIn("No request left this process", honesty["network"])
        self.assertIn("not probabilities", honesty["probabilisticConfidence"])
        self.assertEqual(honesty["mode"], "recall")

    def test_the_default_ai_provider_is_still_no_ai(self) -> None:
        """v0.1's contract: no endpoint configured means no AI capability at all.

        A directly constructed ``Vault`` carries no provider (``None``); the server
        injects ``provider_from_environment()``, which returns ``NoAIProvider``
        unless ``IWTT_AI_BASE_URL`` is set.  Both mean the same thing, and the
        HTTP status line reports "NoAIProvider" for both.
        """
        import os

        from iwtt.ai import NoAIProvider, provider_from_environment

        self.assertIsNone(self.vault.ai_provider)
        self.assertFalse(getattr(self.vault.ai_provider, "available", False))
        self.assertEqual(getattr(self.vault.ai_provider, "name", "NoAIProvider"), "NoAIProvider")

        saved = os.environ.pop("IWTT_AI_BASE_URL", None)
        try:
            provider = provider_from_environment()
            self.assertIsInstance(provider, NoAIProvider)
            self.assertFalse(provider.available)
            self.assertEqual(provider.name, "NoAIProvider")
        finally:
            if saved is not None:
                os.environ["IWTT_AI_BASE_URL"] = saved

        configured = Vault(self._tmp.name + "/ai.sqlite3", ai_provider=provider)
        self.assertIsInstance(configured.ai_provider, NoAIProvider)

    def test_public_payloads_are_camel_case(self) -> None:
        snake = re.compile(r"^[a-z]+(_[a-z0-9]+)+$")
        samples: dict[str, Any] = {
            "recall": self.vault.recall("enemy").to_dict(),
            "find": self.vault.find_my_thought("enemy"),
            "diagnostics": self.vault.recall_diagnostics("enemy"),
            "clusters": {"clusters": self.vault.clusters()},
            "graphView": self.vault.graph_view_model(max_nodes=5),
            "config": self.vault.recall_config(),
            "indexStats": self.vault.recall_index_stats(),
            "learningStats": self.vault.recall_learning_stats(),
            "schemaVersions": self.vault.schema_versions(),
        }

        def walk(node: Any, trail: str) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertFalse(
                        snake.match(str(key)), f"{trail}.{key} is snake_case in a public payload"
                    )
                    walk(value, f"{trail}.{key}")
            elif isinstance(node, list):
                for index, item in enumerate(node):
                    walk(item, f"{trail}[{index}]")

        for name, payload in samples.items():
            with self.subTest(payload=name):
                walk(payload, name)


class ExternalEntityBoundaryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "thought-vault.sqlite3"
        self.vault = Vault(self.path)
        self.vault.initialize()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_the_entity_vocabulary_is_closed_on_both_sides(self) -> None:
        self.assertEqual(ENTITY_KINDS, (ENTITY_THOUGHT, ENTITY_EXTERNAL))
        self.assertEqual(len(EXTERNAL_ENTITY_TYPES), 10)
        self.assertEqual(
            set(EXTERNAL_ENTITY_TYPES),
            {"repository", "source_file", "class", "function", "document", "pdf_section",
             "bug", "test", "commit", "research_source"},
        )
        self.assertNotIn(ENTITY_THOUGHT, EXTERNAL_ENTITY_TYPES)
        self.assertNotIn("thought", EXTERNAL_ENTITY_TYPES)
        self.assertTrue(EXTERNAL_PROVENANCE)
        self.assertNotIn("USER_CONFIRMED", EXTERNAL_PROVENANCE,
                         "external provenance answers a different question than relationship "
                         "provenance and must not reuse its vocabulary")

    def test_an_unknown_entity_type_is_refused(self) -> None:
        with self.assertRaises(EntityBoundaryError):
            ExternalEntityRef(entity_type="thought", label="sneaky")
        with self.assertRaises(EntityBoundaryError):
            ExternalEntityRef(entity_type="function", label="ok", provenance="GUESSED")
        with self.assertRaises(EntityBoundaryError):
            ExternalEntityRef(entity_type="function", label="   ")
        reference = ExternalEntityRef(entity_type="function", label="Vault.initialize")
        self.assertEqual(reference.entity_kind, ENTITY_EXTERNAL)
        self.assertFalse(reference.is_thought)
        self.assertEqual(reference.to_dict()["entityKind"], ENTITY_EXTERNAL)

    def test_the_guards_refuse_a_crossing_in_both_directions(self) -> None:
        reference = ExternalEntityRef(entity_type="source_file", label="iwtt/store.py")
        thought = ThoughtRef(thought_id="abc")
        self.assertEqual(entity_kind_of(reference), ENTITY_EXTERNAL)
        self.assertEqual(entity_kind_of(thought), ENTITY_THOUGHT)
        self.assertEqual(entity_kind_of({"entityKind": ENTITY_EXTERNAL}), ENTITY_EXTERNAL)

        assert_not_thought(thought)      # a thought is not an external entity: fine
        assert_thought(thought)
        with self.assertRaises(EntityBoundaryError):
            assert_not_thought(reference, context="create_thought")
        with self.assertRaises(EntityBoundaryError):
            assert_thought(reference)
        with self.assertRaises(EntityBoundaryError) as raised:
            assert_not_thought(reference)
        self.assertIn("a Thought is something a person thought", str(raised.exception))

    def test_a_payload_cannot_declare_itself_a_thought(self) -> None:
        with self.assertRaises(EntityBoundaryError):
            validate_candidate_entity({"entityKind": ENTITY_THOUGHT, "label": "x"})
        with self.assertRaises(EntityBoundaryError):
            validate_candidate_entity("not an object")
        with self.assertRaises(EntityBoundaryError):
            validate_candidate_entity({"entityKind": "SOMETHING_ELSE", "label": "x"})
        with self.assertRaises(EntityBoundaryError):
            validate_candidate_entity({"entityType": "function", "label": ""})

        validated = validate_candidate_entity({
            "entityType": "function", "label": "Vault.initialize",
            "uri": "file:///repo/iwtt/store.py", "anchor": "L412",
            "provenance": "USER_IMPORTED",
        })
        self.assertEqual(validated["entityKind"], ENTITY_EXTERNAL)
        self.assertEqual(validated["anchor"], "L412")

    def test_the_cross_domain_chain_is_documented_end_to_end(self) -> None:
        design = cross_domain_design()
        edges = [item["edge"] for item in design]
        for expected in ("Thought → Experiment", "Experiment → Project", "Project → Source File",
                         "Source File → Function", "Function → Bug", "Bug → Fix",
                         "Fix → New Thought"):
            self.assertTrue(
                any(expected == edge or expected in edge for edge in edges),
                f"the documented chain is missing {expected!r}: {edges}",
            )
        for item in design:
            with self.subTest(edge=item["edge"]):
                # "thought" = expressible with v0.1's vocabulary today;
                # "boundary" = the edge itself crosses kinds;
                # "external" = both ends live in the future external registry.
                self.assertIn(item["segment"], ("thought", "boundary", "external"),
                              "each edge must say which side of the boundary it lives on")
                self.assertEqual(item["implementable"], item["segment"] == "thought")
                self.assertTrue(item["relationshipType"])
                self.assertTrue(item["direction"])
                if item["implementable"]:
                    self.assertTrue(item.get("note"),
                                    "an implementable edge must say how it is expressed today")
                else:
                    self.assertTrue(item.get("blocker"),
                                    "a deferred edge must name what actually blocks it")
        self.assertEqual(sum(1 for item in design if item["implementable"]), 2,
                         "only the two thought-side edges are expressible in v0.1")
        self.assertEqual(sum(1 for item in design if item["segment"] == "boundary"), 3)
        self.assertIsNot(cross_domain_design(), design, "a fresh copy each call")
        self.assertEqual(cross_domain_design(), design)

    def test_the_capability_manifest_states_what_is_not_built(self) -> None:
        manifest = capability_manifest()
        self.assertEqual(manifest, CAPABILITY_MANIFEST)
        self.assertTrue(manifest["implementedInV02"])
        not_built = " ".join(manifest["explicitlyNotImplementedInV02"]).lower()
        for phrase in ("repository ingestion", "ast parsing", "pdf or document ingestion",
                       "canonical schema change", "cross-kind relationship"):
            self.assertIn(phrase, not_built, f"the manifest must admit it does not do {phrase!r}")
        self.assertFalse(manifest["canonicalSchemaChanged"],
                         "the manifest must state that the canonical schema is untouched")
        self.assertEqual(manifest["newCanonicalTables"], [])
        self.assertEqual(manifest["newCanonicalColumns"], [])
        self.assertIn("original_text", manifest["invariant"])
        self.assertIn("BEFORE UPDATE trigger", manifest["invariant"])
        self.assertTrue(manifest["implementedInV02"])
        self.assertIn("entitytype field on every graphviewmodel node",
                      " ".join(manifest["implementedInV02"]).lower())
        self.assertIn("named blockers", " ".join(manifest["implementedInV02"]).lower())

    def test_the_foundation_creates_no_storage_and_no_entities(self) -> None:
        tables_before = self._tables()
        payload = self.vault.external_foundation()
        self.assertEqual(self._tables(), tables_before,
                         "shipping an interface must not ship a schema")
        self.assertFalse(payload["canonical"])
        self.assertFalse(payload["persisted"])
        self.assertFalse(payload["implemented"])
        self.assertEqual(payload["storageCreated"], [])
        self.assertEqual(payload["entityKinds"], list(ENTITY_KINDS))
        self.assertEqual(payload["externalEntityTypes"], list(EXTERNAL_ENTITY_TYPES))
        self.assertEqual(len(self.vault.list_thoughts()), 0)

    def test_no_code_or_document_ingestion_exists_anywhere_in_the_package(self) -> None:
        # Code/document ingestion would need one of these.  ``re.compile`` is a
        # regular expression, not code execution, and is used throughout the recall
        # layer, so bare ``compile(`` is deliberately not matched.
        forbidden = re.compile(
            r"\b(?:ast\.parse|ast\.walk\s*\(\s*compile|subprocess|os\.walk|glob\.glob"
            r"|importlib|pickle|__import__)\b|(?<![\w.])(?:exec|eval)\s*\("
        )
        for path in sorted((REPO_ROOT / "iwtt").rglob("*.py")):
            with self.subTest(module=str(path.relative_to(REPO_ROOT))):
                source = path.read_text()
                match = forbidden.search(source)
                self.assertIsNone(match, f"{path} contains {match.group(0) if match else ''}")

    def _tables(self) -> list[str]:
        with sqlite3.connect(self.path) as conn:
            return all_tables(conn)


if __name__ == "__main__":
    unittest.main()
